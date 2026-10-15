"""
LLM 调用小工具：结构化输出与纯文本输出，带有限次重试
"""

import asyncio
import json
import logging
from typing import Any, List, Type, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
RETRIES = 2
RETRY_DELAY = 1.5  # 秒，第 n 次失败后等 n 倍


def structured_model(llm: Any, schema: Type[T]):
    """OpenAI 兼容网关普遍支持工具调用，但不一定支持 json_schema，所以优先用 function_calling"""
    try:
        return llm.with_structured_output(schema, method="function_calling")
    except (TypeError, ValueError, NotImplementedError):
        return llm.with_structured_output(schema)


async def _ask_json(llm: Any, schema: Type[T], messages: List[BaseMessage]) -> T | None:
    from ..judge.llm import parse_verdict

    instruction = (
        "不要调用工具，也不要写其他内容，只输出一个符合下面 JSON Schema 的 JSON 对象：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )
    reply = await llm.ainvoke(list(messages) + [HumanMessage(content=instruction)])
    return parse_verdict(message_text(reply), schema)


def as_messages(system: str, user: str) -> List[BaseMessage]:
    return [SystemMessage(content=system), HumanMessage(content=user)]


async def call_structured(llm: Any, schema: Type[T], messages: List[BaseMessage]) -> T:
    model = structured_model(llm, schema)
    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            # 工具调用的参数不合 schema（如必填列表给了空数组）时，再用同样的方式问往往还是一样，
            # 重试改走纯文本 JSON
            result = await model.ainvoke(messages) if attempt == 0 else None
            if result is None:
                # step-5-preview 等模型偶尔无视强制工具调用、直接写文字，改要纯文本 JSON 再解析
                result = await _ask_json(llm, schema, messages)
            if result is None:
                raise ValueError("模型没有返回结构化结果")
            return result if isinstance(result, schema) else schema.model_validate(result)
        except Exception as exc:  # 网关偶发错误或模型输出不合 schema
            last_error = exc
            logger.warning("结构化输出 %s 第 %d 次失败: %s", schema.__name__, attempt + 1, exc)
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"结构化输出 {schema.__name__} 失败: {last_error}") from last_error


def message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


async def call_text(llm: Any, messages: List[BaseMessage]) -> str:
    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            text = message_text(await llm.ainvoke(messages)).strip()
            if text:
                return text
            raise ValueError("模型返回了空内容")
        except Exception as exc:
            last_error = exc
            logger.warning("文本生成第 %d 次失败: %s", attempt + 1, exc)
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"文本生成失败: {last_error}") from last_error


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[省略 {len(text) - limit} 字符]"
