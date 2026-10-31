"""Sandbox lifetime, confirmed loss, and the one safe retry.

OpenSandbox 1.1.1 `Sandbox.renew` sets expiration to now + duration. The server
setting `max_sandbox_timeout_seconds` caps a single create timeout (production
uses 3600). Renewal itself is not capped by that setting, but a refused renewal
or a grant that still cannot cover the next call must be replaced before the
call starts.

A generic connection drop is not proof the process died. Confirmed expiry,
peer-closed/502, and 404 may rebuild once. Ambiguous completion and an already
submitted compiler command stay non-replayable.
"""
import asyncio


RENEW_MARGIN = 30
REPLACED_NOTE = '计算沙箱达到单次寿命上限，已提前更换并继续。工作区文件已恢复；原 Python 内存不再保留。'
RETRIED_NOTE = '计算沙箱已到期/已断开，已自动重建并重试'
RETRY_FAILED_PREFIX = '计算沙箱已到期/已断开，已自动重建并重试，仍然失败：'


class SandboxLifetimeCap(RuntimeError):
    """This sandbox cannot be extended far enough for the next operation."""


class SandboxUnavailable(RuntimeError):
    """A sandbox failure whose message is safe to show to the user and the model."""


def granted_lifetime(settings) -> int:
    """Single expiration window actually requested from the server."""
    return min(int(settings.lifetime), int(settings.max_timeout))


def renewal_seconds(settings, needed) -> int:
    """Extend by enough to finish `needed`, but never past one server grant."""
    required = int(needed) + RENEW_MARGIN
    cap = int(settings.max_timeout)
    if required > cap:
        return cap
    return min(cap, max(int(settings.lifetime), required))


def _chain(exc):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)


def confirmed_sandbox_loss(exc) -> bool:
    """The provider dropped this sandbox. A bare connection error is not enough."""
    if exc is None or isinstance(exc, (SandboxLifetimeCap, asyncio.TimeoutError, TimeoutError)):
        return False
    for current in _chain(exc):
        name = type(current).__name__
        if name == 'RuntimeGone':
            return True
        status = getattr(current, 'status_code', None)
        text = str(current).lower()
        if status == 404 or 'sandbox not found' in text or '原沙箱已不存在' in text:
            return True
        if 'peer closed' in text or 'remoteprotocolerror' in text:
            return True
        if name in {'SandboxApiException', 'SandboxConnectionException', 'SandboxUnhealthyException'} and status in {502, 503, 504}:
            return True
        if any(token in text for token in ('已到期', 'sandbox expired', 'ttl expired', 'expiration')):
            return True
    return False


def replayable_sandbox_loss(exc, *, execution_finished: bool) -> bool:
    if execution_finished or not confirmed_sandbox_loss(exc):
        return False
    text = str(exc)
    if '不会自动重跑' in text or '没有执行完成回执' in text or '不会重放' in text:
        return False
    return True


def loss_phrase(exc) -> str:
    for current in _chain(exc):
        status = getattr(current, 'status_code', None)
        text = str(current).lower()
        if status == 404 or type(current).__name__ == 'RuntimeGone' or '不存在' in text or 'not found' in text:
            return '计算沙箱已不存在'
        if any(token in text for token in ('已到期', 'expired', 'expiration', 'ttl')):
            return '计算沙箱已到期'
    return '计算沙箱已断开'


def readable_sandbox_error(error) -> str | None:
    """User-facing text for a sandbox failure, or None when this is some other error."""
    if isinstance(error, (SandboxUnavailable, SandboxLifetimeCap)):
        text = str(error).strip()
        if isinstance(error, SandboxLifetimeCap) and not text.startswith('计算沙箱'):
            text = '计算沙箱达到单次寿命上限，无法继续续期：' + text
        return text[:2000]
    if not confirmed_sandbox_loss(error):
        return None
    phrase = loss_phrase(error)
    detail = str(error).strip()
    if detail and phrase not in detail:
        return f'{phrase}：{detail[:500]}'
    return phrase


def cap_rejected(exc) -> bool:
    text = str(exc).lower()
    status = getattr(exc, 'status_code', None)
    return status == 400 and any(token in text for token in ('timeout', 'exceed', 'max_sandbox', '上限'))
