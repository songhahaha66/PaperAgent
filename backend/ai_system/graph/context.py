from __future__ import annotations

from dataclasses import dataclass
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
