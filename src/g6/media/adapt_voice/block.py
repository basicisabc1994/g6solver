# SPDX-License-Identifier: Apache-2.0
"""AdaptVoiceBlock, the registry and MCP door (``g6_invoke``) to adapt_voice.

``speak`` uses a local voice (eSpeak NG); ``speak_paid`` and ``transcribe`` use a paid service, each request decided
first by ``g6.action_gating`` (a key in the person's key file is their approval; ``allow_paid`` is the caller's word,
which can only hold a request back); ``voices`` asks eSpeak NG what it speaks; ``engines`` lists them all without asking
any. Every voice is written into the media folder and inspected from disk: it decodes, is not silent, and lasts a
plausible time for its text. A clip is transcribed only when it holds speech G6 can measure, and a transcript is refused
only when it is empty or at an impossible speaking rate for that speech; a wrong one of a plausible length passes.
Every op but ``engines`` runs a program or reaches a paid service, so it is
sensitive. Results are ``qualified-draft`` at best.
"""
from __future__ import annotations

import operator
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core import http, keys
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media import _programs, _wav
from g6.media._types import (
    BACKEND_FAILED,
    BACKEND_UNAVAILABLE,
    DESCRIBED,
    DURATION_MISMATCH,
    FORMAT_MISMATCH,
    INPUT_INVALID,
    PAID_NOT_ALLOWED,
    PROBED,
    REPORTED,
    SILENT,
    TRANSCRIBED,
    TRANSCRIPT_DOUBTFUL,
    Check,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    media_root,
    media_source,
    place,
    refuse,
)
from g6.media.adapt_voice import engines, gate

_SAYS = " (sensitive: it {}; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS = {
    "speak": "speak(text, engine=espeak, voice, wpm): text spoken by a local voice into a WAV, inspected"
             + _SAYS.format("runs eSpeak NG on your machine"),
    "speak_paid": "speak_paid(text, engine=openai|elevenlabs, voice, model, allow_paid): text spoken by a paid voice, "
                  "sent only on the action gate's allow" + _SAYS.format("spends money and reaches the network"),
    "transcribe": "transcribe(source, engine=openai, language, allow_paid): a WAV with speech in it transcribed by a "
                  "paid service, sent only on the action gate's allow" + _SAYS.format("spends money"),
    "voices": "voices(): the voices eSpeak NG speaks" + _SAYS.format("runs eSpeak NG"),
    "engines": "engines(): the voices and the transcription service, what each is and whether its key is in the "
               "person's key file, without asking any of them",
}
SENSITIVE_OPS: tuple[str, ...] = ("speak", "speak_paid", "transcribe", "voices")
#: Engines that are not part of G6, and why.
NOT_HERE = {"coqui": "Coqui TTS is not part of G6 (its models are under a non-commercial licence)",
            "xtts": "Coqui TTS is not part of G6 (its models are under a non-commercial licence)",
            "tts": "Coqui TTS is not part of G6 (its models are under a non-commercial licence)",
            "pyttsx3": "pyttsx3 is not part of G6 (on Linux it loads the GPL eSpeak library into G6's own process)",
            "edge_tts": "edge-tts is not part of G6 (an online service of Microsoft's, LGPL)"}
#: Words a second of speech can hold, at most; a transcript with more is refused.
MAX_WORDS_PER_SECOND = 6.0
#: Seconds of measured speech that must have words: an empty transcript of this much or more is refused.
MIN_SPEECH_WITH_WORDS = 1.0
#: A frame of 20 ms counts as speech when its RMS is above this.
SPEECH_DBFS = -45.0


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class VoiceInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["speak", "speak_paid", "transcribe", "voices", "engines", "ops", "help"] = "help"
    text: str = Field("", max_length=5000)
    engine: str = Field("", pattern=r"^([a-z][a-z0-9_]{0,31})?$")
    voice: str = Field("", pattern=r"^[A-Za-z0-9+-]{0,40}$")
    wpm: int = Field(160, ge=80, le=450)
    model: str = Field("", pattern=r"^[A-Za-z0-9._-]{0,64}$")
    language: str = Field("", pattern=r"^([a-z]{2,3})?$")
    source: str = Field("", max_length=100)
    allow_paid: bool = False
    timeout: float = Field(60.0, ge=1, le=_programs.MAX_SECONDS, allow_inf_nan=False)
    name: str = Field("", max_length=90)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name", "source")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @field_validator("engine", mode="before")
    @classmethod
    def _not_here(cls, value: Any) -> Any:
        key = str(value).replace("-", "_").lower()
        if key in NOT_HERE:
            raise ValueError(NOT_HERE[key])
        return value

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> VoiceInput:
        if self.op in ("speak", "speak_paid") and not self.text.strip():
            raise ValueError(f"{self.op} needs text")
        if self.op == "transcribe" and not self.source:
            raise ValueError("transcribe needs source: a WAV in the media folder")
        if self.op == "speak" and self.engine in ("", "espeak") and self.voice and not engines.ESPEAK_VOICE.fullmatch(
                self.voice):
            raise ValueError(f"{self.voice!r} is not an eSpeak NG voice code (such as en-gb, fr or de+f3)")
        return self


def _ids(d: VoiceInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _fits(seconds: float, chars: int) -> Check:
    low, high = max(0.2, 0.025 * chars), 0.5 * chars + 3.0
    if seconds < low:
        return Check(name="length_fits_text", passed=False, code=DURATION_MISMATCH,
                     detail=f"{seconds:.3f} s is too short for {chars} characters (at least {low:.2f} s)")
    if seconds > high:
        return Check(name="length_fits_text", passed=False, code=DURATION_MISMATCH,
                     detail=f"{seconds:.3f} s is too long for {chars} characters (at most {high:.1f} s)")
    return Check(name="length_fits_text", passed=True, detail=f"{seconds:.3f} s for {chars} characters")


def _keep(d: VoiceInput, data: bytes, info: dict[str, Any], evidence: list[dict[str, Any]]) -> MediaResult:
    """The voice's WAV in the media folder, inspected from disk and its length set against its text, or a refusal."""
    ids, chars = _ids(d), len(d.text.strip())
    temp = media_root() / f".partial-{secrets.token_hex(8)}.wav"
    try:
        final = media_file(d.name or f"voice-{secrets.token_hex(6)}", ".wav")
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        found = _wav.inspect_wav(temp)
        if found.passed:
            found = found.model_copy(update={"checks": (*found.checks, _fits(found.measured["duration_s"], chars))})
        if not found.passed:
            discard(temp)
            return refuse(d.op, found.failure.code, found.failure.detail,
                          evidence=[found.evidence(), *evidence, {"kind": "file", "removed": True}], **ids)
        written = place(temp, final, overwrite=d.overwrite)
    except MediaError as exc:
        discard(temp)
        return refuse(d.op, exc.code, exc.reason, evidence=evidence, **ids)
    recipe = {"op": d.op, **{k: v for k, v in info.items() if k != "program"}, "characters": chars}
    provenance = Provenance(backend=info["engine"], backend_version=str(info.get("program") or info.get("model") or "")
                            [:200], technique="text to speech", recipe=recipe, prompt=d.text[:4000])
    artifact = media_artifact(final, found, provenance, {"characters": chars})
    said = (f"{chars} characters spoken by {info['engine']} and inspected: a WAV of "
            f"{found.measured['duration_s']:.2f} s that decodes, is not silent and fits its text")
    return answer(d.op, said, artifact=artifact, card=PROBED, evidence=evidence, written=written, engine=info["engine"],
                  **ids)


def _speak(d: VoiceInput) -> MediaResult:
    engine = engines.ENGINES.get(d.engine or "espeak")
    if engine is None:
        return refuse(d.op, BACKEND_UNAVAILABLE, f"no voice engine named {d.engine!r} (registered: "
                                                 f"{', '.join(engines.ENGINES)})", **_ids(d))
    if engine.paid or engine.network:
        return refuse(d.op, PAID_NOT_ALLOWED, f"{engine.name} is a paid or online voice, which speak never uses; "
                                              "speak_paid asks the action gate first", **_ids(d))
    data, info = engine.speak(d.text, d.voice, d)
    return _keep(d, data, info, [{"kind": "local_voice", **info}])


def _paid(d: VoiceInput, what: str, would: dict[str, Any], checks: str) -> tuple[Any, list[dict[str, Any]],
                                                                                    MediaResult | None]:
    """The paid engine, the evidence so far, and a refusal when the gate or the caller holds the request."""
    name = d.engine or "openai"
    engine = engines.ENGINES.get(name)
    if engine is None or not engine.paid:
        return None, [], refuse(d.op, INPUT_INVALID if engine else BACKEND_UNAVAILABLE,
                                f"{name} is not a paid engine; {d.op} uses openai or elevenlabs", **_ids(d))
    if d.op == "transcribe" and not hasattr(engine, "transcribe"):
        return None, [], refuse(d.op, INPUT_INVALID, f"{name} does not transcribe; use openai", **_ids(d))
    dry = {"kind": "dry_run", "engine": name, "would_send": would}
    decided = gate.ask(gate.payment(engine, what, would, checks), engine.key.name)
    claim = {"kind": "claim", "about": "the person agreed to pay", "allow_paid": d.allow_paid, "by": "the caller",
             "counts_as_check": False, "note": "a caller's word: it can hold a request back, never send one"}
    evidence = [dry, decided, claim]
    if not gate.lets_through(decided) or not d.allow_paid:
        codes = ", ".join(decided["reason_codes"]) or "no reason code"
        why = (f"the action gate (g6.action_gating) decided {decided['decision']} ({codes}): "
               f"{decided['required_user_message']}" if not gate.lets_through(decided) else "allow_paid was not given")
        return engine, evidence, refuse(d.op, PAID_NOT_ALLOWED, f"nothing was sent to {engine.service}, which spends "
                                                                f"the person's money: {why}", evidence=evidence,
                                        **_ids(d))
    return engine, evidence, None


def _speak_paid(d: VoiceInput) -> MediaResult:
    would = {"characters": len(d.text), "voice": d.voice, "model": d.model}
    engine, evidence, held = _paid(d, "speech", would, "a WAV that G6's sound inspector decodes, finds not silent "
                                                       "and finds a plausible length for the text")
    if held is not None:
        return held
    try:
        data, info = engine.speak(d.text, d.voice, d)
    except http.HTTPError as exc:
        return refuse(d.op, BACKEND_FAILED, f"{engine.service}: {exc}"[:500], evidence=evidence, **_ids(d))
    except keys.KeyMissing as exc:
        return refuse(d.op, PAID_NOT_ALLOWED, exc.message, evidence=evidence, **_ids(d))
    spent = {"kind": "paid_request", "service": engine.service, "model": info.get("model"),
             "cost": "not known to G6: the service's own account says what this cost"}
    return _keep(d, data, info, [*evidence, spent])


def _speech_seconds(path: Path, info: _wav.WavInfo) -> float:
    """How much of the clip is speech-loud: 20 ms frames whose RMS is above :data:`SPEECH_DBFS`."""
    frame = max(1, info.rate // 50) * info.channels
    floor = (10 ** (SPEECH_DBFS / 20)) ** 2
    loud = 0
    for values, scale in _wav.blocks(path, info):
        for start in range(0, len(values) - frame + 1, frame):
            part = values[start:start + frame]
            if sum(map(operator.mul, part, part)) / (frame * scale * scale) > floor:
                loud += 1
    return loud * 0.02


def _transcribe(d: VoiceInput) -> MediaResult:
    ids = _ids(d)
    path = media_source(d.source)
    try:
        info = _wav.read_info(path)
    except MediaError as exc:
        hint = "; transcribe sends a WAV: turn it into one first (adapt_audio transcode)" if (
            exc.code == FORMAT_MISMATCH) else ""
        return refuse(d.op, exc.code, exc.reason + hint, **ids)
    heard = _wav.inspect_wav(path, min_duration=0.1)
    if not heard.passed:
        return refuse(d.op, heard.failure.code, f"{heard.failure.detail}: nothing was sent, so nothing was paid for",
                      evidence=[heard.evidence()], **ids)
    speech = round(_speech_seconds(path, info), 2)
    if speech < 0.2:
        return refuse(d.op, SILENT, f"the clip holds no speech G6 can measure (frames above {SPEECH_DBFS:g} dBFS: "
                                    f"{speech} s): nothing was sent", evidence=[heard.evidence()], **ids)
    would = {"source": d.source, "seconds": round(info.duration, 3), "model": d.model, "language": d.language}
    engine, evidence, held = _paid(d, "transcription", would, "a transcript whose words fit the clip's measured "
                                                              "speech (at most six words a second, and some words "
                                                              "for a second or more of it)")
    if held is not None:
        return held
    evidence = [heard.evidence(), *evidence]
    try:
        found = engine.transcribe(path.read_bytes(), d)
    except http.HTTPError as exc:
        return refuse(d.op, BACKEND_FAILED, f"{engine.service}: {exc}"[:500], evidence=evidence, **ids)
    except keys.KeyMissing as exc:
        return refuse(d.op, PAID_NOT_ALLOWED, exc.message, evidence=evidence, **ids)
    text = found["text"].strip()
    words = len(text.split())
    rate = words / max(speech, 0.5)
    segments = [{k: s.get(k) for k in ("start", "end", "text", "avg_logprob", "no_speech_prob")}
                for s in found.get("segments") or [] if isinstance(s, dict)]
    record = {"kind": "paid_request", "service": engine.service, "model": found.get("model"),
              "cost": "not known to G6: the service's own account says what this cost", "words": words,
              "speech_s": speech, "words_per_second": round(rate, 2)}
    if not words and speech >= MIN_SPEECH_WITH_WORDS:
        return refuse(d.op, TRANSCRIPT_DOUBTFUL, f"{engine.service} gave no words for {speech} s of measured speech: "
                                                 "an empty transcript of a clip that holds speech is not what was said",
                      evidence=[*evidence, record], text=text, **ids)
    if rate > MAX_WORDS_PER_SECOND:
        return refuse(d.op, TRANSCRIPT_DOUBTFUL, f"{words} words for {speech} s of measured speech is {rate:.1f} a "
                                                 f"second, more than the {MAX_WORDS_PER_SECOND:g} speech can hold",
                      evidence=[*evidence, record], text=text, **ids)
    return answer(d.op, f"{engine.service} transcribed {info.duration:.2f} s ({speech} s of it speech) as {words} words",
                  card=TRANSCRIBED, evidence=[*evidence, record], text=text, language=found.get("language"),
                  segments=segments, duration_s=round(info.duration, 3), speech_s=speech, words=words, **ids)


@block_contract(
    purpose="speak text with a local voice or, on the action gate's allow, a paid one, and transcribe speech with a "
            "paid service, each voice inspected and each transcript's word count set against the clip's measured "
            "speech before it is returned (not whether its words are what was said)",
    mitigates_failure_modes=("audio_check_failed", "wrong_transcript", "unchosen_paid_service"),
    verification_method="g6.media sound inspector on every voice written (decodes, not silent, a plausible length for "
                        "its text); a clip's speech measured before it is sent and the transcript's words set against "
                        "it; every paid request decided by g6.action_gating",
)
@component_maturity(
    "beta",
    reason="eSpeak NG over its command line, OpenAI and ElevenLabs through g6.core.http on the action gate's allow "
           "with keys from the person's key file; every voice inspected, every transcript's word count set against "
           "measured speech; qualified-draft at best: nothing judges what was said",
)
class AdaptVoiceBlock(AIBlock[VoiceInput, dict, dict]):
    """Speak or transcribe and say what was measured. Never ``verified``."""

    def __init__(self, name: str = "adapt_voice", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: VoiceInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            if data.op == "engines":
                listed = engines.describe()
                result = answer(data.op, f"{len(listed)} engines registered; none was asked anything", card=DESCRIBED,
                                evidence=[{"kind": "package_table", "engines": len(listed)}], engines=listed,
                                **_ids(data))
            elif data.op == "voices":
                found = engines.ENGINES["espeak"].voices(data.timeout)
                result = answer(data.op, f"eSpeak NG lists {len(found)} voices", card=REPORTED,
                                evidence=[{"kind": "service_report", "program": "eSpeak NG"}], voices=found,
                                **_ids(data))
            elif data.op == "speak":
                result = _speak(data)
            elif data.op == "speak_paid":
                result = _speak_paid(data)
            else:
                result = _transcribe(data)
        except MediaError as exc:
            result = refuse(data.op, exc.code, exc.reason, **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["MAX_WORDS_PER_SECOND", "MIN_SPEECH_WITH_WORDS", "NOT_HERE", "SENSITIVE_OPS", "AdaptVoiceBlock",
           "VoiceInput"]
