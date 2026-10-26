# SPDX-License-Identifier: Apache-2.0
"""Talking to the Cognee server the person runs: only through ``g6.core.outside.call_service``, never by importing it.

That means plain HTTP to 127.0.0.1 (or the local address ``G6_COGNEE_URL`` names), with redirects refused, error
statuses raised, replies capped, and nothing answering the honest ``G6_E_OUTSIDE_PROGRAM_MISSING`` refusal with the
install steps. Every failure here is a :class:`CogneeError` with a stable code, never an empty answer. The routes are
those of Cognee's own HTTP API (``/health``, ``/api/v1/add``, ``/api/v1/cognify``, ``/api/v1/search`` and
``/api/v1/datasets``). G6 sends Cognee no setting. When you put your Cognee token in your key file (:data:`COGNEE_KEY`,
read through ``g6.core.keys``), every request carries it as a bearer token, and only to that local address; without
one, G6 sends no sign-in, and a Cognee that answers has its sign-in off.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from g6.core import keys, outside

#: How long one request may take (a cognify, which runs Cognee's model over the text, is given its own time), and the
#: most one reply may carry.
REQUEST_TIMEOUT = 60.0
MAX_REPLY_BYTES = 32 * 1024 * 1024
REPLY = "G6_E_MEMORY_COGNEE_REPLY"
#: Your Cognee's sign-in token, which you put in your key file; G6 sends it only to the Cognee on this machine. Cognee
#: may spend on its model provider with what G6 sends, so a token there is also your approval of that.
COGNEE_KEY = keys.Key("COGNEE_API_TOKEN", "your Cognee's sign-in token (ctx_cognee), sent only to the Cognee at "
                      "127.0.0.1:8000 or the local address G6_COGNEE_URL names",
                      where="your Cognee's own sign-in (its /api/v1/auth/login route, in the API pages your Cognee "
                            "serves at /docs)", paid=True)


def _sign_in_hint(sent: bool) -> str:
    if sent:
        return (f" Cognee refused the token from your key file ({COGNEE_KEY.name}): it may have expired; sign in to "
                "your Cognee again and replace that line.")
    return (f" Cognee asks for sign-in: put your Cognee token in your key file ({keys.path()}) as "
            f"{COGNEE_KEY.name}=<token>; G6 sends it only to this Cognee.")


class CogneeError(Exception):
    """A Cognee call that gave no usable answer: its stable ``code`` and the reason in words."""

    def __init__(self, code: str, reason: str, *, status: int | None = None) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason, self.status = code, reason, status


class Cognee:
    """One conversation with the declared Cognee service. ``environ`` replaces the environment the service's address
    is read from (the operator's ``G6_COGNEE_URL``); ``token`` is your Cognee token from your key file, or ``None``."""

    def __init__(self, program: outside.OutsideProgram, *, environ: Mapping[str, str] | None = None,
                 token: str | None = None) -> None:
        self.program, self.environ, self._token = program, environ, token or None

    def address(self) -> str:
        return outside.service_url(self.program, environ=self.environ)

    def sign_in(self) -> str:
        """What G6 sent Cognee to sign in, in words (never the token), for the evidence of a call Cognee answered."""
        if self._token:
            return f"G6 sent your Cognee token from your key file ({COGNEE_KEY.name}), only to {self.address()}"
        return (f"off: there is no {COGNEE_KEY.name} in your key file, so G6 sent no sign-in and your Cognee answered "
                "without one. With its sign-in off, any program on this machine, and plausibly a web page open in your "
                "browser (through a cross-site form), can read and write your Cognee datasets")

    def _call(self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
              timeout: float = REQUEST_TIMEOUT) -> outside.ServiceReply:
        sent = dict(headers or {})
        if self._token:
            sent["Authorization"] = f"Bearer {self._token}"
        try:
            return outside.call_service(self.program, method, path, body=body, headers=sent, environ=self.environ,
                                        timeout=timeout, max_bytes=MAX_REPLY_BYTES)
        except outside.ServiceError as exc:
            hint = _sign_in_hint(bool(self._token)) if exc.status in (401, 403) else ""
            message = exc.message.replace(self._token, "<your token>") if self._token else exc.message
            raise CogneeError(exc.code, message + hint, status=exc.status) from None

    def _json(self, method: str, path: str, *, payload: Any = None, timeout: float = REQUEST_TIMEOUT) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        reply = self._call(method, path, body=body, timeout=timeout,
                           headers={"Content-Type": "application/json"} if body is not None else None)
        if not reply.body.strip():
            return None
        try:
            return reply.json()
        except (ValueError, RecursionError):  # not JSON, or JSON nested too deep to read
            raise CogneeError(REPLY, f"Cognee answered {method} {path.split('?')[0]} with something that is not "
                                     "JSON G6 can read") from None

    def health(self) -> dict[str, Any]:
        found = self._json("GET", "/health")
        if not isinstance(found, dict):
            raise CogneeError(REPLY, "Cognee's /health answer is not a JSON object")
        return found

    def datasets(self) -> list[dict[str, Any]]:
        found = self._json("GET", "/api/v1/datasets")
        if not isinstance(found, list) or not all(isinstance(d, dict) and d.get("id") and isinstance(d.get("name"), str)
                                                  for d in found):
            raise CogneeError(REPLY, "Cognee's dataset list is not a list of datasets with an id and a name")
        return found

    def dataset_named(self, name: str) -> dict[str, Any] | None:
        return next((d for d in self.datasets() if d["name"] == name), None)

    def items(self, dataset_id: str) -> list[dict[str, Any]] | None:
        """The items in one dataset; ``None`` when Cognee says there is no such dataset (404)."""
        try:
            found = self._json("GET", f"/api/v1/datasets/{quote(dataset_id, safe='')}/data")
        except CogneeError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(found, list) or not all(isinstance(i, dict) and i.get("id") for i in found):
            raise CogneeError(REPLY, "Cognee's list of a dataset's items is not a list of items with an id")
        return found

    def add(self, text: str, dataset: str, filename: str) -> Any:
        """Send ``text`` as one plain-text file to ``dataset`` (created when Cognee has none of that name)."""
        boundary = f"g6-{uuid.uuid4().hex}"
        body = (b"--%s\r\nContent-Disposition: form-data; name=\"datasetName\"\r\n\r\n%s\r\n"
                % (boundary.encode(), dataset.encode("utf-8")))
        body += (b"--%s\r\nContent-Disposition: form-data; name=\"data\"; filename=\"%s\"\r\n"
                 b"Content-Type: text/plain; charset=utf-8\r\n\r\n" % (boundary.encode(), filename.encode()))
        body += text.encode("utf-8") + b"\r\n--%s--\r\n" % boundary.encode()
        reply = self._call("POST", "/api/v1/add", body=body,
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            return reply.json() if reply.body.strip() else None
        except (ValueError, RecursionError):  # not JSON, or JSON nested too deep to read
            raise CogneeError(REPLY, "Cognee answered POST /api/v1/add with something that is not JSON G6 can "
                                     "read") from None

    def cognify(self, dataset: str, timeout: float) -> Any:
        """Have Cognee build the graph of ``dataset`` (its model reads the text), waiting up to ``timeout`` seconds."""
        return self._json("POST", "/api/v1/cognify", payload={"datasets": [dataset], "run_in_background": False},
                          timeout=timeout)

    def search(self, query: str, search_type: str, top_k: int, dataset: str = "") -> list[Any]:
        payload: dict[str, Any] = {"search_type": search_type, "query": query, "top_k": top_k}
        if dataset:
            payload["datasets"] = [dataset]
        found = self._json("POST", "/api/v1/search", payload=payload)
        if not isinstance(found, list):
            raise CogneeError(REPLY, "Cognee's search answer is not a list")
        return found

    def graph(self, dataset_id: str) -> dict[str, Any]:
        found = self._json("GET", f"/api/v1/datasets/{quote(dataset_id, safe='')}/graph")
        if not isinstance(found, dict) or not isinstance(found.get("nodes"), list) or not isinstance(
                found.get("edges"), list):
            raise CogneeError(REPLY, "Cognee's graph answer has no list of nodes and of edges")
        return found

    def delete(self, dataset_id: str, data_id: str) -> None:
        self._call("DELETE", f"/api/v1/datasets/{quote(dataset_id, safe='')}/data/{quote(data_id, safe='')}")


__all__ = ["COGNEE_KEY", "MAX_REPLY_BYTES", "REPLY", "REQUEST_TIMEOUT", "Cognee", "CogneeError"]
