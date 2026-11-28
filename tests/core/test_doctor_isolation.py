# SPDX-License-Identifier: Apache-2.0
"""``g6 doctor``'s isolation check on each operating system: on Linux it starts bubblewrap's test run for real, and
says ``ok``, ``warn`` or ``fail`` with the fix for that system. Offline; the backend is a stand-in here, so nothing is
started."""
from __future__ import annotations

import re
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
    # These are the interpreter's checks: the repository the tests run in, which the doctor now also checks on Windows
    # (tests/core/test_isolation_refusal_for_agent.py), is not theirs.
    monkeypatch.setattr(doctor, "_repository_here", lambda: None)
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


def test_where_no_boundary_is_the_doctor_says_g6_refuses_until_the_person_allows_it(monkeypatch, tmp_path) -> None:
    from g6.core import choices

    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    monkeypatch.delenv(confine.REQUIRE, raising=False)
    _backend(monkeypatch, "darwin", (None, confine.NO_MACOS))
    refused = doctor._isolation()
    # fails if the doctor still says untrusted code runs with the person's permissions where G6 now refuses it, or
    # stops naming the one choice that allows it
    assert refused.status == "warn" and "so G6 refuses to run it" in refused.detail and "g6 setup" in refused.fix
    choices.record(choices.UNCONFINED, choices.ALLOW, choices.SETUP)
    allowed = doctor._isolation()
    assert "g6 setup allowed it, so it runs with your permissions" in allowed.detail


def test_on_windows_the_doctor_still_reads_only_the_access_lists(monkeypatch) -> None:
    probes = _backend(monkeypatch, "win32", ("appcontainer", ""))
    check = doctor._isolation()
    # fails if the doctor starts a test run in a container on Windows (it reads the access lists only there)
    assert probes == [False] and check.status == "ok" and "access list ok; not started" in check.detail


class _Lists:
    """Stands in for the AppContainer backend: what ALL APPLICATION PACKAGES may do on each folder (path -> mask)."""

    name = confine.APPCONTAINER

    def __init__(self, masks: dict[str, int]):
        self.masks = masks

    def _granted(self, path: str) -> int:
        return self.masks.get(path, 0)


def _windows_lists(monkeypatch, tmp_path, masks) -> list[str]:
    temp = (tmp_path / "Users" / "me" / "Temp").resolve()
    temp.mkdir(parents=True)
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setattr(doctor.tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(confine, "_select", lambda: (_Lists(masks(temp)), ""))
    return [str(folder) for folder in [*reversed(temp.parents), temp][1:]]   # the drive's top is never checked


def test_on_windows_the_doctor_says_a_confined_test_cannot_run_git_where_the_container_sees_no_folder_above(
        monkeypatch, tmp_path) -> None:
    # Dogfood pass 2 (2026-11-26): `git init -q` exits 1 in every confined test ("cannot lock ref 'HEAD': unable to
    # create directory for ...\.git\HEAD"), because Git for Windows checks each folder of the path from the drive's
    # top and the container may read none of the profile's folders above %TEMP%; the doctor said isolation ok.
    above = _windows_lists(monkeypatch, tmp_path, lambda temp: {})
    check = doctor._confined_git()
    # fails if the doctor says nothing of it (no check), or calls it ok
    assert check is not None and check.status == "warn", check
    assert "git" in check.detail and all(folder in check.detail for folder in above)
    # Owner ruling 2026-11-27, option (a): the doctor prints a one-time grant of the folder's attributes alone, for
    # each folder the container cannot see, and nothing more (no contents, no inheritance, nothing below it)
    grants = re.findall(r'icacls "[^"]*" /grant "[^"]*"', check.fix)
    assert grants == [f'icacls "{folder}" /grant "*{confine.ALL_APPLICATION_PACKAGES}:(RA)"' for folder in above], \
        check.fix
    assert "(RX)" not in check.fix and "(OI)" not in check.fix and "(CI)" not in check.fix
    # The icacls recursive switch, as its own word: a POSIX path such as .../Temp holds "/T" as text.
    assert not re.search(r"(^|\s)/T(\s|$)", check.fix)
    for name in ("_isolation", "_names"):
        monkeypatch.setattr(doctor, name, lambda: doctor.Check(name, "ok", "stand-in"))
    monkeypatch.setattr(doctor, "_components", lambda: [])
    assert check in doctor.run_checks({})   # fails if g6 doctor never prints it


def test_on_windows_git_in_confined_runs_is_ok_where_the_container_may_read_each_folder_above(
        monkeypatch, tmp_path) -> None:
    read_attributes, list_folder = 0x80, 0x1
    # every folder's own list grants its attributes, or the folder that holds it may be listed
    above = _windows_lists(monkeypatch, tmp_path, lambda temp: {
        **{str(folder): read_attributes for folder in temp.parents}, str(temp.parent): read_attributes | list_folder})
    check = doctor._confined_git()
    assert check is not None and check.status == "ok" and above, check
    # off Windows, or without the AppContainer, there is no such check
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    assert doctor._confined_git() is None
