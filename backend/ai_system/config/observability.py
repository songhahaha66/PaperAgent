"""Optional LangSmith context shared by graphs and paper comments.

Only identifiers enter metadata. Models, stores and their connection credentials
must never be passed as trace inputs; LangChain records actual model messages.
"""
import os
from contextlib import contextmanager, nullcontext

from langsmith import trace, tracing_context


def tracing_status() -> str:
    if os.getenv('LANGSMITH_TRACING', '').strip().lower() not in {'true', '1', 'yes', 'on'}:
        return 'disabled'
    if not os.getenv('LANGSMITH_API_KEY', '').strip():
        return 'missing_key'
    return 'enabled'


@contextmanager
def research_tracing(work_id: str, research_id: str, operation: str, **metadata):
    """Group repeated/resumed executions in one LangSmith research thread.

    Context variables isolate concurrent projects and are restored on failure.
    LANGSMITH_ENDPOINT / WORKSPACE_ID remain native SDK settings.
    """
    with tracing_context(
        enabled=tracing_status() == 'enabled',
        project_name=os.getenv('LANGSMITH_PROJECT') or 'PaperAgent',
        tags=['paperagent', operation, f'work:{work_id}'],
        metadata={**metadata, 'work_id': work_id, 'research_id': research_id,
                  'thread_id': research_id, 'operation': operation},
    ):
        yield


def capability_span(name: str, module: str, inputs: dict):
    # Capabilities can also run outside a graph (e.g. export or offline evals).
    # Never initialize a tracing client for a disabled/incomplete configuration.
    if tracing_status() != 'enabled':
        return nullcontext()
    return trace(name, run_type='tool', inputs=inputs,
                 metadata={'capability': name, 'module': module})
