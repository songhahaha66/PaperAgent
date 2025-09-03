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


def ensure_owner(entity: Any, user_id: int, *, owner_attr: str = "created_by", not_found_status: int = status.HTTP_404_NOT_FOUND, forbidden_status: int = status.HTTP_403_FORBIDDEN, not_found_detail: str = "Resource not found", forbidden_detail: str = "Not authorized to access this resource") -> Any:
    """通用所有者校验。

    - entity 为 None 则抛 404
    - entity.owner_attr != user_id 则抛 403
    - 返回 entity 以便链式使用
    """
    if entity is None:
        raise HTTPException(status_code=not_found_status, detail=not_found_detail)
    owner_value = getattr(entity, owner_attr, None)
    if owner_value != user_id:
        raise HTTPException(status_code=forbidden_status, detail=forbidden_detail)
    return entity



