# SPDX-License-Identifier: Apache-2.0
"""g6.core.http (FW-10): outbound HTTP checked before every request and every redirect hop, connected to the address
that was checked, with timeouts, a byte cap, an optional robots.txt, a per-host pace, and every failure an error; and a
streamed download to a file under the same hop rules, hashed as it streams and put in place only on a match. The
address cases are the archive's ``test_http_safety`` (audit census FW-10), re-run against this module."""
from __future__ import annotations

import contextlib
import hashlib
import http.server
import threading
import time
from pathlib import Path

import pytest

from g6.core import http as h


def _resolver(table: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError(f"no such host {host}")
        return table[host]
    return resolve


PUBLIC = _resolver({"public.example": ["93.184.216.34"], "other.example": ["93.184.216.35"],
                    "private.example": ["10.0.0.5"], "linklocal.example": ["169.254.169.254"],
                    "mixed.example": ["93.184.216.34", "127.0.0.1"], "translated.example": ["::ffff:0:a9fe:a9fe"],
                    "nat64.example": ["64:ff9b::a9fe:a9fe"]})


def _refused(url: str, resolver=PUBLIC) -> str:
    with pytest.raises(h.HTTPError) as caught:
        h.check_url(url, resolver=resolver)
    assert caught.value.code == h.REFUSED
    return str(caught.value)


# --- the address checks, from the archive's test_http_safety -------------------------------------------------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://public.example/", "gopher://public.example/",
                                 "http:///nohost", "http://localhost/", "http://a.localhost/", "http://127.0.0.1/",
                                 "http://10.0.0.5/", "http://[::1]/", "http://0.0.0.0/"])
def test_schemes_other_than_http_and_local_hosts_are_refused(url: str) -> None:
    _refused(url)


@pytest.mark.parametrize("host", ["private.example", "linklocal.example", "mixed.example", "translated.example",
                                  "nat64.example"])
def test_a_name_that_resolves_to_any_non_public_address_is_refused(host: str) -> None:
    assert host in _refused(f"http://{host}/")


def test_a_name_that_does_not_resolve_is_refused() -> None:
    assert "could not be resolved" in _refused("http://nowhere.example/")


@pytest.mark.parametrize("url", [
    "http://[64:ff9b::169.254.169.254]/latest/meta-data/", "http://[64:ff9b::127.0.0.1]/",
    "http://[64:ff9b::10.0.0.5]/admin", "http://[64:ff9b::192.168.1.1]/", "http://[64:ff9b::172.16.0.1]/",
    "http://[::127.0.0.1]/", "http://[::169.254.169.254]/latest/meta-data/", "http://[::10.0.0.5]/",
    "http://[::ffff:0:a9fe:a9fe]/latest/meta-data/", "http://[::ffff:0:7f00:1]/", "http://[::ffff:0:a00:5]/admin",
    "http://[::ffff:0:c0a8:101]/", "http://[::ffff:0:ac10:1]/",
    "http://[::ffff:169.254.169.254]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:10.0.0.5]/",
])
def test_the_four_ipv6_forms_that_embed_a_private_ipv4_are_refused(url: str) -> None:
    # NAT64 well-known prefix, IPv4-compatible, IPv4-translated and IPv4-mapped: ipaddress calls the first three
    # global whatever they embed, so the embedded address is checked as well.
    _refused(url)


@pytest.mark.parametrize("url", ["http://[2002:7f00:0001::]/", "http://[2002:a9fe:a9fe::]/",
                                 "http://[2002:0808:0808::]/", "http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",
                                 "http://[64:ff9b:1::7f00:1]/"])
def test_6to4_teredo_and_local_use_nat64_are_refused(url: str) -> None:
    # Safe only because CPython classifies these prefixes as non-global; if a release changes that, this goes red.
    _refused(url)


@pytest.mark.parametrize("url", ["http://[64:ff9b::93.184.216.34]/", "http://[::ffff:0:5db8:d822]/",
                                 "http://[2606:4700:4700::1111]/", "https://public.example/path?q=1"])
def test_public_addresses_and_names_are_allowed(url: str) -> None:
    assert h.check_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("addr", ["::5efe:169.254.169.254", "::200:5efe:169.254.169.254",
                                  "2600:1f18::5efe:169.254.169.254", "2600:1f18:aabb::169.254.169.254"])
def test_isatap_and_network_specific_nat64_forms_are_a_stated_limit(addr: str) -> None:
    # They carry an IPv4 and pass: ISATAP identifiers and provider-chosen NAT64 prefixes cannot be enumerated. The
    # module says so; this pins the limit so it is not mistaken for covered.
    assert h.check_url(f"http://[{addr}]/", resolver=PUBLIC)
    assert "network-specific" in (h.__doc__ or "")


# --- the client, with a recording transport -------------------------------------------------------------------------

class Recorder:
    def __init__(self, replies: dict[str, h.Response]) -> None:
        self.replies, self.sent = replies, []

    def __call__(self, request: h.Request) -> h.Response:
        self.sent.append(request)
        return self.replies[request.url]


def _reply(url: str, status: int = 200, body: bytes = b"ok", **headers: str) -> h.Response:
    return h.Response(status=status, headers={k.lower().replace("_", "-"): v for k, v in headers.items()},
                      body=body, url=url)


def _client(replies: dict[str, h.Response], **kw) -> tuple[h.Client, Recorder]:
    recorder = Recorder(replies)
    return h.Client(resolver=PUBLIC, transport=recorder, min_interval=0, **kw), recorder


def test_a_redirect_to_a_loopback_address_is_refused_before_it_is_sent() -> None:
    client, sent = _client({"http://public.example/m": _reply("http://public.example/m", 302,
                                                              location="http://127.0.0.1/admin")})
    with pytest.raises(h.HTTPError) as caught:
        client.get("http://public.example/m")
    assert caught.value.code == h.REFUSED and "127.0.0.1" in str(caught.value)
    assert [r.url for r in sent.sent] == ["http://public.example/m"]


def test_a_redirect_to_a_name_that_resolves_to_the_metadata_address_is_refused() -> None:
    client, sent = _client({"http://public.example/m": _reply("http://public.example/m", 307,
                                                              location="http://linklocal.example/latest/")})
    with pytest.raises(h.HTTPError, match="linklocal.example"):
        client.get("http://public.example/m")
    assert len(sent.sent) == 1


def test_the_request_goes_to_the_address_that_was_checked() -> None:
    # The name is resolved once and the connection is made to that address, so a second lookup (DNS rebinding)
    # cannot swap in another one between the check and the request.
    client, sent = _client({"http://public.example/x": _reply("http://public.example/x")})
    client.get("http://public.example/x")
    assert sent.sent[0].ip == "93.184.216.34"


def test_a_403_and_a_500_are_errors_not_pages() -> None:
    for status in (403, 404, 500):
        client, _ = _client({"http://public.example/x": _reply("http://public.example/x", status, b"denied")})
        with pytest.raises(h.HTTPError) as caught:
            client.get("http://public.example/x")
        assert caught.value.code == h.STATUS and caught.value.status == status


def test_a_redirect_without_a_location_is_an_error() -> None:
    client, _ = _client({"http://public.example/x": _reply("http://public.example/x", 302)})
    with pytest.raises(h.HTTPError) as caught:
        client.get("http://public.example/x")
    assert caught.value.code == h.STATUS


def test_credentials_do_not_cross_to_another_origin() -> None:
    client, sent = _client({
        "http://public.example/a": _reply("http://public.example/a", 302, location="/b"),
        "http://public.example/b": _reply("http://public.example/b", 302, location="http://other.example/c"),
        "http://other.example/c": _reply("http://other.example/c", 302, location="http://public.example/d"),
        "http://public.example/d": _reply("http://public.example/d")})
    client.get("http://public.example/a", headers={"Authorization": "Bearer SECRET", "Cookie": "s=1", "Accept": "x/y"})
    seen = [{k.lower(): v for k, v in r.headers.items()} for r in sent.sent]
    assert seen[1].get("authorization") == "Bearer SECRET" and seen[1].get("cookie") == "s=1"   # same origin
    assert "authorization" not in seen[2] and "cookie" not in seen[2] and seen[2]["accept"] == "x/y"
    assert "authorization" not in seen[3]                     # once dropped, never handed back


def test_a_services_own_key_header_does_not_cross_to_another_origin() -> None:
    """A service's key often travels in a header of its own (Brave's X-Subscription-Token, X-Api-Key), not in
    Authorization: across origins only a short list of harmless headers is kept, so no such key follows a redirect."""
    client, sent = _client({
        "https://public.example/s": _reply("https://public.example/s", 302, location="https://other.example/c"),
        "https://other.example/c": _reply("https://other.example/c")})
    client.get("https://public.example/s", headers={"X-Subscription-Token": "PAID", "X-Api-Key": "K",
                                                    "Accept": "application/json", "User-Agent": "g6-test"})
    crossed = {k.lower(): v for k, v in sent.sent[1].headers.items()}
    assert "x-subscription-token" not in crossed and "x-api-key" not in crossed
    assert crossed["accept"] == "application/json" and crossed["user-agent"] == "g6-test"


def test_an_upgrade_to_https_on_the_same_host_keeps_the_authorization_header() -> None:
    client, sent = _client({"http://public.example/a": _reply("http://public.example/a", 301,
                                                              location="https://public.example/a"),
                            "https://public.example/a": _reply("https://public.example/a")})
    client.get("http://public.example/a", headers={"Authorization": "Bearer T", "Cookie": "c=1"})
    upgraded = {k.lower(): v for k, v in sent.sent[1].headers.items()}
    assert upgraded["authorization"] == "Bearer T" and "cookie" not in upgraded


@pytest.mark.parametrize(("method", "status", "after"), [
    ("POST", 301, "GET"), ("PUT", 301, "PUT"), ("POST", 302, "GET"), ("PUT", 302, "GET"), ("DELETE", 302, "GET"),
    ("HEAD", 302, "HEAD"), ("POST", 303, "GET"), ("HEAD", 303, "HEAD"), ("POST", 307, "POST"), ("PUT", 308, "PUT")])
def test_redirects_change_the_method_the_way_browsers_do(method: str, status: int, after: str) -> None:
    client, sent = _client({"http://public.example/a": _reply("http://public.example/a", status, location="/b"),
                            "http://public.example/b": _reply("http://public.example/b")})
    client.request(method, "http://public.example/a", body=b"x" if method in {"POST", "PUT"} else None)
    assert sent.sent[1].method == after
    assert (sent.sent[1].body is None) == (after in {"GET", "HEAD"})


def test_a_redirect_loop_stops_at_the_limit() -> None:
    client, sent = _client({"http://public.example/a": _reply("http://public.example/a", 302, location="/a")},
                           max_redirects=3)
    with pytest.raises(h.HTTPError, match="more than 3 redirects"):
        client.get("http://public.example/a")
    assert len(sent.sent) == 4


def test_a_body_over_the_cap_is_an_error() -> None:
    client, _ = _client({"http://public.example/x": _reply("http://public.example/x", body=b"x" * 11)}, max_bytes=10)
    with pytest.raises(h.HTTPError) as caught:
        client.get("http://public.example/x")
    assert caught.value.code == h.TOO_LARGE


def test_a_network_failure_is_an_error() -> None:
    def broken(request: h.Request) -> h.Response:
        raise TimeoutError("timed out")
    with pytest.raises(h.HTTPError) as caught:
        h.Client(resolver=PUBLIC, transport=broken, min_interval=0).get("http://public.example/x")
    assert caught.value.code == h.FAILED and "timed out" in str(caught.value)


def test_robots_txt_is_obeyed_when_asked_and_a_missing_one_allows_everything() -> None:
    robots = "User-agent: *\nDisallow: /private\n".encode()
    client, sent = _client({"https://public.example/robots.txt": _reply("https://public.example/robots.txt",
                                                                        body=robots),
                            "https://public.example/open": _reply("https://public.example/open")}, obey_robots=True)
    assert client.get("https://public.example/open").body == b"ok"
    with pytest.raises(h.HTTPError) as caught:
        client.get("https://public.example/private/x")
    assert caught.value.code == h.ROBOTS
    assert [r.url for r in sent.sent].count("https://public.example/robots.txt") == 1    # read once per origin
    client, _ = _client({"https://other.example/robots.txt": _reply("https://other.example/robots.txt", 404),
                         "https://other.example/private": _reply("https://other.example/private")}, obey_robots=True)
    assert client.get("https://other.example/private").body == b"ok"


def test_a_robots_txt_that_cannot_be_read_refuses_the_fetch() -> None:
    client, _ = _client({"https://public.example/robots.txt": _reply("https://public.example/robots.txt", 503),
                         "https://public.example/x": _reply("https://public.example/x")}, obey_robots=True)
    with pytest.raises(h.HTTPError) as caught:
        client.get("https://public.example/x")
    assert caught.value.code == h.ROBOTS


def test_requests_to_one_host_are_paced() -> None:
    now, slept = [100.0], []
    recorder = Recorder({"http://public.example/1": _reply("http://public.example/1"),
                         "http://public.example/2": _reply("http://public.example/2"),
                         "http://other.example/3": _reply("http://other.example/3")})
    client = h.Client(resolver=PUBLIC, transport=recorder, min_interval=2.0, clock=lambda: now[0],
                      sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    client.get("http://public.example/1")
    now[0] += 0.5
    client.get("http://public.example/2")
    client.get("http://other.example/3")
    assert slept == [1.5]


# --- the standard-library transport ---------------------------------------------------------------------------------

def test_the_transport_connects_to_the_given_address_and_names_the_host() -> None:
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            seen["host"], seen["path"] = self.headers["Host"], self.path
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        reply = h.stdlib_transport(h.Request(method="GET", url=f"http://named.example:{port}/p?q=1",
                                             ip="127.0.0.1", headers={}, body=None, timeout=5, max_bytes=100))
    finally:
        server.shutdown()
    assert reply.status == 200 and reply.body == b"hello"
    assert seen == {"host": f"named.example:{port}", "path": "/p?q=1"}


def test_one_time_limit_covers_the_whole_reply_however_slowly_it_comes() -> None:
    # a server sending a byte at a time, each well inside the time limit, cannot hold a request open past it (a
    # socket's timeout starts again at every byte)
    class Drip(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self.send_response(200)
            self.send_header("Content-Length", "40")
            self.end_headers()
            with contextlib.suppress(OSError):
                for _ in range(40):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.1)

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="1 second"):
            h.stdlib_transport(h.Request(method="GET", url=f"http://named.example:{server.server_address[1]}/",
                                         ip="127.0.0.1", headers={}, body=None, timeout=1.0, max_bytes=100))
    finally:
        server.shutdown()
    assert time.monotonic() - started < 2.5


# --- the streamed download to a file ---------------------------------------------------------------------------------

BODY = b"line one\nline two\n" * 50
DIGEST = hashlib.sha256(BODY).hexdigest()


class Streams:
    """A stream transport double: each URL answers a status, headers and a body cut into chunks (or a callable that
    yields them), and every request and every close is recorded."""

    def __init__(self, replies: dict[str, tuple[int, dict[str, str], object]]) -> None:
        self.replies, self.sent, self.closed = replies, [], []

    def __call__(self, request: h.Request) -> h.Stream:
        self.sent.append(request)
        status, headers, body = self.replies[request.url]
        chunks = body() if callable(body) else iter([body[i:i + 64] for i in range(0, len(body), 64)])
        return h.Stream(status=status, headers=headers, chunks=chunks, url=request.url,
                        close=lambda: self.closed.append(request.url))


def _downloader(replies: dict[str, tuple[int, dict[str, str], object]]) -> tuple[h.Client, Streams]:
    streams = Streams(replies)
    return h.Client(resolver=PUBLIC, stream_transport=streams, min_interval=0), streams


def _left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_a_download_follows_the_redirect_to_its_cdn_under_the_hop_rules_and_lands_atomically(tmp_path: Path) -> None:
    dest = tmp_path / "corpus.jsonl"
    seen_mid_stream: list[list[str]] = []

    def body():  # while the body streams, nothing is at dest yet: only a temporary file beside it
        for i in range(0, len(BODY), 100):
            seen_mid_stream.append(_left(tmp_path))
            yield BODY[i:i + 100]

    client, streams = _downloader({
        "https://public.example/resolve/abc/corpus.jsonl": (302, {"Location": "https://other.example/blob?sig=1"}, b""),
        "https://other.example/blob?sig=1": (200, {"Content-Length": str(len(BODY))}, body)})
    got = client.download("https://public.example/resolve/abc/corpus.jsonl", dest, expected_bytes=len(BODY),
                          sha256=DIGEST, headers={"Authorization": "Bearer T", "Accept": "*/*"})
    assert dest.read_bytes() == BODY and _left(tmp_path) == ["corpus.jsonl"]
    assert (got.path, got.size, got.sha256, got.url) == (dest, len(BODY), DIGEST, "https://other.example/blob?sig=1")
    assert seen_mid_stream and all("corpus.jsonl" not in names and len(names) == 1 and names[0].endswith(".part")
                                   for names in seen_mid_stream)
    crossed = {k.lower(): v for k, v in streams.sent[1].headers.items()}
    assert "authorization" not in crossed and crossed["accept"] == "*/*"      # the CDN hop gets no credential
    assert [r.ip for r in streams.sent] == ["93.184.216.34", "93.184.216.35"]   # each hop checked and pinned
    assert streams.closed == [r.url for r in streams.sent]                      # the redirect's reply closed too


def test_a_download_redirected_to_a_private_address_is_refused_and_leaves_no_file(tmp_path: Path) -> None:
    client, streams = _downloader({
        "https://public.example/f": (302, {"location": "http://10.0.0.5/f"}, b""),
        "http://10.0.0.5/f": (200, {}, BODY)})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.REFUSED and "10.0.0.5" in str(caught.value)
    assert [r.url for r in streams.sent] == ["https://public.example/f"] and _left(tmp_path) == []


def test_a_download_longer_than_expected_is_refused_and_leaves_no_file(tmp_path: Path) -> None:
    client, _ = _downloader({"https://public.example/f": (200, {}, BODY + b"more")})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.TOO_LARGE and _left(tmp_path) == []


def test_a_download_whose_stated_length_is_over_the_expected_size_is_refused_before_its_body_is_read(
        tmp_path: Path) -> None:
    read: list[int] = []

    def body():
        read.append(1)
        yield BODY

    client, _ = _downloader({"https://public.example/f": (200, {"content-length": str(len(BODY) + 1)}, body)})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.TOO_LARGE and read == [] and _left(tmp_path) == []


def test_a_hash_mismatch_leaves_no_file_and_keeps_what_was_at_dest(tmp_path: Path) -> None:
    dest = tmp_path / "f"
    dest.write_bytes(b"what was there")
    tampered = BODY[:-1] + b"X"
    client, _ = _downloader({"https://public.example/f": (200, {}, tampered)})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", dest, expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.MISMATCH and "SHA-256" in str(caught.value)
    assert dest.read_bytes() == b"what was there" and _left(tmp_path) == ["f"]


def test_a_short_download_is_a_mismatch_and_leaves_no_file(tmp_path: Path) -> None:
    client, _ = _downloader({"https://public.example/f": (200, {}, BODY[:-10])})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.MISMATCH and _left(tmp_path) == []


def test_a_download_that_breaks_off_is_a_failure_and_leaves_no_file(tmp_path: Path) -> None:
    def body():
        yield BODY[:100]
        raise ConnectionResetError("reset by peer")

    client, streams = _downloader({"https://public.example/f": (200, {}, body)})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.FAILED and "reset by peer" in str(caught.value)
    assert _left(tmp_path) == [] and streams.closed == ["https://public.example/f"]


def test_a_download_past_its_deadline_is_a_failure_and_leaves_no_file(tmp_path: Path) -> None:
    def body():
        for _ in range(20):
            time.sleep(0.02)
            yield b"x"

    client, _ = _downloader({"https://public.example/f": (200, {}, body)})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=20,
                        sha256=hashlib.sha256(b"x" * 20).hexdigest(), deadline=0.1)
    assert caught.value.code == h.FAILED and "0.1 second" in str(caught.value) and _left(tmp_path) == []


def test_a_403_download_is_an_error_and_nothing_is_written(tmp_path: Path) -> None:
    client, _ = _downloader({"https://public.example/f": (403, {}, b"denied")})
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", tmp_path / "f", expected_bytes=6, sha256=DIGEST)
    assert caught.value.code == h.STATUS and caught.value.status == 403 and _left(tmp_path) == []


def test_a_download_needs_a_size_and_a_sha256_to_check() -> None:
    client, streams = _downloader({})
    for size, digest in ((-1, DIGEST), (10, "abc"), (10, DIGEST.upper() + "0")):
        with pytest.raises(ValueError):
            client.download("https://public.example/f", "f", expected_bytes=size, sha256=digest)
    assert streams.sent == []


def test_the_stream_transport_reads_the_body_as_it_comes() -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        stream = h.stdlib_stream_transport(h.Request(
            method="GET", url=f"http://named.example:{server.server_address[1]}/f", ip="127.0.0.1", headers={},
            body=None, timeout=5, max_bytes=len(BODY), read_timeout=2))
        try:
            got = b"".join(stream.chunks)
        finally:
            stream.close()
    finally:
        server.shutdown()
    assert stream.status == 200 and got == BODY and stream.headers["content-length"] == str(len(BODY))


@pytest.mark.parametrize("hop_seconds, sent_count", [(1.0, 3), (1.5, 2)])
def test_download_aggregate_deadline_includes_redirects_and_empty_body(
        tmp_path: Path, hop_seconds: float, sent_count: int) -> None:
    # RED: remove http.py's self._remaining(ends, url, deadline) check before os.replace (or before the next hop).
    now, sent, closed, resolved = [0.0], [], [], []
    dest = tmp_path / "f"
    dest.write_bytes(b"original")

    def resolver(host, port):
        resolved.append(host)
        return PUBLIC(host, port)

    def transport(request):
        sent.append(request)
        now[0] += hop_seconds
        status = 302 if len(sent) < 3 else 200
        headers = {"location": f"/hop{len(sent)}"} if status == 302 else {}
        return h.Stream(status, headers, iter(()), request.url, close=lambda: closed.append(request.url))

    client = h.Client(resolver=resolver, stream_transport=transport, min_interval=0, clock=lambda: now[0])
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", dest, expected_bytes=0,
                        sha256=hashlib.sha256(b"").hexdigest(), deadline=3)
    assert caught.value.code == h.FAILED
    assert len(sent) == len(closed) == len(resolved) == sent_count
    assert [r.timeout for r in sent] == [3 - hop_seconds * i for i in range(sent_count)]
    assert all(r.read_timeout <= r.timeout for r in sent)
    assert dest.read_bytes() == b"original" and _left(tmp_path) == ["f"]


@pytest.mark.parametrize("phase", ["resolution", "pacing"])
def test_download_deadline_counts_resolution_and_pacing_before_sending(tmp_path: Path, phase: str) -> None:
    # RED: remove http.py's remaining = self._remaining(ends, url, deadline) after _checked.
    now, sent = [0.0], []

    def resolver(host, port):
        if phase == "resolution":
            now[0] += 3
        return ["93.184.216.34"]

    def sleep(seconds):
        now[0] += seconds

    def transport(request):
        sent.append(request)
        return h.Stream(200, {}, iter(()), request.url)

    client = h.Client(resolver=resolver, stream_transport=transport, clock=lambda: now[0],
                      min_interval=3, sleep=sleep)
    client._pace("public.example")  # A preceding request establishes the host's pacing window.
    dest = tmp_path / "f"
    dest.write_bytes(b"original")
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", dest, expected_bytes=0,
                        sha256=hashlib.sha256(b"").hexdigest(), deadline=3)
    assert caught.value.code == h.FAILED and sent == []
    assert dest.read_bytes() == b"original" and _left(tmp_path) == ["f"]


@pytest.mark.parametrize("failure", ["write", "flush", "fsync", "close", "stream_close"])
def test_download_cleanup_keeps_first_failure_and_removes_part(tmp_path: Path, monkeypatch, failure: str) -> None:
    # RED: remove http.py's guarded out.close() cleanup (or _close(reply) before os.replace).
    import builtins

    dest = tmp_path / "f"
    dest.write_bytes(b"original")
    real_open, files, closed = builtins.open, [], []

    class BrokenOutput:
        def __init__(self, file):
            self.file = file

        def write(self, data):
            if failure == "write":
                raise OSError("first write failure")
            return self.file.write(data)

        def flush(self):
            if failure == "flush":
                raise OSError("first flush failure")
            return self.file.flush()

        def fileno(self):
            return self.file.fileno()

        def close(self):
            self.file.close()
            if failure != "stream_close":
                raise OSError("output close failure")

    def open_part(path, mode):
        assert mode == "xb"
        output = BrokenOutput(real_open(path, mode))
        files.append(output)
        return output

    def close_stream():
        closed.append(True)
        raise OSError("stream close failure")

    def transport(request):
        return h.Stream(200, {}, iter([BODY]), request.url, close=close_stream)

    def fsync(fd):
        if failure == "fsync":
            raise OSError("first fsync failure")

    monkeypatch.setattr(h, "open", open_part, raising=False)
    monkeypatch.setattr(h.os, "fsync", fsync)
    client = h.Client(resolver=PUBLIC, stream_transport=transport, min_interval=0)
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", dest, expected_bytes=len(BODY), sha256=DIGEST)
    assert caught.value.code == h.FAILED
    first = {"write": "first write failure", "flush": "first flush failure", "fsync": "first fsync failure",
             "close": "output close failure", "stream_close": "stream close failure"}[failure]
    assert first in str(caught.value) and str(caught.value.__cause__) == first
    assert files and all(out.file.closed for out in files) and closed
    assert dest.read_bytes() == b"original" and _left(tmp_path) == ["f"]


def test_closing_unread_stream_closes_response_that_owns_socket(monkeypatch) -> None:
    # RED: remove http.py's reply.close() in stdlib_stream_transport.close.
    class Socket:
        closed = False

        def close(self):
            self.closed = True

    socket = Socket()

    class Reply:
        status = 200
        closed = False

        def getheaders(self):
            return [("Connection", "close")]

        def read1(self, size):
            raise AssertionError("unread stream must not be consumed")

        def close(self):
            self.closed = True
            socket.close()

    reply = Reply()

    class Connection:
        sock = socket
        closed = False

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            self.sock = None  # HTTPConnection transfers socket ownership on Connection: close.
            return reply

        def close(self):
            self.closed = True
            if self.sock is not None:
                self.sock.close()

    conn = Connection()

    class Timer:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    timer = Timer()
    monkeypatch.setattr(h, "_prepare", lambda request, wait: (conn, {}, "/f"))
    monkeypatch.setattr(h, "_cutter", lambda request, connection: (threading.Event(), [], timer))
    stream = h.stdlib_stream_transport(h.Request("GET", "https://public.example/f", "93.184.216.34",
                                                {}, None, 3, 100))
    stream.close()
    assert reply.closed and socket.closed and conn.closed and timer.cancelled


def test_download_checks_deadline_after_closing_before_replacement(tmp_path: Path) -> None:
    # RED: remove http.py's self._remaining(ends, url, deadline) immediately before os.replace.
    now = [0.0]
    dest = tmp_path / "f"
    dest.write_bytes(b"original")

    def close():
        now[0] = 3

    def transport(request):
        return h.Stream(200, {}, iter([BODY]), request.url, close=close)

    client = h.Client(resolver=PUBLIC, stream_transport=transport, min_interval=0, clock=lambda: now[0])
    with pytest.raises(h.HTTPError) as caught:
        client.download("https://public.example/f", dest, expected_bytes=len(BODY), sha256=DIGEST, deadline=3)
    assert caught.value.code == h.FAILED
    assert dest.read_bytes() == b"original" and _left(tmp_path) == ["f"]
