"""Upload a small graph/model/capability trace without calling a paid model.

Run from backend: uv run python -m evals.langsmith_smoke
"""
import asyncio
import uuid
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, START, StateGraph
from langsmith import Client
from langsmith.utils import LangSmithNotFoundError

from ai_system.config.observability import research_tracing, tracing_status
from ai_system.modules.registry import CapabilityProduct, CapabilityRequest, ModuleRegistry


class SmokeState(TypedDict):
    question: str
    answer: str


async def run_smoke():
    model = FakeListChatModel(responses=['LangSmith tracing OK'])
    registry = ModuleRegistry()

    async def check(ctx, inputs):
        return CapabilityProduct('experiment', {'passed': True})

    registry.register('smoke', 'smoke.check', 'experiment', check)

    async def observe(state):
        reply = await model.ainvoke(state['question'])
        await registry.invoke(CapabilityRequest(name='smoke.check'), None)
        return {'answer': reply.content}

    builder = StateGraph(SmokeState)
    builder.add_node('observe', observe)
    builder.add_edge(START, 'observe')
    builder.add_edge('observe', END)
    run_id = uuid.uuid4()
    with research_tracing('langsmith-smoke', str(run_id), 'smoke'):
        await builder.compile().ainvoke({'question': 'Check tracing; no external model request.'},
                                        {'run_name': 'PaperAgent.smoke', 'run_id': run_id})
    return run_id


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    if tracing_status() != 'enabled':
        raise SystemExit('请在 backend/.env 设置 LANGSMITH_TRACING=true 和 LANGSMITH_API_KEY。')
    run_id = await run_smoke()
    # The SDK batches uploads; a new client would not flush its tracing queue.
    from langsmith.run_trees import get_cached_client
    get_cached_client().flush(timeout=15)
    client = Client()
    for attempt in range(6):
        try:
            run = await asyncio.to_thread(client.read_run, run_id, load_child_runs=True)
            def descendants(node):
                return [child for direct in node.child_runs or [] for child in [direct, *descendants(direct)]]

            children = descendants(run)
            if run.end_time and {'llm', 'tool'} <= {child.run_type for child in children} \
                    and all(child.end_time for child in children):
                break
        except LangSmithNotFoundError:
            if attempt == 5:
                raise
        await asyncio.sleep(1)
    else:
        raise RuntimeError(f'追踪已提交但尚未读回完整子步骤：{run_id}')
    print('LangSmith 已验证接收 graph / model / capability trace：')
    print(client.get_run_url(run=run))


if __name__ == '__main__':
    asyncio.run(main())
