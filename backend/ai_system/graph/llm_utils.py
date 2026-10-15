"""
LLM 调用小工具：结构化输出与纯文本输出，带有限次重试
"""

import asyncio
import logging
from typing import Any, List, Type, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
RETRIES = 2


def structured_model(llm: Any, schema: Type[T]):
    """OpenAI 兼容网关普遍支持工具调用，但不一定支持 json_schema，所以优先用 function_calling"""
    try:
        return llm.with_structured_output(schema, method="function_calling")
    except (TypeError, ValueError, NotImplementedError):
        return llm.with_structured_output(schema)


def as_messages(system: str, user: str) -> List[BaseMessage]:
    return [SystemMessage(content=system), HumanMessage(content=user)]


async def call_structured(llm: Any, schema: Type[T], messages: List[BaseMessage]) -> T:
    model = structured_model(llm, schema)
    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            result = await model.ainvoke(messages)
            if result is None:
                raise ValueError("模型没有返回结构化结果")
            return result if isinstance(result, schema) else schema.model_validate(result)
        except Exception as exc:  # 网关偶发错误或模型输出不合 schema
            last_error = exc
            logger.warning("结构化输出 %s 第 %d 次失败: %s", schema.__name__, attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
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
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"文本生成失败: {last_error}") from last_error


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[省略 {len(text) - limit} 字符]"
