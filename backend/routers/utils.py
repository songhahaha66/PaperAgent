from __future__ import annotations

import functools
from typing import Any, Callable, Coroutine, TypeVar
from fastapi import HTTPException, status

T = TypeVar("T")


def ok(data: Any = None, **extra: Any) -> dict:
    resp = {"status": "success"}
    if data is not None:
        resp["data"] = data
    resp.update(extra)
    return resp


def fail(detail: str, code: int = status.HTTP_400_BAD_REQUEST, **extra: Any) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=extra or None)


def route_guard(func: Callable[..., Coroutine[Any, Any, Any]]):
    """统一路由层异常处理：保留 HTTPException，其他异常转 500。"""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any):
        try:
            return await func(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return wrapper


