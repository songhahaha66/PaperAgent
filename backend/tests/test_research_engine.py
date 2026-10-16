"""Run real graph/checkpoint/kernel adapters with deterministic model replies."""
import asyncio
import json
import re

import pytest
from langchain_core.messages import ToolMessage

from test_paper_graph import FakeModel, FixedJudge, _make_workspace, _Structured, TASK, FAIL_ONCE
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.registry import ResultRegistry
from ai_system.research.store import ResearchStore
from services.chat_services.research_session import research_snapshot


class VersionAwareModel(FakeModel):
    def _solver_step(self, task, messages):
        reply = super()._solver_step(task, messages)
        if reply.tool_calls and reply.tool_calls[0]["name"] == "SolveReport":
            refs = [ref for message in messages if isinstance(message, ToolMessage)
                    for ref in re.findall(r"\{\{R:R\d+\}\}", str(message.content))]
            if refs:
                call = reply.tool_calls[0]
                call["args"] = json.loads(json.dumps(call["args"]).replace("{{R:R1}}", refs[-1]))
        return reply


@pytest.fixture(autouse=True)
def no_retries_or_state_leaks(monkeypatch):
    from ai_system.graph import llm_utils
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    FAIL_ONCE.clear()
    _Structured.reviewed_q1 = True
    _Structured.audits = 2
    yield
    FAIL_ONCE.clear()


def agent_for(tmp_path, **options):
    ws = _make_workspace(tmp_path)
    return PaperGraphAgent(VersionAwareModel(), workspace_dir=str(ws), judge=FixedJudge(), explore_top_k=1, **options)


def test_new_engine_selective_revision_preserves_unrelated_results_and_sections(tmp_path):
    agent = agent_for(tmp_path)
    assert "paper.md" in asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    original = store.snapshot()
    original_q1 = original["tasks"]["Q1"]["active_attempt"]
    # Change the downstream task, preserving Q1's evidence and its solve section.
    command = dict(command_id="revise-q2", research_id=agent._load_thread(), base_revision=1,
                   change="补充政策边界条件", scope=["Q2"])
    assert "paper.md" in asyncio.run(agent.revise_research(**command))
    revised = store.snapshot()
    assert revised["tasks"]["Q1"]["active_attempt"] == original_q1
    assert revised["tasks"]["Q2"]["revision"] == 2
    assert revised["tasks"]["Q3"]["active_attempt"] != original["tasks"]["Q3"]["active_attempt"]
    assert revised["sections"]["solve:Q1"]["text"] == original["sections"]["solve:Q1"]["text"]
    assert revised["outcome"] in {"completed", "needs_attention"}
    before = len(revised["attempts"])
    written = len(agent.llm.text_calls)
    commit_seq = store.snapshot()["commit_seq"]
    assert "paper.md" in asyncio.run(agent.revise_research(**command))
    assert len(store.snapshot()["attempts"]) == before
    assert len(agent.llm.text_calls) == written
    assert store.snapshot()["commit_seq"] == commit_seq
    snapshot = research_snapshot(agent.workspace_dir, "work", "completed")
    assert snapshot.engine_version == "research-v1" and snapshot.research_revision == 2
    assert len(snapshot.tasks) == 3


@pytest.mark.parametrize("mode", ["direct", "chat"])
def test_failed_attempt_is_not_published_and_resume_preserves_accepted_predecessor(tmp_path, mode):
    agent = agent_for(tmp_path)
    FAIL_ONCE.add("Q2")
    assert "研究已暂停" in asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    initial = store.snapshot()
    q1 = initial["tasks"]["Q1"]["active_attempt"]
    assert not any(r["subproblem"] == "Q2" for r in store.registry_payload()["results"])
    output = asyncio.run(agent.resume_failed() if mode == "direct" else agent.run("继续"))
    assert "paper.md" in output
    assert store.snapshot()["tasks"]["Q1"]["active_attempt"] == q1
    assert store.snapshot()["tasks"]["Q2"]["acceptance"] == "accepted"


def test_legacy_metadata_resumes_legacy_engine_without_creating_business_database(tmp_path):
    agent = agent_for(tmp_path, engine_version="legacy", confirm_models=True)
    asyncio.run(agent.run(TASK))
    thread = agent._thread_file()
    metadata = json.loads(thread.read_text())
    metadata.pop("engine_version")
    thread.write_text(json.dumps(metadata))
    replacement = PaperGraphAgent(VersionAwareModel(), workspace_dir=agent.workspace_dir, judge=FixedJudge(), explore_top_k=1)
    asyncio.run(replacement.run("确认"))
    assert replacement.engine_version == "legacy"
    assert not (thread.parent / "research.sqlite").exists()
