from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..runtime.events import EventEmitter
from ..schemas.draft import Draft
from ..schemas.template_spec import Slot
from .state import PaperState


@dataclass
class RunContext:
    llm: Any = None
    writer_llm: Any = None
    stream: Any = None
    emitter: EventEmitter | None = None
    writer: Callable[[Slot, PaperState, "RunContext"], Awaitable[Draft] | Draft] | None = None
    gather_fn: Callable[..., Awaitable[None] | None] | None = None
    judge: Any = None
    coder_llm: Any = None


def bind_emitter(stream_manager, workspace_dir: str, run_id: str, work_id: str) -> EventEmitter:
    emitter = getattr(stream_manager, "event_emitter", None)
    if emitter is None:
        emitter = EventEmitter(workspace_dir=workspace_dir, run_id=run_id, thread_id=work_id)
        if stream_manager is not None:
            stream_manager.event_emitter = emitter
        return emitter
    if getattr(emitter, "workspace_dir", None) is None:
        emitter.workspace_dir = Path(workspace_dir)
    if not getattr(emitter, "run_id", ""):
        emitter.run_id = run_id
    if not getattr(emitter, "thread_id", ""):
        emitter.thread_id = work_id
    return emitter


async def emit(ctx: RunContext, event_type: str, payload: dict | None = None, run_id: str = "", thread_id: str = ""):
    event = None
    if ctx.emitter:
        event = ctx.emitter.emit(event_type, payload or {}, run_id=run_id, thread_id=thread_id)
    if ctx.stream is None:
        return event
    send_event = getattr(ctx.stream, "send_agui_event", None)
    if send_event and event is not None:
        await send_event(event)
    return event


async def emit_json(ctx: RunContext, block_type: str, content):
    if ctx.stream is None:
        return
    await ctx.stream.send_json_block(block_type, content)
