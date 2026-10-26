# SPDX-License-Identifier: Apache-2.0
"""``g6 doctor``'s isolation check on each operating system: on Linux it starts bubblewrap's test run for real, and
says ``ok``, ``warn`` or ``fail`` with the fix for that system. Offline; the backend is a stand-in here, so nothing is
started."""
from __future__ import annotations

import sys

import pytest

from g6.cli import doctor
from g6.core import confine


def _backend(monkeypatch: pytest.MonkeyPatch, platform: str, answer: tuple[str | None, str],
             started: bool = False) -> list[bool]:
    probes: list[bool] = []
    monkeypatch.setattr(doctor.sys, "platform", platform)
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: (probes.append(probe), answer)[1])
    monkeypatch.setattr(confine, "started", lambda python: started)
    monkeypatch.delenv("G6_PYTHON", raising=False)
    return probes


def test_on_linux_the_doctor_starts_bubblewraps_test_run_and_says_ok(monkeypatch) -> None:
    probes = _backend(monkeypatch, "linux", (confine.BUBBLEWRAP, ""), started=True)
    check = doctor._isolation()
    # fails if the doctor reads only bubblewrap's settings on Linux instead of starting its test run
    assert probes == [True]
    assert check.status == "ok" and "bubblewrap" in check.detail and "started" in check.detail, check
    assert sys.executable in check.detail


@pytest.mark.parametrize(("facts", "fix"), [
    ({"program": None}, "Install bubblewrap: sudo apt-get install bubblewrap"),
    ({"program": "/usr/bin/bwrap", "max_user_namespaces": "0"},
     "Switch user namespaces on: sudo sysctl -w user.max_user_namespaces="),
    ({"program": "/usr/bin/bwrap", "apparmor_restrict": "1"}, "Add the AppArmor profile once: printf '"),
    ({"program": "/usr/bin/bwrap", "container": True, "stderr": "bwrap: No permissions to create new namespace"},
     "Start the container with --security-opt seccomp=unconfined"),
])
def test_on_linux_each_reason_comes_with_its_own_fix(monkeypatch, facts: dict, fix: str) -> None:
    _backend(monkeypatch, "linux", (None, confine.bwrap_reason(facts)))
    check = doctor._isolation()
    # fails if the doctor's fix for Linux is not the one the reason names (a generic fix, or Windows's icacls)
    assert check.status == "warn" and check.fix.startswith(fix), check.fix
    assert "icacls" not in check.fix


def test_on_linux_a_boundary_that_leaked_is_a_failure(monkeypatch) -> None:
    _backend(monkeypatch, "linux", (None, confine.bwrap_reason({"program": "/usr/bin/bwrap",
                                                                "leaked": "the file planted outside its run folder"})))
    check = doctor._isolation()
    # fails if a bubblewrap whose test run saw a planted file is reported as a mere warning
    assert check.status == "fail" and confine.BWRAP_LEAKED in check.detail
    assert check.fix.startswith("Report it")


def test_on_macos_the_doctor_warns_and_says_what_a_mac_can_do(monkeypatch) -> None:
    probes = _backend(monkeypatch, "darwin", (None, confine.NO_MACOS))
    check = doctor._isolation()
    # fails if the macOS fix is missing, or names a Linux or Windows command
    assert probes == [False] and check.status == "warn"
    assert "virtual machine" in check.fix and "apt-get" not in check.fix and "icacls" not in check.fix


def test_on_linux_with_bubblewrap_the_doctors_own_test_run_starts_and_says_ok(bwrap_backend, monkeypatch) -> None:
    monkeypatch.delenv("G6_PYTHON", raising=False)
    check = doctor._isolation()
    # fails if the doctor reports Linux's real bubblewrap anything but ok where its test run starts and holds
    assert check.status == "ok" and check.detail.startswith("started: ") and "inside bubblewrap" in check.detail


def test_on_windows_the_doctor_still_reads_only_the_access_lists(monkeypatch) -> None:
    probes = _backend(monkeypatch, "win32", ("appcontainer", ""))
    check = doctor._isolation()
    # fails if the doctor starts a test run in a container on Windows (it reads the access lists only there)
    assert probes == [False] and check.status == "ok" and "access list ok; not started" in check.detail
