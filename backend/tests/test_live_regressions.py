"""Failures observed with a real model: bounded feedback, verification and delivery."""

from ai_system.research.domain import findings as research_findings
import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.store import ResearchStore
from helpers.graph import FakeModel, TASK, _Structured


class BrokenWriter(FakeModel):
    broken: bool = True

    def _write(self, request):
        text = super()._write(request)
        return text + " 见 {{F:F999}}。" if self.broken else text


def test_exhausted_writing_repairs_preserve_drafts_and_resume_without_recomputing(tmp_path, monkeypatch):
    monkeypatch.setattr(_Structured, "reviewed_q1", True)
    monkeypatch.setattr(_Structured, "audits", 10)
    model = BrokenWriter(loose_number_once=False)
    events = []

    class Recorder:
        async def send_json_block(self, kind, content):
            events.append((kind, content))

    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path),
        stream_manager=Recorder(), max_repair_rounds=1)
    assert "研究已暂停" in asyncio.run(agent.run(TASK))
    store = ResearchStore(tmp_path, agent._load_thread())
    before = store.snapshot()
    assert before["outcome"] == "blocked" and before["sections"]
    assert any(i['category']=='writing' and i['source']=='sections' and not i['blocking'] for i in research_findings(before))
    assert 'F999' in '\n'.join(section['text'] for section in before['sections'].values())
    assert before['publication']['stage']=='repair' and before['action']['status']=='failed'
    assert before['action']['result']['research_blocked']
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
    history=before['publication']['repair_inputs']
    assert after['publication']['repair_inputs'][:len(history)]==history
    assert after['publication']['repair_resume_offset']==len(history)
    assert len(after['publication']['repair_inputs'])>len(history)
    assert all(i["status"] in {"resolved", "dismissed"} for i in research_findings(after))
    assert store.delivery_ready(after) and (tmp_path / "paper.md").exists()
    assert "F999" not in (tmp_path / "paper.md").read_text()


def test_explicit_resume_of_unchanged_bad_writer_is_bounded_and_keeps_history_and_usage(tmp_path,monkeypatch):
    from ai_system.research.runtime import ResearchRuntime
    monkeypatch.setattr(_Structured,'reviewed_q1',True)
    monkeypatch.setattr(_Structured,'audits',10)
    model=BrokenWriter(loose_number_once=False)
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path))
    assert '研究已暂停' in asyncio.run(agent.run(TASK))
    store=ResearchStore(tmp_path,agent._load_thread());before=store.snapshot()
    history=list(before['publication']['repair_inputs']);written=len(model.text_calls)
    # Synthetic usage through the real meter; no provider or network is invoked.
    runtime=ResearchRuntime(tmp_path,store.research_id)
    sequence=runtime.reserve('offline-controlled-fixture')
    runtime.usage(sequence,{'input_tokens':100,'output_tokens':20})
    budget=runtime.status()
    assert '研究已暂停' in asyncio.run(agent.resume_failed())
    after=store.snapshot();added=len(model.text_calls)-written
    assert 0<added<=3*len(before['sections']), 'explicit recovery gets another bounded attempt, never an automatic endless retry'
    assert history==after['publication']['repair_inputs'][:len(history)]
    assert len(history)<len(after['publication']['repair_inputs'])<=len(history)+3
    assert after['publication']['repair_resume_offset']==len(history)
    assert after['publication']['repairs']>before['publication']['repairs']
    assert after['outcome']=='blocked' and after['action']['status']=='failed'
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
    assert not (tmp_path/'paper.md').exists()
    retained=runtime.status()
    assert {k:retained[k] for k in ('used','input_tokens','output_tokens','request_limit')}=={k:budget[k] for k in ('used','input_tokens','output_tokens','request_limit')}
    assert retained['used']>=1 and retained['input_tokens']>=100 and retained['output_tokens']>=20
