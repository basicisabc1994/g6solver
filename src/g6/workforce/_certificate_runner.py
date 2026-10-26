# SPDX-License-Identifier: Apache-2.0
"""Bounded, offline child protocol for component certificate observations.

Candidate, reference and generator execute in separate fresh sandboxes. The
candidate never sees expected values, the reference output, or a pass/fail check.
The parent computes comparisons and which mutations were caught.
"""
from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from typing import Any

from g6.core import sandbox

# Code in this string executes only through sandbox.run_script, never in G6's process.
CHILD = r'''
import base64
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import math
import runpy
import sys
from decimal import Decimal
from pathlib import Path

sys.dont_write_bytecode = True
root = Path.cwd().resolve()
payload = json.loads((root / "request.json").read_text(encoding="utf-8"))
snapshot = root / "snapshot"
for name, data in payload["files"].items():
    path = snapshot / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))

class Snapshot(importlib.abc.MetaPathFinder):
    # Every g6 module comes from the snapshot (the exact bytes under check, with each package initialiser and what
    # it imports) or not at all: none is loaded from an installed tree. Other modules are imported as installed.
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] != "g6":
            return None
        base = snapshot.joinpath(*fullname.split("."))
        init, module = base / "__init__.py", base.with_suffix(".py")
        if init.is_file():
            return importlib.util.spec_from_file_location(fullname, init, submodule_search_locations=[str(base)])
        if module.is_file():
            return importlib.util.spec_from_file_location(fullname, module)
        if base.is_dir():
            spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [str(base)]
            return spec
        raise ModuleNotFoundError(f"{fullname} is not in the certificate's snapshot", name=fullname)

sys.meta_path.insert(0, Snapshot())

def offline(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.fork"):
        raise PermissionError("certificate runs are offline and start no further programs")
sys.addaudithook(offline)

def number(value):
    if type(value) is bool:
        raise ValueError("boolean is not a numeric observation")
    if isinstance(value, (list, tuple)):
        return [number(v) for v in value]
    if isinstance(value, dict):
        return {str(k): number(v) for k, v in value.items()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite observation")
        value = Decimal.from_float(value)
    if not isinstance(value, (Decimal, int, str)):
        raise ValueError("non-numeric observation")
    found = Decimal(value)
    if not found.is_finite():
        raise ValueError("non-finite observation")
    return str(found)

mode = payload["mode"]
raised = []
if mode == "generate":
    mod = runpy.run_path(str(root / "snapshot" / payload["file"]))
    value = list(mod["generate"](payload["rule"], payload["seed"], payload["count"]))
    if len(value) != payload["count"]:
        raise ValueError("generator did not return the declared count")
    result = {"inputs": value}
elif mode == "invariants":
    mod = runpy.run_path(str(root / "snapshot" / payload["file"]))
    def decimal_tree(v):
        if isinstance(v, list):
            return [decimal_tree(x) for x in v]
        if isinstance(v, dict):
            return {k: decimal_tree(x) for k, x in v.items()}
        return Decimal(v)
    result = {"invariants": [mod["invariant"](payload["rule"], x, decimal_tree(y)) is True
                              for x, y in zip(payload["inputs"], payload["values"])]}
else:
    if mode == "reference":
        mod = runpy.run_path(str(root / "snapshot" / payload["file"]))
        fn = lambda x: mod["evaluate"](payload["rule"], x)
    else:
        name = payload["file"].removesuffix(".py").replace("/", ".")
        mod = importlib.import_module(name)
        target = getattr(mod, payload["function"])
        if payload.get("adapter"):
            # The adapter is the candidate's code too: an exception the target raises marks the observation an
            # error even when the adapter, or component code calling the target by name, catches it. The one
            # exception is a guard's declared refusal (its class named in checks.json's refusals, sent only for a
            # guard rule): that refusal is what the guard does, and the adapter projects it as the guard firing.
            refusals = set(payload.get("refusals") or ())
            def watched(*args, **kwargs):
                try:
                    return target(*args, **kwargs)
                except BaseException as exc:
                    if type(exc).__name__ not in refusals:
                        raised.append(type(exc).__name__)
                    raise
            setattr(mod, payload["function"], watched)
            adapter = runpy.run_path(str(root / "snapshot" / payload["adapter"]))
            fn = lambda x: adapter["evaluate"](watched, payload["rule"], x)
        else:
            fn = lambda x: target(**x)
    values = []
    for item in payload["inputs"]:
        raised.clear()
        try:
            value = number(fn(item))
            values.append({"error": raised[0]} if raised else {"value": value})
        except Exception as exc:
            values.append({"error": raised[0] if raised else type(exc).__name__})
    result = {"values": values}
result["nonce"] = payload["nonce"]
print("G6-CERTIFICATE " + json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
'''


def observe(files: Mapping[str, bytes], mode: str, **request: Any) -> dict | None:
    nonce = secrets.token_hex(32)
    payload = {**request,
               "files": {name: base64.b64encode(value).decode("ascii") for name, value in sorted(files.items())},
               "mode": mode, "nonce": nonce}
    try:
        run = sandbox.run_script(CHILD, {"request.json": json.dumps(payload, sort_keys=True, allow_nan=False)},
                                 timeout_s=15, mem_mb=512, max_output_bytes=2 * 1024 * 1024, file_mb=16)
    except (OSError, TimeoutError):
        return None
    if run.status != "ok" or run.returncode != 0:
        return None
    lines = run.stdout.splitlines()
    rows = [line for line in lines if line.startswith("G6-CERTIFICATE ")]
    if len(rows) != 1 or lines[-1] != rows[0]:
        return None
    try:
        result = json.loads(rows[0][len("G6-CERTIFICATE "):])
        if not isinstance(result, dict) or result.pop("nonce", None) != nonce:
            return None
        return result
    except (ValueError, TypeError):
        return None
