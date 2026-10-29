"""Bounded, credential-free public fetching with DNS pinning and redirect checks."""
import asyncio
import http.client
import ipaddress
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from urllib.request import proxy_bypass


class FetchError(ValueError):
    pass


def public_url(value: str) -> str:
    if len(value) > 2048 or any(ord(c) < 33 for c in value) or any(c in value for c in '\\<>"{}'):
        raise FetchError('URL 格式无效')
    try:
        p = urlsplit(value)
        port = p.port
        host = (p.hostname or '').encode('idna').decode().lower().rstrip('.')
    except (ValueError, UnicodeError):
        raise FetchError('URL 格式无效') from None
    if (p.scheme not in {'http', 'https'} or not host or p.username is not None or p.password is not None
            or port not in (None, 443 if p.scheme == 'https' else 80)):
        raise FetchError('仅允许不含认证信息的公开 HTTP(S) 默认端口 URL')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise FetchError('禁止访问本机或内网地址')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise FetchError('禁止访问本机或内网地址')
    if any(k.lower() in {'token', 'api_key', 'apikey', 'access_token', 'signature', 'x-amz-signature'}
           for k, _ in parse_qsl(p.query)):
        raise FetchError('不接受包含访问凭据的 URL')
    netloc = f'[{host}]' if ':' in host else host
    return urlunsplit((p.scheme, netloc, p.path or '/', p.query, ''))


def public_addresses(host, port):
    try:
        values = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except OSError:
        raise FetchError('来源域名解析失败') from None
    if not values or any(not ipaddress.ip_address(value).is_global for value in values):
        raise FetchError('来源解析到非公开地址，已拒绝')
    return values


@dataclass
class FetchBudget:
    max_requests: int = 6
    seconds: float = 25
    requests: int = 0
    started: float = field(default_factory=time.monotonic)

    def remaining(self):
        value = self.seconds - (time.monotonic() - self.started)
        if value <= 0:
            raise FetchError('检索时间预算已用尽')
        return min(value, 8)

    def take(self):
        self.remaining()
        if self.requests >= self.max_requests:
            raise FetchError('检索请求预算已用尽')
        self.requests += 1


@dataclass
class PublicDocument:
    url: str
    body: bytes
    content_type: str


def dial_public(ip, port, hostname, scheme, timeout, source_address=None):
    """A trusted server-configured HTTP CONNECT proxy may be the egress path.

    The source cannot choose the proxy, and CONNECT targets the validated IP,
    not a hostname the proxy might resolve differently. TLS is wrapped by the
    caller with the original source hostname after the tunnel is established.
    """
    proxy_url = os.getenv('HTTPS_PROXY' if scheme=='https' else 'HTTP_PROXY','')
    if not proxy_url or proxy_bypass(hostname):
        return socket.create_connection((ip,port),timeout,source_address)
    proxy = urlsplit(proxy_url)
    if proxy.scheme!='http' or not proxy.hostname or proxy.username is not None or proxy.password is not None:
        raise FetchError('环境代理需要无认证的 HTTP CONNECT 传输')
    tunnel = http.client.HTTPConnection(proxy.hostname,proxy.port or 80,timeout=timeout)
    try:
        tunnel.set_tunnel(ip,port,headers={'Host':hostname})
        tunnel.connect()
        sock = tunnel.sock
        tunnel.sock = None
        return sock
    except BaseException:
        tunnel.close()
        raise


def fetch_public(url: str, budget: FetchBudget, max_bytes=2_000_000) -> PublicDocument:
    """Resolve once per hop and connect to that validated IP; TLS still uses hostname."""
    current = public_url(url)
    for hop in range(3):
        budget.take()
        p = urlsplit(current)
        port = 443 if p.scheme == 'https' else 80
        addresses = public_addresses(p.hostname, port)
        timeout = budget.remaining()
        connection = (http.client.HTTPSConnection(p.hostname, port, timeout=timeout, context=ssl.create_default_context())
                      if p.scheme == 'https' else http.client.HTTPConnection(p.hostname, port, timeout=timeout))
        # HTTPConnection.connect uses this factory. Keeping .host unchanged
        # preserves Host and HTTPS SNI/hostname certificate validation.
        connection._create_connection = lambda _address, timeout=timeout, source_address=None: dial_public(
            addresses[0],port,p.hostname,p.scheme,timeout,source_address)
        try:
            path = urlunsplit(('', '', p.path, p.query, ''))
            connection.request('GET', path, headers={'User-Agent':'PaperAgent-Literature/1.0',
                'Accept':'application/json, application/xml, text/html, application/pdf, text/plain', 'Accept-Encoding':'identity'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                if hop == 2 or not response.getheader('Location'):
                    raise FetchError('来源重定向次数超限')
                target = public_url(urljoin(current, response.getheader('Location')))
                if p.scheme == 'https' and urlsplit(target).scheme != 'https':
                    raise FetchError('拒绝 HTTPS 降级重定向')
                current = target
                continue
            if response.status != 200:
                raise FetchError(f'来源返回 HTTP {response.status}')
            if response.getheader('Content-Encoding', 'identity').lower() not in {'identity', ''}:
                raise FetchError('来源返回不受支持的压缩内容')
            size = response.getheader('Content-Length')
            if size and (not size.isdigit() or int(size) > max_bytes):
                raise FetchError('来源内容超过大小预算')
            parts, total = [], 0
            while True:
                connection.sock.settimeout(budget.remaining()) if connection.sock else None
                chunk = response.read(min(16384, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError('来源内容超过大小预算')
                parts.append(chunk)
            return PublicDocument(current, b''.join(parts), response.getheader('Content-Type', '').split(';')[0].lower())
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError('来源连接失败或超时') from None
        finally:
            connection.close()
    raise FetchError('来源重定向次数超限')


async def fetch_document(url, budget, max_bytes=2_000_000):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fetch_public, url, budget, max_bytes),
                                      timeout=max(0.1, budget.seconds - (time.monotonic() - budget.started)))
    except TimeoutError:
        raise FetchError('检索时间预算已用尽') from None
