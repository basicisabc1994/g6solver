# SPDX-License-Identifier: Apache-2.0
"""g6.core.outside.call_service: talking to a service the person runs on this machine (ComfyUI, Ollama, ...).

Only plain http on loopback, at the declared url or the one the operator's environment variable names; redirects are
refused, replies are capped, the time limit holds for the whole call, and nothing answering is the honest "not ready"
refusal with install steps, while a service that answered and then was slow or broke off says so.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from g6.core import outside
from g6.core.outside import OutsideProgram, ServiceError, call_service, detect, service_url

#: The reason a 400 gives: a control character, then far more than the excerpt keeps.
_REASON = "bad prompt:\x1b[31m node 7\x00 has no input\r\n" + "y" * 1000


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass

    def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self.path == "/redirect":
            self._reply(302, b"", {"Location": "http://example.com/"})
        elif self.path == "/missing":
            self._reply(404, b"no such thing")
        elif self.path == "/bad":
            self._reply(400, _REASON.encode())
        elif self.path == "/big":
            self._reply(200, b"x" * 5000)
        elif self.path == "/big-chunked":
            self.protocol_version = "HTTP/1.1"
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for _ in range(5):
                self.wfile.write(b"3e8\r\n" + b"x" * 1000 + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif self.path == "/big-unknown-length":  # HTTP/1.0, no length: the body ends when the server closes
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 5000)
        elif self.path == "/trickle":  # every byte well within a read's timeout; the whole reply far beyond it
            self.send_response(200)
            self.send_header("Content-Length", "30")
            self.end_headers()
            for _ in range(30):
                self.wfile.write(b"x")
                time.sleep(0.2)
        elif self.path == "/slow":  # running, but slower than the caller's limit (ComfyUI on a long render)
            time.sleep(2.5)
            self._reply(200, b"{}")
        elif self.path == "/hang-up":  # took the request, then closed without a reply
            self.close_connection = True
        else:
            self._reply(200, json.dumps({"path": self.path}).encode(), {"Content-Type": "application/json"})

    def do_POST(self) -> None:  # noqa: N802
        sent = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._reply(200, json.dumps({"got": json.loads(sent)}).encode())


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True  # a slow handler never holds up the next test

    def handle_error(self, request: object, client_address: object) -> None:  # a caller that gave up: no noise
        pass


@contextmanager
def _serving(httpd: http.server.HTTPServer) -> Iterator[http.server.HTTPServer]:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def server() -> Iterator[str]:
    with _serving(_Server(("127.0.0.1", 0), _Handler)) as httpd:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"


def _service(url: str, env: str = "TEST_SERVICE_URL") -> OutsideProgram:
    return OutsideProgram("Test service", "answers test requests", "run the test server", "MIT", url=url, env=env)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_get_reaches_the_declared_service(server: str) -> None:
    reply = call_service(_service(server), "GET", "/system_stats", environ={})
    assert reply.status == 200 and reply.json() == {"path": "/system_stats"}


def test_a_post_sends_its_body(server: str) -> None:
    reply = call_service(_service(server), "POST", "/prompt", body=json.dumps({"a": 1}).encode(), environ={})
    assert reply.json() == {"got": {"a": 1}}


def test_the_operators_variable_names_another_local_service(server: str) -> None:
    declared = _service(f"http://127.0.0.1:{_free_port()}")  # nothing listens at the declared one
    environ = {"TEST_SERVICE_URL": server}
    assert service_url(declared, environ=environ) == server
    assert call_service(declared, "GET", "/x", environ=environ).json() == {"path": "/x"}


def test_the_operators_variable_cannot_point_off_this_machine() -> None:
    declared = _service("http://127.0.0.1:8188")
    for elsewhere in ("http://example.com:8188", "https://127.0.0.1:8188", "http://10.0.0.5:8188", "ftp://localhost/"):
        with pytest.raises(ServiceError) as caught:
            call_service(declared, "GET", "/", environ={"TEST_SERVICE_URL": elsewhere})
        assert caught.value.code == outside.SERVICE_NOT_LOCAL


def test_a_redirect_is_refused_never_followed(server: str) -> None:
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/redirect", environ={})
    assert caught.value.code == outside.SERVICE_STATUS and caught.value.status == 302


def test_an_error_status_is_an_error_not_an_empty_answer(server: str) -> None:
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/missing", environ={})
    assert caught.value.code == outside.SERVICE_STATUS and caught.value.status == 404


@pytest.mark.parametrize("path", ["/big", "/big-chunked", "/big-unknown-length"])
def test_a_reply_over_the_cap_is_refused(server: str, path: str) -> None:
    """With a stated length, in chunks, and with no length at all (read until the service closes)."""
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", path, environ={}, max_bytes=1000)
    assert caught.value.code == outside.SERVICE_TOO_LARGE


@pytest.mark.parametrize("path", ["/big-chunked", "/big-unknown-length"])
def test_a_reply_under_the_cap_is_read_whole(server: str, path: str) -> None:
    assert call_service(_service(server), "GET", path, environ={}).body == b"x" * 5000


def test_nothing_answering_is_not_ready_with_the_install_steps() -> None:
    declared = _service(f"http://127.0.0.1:{_free_port()}")
    with pytest.raises(ServiceError) as caught:
        call_service(declared, "GET", "/", environ={}, timeout=2.0)
    assert caught.value.code == outside.OUTSIDE_PROGRAM_MISSING
    assert "run the test server" in str(caught.value) and "G6 never installs it" in str(caught.value)


def test_the_time_limit_holds_for_a_reply_that_keeps_trickling_in(server: str) -> None:
    """Review A5: the limit was per socket read, so a reply sending a byte every 0.2 s ran for as long as it liked."""
    started = time.monotonic()
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/trickle", environ={}, timeout=1.0)
    assert caught.value.code == outside.SERVICE_TIMEOUT
    assert time.monotonic() - started < 3.0


def test_a_slow_service_is_slow_not_missing(server: str) -> None:
    """Review A5: a service that took the request and is still working is not "not installed"."""
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/slow", environ={}, timeout=1.0)
    assert caught.value.code == outside.SERVICE_TIMEOUT
    assert "install" not in str(caught.value) and "1 second" in str(caught.value)


def test_a_service_that_hangs_up_without_a_reply_is_failed_not_missing(server: str) -> None:
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/hang-up", environ={})
    assert caught.value.code == outside.SERVICE_FAILED and "install" not in str(caught.value)


def test_an_error_status_carries_a_bounded_clean_excerpt_of_the_reason(server: str) -> None:
    """A ComfyUI 400 says why in its body: the caller sees it, without control characters, at most 300 of them."""
    with pytest.raises(ServiceError) as caught:
        call_service(_service(server), "GET", "/bad", environ={})
    said = str(caught.value)
    assert caught.value.code == outside.SERVICE_STATUS and caught.value.status == 400
    assert "bad prompt: [31m node 7 has no input" in said
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in said)
    assert "y" * 250 in said and "y" * 301 not in said


def test_an_ipv6_loopback_service_is_reached() -> None:
    class _V6(_Server):
        address_family = socket.AF_INET6

    try:
        httpd = _V6(("::1", 0), _Handler)
    except OSError:
        pytest.skip("no IPv6 loopback on this machine")
    with _serving(httpd):
        reply = call_service(_service(f"http://[::1]:{httpd.server_address[1]}"), "GET", "/v6", environ={})
    assert reply.json() == {"path": "/v6"}


@pytest.mark.parametrize("url", ["http://user:secret@127.0.0.1:8188", "http://evil.example@127.0.0.1:8188",
                                 "http://127.0.0.1@evil.example:8188", "http://localhost.:8188",
                                 "http://[::ffff:127.0.0.1]:8188"])
def test_a_url_with_a_user_name_or_an_unusual_host_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="this machine"):
        _service(url)
    with pytest.raises(ServiceError) as caught:
        call_service(_service("http://127.0.0.1:8188"), "GET", "/", environ={"TEST_SERVICE_URL": url})
    assert caught.value.code == outside.SERVICE_NOT_LOCAL


def test_only_a_declared_service_and_a_plain_path_are_called(server: str) -> None:
    command = OutsideProgram("Tool", "does things", "install it", "MIT", command="tool")
    with pytest.raises(ValueError, match="service"):
        call_service(command, "GET", "/")
    for bad in ("system_stats", "http://127.0.0.1:1/x", "/a\r\nHost: evil"):
        with pytest.raises(ValueError):
            call_service(_service(server), "GET", bad, environ={})


def test_detect_reads_the_operators_variable_too(server: str) -> None:
    declared = _service(f"http://127.0.0.1:{_free_port()}")
    assert detect(declared, environ={"TEST_SERVICE_URL": server}).found
    refused = detect(declared, environ={"TEST_SERVICE_URL": "http://example.com/"})
    assert not refused.found and "this machine" in refused.detail
