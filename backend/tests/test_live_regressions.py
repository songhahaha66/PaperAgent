"""Unchanged bad manuscripts stop; recovery keeps calculations and the same session."""
import asyncio
import pytest
from helpers.graph import FakeModel,TASK
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.store import ResearchStore
from ai_system.research.runtime import ResearchRuntime
pytestmark=pytest.mark.usefixtures('offline_kernel')

class BrokenDraft(FakeModel):
    broken: bool=True
    def _draft(self,text):return text+'\n![missing](outputs/missing.png)' if self.broken else text


def test_bad_draft_is_retained_and_corrected_by_same_agent_without_recomputation(tmp_path):
    model=BrokenDraft();agent=PaperGraphAgent(model,workspace_dir=str(tmp_path))
    assert '任务执行失败' in asyncio.run(agent.run(TASK))
    store=ResearchStore(tmp_path,agent._load_thread());before=store.snapshot()
    assert (tmp_path/'paper.md').exists() and 'missing.png' in (tmp_path/'paper.md').read_text()
    from ai_system.research.runs import direct_runs
    receipts=direct_runs(store)
    assert before['outcome']!='completed' and receipts
    assert all('previous_summary' in text for text in model.text_calls), '只允许工作摘要，不启动独立写作'
    model.broken=False
    assert 'paper.md' in asyncio.run(agent.resume_failed())
    assert direct_runs(store)==receipts and not store.snapshot()['executions']
    assert 'missing.png' not in (tmp_path/'paper.md').read_text()
    assert store.snapshot()['outcome']=='completed'


def test_unchanged_draft_resume_stops_without_resetting_usage_or_replaying_code(tmp_path):
    model=BrokenDraft();agent=PaperGraphAgent(model,workspace_dir=str(tmp_path))
    assert '任务执行失败' in asyncio.run(agent.run(TASK))
    store=ResearchStore(tmp_path,agent._load_thread());before=store.snapshot()
    runtime=ResearchRuntime(tmp_path);seq=runtime.reserve('controlled');runtime.usage(seq,{'input_tokens':100,'output_tokens':20})
    with pytest.raises(RuntimeError,match='正文与诊断没有变化'):
        asyncio.run(agent.resume_failed())
    after=store.snapshot()
    assert after['outcome']!='completed' and after['executions']==before['executions']
    assert runtime.status()['used']==1 and runtime.status()['input_tokens']==100
    assert all('previous_summary' in text for text in model.text_calls), '只允许工作摘要，不启动独立写作'
