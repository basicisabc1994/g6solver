"""A child's library path must not depend on its loop's working folder."""
import os
import subprocess

import pytest

from g6.mcp import improve


@pytest.mark.skipif(os.name != "nt", reason="Windows Python's home uses Lib/encodings")
def test_relative_pythonhome_cannot_run_a_library_planted_in_the_loop(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    encodings = root / "runtime" / "Lib" / "encodings"
    encodings.mkdir(parents=True)
    marker = root / "foreign-stdlib-ran"
    # Python loads encodings during startup, before G6 can check its binding.
    # No installed library is copied or inspected: this is an import-only trap.
    (encodings / "__init__.py").write_text(
        "import _io\n"
        f"_io.open({str(marker)!r}, 'wb').write(b'foreign encodings executed')\n"
        "raise RuntimeError('review import trap; no runner is started')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONHOME", "runtime")
    real_popen = subprocess.Popen
    children = []

    def import_probe(command, **kwargs):
        # Retain the real launcher's Python and startup flags; replace only its
        # entry point so this test never starts a runner or performs an act.
        flag = "-m" if "-m" in command else "-c"
        child = real_popen(command[:command.index(flag)] + ["-c", "pass"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(improve.subprocess, "Popen", import_probe)
    executed = []
    try:
        for launcher in ("act", "runner"):
            if marker.exists():
                marker.unlink()
            if launcher == "act":
                improve._run_act(root, {}, {"act": "enrol", "folder": str(root), "binding": {}, "who": "Ada"})
            else:
                child = improve._spawn(root, {})
                child.wait(timeout=15)
            if marker.exists():
                executed.append(launcher)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=15)
    assert not executed, f"foreign encodings ran through relative PYTHONHOME in {executed}"
