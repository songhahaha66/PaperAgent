"""Run real graph/checkpoint/kernel adapters with deterministic model replies."""
import asyncio
import json
import re
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from helpers.graph import FakeModel, _make_workspace, _Structured, TASK, FAIL_ONCE
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.store import ResearchStore
from services.chat_services.research_session import research_snapshot


pytestmark = pytest.mark.usefixtures("offline_kernel")

class VersionAwareModel(FakeModel):
    fail_title_once: str = ''
    def _research_step(self,messages,plan):
        reply=super()._research_step(messages,plan)
        if (self.fail_title_once and reply.tool_calls
                and reply.tool_calls[0]['name']=='research_run_python'
                and reply.tool_calls[0]['args'].get('title')==self.fail_title_once):
            self.fail_title_once=''
            reply.tool_calls[0]['args']['code']="raise ValueError('controlled computation failure')"
        return reply


@pytest.fixture(autouse=True)
def no_retries_or_state_leaks(monkeypatch):
    from ai_system.graph import llm_utils
    FAIL_ONCE.clear()
    _Structured.reviewed_q1 = True
    _Structured.audits = 2
    yield
    FAIL_ONCE.clear()


def agent_for(tmp_path, *, fail_title_once="", **options):
    ws = _make_workspace(tmp_path)
    return PaperGraphAgent(VersionAwareModel(fail_title_once=fail_title_once), workspace_dir=str(ws), **options)


def test_new_engine_selective_revision_preserves_unrelated_results_and_sections(tmp_path):
    agent = agent_for(tmp_path)
    assert "paper.md" in asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    original = store.snapshot()
    targets = {q['title']: key for key,q in original['questions'].items()}
    forecast, policy = targets['用电量预测'], targets['政策评估']
    # Revising the question preserves calculations; new conditions are not
    # retroactively claimed as verified by the old experiment.
    command = dict(command_id="revise-policy", research_id=agent._load_thread(), base_revision=original["revision"],
                   change="补充政策边界条件", scope=[policy])
    assert "paper.md" in asyncio.run(agent.revise_research(**command))
    revised = store.snapshot()
    for name in ('experiments','executions','artifacts'):
        assert revised[name] == original[name]
    forecast_qid,policy_qid = forecast,policy
    assert revised['questions'][forecast_qid] == original['questions'][forecast_qid]
    assert revised['questions'][policy_qid]['version'] > original['questions'][policy_qid]['version']
    assert revised['questions'][policy_qid]['coverage'] == 'partial'
    assert '未执行条件仍未知' in revised['questions'][policy_qid]['stop_reason']
    assert original['questions'][forecast_qid]['judgment'] in revised['sections']['document']['text']
    assert revised["outcome"] in {"completed", "needs_attention"}
    before = len(revised["executions"])
    written = len(agent.llm.text_calls)
    commit_seq = store.snapshot()["commit_seq"]
    assert "paper.md" in asyncio.run(agent.revise_research(**command))
    assert len(store.snapshot()["executions"]) == before
    assert len(agent.llm.text_calls) == written
    assert store.snapshot()["commit_seq"] == commit_seq
    snapshot = research_snapshot(agent.workspace_dir, "work", "completed")
    assert snapshot.engine_version == "research-v3" and snapshot.research_revision == revised["revision"] > original["revision"]
    assert not snapshot.experiments and len(snapshot.runs)==4


@pytest.mark.parametrize("mode", ["direct", "chat"])
def test_failed_attempt_is_not_published_and_resume_preserves_accepted_predecessor(tmp_path, mode):
    agent = agent_for(tmp_path, fail_title_once="政策评估")
    first_output = asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    initial = store.snapshot()
    from ai_system.research.runs import direct_runs
    targets={q['title']:key for key,q in initial['questions'].items()}
    forecast,policy=targets['用电量预测'],targets['政策评估']
    runs=direct_runs(store)
    failed=[rid for rid,r in runs.items() if r['question_id']==policy and r['status']=='error']
    assert len(failed)==1 and not initial['artifacts'] and not initial['experiments']
    original_q1={rid:r for rid,r in runs.items() if r['question_id']==forecast}
    assert len(original_q1)==2
    output=asyncio.run(agent.resume_failed() if mode=='direct' else agent.run('继续',action='resume')) if '研究已暂停' in first_output else first_output
    assert 'paper.md' in output
    current=direct_runs(store)
    assert all(current[rid]==r for rid,r in original_q1.items())
    assert len([r for r in current.values() if r['question_id']==policy and r['status']=='success'])==2
    assert store.registry_payload()['results']==[]
    paper=(Path(agent.workspace_dir)/'paper.md').read_bytes()
    assert 'paper.md' in asyncio.run(agent.resume_failed())
    assert direct_runs(store)==current and (Path(agent.workspace_dir)/'paper.md').read_bytes()==paper


def test_resume_after_delivery_failure_preserves_all_committed_attempts(tmp_path, monkeypatch):
    import ai_system.modules.writing.export as build_module
    agent = agent_for(tmp_path)
    writer = build_module.export

    async def fail_delivery(state, runtime):
        raise RuntimeError("writer temporarily unavailable")

    monkeypatch.setattr(build_module, "export", fail_delivery)
    assert "任务执行失败" in asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    from ai_system.research.runs import direct_runs
    attempts = direct_runs(store)
    monkeypatch.setattr(build_module, "export", writer)
    assert "paper.md" in asyncio.run(agent.resume_failed())
    assert direct_runs(store) == attempts
