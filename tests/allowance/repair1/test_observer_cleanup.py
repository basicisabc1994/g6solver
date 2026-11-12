"""Observer cleanup errors stay isolated from the renderer's streams."""
import subprocess
import sys
import threading
import base64
import io
import os
import shlex
import time

from g6._allowance import statusline


def test_observer_cleanup_failure_does_not_escape_its_thread(monkeypatch, tmp_path, capfdbinary):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    original = subprocess.Popen
    errors = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    class BrokenObserver:
        def communicate(self, **kwargs):
            raise OSError("synthetic exchange failure")
        def poll(self):
            return None
        def kill(self):
            raise OSError("synthetic cleanup failure")
        def wait(self):
            raise OSError("synthetic wait failure")
    def popen(args, **kwargs):
        return BrokenObserver() if "--observe" in args else original(args, **kwargs)
    monkeypatch.setattr(statusline.subprocess, "Popen", popen)
    code = statusline.run_wrapper(b"input", [sys.executable, "-c",
        "import sys; sys.stdout.buffer.write(b'out'); sys.stderr.buffer.write(b'err'); sys.exit(7)"],
        observer=lambda raw: statusline.observe_in_worker(raw, "p", "2.1.319", timeout=.05))
    output = capfdbinary.readouterr()
    assert (code, output.out, output.err) == (7, b"out", b"err")
    assert errors == []


def test_stalled_generation_lookup_cannot_delay_the_renderer(tmp_path, monkeypatch, capfdbinary):
    from g6._allowance import store
    class StalledStore:
        def __init__(self, root):
            time.sleep(2)
            raise OSError("synthetic stalled metadata read")
    monkeypatch.setattr(store, "Store", StalledStore)
    monkeypatch.setattr(statusline, "observe_in_worker", lambda *args, **kwargs: False)
    renderer = tmp_path / "renderer.py"
    renderer.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
                        "sys.stderr.buffer.write(b'\\xfferr')\nraise SystemExit(7)\n", encoding="utf-8")
    command = (f'"{sys.executable}" "{renderer}"' if os.name == "nt" else
               shlex.join([sys.executable, str(renderer)]))
    raw = b'\x00\xff\r\n'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    token = base64.urlsafe_b64encode(command.encode()).decode("ascii")
    start = time.monotonic()
    code = statusline.main(["--renderer=" + token, "--shell=" + ("cmd" if os.name == "nt" else "sh"), "--profile=p"])
    elapsed = time.monotonic() - start
    output = capfdbinary.readouterr()
    assert (code, output.out, output.err) == (7, raw, b'\xfferr')
    assert elapsed < 1.25
