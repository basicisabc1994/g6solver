"""g6.core.outside: programs a person installs themselves, found on this machine or refused with how to install them."""
from __future__ import annotations

import http.server
import os
import re
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from g6.core import outside
from g6.core.outside import OUTSIDE_PROGRAM_MISSING, OutsideProgram, detect, require

_BASE = {"name": "Tool", "purpose": "runs the tool", "install": "pip install tool", "licence": "MIT"}


def _program(**fields: object) -> OutsideProgram:
    return OutsideProgram(**{**_BASE, "command": "tool", **fields})  # type: ignore[arg-type]


def test_the_code_is_stable() -> None:
    assert OUTSIDE_PROGRAM_MISSING == "G6_E_OUTSIDE_PROGRAM_MISSING"


@pytest.mark.parametrize("fields", [{"command": "", "url": ""}, {"command": "tool", "url": "http://127.0.0.1:1/"}])
def test_a_program_names_a_command_or_a_url(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="command or a url"):
        OutsideProgram(**{**_BASE, **fields})


@pytest.mark.parametrize("url", ["https://127.0.0.1:8188/", "http://example.com:8188/", "http://10.0.0.5/",
                                 "http://127.0.0.1.example.com/", "file:///etc/passwd"])
def test_a_service_must_be_plain_http_on_this_machine(url: str) -> None:
    with pytest.raises(ValueError, match="this machine"):
        OutsideProgram(**_BASE, url=url)


@pytest.mark.parametrize("missing", ["name", "purpose", "install", "licence"])
def test_the_declaration_says_what_it_is_and_how_to_install_it(missing: str) -> None:
    with pytest.raises(ValueError, match=missing):
        _program(**{missing: " "})


def test_a_program_not_on_path_is_missing() -> None:
    found = detect(_program(), which=lambda name: None)
    assert (found.found, found.detail) == (False, "no tool on PATH")


def test_the_environment_variable_names_the_program() -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], timeout: float) -> tuple[int, str]:
        seen.append(argv)
        return 0, "Tool 1.2.3\nmore\n"

    found = detect(_program(env="G6_TOOL"), which=lambda name: f"/opt/{name}" if name == "mytool" else None,
                   run=run, environ={"G6_TOOL": "mytool"})
    assert (found.found, found.where, found.version) == (True, "/opt/mytool", "Tool 1.2.3")
    assert seen == [["/opt/mytool", "--version"]]


def test_an_environment_variable_naming_nothing_is_missing() -> None:
    found = detect(_program(env="G6_TOOL"), which=lambda name: None, environ={"G6_TOOL": "nothing-here"})
    assert not found.found
    assert found.detail == "G6_TOOL names nothing-here, which is not a program here"


def test_a_program_that_fails_its_version_probe_is_not_ready() -> None:
    found = detect(_program(), which=lambda name: "/bin/tool", run=lambda argv, timeout: (3, "broken"))
    assert not found.found
    assert found.detail == "/bin/tool --version exited 3: broken"


def test_a_program_that_hangs_is_not_ready() -> None:
    def run(argv: list[str], timeout: float) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(argv, timeout)

    found = detect(_program(), which=lambda name: "/bin/tool", run=run, timeout=2)
    assert not found.found
    assert found.detail == "/bin/tool --version did not answer within 2 seconds"


def test_a_real_program_is_found_with_its_version() -> None:
    found = detect(_program(command=sys.executable))
    assert found.found
    assert found.version.startswith("Python 3")


def test_the_version_check_gets_none_of_g6s_environment_but_what_a_program_needs(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # a key or token set for something else is never handed to a program the person installed
    monkeypatch.setenv("G6_TEST_CANARY", "passed-on")
    probe = "import os; print(os.environ.get('G6_TEST_CANARY', 'clean'))"
    found = detect(_program(command=sys.executable, version_args=("-c", probe)))
    assert (found.found, found.version) == (True, "clean")


def test_a_program_keeps_the_windows_shared_settings_folder() -> None:
    # Windows' OpenSSH reads its settings from %PROGRAMDATA%\ssh and exits 255 without it, so ssh-keygen could never
    # check a reviewer's signature; the folder's path is a system location, not a key or token.
    env = outside.clean_environment(environ={"PROGRAMDATA": r"C:\ProgramData", "G6_TEST_CANARY": "passed-on"})
    assert env == {"PROGRAMDATA": r"C:\ProgramData"}


def test_the_version_check_reads_a_bounded_answer() -> None:
    flood = "import sys; sys.stdout.write('v1\\n' + 'x' * 17_000_000)"
    found = detect(_program(command=sys.executable, version_args=("-c", flood)))
    assert not found.found and "could not be run" in found.detail


def test_a_program_left_in_the_current_folder_is_never_the_one_found(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows, and shutil.which there, look in the current folder before PATH: a program in the folder G6 runs in
    # (a cloned repository's, say) must not be started in place of the one the person installed. Some clients set
    # NoDefaultCurrentDirectoryInExePath, which turns that search off; G6 must not rely on its client doing so.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    name = "g6-planted-tool"
    planted = tmp_path / (f"{name}.cmd" if os.name == "nt" else name)
    planted.write_text("@echo planted\n" if os.name == "nt" else "#!/bin/sh\necho planted\n", encoding="utf-8")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    assert detect(_program(command=name)).detail == f"no {name} on PATH"
    monkeypatch.setenv("PATH", str(tmp_path))
    found = detect(_program(command=name))
    assert (found.found, found.version) == (True, "planted")


def test_programs_are_found_only_through_this_module() -> None:
    # shutil.which, and a bare program name given to subprocess, both look in the current folder first on Windows
    root = Path(outside.__file__).parent.parent
    bare = re.compile(r"""shutil\.which\(|\[\s*["'](?:git|python3?|java|lean|sage|dot|ffmpeg|swipl)["']\s*,""")
    hits = [f"{path.relative_to(root).as_posix()}:{number}" for path in sorted(root.rglob("*.py"))
            if path != Path(outside.__file__)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if bare.search(line)]
    assert hits == []


@pytest.fixture
def service() -> Iterator[str]:
    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


def test_a_running_local_service_is_found(service: str) -> None:
    found = detect(OutsideProgram(**_BASE, url=service))
    assert found.found
    assert found.detail == f"{service} answered 404"


def test_a_local_service_that_is_not_running_is_missing() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    found = detect(OutsideProgram(**_BASE, url=f"http://127.0.0.1:{port}/"), timeout=2)
    assert not found.found
    assert found.detail.startswith(f"nothing answered at http://127.0.0.1:{port}/")


def test_require_refuses_with_how_to_install() -> None:
    result = require(_program(), which=lambda name: None)
    assert result.is_fail()
    assert result.error == ("[G6_E_OUTSIDE_PROGRAM_MISSING] Tool (runs the tool) is not ready: no tool on PATH. "
                            "G6 never installs it; install it yourself: pip install tool")  # the form g6_invoke reads


def test_require_returns_what_it_found() -> None:
    result = require(_program(command=sys.executable))
    assert result.is_ok()
    assert result.value.where


def test_a_declaration_is_described_without_running_anything() -> None:
    program = _program(env="G6_TOOL", paid=True, online=True)
    assert program.describe() == {"name": "Tool", "purpose": "runs the tool", "install": "pip install tool",
                                  "licence": "MIT", "command": "tool", "url": "", "env": "G6_TOOL", "paid": True,
                                  "online": True}
