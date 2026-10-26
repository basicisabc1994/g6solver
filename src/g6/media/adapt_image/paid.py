# SPDX-License-Identifier: Apache-2.0
"""Paid image services as media family backends: Stability AI, Replicate, and any OpenAI-compatible images endpoint.

None is registered by default. Each backend declares the key it needs (:data:`KEYS`, listed by ``g6_describe``): the
person puts that key in their key file, ``~/.g6/.env`` (``g6.core.keys``), and registers the backend built from it::

    from g6.media._types import image_backends
    from g6.media.adapt_image.paid import StabilityBackend
    image_backends.register(StabilityBackend.from_key_file(model="stable-diffusion-xl-1024-v1-0"))

``from_key_file`` raises ``KeyMissing`` (the file and the line to add) when the key is not there; nothing here reads a
key from anywhere else. A :class:`PaidService` built in the person's own Python with a key in hand still works.

Each is ``paid`` and ``network``, so ``adapt_image`` sends work to it only from ``generate_paid``, a sensitive op, and
only when ``g6.action_gating`` lets that payment through: the gate reads the backend's key in the key file as the
person's approval to spend it (``allow_with_notice``, ``G6_E_KEY_FILE_APPROVED``). (From Python, ``image_backends.generate`` on one sends at
once: that is the person's own code with their own key; an agent reaches a paid backend only through
``generate_paid``.) Every request goes through ``g6.core.http`` (each hop checked, no private address, redirects
re-checked, a 4xx or 5xx an error, replies capped); the key is sent only to the service's own address, never to the
address of a picture it points to. The picture that comes back is written into the media folder and inspected like any
other: the service's "success" is not a check.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from g6.core import http, keys
from g6.media._types import (
    BACKEND_FAILED,
    INPUT_INVALID,
    NO_OUTPUT,
    ImageRequest,
    MediaError,
    MediaResult,
    Provenance,
    refuse,
)
from g6.media.adapt_image.keep import digest, keep

#: How long a paid service may take to make one picture (Replicate is polled within it).
DEFAULT_TIMEOUT = 120.0
MAX_REPLY_BYTES = 50 * 1024 * 1024
_REPLICATE_MODEL = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.+-]+(?::[a-f0-9]{8,128})?$")

#: The key each paid backend needs, which the person puts in their key file; putting it there approves spending it.
STABILITY_KEY = keys.Key("STABILITY_API_KEY", "Stability AI pictures (adapt_image's stability backend)",
                         where="https://platform.stability.ai/account/keys", paid=True)
REPLICATE_KEY = keys.Key("REPLICATE_API_TOKEN", "Replicate image models (adapt_image's replicate backend)",
                         where="https://replicate.com/account/api-tokens", paid=True)
IMAGES_KEY = keys.Key("G6_IMAGES_API_KEY", "the OpenAI-compatible images endpoint you name (adapt_image's images_api "
                      "backend)", where="the service whose address you give as base_url", paid=True)
KEYS: tuple[keys.Key, ...] = (STABILITY_KEY, REPLICATE_KEY, IMAGES_KEY)


class PaidService(BaseModel):
    """What the person running G6 configures for one paid service: their key, the service's address (``https`` only;
    each backend has its own default), the model, and limits. The key never appears in a result or a log."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SecretStr = Field(min_length=1)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field("", max_length=300)
    timeout: float = Field(DEFAULT_TIMEOUT, gt=0, le=3600, allow_inf_nan=False)
    max_bytes: int = Field(MAX_REPLY_BYTES, gt=0, le=512 * 1024 * 1024)

    @field_validator("base_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if value and (urlsplit(value).scheme != "https" or not urlsplit(value).hostname):
            raise ValueError("a paid service's address is an https URL")
        return value.rstrip("/")


def _decode(text: Any, said: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise MediaError(NO_OUTPUT, f"{said} sent no picture")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError(BACKEND_FAILED, f"{said} sent a picture that is not valid base64: {exc}") from None


def _json(reply: http.Response, said: str) -> Any:
    try:
        return json.loads(reply.body)
    except ValueError:
        raise MediaError(BACKEND_FAILED, f"{said} answered with something that is not JSON") from None


class _Paid:
    """What the three backends share: the configuration, the checked HTTP client, and keeping the picture."""

    local, network, paid = False, True, True
    service_name = ""
    default_name = ""
    default_url = ""
    options_allowed: frozenset[str] = frozenset()
    key: keys.Key

    def __init__(self, service: PaidService, *, name: str = "", client: http.Client | None = None) -> None:
        self.service = service
        self.name = name or type(self).default_name
        self._client = client

    @classmethod
    def from_key_file(cls, *, model: str, base_url: str = "", timeout: float = DEFAULT_TIMEOUT,
                      max_bytes: int = MAX_REPLY_BYTES, **backend: Any) -> _Paid:
        """This backend with its key from the person's key file; ``KeyMissing`` (the file and the line to add) when the
        key is not there. ``backend`` passes on to the constructor (``name``, ``client``)."""
        service = PaidService(key=SecretStr(keys.require(cls.key)), model=model, base_url=base_url, timeout=timeout,
                              max_bytes=max_bytes)
        return cls(service, **backend)

    @property
    def base_url(self) -> str:
        return self.service.base_url or self.default_url

    def client(self) -> http.Client:
        return self._client or http.Client(timeout=self.service.timeout, max_bytes=self.service.max_bytes,
                                           min_interval=0.0)

    def available(self) -> str:
        return "" if self.base_url else f"no address configured for {self.service_name}: give PaidService(base_url=...)"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.service.key.get_secret_value()}", "Accept": "application/json",
                "Content-Type": "application/json"}

    def _post(self, path: str, body: dict[str, Any], extra: dict[str, str] | None = None) -> Any:
        reply = self.client().request("POST", f"{self.base_url}{path}", headers={**self._headers(), **(extra or {})},
                                      body=json.dumps(body).encode())
        return _json(reply, self.service_name)

    def _fetch(self, url: str) -> bytes:
        """A picture the service points to, fetched without its key (another address may hold it)."""
        return self.client().get(url, headers={"Accept": "image/*"}).body

    def make(self, request: ImageRequest) -> tuple[bytes, dict[str, Any], int | None]:  # pragma: no cover - abstract
        raise NotImplementedError

    def generate(self, request: ImageRequest) -> MediaResult:
        ids = {"request_id": request.request_id, "run_id": request.run_id}
        if not request.prompt:
            return refuse("generate", INPUT_INVALID, f"{self.service_name} draws from a prompt; give one", **ids)
        extra = sorted(set(request.options) - self.options_allowed)
        if extra:
            allowed = ", ".join(sorted(self.options_allowed)) or "none"
            return refuse("generate", INPUT_INVALID, f"{self.name} takes only these options: {allowed}; not "
                                                     f"{', '.join(extra)}", **ids)
        try:
            data, sent, seed = self.make(request)
        except http.HTTPError as exc:
            return refuse("generate", BACKEND_FAILED, f"{self.service_name}: {exc}"[:500],
                          evidence=[{"kind": "paid_request", "service": self.service_name, "error": exc.code,
                                     "status": exc.status}], **ids)
        except MediaError as exc:
            return refuse("generate", exc.code, exc.reason, **ids)
        spent = {"kind": "paid_request", "service": self.service_name, "model": self.service.model,
                 "cost": "not known to G6: the service's own account says what this cost"}
        recipe = {"service": self.service_name, "address": self.base_url, "model": self.service.model, "sent": sent}
        provenance = Provenance(backend=self.name, backend_version=f"{self.service_name} {self.service.model}"[:200],
                                technique="text to image", seed=seed, recipe=recipe, recipe_sha256=digest(recipe),
                                prompt=request.prompt)
        name = request.name or f"{self.name}-{hashlib.sha256(data).hexdigest()[:12]}"
        return keep("generate", data, name=name, fmt="png", width=request.width, height=request.height,
                    overwrite=request.overwrite, provenance=provenance, ids=ids, evidence=[spent], seed=seed,
                    message=f"a picture from {self.service_name}, inspected: it decodes, is a PNG of the size asked "
                            "for and is not blank; the service's success was not taken as a check")


class StabilityBackend(_Paid):
    """Stability AI's text-to-image endpoint (``/v1/generation/<engine>/text-to-image``); ``model`` is the engine.
    A finish reason other than ``SUCCESS`` (a content filter, an error) is a refusal, never a picture."""

    service_name, default_name, default_url = "Stability AI", "stability", "https://api.stability.ai"
    options_allowed = frozenset({"negative_prompt", "steps", "cfg_scale", "style_preset"})
    key = STABILITY_KEY

    def make(self, request: ImageRequest) -> tuple[bytes, dict[str, Any], int | None]:
        o = request.options
        texts = [{"text": request.prompt, "weight": 1.0}]
        if o.get("negative_prompt"):
            texts.append({"text": str(o["negative_prompt"]), "weight": -1.0})
        body: dict[str, Any] = {"text_prompts": texts, "width": request.width, "height": request.height, "samples": 1,
                                **{k: o[k] for k in ("steps", "cfg_scale", "style_preset") if k in o}}
        if request.seed is not None:
            body["seed"] = request.seed
        found = self._post(f"/v1/generation/{quote(self.service.model, safe='')}/text-to-image", body)
        made = (found.get("artifacts") or []) if isinstance(found, dict) else []
        if not made or not isinstance(made[0], dict):
            raise MediaError(NO_OUTPUT, "Stability AI answered with no picture")
        reason = str(made[0].get("finishReason", ""))
        if reason != "SUCCESS":
            raise MediaError(BACKEND_FAILED, f"Stability AI finished with {reason or 'no finish reason'}, not SUCCESS: "
                                             "no picture is returned")
        seed = made[0].get("seed")
        return _decode(made[0].get("base64"), "Stability AI"), body, seed if isinstance(seed, int) else request.seed


class ReplicateBackend(_Paid):
    """A Replicate model (``owner/name``, or ``owner/name:version``), run as a prediction and polled until it
    finishes within the configured time; its first output URL is fetched through ``g6.core.http``."""

    service_name, default_name, default_url = "Replicate", "replicate", "https://api.replicate.com"
    options_allowed = frozenset({"negative_prompt", "output_format", "aspect_ratio", "guidance", "num_inference_steps"})
    key = REPLICATE_KEY

    def __init__(self, service: PaidService, *, name: str = "", client: http.Client | None = None,
                 sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> None:
        if not _REPLICATE_MODEL.fullmatch(service.model):
            raise ValueError(f"{service.model!r} is not a Replicate model (owner/name or owner/name:version)")
        super().__init__(service, name=name, client=client)
        self._sleep, self._clock = sleep or time.sleep, clock or time.monotonic

    def make(self, request: ImageRequest) -> tuple[bytes, dict[str, Any], int | None]:
        fields: dict[str, Any] = {"prompt": request.prompt, "width": request.width, "height": request.height,
                                  **request.options}
        if request.seed is not None:
            fields["seed"] = request.seed
        model, _, version = self.service.model.partition(":")
        body: dict[str, Any] = {"input": fields}
        if version:
            found = self._post("/v1/predictions", {**body, "version": version}, {"Prefer": "wait=60"})
        else:
            owner, name = model.split("/", 1)
            found = self._post(f"/v1/models/{quote(owner, safe='')}/{quote(name, safe='')}/predictions", body,
                               {"Prefer": "wait=60"})
        deadline = self._clock() + self.service.timeout
        while isinstance(found, dict) and found.get("status") in ("starting", "processing"):
            if self._clock() >= deadline:
                raise MediaError(BACKEND_FAILED, f"Replicate prediction {found.get('id')} was still "
                                                 f"{found.get('status')} after {self.service.timeout:g} seconds")
            self._sleep(1.0)
            ident = str(found.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9]{1,64}", ident):
                raise MediaError(BACKEND_FAILED, "Replicate gave a prediction without a usable id")
            found = _json(self.client().get(f"{self.base_url}/v1/predictions/{ident}", headers=self._headers()),
                          "Replicate")
        if not isinstance(found, dict) or found.get("status") != "succeeded":
            status = found.get("status") if isinstance(found, dict) else "unreadable"
            error = str(found.get("error") or "")[:300] if isinstance(found, dict) else ""
            raise MediaError(BACKEND_FAILED, f"Replicate prediction ended {status}" + (f": {error}" if error else ""))
        output = found.get("output")
        first = output[0] if isinstance(output, list) and output else output
        if not isinstance(first, str) or not first.startswith("https://"):
            raise MediaError(NO_OUTPUT, "Replicate's prediction succeeded without an https picture address")
        return self._fetch(first), {**body, "prediction": str(found.get("id", ""))}, request.seed


class ImagesEndpointBackend(_Paid):
    """An OpenAI-compatible images endpoint (``<base_url>/images/generations``), which the person names in
    ``PaidService(base_url=...)``. A picture sent inline (``b64_json``) is decoded; one sent as an address is fetched
    through ``g6.core.http``."""

    service_name, default_name, default_url = "images endpoint", "images_api", ""
    options_allowed = frozenset({"quality", "style", "background", "response_format"})
    key = IMAGES_KEY

    def make(self, request: ImageRequest) -> tuple[bytes, dict[str, Any], int | None]:
        body: dict[str, Any] = {"model": self.service.model, "prompt": request.prompt, "n": 1,
                                "size": f"{request.width}x{request.height}", **request.options}
        found = self._post("/images/generations", body)
        data = (found.get("data") or []) if isinstance(found, dict) else []
        if not data or not isinstance(data[0], dict):
            raise MediaError(NO_OUTPUT, "the images endpoint answered with no picture")
        if data[0].get("b64_json"):
            return _decode(data[0]["b64_json"], "the images endpoint"), body, request.seed
        url = data[0].get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise MediaError(NO_OUTPUT, "the images endpoint answered with neither an inline picture nor an https "
                                        "address")
        return self._fetch(url), body, request.seed


__all__ = ["DEFAULT_TIMEOUT", "IMAGES_KEY", "KEYS", "MAX_REPLY_BYTES", "REPLICATE_KEY", "STABILITY_KEY",
           "ImagesEndpointBackend", "PaidService", "ReplicateBackend", "StabilityBackend"]
