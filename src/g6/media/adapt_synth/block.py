# SPDX-License-Identifier: Apache-2.0
"""AdaptSynthBlock, the registry and MCP door (``g6_invoke``) to adapt_synth.

Every op but ``prompt_to_params`` renders a sound with NumPy and keeps it through :func:`g6.media._sound.keep`: a WAV in
the media folder, inspected from disk, measured, and checked against what was asked (its pitch, its chord tones, its
sidebands, its colour, its drum hits) before it is named; a sound that is off is refused and removed.
``prompt_to_params`` reads parameters from words by fixed rules; a model given to the block from Python
(``AdaptSynthBlock(llm=...)``) may be asked for its reading too, which is kept as a claim and changes nothing. Nothing
here reaches the network or runs a program, so no op is sensitive.
"""
from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media import _dsp, _sound
from g6.media._types import INPUT_INVALID, PARSED, MediaError, MediaResult, answer, check_name, refuse
from g6.media.adapt_synth import prompt, render, theory

_OPS = {
    "tone": "tone(waveform=sine|square|saw|triangle, frequency, duration, volume): one band-limited wave; its pitch is "
            "measured",
    "fm": "fm(frequency, ratio, index, duration): frequency modulation; its sidebands are measured",
    "noise": "noise(noise_type=white|pink|brown, duration, seed): its spectral slope is measured",
    "note": "note(note, duration, waveform, attack, decay, sustain, release): one note such as A4 or Eb3",
    "chord": "chord(root, chord_type, duration, waveform): each chord tone is measured",
    "arpeggio": "arpeggio(root, chord_type, pattern=up|down|updown, note_duration, repeats): each note measured",
    "scale": "scale(root, scale_type, octaves, note_duration): each note measured",
    "envelope": "envelope(note, attack, decay, sustain, release, duration): a note shaped by an ADSR envelope; its "
                "pitch and held level are measured",
    "sequence": "sequence(notes=[{note, beats} or {rest, beats}], bpm, waveform): each note measured at its time",
    "drum_pattern": "drum_pattern(pattern={kick, snare, hat, clap: 'x...'}, bpm, bars, seed): a bar of 4 beats per "
                    "string; each hit is measured on its step",
    "prompt_to_params": "prompt_to_params(prompt, use_model): musical parameters read from words by fixed rules, each "
                        "with where it came from; nothing is made",
}
_NOTE = r"^[A-G](#{1,2}|b{1,2})?[0-8]$"
_ASK = ("Read this request for a short piece of music and answer with one JSON object with the keys bpm, root (a note "
        "such as C4), mode, waveform and duration (seconds). Answer with the JSON only.")


class NoteEvent(BaseModel):
    """One step of a ``sequence``: a note, or a rest, for a number of beats."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, pattern=_NOTE)
    rest: bool = False
    beats: float = Field(1.0, ge=0.0625, le=64, allow_inf_nan=False)

    @model_validator(mode="after")
    def _a_note_or_a_rest(self) -> NoteEvent:
        if (self.note is None) == (not self.rest):
            raise ValueError("a step is a note or a rest, one of them")
        return self


class SynthInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["tone", "fm", "noise", "note", "chord", "arpeggio", "scale", "envelope", "sequence", "drum_pattern",
                "prompt_to_params", "ops", "help"] = "help"
    waveform: Literal["sine", "square", "saw", "triangle"] = "sine"
    frequency: float = Field(440.0, ge=20, le=20000, allow_inf_nan=False)
    duration: float = Field(1.0, ge=0.05, le=60, allow_inf_nan=False)
    volume: float = Field(0.5, ge=0, le=1, allow_inf_nan=False)
    rate: int = Field(48000, ge=8000, le=96000)
    bits: Literal[16, 24] = 16
    note: str = Field("A4", pattern=_NOTE)
    root: str = Field("C4", pattern=_NOTE)
    chord_type: Literal["major", "minor", "dim", "aug", "7", "maj7", "min7", "sus2", "sus4", "power"] = "major"
    scale_type: Literal["major", "minor", "pentatonic", "minor_pentatonic", "blues", "dorian", "mixolydian",
                        "harmonic_minor", "chromatic"] = "major"
    octaves: int = Field(1, ge=1, le=3)
    pattern: Literal["up", "down", "updown"] | dict[str, str] = "up"
    repeats: int = Field(1, ge=1, le=8)
    note_duration: float = Field(0.25, ge=0.05, le=4, allow_inf_nan=False)
    attack: float = Field(0.01, ge=0, le=10, allow_inf_nan=False)
    decay: float = Field(0.05, ge=0, le=10, allow_inf_nan=False)
    sustain: float = Field(0.8, ge=0, le=1, allow_inf_nan=False)
    release: float = Field(0.05, ge=0, le=10, allow_inf_nan=False)
    ratio: float = Field(2.0, ge=0.1, le=16, allow_inf_nan=False)
    index: float = Field(2.0, ge=0, le=20, allow_inf_nan=False)
    noise_type: Literal["white", "pink", "brown"] = "white"
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    notes: list[NoteEvent] = Field(default_factory=list, max_length=256)
    bpm: float = Field(120.0, ge=20, le=300, allow_inf_nan=False)
    bars: int = Field(1, ge=1, le=16)
    prompt: str = Field("", max_length=2000)
    use_model: bool = False
    name: str = Field("", max_length=90)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if value:
            try:
                check_name(value)
            except MediaError as exc:
                raise ValueError(exc.reason) from None
        return value

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> SynthInput:
        if self.op == "drum_pattern":
            if not isinstance(self.pattern, dict) or not self.pattern:
                raise ValueError("drum_pattern needs pattern: {kind: 'x...x...'} for kick, snare, hat or clap")
            unknown = sorted(set(self.pattern) - {"kick", "snare", "hat", "clap"})
            lengths = {len(v) for v in self.pattern.values()}
            if unknown or len(lengths) != 1 or not 1 <= lengths.pop() <= 64 or any(
                    set(v) - {"x", "."} for v in self.pattern.values()):
                raise ValueError("a drum pattern is kick, snare, hat or clap, each one string of x (a hit) and . "
                                 f"(a rest), all the same length, at most 64{f'; not {unknown}' if unknown else ''}")
        elif self.op == "arpeggio" and isinstance(self.pattern, dict):
            raise ValueError("arpeggio's pattern is up, down or updown")
        if self.op == "sequence" and not self.notes:
            raise ValueError("sequence needs notes: [{note, beats} or {rest: true, beats}]")
        if self.op == "prompt_to_params" and not self.prompt.strip():
            raise ValueError("prompt_to_params needs prompt")
        for field in ("note", "root"):
            try:
                theory.midi(getattr(self, field))
            except ValueError as exc:
                raise ValueError(str(exc)) from None
        return self


def _ids(d: SynthInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _shaped(d: SynthInput, wave: Any, seconds: float) -> Any:
    return wave * render.adsr(len(wave), d.rate, d.attack, d.decay, d.sustain, d.release)


def _names(d: SynthInput) -> list[str]:
    """The notes of an ``arpeggio`` or a ``scale``, in order."""
    if d.op == "arpeggio":
        up = theory.chord(d.root, d.chord_type)
        return {"up": up, "down": up[::-1], "updown": up + up[-2:0:-1]}[d.pattern] * d.repeats
    return theory.scale(d.root, d.scale_type, d.octaves)


def _frames(d: SynthInput) -> int:
    """How many frames the sound asked for holds, from the request alone, before anything is made."""
    if d.op == "sequence":
        beat = 60.0 / d.bpm
        return sum(int(round(step.beats * beat * d.rate)) for step in d.notes)
    if d.op == "drum_pattern":
        return int(round(240.0 / d.bpm * d.bars * d.rate))
    if d.op in ("arpeggio", "scale"):
        return len(_names(d)) * int(round(d.note_duration * d.rate))
    return int(round(d.duration * d.rate))


def _highest(d: SynthInput) -> tuple[float, str] | None:
    """The highest frequency the sound asked for holds, and what it is: a fundamental, or an FM sound's upper sideband
    by Carson's rule (the carrier plus index + 1 times the modulator); ``None`` for noise and drums."""
    if d.op == "tone":
        return d.frequency, f"{d.frequency:g} Hz"
    if d.op == "fm":
        top = d.frequency * (1 + (d.index + 1) * d.ratio)
        return top, f"the FM sound's sidebands, up to {top:g} Hz (the carrier plus index + 1 times the modulator),"
    if d.op in ("note", "envelope"):
        notes = [d.note]
    elif d.op == "chord":
        notes = theory.chord(d.root, d.chord_type)
    elif d.op in ("arpeggio", "scale"):
        notes = _names(d)
    elif d.op == "sequence":
        notes = [s.note for s in d.notes if s.note]
    else:
        return None
    if not notes:
        return None
    top = max(notes, key=theory.frequency)
    return theory.frequency(top), f"{top} ({theory.frequency(top):.2f} Hz)"


def _unmeasured(fields: dict[str, Any], notes: list[tuple[str, float, float, float]]) -> None:
    missed = render.unmeasured(notes)
    if missed:
        fields["pitch_not_measured"] = missed


def _melody(d: SynthInput, notes: list[str], seconds: float) -> tuple[Any, list[tuple[str, float, float, float]]]:
    np = _dsp.numpy("make the sound")
    parts, placed = [], []
    for i, note in enumerate(notes):
        wave = render.oscillator(d.waveform, theory.frequency(note), seconds, d.rate)
        parts.append(wave * render.adsr(len(wave), d.rate, min(d.attack, seconds / 4), min(d.decay, seconds / 4),
                                        d.sustain or 0.8, min(d.release, seconds / 4)))
        placed.append((note, theory.frequency(note), i * seconds, seconds))
    return np.concatenate(parts), placed


def _render(d: SynthInput) -> tuple[Any, dict[str, Any], Any, int | None, dict[str, Any]]:
    """The samples, the parameters used, the checks on the written sound, the seed and fields for the result."""
    np = _dsp.numpy("make the sound")
    rate, fields = d.rate, {}
    if d.op == "tone":
        wave = _shaped(d.model_copy(update={"attack": 0.005, "decay": 0.0, "sustain": 1.0, "release": 0.01}),
                       render.oscillator(d.waveform, d.frequency, d.duration, rate), d.duration)
        placed = [(f"{d.frequency:g} Hz", d.frequency, 0.0, d.duration)]
        _unmeasured(fields, placed)
        return wave, {"waveform": d.waveform, "frequency": d.frequency, "duration": d.duration}, (
            lambda z, _: render.pitch_checks(z, rate, placed)), None, fields
    if d.op in ("note", "envelope"):
        hz = theory.frequency(d.note)
        wave = _shaped(d, render.oscillator(d.waveform, hz, d.duration, rate), d.duration)
        _unmeasured(fields, [(d.note, hz, 0.0, d.duration)])

        def note_checks(z: Any, _: dict) -> list:
            found = render.pitch_checks(z, rate, [(d.note, hz, 0.0, d.duration)])
            held = render.sustain_check(z, rate, d.attack, d.decay, d.sustain, d.release, d.duration)
            return found + ([held] if held is not None and d.op == "envelope" else [])

        return wave, {"note": d.note, "hz": round(hz, 4), "adsr": [d.attack, d.decay, d.sustain, d.release]}, \
            note_checks, None, fields
    if d.op == "chord":
        names = theory.chord(d.root, d.chord_type)
        wave = sum(render.oscillator(d.waveform, theory.frequency(n), d.duration, rate) for n in names)
        wave = _shaped(d, wave, d.duration)
        fields["notes"] = names
        return wave, {"root": d.root, "chord_type": d.chord_type, "notes": names}, (
            lambda z, _: [render.chord_check(z, rate, names)]), None, fields
    if d.op in ("arpeggio", "scale"):
        names = _names(d)
        wave, placed = _melody(d, names, d.note_duration)
        fields["notes"] = names
        _unmeasured(fields, placed)
        return wave, {"notes": names, "note_duration": d.note_duration}, (
            lambda z, _: render.pitch_checks(z, rate, placed)), None, fields
    if d.op == "sequence":
        beat = 60.0 / d.bpm
        parts, placed, at = [], [], 0.0
        for step in d.notes:
            seconds = step.beats * beat
            count = int(round(seconds * rate))
            if step.rest:
                parts.append(np.zeros(count))
            else:
                wave = render.oscillator(d.waveform, theory.frequency(step.note), count / rate, rate)
                parts.append(wave * render.adsr(len(wave), rate, min(d.attack, seconds / 4), min(d.decay, seconds / 4),
                                                d.sustain or 0.8, min(d.release, seconds / 4)))
                placed.append((step.note, theory.frequency(step.note), at, seconds))
            at += count / rate
        fields["notes"] = [s.note or "rest" for s in d.notes]
        _unmeasured(fields, placed)
        return np.concatenate(parts), {"bpm": d.bpm, "steps": [s.model_dump() for s in d.notes]}, (
            lambda z, _: render.pitch_checks(z, rate, placed)), None, fields
    if d.op == "fm":
        wave = _shaped(d, render.fm(d.frequency, d.ratio, d.index, d.duration, rate), d.duration)
        return wave, {"frequency": d.frequency, "ratio": d.ratio, "index": d.index}, (
            lambda z, _: [render.sideband_check(z, rate, d.frequency, d.ratio)]), None, fields
    seed = d.seed if d.seed is not None else secrets.randbits(32)
    if d.op == "noise":
        wave = render.noise(d.noise_type, d.duration, rate, seed)
        return wave, {"noise_type": d.noise_type}, (
            lambda z, _: [render.colour_check(z, rate, d.noise_type)]), seed, {"seed": seed}
    pattern: dict[str, str] = d.pattern  # drum_pattern; the input model checked its shape
    steps = len(next(iter(pattern.values())))
    bar = 240.0 / d.bpm
    total = int(round(bar * d.bars * rate))
    out = np.zeros(total + rate)
    rng = np.random.default_rng(seed)
    hits: set[float] = set()
    for b in range(d.bars):
        for kind, line in sorted(pattern.items()):
            for i, mark in enumerate(line):
                if mark == "x":
                    at = b * bar + i * bar / steps
                    hit = render.drum(kind, rate, rng)
                    start = int(round(at * rate))
                    out[start:start + len(hit)] += hit
                    hits.add(round(at, 6))
    return out[:total], {"pattern": pattern, "bpm": d.bpm, "bars": d.bars}, (
        lambda z, _: [render.hits_check(z, rate, sorted(hits))]), seed, {"seed": seed}


def _make(d: SynthInput) -> MediaResult:
    frames = _frames(d)  # what the request asks for, which the written file is held to
    _dsp.fits(frames, f"the {d.op}")  # mono: one sample a frame
    top = _highest(d)
    if top is not None and top[0] >= render.ALIAS_SHARE * d.rate:
        raise MediaError(INPUT_INVALID, f"{top[1]} is at or above {render.ALIAS_SHARE:g} of the {d.rate} Hz sample "
                                        f"rate ({render.ALIAS_SHARE * d.rate:g} Hz), so it would alias: raise rate or "
                                        "lower the frequency")
    samples, params, checks, seed, fields = _render(d)
    samples = render.peak_to(samples, d.volume)[:, None]
    return _sound.keep(d.op, samples, d.rate, d.bits, frames=frames, channels=1, name=d.name, overwrite=d.overwrite,
                       ids=_ids(d), params=params, checks=checks, seed=seed, fields=fields, made=f"a {d.op}")


def _read(d: SynthInput, llm: Any) -> MediaResult:
    found = prompt.read(d.prompt)
    evidence: list[dict[str, Any]] = [{"kind": "rules", "read": "g6.media.adapt_synth.prompt", "sources":
                                       found["sources"], "unread": found["unread"]}]
    if d.use_model:
        claim: dict[str, Any] = {"kind": "claim", "about": "the musical parameters the words ask for",
                                 "by": type(llm).__name__ if llm is not None else "", "counts_as_check": False}
        if llm is None:
            claim["said"] = ("no model was given to the block (over MCP: no model is configured), so no model was "
                             "asked")
        else:
            try:
                claim["said"] = str(llm.complete(_ASK, d.prompt, max_tokens=200))[:2000]
            except Exception as exc:  # noqa: BLE001 - a reading that could not be had changes nothing
                claim["said"] = f"no model reading: {type(exc).__name__}: {exc}"[:300]
        evidence.append(claim)
    unread = f"; words not read: {', '.join(found['unread'])}" if found["unread"] else ""
    return answer(d.op, f"parameters read from the words by fixed rules{unread}", card=PARSED, evidence=evidence,
                  **found, **_ids(d))


@block_contract(
    purpose="make tones, notes, chords, scales, melodies, noise and drum patterns, each measured against what was "
            "asked before it is returned",
    mitigates_failure_modes=("audio_check_failed",),
    verification_method="g6.media sound inspector on every WAV written, and the written sound measured: each note's "
                        "fundamental (YIN) within 25 cents, each chord tone's spectral peak, FM sidebands, a noise's "
                        "spectral slope, an onset at every drum hit",
)
@component_maturity(
    "beta",
    reason="band-limited NumPy synthesis; every render measured from the written file against the request and refused "
           "when off; prompts read by rules, a model's reading a claim; qualified-draft at best: nothing judges the "
           "music",
)
class AdaptSynthBlock(AIBlock[SynthInput, dict, dict]):
    """Make a sound and say what was measured about it. Never ``verified``."""

    def __init__(self, name: str = "adapt_synth", state: dict | None = None, *, llm: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.llm = llm

    def infer(self, data: SynthInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _read(data, self.llm) if data.op == "prompt_to_params" else _make(data)
        except MediaError as exc:
            result = refuse(data.op, exc.code, exc.reason, **_ids(data))
        except ValueError as exc:
            result = refuse(data.op, INPUT_INVALID, str(exc)[:500], **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["AdaptSynthBlock", "NoteEvent", "SynthInput"]
