# SPDX-License-Identifier: Apache-2.0
"""Operator registration, outside-program declarations and lazy optional imports."""
from __future__ import annotations

import re
from collections.abc import Callable

from g6.core.outside import OutsideProgram

# G6-owned process state. Only trusted Python setup can change it; there is no registration op.
FUNCTIONS: dict[str, str] = {}


def register_function(name: str, function: Callable) -> None:
    """Register a trusted importable function without calling it. Repeat at server startup."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or not callable(function):
        raise ValueError("a function needs an operator-selected name and a callable")
    module, qualified = getattr(function, "__module__", ""), getattr(function, "__qualname__", "")
    if not module or module == "__main__" or not qualified or "<" in qualified:
        raise ValueError("register an importable module-level function")
    if name not in FUNCTIONS and len(FUNCTIONS) >= 256:
        raise ValueError("at most 256 functions may be registered")
    FUNCTIONS[name] = f"{module}.{qualified}"


REDIS = OutsideProgram(
    name="Redis server", purpose="holds the operator's RQ queues over the network protocol; detected by PING, never linked or distributed",
    install="Install a Redis server separately and start redis-server --bind 127.0.0.1 --port 6379. "
            "Alternatively install protocol-compatible Valkey (BSD-3-Clause) and start valkey-server on that port. "
            "Use an isolated trusted queue and an operator-configured local connection.",
    licence="Redis up to 7.2: BSD-3-Clause; 7.4: RSALv2 or SSPLv1; 8.0 and later: RSALv2, SSPLv1 or AGPLv3. "
            "Valkey: BSD-3-Clause, protocol-compatible. User-installed outside server; never bundled.",
    command="redis-server")
WORKER = OutsideProgram(
    name="rq worker", purpose="runs registered jobs in a separate POSIX-only program; detected by its heartbeat",
    install="On POSIX, install the rq extra in the worker environment and run "
            "rq worker --serializer rq.serializers.JSONSerializer --with-scheduler default. "
            "Make registered functions importable there. The worker is POSIX-only; G6 never starts it.",
    licence="BSD-2-Clause", command="rq")


def dependencies():
    """Optional modules are loaded only when an operation needs them."""
    import redis
    from rq import Queue, Worker
    from rq.job import Job
    from rq.registry import FailedJobRegistry
    from rq.serializers import JSONSerializer
    return redis, Queue, Worker, Job, FailedJobRegistry, JSONSerializer
