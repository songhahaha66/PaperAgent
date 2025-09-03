from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional, TypeVar
from fastapi import HTTPException, status

T = TypeVar("T")


def http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def handle_service_errors(default_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """装饰器：将任意异常统一为 HTTPException，避免重复 try/except 模板。

    - 已是 HTTPException 则原样抛出
    - 其他异常包裹为 HTTP 500（或指定的默认码）
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - 需要兜底
                raise HTTPException(status_code=default_status, detail=str(exc))

        return wrapper

    return decorator


def model_to_dict(model_obj: Any, *, exclude_unset: bool = False) -> Dict[str, Any]:
    """Pydantic v1/v2 兼容转换函数。

    - v2: 使用 model_dump
    - v1: 使用 dict
    """
    if hasattr(model_obj, "model_dump"):
        return model_obj.model_dump(exclude_unset=exclude_unset)
    return model_obj.dict(exclude_unset=exclude_unset)



