"""PowerShell's explicit renderer exit must survive the invocation shell."""
import json
import subprocess
import sys
import os
import pytest


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell reproduction")
def test_powershell_preview_preserves_explicit_nonzero_exit(tmp_path, monkeypatch):
    from g6._allowance.statusline import preview, shell_argv

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    original = "[Console]::Out.Write('status'); [Console]::Error.Write('renderer-error'); exit 7"
    p = preview({'statusLine': {'type': 'command', 'command': original}},
                python=sys.executable, profile='p', shell='powershell', invocation_shell='powershell')
    command = json.loads(p['settings_line'])['statusLine']['command']
    baseline = subprocess.run(shell_argv('powershell', original), input=b'{}',
                              capture_output=True, timeout=10)
    wrapped = subprocess.run(shell_argv('powershell', command), input=b'{}',
                             capture_output=True, timeout=10)
    assert (baseline.returncode, baseline.stdout, baseline.stderr) == (7, b'status', b'renderer-error')
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
        baseline.returncode, baseline.stdout, baseline.stderr)
