from __future__ import annotations

from typing import Any, Dict, TypeVar
from fastapi import HTTPException, status

T = TypeVar("T")


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
