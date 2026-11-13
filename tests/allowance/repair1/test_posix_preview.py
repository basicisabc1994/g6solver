"""Check POSIX preview's argv structurally without claiming Unix execution."""
import json
import os
import shlex
import subprocess
import sys

import pytest

from g6._allowance.statusline import preview


@pytest.mark.parametrize("profile", ["p", "-local-profile", "--", "_", "z" * 80])
def test_posix_preview_preserves_argv_and_native_renderer_bytes(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    renderer = tmp_path / "renderer with spaces.py"
    renderer.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
                        "sys.stderr.buffer.write(b'\\xfferr')\nraise SystemExit(7)\n", encoding="utf-8")
    shell = "cmd" if os.name == "nt" else "sh"
    command = (f'"{sys.executable}" "{renderer}"' if os.name == "nt" else
               shlex.join([sys.executable, str(renderer)]))
    p = preview({"statusLine": {"type": "command", "command": command}}, python=sys.executable,
                profile=profile, shell=shell, invocation_shell="sh")
    argv = shlex.split(json.loads(p["settings_line"])["statusLine"]["command"])
    raw = b'\x80\x00\xff\r\n'
    result = subprocess.run(argv, input=raw, capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (7, raw, b'\xfferr')
