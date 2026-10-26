# SPDX-License-Identifier: Apache-2.0
"""The voices adapt_voice speaks with and the service it transcribes with, registered by name.

* ``espeak``: eSpeak NG, a program the person installs (``__g6_outside__``; GPL, so run over its command line only and
  never bundled), local, free. The text goes to it on its input, never on its command line; its voice is a checked
  code (``en-gb``, ``fr``, ``de+f3``); it writes a WAV.
* ``openai``: OpenAI's speech endpoint (``/v1/audio/speech``, raw 24 kHz PCM) and its transcription endpoint
  (``/v1/audio/transcriptions``, ``whisper-1`` with segments); paid and online.
* ``elevenlabs``: ElevenLabs' text to speech (raw 22.05 kHz PCM); paid and online.

A paid engine's key is declared as a ``g6.core.keys.Key`` and read only from the person's key file, at the moment a
request is sent and only after ``g6.action_gating`` let it through; it goes only to that service's own address, through
``g6.core.http`` (every hop checked, a redirect re-checked, a 4xx or 5xx an error). An engine from outside the package
joins with :func:`register`.
"""
from __future__ import annotations

import json
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from g6.core import http, keys, outside
from g6.media import _programs, _wav
from g6.media._types import BACKEND_FAILED, INPUT_INVALID, NO_OUTPUT, MediaError

ESPEAK = outside.OutsideProgram(
    name="eSpeak NG", purpose="adapt_voice's local voice: it turns text into a WAV on your machine",
    install="Install eSpeak NG (github.com/espeak-ng/espeak-ng: the Windows installer, brew install espeak-ng or apt "
            "install espeak-ng) and put espeak-ng on PATH, or set G6_ESPEAK to the program",
    licence="GPL-3.0-or-later (G6 runs it over its command line and never bundles it)", command="espeak-ng",
    env="G6_ESPEAK", version_args=("--version",))
OPENAI_KEY = keys.Key("OPENAI_API_KEY", "OpenAI's voices and transcription (adapt_voice's openai engine)",
                      where="https://platform.openai.com/api-keys", paid=True)
ELEVENLABS_KEY = keys.Key("ELEVENLABS_API_KEY", "ElevenLabs' voices (adapt_voice's elevenlabs engine)",
                          where="https://elevenlabs.io/app/settings/api-keys", paid=True)
KEYS: tuple[keys.Key, ...] = (OPENAI_KEY, ELEVENLABS_KEY)
ESPEAK_VOICE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{1,8}){0,2}(\+[a-z0-9]{1,12})?$")
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")
ELEVENLABS_VOICE = re.compile(r"^[A-Za-z0-9]{8,40}$")
MAX_UPLOAD = 25 * 1024 * 1024


@runtime_checkable
class Engine(Protocol):
    """A voice: what it is (local, network, paid, the key it needs) and :meth:`speak`, which returns WAV bytes."""

    name: str
    local: bool
    network: bool
    paid: bool
    key: keys.Key | None

    def speak(self, text: str, voice: str, d: Any) -> tuple[bytes, dict[str, Any]]: ...


class Espeak:
    name, local, network, paid, key = "espeak", True, False, False, None

    def speak(self, text: str, voice: str, d: Any) -> tuple[bytes, dict[str, Any]]:
        if voice and not ESPEAK_VOICE.fullmatch(voice):
            raise MediaError(INPUT_INVALID, f"{voice!r} is not an eSpeak NG voice code (such as en-gb, fr or de+f3)")
        found = _programs.find(ESPEAK)
        with tempfile.TemporaryDirectory(prefix="g6-voice-", ignore_cleanup_errors=True) as scratch:
            out = Path(scratch) / "voice.wav"
            args = ["-b", "1", "-v", voice or "en", "-s", str(d.wpm), "-w", str(out), "--stdin"]
            ran = _programs.run(found.where, args, seconds=d.timeout, stdin=text.encode("utf-8"), name="espeak-ng")
            if ran.code != 0:
                raise MediaError(BACKEND_FAILED, f"eSpeak NG failed (exit {ran.code}): {ran.text(300)}")
            if not out.is_file():
                raise MediaError(NO_OUTPUT, "eSpeak NG exited 0 and wrote no file")
            data = out.read_bytes()
        return data, {"engine": self.name, "program": found.version, "voice": voice or "en", "wpm": d.wpm,
                      "argv": ["espeak-ng", *args[:-3], "-w", "{out}", "--stdin"]}

    def voices(self, seconds: float) -> list[dict[str, str]]:
        found = _programs.find(ESPEAK)
        ran = _programs.run(found.where, ["--voices"], seconds=seconds, name="espeak-ng")
        if ran.code != 0:
            raise MediaError(BACKEND_FAILED, f"eSpeak NG could not list its voices: {ran.text(300)}")
        out = []
        for line in ran.out.decode("utf-8", "replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and ESPEAK_VOICE.fullmatch(parts[1].lower()):
                out.append({"code": parts[1].lower(), "name": parts[3].replace("_", " ")})
        return out


class _Paid:
    local, network, paid = False, True, True
    service = ""
    address = ""
    key: keys.Key

    def __init__(self) -> None:
        self.client: http.Client | None = None

    def _client(self, timeout: float) -> http.Client:
        return self.client or http.Client(timeout=timeout, max_bytes=64 * 1024 * 1024, min_interval=0.0)

    def _sound(self, reply: http.Response, rate: int) -> bytes:
        kind = reply.headers.get("content-type", "")
        if "json" in kind or reply.body[:1] in (b"{", b"["):
            said = reply.body[:300].decode("utf-8", "replace")
            raise MediaError(BACKEND_FAILED, f"{self.service} answered with a message, not sound: {said}")
        if not reply.body:
            raise MediaError(NO_OUTPUT, f"{self.service} answered with no sound")
        return _wav.pcm_wav(reply.body, rate=rate)


class OpenAIVoice(_Paid):
    name, service, address, key = "openai", "OpenAI", "https://api.openai.com", OPENAI_KEY

    def speak(self, text: str, voice: str, d: Any) -> tuple[bytes, dict[str, Any]]:
        voice = voice or "alloy"
        if voice not in OPENAI_VOICES:
            raise MediaError(INPUT_INVALID, f"{voice!r} is not one of OpenAI's voices ({', '.join(OPENAI_VOICES)})")
        body = {"model": d.model or "gpt-4o-mini-tts", "input": text, "voice": voice, "response_format": "pcm"}
        reply = self._client(d.timeout).request(
            "POST", f"{self.address}/v1/audio/speech", body=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {keys.require(self.key)}", "Content-Type": "application/json"})
        return self._sound(reply, 24000), {"engine": self.name, "model": body["model"], "voice": voice}

    def transcribe(self, data: bytes, d: Any) -> dict[str, Any]:
        if len(data) > MAX_UPLOAD:
            raise MediaError(INPUT_INVALID, f"the clip is {len(data)} bytes; OpenAI takes at most {MAX_UPLOAD}")
        boundary = f"g6-{secrets.token_hex(12)}"
        fields = {"model": d.model or "whisper-1", "response_format": "verbose_json"}
        if d.language:
            fields["language"] = d.language
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in fields.items()]
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\n"
                     f"Content-Type: audio/wav\r\n\r\n".encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        reply = self._client(d.timeout).request(
            "POST", f"{self.address}/v1/audio/transcriptions", body=body,
            headers={"Authorization": f"Bearer {keys.require(self.key)}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            found = json.loads(reply.body)
        except ValueError:
            raise MediaError(BACKEND_FAILED, "OpenAI's transcription is not JSON") from None
        if not isinstance(found, dict) or not isinstance(found.get("text"), str):
            raise MediaError(BACKEND_FAILED, "OpenAI's transcription has no text")
        return {**found, "model": fields["model"]}


class ElevenLabsVoice(_Paid):
    name, service, address, key = "elevenlabs", "ElevenLabs", "https://api.elevenlabs.io", ELEVENLABS_KEY

    def speak(self, text: str, voice: str, d: Any) -> tuple[bytes, dict[str, Any]]:
        if not ELEVENLABS_VOICE.fullmatch(voice or ""):
            raise MediaError(INPUT_INVALID, "elevenlabs needs voice: the id of a voice in your ElevenLabs account")
        body = {"text": text, "model_id": d.model or "eleven_multilingual_v2"}
        reply = self._client(d.timeout).request(
            "POST", f"{self.address}/v1/text-to-speech/{voice}?output_format=pcm_22050", body=json.dumps(body).encode(),
            headers={"xi-api-key": keys.require(self.key), "Content-Type": "application/json"})
        return self._sound(reply, 22050), {"engine": self.name, "model": body["model_id"], "voice": voice}


ENGINES: dict[str, Any] = {"espeak": Espeak(), "openai": OpenAIVoice(), "elevenlabs": ElevenLabsVoice()}


def register(engine: Engine, *, replace: bool = False) -> None:
    """Add a voice from outside the package (local ones answer ``speak``, paid ones ``speak_paid``)."""
    if not isinstance(engine, Engine) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(engine.name)):
        raise TypeError(f"{engine!r} is not a voice engine (name, local, network, paid, key, speak)")
    if engine.name in ENGINES and not replace:
        raise ValueError(f"a voice engine named {engine.name!r} is already registered")
    ENGINES[engine.name] = engine


def unregister(name: str) -> None:
    if name not in ("espeak", "openai", "elevenlabs"):
        ENGINES.pop(name, None)


def describe() -> list[dict[str, Any]]:
    """Each engine, without asking any of them: what it is, and whether the person's key file holds its key."""
    return [{"name": e.name, "local": e.local, "network": e.network, "paid": e.paid,
             "key": e.key.name if e.key else None, "key_in_file": keys.present(e.key.name) if e.key else None,
             "transcribes": hasattr(e, "transcribe")} for e in ENGINES.values()]


__all__ = ["ELEVENLABS_KEY", "ENGINES", "ESPEAK", "ESPEAK_VOICE", "KEYS", "OPENAI_KEY", "OPENAI_VOICES", "Engine",
           "ElevenLabsVoice", "Espeak", "OpenAIVoice", "describe", "register", "unregister"]
