"""
LLM 调用耗时追踪

整条流水线的耗时几乎都花在等模型上（内核执行通常只有几秒），所以按调用记录：
哪个节点、哪个子问题/章节、等了多久、输入输出多少 token、是否被截断或出错。
SDK 内部的重试对回调不可见：一次 524 之后重试成功的调用，耗时里包含了前面白等的时间，正好能看出来。

子问题/章节标签用 ContextVar 传递：Send 分支和 asyncio.gather 的子任务各自拷贝上下文，互不串扰。
"""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

CURRENT_LABEL: ContextVar[str] = ContextVar("paper_graph_label", default="")
SLOW_CALL_SECONDS = 90.0


def set_label(label: str) -> None:
    """标记当前任务（每个 Send 分支是独立任务），之后在这个任务里发起的模型调用都带上它"""
    CURRENT_LABEL.set(label)


@contextmanager
def labelled(label: str):
    """在这段代码里发起的模型调用都带上标签（子问题编号、阶段或章节 key）"""
    token = CURRENT_LABEL.set(label)
    try:
        yield
    finally:
        CURRENT_LABEL.reset(token)


def _node(metadata: Optional[Dict[str, Any]]) -> str:
    """外层图节点名：子 agent 内部的节点（model、tools）挂在 checkpoint_ns 的第一段下"""
    metadata = metadata or {}
    namespace = str(metadata.get("langgraph_checkpoint_ns") or "")
    if namespace:
        return namespace.split("|")[0].split(":")[0]
    return str(metadata.get("langgraph_node") or "")


def _usage(response) -> Dict[str, int]:
    for generations in response.generations or []:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                return {"input": int(usage.get("input_tokens") or 0), "output": int(usage.get("output_tokens") or 0)}
    return {"input": 0, "output": 0}


def _finish_reason(response) -> str:
    for generations in response.generations or []:
        for generation in generations:
            info = generation.generation_info or {}
            metadata = getattr(getattr(generation, "message", None), "response_metadata", None) or {}
            reason = info.get("finish_reason") or metadata.get("finish_reason")
            if reason:
                return str(reason)
    return ""


class LLMCallTrace(BaseCallbackHandler):
    run_inline = True

    def __init__(self, started: Optional[float] = None):
        super().__init__()
        self.started = started if started is not None else time.time()
        self.calls: List[Dict[str, Any]] = []
        self._open: Dict[UUID, Dict[str, Any]] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, metadata=None, **kwargs) -> None:
        self._open[run_id] = {
            "start": round(time.time() - self.started, 1),
            "node": _node(metadata),
            "label": CURRENT_LABEL.get(),
            "_t0": time.monotonic(),
        }

    def _close(self, run_id: UUID, **fields) -> None:
        record = self._open.pop(run_id, None)
        if record is None:
            return
        record["seconds"] = round(time.monotonic() - record.pop("_t0"), 1)
        record.update(fields)
        self.calls.append(record)
        if record["seconds"] >= SLOW_CALL_SECONDS:
            logger.warning("慢调用 %.0fs：%s %s %s", record["seconds"], record["node"], record["label"], fields)

    def on_llm_end(self, response, *, run_id: UUID, **kwargs) -> None:
        self._close(run_id, **_usage(response), finish=_finish_reason(response))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs) -> None:
        self._close(run_id, error=f"{type(error).__name__}: {str(error)[:200]}")

    def summary(self) -> Dict[str, Any]:
        seconds = sorted(c["seconds"] for c in self.calls)
        by_node: Dict[str, Dict[str, float]] = {}
        for call in self.calls:
            stats = by_node.setdefault(call["node"] or "?", {"calls": 0, "seconds": 0.0, "output": 0})
            stats["calls"] += 1
            stats["seconds"] = round(stats["seconds"] + call["seconds"], 1)
            stats["output"] += call.get("output", 0)

        def pct(q: float) -> float:
            return seconds[min(int(q * len(seconds)), len(seconds) - 1)] if seconds else 0.0

        return {
            "calls": len(self.calls),
            "call_seconds": round(sum(seconds), 1),
            "p50": pct(0.5),
            "p90": pct(0.9),
            "max": seconds[-1] if seconds else 0.0,
            "slow": sum(1 for s in seconds if s >= SLOW_CALL_SECONDS),
            "truncated": sum(1 for c in self.calls if c.get("finish") == "length"),
            "errors": sum(1 for c in self.calls if c.get("error")),
            "by_node": by_node,
        }

    def dump(self, path) -> None:
        payload = {"summary": self.summary(), "calls": self.calls}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
