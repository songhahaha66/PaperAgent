"""
Shared API settings for LLM and vision clients.

The UI-stored model config remains the primary source. PAPERAGENT_API_*
environment variables are a testing fallback so Cloud Agents can reuse a
known OpenAI-compatible gateway without opening the admin page.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse


DEFAULT_MODEL = "grok-4.6"
DEFAULT_PROVIDER = "openai"


@dataclass(frozen=True)
class EnvApiSettings:
    api_key: str
    base_url: str
    model_id: str
    provider: str = DEFAULT_PROVIDER
    # 供应商私有参数（PAPERAGENT_API_EXTRA_BODY，JSON），如 DeepSeek 官方 API 关闭思考模式 {"thinking": {"type": "disabled"}}
    extra_body: Optional[Dict[str, Any]] = None


def normalize_openai_base_url(url: str) -> str:
    """
    OpenAI-compatible gateways (Sub2API / New API) require a `/v1` suffix.

    Users often paste `http://host:port`. Without `/v1`, LangChain posts to
    `/chat/completions` and the gateway returns the SPA HTML instead of JSON.
    """
    if not url:
        return ""

    cleaned = url.strip()
    parsed = urlparse(cleaned if "://" in cleaned else f"http://{cleaned}")
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v1"):
        return cleaned.rstrip("/")
    if path in ("", "/"):
        return cleaned.rstrip("/") + "/v1"
    return cleaned.rstrip("/")


def load_env_api_settings() -> Optional[EnvApiSettings]:
    api_key = (
        os.getenv("PAPERAGENT_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.getenv("PAPERAGENT_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or ""
    ).strip()
    model_id = (
        os.getenv("PAPERAGENT_API_MODEL")
        or os.getenv("OPENAI_MODEL")
        or DEFAULT_MODEL
    ).strip()
    provider = (os.getenv("PAPERAGENT_API_PROVIDER") or DEFAULT_PROVIDER).strip()
    extra_body_raw = (os.getenv("PAPERAGENT_API_EXTRA_BODY") or "").strip()

    if not api_key or not base_url:
        return None

    return EnvApiSettings(
        api_key=api_key,
        base_url=normalize_openai_base_url(base_url),
        model_id=model_id or DEFAULT_MODEL,
        provider=provider or DEFAULT_PROVIDER,
        extra_body=json.loads(extra_body_raw) if extra_body_raw else None,
    )


class EnvModelConfig:
    """Duck-typed stand-in for models.ModelConfig."""

    def __init__(self, settings: EnvApiSettings, system_type: str = "brain"):
        self.type = system_type
        self.provider = settings.provider
        self.model_id = settings.model_id
        self.base_url = settings.base_url
        self.api_key = settings.api_key
        self.extra_body = settings.extra_body
        self.is_active = True
        self.id = None
        self.created_by = None
