# SPDX-License-Identifier: Apache-2.0
"""Synthetic status-line and harmless local renderer, never Claude Code."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("observer_fails", [False, True])
def test_original_bytes_stdout_stderr_and_failure_exit_survive_observer_error(tmp_path, capfdbinary, observer_fails):
    from g6._allowance.statusline import run_wrapper

    renderer = tmp_path / "synthetic renderer.py"
    renderer.write_text("import sys\np=sys.stdin.buffer.read()\nsys.stdout.buffer.write(p)\n"
                        "sys.stderr.buffer.write(b'renderer stderr')\nsys.exit(7)\n", encoding="utf-8")
    raw = b' {"rate_limits": {}, "foreign": "unchanged"}\r\n\x00\xff'
    seen = []
    def observer(data):
        seen.append(data)
        if observer_fails:
            raise OSError("synthetic observer failure")
    assert run_wrapper(raw, [sys.executable, str(renderer)], observer=observer) == 7
    out = capfdbinary.readouterr()
    assert out.out == raw
    assert out.err == b"renderer stderr" and seen == [raw]


def test_renderer_failure_does_not_prevent_observation(tmp_path):
    from g6._allowance.statusline import run_wrapper

    seen = []
    result = run_wrapper(b"synthetic", [sys.executable, "-c", "raise SystemExit(9)"], observer=seen.append)
    assert result == 9 and seen == [b"synthetic"]


@pytest.mark.parametrize("shell", ["cmd", "powershell", "sh"])
def test_preview_preserves_original_config_quoting_and_exact_rollback(shell):
    from g6._allowance.statusline import decode_renderer, preview, shell_argv

    command = '"C:/synthetic path/renderer.exe" --label "a b" | other --literal "quoted"'
    original = {"statusLine": {"type": "command", "command": command, "padding": 2}, "other": {"enabled": True}}
    p = preview(original, python="C:/synthetic path/python.exe", profile="local-test", shell=shell)
    new = json.loads(p["settings_line"])
    assert new["statusLine"]["padding"] == 2 and new["other"] == original["other"]
    assert json.loads(p["rollback_line"]) == original and p["original_config"] == original
    assert decode_renderer(p["renderer_token"]) == command
    assert shell_argv(shell, command)[-1] == command
    assert "g6._allowance.statusline" in new["statusLine"]["command"]
    assert "not established" in p["support"]


def test_unsupported_forms_and_recursive_wrapper_are_refused():
    from g6._allowance.statusline import preview

    for config in [{}, {"statusLine": {"type": "other", "command": "echo x"}},
                   {"statusLine": {"type": "command", "command": "python -m g6._allowance.statusline"}}]:
        with pytest.raises(ValueError):
            preview(config, python="python", profile="local-test")


def test_rollback_restores_renderer_without_overwriting_concurrent_settings():
    from g6._allowance.statusline import rollback

    original = {"statusLine": {"type": "command", "command": "original", "padding": 3}, "other": 1}
    current = {"statusLine": {"type": "command", "command": "wrapper"}, "other": 2}
    assert rollback(current, original) == {"statusLine": original["statusLine"], "other": 2}


def test_observer_worker_timeout_is_bounded_and_cannot_break_renderer(tmp_path, monkeypatch):
    from g6._allowance.statusline import observe_in_worker, run_wrapper

    monkeypatch.setenv("G6_HOME", str(tmp_path))
    assert observe_in_worker(b"{}", "p", "2.1.319", timeout=.000001) is False
    assert run_wrapper(b"{}", [sys.executable, "-c", "raise SystemExit(0)"],
                       observer=lambda data: observe_in_worker(data, "p", "2.1.319", timeout=.000001)) == 0


def test_worker_capture_and_malformed_or_oversized_input_do_not_renew_good_time(tmp_path, monkeypatch):
    from g6._allowance.statusline import observe_payload
    from g6._allowance.store import Store

    monkeypatch.setenv("G6_HOME", str(tmp_path))
    payload = b'{"rate_limits":{"five_hour":{"used_percentage":50,"resets_at":2000000000}},"account_id":"discard"}'
    assert observe_payload(payload, "p", "2.1.319")
    s = Store(tmp_path / "allowances")
    before = s.load("claude", "p")
    for raw in [b"", b"{", b"[]", b"x" * (1024 * 1024 + 1)]:
        assert not observe_payload(raw, "p", "2.1.319")
    after = s.load("claude", "p")
    assert after.windows[0].observed_at == before.windows[0].observed_at
    assert after.windows[0].failure and after.windows[0].attempted_at >= before.attempted_at
    assert "discard" not in s.capture_path("p").read_text(encoding="utf-8")


def test_real_sanitised_capture_admission():
    capture = Path(__file__).parents[1] / "fixtures" / "claude_statusline_2_1_319.capture.json"
    if not capture.exists():
        pytest.skip("Claude Code support is not established until a real sanitised 2.1.319 capture is checked")
    from datetime import datetime
    from g6._allowance.readers import claude_reading

    saved = json.loads(capture.read_text(encoding="utf-8"))
    assert set(saved) == {"rate_limits", "read_at"}
    r = claude_reading(saved, "capture-check", datetime.fromisoformat(saved["read_at"]), version="2.1.319")
    assert any(w.used_share is not None and not w.failure for w in r.windows)


@pytest.mark.skipif(os.name != "nt", reason="native Windows shell acceptance")
@pytest.mark.parametrize("shell", ["cmd", "powershell"])
def test_preview_runs_through_native_shell_with_spaced_paths_and_quoted_arguments(tmp_path, monkeypatch, shell):
    from g6._allowance.statusline import preview, shell_argv

    monkeypatch.setenv("G6_HOME", str(tmp_path))
    renderer = tmp_path / "synthetic renderer with spaces.py"
    renderer.write_text("import json,sys\np=sys.stdin.buffer.read()\n"
                        "sys.stdout.buffer.write(p)\nprint(json.dumps(sys.argv[1:]))\n"
                        "print('synthetic stderr', file=sys.stderr)\nraise SystemExit(7)\n", encoding="utf-8")
    if shell == "cmd":
        original = f'"{sys.executable}" "{renderer}" "a b"'
    else:
        original = f"& '{sys.executable}' '{renderer}' 'a b'"
    p = preview({"statusLine": {"type": "command", "command": original}}, python=sys.executable,
                profile="test-profile", shell=shell, invocation_shell=shell)
    command = json.loads(p["settings_line"])["statusLine"]["command"]
    raw = b'{"rate_limits":{"five_hour":{"used_percentage":40,"resets_at":2000000000}}}\r\n'
    if shell == "cmd":
        baseline = subprocess.run(original, shell=True, executable=os.environ["COMSPEC"], input=raw,
                                  capture_output=True, timeout=10)
        done = subprocess.run(command, shell=True, executable=os.environ["COMSPEC"], input=raw,
                              capture_output=True, timeout=10)
    else:
        baseline = subprocess.run(shell_argv(shell, original), input=raw, capture_output=True, timeout=10)
        done = subprocess.run(shell_argv(shell, command), input=raw, capture_output=True, timeout=10)
    assert baseline.stdout == raw + b'["a b"]\r\n' and baseline.stderr == b"synthetic stderr\r\n"
    assert (done.returncode, done.stdout, done.stderr) == (baseline.returncode, baseline.stdout, baseline.stderr)
    from g6._allowance.store import Store
    assert Store(tmp_path / "allowances").capture_path("test-profile").exists()
