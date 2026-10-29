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
    def _solver_step(self, task, messages):
        if self.fail_title_once and self.fail_title_once in task and len([m for m in messages if isinstance(m,ToolMessage)]) == 2:
            self.fail_title_once = ''
            raise RuntimeError('403 real_name_required')
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
    targets = {t['subproblem']['title']: key for key,t in original['experiments'].items()}
    forecast, policy = targets['用电量预测'], targets['政策评估']
    # Revising the question preserves calculations; new conditions are not
    # retroactively claimed as verified by the old experiment.
    command = dict(command_id="revise-policy", research_id=agent._load_thread(), base_revision=original["revision"],
                   change="补充政策边界条件", scope=[policy])
    assert "paper.md" in asyncio.run(agent.revise_research(**command))
    revised = store.snapshot()
    for name in ('experiments','executions','artifacts'):
        assert revised[name] == original[name]
    forecast_qid,policy_qid = (original['experiments'][key]['question_id'] for key in (forecast,policy))
    assert revised['questions'][forecast_qid] == original['questions'][forecast_qid]
    assert revised['questions'][policy_qid]['version'] > original['questions'][policy_qid]['version']
    assert revised['questions'][policy_qid]['coverage'] == 'partial'
    assert '未执行条件仍未知' in revised['questions'][policy_qid]['stop_reason']
    assert revised['sections']['solve:'+forecast]['text'] == original['sections']['solve:'+forecast]['text']
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
    assert len(snapshot.experiments) == 2


@pytest.mark.parametrize("mode", ["direct", "chat"])
def test_failed_attempt_is_not_published_and_resume_preserves_accepted_predecessor(tmp_path, mode):
    agent = agent_for(tmp_path, fail_title_once="政策评估")
    first_output = asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    initial = store.snapshot()
    targets = {t['subproblem']['title']: key for key,t in initial['experiments'].items()}
    forecast,policy = targets['用电量预测'],targets['政策评估']
    q1 = initial['experiments'][forecast]['execution_id']
    failed=[a for a in initial['executions'].values() if a['task_id']==policy and a['execution_status']=='failed']
    assert len(failed)==1 and failed[0]['execution_status']=='failed' and failed[0]['acceptance']!='accepted'
    failed_ids={a['attempt_id'] for a in failed}
    failed_refs={ref for ref,e in initial['artifacts'].items() if e['producer_attempt'] in failed_ids}
    assert failed_refs.isdisjoint({r['id'] for r in store.registry_payload()['results']})
    assert len([a for a in initial['executions'].values() if a['task_id']==forecast])==1
    # The controller may already have repaired Q2. Resume only if work remains.
    output = asyncio.run(agent.resume_failed() if mode == 'direct' else agent.run('继续', action='resume')) if '研究已暂停' in first_output else first_output
    assert 'paper.md' in output
    current=store.snapshot();active=current['experiments'][policy]['execution_id']
    assert current['experiments'][forecast]['execution_id']==q1 and current['experiments'][policy]['acceptance']=='accepted'
    assert len([a for a in current['executions'].values() if a['task_id']==forecast])==1
    assert active not in failed_ids and current['executions'][active]['execution_status']=='succeeded'
    assert current['executions'][active]['acceptance']=='accepted'
    results=store.registry_payload()['results'];q2_results=[r for r in results if r['subproblem']==policy]
    assert q2_results==[]
    assert any(a['producer_attempt']==active and a['type']=='stdout' for a in current['artifacts'].values())
    assert failed_refs.isdisjoint({r['id'] for r in results})
    paper=(Path(agent.workspace_dir)/'paper.md').read_bytes();attempts=current['executions'];written=len(agent.llm.text_calls)
    assert 'paper.md' in asyncio.run(agent.resume_failed())
    after=store.snapshot()
    assert after['executions']==attempts and after['experiments'][forecast]['execution_id']==q1
    assert (Path(agent.workspace_dir)/'paper.md').read_bytes()==paper and len(agent.llm.text_calls)==written


def test_resume_after_delivery_failure_preserves_all_committed_attempts(tmp_path, monkeypatch):
    import ai_system.modules.writing.sections as build_module
    agent = agent_for(tmp_path)
    writer = build_module.write

    async def fail_delivery(state, runtime):
        raise RuntimeError("writer temporarily unavailable")

    monkeypatch.setattr(build_module, "write", fail_delivery)
    assert "任务执行失败" in asyncio.run(agent.run(TASK))
    store = ResearchStore(agent.workspace_dir, agent._load_thread())
    attempts = store.snapshot()["executions"]
    monkeypatch.setattr(build_module, "write", writer)
    assert "paper.md" in asyncio.run(agent.resume_failed())
    assert store.snapshot()["executions"] == attempts
