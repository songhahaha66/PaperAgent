"""Server-side configuration only; model tool arguments cannot choose a provider."""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SandboxSettings:
    provider: str = "opensandbox"
    endpoint: str = "http://127.0.0.1:18080"
    api_key: str = field(default="", repr=False)
    image: str = "paperagent-research:workspace-v2"
    cpu: str = "1"
    memory: str = "768Mi"
    lifetime: int = 1800
    # OpenSandbox server.max_sandbox_timeout_seconds. One create/renew request
    # cannot ask for more than this; the research runtime renews or replaces.
    max_timeout: int = 3600
    startup_timeout: int = 120
    max_file_bytes: int = 64 * 1024 * 1024
    host_workspace_root: str = ""  # OpenSandbox-only; retained for env-file compatibility.
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
        if any(getattr(settings, n) <= 0 for n in ("lifetime", "max_timeout", "startup_timeout", "max_file_bytes", "max_log_bytes")):
            raise ValueError("沙箱限额必须为正数")
        # Authentication and endpoint syntax belong to the selected adapter;
        # another provider may use a different protocol or credential mechanism.
        return settings
