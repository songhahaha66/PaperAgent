"""Retire legacy checkpoint mirrors at the explicit recovery boundary."""
from langgraph.checkpoint.base import create_checkpoint
from .state import GraphState, PaperState


async def discard_business_mirror(graph, config):
    """Append a clean checkpoint, preserving historical snapshots verbatim.

    LangGraph retains channels absent from a new schema. Recovery already
    reconciles ResearchStore and resumes at solve_join, so old pending worker
    sends and business projections must not be carried into that new run.
    """
    saved = await graph.checkpointer.aget_tuple(config)
    if saved is None:
        return
    retired = set(PaperState.__annotations__) - set(GraphState.__annotations__)
    if not retired.intersection(saved.checkpoint['channel_values']):
        return
    step = saved.metadata.get('step', -1) + 1
    checkpoint = create_checkpoint(saved.checkpoint, None, step)
    for field in ('channel_values', 'channel_versions'):
        checkpoint[field] = {key: value for key, value in checkpoint[field].items() if key not in retired}
    checkpoint['versions_seen'] = {
        node: {key: value for key, value in versions.items() if key not in retired}
        for node, versions in checkpoint['versions_seen'].items()}
    checkpoint['pending_sends'] = []
    metadata = {key: value for key, value in saved.metadata.items() if key != 'writes'}
    metadata.update(source='update', step=step)
    await graph.checkpointer.aput(saved.config, checkpoint, metadata, {})
