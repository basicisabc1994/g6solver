# SPDX-License-Identifier: Apache-2.0
"""adapt_synth: every sound it renders is written, inspected from disk and measured against what was asked: its length,
that it is not silent, and its pitch (each note's fundamental within 25 cents of the note asked for, each chord tone
present, an FM sound's sidebands where the ratio puts them, a noise's colour by its spectral slope, each drum hit on its
step). A render that is off is refused (D1, ADAPT_SYNTH-01); a prompt is read by rules and a model's reading is a
claim (ADAPT_SYNTH-01, -03); no remote service is registered (-02)."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import g6.media.adapt_synth as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, NoModel
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media import _dsp
from g6.media._types import DURATION_MISMATCH, EXTRA_MISSING, INPUT_INVALID, PITCH_MISMATCH, SILENT, media_root
from g6.media.adapt_synth import block, render, theory

np = pytest.importorskip("numpy")
SRC = Path(package.__file__).parent


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_synth", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_synth", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _check(out: dict, name: str) -> dict:
    return next(c for c in out["artifact"]["checks"] if c["name"] == name)


# -- D1: a note rendered off pitch is refused, whatever the renderer thought it made --------------------------------

def test_d1_a_note_rendered_off_its_pitch_is_refused_and_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    real = render.oscillator
    monkeypatch.setattr(render, "oscillator", lambda wave, freq, *a, **k: real(wave, freq * 1.03, *a, **k))  # +51 cents
    before = _media_files()
    out = _invoke("note", note="A4", duration=0.5, name="d1-sharp")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == PITCH_MISMATCH, out.get("reason")
    assert "A4" in out["reason"] and _media_files() == before and check_envelope(out) == []


def test_d1_one_wrong_note_in_the_middle_of_a_scale_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    real = render.oscillator
    calls = []

    def third_flat(wave, freq, *a, **k):
        calls.append(freq)
        return real(wave, freq * (0.94 if len(calls) == 3 else 1.0), *a, **k)

    monkeypatch.setattr(render, "oscillator", third_flat)
    out = _invoke("scale", root="C4", scale_type="major", note_duration=0.25, name="d1-scale")
    assert out["code"] == PITCH_MISMATCH and "note 3" in out["reason"] and "E4" in out["reason"]


def test_d1_a_render_twice_the_length_asked_for_is_refused_though_its_pitch_is_right(
        monkeypatch: pytest.MonkeyPatch) -> None:
    real = render.oscillator
    monkeypatch.setattr(render, "oscillator", lambda wave, freq, seconds, rate, *a, **k: real(wave, freq, seconds * 2,
                                                                                             rate, *a, **k))
    out = _invoke("tone", frequency=440.0, duration=0.5, name="d1-tone-twice")
    assert out["code"] == DURATION_MISMATCH and "1.000 s, not the 0.500 s" in out["reason"], out.get("reason")
    assert "d1-tone-twice.wav" not in _media_files()


def test_d1_a_frequency_at_or_above_the_aliasing_line_is_refused_before_it_is_made() -> None:
    tone = _invoke("tone", frequency=15000, rate=8000, duration=0.5, name="d1-alias")  # would alias to 1 kHz
    assert tone["code"] == INPUT_INVALID and "alias" in tone["reason"], tone.get("reason")
    fm = _invoke("fm", frequency=1000, ratio=2, index=2, rate=8000)  # sidebands up to 1000 + 3 x 2000 = 7000 Hz
    assert fm["code"] == INPUT_INVALID and "alias" in fm["reason"], fm.get("reason")
    for op, params in (("note", {"note": "C8"}), ("scale", {"root": "C6", "octaves": 2})):  # up to C8, 4186 Hz
        out = _invoke(op, rate=8000, **params)  # past 0.45 x 8000 = 3600 Hz
        assert out["code"] == INPUT_INVALID and "alias" in out["reason"], (op, out.get("reason"))
    assert "d1-alias.wav" not in _media_files()


def test_d1_a_note_the_detector_cannot_read_never_passes_a_pitch_check() -> None:
    low = _invoke("note", note="A0", duration=1.0, name="a0", overwrite=True)  # 27.5 Hz, under the detector's 30 Hz
    assert low["completion_state"] == "qualified-draft", low.get("reason")
    assert "pitch_matches_request" not in [c["name"] for c in low["artifact"]["checks"]]
    assert low["pitch_not_measured"] == ["A0 (27.50 Hz)"]
    assert render.pitch_checks(np.zeros((48000, 1)), 48000, [("C8", 4186.01, 0.0, 1.0)]) == []


def test_d1_a_silent_render_is_refused() -> None:
    out = _invoke("noise", noise_type="white", duration=0.5, volume=0.0, name="d1-hush")
    assert out["code"] in (SILENT, INPUT_INVALID) and "d1-hush.wav" not in _media_files()


# -- every render's length is checked against the cap before anything is made -------------------------------------

def _nothing_made(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("oscillator", "fm", "noise", "drum"):
        monkeypatch.setattr(render, name, lambda *a, _n=name, **k: pytest.fail(f"render.{_n} ran before the length "
                                                                               "was checked"))


def test_a_sequence_longer_than_the_cap_is_refused_before_a_note_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    _nothing_made(monkeypatch)
    notes = [{"note": "A4", "beats": 64}] * 256  # 256 notes of 64 beats at 20 bpm: 49152 s, 4.7 billion samples
    out = _invoke("sequence", notes=notes, bpm=20, rate=96000)
    assert out["code"] == INPUT_INVALID and str(_dsp.MAX_SAMPLES) in out["reason"], out.get("reason")


@pytest.mark.parametrize(("op", "params"), [
    ("tone", {}), ("fm", {}), ("noise", {}), ("note", {}), ("envelope", {}), ("chord", {}), ("arpeggio", {}),
    ("scale", {}), ("sequence", {"notes": [{"note": "C4", "beats": 1}]}), ("drum_pattern", {"pattern": {"kick": "x..."}})])
def test_every_render_is_checked_against_the_cap_before_it_is_made(monkeypatch: pytest.MonkeyPatch, op: str,
                                                                    params: dict) -> None:
    _nothing_made(monkeypatch)
    monkeypatch.setattr(_dsp, "MAX_SAMPLES", 100)
    out = _invoke(op, **params)
    assert out["code"] == INPUT_INVALID and "more than an op makes" in out["reason"], out.get("reason")


# -- what each op makes, measured -----------------------------------------------------------------------------------

@pytest.mark.parametrize("wave", ["sine", "square", "saw", "triangle"])
def test_a_tone_of_every_wave_measures_at_its_frequency(wave: str) -> None:
    out = _invoke("tone", waveform=wave, frequency=220.0, duration=0.5, name=f"tone-{wave}", overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_PROBED"
    found = _check(out, "pitch_matches_request")
    assert found["passed"] and "220" in found["detail"]
    assert out["artifact"]["measured"]["frames"] == 24000 and abs(out["artifact"]["measured"]["peak_dbfs"] + 6.02) < 0.1


def test_notes_chords_arpeggios_scales_envelopes_and_sequences_measure_right() -> None:
    assert _check(_invoke("note", note="C#5", duration=0.4, overwrite=True), "pitch_matches_request")["passed"]
    chord = _invoke("chord", root="C4", chord_type="maj7", duration=1.0, overwrite=True)
    assert "C4, E4, G4, B4" in _check(chord, "chord_tones_present")["detail"]
    arp = _invoke("arpeggio", root="A3", chord_type="minor", pattern="updown", note_duration=0.2, overwrite=True)
    assert _check(arp, "pitch_matches_request")["passed"] and arp["notes"] == ["A3", "C4", "E4", "C4"]
    scale = _invoke("scale", root="D4", scale_type="dorian", note_duration=0.2, overwrite=True)
    assert scale["notes"][:3] == ["D4", "E4", "F4"] and len(scale["notes"]) == 8
    shaped = _invoke("envelope", note="G4", duration=1.0, attack=0.05, decay=0.1, sustain=0.5, release=0.2,
                     overwrite=True)
    assert _check(shaped, "sustain_level_matches_request")["passed"]
    song = _invoke("sequence", notes=[{"note": "C4", "beats": 1}, {"rest": True, "beats": 0.5},
                                      {"note": "G4", "beats": 1.5}], bpm=120, overwrite=True)
    assert song["artifact"]["measured"]["duration_s"] == 1.5 and _check(song, "pitch_matches_request")["passed"]


def test_fm_puts_its_sidebands_where_the_ratio_says() -> None:
    out = _invoke("fm", frequency=440.0, ratio=2.0, index=2.0, duration=0.5, overwrite=True)
    assert _check(out, "sidebands_where_expected")["passed"]


@pytest.mark.parametrize(("colour", "slope"), [("white", 0.0), ("pink", -3.0), ("brown", -6.0)])
def test_noise_has_the_colour_asked_for(colour: str, slope: float) -> None:
    out = _invoke("noise", noise_type=colour, duration=2.0, seed=5, overwrite=True)
    found = _check(out, "colour_matches_request")
    assert found["passed"], found["detail"]
    assert out["artifact"]["provenance"]["seed"] == 5


def test_a_drum_pattern_lands_each_hit_on_its_step() -> None:
    out = _invoke("drum_pattern", pattern={"kick": "x...x...x...x...", "snare": "....x.......x...",
                                           "hat": "x.x.x.x.x.x.x.x."}, bpm=120, bars=1, overwrite=True)
    assert out["artifact"]["measured"]["duration_s"] == 2.0
    assert _check(out, "hits_on_their_steps")["passed"]


def test_the_same_seed_makes_the_same_file() -> None:
    a = _invoke("noise", noise_type="pink", duration=0.5, seed=9, name="same-a", overwrite=True)
    b = _invoke("noise", noise_type="pink", duration=0.5, seed=9, name="same-b", overwrite=True)
    assert a["artifact"]["sha256"] == b["artifact"]["sha256"]


@pytest.mark.parametrize("field", [{"note": "H4"}, {"note": "A10"}, {"note": "c"}])
def test_a_note_name_that_is_not_one_is_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("note", **field)


def test_theory_knows_its_notes_chords_and_scales() -> None:
    assert abs(theory.frequency("A4") - 440.0) < 1e-9 and abs(theory.frequency("C4") - 261.6256) < 1e-3
    assert theory.chord("C4", "minor") == ["C4", "D#4", "G4"] and theory.scale("A3", "pentatonic")[-1] == "A4"


# -- prompt_to_params: rules first; a model's reading is a claim -------------------------------------------------------

def test_a_prompt_is_read_by_rules_and_says_where_each_value_came_from() -> None:
    out = _invoke("prompt_to_params", prompt="a sad slow piano piece in D minor, 90 bpm, about 20 seconds")
    params, said = out["params"], out["sources"]
    assert (params["bpm"], params["root"], params["mode"], params["duration"]) == (90, "D4", "minor", 20.0)
    assert said["bpm"].startswith("said") and said["mode"].startswith("said")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_PARSED"
    plain = _invoke("prompt_to_params", prompt="something nice")
    assert plain["sources"]["bpm"] == "default" and plain["unread"] == ["something", "nice"]


def test_p0_a_models_reading_is_a_claim_that_changes_nothing() -> None:
    reading = json.dumps({"bpm": 200, "root": "F#2", "mode": "phrygian"})
    plain = block.AdaptSynthBlock().infer(block.SynthInput(op="prompt_to_params", prompt="happy tune in C major"))
    asked = block.AdaptSynthBlock(llm=FakeLLM(default=reading)).infer(
        block.SynthInput(op="prompt_to_params", prompt="happy tune in C major", use_model=True))
    a, b = plain.unwrap(), asked.unwrap()
    assert a["params"] == b["params"] and b["completion_state"] == "qualified-draft"
    claim = next(e for e in b["evidence"] if e["kind"] == "claim")
    assert claim["counts_as_check"] is False and "phrygian" in claim["said"]
    none = block.AdaptSynthBlock(llm=NoModel()).infer(block.SynthInput(op="prompt_to_params", prompt="x",
                                                                       use_model=True)).unwrap()
    assert "no model" in next(e for e in none["evidence"] if e["kind"] == "claim")["said"]


# -- G1, G3, G5-G8, D2, D4, D6, D9 -------------------------------------------------------------------------------------

def test_g1_nothing_is_verified_and_the_package_never_assigns_it() -> None:
    out = _invoke("tone", frequency=330, duration=0.3, overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["artifact"]["completion_state"] == "qualified-draft"
    for path in SRC.rglob("*.py"):
        assert "\"verified\"" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("field", [{"approved": True}, {"completion_state": "verified"}, {"backend": "signalflow"},
                                   {"model": "openrouter/openai/gpt-4o-mini"}, {"output_path": "D:/x.wav"}])
def test_g3_a_callers_claims_and_choices_of_backend_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("tone", **field)


def test_g5_nothing_is_sensitive_and_everything_runs_over_mcp() -> None:
    assert package.__g6_sensitive_ops__ == ()
    reply = invoke_reply("adapt_synth", "note", json.dumps({"note": "E4", "duration": 0.3, "request_id": "q1",
                                                            "run_id": "u1", "overwrite": True}))
    assert reply["ok"] and reply["result"]["request_id"] == "q1" and check_envelope(reply["result"]) == []


def test_g6_g7_g8_and_p1_no_key_no_default_model_no_remote_service() -> None:
    assert sorted(p.name for p in (SRC / "skill").iterdir()) == ["SKILL.md"]
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "litellm", "openrouter", "anthropic", "mock", "fake", "simulat",
                     "experimental", "all rights reserved", "splice", "httpx", "socket", "subprocess"):
            assert word not in text, (path.name, word)
    assert package.__g6_extras__ == {"audio": ("numpy",)} and package.__g6_outside__ == ()


def test_d2_it_declares_the_failure_mode_it_catches_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("audio_check_failed",) and package.__g6_introduces__ == ()
    assert "g6.media.adapt_synth" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_synth") is block.AdaptSynthBlock
    assert block.SynthInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 11
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2].startswith("_") or node.module.split(".")[2] == "adapt_synth"


def test_d6_an_agent_finds_it_and_describes_it() -> None:
    assert "adapt_synth" in [r["component"] for r in discovery.find("generate a tone, a chord or a drum pattern")]
    info = describe_reply("adapt_synth")
    assert info["family"] == "media" and info["skill"] and info["sensitive_ops"] == []


_WITHOUT = """
import json, sys
sys.modules["numpy"] = None
from g6.core.registry import get_registry
reg = get_registry()
out = {op: reg.invoke("adapt_synth", op, params).unwrap() for op, params in (
    ("tone", {"frequency": 440}), ("prompt_to_params", {"prompt": "fast happy tune"}), ("ops", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", ""), v.get("reason", "")] for op, v in out.items()}))
"""


def test_d9_without_numpy_a_render_says_it_is_not_installed_and_the_rules_still_read(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["tone"][:2] == ["blocked-escalated", EXTRA_MISSING] and "g6[audio]" in found["tone"][2]
    assert found["prompt_to_params"][0] == "qualified-draft" and found["ops"][0] is None
