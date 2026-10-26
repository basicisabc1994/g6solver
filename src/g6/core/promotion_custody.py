# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""promotion_custody — route the evaluator OUT OF PROCESS (K-P0-4 slice 5, client side).

Slices 1-3 built an in-process evaluator authority (``core.promotion_evaluator``):
a witness promotes only when HMAC-signed by the key
``G6_PROMOTION_EVALUATOR_KEY`` in the person's key file. Two gaps remained, both documented in that module:

- **G1 key custody** — in one process the key file is readable, so a
  determined in-process component could read the key.
- **G2 raw-``stamp`` bypass** — an in-process key-holder can call ``stamp``
  directly and skip the independent replay gate.

This module closes both by moving the key AND the crypto into a separate process
(the ``promotion_evaluator_service`` child, cloning the science_harness
``hidden_evaluator`` per-call-subprocess model). When
``G6_PROMOTION_EVALUATOR_KEY_PATH`` names a key file, the generator process never
holds the key: ``verify`` and ``stamp_replayed`` are answered by spawning the child
(which loads the key from the file, in ITS process only), and there is **no
cross-process ``stamp``/``sign``** — so the raw-stamp bypass cannot be reached from
the generator at all. Because the child runs the replay before it signs (the child
calls ``stamp_replayed``), moving signing out of process does not turn the service
into a blind signing oracle: a result that does not independently reproduce is
never signed (the de Bruijn property, now in a genuinely separate process — G3).

Staging (opt-in, fail-closed, non-regressing):

- ``G6_PROMOTION_EVALUATOR_KEY_PATH`` unset (production + every existing caller
  default): ``custody_configured()`` is False and ``promotion_evaluator`` takes its
  existing in-process branch — byte-identical to before this module existed.
- Path set: the three promotion seams (the law's ``promotion_witness_faults``, the
  registry invoker's ``_merge_envelope``, and lean's ``server._run``) route through
  the child transparently, because they already call ``promotion_evaluator``'s
  ``evaluator_configured`` / ``verify`` / ``stamp_replayed`` — which delegate here.

COST NOTE: ``verify`` spawns a subprocess. It is called at a promotion door only
for an envelope that both proposes ``verified`` AND carries a witness (rare — only
oracle components minting ``verified``), so per-call spawn is acceptable. If
promotion ever becomes hot, a long-lived local signing daemon (design memo option
B) can be layered UNDER this same interface without changing any call site.

HONEST SCOPE: the custody boundary is process-memory + interface, not an OS-level
ACL against the same user — the key file is same-user-readable, exactly as the
science_harness hidden seed is. The property is that the generator's own code path
never loads the key and has no signing interface; the key exists only in the child.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from typing import Any

# The key file that turns ON out-of-process custody (path to the evaluator key).
_KEY_PATH_ENV = "G6_PROMOTION_EVALUATOR_KEY_PATH"
# Comma-separated importable modules the child imports so they register their
# oracle replays (e.g. lean_prover's promotion_replay). Optional.
_ORACLE_MODULES_ENV = "G6_PROMOTION_ORACLE_MODULES"
# os.pathsep-joined extra PYTHONPATH entries for oracle modules that live outside
# the components root. Optional.
_EXTRA_PATH_ENV = "G6_PROMOTION_EVALUATOR_EXTRA_PATH"
# Set inside the child so promotion_evaluator's custody wrappers resolve to the
# IN-PROCESS crypto there instead of spawning another child (recursion guard).
_INPROC_SENTINEL_ENV = "G6_PROMOTION_EVALUATOR_INPROC"

_SERVICE_MODULE = "g6.core.promotion_evaluator_service"
_VERIFY_TIMEOUT_S = 60.0
_STAMP_TIMEOUT_S = 240.0


def custody_configured() -> bool:
    """True when an OUT-OF-PROCESS evaluator custody service is configured.

    Enabled by ``G6_PROMOTION_EVALUATOR_KEY_PATH``. Always False inside the
    evaluator child (the ``_INPROC_SENTINEL_ENV`` guard), so the child answers
    with the in-process crypto rather than re-spawning itself.
    """
    if os.environ.get(_INPROC_SENTINEL_ENV):
        return False
    return bool((os.environ.get(_KEY_PATH_ENV) or "").strip())


def _components_root() -> str:
    """The folder that holds the ``g6`` package (``…/src`` for ``src/g6/core/promotion_custody.py``)."""
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _service_env() -> dict[str, str]:
    """A scrubbed environment for the child (mirrors science_harness).

    The child starts CLEAN — interpreter needs + import path + the custody config
    vars — and NOTABLY does not carry ``G6_PROMOTION_EVALUATOR_KEY``: no key the
    generator's environment holds may reach the child, which loads the key
    from the FILE at ``_KEY_PATH_ENV``. The sentinel makes the child's custody
    wrappers resolve in-process (no re-spawn).
    """
    components = _components_root()
    vendor = os.path.join(os.path.dirname(components), "vendor")
    path_entries = [components] + ([vendor] if os.path.isdir(vendor) else [])
    extra = (os.environ.get(_EXTRA_PATH_ENV) or "").strip()
    if extra:
        path_entries = [p for p in extra.split(os.pathsep) if p] + path_entries
    env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join(path_entries),
        _INPROC_SENTINEL_ENV: "1",
    }
    for name in (_KEY_PATH_ENV, _ORACLE_MODULES_ENV):
        value = os.environ.get(name)
        if value:
            env[name] = value
    # Only the OS essentials the interpreter/toolchain need to start — including
    # the home-directory vars. Some oracle import chains compute ``Path.home()``
    # at MODULE LOAD (e.g. importing ``mvp.coder.promotion_replay`` runs
    # ``mvp/coder/__init__.py`` → ``learning_layer.runner``, which sets a ``~/.g6``
    # default path when the module is imported). With no home var in the scrubbed
    # env, ``Path.home()`` raises ``RuntimeError`` there, the oracle silently fails
    # to register (``_import_oracles`` is best-effort), and its otherwise-earned
    # ``verified`` would then wrongly demote under enforce. The home directory is
    # NOT the evaluator key (that is still loaded only from ``_KEY_PATH_ENV``), so
    # forwarding it does not weaken the custody boundary.
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT",
                 "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _call_service(
    op: str,
    witness: Any,
    timeout_s: float,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Spawn the child on one request and parse its JSON reply (fail-closed).

    Any spawn/exit/parse problem, or a witness that is not JSON-serializable (a
    well-formed witness is), returns ``{"ok": False, ...}``.

    ``extra`` carries the non-witness request fields the receipt ops need
    (``core``/``signature``). It is merged UNDER the fixed ``op``/``witness``
    keys so a caller can never restate the operation.
    """
    payload: Any = dict(witness) if isinstance(witness, Mapping) else witness
    try:
        request = json.dumps({**dict(extra or {}), "op": op, "witness": payload})
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"witness not serializable: {exc!r}"}
    try:
        with tempfile.TemporaryDirectory(prefix="g6_promotion_") as tmp:
            request_path = os.path.join(tmp, "request.json")
            with open(request_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(request)
            proc = subprocess.run(
                [sys.executable, "-m", _SERVICE_MODULE, request_path],
                env=_service_env(),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"evaluator service failed to run: {exc!r}"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"evaluator service exited {proc.returncode}: {proc.stderr[-500:]}",
        }
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "error": "evaluator service emitted no output"}
    try:
        reply = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": "evaluator service emitted no parseable reply"}
    if not isinstance(reply, dict):
        return {"ok": False, "error": "evaluator service reply was not an object"}
    return reply


def verify_via_custody(witness: Any) -> bool:
    """Out-of-process signature check. False on any gap (fail-closed)."""
    reply = _call_service("verify", witness, _VERIFY_TIMEOUT_S)
    return reply.get("ok") is True and reply.get("result") is True


def stamp_replayed_via_custody(witness: Mapping[str, Any]) -> dict[str, Any]:
    """Out-of-process replay-then-sign. Mirrors the in-process contract.

    Returns the signed witness on success; raises ``ValueError`` when the service
    could not stamp (no reproduction, no usable key, or any transport gap), so the
    caller (e.g. lean ``server._run``) leaves the witness unsigned and the law
    demotes it under enforce — fail-closed, never a silent promotion.
    """
    reply = _call_service("stamp_replayed", witness, _STAMP_TIMEOUT_S)
    signed = reply.get("witness") if reply.get("ok") is True else None
    if not isinstance(signed, dict):
        # ValueError (not TypeError) deliberately: this mirrors the in-process
        # stamp_replayed's "could not reproduce" contract, which lean's
        # server._run catches as ``except (RuntimeError, ValueError)``. A missing
        # witness here means the service could not stamp, not a caller type error.
        raise ValueError(  # noqa: TRY004 - intentional contract parity, see above
            "out-of-process evaluator could not stamp the witness: "
            f"{reply.get('error', 'unknown error')}"
        )
    return signed


def sign_receipt_via_custody(core: Mapping[str, Any]) -> str:
    """Out-of-process seal for an execution-receipt core. ``""`` on any gap.

    Returns the empty string — never raises, never falls back to an in-process
    key — when custody is unconfigured, the child cannot start, the key file is
    unusable, or the reply is malformed. The caller
    (``job_framework.execution_receipts``) treats ``""`` as "no seal", which
    fails closed under its ``require`` mode.

    HONEST SCOPE, and the reason this is NOT a reopening of the G2 raw-``stamp``
    bypass: this op signs in the RECEIPT domain only (a JSON-object payload with
    a literal scheme tag), which can never produce a byte string the promotion
    witness encoder produces (a JSON array). So a caller cannot use it to obtain
    a witness signature and skip the replay gate — ``stamp_replayed`` remains the
    only cross-process mint for a promotable witness. What this op DOES give an
    in-process caller is a blind sealing oracle *within the receipt domain*: it
    will seal any (content, run, task) it is handed. That is what a receipt
    means. It binds and authenticates the statement; it does not attest that an
    execution occurred.
    """
    if not custody_configured() or not isinstance(core, Mapping):
        return ""
    reply = _call_service(
        "sign_receipt", None, _VERIFY_TIMEOUT_S, extra={"core": dict(core)}
    )
    if reply.get("ok") is not True:
        return ""
    signature = reply.get("signature")
    return signature if isinstance(signature, str) and signature else ""


def verify_receipt_via_custody(core: Mapping[str, Any], signature: Any) -> bool:
    """Out-of-process seal check for a receipt core. False on any gap (fail-closed)."""
    if not custody_configured() or not isinstance(core, Mapping):
        return False
    if not isinstance(signature, str) or not signature:
        return False
    reply = _call_service(
        "verify_receipt",
        None,
        _VERIFY_TIMEOUT_S,
        extra={"core": dict(core), "signature": signature},
    )
    return reply.get("ok") is True and reply.get("result") is True


__all__ = [
    "custody_configured",
    "sign_receipt_via_custody",
    "stamp_replayed_via_custody",
    "verify_receipt_via_custody",
    "verify_via_custody",
]
