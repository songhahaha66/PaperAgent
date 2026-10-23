"""
LLM 调用小工具：结构化输出与纯文本输出，带有限次重试
"""

import json
import logging
from typing import Any, Callable, List, Type, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
RETRIES = 2
STRUCTURED_MAX_TOKENS = 6000


def structured_model(llm: Any, schema: Type[T], *, include_raw: bool = False):
    """OpenAI 兼容网关普遍支持工具调用，但不一定支持 json_schema，所以优先用 function_calling"""
    try:
        return llm.with_structured_output(schema, method="function_calling", include_raw=include_raw)
    except (TypeError, ValueError, NotImplementedError):
        return llm.with_structured_output(schema)


class InvalidStructuredReply(ValueError):
    def __init__(self, error: Exception, reply: BaseMessage):
        self.truncated=(getattr(reply, "response_metadata", {}) or {}).get("finish_reason") == "length"
        if self.truncated:
            error = ValueError("输出达到长度上限被截断；请缩短描述和候选方案，返回完整闭合对象，保留全部原题要求，不要反复扩展假设和检查列表")
        super().__init__(str(error))
        calls = getattr(reply, "tool_calls", [])
        invalid = getattr(reply, "invalid_tool_calls", [])
        content = (json.dumps(calls[0]["args"], ensure_ascii=False) if calls else
                   str(invalid[0].get("args") or "") if invalid else message_text(reply))
        self.candidate = AIMessage(content=content)


async def _ask_json(llm: Any, schema: Type[T], messages: List[BaseMessage], error: Exception | None = None) -> T:
    from ..judge.llm import parse_verdict

    instruction = (
        "不要调用工具，也不要写其他内容，只输出一个符合下面 JSON Schema 的 JSON 对象：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )
    if error is not None:
        instruction += "\n\n上一次输出未通过校验，请针对以下错误修正，保留原题要求：\n" + str(error)[:2000]
    reply = await llm.ainvoke(list(messages) + [HumanMessage(content=instruction)])
    if (getattr(reply, "response_metadata", {}) or {}).get("finish_reason") == "length":
        raise InvalidStructuredReply(ValueError("被截断"), reply)
    text = message_text(reply)
    parsed = parse_verdict(text, schema)
    if parsed is not None:
        return parsed
    # Keep the actual field errors; parse_verdict deliberately swallows these
    # for judge responses, but retries here need actionable feedback.
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        try:
            return schema.model_validate(data)
        except ValueError as exc:
            raise InvalidStructuredReply(exc, reply) from exc
    raise InvalidStructuredReply(ValueError("没有完整的 JSON 对象；请输出简洁、闭合的对象，不要重复扩展列表"), reply)


def cap_output(llm: Any, max_tokens: int) -> Any:
    """限制单次输出长度：模型偶尔重复输出停不下来，16000 token 按网关约 35 token/秒要等七八分钟"""
    fields = getattr(type(llm), "model_fields", {})
    if "max_tokens" not in fields:
        return llm
    current = getattr(llm, "max_tokens", None)
    if current is not None and current <= max_tokens:
        return llm
    return llm.model_copy(update={"max_tokens": max_tokens})


def as_messages(system: str, user: str) -> List[BaseMessage]:
    return [SystemMessage(content=system), HumanMessage(content=user)]


async def call_structured(llm: Any, schema: Type[T], messages: List[BaseMessage], *, validate: Callable[[T], None] | None = None,retries: int=RETRIES,json_first: bool=False) -> T:
    llm = cap_output(llm, STRUCTURED_MAX_TOKENS)
    model = None if json_first else structured_model(llm, schema, include_raw=True)
    last_error: Exception | None = None
    retry_messages = messages
    from ..research.runtime import ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError
    for attempt in range(retries + 1):
        parsed = None
        try:
            # 工具调用的参数不合 schema（如必填列表给了空数组）时，再用同样的方式问往往还是一样，
            # 重试改走纯文本 JSON
            result = await model.ainvoke(messages) if attempt == 0 and not json_first else await _ask_json(llm, schema, retry_messages, last_error)
            if isinstance(result, dict) and "raw" in result and "parsed" in result:
                if (getattr(result["raw"], "response_metadata", {}) or {}).get("finish_reason") == "length":
                    raise InvalidStructuredReply(ValueError("被截断"), result["raw"])
                raw=result['raw'];parsed_reply=result['parsed']
                if parsed_reply is None:
                    from ..judge.llm import parse_verdict
                    parsed_reply=parse_verdict(message_text(raw),schema)
                if parsed_reply is None:
                    # Some providers return neither a parsed value nor an error.
                    # Keep public content/tool arguments for the existing JSON
                    # repair, without forwarding metadata or reasoning_content.
                    raise InvalidStructuredReply(result.get('parsing_error') or ValueError("模型没有返回结构化结果"),raw)
                result = parsed_reply
            if result is None:
                raise ValueError("模型没有返回结构化结果")
            parsed = result if isinstance(result, schema) else schema.model_validate(result)
            if validate is not None:
                validate(parsed)
            return parsed
        except Exception as exc:  # 网关偶发错误或模型输出不合 schema
            if isinstance(exc,(ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError)) or not isinstance(exc,ValueError):raise
            last_error = exc
            if parsed is not None:
                # Repair this candidate, rather than guessing a different plan
                # from an error message with no associated task/check IDs.
                retry_messages = [*messages, AIMessage(content=parsed.model_dump_json())]
            elif isinstance(exc, InvalidStructuredReply):
                # A truncated repetitive list is not a useful repair candidate.
                retry_messages = messages if exc.truncated else [*messages, exc.candidate]
            logger.warning("结构化输出 %s 第 %d 次失败: %s", schema.__name__, attempt + 1, type(exc).__name__)
    candidate=AIMessage(content=parsed.model_dump_json()) if parsed is not None else (
        last_error.candidate if isinstance(last_error,InvalidStructuredReply) else AIMessage(content=''))
    raise InvalidStructuredReply(last_error,candidate) from last_error


def message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


async def call_text(llm: Any, messages: List[BaseMessage]) -> str:
    last_error: Exception | None = None
    from ..research.runtime import ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError
    for attempt in range(RETRIES + 1):
        try:
            text = message_text(await llm.ainvoke(messages)).strip()
            if text:
                return text
            raise ValueError("模型返回了空内容")
        except Exception as exc:
            if isinstance(exc,(ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError)) or not isinstance(exc,ValueError):raise
            last_error = exc
            logger.warning("文本生成第 %d 次失败: %s", attempt + 1, type(exc).__name__)
    raise ValueError(f"文本生成失败: {last_error}") from last_error


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[省略 {len(text) - limit} 字符]"
