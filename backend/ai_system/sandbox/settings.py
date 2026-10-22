"""Server-side configuration only; model tool arguments cannot choose a provider."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SandboxSettings:
    provider: str = "opensandbox"
    endpoint: str = "http://127.0.0.1:18080"
    api_key: str = field(default="", repr=False)
    image: str = "paperagent-research:workspace-v2"
    cpu: str = "2"
    memory: str = "2Gi"
    lifetime: int = 1800
    startup_timeout: int = 120
    max_file_bytes: int = 64 * 1024 * 1024
    host_workspace_root: str = ""
    host_evidence_root: str = ""
    max_log_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(cls):
        # An explicit file keeps local credentials out of source and existing .env.
        values = {}
        if path := os.environ.get("PAPERAGENT_SANDBOX_CONFIG"):
            from dotenv import dotenv_values
            path = Path(path).expanduser()
            if not path.is_file():
                raise ValueError("指定的沙箱配置文件不存在；不会回退本机执行")
            values.update(dotenv_values(path))
        values.update(os.environ)
        fields = {}
        for name in cls.__dataclass_fields__:
            value = values.get("PAPERAGENT_SANDBOX_" + name.upper())
            if value is not None:
                fields[name] = int(value) if isinstance(getattr(cls, name, None), int) else value
        settings = cls(**fields)
        if any(getattr(settings, n) <= 0 for n in ("lifetime", "startup_timeout", "max_file_bytes", "max_log_bytes")):
            raise ValueError("沙箱限额必须为正数")
        if settings.provider != "local":
            url = urlsplit(settings.endpoint)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.query or url.fragment or url.path not in {"", "/"}:
                raise ValueError("沙箱 endpoint 必须是 http(s) 服务地址，不含路径或凭据")
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("远端沙箱使用 HTTPS，或通过 SSH 隧道连接 localhost")
            if not settings.api_key:
                raise ValueError("远端沙箱缺少 API key")
        return settings
