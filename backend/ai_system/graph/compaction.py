"""
求解循环的上下文压缩（兼顾前缀缓存）

CodeAct 循环每一步都把完整历史重新发给模型，输入随步数增长。
2024_C 一题输入 496 万 token，但其中 445 万（90%）命中了网关的前缀缓存，所以成本主要不在这里；
真正的风险是长循环把上下文撑满、拖慢每一步。

如果每一步都把"最近 N 步之外"的内容截短，压缩边界每步都在移动，前缀每步都变，缓存会全部失效，反而更贵。
所以这里按块推进边界：边界只在步数跨过 block 的整数倍时前移一次，块内的请求前缀完全相同，缓存照常命中。
内核是持久的，早期变量和数据都还在，被截短的旧代码和输出不影响后续计算。
压缩只作用于发给模型的请求，不改动 agent 状态，因此不影响登记和报告。
"""

from typing import Any, Awaitable, Callable, List

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

KEEP_RECENT_STEPS = 6
BLOCK_STEPS = 10
OLD_OUTPUT_CHARS = 400
OLD_CODE_CHARS = 600
OUTPUT_NOTE = "\n...[早期输出已压缩；内核中的变量仍然存在，需要时重新 print]"
CODE_NOTE = "\n# ...[早期代码已压缩，定义的变量仍在内核中]"


def _clip_tool_message(message: ToolMessage) -> ToolMessage:
    content = message.content if isinstance(message.content, str) else str(message.content)
    if len(content) <= OLD_OUTPUT_CHARS:
        return message
    return message.model_copy(update={"content": content[:OLD_OUTPUT_CHARS] + OUTPUT_NOTE})


def _clip_ai_message(message: AIMessage) -> AIMessage:
    changed = False
    tool_calls = []
    for call in message.tool_calls:
        code = call.get("args", {}).get("code")
        if isinstance(code, str) and len(code) > OLD_CODE_CHARS:
            call = {**call, "args": {**call["args"], "code": code[:OLD_CODE_CHARS] + CODE_NOTE}}
            changed = True
        tool_calls.append(call)
    if not changed:
        return message
    return message.model_copy(update={"tool_calls": tool_calls, "additional_kwargs": {}})


def compaction_boundary(steps: int, keep_recent: int, block: int) -> int:
    """前多少步要压缩：按 block 整块推进，块内不变，保证请求前缀稳定"""
    if steps <= keep_recent + block:
        return 0
    return ((steps - keep_recent) // block) * block


def compact_messages(
    messages: List[BaseMessage], keep_recent: int = KEEP_RECENT_STEPS, block: int = BLOCK_STEPS
) -> List[BaseMessage]:
    """至少保留最近 keep_recent 次工具调用及其结果的原文，更早的按整块截短"""
    ai_indexes = [i for i, m in enumerate(messages) if isinstance(m, AIMessage) and m.tool_calls]
    compact_steps = compaction_boundary(len(ai_indexes), keep_recent, block)
    if compact_steps == 0:
        return messages
    boundary = ai_indexes[compact_steps]
    compacted = []
    for i, message in enumerate(messages):
        if i < boundary and isinstance(message, ToolMessage):
            message = _clip_tool_message(message)
        elif i < boundary and isinstance(message, AIMessage) and message.tool_calls:
            message = _clip_ai_message(message)
        compacted.append(message)
    return compacted


class CompactHistoryMiddleware(AgentMiddleware):
    def __init__(self, keep_recent: int = KEEP_RECENT_STEPS, block: int = BLOCK_STEPS):
        super().__init__()
        self.keep_recent = keep_recent
        self.block = block

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(request.override(messages=compact_messages(request.messages, self.keep_recent, self.block)))
