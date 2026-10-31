"""Shared provider selection for research and compilation; no local fallback."""
from .contracts import SandboxSession, SessionFactory
from .remote_kernel import RemoteKernel
from .settings import SandboxSettings


def _opensandbox(settings, **kwargs):
    # Keep vendor imports at the adapter boundary, including optional SDKs.
    from .opensandbox_provider import OpenSandboxSession
    return OpenSandboxSession(settings, **kwargs)


_PROVIDERS: dict[str, SessionFactory] = {"opensandbox": _opensandbox}


def register_provider(name: str, factory: SessionFactory) -> None:
    """Register an adapter at backend startup, before creating any sessions."""
    if not name or name != name.strip() or name == "local":
        raise ValueError("沙箱平台名称无效；不允许注册本机执行回退")
    if name in _PROVIDERS:
        raise ValueError(f"沙箱平台已注册: {name}")
    if not callable(factory):
        raise TypeError("沙箱平台必须提供会话工厂")
    _PROVIDERS[name] = factory


def create_session(workspace_dir, *, settings=None, work_id=None, experiment_id=None,
                   cache_key=None) -> SandboxSession:
    settings = settings or SandboxSettings.from_env()
    if settings.provider == "local":
        raise ValueError("项目执行必须使用远端沙箱；本机内核仅供独立单元测试")
    factory = _PROVIDERS.get(settings.provider)
    if factory is None:
        raise ValueError(f"未支持的沙箱平台: {settings.provider}")
    return factory(settings, workspace_dir=workspace_dir, work_id=work_id,
                   experiment_id=experiment_id, cache_key=cache_key)


def create_kernel(workspace_dir, *, timeout=180, fs=None, settings=None, work_id=None, experiment_id=None, execution_scope=None):
    settings = settings or SandboxSettings.from_env()
    cache_key=None
    if experiment_id not in {None,'default','research'}:
        import hashlib
        from pathlib import Path
        cache_key=hashlib.sha256((str(Path(workspace_dir).absolute())+'\0'+experiment_id).encode()).hexdigest()
    session = create_session(workspace_dir, settings=settings, work_id=work_id,
                             experiment_id=experiment_id,cache_key=cache_key)
    return RemoteKernel(workspace_dir, session, settings, timeout=timeout, fs=fs,
                        execution_scope=execution_scope)
