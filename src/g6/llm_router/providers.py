# SPDX-License-Identifier: Apache-2.0
"""The ops that reach the network or change the machine: list, pull and delete local Ollama models, and fetch
OpenRouter's live model catalogue and prices.

Each is listed in ``__g6_sensitive_ops__``, so the registry refuses it over MCP unless the person who runs the server
lists it in ``G6_MCP_SENSITIVE``; it runs from Python. Nothing here opens a connection itself:

* the Ollama ops go through :func:`g6.core.outside.call_service` to :data:`OLLAMA`, the local service the package
  declares: plain HTTP on this machine only (the address is ``OLLAMA_BASE_URL``, the setting of the person who runs
  G6, which may move it but never off this machine), under a time limit and a size cap, no redirect followed, and
  nothing answering refused with the install steps;
* the catalogue goes through :class:`g6.core.http.Client`: every address, and every redirect hop, checked before it is
  sent (a redirect to this machine or a private address is refused), a time limit, a size cap, a 4xx or 5xx an error,
  never an empty catalogue. OpenRouter's public catalogue needs no key, so none is sent. It is an online op, off over
  MCP unless the person who runs the server turns it on.

A reply larger than :data:`MAX_REPLY_BYTES` is refused. The live catalogue is returned and compared with the committed
table, never written into it. Every result is labelled: what a server said is a ``qualified-draft`` (G6 did not run any
model it lists); a server that cannot be reached, or that answers with an error, is ``blocked-escalated`` with its own
code.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from g6.core import http, outside
from g6.llm_router.envelope import (
    INPUT_INVALID,
    LIVE_ANSWER,
    PROVIDER_ERROR,
    PROVIDER_UNREACHABLE,
    RouterResult,
    answer,
    refuse,
)
from g6.llm_router.registry import CognitiveTaskRegistry, default_registry

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
#: The largest reply read from a server; a larger one is refused rather than held in memory.
MAX_REPLY_BYTES = 32 * 1024 * 1024
#: The local model server the Ollama ops reach, and the router's chains run on.
OLLAMA = outside.OutsideProgram(
    name="Ollama", purpose="list_local_models, pull_model and delete_model reach the Ollama server on this machine, "
    "and the router's chains run its models", install="Install Ollama from ollama.com and start it (ollama serve).",
    licence="MIT", url="http://127.0.0.1:11434/", env="OLLAMA_BASE_URL")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")
_TOLERANCE = 0.005  # relative; a zero price is exact
_HTTP_CODES = {http.REFUSED: http.REFUSED, http.FAILED: PROVIDER_UNREACHABLE}
_SERVICE_CODES = {outside.OUTSIDE_PROGRAM_MISSING: outside.OUTSIDE_PROGRAM_MISSING,
                  outside.SERVICE_NOT_LOCAL: outside.SERVICE_NOT_LOCAL}

#: ``g6.core.outside.call_service``'s signature: the Ollama ops take another for tests.
Call = Callable[..., outside.ServiceReply]


def is_model_name(name: object) -> bool:
    """Whether ``name`` can be an Ollama model name: letters, digits and ``. _ : / -``, at most 200 characters."""
    return isinstance(name, str) and bool(_MODEL_NAME.fullmatch(name)) and ".." not in name


class _Failed(Exception):
    def __init__(self, code: str, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.code, self.reason, self.status = code, reason, status


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _model_name(model: str) -> str:
    if not is_model_name(model):
        raise _Failed(INPUT_INVALID, f"{model!r} is not an Ollama model name (letters, digits and . _ : / -, at most "
                                     "200 characters)")
    return model


def _json(raw: bytes, where: str) -> Any:
    if not raw.strip():
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _Failed(PROVIDER_ERROR, f"the reply from {where} is not JSON") from exc


def _ollama(method: str, path: str, body: dict[str, Any] | None, *, timeout: float, call: Call | None) -> Any:
    """One request to :data:`OLLAMA` through ``g6.core.outside``; its JSON reply, or :class:`_Failed`."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data is not None else {}
    try:
        reply = (call or outside.call_service)(OLLAMA, method, path, body=data, headers=headers, timeout=timeout,
                                               max_bytes=MAX_REPLY_BYTES)
    except outside.ServiceError as exc:
        raise _Failed(_SERVICE_CODES.get(exc.code, PROVIDER_ERROR), str(exc), exc.status) from exc
    return _json(reply.body, f"Ollama {method} {path}")


def _where() -> str:
    """The Ollama address, as the operator's setting gives it (for the evidence; ``call_service`` checks it)."""
    try:
        return outside.service_url(OLLAMA).rstrip("/")
    except outside.ServiceError:
        return ""


def catalogue_client(timeout: float) -> http.Client:
    """The checked HTTP client the catalogue is fetched with: ``timeout`` seconds, at most :data:`MAX_REPLY_BYTES`."""
    return http.Client(timeout=timeout, max_bytes=MAX_REPLY_BYTES)


def _evidence(url: str, method: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "live_server", "url": url, "method": method, "network": True, "at": _now(), **extra}


def _refused(operation: str, failed: _Failed, url: str, method: str, ids: dict[str, str],
             **fields: Any) -> RouterResult:
    return refuse(operation, failed.code, failed.reason, evidence=_evidence(url, method, status=failed.status),
                  **fields, **ids)


def list_local_models(*, timeout: float = 10.0, call: Call | None = None,
                      registry: CognitiveTaskRegistry | None = None, request_id: str = "",
                      run_id: str = "") -> RouterResult:
    """The models installed in the local Ollama (``GET /api/tags``), each marked ``in_table`` when the committed table
    has a profile for it. ``installed`` is the list of names to pass to ``route``."""
    ids, base = {"request_id": request_id, "run_id": run_id}, _where()
    try:
        reply = _ollama("GET", "/api/tags", None, timeout=timeout, call=call)
        if not isinstance(reply, dict) or not isinstance(reply.get("models", []), list):
            raise _Failed(PROVIDER_ERROR, "Ollama's reply to GET /api/tags has no list of models")
    except _Failed as failed:
        return _refused("list_local_models", failed, f"{base}/api/tags", "GET", ids, models=[], installed=[])
    reg = registry or default_registry()
    models = []
    for row in reply.get("models") or []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        name, details = str(row["name"]), row.get("details") if isinstance(row.get("details"), dict) else {}
        models.append({"name": name, "model_id": f"ollama/{name}", "in_table": reg.get_profile(f"ollama/{name}")
                       is not None, "size_bytes": row.get("size"), "family": details.get("family"),
                       "parameter_size": details.get("parameter_size"),
                       "quantization_level": details.get("quantization_level")})
    return answer("list_local_models", LIVE_ANSWER, f"What the Ollama server at {base} reported. G6 did not run any "
                  "of these models, so whether each one works is not checked.",
                  "Pass installed to route to keep the route to the local models you have.",
                  evidence=_evidence(f"{base}/api/tags", "GET", status=200, count=len(models)), base_url=base,
                  models=models, installed=[m["name"] for m in models], count=len(models), **ids)


def pull_model(model: str, *, timeout: float = 1800.0, call: Call | None = None, request_id: str = "",
               run_id: str = "") -> RouterResult:
    """Download ``model`` into the local Ollama (``POST /api/pull``, waiting for it to finish). Changes the machine."""
    ids, base = {"request_id": request_id, "run_id": run_id}, _where()
    try:
        name = _model_name(model)
        reply = _ollama("POST", "/api/pull", {"model": name, "stream": False}, timeout=timeout, call=call)
        if isinstance(reply, dict) and reply.get("error"):
            raise _Failed(PROVIDER_ERROR, f"Ollama could not pull {name}: {str(reply['error'])[:300]}")
    except _Failed as failed:
        return _refused("pull_model", failed, f"{base}/api/pull", "POST", ids, model=model, status=None)
    status = str(reply.get("status", "")) if isinstance(reply, dict) else ""
    return answer("pull_model", LIVE_ANSWER, f"The Ollama server at {base} reported {status or 'no status'!r} for "
                  f"{name}. G6 did not run the model, so whether it works on your machine is not checked.",
                  "Run the model on an example before relying on it.",
                  evidence=_evidence(f"{base}/api/pull", "POST", status=200, model=name), model=name, status=status,
                  base_url=base, **ids)


def delete_model(model: str, *, timeout: float = 30.0, call: Call | None = None, request_id: str = "",
                 run_id: str = "") -> RouterResult:
    """Remove ``model`` from the local Ollama (``DELETE /api/delete``). Changes the machine."""
    ids, base = {"request_id": request_id, "run_id": run_id}, _where()
    try:
        name = _model_name(model)
        _ollama("DELETE", "/api/delete", {"model": name}, timeout=timeout, call=call)
    except _Failed as failed:
        return _refused("delete_model", failed, f"{base}/api/delete", "DELETE", ids, model=model, deleted=False)
    return answer("delete_model", LIVE_ANSWER, f"The Ollama server at {base} reported {name} deleted.",
                  "List the local models to confirm it is gone.",
                  evidence=_evidence(f"{base}/api/delete", "DELETE", status=200, model=name), model=name,
                  deleted=True, base_url=base, **ids)


def parse_price(value: Any) -> float | None:
    """A price the provider stated (US dollars per token), or ``None`` when it stated none.

    Ported from the earlier router: ``-1`` (OpenRouter's mark for a variable price), a negative, a flag, text that is
    not a number, NaN or infinity is ``None``, never zero; and a zero is accepted only when the text really is zero,
    so ``"1e-999"`` (a stated, tiny price that underflows to 0.0) is ``None``, not free.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    if parsed == 0.0:
        try:
            if Decimal(str(value)) != 0:
                return None
        except InvalidOperation:
            return None
    return parsed


def _per_million(value: Any) -> float | None:
    price = parse_price(value)
    return None if price is None else float(Decimal(str(value)) * 1_000_000)


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None or a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(abs(a), abs(b)) <= _TOLERANCE


def _catalogue(client: http.Client) -> Any:
    try:
        reply = client.get(OPENROUTER_MODELS_URL, headers={"accept": "application/json"})
    except http.HTTPError as exc:
        raise _Failed(_HTTP_CODES.get(exc.code, PROVIDER_ERROR), str(exc), exc.status) from exc
    found = _json(reply.body, OPENROUTER_MODELS_URL)
    if not isinstance(found, dict) or not isinstance(found.get("data"), list):
        raise _Failed(PROVIDER_ERROR, f"the reply from {OPENROUTER_MODELS_URL} has no list of models")
    return found


def fetch_openrouter_catalogue(*, timeout: float = 15.0, client: http.Client | None = None,
                               registry: CognitiveTaskRegistry | None = None, request_id: str = "",
                               run_id: str = "") -> RouterResult:
    """OpenRouter's live models and prices (US dollars per million tokens; ``None`` when not stated), and how they
    differ from the committed table: ``committed_differences`` names each committed OpenRouter model whose live price
    differs or that the listing no longer has. The committed table is never changed. ``client`` replaces the checked
    client (:func:`catalogue_client`), for tests."""
    ids, url = {"request_id": request_id, "run_id": run_id}, OPENROUTER_MODELS_URL
    try:
        reply = _catalogue(client or catalogue_client(timeout))
    except _Failed as failed:
        return _refused("fetch_openrouter_catalogue", failed, url, "GET", ids, models=[], committed_differences=[])
    fetched_at = _now()
    models = []
    for row in reply["data"]:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        prompt, completion = _per_million(pricing.get("prompt")), _per_million(pricing.get("completion"))
        models.append({"model_id": f"openrouter/{row['id']}", "name": str(row.get("name") or ""),
                       "context_length": row.get("context_length"), "prompt_usd_per_million": prompt,
                       "completion_usd_per_million": completion,
                       "price_known": prompt is not None and completion is not None})
    live = {m["model_id"]: m for m in models}
    differences = []
    for prof in (registry or default_registry()).models_by_provider("openrouter"):
        committed_prompt, committed_completion = prof.prices()
        found = live.get(prof.model_id)
        if found is None:
            differences.append({"model_id": prof.model_id, "difference": "not listed"})
        elif not (_close(committed_prompt, found["prompt_usd_per_million"])
                  and _close(committed_completion, found["completion_usd_per_million"])):
            differences.append({"model_id": prof.model_id, "difference": "price",
                                "committed_prompt_usd_per_million": committed_prompt,
                                "committed_completion_usd_per_million": committed_completion,
                                "live_prompt_usd_per_million": found["prompt_usd_per_million"],
                                "live_completion_usd_per_million": found["completion_usd_per_million"]})
    unpriced = sum(1 for m in models if not m["price_known"])
    return answer("fetch_openrouter_catalogue", LIVE_ANSWER,
                  f"OpenRouter's catalogue as listed at {fetched_at}. Prices change, and a model being listed does "
                  "not show it works. G6's committed table was not changed.",
                  "Read committed_differences; change the committed table only through a reviewed change.",
                  evidence=_evidence(url, "GET", status=200, count=len(models), unpriced=unpriced), models=models,
                  count=len(models), unpriced=unpriced, fetched_at=fetched_at, committed_differences=differences,
                  **ids)


__all__ = ["MAX_REPLY_BYTES", "OLLAMA", "OPENROUTER_MODELS_URL", "catalogue_client", "delete_model",
           "fetch_openrouter_catalogue", "is_model_name", "list_local_models", "parse_price", "pull_model"]