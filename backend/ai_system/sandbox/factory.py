"""Select an execution backend once per kernel; never silently fall back to local."""
from .remote_kernel import RemoteKernel
from .settings import SandboxSettings


def create_kernel(workspace_dir, *, timeout=180, fs=None, settings=None, work_id=None, experiment_id=None):
    settings = settings or SandboxSettings.from_env()
    if settings.provider == "local":
        raise ValueError("项目执行必须使用 OpenSandbox；本机内核仅供独立单元测试")
    if settings.provider == "opensandbox":
        from .opensandbox_provider import OpenSandboxSession
        return RemoteKernel(workspace_dir, OpenSandboxSession(settings, work_id=work_id, experiment_id=experiment_id, workspace_dir=workspace_dir), settings, timeout=timeout, fs=fs)
    raise ValueError(f"未支持的沙箱平台: {settings.provider}")
