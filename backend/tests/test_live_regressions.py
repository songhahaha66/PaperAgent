"""Failures observed with a real model: bounded feedback, verification and delivery."""

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph import llm_utils
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.models import IssueReport, Requirement, ResearchConflict
from ai_system.research.store import ResearchStore
from test_paper_graph import FakeModel, FixedJudge, TASK, _Structured
from test_research_p2 import problem, store_for, submit


def test_structured_retries_return_actual_field_errors_and_keep_call_budget(monkeypatch):
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    bad = dict(id="R1", statement="do task", source_quote="task", kind="constraint", task_ids=["Q1"], checks=[])

    class Model:
        calls = 0

        def with_structured_output(self, schema, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return Requirement.model_validate(bad)
            assert "必须有可执行检查" in messages[-1].content
            # The plain JSON retry has the same error; it must not be discarded
            # as a generic missing response on the next attempt.
            data = bad if self.calls == 2 else {**bad, "kind": "goal"}
            return SimpleNamespace(content=json.dumps(data))

    model = Model()
    messages = [HumanMessage(content="task")]
    result = asyncio.run(llm_utils.call_structured(model, Requirement, messages))
    assert result.kind == "goal" and model.calls == 3
    assert len(messages) == 1


def test_unresolved_writing_issue_blocks_direct_publication(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    submit(store, "Q3", {"independent": 1})
    store.report_issues([IssueReport(task_id="Q1", category="writing", section="sensitivity",
                                    message="引用了不存在的结果：F1")], "sections")
    assert not store.delivery_ready(store.snapshot())
    with pytest.raises(ResearchConflict):
        store.publish_paper("unrepaired draft")
    assert not (tmp_path / "paper.md").exists()


@pytest.mark.parametrize("checks", [[], ["unknown"], ["capacity"], ["capacity", "unknown"]])
def test_missing_or_unrelated_checks_cannot_dismiss_a_cited_metric(tmp_path, checks):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    _, refs = submit(store, "Q2", {"allocation": 8, "residual": 0, "perturbed_objective": 10})
    store.report_issues([IssueReport(task_id="Q2", category="evidence", section="abstract",
        message="扰动目标值不能证明未登记的解坐标", evidence_refs=[refs[-1]["id"]], check_ids=checks)], "sections")
    assert store.schedule_investigations() == []
    assert research_findings(store.snapshot())[-1]["status"] == "inconclusive"
    assert not store.delivery_ready(store.snapshot())


@pytest.mark.parametrize("value,status", [(8, "dismissed"), (8.005, "dismissed"), (9, "confirmed")])
def test_independent_finite_value_must_agree_with_the_original_result(tmp_path, value, status):
    analysis = problem()
    analysis.assumptions = []
    analysis.requirements[1].checks[0].operator = "finite"
    analysis.requirements[1].checks[0].expected = None
    analysis.requirements[1].checks[0].tolerance = 0.01
    store = ResearchStore(tmp_path, "comparison")
    from test_research_p2 import TASK as source
    store.initialize(source, analysis.model_dump())
    submit(store, "Q1", {"demand": 8})
    _, refs = submit(store, "Q2", {"allocation": 8})
    store.report_issues([IssueReport(task_id="Q2", category="model", message="复核配置量",
                                   evidence_refs=[refs[0]["id"]], check_ids=["capacity"])], "review")
    [verification] = store.schedule_investigations()
    submit(store, verification, {"allocation": value})
    state = store.snapshot()
    assert research_findings(state)[0]["status"] == status
    assert state["tasks"]["Q2"]["validity"] == ("stale" if status == "confirmed" else "current")
    check = state["validations"][-1]["checks"][0]
    assert check["outcome"] == ("failed" if status == "confirmed" else "passed")
    assert len(check["evidence_refs"]) == 2


class BrokenWriter(FakeModel):
    broken: bool = True

    def _write(self, request):
        text = super()._write(request)
        return text + " 见 {{F:F999}}。" if self.broken else text


class MathLeakWriter(BrokenWriter):
    def _write(self, request):
        text = FakeModel._write(self, request)
        return text + r" 降幅约为 $23.1\%$。" if self.broken else text


@pytest.mark.parametrize("writer", [BrokenWriter, MathLeakWriter])
def test_exhausted_writing_repairs_preserve_drafts_and_resume_without_recomputing(tmp_path, monkeypatch, writer):
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    monkeypatch.setattr(_Structured, "reviewed_q1", True)
    monkeypatch.setattr(_Structured, "audits", 10)
    model = writer(loose_number_once=False)
    events = []

    class Recorder:
        async def send_json_block(self, kind, content):
            events.append((kind, content))

    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path), judge=FixedJudge(),
        stream_manager=Recorder(), explore_top_k=1, max_review_rounds=0, max_repair_rounds=1)
    assert "研究已暂停" in asyncio.run(agent.run(TASK))
    store = ResearchStore(tmp_path, agent._load_thread())
    before = store.snapshot()
    assert before["outcome"] == "blocked" and before["sections"]
    assert all(i["blocking"] for i in research_findings(before) if i["status"] == "confirmed")
    assert not (tmp_path / "paper.md").exists()
    assert any(kind == "paper_phase" and content["stage"] == "validate" and content["status"] == "blocked"
               for kind, content in events)
    assert any(kind == "research_state" and content["outcome"] == "blocked" and (content["feedback"] or content["faults"])
               for kind, content in events)
    attempts = set(before["attempts"])
    model.broken = False
    assert "paper.md" in asyncio.run(agent.resume_failed())
    after = store.snapshot()
    assert set(after["attempts"]) == attempts
    assert all(i["status"] in {"resolved", "dismissed"} for i in research_findings(after))
    assert store.delivery_ready(after) and (tmp_path / "paper.md").exists()
    assert "F999" not in (tmp_path / "paper.md").read_text()
    assert "23.1" not in (tmp_path / "paper.md").read_text()
