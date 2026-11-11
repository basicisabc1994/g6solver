"""An accepted opaque profile must survive the generated command line."""
import json
import os
import subprocess
import sys
import pytest


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD reproduction")
def test_preview_for_hyphen_prefixed_profile_still_runs_renderer(tmp_path, monkeypatch):
    from g6._allowance.statusline import preview
    from g6.allowance import profile_token

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    profile = profile_token('-local-profile')
    original = 'echo renderer-output'
    p = preview({'statusLine': {'type': 'command', 'command': original}},
                python=sys.executable, profile=profile, shell='cmd', invocation_shell='cmd')
    command = json.loads(p['settings_line'])['statusLine']['command']
    baseline = subprocess.run(original, shell=True, executable=os.environ['COMSPEC'],
                              input=b'{}', capture_output=True, timeout=10)
    wrapped = subprocess.run(command, shell=True, executable=os.environ['COMSPEC'],
                             input=b'{}', capture_output=True, timeout=10)
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
        baseline.returncode, baseline.stdout, baseline.stderr)
