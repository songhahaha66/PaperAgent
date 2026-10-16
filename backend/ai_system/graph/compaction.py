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

import logging
from typing import Any, Awaitable, Callable, List

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

logger = logging.getLogger(__name__)

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


class StepBudgetMiddleware(AgentMiddleware):
    """步数快用完时提醒模型交报告

    超过 recursion_limit 时 LangGraph 直接抛异常，已经做的工作和对话全部丢失（2024_C 试跑中一个探索分支跑了 10 次代码后因此作废）。
    这里在剩余步数不多时，在请求末尾追加一条提醒；只追加在末尾，不影响前缀缓存。最后一步只给报告工具，强制收尾。
    """

    def __init__(self, budget: int, reserve: int, message: str):
        super().__init__()
        self.budget = budget
        self.reserve = reserve
        self.message = message

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        # recursion_limit 按单次调用计：只数最后一条用户消息（任务、晋升、返工意见）之后的步数
        last_human = max((i for i, m in enumerate(request.messages) if isinstance(m, HumanMessage)), default=-1)
        steps = sum(1 for m in request.messages[last_human + 1:] if isinstance(m, AIMessage) and m.tool_calls)
        if steps >= self.budget:
            # Failed structured submissions return directly to the model without
            # a tools node, so recursion_limit alone is not a tool-step budget.
            raise GraphRecursionError(f"工具调用步数已达到预算 {self.budget}")
        if steps >= self.budget - 1:
            # 最后一步只留报告工具（ToolStrategy 下 tool_choice=any，模型只能交报告），不会撞 recursion_limit。
            # 官方 API 的 deepseek-flash 一步只要几秒、步子很碎，v2l 的 Q1 三分钟用完 40 步没交报告，对话和报告全丢
            note = HumanMessage(content=f"[系统提醒] 步数已用完，不能再执行代码。{self.message}")
            return await handler(request.override(messages=[*request.messages, note], tools=[]))
        if steps >= self.budget - self.reserve:
            left = max(self.budget - steps, 0)
            note = HumanMessage(content=f"[系统提醒] 还剩约 {left} 步。{self.message}")
            request = request.override(messages=[*request.messages, note])
        return await handler(request)


def drop_refused_steps(messages: List[BaseMessage], refusal: str) -> List[BaseMessage]:
    """去掉所有工具调用都被拒绝（结果是 refusal）的那几步：AI 消息连同它的工具结果一起去掉

    只有部分调用被拒时整步保留，否则剩下的调用没有对应结果，接口直接 400。
    """
    refused = {m.tool_call_id for m in messages if isinstance(m, ToolMessage) and m.content == refusal}
    if not refused:
        return messages
    drop_calls = set()
    kept = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            ids = {c.get("id") for c in message.tool_calls}
            if ids <= refused:
                drop_calls |= ids
                continue
        if isinstance(message, ToolMessage) and message.tool_call_id in drop_calls:
            continue
        kept.append(message)
    return kept


class DropRefusedStepsMiddleware(AgentMiddleware):
    """请求里不带被拒绝的空执行

    v3 的 2022_C Q3 先打了几次 print('ok')，被拒后照着历史里的样子越打越多，57 次空执行把步数用光，
    报告只剩"..."。历史里不留这些步，模型就没有可模仿的样子。放在 StepBudget 里层：预算照样按全部步数算。
    去掉是确定性的，同一段历史每次去掉的内容一样，不破坏前缀缓存。
    """

    def __init__(self, refusal: str):
        super().__init__()
        self.refusal = refusal

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(request.override(messages=drop_refused_steps(request.messages, self.refusal)))


class CompactHistoryMiddleware(AgentMiddleware):
    def __init__(self, keep_recent: int = KEEP_RECENT_STEPS, block: int = BLOCK_STEPS):
        super().__init__()
        self.keep_recent = keep_recent
        self.block = block

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(request.override(messages=compact_messages(request.messages, self.keep_recent, self.block)))


class HistoryRecorder(AgentMiddleware):
    """记下最近一次发给模型的完整对话（放在中间件最外层，看到的是压缩前的原文）

    超过 recursion_limit 时 LangGraph 直接抛异常、拿不到对话；有了这份记录，可以接着要一份报告，而不是整段作废。
    """

    def __init__(self):
        super().__init__()
        self.messages: List[BaseMessage] = []

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        self.messages = list(request.messages)
        return await handler(request)


TRUNCATED_NOTE = "[系统提醒] 你上一次输出太长，在中途被截断，没有执行。请把代码拆成更短的一段，只写这一步需要的部分。"
MALFORMED_NOTE = "[系统提醒] 你上一次工具调用的参数不是合法 JSON，没有执行。请重新调用，代码里的引号和换行要正确转义。"


def drop_invalid_calls(message: AIMessage) -> AIMessage:
    """参数解析失败的工具调用不能留在历史里：发回接口时会被当成正常调用，又没有对应的工具结果，接口直接 400"""
    if not message.invalid_tool_calls:
        return message
    return message.model_copy(update={"invalid_tool_calls": []})


class TruncationGuardMiddleware(AgentMiddleware):
    """工具调用不完整时带提示重试一次：输出撞到长度上限，或参数不是合法 JSON。

    这两种情况下 tool_calls 为空，循环会误以为模型收尾了；坏掉的调用留在历史里，
    下一次请求还会因为"工具调用没有对应结果"被接口拒绝（官方 DeepSeek API 返回 400，v2l 的 Q1 因此整段失败）。
    """

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        response = await handler(request)
        messages = getattr(response, "result", None) or [response]
        message = next((m for m in messages if isinstance(m, AIMessage)), None)
        if message is None:
            return response
        if (message.response_metadata or {}).get("finish_reason") == "length":
            note = TRUNCATED_NOTE
            # 截断的调用会被丢掉、transcript 里看不到；记下开头和结尾，能看出是代码太长还是重复输出停不下来
            partial = " ".join(str(c.get("args") or "") for c in message.invalid_tool_calls) or str(message.content)
            logger.warning("输出被截断（%d 字）：%r ... %r", len(partial), partial[:150], partial[-300:])
        elif message.invalid_tool_calls:
            note = MALFORMED_NOTE
        else:
            return response
        retried = await handler(request.override(messages=[*request.messages, HumanMessage(content=note)]))
        if hasattr(retried, "result"):
            retried.result = [drop_invalid_calls(m) if isinstance(m, AIMessage) else m for m in retried.result]
            return retried
        return drop_invalid_calls(retried) if isinstance(retried, AIMessage) else retried
