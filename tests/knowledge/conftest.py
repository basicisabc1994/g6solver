# SPDX-License-Identifier: Apache-2.0
"""The knowledge family's tests reach no network service. Every test runs with g6.core.http's own transport and
resolver replaced by ones that refuse, and socket.create_connection refusing anything but 127.0.0.1 (the local test
servers), unless the test brings its own double (a client made with its own transport and resolver). Whatever a test
reached for without a double is recorded, and the test fails when it ends: nothing was sent."""
from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from g6.core import http

#: The one address a test may connect to: the local servers the tests start.
LOOPBACK = "127.0.0.1"


class NoNetwork:
    """The guard: each request, lookup or connection a test made without a double is refused and recorded."""

    def __init__(self, connect) -> None:
        self.attempts: list[str] = []
        self._connect = connect

    def _refuse(self, what: str) -> OSError:
        self.attempts.append(what)
        return ConnectionRefusedError(f"the knowledge tests reach no network service: {what} was refused; give the "
                                      "test a transport double")

    def transport(self, request: http.Request) -> http.Response:
        raise self._refuse(f"a request to {request.url}")

    def resolver(self, host: str, port: int) -> list[str]:
        raise self._refuse(f"a lookup of {host}")

    def create_connection(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else address
        if host != LOOPBACK:
            raise self._refuse(f"a connection to {address!r}")
        return self._connect(address, *args, **kwargs)

    def check(self) -> None:
        """Fail the test if it reached for the network without a double."""
        if self.attempts:
            pytest.fail("a test reached for the network without a double: " + "; ".join(self.attempts), pytrace=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch) -> Iterator[NoNetwork]:
    guard = NoNetwork(socket.create_connection)
    monkeypatch.setattr(http, "stdlib_transport", guard.transport)
    monkeypatch.setattr(http, "system_resolver", guard.resolver)
    monkeypatch.setattr(socket, "create_connection", guard.create_connection)
    yield guard
    guard.check()
