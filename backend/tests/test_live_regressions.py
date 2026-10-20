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






class BrokenWriter(FakeModel):
    broken: bool = True

    def _write(self, request):
        text = super()._write(request)
        return text + " 见 {{F:F999}}。" if self.broken else text


class LiteralNumberWriter(FakeModel):
    def _write(self, request):
        return super()._write(request) + r" 降幅约为 $23.1\%$。"


def test_exhausted_writing_repairs_preserve_drafts_and_resume_without_recomputing(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    monkeypatch.setattr(_Structured, "reviewed_q1", True)
    monkeypatch.setattr(_Structured, "audits", 10)
    model = BrokenWriter(loose_number_once=False)
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
    attempts = set(before["executions"])
    model.broken = False
    assert "paper.md" in asyncio.run(agent.resume_failed())
    after = store.snapshot()
    assert set(after["executions"]) == attempts
    assert all(i["status"] in {"resolved", "dismissed"} for i in research_findings(after))
    assert store.delivery_ready(after) and (tmp_path / "paper.md").exists()
    assert "F999" not in (tmp_path / "paper.md").read_text()


def test_literal_numbers_are_allowed_without_regex_writing_repairs(tmp_path,monkeypatch):
    monkeypatch.setattr(_Structured,'reviewed_q1',True)
    monkeypatch.setattr(_Structured,'audits',10)
    model=LiteralNumberWriter(loose_number_once=False);start=len(model.text_calls)
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path),judge=FixedJudge(),
        explore_top_k=1,max_review_rounds=0,max_repair_rounds=1)
    output=asyncio.run(agent.run(TASK))
    assert 'paper.md' in output and '引用有效性与文稿结构校验已完成' in output
    assert '数字校验' not in output
    assert '23.1' in (tmp_path/'paper.md').read_text()
    assert agent.last_result.get('repair_round',0)==0 and not agent.last_result.get('issues')
    assert not any('# 当前稿' in request for request in model.text_calls[start:])
