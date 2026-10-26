# SPDX-License-Identifier: Apache-2.0
"""adapt_voice: every voice is a WAV written into the media folder and inspected from disk (it decodes, is not silent,
and lasts a plausible time for its text), whatever the engine said (D1 audio_check_failed, ADAPT_VOICE-06); a clip is
transcribed only when it holds speech, and a transcript with more words than its speech allows is refused (D1
wrong_transcript); Coqui is not here and nothing is verified (ADAPT_VOICE-01, -02); a paid voice or transcription is
sent only on the action gate's allow, with the key from the person's key file (ADAPT_VOICE-03, -05); eSpeak NG runs as
a stand-in program through the same child-process path, and the paid services through transport doubles."""
from __future__ import annotations

import ast
import json
import math
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from media_kit import outside_link, put, stand_in, tone_wav

import g6.media.adapt_voice as package
from g6.core import discovery, http
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media._types import (
    BACKEND_FAILED,
    DURATION_MISMATCH,
    INPUT_INVALID,
    OUTSIDE_MISSING,
    PAID_NOT_ALLOWED,
    SILENT,
    TRANSCRIPT_DOUBTFUL,
    media_root,
)
from g6.media.adapt_voice import block, engines

SRC = Path(package.__file__).parent
PUBLIC = "93.184.216.34"
SECRET = "sk-test-not-a-real-key-0123456789"

ESPEAK = r'''
import json, math, sys, wave
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
if args == ["--version"]:
    print("eSpeak NG text-to-speech: 1.51-stand-in  Data at: here"); sys.exit(0)
if args == ["--voices"]:
    print("Pty Language       Age/Gender VoiceName          File                 Other Languages")
    print(" 5  en-gb           --/M      English_(Great_Britain) gmw/en")
    print(" 5  fr-fr           --/M      French_(France)    roa/fr"); sys.exit(0)
mode = json.loads((here / "mode.json").read_text()) if (here / "mode.json").exists() else {}
text = sys.stdin.buffer.read().decode("utf-8")
with open(here / "calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "text": text}) + "\n")
if mode.get("fail"):
    sys.stderr.write("espeak-ng: no such voice\n"); sys.exit(1)
out = args[args.index("-w") + 1]
seconds = {"short": 0.01}.get(mode.get("write"), 0.07 * len(text))
rate = 22050
level = 0 if mode.get("write") == "silent" else 8000
frames = b"".join(int(level * math.sin(2 * math.pi * 180 * i / rate)).to_bytes(2, "little", signed=True)
                  for i in range(int(seconds * rate)))
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
'''


class Program:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, **mode: object) -> None:
        (self.folder / "mode.json").write_text(json.dumps(mode), encoding="utf-8")

    def calls(self) -> list[dict]:
        log = self.folder / "calls.jsonl"
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@pytest.fixture
def espeak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Program:
    monkeypatch.setenv("G6_ESPEAK", str(stand_in(tmp_path, "espeak-ng", ESPEAK)))
    found = Program(tmp_path)
    found.behave()
    return found


class Wire:
    """A transport double for g6.core.http: answers each request with ``answer(request)`` and records it."""

    def __init__(self, answer: Callable[[http.Request], http.Response]) -> None:
        self.answer, self.sent = answer, []

    def __call__(self, request: http.Request) -> http.Response:
        self.sent.append(request)
        return self.answer(request)


def _pcm(seconds: float, rate: int, *, silent: bool = False) -> bytes:
    level = 0 if silent else 9000
    return b"".join(int(level * math.sin(2 * math.pi * 200 * i / rate)).to_bytes(2, "little", signed=True)
                    for i in range(int(seconds * rate)))


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Wire]:
    def attach(name: str, answer: Callable[[http.Request], http.Response],
               resolver: Callable[[str, int], list[str]] | None = None) -> Wire:
        wire = Wire(answer)
        client = http.Client(transport=wire, resolver=resolver or (lambda host, port: [PUBLIC]), min_interval=0.0)
        monkeypatch.setattr(engines.ENGINES[name], "client", client)
        return wire
    return attach


def _reply(status: int, body: bytes, headers: dict | None = None) -> Callable[[http.Request], http.Response]:
    return lambda request: http.Response(status=status, headers=headers or {}, body=body, url=request.url)


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_voice", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_voice", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


TEXT = "Hello from G6, a short sentence to say aloud."


# -- D1 (audio_check_failed): a voice that is silent or too short for its text is refused -----------------------------

def test_d1_a_voice_espeak_made_and_called_done_but_silent_is_refused_and_removed(espeak: Program) -> None:
    espeak.behave(write="silent")
    before = _media_files()
    out = _invoke("speak", text=TEXT, name="d1-silent")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == SILENT, out.get("reason")
    assert _media_files() == before and check_envelope(out) == []


def test_d1_a_voice_too_short_for_its_text_is_refused(espeak: Program) -> None:
    espeak.behave(write="short")
    out = _invoke("speak", text=TEXT, name="d1-short")
    assert out["code"] == DURATION_MISMATCH and "too short" in out["reason"]


def test_d1_a_paid_voice_of_silence_is_refused_after_it_was_paid_for(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    service("openai", _reply(200, _pcm(2.0, 24000, silent=True), {"content-type": "audio/pcm"}))
    out = _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True, name="d1-paid-silent")
    assert out["code"] == SILENT and "d1-paid-silent.wav" not in _media_files()


# -- D1 (wrong_transcript): silence is never sent; a transcript that does not fit its sound is refused ---------------

def test_d1_a_silent_clip_is_refused_before_anything_is_sent_or_paid_for(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    wire = service("openai", _reply(200, json.dumps({"text": "Thank you for watching."}).encode()))
    put("hush.wav", tone_wav(seconds=2.0, silent=True))
    out = _invoke("transcribe", source="hush.wav", allow_paid=True)
    assert out["code"] == SILENT and wire.sent == []


def test_d1_more_words_than_the_speech_allows_is_a_doubtful_transcript(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    words = " ".join(["word"] * 40)
    service("openai", _reply(200, json.dumps({"text": words, "language": "english", "duration": 1.0,
                                              "segments": [{"start": 0, "end": 1, "text": words, "avg_logprob": -0.2,
                                                            "no_speech_prob": 0.01}]}).encode()))
    put("one-second.wav", tone_wav(seconds=1.0))
    out = _invoke("transcribe", source="one-second.wav", allow_paid=True)
    assert out["code"] == TRANSCRIPT_DOUBTFUL and "40 words" in out["reason"]


def test_p1_adapt_voice_01_a_wrong_transcript_of_a_plausible_length_passes_and_the_words_say_so(service,
                                                                                                key_file) -> None:
    # Fails if the package's first line, its block's purpose or its skill again claim more than the two checks that run
    # (block.py: "if not words and speech >= MIN_SPEECH_WITH_WORDS" and "if rate > MAX_WORDS_PER_SECOND").
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    service("openai", _reply(200, json.dumps({"text": "Thank you for watching."}).encode()))
    put("noise.wav", tone_wav(seconds=2.0))  # a tone, not those words: nothing compares the words with the sound
    out = _invoke("transcribe", source="noise.wav", allow_paid=True)
    assert out["completion_state"] == "qualified-draft" and out["text"] == "Thank you for watching."
    first = package.__doc__.strip().splitlines()[0]  # the summary the site shows
    assert "empty" in first and "impossible speaking rate" in first
    assert "a wrong one of a plausible length passes" in first and "each checked against its sound" not in first
    purpose = block.AdaptVoiceBlock.__g6_block_contract__["purpose"]
    assert "each transcript checked against the clip" not in purpose
    assert "not whether its words are what was said" in purpose
    skill = (SRC / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "each transcript checked against the sound" not in skill.split("---", 2)[1]  # the skill's description
    catches = skill.split("## Failure modes it catches", 1)[1].split("## ", 1)[0]
    wrong = catches.split("- `wrong_transcript`", 1)[1].split("\n- ", 1)[0]
    assert "only an empty transcript and an impossible speaking rate" in " ".join(wrong.split())
    assert "a wrong transcript of a plausible length passes" in " ".join(wrong.split()).lower()


def test_p1_adapt_voice_01_the_failure_mode_title_names_only_what_is_checked() -> None:
    # Fails if core/data/failure_modes/media.json's wrong_transcript title again says a transcript is matched with what
    # was said: its only guard, adapt_voice, checks an empty transcript and an impossible speaking rate.
    title = FailureModeRegistry.title("wrong_transcript")
    assert title == "a transcript too empty or too long for the speech it came from"
    skill = " ".join((SRC / "skill" / "SKILL.md").read_text(encoding="utf-8").split())
    assert f"`wrong_transcript` ({title})" in skill


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_to_a_recording_outside_the_media_folder_is_never_read_or_sent(service, key_file, tmp_path: Path,
                                                                              kind: str) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    wire = service("openai", _reply(200, json.dumps({"text": "Hello there."}).encode()))
    outside_link(tmp_path, "meeting.wav", tone_wav(seconds=2.0), kind)
    out = _invoke("transcribe", source="meeting.wav", allow_paid=True)
    assert out["code"] == INPUT_INVALID and "link" in out["reason"] and wire.sent == []


@pytest.mark.parametrize("said", [" ", "", "\n\t "])
def test_d1_an_empty_transcript_of_a_clip_with_measured_speech_is_doubtful(service, key_file, said: str) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    service("openai", _reply(200, json.dumps({"text": said}).encode()))
    put("spoken.wav", tone_wav(seconds=2.0))
    out = _invoke("transcribe", source="spoken.wav", allow_paid=True)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == TRANSCRIPT_DOUBTFUL, out.get("reason")
    assert "no words" in out["reason"] and check_envelope(out) == []


def test_a_transcript_that_fits_is_returned_with_the_services_own_signals_named(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    wire = service("openai", _reply(200, json.dumps({
        "text": "Hello there.", "language": "english", "duration": 2.0,
        "segments": [{"start": 0, "end": 2, "text": "Hello there.", "avg_logprob": -0.31, "no_speech_prob": 0.02}]
    }).encode()))
    put("hello.wav", tone_wav(seconds=2.0))
    out = _invoke("transcribe", source="hello.wav", allow_paid=True)
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_TRANSCRIBED"
    assert out["text"] == "Hello there." and out["segments"][0]["no_speech_prob"] == 0.02
    assert "confidence" not in json.dumps(out)  # ADAPT_VOICE-02, -04: no confidence the service did not give
    (sent,) = wire.sent
    assert sent.url == "https://api.openai.com/v1/audio/transcriptions" and SECRET in sent.headers["Authorization"]
    assert SECRET not in json.dumps(out)


# -- the local voice --------------------------------------------------------------------------------------------------

def test_espeak_speaks_from_stdin_with_an_allowed_voice_and_the_result_is_inspected(espeak: Program) -> None:
    out = _invoke("speak", text="-v xx --and-a-flag; rm -rf /", voice="en-gb", wpm=150, name="spoken", overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == "G6_E_MEDIA_PROBED"
    (call,) = espeak.calls()
    assert call["text"] == "-v xx --and-a-flag; rm -rf /" and "--stdin" in call["args"]
    assert call["args"][call["args"].index("-v") + 1] == "en-gb" and "rm" not in call["args"]
    assert out["artifact"]["measured"]["rate"] == 22050 and out["engine"] == "espeak"


@pytest.mark.parametrize("voice", ["en; rm", "-w", "../x", "en gb", "a" * 40])
def test_a_voice_that_is_not_a_voice_code_is_refused(voice: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("speak", text="hi", voice=voice)


def test_voices_lists_what_espeak_says(espeak: Program) -> None:
    out = _invoke("voices")
    assert out["warning_card"]["code"] == "G6_E_MEDIA_REPORTED" and [v["code"] for v in out["voices"]] == ["en-gb",
                                                                                                          "fr-fr"]


def test_g4_a_missing_or_failing_espeak_is_a_refusal_with_its_reason(espeak: Program, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    espeak.behave(fail=True)
    assert _invoke("speak", text=TEXT)["code"] == BACKEND_FAILED
    monkeypatch.setenv("G6_ESPEAK", str(tmp_path / "absent.exe"))
    out = _invoke("speak", text=TEXT)
    assert out["code"] == OUTSIDE_MISSING and "G6 never installs it" in out["reason"]


# -- paid voices: the action gate decides each request; the key comes from the key file -------------------------------

def test_p1_without_the_key_in_the_file_nothing_is_sent_and_the_reply_says_which_line_to_add(service,
                                                                                              key_file) -> None:
    key_file("")
    wire = service("elevenlabs", _reply(200, _pcm(2.0, 22050)))
    out = _invoke("speak_paid", text=TEXT, engine="elevenlabs", voice="21m00Tcm4TlvDq8ikWAM", allow_paid=True)
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == []
    gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
    assert "ELEVENLABS_API_KEY" in gate["required_user_message"] and out["evidence"][0]["kind"] == "dry_run"


def test_with_the_key_in_the_file_the_real_gate_approves_and_one_request_is_sent(service, key_file) -> None:
    key_file(f"ELEVENLABS_API_KEY={SECRET}\n")
    wire = service("elevenlabs", _reply(200, _pcm(3.0, 22050)))
    out = _invoke("speak_paid", text=TEXT, engine="elevenlabs", voice="21m00Tcm4TlvDq8ikWAM", allow_paid=True,
                  name="paid-voice", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
    assert gate["decision"] == "allow_with_notice" and "G6_E_KEY_FILE_APPROVED" in gate["reason_codes"]
    (sent,) = wire.sent
    assert sent.url.startswith("https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM")
    assert sent.headers["xi-api-key"] == SECRET and SECRET not in json.dumps(out)
    assert out["artifact"]["measured"]["rate"] == 22050


def test_g3_allow_paid_holds_a_request_back_and_never_sends_one_alone(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    wire = service("openai", _reply(200, _pcm(2.0, 24000)))
    assert _invoke("speak_paid", text=TEXT, engine="openai")["code"] == PAID_NOT_ALLOWED and wire.sent == []
    key_file("")
    assert _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)["code"] == PAID_NOT_ALLOWED
    assert wire.sent == []


def test_with_the_key_in_the_file_a_gate_that_holds_still_sends_nothing(service, key_file,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    wire = service("openai", _reply(200, _pcm(2.0, 24000)))
    monkeypatch.setattr(block.gate, "ask", lambda action, key: {
        "kind": "action_gate", "gate": "a test's stand-in", "action_type": "payment", "decision": "require_confirmation",
        "completion_state": "blocked-escalated", "reason_codes": ["G6_E_CONFIRMATION_REQUIRED"],
        "required_user_message": "a person decides", "required_confirmation_phrase": None, "audit_id": ""})
    out = _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)
    assert out["code"] == PAID_NOT_ALLOWED and wire.sent == [] and "a person decides" in out["reason"]


def test_a_key_in_the_environment_approves_nothing(service, key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file("")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    wire = service("openai", _reply(200, _pcm(2.0, 24000)))
    assert _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)["code"] == PAID_NOT_ALLOWED
    assert wire.sent == []


@pytest.mark.parametrize("field", [{"api_key": SECRET}, {"approved": True}, {"completion_state": "verified"},
                                   {"engine": "coqui"}, {"engine": "edge-tts"}, {"engine": "pyttsx3"}])
def test_p0_g3_coqui_online_engines_keys_and_claims_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("speak_paid", text="hi", **field)


def test_g4_a_service_error_or_an_error_page_sent_as_200_is_a_refusal(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    service("openai", _reply(500, b'{"error": {"message": "overloaded"}}'))
    assert _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)["code"] == BACKEND_FAILED
    service("openai", _reply(200, b'{"error": {"message": "quota"}}', {"content-type": "application/json"}))
    assert _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)["code"] == BACKEND_FAILED


def test_g9_a_redirect_to_this_machine_and_a_403_are_refused(service, key_file) -> None:
    key_file(f"OPENAI_API_KEY={SECRET}\n")
    service("openai", _reply(302, b"", {"location": "http://127.0.0.1:9/steal"}))
    assert _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)["code"] == BACKEND_FAILED
    service("openai", _reply(403, b"forbidden"))
    out = _invoke("speak_paid", text=TEXT, engine="openai", allow_paid=True)
    assert out["code"] == BACKEND_FAILED and "403" in out["reason"]


# -- G1, G5-G8, D2, D4, D6 -----------------------------------------------------------------------------------------------

def test_p0_g1_nothing_is_verified_and_no_coqui_ships() -> None:
    banned = ("tts", "coqui", "edge_tts", "pyttsx3", "faster_whisper")
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names] + [
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not [m for m in imported if m.split(".")[0].lower() in banned], path.name
        said = (ast.get_docstring(tree) or "").strip()
        words = [n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value.strip() != said and "is not part of g6" not in n.value.lower()
                 and n.value not in block.NOT_HERE]  # the names it refuses, and why
        assert not [w for w in words if "coqui" in w or w == "verified" or "edge-tts" in w], path.name
    assert not set(engines.ENGINES) & {"coqui", "xtts", "tts", "edge_tts", "pyttsx3"}


def test_g5_every_op_that_runs_espeak_or_reaches_a_service_is_sensitive() -> None:
    assert set(package.__g6_sensitive_ops__) == {"speak", "speak_paid", "transcribe", "voices"}
    for op in package.__g6_sensitive_ops__:
        denied = invoke_reply("adapt_voice", op, json.dumps({"text": "hi", "source": "x.wav"}),
                              allow_sensitive=frozenset())
        assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", op
    reply = invoke_reply("adapt_voice", "engines", "{}")
    assert reply["ok"] and reply["result"]["warning_card"]["code"] == "G6_E_MEDIA_DESCRIBED"


def test_g6_g7_g8_keys_only_as_declarations_and_nothing_to_install_but_espeak() -> None:
    assert {k.name for k in package.__g6_keys__} == {"OPENAI_API_KEY", "ELEVENLABS_API_KEY"}
    assert all(k.paid for k in package.__g6_keys__)
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("getenv", "os.environ", "litellm", "mock", "fake", "simulat", "experimental", "all rights reserved",
                     "httpx", "socket", "subprocess", "faster_whisper"):
            assert word not in text, (path.name, word)
    assert package.__g6_extras__ == {} and [p.name for p in package.__g6_outside__] == ["eSpeak NG"]
    assert "command line" in package.__g6_outside__[0].licence


def test_d2_it_declares_both_failure_modes_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("audio_check_failed", "wrong_transcript", "unchosen_paid_service")
    assert package.__g6_introduces__ == () and "g6.media.adapt_voice" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_voice") is block.AdaptVoiceBlock
    assert block.VoiceInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 5
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2].startswith("_") or node.module.split(".")[2] == "adapt_voice"


def test_d5_an_engine_from_outside_the_package_is_used(key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file("")

    class Local:
        name, local, network, paid, key = "test_local", True, False, False, None

        def speak(self, text: str, voice: str, d) -> tuple[bytes, dict]:
            return tone_wav(seconds=0.07 * len(text), rate=16000), {"engine": self.name}

    engines.register(Local())
    try:
        assert "test_local" in [e["name"] for e in _invoke("engines")["engines"]]
    finally:
        engines.unregister("test_local")


def test_d6_an_agent_finds_it_by_its_failure_modes_and_describes_it() -> None:
    found = [r["component"] for r in discovery.find(FailureModeRegistry.title("wrong_transcript"))]
    assert "adapt_voice" in found
    info = describe_reply("adapt_voice")
    assert info["family"] == "media" and info["skill"] and {k["name"] for k in info["keys"]} == {
        "OPENAI_API_KEY", "ELEVENLABS_API_KEY"}
    assert os.environ.get("G6_MCP_SENSITIVE") is None
