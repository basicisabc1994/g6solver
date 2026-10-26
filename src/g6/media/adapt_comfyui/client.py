# SPDX-License-Identifier: Apache-2.0
"""Talking to the ComfyUI server the person runs: only through ``g6.core.outside.call_service``.

That means plain HTTP to 127.0.0.1 (or the local address ``G6_COMFYUI_URL`` names), with redirects refused, error
statuses raised, replies capped, and nothing answering the honest "not ready" refusal with install steps. Every
failure here is a :class:`~g6.media._types.MediaError` with a stable code, never an empty answer.

:meth:`Comfy.wait` polls a prompt's history until it is finished. ComfyUI marks an errored prompt and a fully cached
one (every node served from its cache) as complete with no outputs; both are terminal. A cached prompt's pictures are
those of the most recent earlier run of exactly the same graph still in the server's history, so :meth:`Comfy.cached`
reads that run's outputs and says which run it was; with no such run, the prompt has no pictures.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from g6.core import outside
from g6.media._types import BACKEND_FAILED, NO_OUTPUT, PENDING, MediaError

#: How long one request may take, and the most one reply may carry (a picture is the largest).
REQUEST_TIMEOUT = 60.0
MAX_REPLY_BYTES = 64 * 1024 * 1024
HISTORY_ITEMS = 200
#: Seconds between two looks at a running prompt's history.
POLL_INTERVAL = 1.0


class Comfy:
    """One conversation with the declared ComfyUI service. ``environ`` replaces the environment the service's address
    is read from (the operator's ``G6_COMFYUI_URL``); ``clock`` and ``sleep`` pace the polling."""

    def __init__(self, program: outside.OutsideProgram, *, environ: dict[str, str] | None = None,
                 clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None) -> None:
        self.program, self.environ = program, environ
        self._clock, self._sleep = clock or time.monotonic, sleep or time.sleep
        self._object_info: dict[str, Any] | None = None

    def _call(self, method: str, path: str, *, body: bytes | None = None,
              headers: dict[str, str] | None = None) -> outside.ServiceReply:
        try:
            return outside.call_service(self.program, method, path, body=body, headers=headers, environ=self.environ,
                                        timeout=REQUEST_TIMEOUT, max_bytes=MAX_REPLY_BYTES)
        except outside.ServiceError as exc:
            raise MediaError(exc.code, exc.message) from None

    def get_json(self, path: str) -> Any:
        reply = self._call("GET", path)
        try:
            return reply.json()
        except ValueError:
            raise MediaError(BACKEND_FAILED, f"ComfyUI answered GET {path.split('?')[0]} with something that is not "
                                             "JSON") from None

    def post_json(self, path: str, body: dict[str, Any]) -> Any:
        reply = self._call("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            return reply.json()
        except ValueError:
            raise MediaError(BACKEND_FAILED, f"ComfyUI answered POST {path} with something that is not JSON") from None

    def version(self) -> str:
        """ComfyUI's own version, from ``/system_stats``; ``""`` when it does not say."""
        stats = self.get_json("/system_stats")
        system = stats.get("system") if isinstance(stats, dict) else None
        return str((system or {}).get("comfyui_version") or "") if isinstance(system, dict) else ""

    def object_info(self) -> dict[str, Any]:
        """Every node class the server has, with its inputs and outputs (read once per conversation)."""
        if self._object_info is None:
            found = self.get_json("/object_info")
            if not isinstance(found, dict):
                raise MediaError(BACKEND_FAILED, "ComfyUI's /object_info is not a JSON object")
            self._object_info = found
        return self._object_info

    def upload(self, data: bytes, filename: str) -> str:
        """Put a source picture into ComfyUI's temporary folder; the name a ``LoadImage`` node reads it by."""
        boundary = f"g6-{uuid.uuid4().hex}"
        fields = [(b'name="type"', b"temp"), (b'name="overwrite"', b"true")]
        body = b"".join(b"--%s\r\nContent-Disposition: form-data; %s\r\n\r\n%s\r\n" % (boundary.encode(), k, v)
                        for k, v in fields)
        body += (b"--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
                 b"Content-Type: application/octet-stream\r\n\r\n" % (boundary.encode(), filename.encode()))
        body += data + b"\r\n--%s--\r\n" % boundary.encode()
        reply = self._call("POST", "/upload/image", body=body,
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            said = reply.json()
        except ValueError:
            said = None
        if not isinstance(said, dict) or not isinstance(said.get("name"), str) or not said["name"]:
            raise MediaError(BACKEND_FAILED, "ComfyUI's /upload/image did not say the name it stored the picture under")
        kind = said.get("type") if said.get("type") in ("input", "temp", "output") else "input"
        folder = str(said.get("subfolder") or "")
        return f"{folder + '/' if folder else ''}{said['name']} [{kind}]"

    def submit(self, graph: dict[str, Any]) -> str:
        """Queue ``graph``; the prompt id ComfyUI gave it."""
        try:
            said = self.post_json("/prompt", {"prompt": graph, "client_id": f"g6-{uuid.uuid4().hex[:12]}"})
        except MediaError as exc:
            if exc.code == outside.SERVICE_STATUS:
                raise MediaError(BACKEND_FAILED, f"ComfyUI refused the workflow ({exc.reason}); G6 checked its node "
                                                 "classes, links and required inputs, so look at the input values "
                                                 "(ComfyUI's own log names the node)") from None
            raise
        prompt_id = said.get("prompt_id") if isinstance(said, dict) else None
        errors = said.get("node_errors") if isinstance(said, dict) else None
        if errors:
            raise MediaError(BACKEND_FAILED, f"ComfyUI refused nodes of the workflow: {json.dumps(errors)[:400]}")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise MediaError(BACKEND_FAILED, "ComfyUI accepted the workflow but gave no prompt id")
        return prompt_id

    def entry(self, prompt_id: str) -> dict[str, Any]:
        """The history entry of ``prompt_id`` (``{}`` while it is queued or running)."""
        found = self.get_json(f"/history/{quote(prompt_id, safe='')}")
        entry = found.get(prompt_id) if isinstance(found, dict) else None
        return entry if isinstance(entry, dict) else {}

    def finished(self, prompt_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        """``entry`` when it holds outputs, the cached run's entry when it is a fully cached prompt, ``None`` while it
        is still running; :class:`MediaError` when it failed or finished with no pictures."""
        if entry.get("outputs"):
            return entry
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        if status.get("status_str") == "error":
            said = [m[1] for m in status.get("messages") or [] if isinstance(m, (list, tuple)) and len(m) > 1
                    and m[0] == "execution_error" and isinstance(m[1], dict)]
            detail = (f": {said[0].get('node_type', '')} said {str(said[0].get('exception_message', ''))[:300]}"
                      if said else "")
            raise MediaError(BACKEND_FAILED, f"ComfyUI reported an error running prompt {prompt_id}{detail}")
        if status.get("completed") or status.get("status_str") == "success":
            served = self.cached(entry, prompt_id)
            if served is None:
                raise MediaError(NO_OUTPUT, f"prompt {prompt_id} finished with no pictures: every node came from "
                                            "ComfyUI's cache and no earlier run of the same workflow is left in its "
                                            "history to read them from; change the seed and ask again")
            return served
        return None

    def cached(self, entry: dict[str, Any], prompt_id: str) -> dict[str, Any] | None:
        """The outputs of the most recent earlier run of exactly the same graph, marked with its prompt id."""
        prompt = entry.get("prompt")
        graph = prompt[2] if isinstance(prompt, (list, tuple)) and len(prompt) > 2 else None
        if not isinstance(graph, dict) or not graph:
            return None
        history = self.get_json(f"/history?{urlencode({'max_items': HISTORY_ITEMS})}")
        if not isinstance(history, dict):
            return None
        for other_id, other in reversed(list(history.items())):
            if other_id == prompt_id or not isinstance(other, dict) or not other.get("outputs"):
                continue
            theirs = other.get("prompt")
            if isinstance(theirs, (list, tuple)) and len(theirs) > 2 and theirs[2] == graph:
                return {**other, "cached_from": str(other_id)}
        return None

    def wait(self, prompt_id: str, timeout: float, interval: float | None = None) -> dict[str, Any]:
        """The finished entry of ``prompt_id``, polling every ``interval`` seconds (:data:`POLL_INTERVAL`) until
        ``timeout`` seconds have passed (:class:`MediaError` ``G6_E_MEDIA_PENDING`` after that, with the id to ask again
        with)."""
        interval = POLL_INTERVAL if interval is None else interval
        deadline = self._clock() + timeout
        while True:
            done = self.finished(prompt_id, self.entry(prompt_id))
            if done is not None:
                return done
            if self._clock() >= deadline:
                raise MediaError(PENDING, f"prompt {prompt_id} is still queued or running after {timeout:g} seconds; "
                                          "ask get_image with this prompt_id later")
            self._sleep(interval)

    def view(self, picture: dict[str, Any]) -> bytes:
        """The bytes of one picture a history entry lists."""
        query = urlencode({"filename": str(picture.get("filename", "")), "subfolder": str(picture.get("subfolder", "")),
                           "type": str(picture.get("type", "output"))})
        return self._call("GET", f"/view?{query}").body


def pictures(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Every picture a finished entry's outputs list, in node order."""
    found = []
    outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
    for node_id in sorted(outputs, key=lambda k: (len(str(k)), str(k))):
        node = outputs[node_id]
        for picture in (node.get("images") or []) if isinstance(node, dict) else []:
            if isinstance(picture, dict) and picture.get("filename"):
                found.append(picture)
    return found


__all__ = ["HISTORY_ITEMS", "MAX_REPLY_BYTES", "POLL_INTERVAL", "REQUEST_TIMEOUT", "Comfy", "pictures"]
