# SPDX-License-Identifier: Apache-2.0
"""adapt_audio: every edit writes a WAV that is inspected from disk and measured before and after (peak, true peak,
loudness, length, rate, channels); a result that would clip, misses its loudness or true-peak target, or is silent is
refused (D1, ADAPT_AUDIO-03); no caller code runs and no review makes anything verified (ADAPT_AUDIO-01, -02); no remote
service is registered (-04). Known values: a 997 Hz sine at -20 dBFS is -23.0 LUFS (ITU-R BS.1770); a sine at a quarter
of the sample rate, a quarter cycle off, peaks 3 dB above its samples."""
from __future__ import annotations

import ast
import base64
import dataclasses
import io
import json
import math
import shutil
import subprocess
import sys
import tracemalloc
import wave
from pathlib import Path

import pytest
from media_kit import outside_link, put, tone_wav

import g6.media.adapt_audio as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media._types import (
    CLIPPED,
    DURATION_MISMATCH,
    EXTRA_MISSING,
    FORMAT_MISMATCH,
    INPUT_INVALID,
    LOUDNESS_MISSED,
    SILENT,
    media_root,
)
from g6.media import _dsp, _sound
from g6.media.adapt_audio import block, edits

np = pytest.importorskip("numpy")
SRC = Path(package.__file__).parent
REAL = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                          reason="ffmpeg and ffprobe are not installed here")


def wav(samples, rate: int = 48000, bits: int = 16) -> bytes:
    """``samples`` (frames, or frames x channels, full scale 1.0) as PCM WAV bytes."""
    data = np.asarray(samples, dtype=np.float64)
    data = data[:, None] if data.ndim == 1 else data
    full = 2 ** (bits - 1) - 1
    ints = np.round(np.clip(data, -1, 1) * full).astype("<i2" if bits == 16 else "<i4")
    raw = ints.tobytes() if bits != 24 else b"".join(int(v).to_bytes(3, "little", signed=True) for v in ints.ravel())
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(raw)
    return out.getvalue()


def sine(freq: float, seconds: float, level: float = 0.5, rate: int = 48000, phase: float = 0.0):
    t = np.arange(int(round(seconds * rate))) / rate
    return level * np.sin(2 * np.pi * freq * t + phase)


def store(name: str, samples, rate: int = 48000, bits: int = 16) -> str:
    put(f"{name}.wav", wav(samples, rate, bits))
    return f"{name}.wav"


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_audio", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_audio", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _after(out: dict) -> dict:
    return next(e for e in out["evidence"] if e.get("kind") == "probe_after")


# -- D1: a level change that would distort is refused, not written; the target is measured again --------------------

def test_d1_a_quiet_track_pushed_to_a_loud_target_would_pass_its_true_peak_and_is_refused() -> None:
    # quiet material with sharp transients, and a target at which the transients land at 0.95 of full scale: no sample
    # would clip, but the true peak would be over the -1 dBTP ceiling (0.89 of full scale)
    body = sine(300, 3.0, 0.02)
    body[::4800] = 0.09
    source = store("quiet-clicks", body)
    level = _invoke("loudness", source=source)["measured"]["lufs"]
    target = round(level + 20 * np.log10(0.95 / 0.09), 2)
    before = _media_files()
    out = _invoke("normalise_loudness", source=source, target_lufs=target, ceiling_dbtp=-1.0, name="d1-loud")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == CLIPPED, out.get("reason")
    assert "true peak" in out["reason"] and _media_files() == before and check_envelope(out) == []


def test_d1_a_gain_that_goes_past_full_scale_is_refused_never_clipped_silently() -> None:
    out = _invoke("gain", source=store("loud-tone", sine(440, 1.0, 0.7)), gain_db=6.0, name="d1-gain")
    assert out["code"] == CLIPPED and "past full scale" in out["reason"]
    assert "d1-gain.wav" not in _media_files()


def test_d1_with_a_limiter_the_target_is_reached_and_measured_again_or_refused_as_missed() -> None:
    body = sine(300, 3.0, 0.02)
    body[::4800] = 0.25
    source = store("quiet-clicks-2", body)
    out = _invoke("normalise_loudness", source=source, target_lufs=-16.0, ceiling_dbtp=-1.0, limit=True,
                  name="au-levelled", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    after = _after(out)
    assert abs(after["lufs"] + 16.0) <= 0.5 and after["true_peak_dbtp"] <= -1.0 + 0.1
    names = [c["name"] for c in out["artifact"]["checks"]]
    assert {"loudness_matches_request", "true_peak_within_ceiling", "not_clipped", "not_silent"} <= set(names)
    noise = np.random.default_rng(1).standard_normal(48000 * 2) * 0.05
    missed = _invoke("normalise_loudness", source=store("noise", noise), target_lufs=-5.0, ceiling_dbtp=-6.0,
                     limit=True, name="too-loud")
    assert missed["code"] == LOUDNESS_MISSED and "too-loud.wav" not in _media_files()


# -- known values ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("rate", "channels", "want"), [(48000, 1, -23.01), (44100, 1, -23.01), (48000, 2, -20.0)])
def test_loudness_of_a_997_hz_sine_at_minus_20_dbfs_is_the_standards_value(rate: int, channels: int,
                                                                            want: float) -> None:
    tone = sine(997, 5.0, 0.1, rate)
    out = _invoke("loudness", source=store(f"k{rate}{channels}", np.stack([tone] * channels, 1), rate))
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_MEASURED"
    assert abs(out["measured"]["lufs"] - want) <= 0.1, out["measured"]


def test_true_peak_finds_the_peak_between_the_samples() -> None:
    tone = sine(12000, 1.0, 0.5, 48000, phase=np.pi / 4)  # every sample at 0.354 of full scale; the wave peaks at 0.5
    out = _invoke("crest_factor", source=store("between", tone))
    found = out["measured"]
    assert abs(found["peak_dbfs"] + 9.03) <= 0.1 and abs(found["true_peak_dbtp"] + 6.02) <= 0.3


def test_pitch_bpm_silence_phase_and_spectrum_measure_what_is_there() -> None:
    assert abs(_invoke("pitch", source=store("a3", sine(220, 1.0)))["measured"]["frequency_hz"] - 220) <= 1.0
    assert _invoke("pitch", source="a3.wav")["measured"]["note"] == "A3"
    clicks = np.zeros(48000 * 6)
    for at in range(0, len(clicks), 24000):
        clicks[at:at + 480] = sine(1000, 0.01, 0.8)
    assert abs(_invoke("bpm", source=store("clicks", clicks))["measured"]["bpm"] - 120) <= 2
    gap = np.concatenate([sine(440, 1.0), np.zeros(48000), sine(440, 1.0)])
    (quiet,) = _invoke("silence", source=store("gap", gap), threshold_dbfs=-50)["measured"]["silences"]
    assert abs(quiet["start_s"] - 1.0) <= 0.03 and abs(quiet["end_s"] - 2.0) <= 0.03
    tone = sine(440, 1.0)
    assert _invoke("phase_correlation", source=store("same", np.stack([tone, tone], 1)))["measured"][
        "correlation"] > 0.99
    assert _invoke("phase_correlation", source=store("flip", np.stack([tone, -tone], 1)))["measured"][
        "correlation"] < -0.99
    peaks = _invoke("spectrum", source=store("two", sine(440, 1.0, 0.4) + sine(1000, 1.0, 0.2)))["measured"]["peaks"]
    assert [round(p["frequency_hz"] / 10) * 10 for p in peaks[:2]] == [440, 1000]
    assert abs(_invoke("crest_factor", source="a3.wav")["measured"]["crest_factor_db"] - 3.01) <= 0.1


def test_metadata_reads_the_file_with_the_standard_library_alone() -> None:
    put("meta.wav", tone_wav(seconds=0.5, rate=22050, channels=2, width=3))
    found = _invoke("metadata", source="meta.wav")["measured"]
    assert (found["rate"], found["channels"], found["bits"], found["frames"]) == (22050, 2, 24, 11025)


# -- the work and the result are bounded, as the inputs are --------------------------------------------------------

def _peak_mb(run) -> tuple[object, float]:
    tracemalloc.start()
    try:
        out = run()
        peak = tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()
    return out, peak


def _no_numpy_empty_over(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    real = np.empty

    def guard(shape, *args, **kwargs):
        count = math.prod(shape) if isinstance(shape, tuple) else int(shape)
        assert count <= limit, f"numpy.empty was asked for {count} values before the size was checked"
        return real(shape, *args, **kwargs)

    monkeypatch.setattr(np, "empty", guard)


def test_bpm_of_a_long_sound_at_a_low_rate_is_measured_in_bounded_memory() -> None:
    clicks = np.zeros(100_000)  # 100 s at 1 kHz: a ten-sample hop, which once meant a 1024-sample window per hop
    for at in range(0, len(clicks), 500):
        clicks[at:at + 10] = 0.8 * (-1.0) ** np.arange(10)
    source = store("slow-clicks", clicks, rate=1000)
    out, peak = _peak_mb(lambda: _invoke("bpm", source=source))
    assert peak < 48, f"{peak:.0f} MiB"
    assert abs(out["measured"]["bpm"] - 120) <= 2, out["measured"]


def test_a_resample_whose_result_would_pass_the_cap_is_refused_before_it_is_made(monkeypatch) -> None:
    source = store("long-8k", sine(440, 162.5, rate=8000), rate=8000)  # 1.3 million frames: 31.2 million at 192 kHz
    _no_numpy_empty_over(monkeypatch, 4_000_000)
    out = _invoke("resample", source=source, rate=192000, name="too-many")
    assert out["code"] == INPUT_INVALID and str(_dsp.MAX_SAMPLES) in out["reason"], out.get("reason")
    assert _invoke("resample", source=source, rate=16000, name="twice", overwrite=True)["completion_state"] == (
        "qualified-draft")


def test_downsampling_many_channels_works_in_bounded_steps() -> None:
    tone = np.sin(2 * np.pi * 1000 * np.arange(15360) / 768000)
    many = np.repeat(tone[:, None], 32, axis=1) * 0.5
    out, peak = _peak_mb(lambda: _dsp.resample(many, 768000, 8000))
    assert peak < 64, f"{peak:.0f} MiB"
    assert out.shape == (160, 32) and np.allclose(out, out[:, :1])
    assert abs(np.sqrt(np.mean(out[20:-20, 0] ** 2)) - 0.5 / np.sqrt(2)) < 0.03


def test_a_concat_past_the_cap_is_refused_from_the_headers_before_a_sample_is_read(monkeypatch) -> None:
    sources = [store("half-a", sine(440, 1.25)), store("half-b", sine(330, 1.25))]  # 60000 samples each
    monkeypatch.setattr(_dsp, "MAX_SAMPLES", 100_000)
    read = []
    real = _dsp.load
    monkeypatch.setattr(_dsp, "load", lambda path: read.append(path) or real(path))
    out = _invoke("concat", sources=sources, name="too-long")
    assert out["code"] == INPUT_INVALID and "120000" in out["reason"] and read == [], out.get("reason")


def test_a_conversion_to_stereo_past_the_cap_is_refused_before_it_is_made(monkeypatch) -> None:
    source = store("mono-long", sine(440, 1.25))
    monkeypatch.setattr(_dsp, "MAX_SAMPLES", 100_000)
    monkeypatch.setattr(_sound, "keep", lambda *a, **k: pytest.fail("the stereo sound was made and kept"))
    out = _invoke("convert", source=source, channels=2, name="too-wide")
    assert out["code"] == INPUT_INVALID and "120000" in out["reason"], out.get("reason")


# -- the edits: each result inspected, measured before and after ---------------------------------------------------

def test_each_edit_writes_what_it_was_asked_for_and_measures_it() -> None:
    tone = store("base", sine(1000, 2.0))
    trim = _invoke("trim", source=tone, start=0.5, end=1.25, name="cut")
    assert trim["artifact"]["measured"]["frames"] == 36000 and trim["warning_card"]["code"] == "G6_E_MEDIA_PROBED"
    assert {e["kind"] for e in trim["evidence"]} >= {"probe_before", "probe_after"}
    assert _invoke("concat", sources=[tone, "cut.wav"])["artifact"]["measured"]["frames"] == 96000 + 36000
    mixed = _invoke("mix", sources=[tone, store("low", sine(200, 1.0, 0.3))], gains_db=[-6.0, -6.0])
    assert mixed["artifact"]["measured"]["frames"] == 96000
    faded = _invoke("fade", source=tone, fade_in=0.1, fade_out=0.1, name="faded")
    assert faded["artifact"]["measured"]["frames"] == 96000
    up = _invoke("gain", source=tone, gain_db=3.0)
    assert abs(_after(up)["peak_dbfs"] - (20 * np.log10(0.5) + 3)) <= 0.05
    re = _invoke("resample", source=tone, rate=44100)
    assert re["artifact"]["measured"]["rate"] == 44100 and re["artifact"]["measured"]["frames"] == 88200
    assert abs(_invoke("pitch", source=re["artifact"]["name"])["measured"]["frequency_hz"] - 1000) <= 2
    mono = _invoke("convert", source=store("st", np.stack([sine(440, 1.0), sine(440, 1.0)], 1)), channels=1, bits=24)
    assert (mono["artifact"]["measured"]["channels"], mono["artifact"]["measured"]["bits"]) == (1, 24)
    dithered = _invoke("dither", source=mono["artifact"]["name"], bits=16)
    assert dithered["artifact"]["measured"]["bits"] == 16


def test_eq_compress_limit_gate_de_ess_and_noise_reduce_change_what_they_say() -> None:
    tone = store("eq-in", sine(1000, 1.0, 0.25))
    boosted = _invoke("eq", source=tone, bands=[{"type": "peak", "frequency_hz": 1000, "gain_db": 6.0, "q": 1.0}])
    assert abs(_after(boosted)["rms_dbfs"] - _invoke("metadata", source=tone)["measured"]["rms_dbfs"] - 6.0) <= 0.3
    cut = _invoke("eq", source=store("low-in", sine(100, 1.0, 0.5)),
                  bands=[{"type": "high_pass", "frequency_hz": 1000, "q": 0.707}])
    assert _after(cut)["rms_dbfs"] < -30
    steady = store("steady", sine(500, 2.0, 0.447))  # -10 dBFS RMS
    squeezed = _invoke("compress", source=steady, threshold_db=-20, ratio=4, attack_ms=5, release_ms=50)
    assert abs(_after(squeezed)["rms_dbfs"] - (-20 + 10 / 4)) <= 1.0
    noisy = np.random.default_rng(2).standard_normal(48000) * 0.25
    held = _invoke("limit", source=store("peaky", noisy), ceiling_dbtp=-3.0)
    assert _after(held)["true_peak_dbtp"] <= -3.0 + 0.1
    floor = np.random.default_rng(3).standard_normal(48000 * 2) * 0.002
    floor[:48000] += sine(440, 1.0, 0.5)
    gated = _invoke("gate", source=store("floor", floor), threshold_db=-40, floor_db=-30)
    out = np.frombuffer(Path(gated["artifact"]["path"]).read_bytes()[44:], "<i2") / 32767
    assert np.sqrt(np.mean(out[60000:] ** 2)) < 0.002 * 10 ** (-20 / 20)
    sibilant = sine(300, 1.0, 0.3) + sine(7000, 1.0, 0.4)
    eased = _invoke("de_ess", source=store("ess", sibilant), frequency_hz=5000, threshold_db=-30, reduction_db=10)
    bands = _invoke("spectrum", source=eased["artifact"]["name"])["measured"]["peaks"]
    level = {round(p["frequency_hz"] / 100) * 100: p["level_db"] for p in bands}
    assert level[7000] - level[300] < 20 * np.log10(0.4 / 0.3) - 6
    hiss = np.random.default_rng(4).standard_normal(48000 * 2) * 0.01
    hiss[48000:] += sine(440, 1.0, 0.5)
    cleaned = _invoke("noise_reduce", source=store("hiss", hiss), noise_start=0.0, noise_end=0.9, reduction_db=20)
    assert cleaned["noise_floor"]["after_dbfs"] <= cleaned["noise_floor"]["before_dbfs"] - 10


def test_d1_an_edit_that_returns_twice_the_length_of_its_source_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = edits.EDITS["fade"]
    monkeypatch.setitem(edits.EDITS, "fade", lambda d, x, rate: dataclasses.replace(real(d, x, rate),
                                                                                    samples=np.concatenate([x, x])))
    out = _invoke("fade", source=store("fade-src", sine(440, 1.0)), fade_in=0.1, name="d1-fade-twice")
    assert out["code"] == DURATION_MISMATCH and "2.000 s, not the 1.000 s" in out["reason"], out.get("reason")


def test_an_edit_that_leaves_silence_is_refused() -> None:
    out = _invoke("gain", source=store("faint", sine(440, 1.0, 0.01)), gain_db=-60.0, name="gone")
    assert out["code"] == SILENT and "gone.wav" not in _media_files()


def test_a_file_that_is_not_wav_is_refused_with_how_to_convert_it() -> None:
    (media_root() / "song.wav").write_bytes(b"ID3\x04" + bytes(400))
    out = _invoke("gain", source="song.wav", gain_db=1.0)
    assert out["code"] == FORMAT_MISMATCH and "transcode" in out["reason"]


def test_inline_audio_is_taken_as_base64_and_capped() -> None:
    data = base64.b64encode(wav(sine(440, 0.5))).decode()
    assert _invoke("metadata", audio_base64=data)["measured"]["frames"] == 24000
    assert "G6_E_INPUT_VALIDATION" in _refused("metadata", audio_base64="A" * (block.MAX_BASE64_CHARS + 4))


@pytest.mark.parametrize("source", ["../x.wav", "C:/x.wav", "/etc/passwd", "a/b.wav"])
def test_a_source_outside_the_media_folder_is_refused(source: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("metadata", source=source)


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_named_like_a_wav_to_a_file_outside_the_media_folder_is_refused_unread(tmp_path: Path,
                                                                                       kind: str) -> None:
    outside_link(tmp_path, "meeting.wav", wav(sine(440, 0.5)), kind)
    for op, params in (("metadata", {"source": "meeting.wav"}), ("loudness", {"source": "meeting.wav"}),
                       ("gain", {"source": "meeting.wav", "gain_db": 1.0}),
                       ("concat", {"sources": [store("plain", sine(440, 0.5)), "meeting.wav"]})):
        out = _invoke(op, **params)
        assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT_INVALID, (op, out.get("reason"))
        assert "link" in out["reason"] and "measured" not in out, op


def test_files_that_do_not_match_are_refused_rather_than_mixed() -> None:
    out = _invoke("concat", sources=[store("r48", sine(440, 0.5)), store("r44", sine(440, 0.5, rate=44100), 44100)])
    assert out["code"] == INPUT_INVALID and "resample" in out["reason"]


# -- ADAPT_AUDIO-01, -02, -04 -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("op", "field"), [("metadata", {"code": "play 60"}), ("metadata", {"host": "10.0.0.2"}),
                                            ("gain", {"approved": True}), ("gain", {"reviewer": "Dr Who"}),
                                            ("gain", {"completion_state": "verified"}),
                                            ("gain", {"output_path": "D:/clips/x.wav"})])
def test_p0_no_live_code_and_no_review_a_callers_word_is_refused(op: str, field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused(op, source="x.wav", **field)
    assert "G6_E_INPUT_VALIDATION" in _refused("sonic_pi_eval")
    assert "G6_E_INPUT_VALIDATION" in _refused("record_human_review")


def test_p0_nothing_is_verified_and_no_live_coding_or_remote_service_ships() -> None:
    out = _invoke("gain", source=store("au-g1", sine(440, 1.0, 0.25)), gain_db=1.0, overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["artifact"]["completion_state"] == "qualified-draft"
    assert sorted(p.name for p in (SRC / "skill").iterdir()) == ["SKILL.md"]
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        said = ast.get_docstring(tree) or ""
        words = [n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value.strip() != said]  # the package docstring says what was cut, and why
        assert not [w for w in words if "sonic" in w or "splice" in w or w == "verified"], path.name
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import) and any(a.name == "subprocess"
                                                                                   for a in n.names)]


# -- G5, G6-G8, D2, D4, D6, D9 ----------------------------------------------------------------------------------------

def test_g5_only_transcode_runs_a_program_and_only_it_is_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ("transcode",)
    denied = invoke_reply("adapt_audio", "transcode", '{"source": "x.wav", "format": "flac"}',
                          allow_sensitive=frozenset())
    assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    reply = invoke_reply("adapt_audio", "gain", json.dumps({"source": store("mcp", sine(440, 0.5, 0.25)),
                                                            "gain_db": 2, "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["result"]["request_id"] == "q1" and check_envelope(reply["result"]) == []


def test_g6_g7_g8_no_key_no_model_no_stand_in_and_only_numpy() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "litellm", "openrouter", "anthropic", "openai", "mock", "fake", "simulat",
                     "experimental", "all rights reserved", "httpx", "socket", "demucs", "torch"):
            assert word not in text or (word in ("demucs", "torch") and path.name in ("__init__.py", "SKILL.md")), (
                path.name, word)
    assert package.__g6_extras__ == {"audio": ("numpy",)}
    assert {p.name for p in package.__g6_outside__} == {"ffmpeg", "ffprobe"}


def test_d2_it_declares_the_failure_mode_it_catches_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("audio_check_failed",) and package.__g6_introduces__ == ()
    assert "g6.media.adapt_audio" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_audio") is block.AdaptAudioBlock
    assert block.AudioInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 24
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2].startswith("_") or node.module.split(".")[2] == "adapt_audio"


def test_d6_an_agent_finds_it_by_its_failure_mode_and_invokes_it_over_mcp() -> None:
    found = [r["component"] for r in discovery.find(FailureModeRegistry.title("audio_check_failed"))]
    assert "adapt_audio" in found
    info = describe_reply("adapt_audio")
    assert info["family"] == "media" and info["skill"] and info["sensitive_ops"] == ["transcode"]


_WITHOUT = """
import json, sys
sys.modules["numpy"] = None
from g6.core.registry import get_registry
reg = get_registry()
out = {op: reg.invoke("adapt_audio", op, params).unwrap() for op, params in (
    ("gain", {"source": "x.wav", "gain_db": 1}), ("loudness", {"source": "x.wav"}), ("metadata", {"source": "x.wav"}),
    ("ops", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", ""), v.get("reason", "")] for op, v in out.items()}))
"""


def test_d9_without_numpy_every_edit_says_it_is_not_installed_and_metadata_still_answers(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "media").mkdir(parents=True)
    (home / "media" / "x.wav").write_bytes(tone_wav())
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(home), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    for op in ("gain", "loudness"):
        assert found[op][:2] == ["blocked-escalated", EXTRA_MISSING] and "g6[audio]" in found[op][2]
    assert found["metadata"][0] == "qualified-draft" and found["ops"][0] is None


@REAL
def test_real_transcode_turns_a_wav_into_flac_and_back_each_inspected() -> None:
    source = store("tc", sine(440, 1.0))
    flac = _invoke("transcode", source=source, format="flac", name="tc-out")
    assert flac["completion_state"] == "qualified-draft" and flac["artifact"]["mime"] == "audio/flac", flac.get("reason")
    back = _invoke("transcode", source="tc-out.flac", format="wav", name="tc-back")
    assert back["artifact"]["measured"]["frames"] == 48000
