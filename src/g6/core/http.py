# SPDX-License-Identifier: Apache-2.0
"""Outbound HTTP for components (FW-10): every request, and every redirect hop, is checked before it is sent.

:func:`check_url` refuses any scheme but ``http`` and ``https``, local names, and any host that is, or resolves to, an
address that is not public. That includes an IPv4 address carried inside one of four IPv6 forms that Python's
``ipaddress`` otherwise calls public: the NAT64 well-known prefix ``64:ff9b::/96``, the IPv4-compatible ``::/96``, the
IPv4-translated ``::ffff:0:0:0/96`` and the IPv4-mapped ``::ffff:0:0/96``. 6to4, Teredo and the local-use NAT64 prefix
are refused because ``ipaddress`` already calls them non-public. A stated limit: ISATAP identifiers and network-specific
NAT64 prefixes (chosen by each provider, so no table can list them) carry an IPv4 and pass.

:class:`Client` resolves each host once, checks every address, and connects to the address it checked, so a second
lookup cannot swap in another one (DNS rebinding). It follows redirects itself, checking each ``Location`` before the
next request, keeping only a few harmless headers (``Accept``, ``User-Agent`` and the like) once a hop leaves the
origin, so no key in ``Authorization`` or a header of the service's own goes with it (an ``http`` to ``https`` upgrade
of the same host keeps all but ``Cookie`` and ``Proxy-Authorization``), and changing the method as browsers do (301
turns POST into GET; 302 and 303 turn everything but HEAD into GET; 307 and 308 keep it). It applies a time limit to
each request as a whole (not to each read, so a server cannot drip a reply past it), a cap on the body, a pause
between requests to one host, and, when asked, the site's ``robots.txt``. Every failure is an
:class:`HTTPError` with a code: a refused address, a network failure, a 4xx or 5xx status (a 403 is an error, not a
page), a body over the cap, a page ``robots.txt`` disallows. The resolver and the transport can be replaced (tests use
both). It uses the standard library only and never a proxy.

:meth:`Client.download` streams one file to disk under the same rules: every hop is checked, pinned and paced as above
(a redirect to a download host is followed like any other), and the body is read in pieces by the client's stream
transport (:func:`stdlib_stream_transport`; tests replace it), written to a temporary file beside the destination and
hashed as it comes. It is refused (``TOO_LARGE``) past ``expected_bytes``, before any of the body is read when the
reply's ``Content-Length`` already says so; a body that ends short or whose SHA-256 differs is ``MISMATCH``. On any
failure the temporary file is deleted and whatever was at the destination is left as it was; only a body of exactly
the expected size and hash is renamed into place, in one step. The client's ``timeout`` is the longest wait for any one
read, and ``deadline`` the time allowed for the whole download.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import os
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.robotparser
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REFUSED = "G6_E_HTTP_REFUSED"
FAILED = "G6_E_HTTP_FAILED"
STATUS = "G6_E_HTTP_STATUS"
TOO_LARGE = "G6_E_HTTP_TOO_LARGE"
ROBOTS = "G6_E_HTTP_ROBOTS"
MISMATCH = "G6_E_HTTP_MISMATCH"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MIN_INTERVAL = 1.0
#: The time :meth:`Client.download` allows for a whole download unless told otherwise: four hours.
DEFAULT_DOWNLOAD_DEADLINE = 4 * 3600.0
USER_AGENT = "g6"

_CREDENTIALS = frozenset({"authorization", "cookie", "proxy-authorization"})
#: The only caller headers a hop to another origin keeps: a service's key often travels in a header of its own (an
#: ``X-Api-Key``, Brave's ``X-Subscription-Token``), so every other header is dropped there, not only ``_CREDENTIALS``.
_CROSS_ORIGIN_SAFE = frozenset({"accept", "accept-language", "user-agent", "content-type", "content-language",
                                "cache-control"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_TRANSLATED = ipaddress.IPv6Network("::ffff:0:0:0/96")
_COMPATIBLE = ipaddress.IPv6Network("::/96")
_SHA256 = re.compile(r"[0-9a-f]{64}")
#: The most one read of a streamed body asks for.
_CHUNK = 1024 * 1024

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]


class HTTPError(Exception):
    """A refused or failed request: ``code`` is one of this module's ``G6_E_HTTP_*`` codes."""

    def __init__(self, code: str, message: str, *, url: str = "", status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.url, self.status = code, url, status


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    ip: str                       # the checked address the transport must connect to
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float
    max_bytes: int
    read_timeout: float | None = None   # a streamed reply's longest wait for one read (None: timeout)


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]    # names in lower case
    body: bytes
    url: str


def _nothing() -> None:
    return None


@dataclass(frozen=True)
class Stream:
    """A reply whose body is read as it comes: ``chunks`` yields it in pieces, and ``close`` ends the connection (the
    client calls it once, when the body is read or given up)."""

    status: int
    headers: Mapping[str, str]    # names in lower case once the client has it
    chunks: Iterable[bytes]
    url: str
    close: Callable[[], None] = _nothing


@dataclass(frozen=True)
class Downloaded:
    """A file :meth:`Client.download` put in place: its path, size and SHA-256, and the address of the last hop."""

    path: Path
    size: int
    sha256: str
    url: str


Transport = Callable[[Request], Response]
StreamTransport = Callable[[Request], Stream]


def _embedded_ipv4(ip: Address) -> ipaddress.IPv4Address | None:
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64 or ip in _TRANSLATED or ip in _COMPATIBLE:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _forbidden(ip: Address) -> bool:
    embedded = _embedded_ipv4(ip)
    return (embedded is not None and not embedded.is_global) or not ip.is_global


def system_resolver(host: str, port: int) -> list[str]:
    """Every address the system resolver gives for ``host``."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM) if info[4]]


def _port(parts) -> int:
    return parts.port or (443 if parts.scheme == "https" else 80)


def check_url(url: str, *, resolver: Resolver | None = None) -> list[str]:
    """The public addresses ``url``'s host resolves to, in order; :class:`HTTPError` ``REFUSED`` if any is not."""
    try:
        parts = urlsplit((url or "").strip())
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        port = _port(parts)
    except ValueError as exc:
        raise HTTPError(REFUSED, f"not a URL: {exc}", url=url) from exc
    if scheme not in {"http", "https"}:
        raise HTTPError(REFUSED, f"scheme {scheme!r} is not allowed", url=url)
    if not host:
        raise HTTPError(REFUSED, "the URL has no host", url=url)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise HTTPError(REFUSED, f"host {host} is this computer", url=url)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _forbidden(literal):
            raise HTTPError(REFUSED, f"host {host} is not a public address", url=url)
        return [str(literal)]
    try:
        found = (resolver or system_resolver)(host, port)
    except (OSError, UnicodeError) as exc:
        raise HTTPError(REFUSED, f"host {host} could not be resolved: {exc}", url=url) from exc
    addresses = []
    for text in found:
        try:
            addresses.append(ipaddress.ip_address(text.split("%", 1)[0]))
        except ValueError:
            continue
    if not addresses:
        raise HTTPError(REFUSED, f"host {host} resolved to no address", url=url)
    bad = [str(a) for a in addresses if _forbidden(a)]
    if bad:
        raise HTTPError(REFUSED, f"host {host} resolves to a non-public address {bad[0]}", url=url)
    return [str(a) for a in addresses]


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to a chosen address, with the certificate checked for the host name."""

    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _prepare(request: Request, wait: float) -> tuple[http.client.HTTPConnection, dict[str, str], str]:
    """The connection to ``request.ip`` (each socket operation waiting at most ``wait``), the headers with the URL's
    host in ``Host``, and the path to ask for."""
    parts = urlsplit(request.url)
    host, port = parts.hostname or "", _port(parts)
    if parts.scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPS(host, port, request.ip, wait)
    else:
        conn = http.client.HTTPConnection(request.ip, port, timeout=wait)
    default = port == (443 if parts.scheme == "https" else 80)
    named = f"[{host}]" if ":" in host else host
    headers = {"Host": named if default else f"{named}:{port}", "Accept-Encoding": "identity", **request.headers}
    return conn, headers, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _cutter(request: Request, conn: http.client.HTTPConnection) -> tuple[threading.Event, list, threading.Timer]:
    """A started timer that shuts ``conn``'s socket when ``request.timeout`` passes, the event it sets, and the list
    the caller adds the connected socket to (a reply that closes takes it from ``conn``)."""
    over, held = threading.Event(), []

    def cut() -> None:
        over.set()
        for sock in (*held, conn.sock):
            with contextlib.suppress(OSError, AttributeError):
                sock.shutdown(socket.SHUT_RDWR)

    timer = threading.Timer(request.timeout, cut)
    timer.daemon = True
    timer.start()
    return over, held, timer


def _late(request: Request) -> TimeoutError:
    return TimeoutError(f"{request.url} did not finish within {request.timeout:g} "
                        f"second{'' if request.timeout == 1 else 's'}")


def stdlib_transport(request: Request) -> Response:
    """Send ``request`` to ``request.ip`` with the URL's host in the ``Host`` header (and in TLS for ``https``).

    ``request.timeout`` is one limit for the whole exchange: a socket's own timeout starts again at every byte, so a
    timer shuts the connection when the limit passes, and a server sending a byte at a time cannot hold the request
    open (``TimeoutError``, which the client reports as ``FAILED``)."""
    conn, headers, path = _prepare(request, request.timeout)
    over, held, timer = _cutter(request, conn)
    late = _late(request)
    try:
        conn.request(request.method, path, body=request.body, headers=headers)
        held.append(conn.sock)
        if over.is_set():
            raise late
        reply = conn.getresponse()
        body = reply.read(request.max_bytes + 1)
        if over.is_set():
            raise late
        return Response(status=reply.status, headers={k.lower(): v for k, v in reply.getheaders()}, body=body,
                        url=request.url)
    except (OSError, http.client.HTTPException) as exc:
        if over.is_set() and exc is not late:
            raise late from exc
        raise
    finally:
        timer.cancel()
        conn.close()


def stdlib_stream_transport(request: Request) -> Stream:
    """Send ``request`` as :func:`stdlib_transport` does and hand back its body as it comes, in pieces of at most a
    mebibyte. ``request.read_timeout`` (else ``timeout``) bounds the wait for each read, and ``request.timeout`` the
    whole exchange: a timer shuts the connection when it passes (``TimeoutError``). ``close`` ends the response and
    connection, including a socket transferred to the response by ``Connection: close``."""
    conn, headers, path = _prepare(request, request.read_timeout or request.timeout)
    over, held, timer = _cutter(request, conn)
    late = _late(request)

    reply = None

    def close() -> None:
        timer.cancel()
        try:
            if reply is not None:
                reply.close()
        finally:
            conn.close()

    try:
        conn.request(request.method, path, body=request.body, headers=headers)
        held.append(conn.sock)
        if over.is_set():
            raise late
        reply = conn.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        close()
        if over.is_set() and exc is not late:
            raise late from exc
        raise
    except BaseException:
        close()
        raise

    def chunks() -> Iterable[bytes]:
        try:
            while True:
                piece = reply.read1(_CHUNK)
                if over.is_set():
                    raise late
                if not piece:
                    return
                yield piece
        except (OSError, http.client.HTTPException) as exc:
            if over.is_set() and exc is not late:
                raise late from exc
            raise

    return Stream(status=reply.status, headers={k.lower(): v for k, v in reply.getheaders()}, chunks=chunks(),
                  url=request.url, close=close)


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), _port(parts)


def _forwardable(headers: Mapping[str, str], *, frm: str, to: str) -> dict[str, str]:
    if _origin(frm) == _origin(to):
        return dict(headers)
    (fs, fh, fp), (ts, th, tp) = _origin(frm), _origin(to)
    if fh == th and (fs, fp, ts, tp) == ("http", 80, "https", 443):   # the same host, upgraded to https
        return {k: v for k, v in headers.items() if k.lower() not in _CREDENTIALS or k.lower() == "authorization"}
    return {k: v for k, v in headers.items() if k.lower() in _CROSS_ORIGIN_SAFE}


def _next_method(method: str, status: int) -> str:
    upper = method.upper()
    if status in {302, 303} and upper != "HEAD":
        return "GET"
    if status == 301 and upper == "POST":
        return "GET"
    return method


def _close(reply: Response | Stream) -> None:
    close = getattr(reply, "close", None)
    if close is not None:
        close()


class Client:
    """Checked outbound HTTP. One client keeps its per-host pace and its ``robots.txt`` answers."""

    def __init__(self, *, resolver: Resolver | None = None, transport: Transport | None = None,
                 timeout: float = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
                 max_redirects: int = DEFAULT_MAX_REDIRECTS, min_interval: float = DEFAULT_MIN_INTERVAL,
                 obey_robots: bool = False, user_agent: str = USER_AGENT,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 stream_transport: StreamTransport | None = None) -> None:
        self.resolver, self.transport = resolver, transport or stdlib_transport
        self.stream_transport = stream_transport or stdlib_stream_transport
        self.timeout, self.max_bytes, self.max_redirects = timeout, max_bytes, max_redirects
        self.min_interval, self.obey_robots, self.user_agent = min_interval, obey_robots, user_agent
        self._clock, self._sleep = clock, sleep
        self._last: dict[str, float] = {}
        self._robots: dict[tuple[str, str, int], urllib.robotparser.RobotFileParser] = {}

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> Response:
        return self.request("GET", url, headers=headers)

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: bytes | None = None) -> Response:
        """The final response of ``url`` after its redirects; :class:`HTTPError` for any refusal or failure."""
        return self._request(method, url, {"User-Agent": self.user_agent, **(headers or {})}, body,
                             robots=self.obey_robots)

    def download(self, url: str, dest: str | os.PathLike[str], *, expected_bytes: int, sha256: str,
                 headers: Mapping[str, str] | None = None,
                 deadline: float = DEFAULT_DOWNLOAD_DEADLINE) -> Downloaded:
        """Stream ``url`` (a GET, after its redirects) to the file ``dest``, put in place only when its size is
        ``expected_bytes`` and its SHA-256 (hex) is ``sha256``; see the module docstring. :class:`HTTPError` for any
        refusal, failure or mismatch, with nothing left behind; ``ValueError`` before anything is sent when the size
        or the hash to check is not one."""
        want = (sha256 or "").strip().lower()
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError(f"expected_bytes must be a size in bytes; got {expected_bytes!r}")
        if not _SHA256.fullmatch(want):
            raise ValueError(f"sha256 must be 64 hexadecimal characters; got {sha256!r}")
        target = Path(dest)
        part = target.with_name(f".{target.name}.{secrets.token_hex(6)}.part")
        ends = self._clock() + deadline
        placed, reply, out = False, None, None
        try:
            out = open(part, "xb")  # noqa: SIM115 - closed in the finally below, before the rename or the delete
            reply = self._follow("GET", url, {"User-Agent": self.user_agent, **(headers or {})}, None,
                                 lambda m, u, h, b: self._open(m, u, h, b, ends=ends, deadline=deadline))
            stated = str(reply.headers.get("content-length", "")).strip()
            if stated.isdigit() and int(stated) > expected_bytes:
                raise HTTPError(TOO_LARGE, f"{reply.url} says it will send {int(stated)} bytes, more than the "
                                           f"{expected_bytes} expected", url=reply.url, status=reply.status)
            digest, size = hashlib.sha256(), 0
            for piece in reply.chunks:
                size += len(piece)
                if size > expected_bytes:
                    raise HTTPError(TOO_LARGE, f"{reply.url} sent more than the {expected_bytes} bytes expected",
                                    url=reply.url, status=reply.status)
                self._remaining(ends, url, deadline)
                digest.update(piece)
                out.write(piece)
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            if size != expected_bytes:
                raise HTTPError(MISMATCH, f"{reply.url} sent {size} bytes, not the {expected_bytes} expected",
                                url=reply.url, status=reply.status)
            got = digest.hexdigest()
            if got != want:
                raise HTTPError(MISMATCH, f"{reply.url}: the SHA-256 of what arrived is {got}, not {want}",
                                url=reply.url, status=reply.status)
            final_url = reply.url
            _close(reply)
            reply = None
            self._remaining(ends, url, deadline)
            os.replace(part, target)
            placed = True
            return Downloaded(path=target, size=size, sha256=got, url=final_url)
        except HTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise HTTPError(FAILED, f"{url}: {type(exc).__name__}: {exc}", url=url) from exc
        finally:
            # Keep the first failure; a failed close must not skip the other cleanup steps.
            if out is not None:
                with contextlib.suppress(Exception):
                    out.close()
            if reply is not None:
                with contextlib.suppress(Exception):
                    _close(reply)
            if not placed:
                with contextlib.suppress(Exception):
                    part.unlink()

    def _request(self, method: str, url: str, sent: dict[str, str], body: bytes | None, *,
                 robots: bool) -> Response:
        return self._follow(method, url, sent, body, lambda m, u, h, b: self._send(m, u, h, b, robots=robots))

    def _follow(self, method: str, url: str, sent: dict[str, str], body: bytes | None,
                hop: Callable[[str, str, dict[str, str], bytes | None], Response | Stream]) -> Response | Stream:
        """The final reply of ``url``: each hop sent with ``hop``, each redirect's reply closed before the next."""
        target = url
        for _ in range(self.max_redirects + 1):
            reply = hop(method, target, sent, body)
            if reply.status not in _REDIRECTS:
                if reply.status >= 400:
                    _close(reply)
                    raise HTTPError(STATUS, f"{target} answered {reply.status}", url=target, status=reply.status)
                return reply
            _close(reply)
            location = reply.headers.get("location", "")
            if not location:
                raise HTTPError(STATUS, f"{target} answered {reply.status} with no Location", url=target,
                                status=reply.status)
            following = urljoin(target, location)
            sent = _forwardable(sent, frm=target, to=following)
            changed = _next_method(method, reply.status)
            if changed != method:
                body = None
            method, target = changed, following
        raise HTTPError(STATUS, f"{url} took more than {self.max_redirects} redirects", url=url)

    def _checked(self, url: str, *, robots: bool) -> str:
        """The checked address to connect to for ``url``, after ``robots.txt`` (when asked) and the host's pause."""
        ip = check_url(url, resolver=self.resolver)[0]
        if robots:
            self._allowed(url)
        self._pace(_origin(url)[1])
        return ip

    def _send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, *,
              robots: bool) -> Response:
        ip = self._checked(url, robots=robots)
        try:
            reply = self.transport(Request(method=method, url=url, ip=ip, headers=dict(headers), body=body,
                                           timeout=self.timeout, max_bytes=self.max_bytes))
        except HTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise HTTPError(FAILED, f"{url}: {type(exc).__name__}: {exc}", url=url) from exc
        if len(reply.body) > self.max_bytes:
            raise HTTPError(TOO_LARGE, f"{url} sent more than {self.max_bytes} bytes", url=url, status=reply.status)
        return replace(reply, headers={k.lower(): v for k, v in reply.headers.items()})

    def _remaining(self, ends: float, url: str, deadline: float) -> float:
        """Time left in one download's absolute deadline, shared by every hop and the final rename."""
        remaining = ends - self._clock()
        if remaining <= 0:
            raise HTTPError(FAILED, f"{url} did not finish within {deadline:g} "
                                    f"second{'' if deadline == 1 else 's'}", url=url)
        return remaining

    def _open(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, *,
              ends: float, deadline: float) -> Stream:
        """One hop of a download, with only the time remaining before the shared expiry."""
        self._remaining(ends, url, deadline)
        ip = self._checked(url, robots=self.obey_robots)
        remaining = self._remaining(ends, url, deadline)
        try:
            reply = self.stream_transport(Request(method=method, url=url, ip=ip, headers=dict(headers), body=body,
                                                  timeout=remaining, max_bytes=self.max_bytes,
                                                  read_timeout=min(self.timeout, remaining)))
        except HTTPError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise HTTPError(FAILED, f"{url}: {type(exc).__name__}: {exc}", url=url) from exc
        return replace(reply, headers={k.lower(): v for k, v in reply.headers.items()})

    def _pace(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def _allowed(self, url: str) -> None:
        origin = _origin(url)
        if origin not in self._robots:
            scheme, host, port = origin
            default = port == (443 if scheme == "https" else 80)
            named = f"[{host}]" if ":" in host else host
            robots_url = f"{scheme}://{named if default else f'{named}:{port}'}/robots.txt"
            parser = urllib.robotparser.RobotFileParser(robots_url)
            try:
                reply = self._request("GET", robots_url, {"User-Agent": self.user_agent}, None, robots=False)
                parser.parse(reply.body.decode("utf-8", "replace").splitlines())
            except HTTPError as exc:
                if exc.code != STATUS or exc.status is None or not 400 <= exc.status < 500:
                    raise HTTPError(ROBOTS, f"robots.txt for {host} could not be read ({exc}), so G6 does not "
                                            "fetch from it", url=url) from exc
                parser.parse([])   # no robots.txt: everything is allowed
            self._robots[origin] = parser
        if not self._robots[origin].can_fetch(self.user_agent, url):
            raise HTTPError(ROBOTS, f"{url} is disallowed by robots.txt", url=url)


__all__ = ["DEFAULT_DOWNLOAD_DEADLINE", "DEFAULT_MAX_BYTES", "DEFAULT_MAX_REDIRECTS", "DEFAULT_MIN_INTERVAL",
           "DEFAULT_TIMEOUT", "FAILED", "MISMATCH", "REFUSED", "ROBOTS", "STATUS", "TOO_LARGE", "Client", "Downloaded",
           "HTTPError", "Request", "Response", "Stream", "StreamTransport", "check_url", "stdlib_stream_transport",
           "stdlib_transport", "system_resolver"]
