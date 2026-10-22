"""Select an execution backend once per kernel; never silently fall back to local."""
from .kernel import PythonKernel
from .remote_kernel import RemoteKernel
from .settings import SandboxSettings


def create_kernel(workspace_dir, *, timeout=180, fs=None, settings=None):
    settings = settings or SandboxSettings.from_env()
    if settings.provider == "local":
        return PythonKernel(workspace_dir, timeout=timeout, fs=fs)
    if settings.provider == "opensandbox":
        from .opensandbox_provider import OpenSandboxSession
        return RemoteKernel(workspace_dir, OpenSandboxSession(settings), settings, timeout=timeout, fs=fs)
    raise ValueError(f"未支持的沙箱平台: {settings.provider}")
