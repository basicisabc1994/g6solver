# SPDX-License-Identifier: Apache-2.0
"""G6's Linux backend, bubblewrap (``g6.core.confine._Bubblewrap``): the arguments it gives ``bwrap``, the flags it
reads from ``bwrap --help`` and the reason it gives when it cannot confine a run. These need no Linux: the arguments,
flags and reasons are built from facts the tests hand over. What a confined run can and cannot reach on Linux is in
``tests/core/test_confine_bwrap_linux.py``, through the ``bwrap_backend`` fixture."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="bubblewrap's folders are made with POSIX owners and "
                                                                "modes")

#: A merged /usr, as Debian 12 and Ubuntu lay it out: /bin, /lib and /lib64 are links into /usr; /libx32 is absent.
MERGED = {"/bin": "usr/bin", "/sbin": "usr/sbin", "/lib": "usr/lib", "/lib32": None, "/lib64": "usr/lib64",
          "/libx32": None}
#: An older layout: /lib is a folder of its own.
SPLIT = {"/bin": "", "/sbin": "", "/lib": "", "/lib32": None, "/lib64": "", "/libx32": None}
#: The child's own folder G6 made for one run (its ``tmp`` and ``hosts``), where G6 makes it: in the host's /tmp.
PRIVATE = "/tmp/g6_child_test"


def _args(confinement: confine.Confinement, reads: tuple[str, ...] = ("/opt/py",), *, cwd: str | None = "/tmp/run",
          disable_userns: bool = True, layout: dict | None = None, private: str = PRIVATE) -> list[str]:
    return confine.bwrap_arguments(confinement, reads, uid=1000, gid=1000, cwd=cwd, disable_userns=disable_userns,
                                   layout=MERGED if layout is None else layout, private=private)


def _pairs(args: list[str], flag: str) -> list[str]:
    """The destination of every ``flag SRC DEST`` (or ``flag DEST``) in ``args``."""
    width = {"--ro-bind": 2, "--ro-bind-try": 2, "--bind": 2, "--symlink": 2, "--proc": 1, "--dev": 1,
             "--chdir": 1, "--remount-ro": 1}[flag]
    return [args[i + width] for i, arg in enumerate(args) if arg == flag]


def _binds(args: list[str]) -> list[tuple[str, str, str]]:
    """Every bind in ``args``, in order: its flag, its source on the host and its destination inside."""
    return [(arg, args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg in ("--ro-bind", "--ro-bind-try",
                                                                                   "--bind")]


def _position(args: list[str], flag: str, dest: str) -> int:
    """Where the bind ``flag ... dest`` (or ``flag dest``) is in ``args``."""
    width = 1 if flag in ("--proc", "--dev", "--remount-ro") else 2
    return next(i for i, arg in enumerate(args) if arg == flag and args[i + width] == dest)


RUN = confine.Confinement(read_write=("/tmp/run",), read_only=("/srv/repo",))


def test_the_child_gets_new_namespaces_its_own_ids_no_capabilities_and_dies_with_g6() -> None:
    args = _args(RUN)
    # fails if a namespace, the ids, the capability drop, the parent-death kill or the new session goes
    head = args[:args.index("--ro-bind")]
    assert head == ["--unshare-all", "--unshare-user", "--uid", "1000", "--gid", "1000", "--cap-drop", "ALL",
                    "--die-with-parent", "--new-session", "--disable-userns"]
    assert "--share-net" not in args
    # fails if --disable-userns is passed to a bwrap that cannot honour it here
    assert "--disable-userns" not in _args(RUN, disable_userns=False)


def test_the_network_is_a_private_loopback_unless_asked_for() -> None:
    quiet, open_ = _args(RUN), _args(confine.Confinement(read_write=("/tmp/run",), network=True))
    # fails if the network is shared without being asked for, or not shared when it is
    assert "--share-net" not in quiet and quiet[0] == "--unshare-all"
    assert open_[:2] == ["--unshare-all", "--share-net"]
    # name resolution and certificates are readable only when the network is
    assert "/etc/resolv.conf" in _pairs(open_, "--ro-bind-try") and "/etc/resolv.conf" not in _pairs(quiet,
                                                                                                    "--ro-bind-try")


def test_the_system_is_read_only_and_only_the_run_folder_is_writable() -> None:
    args = _args(RUN)
    # fails if /usr, the interpreter or the code under test is not bound read only, or anything but the run folder and
    # the child's own /tmp (from the private folder G6 made for it) is bound writable; or, the network off, anything
    # but G6's own hosts file is bound at /etc/hosts
    assert set(_pairs(args, "--ro-bind")) == {"/usr", "/etc/hosts", "/opt/py", "/srv/repo"}
    assert ("--ro-bind", f"{PRIVATE}/hosts", "/etc/hosts") in _binds(args)
    assert _pairs(args, "--bind") == ["/tmp", "/tmp/run"]
    assert ("--bind", f"{PRIVATE}/tmp", "/tmp") in _binds(args)
    assert _pairs(args, "--symlink") == ["/bin", "/sbin", "/lib", "/lib64"]
    assert args[args.index("/lib") - 1] == "usr/lib"
    assert set(_pairs(args, "--ro-bind-try")) <= set(confine.BWRAP_ETC) and "/etc/ld.so.cache" in _pairs(
        args, "--ro-bind-try")
    assert _pairs(args, "--proc") == ["/proc"] and _pairs(args, "--dev") == ["/dev"]
    assert _pairs(args, "--chdir") == ["/tmp/run"]
    # fails if the root is left writable: the last arguments remount it read only
    assert args[-2:] == ["--remount-ro", "/"]


def test_a_writable_folder_is_bound_after_the_read_only_ones_so_it_stays_writable_inside_one() -> None:
    nested = confine.Confinement(read_write=("/tmp/top/home", "/tmp/top/report"), read_only=("/tmp/top",))
    args = _args(nested, reads=())
    # fails if a read-only folder holding a run folder is bound after it (it would cover the run folder read only)
    assert args.index("/tmp/top") < args.index("/tmp/top/home") and args.index("/tmp/top") < args.index(
        "/tmp/top/report")


def test_an_older_layout_binds_its_top_level_folders_read_only() -> None:
    args = _args(RUN, layout=SPLIT)
    # fails if a real /lib is linked to usr/lib instead of bound as it is
    assert {"/bin", "/sbin", "/lib", "/lib64"} <= set(_pairs(args, "--ro-bind")) and not _pairs(args, "--symlink")


# --- the child's own /tmp and hosts file -------------------------------------------------------------------------------

def test_the_private_folder_is_required_so_no_caller_can_leave_a_child_without_its_own_tmp() -> None:
    # fails if bwrap_arguments can be called without the child's own folder (a default would let a caller forget it)
    with pytest.raises(TypeError, match="private"):
        confine.bwrap_arguments(RUN, ("/opt/py",), uid=1000, gid=1000, cwd=None, disable_userns=False,  # type: ignore[call-arg]
                                layout=MERGED)


def test_the_childs_own_tmp_is_bound_after_the_system_and_before_the_runs_own_paths() -> None:
    args = _args(RUN)
    at = _position(args, "--bind", "/tmp")
    # fails if the child's /tmp is bound before the system's binds, or after a path the run reads or writes (a run
    # folder or a copy in the host's /tmp would be covered by it), or after the root is made read only
    system = [_position(args, "--ro-bind", "/usr"), _position(args, "--symlink", "/lib"),
              _position(args, "--ro-bind-try", "/etc/passwd"), _position(args, "--ro-bind", "/etc/hosts"),
              _position(args, "--proc", "/proc"), _position(args, "--dev", "/dev")]
    run = [_position(args, "--ro-bind", "/opt/py"), _position(args, "--ro-bind", "/srv/repo"),
           _position(args, "--bind", "/tmp/run"), _position(args, "--remount-ro", "/")]
    assert max(system) < at < min(run), args


def test_with_the_network_off_etc_hosts_is_g6s_own_file_and_with_it_on_the_hosts_as_before() -> None:
    quiet = _args(RUN)
    open_ = _args(confine.Confinement(read_write=("/tmp/run",), read_only=("/srv/repo",), network=True))
    # fails if, the network off, the host's /etc/hosts (which can name machines inside the owner's network) is bound,
    # or G6's own file is not, or is bound writable
    assert [bind for bind in _binds(quiet) if bind[2] == "/etc/hosts"] == [
        ("--ro-bind", f"{PRIVATE}/hosts", "/etc/hosts")]
    # fails if, the network on, /etc/hosts is not the host's, as it was before
    assert [bind for bind in _binds(open_) if bind[2] == "/etc/hosts"] == [("--ro-bind-try", "/etc/hosts", "/etc/hosts")]
    # with the network on the child still has its own /tmp
    assert ("--bind", f"{PRIVATE}/tmp", "/tmp") in _binds(open_)


def test_g6s_hosts_file_names_this_machine_as_localhost_and_nothing_else() -> None:
    # fails if G6's hosts file names anything but localhost on the loopback (IPv4 and IPv6)
    assert [line.split() for line in confine.BWRAP_HOSTS.splitlines()] == [["127.0.0.1", "localhost"],
                                                                           ["::1", "localhost"]]


def test_the_mount_points_in_tmp_are_the_first_folder_under_tmp_of_each_path_a_run_is_given() -> None:
    # fails if the self-check counts a path outside /tmp, or /tmp itself, as one of the run's own mount points there
    assert confine._tmp_mounts(["/tmp/run", "/tmp/top/home", "/tmp/top/report", "/usr", "/tmpfoo/x", "/var/tmp/y",
                                "/tmp"]) == {"run", "top"}


SEEN = {"planted": False, "g6": False, "home": [], "interfaces": ["lo"], "tmp_planted": False,
        "tmp": ["g6_confine_probe_x"]}


def _leaks(**seen: object) -> list[str]:
    return confine._bwrap_leaks({**SEEN, **seen}, planted="/tmp/g6_confine_planted_a/planted.txt",
                                tmp_planted="/tmp/g6_confine_planted_b.txt", home="/srv/stand-in-home",
                                g6="/srv/stand-in-home/.g6", allowed=set(), mounts={"g6_confine_probe_x"})


def test_the_self_check_holds_where_tmp_holds_only_the_runs_own_mount_points() -> None:
    # fails if a child whose /tmp holds only the mount points of its own folders is called a leak
    assert _leaks() == []
    assert _leaks(tmp=[]) == []


def test_the_self_check_fails_on_the_hosts_tmp_and_on_anything_in_tmp_the_run_was_not_given() -> None:
    # fails if bubblewrap's test run could see a file planted in the host's /tmp, or anything in its /tmp but the mount
    # points of its own folders, and G6 still used it
    assert any("/tmp/g6_confine_planted_b.txt" in leak for leak in _leaks(tmp_planted=True))
    assert any("/tmp" in leak and "g6_confine_planted_b.txt" in leak
               for leak in _leaks(tmp=["g6_confine_planted_b.txt", "g6_confine_probe_x"]))
    # the checks it had before still hold
    assert _leaks(planted=True) and _leaks(g6=True) and _leaks(home=["secret"]) and _leaks(interfaces=["eth0", "lo"])


def test_the_private_folder_is_made_outside_every_path_the_run_reads_or_writes(tmp_path, monkeypatch) -> None:
    temp, other = tmp_path / "temp", tmp_path / "other"
    temp.mkdir()
    other.mkdir()
    monkeypatch.setattr(confine.tempfile, "tempdir", str(temp))
    monkeypatch.setattr(confine, "BWRAP_PLACES", (str(other),))
    # Python's temporary folder first, where the run neither reads nor writes it
    assert confine._private_place([str(tmp_path / "repo")]) == os.path.realpath(temp)
    # fails if the child's own folder could be made inside the repository, a copy, an interpreter's folder or a run
    # folder: a file a test writes in /tmp could then change what G6 hashes or snapshots
    assert confine._private_place([str(temp)]) == os.path.realpath(other)
    assert confine._private_place([str(tmp_path)]) is None


def test_a_place_g6_may_not_write_in_is_passed_over(tmp_path, monkeypatch) -> None:
    temp, other = tmp_path / "temp", tmp_path / "other"
    temp.mkdir()
    other.mkdir()
    monkeypatch.setattr(confine.tempfile, "tempdir", str(temp))
    monkeypatch.setattr(confine, "BWRAP_PLACES", (str(other),))
    real = os.access
    monkeypatch.setattr(confine.os, "access", lambda path, mode: os.fspath(path) != str(temp) and real(path, mode))
    # fails if G6 picks a folder it cannot make the child's own folder in (the run would then not start)
    assert confine._private_place([]) == os.path.realpath(other)


def test_with_no_place_for_the_private_folder_the_run_is_refused_whatever_the_person_allowed(tmp_path,
                                                                                          monkeypatch) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(confine.tempfile, "tempdir", str(temp))
    monkeypatch.setattr(confine, "BWRAP_PLACES", ())
    chosen = confine._Bubblewrap("/usr/bin/bwrap")
    monkeypatch.setattr(chosen, "ready", lambda python, probe=True: "")
    monkeypatch.setattr(confine, "_select", lambda: (chosen, ""))
    monkeypatch.setenv(confine.ALLOW, "1")   # the person allowed unconfined runs: no way round here
    monkeypatch.delenv(confine.REQUIRE, raising=False)
    with confine.run_folder("g6_test_place_", dir=temp) as folder:
        nowhere = confine.plan(confine.Confinement(read_write=(folder,), read_only=(str(tmp_path),)))
        kept = confine.plan(confine.Confinement(read_write=(folder,)))
        try:
            # fails if a run with nowhere for its own /tmp goes ahead, unconfined or confined without it
            assert nowhere.backend is None and confine.NO_PRIVATE_TMP in nowhere.why, nowhere.why
            said = confine.refused(nowhere)
            assert said.startswith(f"[{ISOLATION_UNAVAILABLE}]") and confine.NO_PRIVATE_TMP in said, said
            assert "allowed_by" not in nowhere.record() and "TMPDIR" in said
            # the same run with a place for its folder is confined
            assert kept.backend == confine.BUBBLEWRAP and confine.refused(kept) == ""
        finally:
            assert kept.release() == ""


@posix_only
def test_a_private_folder_must_be_a_real_folder_of_g6s_own_user_with_owner_only_access(tmp_path) -> None:
    real, shared, link = tmp_path / "real", tmp_path / "shared", tmp_path / "link"
    real.mkdir()
    real.chmod(0o700)
    shared.mkdir()
    shared.chmod(0o755)
    link.symlink_to(real)
    # fails if a link, or a folder others may open, is taken as the child's own folder
    assert confine._own_folder(str(real))
    assert not confine._own_folder(str(shared)) and not confine._own_folder(str(link))
    assert not confine._own_folder(str(tmp_path / "absent"))


def _started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The commands ``subprocess.Popen`` is given from here on, none of them started."""
    commands: list[list[str]] = []
    monkeypatch.setattr(confine.subprocess, "Popen", lambda command, **options: commands.append(command))
    return commands


@posix_only
def test_each_child_gets_a_fresh_owner_only_folder_and_the_runs_end_removes_every_one(tmp_path, monkeypatch) -> None:
    commands = _started(monkeypatch)
    chosen = confine._Bubblewrap("/usr/bin/bwrap")
    with confine.run_folder("g6_test_start_", dir=tmp_path) as folder:
        run = confine._BwrapRun(confine.Confinement(read_write=(folder,)), (), str(tmp_path))
        chosen.start(["python3", "-c", "pass"], run, cwd=folder)
        chosen.start(["python3", "-c", "pass"], run, cwd=folder)
        tmps = [source for command in commands for _, source, dest in _binds(command) if dest == "/tmp"]
        # fails if two children share a /tmp, or a child's folder is not G6's own, owner only, made outside the run
        # folder, with an empty tmp and G6's hosts file
        assert len(tmps) == 2 and len(set(tmps)) == 2, commands
        for tmp in tmps:
            holder = os.path.dirname(tmp)
            assert os.path.dirname(holder) == os.path.realpath(tmp_path) and os.path.basename(tmp) == "tmp"
            assert confine._own_folder(holder) and confine._own_folder(tmp) and os.listdir(tmp) == []
            assert Path(holder, "hosts").read_text(encoding="utf-8") == confine.BWRAP_HOSTS
            assert ("--ro-bind", f"{holder}/hosts", "/etc/hosts") in _binds(commands[tmps.index(tmp)])
        # what a child may leave in its /tmp: a folder no one may open, with a file in it
        locked = Path(tmps[0], "locked")
        (locked / "inner").mkdir(parents=True)
        (locked / "inner" / "left.txt").write_text("x", encoding="utf-8")
        locked.chmod(0)
        # fails if the run's end leaves a child's folder behind
        assert run.close() == ""
        assert not any(os.path.exists(os.path.dirname(tmp)) for tmp in tmps)
        assert sorted(os.listdir(tmp_path)) == [os.path.basename(folder)]


@posix_only
def test_a_child_whose_folder_is_not_g6s_own_is_not_started_and_nothing_is_left(tmp_path, monkeypatch) -> None:
    commands = _started(monkeypatch)
    monkeypatch.setattr(confine, "_own_folder", lambda path: False)
    chosen = confine._Bubblewrap("/usr/bin/bwrap")
    with confine.run_folder("g6_test_start_", dir=tmp_path) as folder:
        run = confine._BwrapRun(confine.Confinement(read_write=(folder,)), (), str(tmp_path))
        # fails if a child starts with a /tmp that is a link, someone else's, or open to others
        with pytest.raises(OSError):
            chosen.start(["python3", "-c", "pass"], run, cwd=folder)
        assert commands == [] and sorted(os.listdir(tmp_path)) == [os.path.basename(folder)]
        assert run.close() == ""


@posix_only
@pytest.mark.parametrize(("seen", "leaked"), [({"tmp_planted": True}, True), ({"tmp": ["g6_planted_elsewhere"]}, True),
                                              ({}, False)])
def test_bubblewraps_test_run_binds_a_private_tmp_and_fails_where_its_tmp_shows_more(monkeypatch, seen, leaked) -> None:
    chosen = confine._Bubblewrap("/usr/bin/bwrap")
    monkeypatch.setattr(chosen, "_features", lambda: ([], False))
    commands: list[list[str]] = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        commands.append(command)
        cwd = Path(command[command.index("--chdir") + 1])
        mounts = [cwd.relative_to("/tmp").parts[0]] if cwd.is_relative_to("/tmp") else []
        said = {"planted": False, "g6": False, "home": [], "interfaces": ["lo"], "tmp_planted": False, "tmp": mounts}
        said.update({key: (value + mounts if key == "tmp" else value) for key, value in seen.items()})
        return subprocess.CompletedProcess(command, 0, json.dumps(said).encode(), b"")

    monkeypatch.setattr(confine.subprocess, "run", run)
    why = chosen._probe(sys.executable)
    # fails if the test run has no /tmp of its own, or plants nothing in the host's /tmp for it to look for
    assert [source for _, source, dest in _binds(commands[0]) if dest == "/tmp"], commands[0]
    assert Path(commands[0][-1]).parent == Path("/tmp") if os.path.isdir("/tmp") else True
    # fails if a test run that saw the host's /tmp, or anything in its /tmp it was not given, is let through
    assert (confine.BWRAP_LEAKED in why) is leaked and (why == "") is (not leaked), why


def _stand_in_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("G6_HOME", str(home / ".g6"))


def test_the_builder_binds_no_home_no_g6_folder_no_run_or_var_and_tmp_and_hosts_only_from_g6s_own_folder(
        tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _stand_in_home(monkeypatch, home)
    args = _args(RUN)
    bound = set(_pairs(args, "--ro-bind")) | set(_pairs(args, "--ro-bind-try")) | set(_pairs(args, "--bind"))
    # fails if the builder binds the owner's home, G6's own folder, or the host's /run or /var
    assert not bound & {str(home), str(home / ".g6"), "/run", "/var", "/home", "/root", "/"}
    # fails if /tmp is bound from anything but the private folder G6 made for the run (the host's /tmp above all), or,
    # the network off, /etc/hosts from anything but G6's own file
    sources = {dest: [source for _, source, at in _binds(args) if at == dest] for dest in ("/tmp", "/etc/hosts")}
    assert sources == {"/tmp": [f"{PRIVATE}/tmp"], "/etc/hosts": [f"{PRIVATE}/hosts"]}
    assert "/tmp" not in {source for _, source, _ in _binds(args)}


def test_a_run_that_names_a_guarded_folder_is_not_confined_and_says_why(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".g6").mkdir(parents=True)
    _stand_in_home(monkeypatch, home)
    chosen = confine._Bubblewrap("/usr/bin/bwrap")
    with confine.run_folder("g6_test_bwrap_") as folder:
        identity, why = chosen.prepare(confine.Confinement(read_write=(folder,), read_only=(str(home),)), "python3")
    # fails if bubblewrap would bind the home folder (it holds G6's own folder and the key file) into the sandbox
    assert identity is None and "home folder" in why and "key file" in why


def test_the_flags_are_read_from_bwraps_own_help() -> None:
    full = ("usage: bwrap [OPTIONS...] [--] COMMAND [ARGS...]\n" + "\n".join(
        f"    {flag}  text" for flag in (*confine.BWRAP_NEEDS, "--disable-userns")))
    # fails if a bwrap that lists every flag is refused, or --disable-userns is not seen
    assert confine.bwrap_features(full) == ([], True)
    old = "\n".join(f"    {flag}  text" for flag in confine.BWRAP_NEEDS if flag != "--cap-drop")
    # fails if a bwrap too old to drop capabilities is used anyway
    assert confine.bwrap_features(old) == (["--cap-drop"], False)


@pytest.mark.parametrize(("facts", "said"), [
    ({"program": None}, ["bubblewrap (bwrap) is not installed", "sudo apt-get install bubblewrap"]),
    ({"program": "/usr/bin/bwrap", "max_user_namespaces": "0"},
     ["user namespaces are switched off", "sudo sysctl -w user.max_user_namespaces="]),
    ({"program": "/usr/bin/bwrap", "unprivileged_userns_clone": "0"},
     ["user namespaces are switched off", "kernel.unprivileged_userns_clone=1"]),
    ({"program": "/usr/bin/bwrap", "apparmor_restrict": "1", "apparmor_profile": False},
     ["kernel.apparmor_restrict_unprivileged_userns", "/etc/apparmor.d/bwrap", "profile bwrap /usr/bin/bwrap",
      "userns,", "apparmor_parser -r /etc/apparmor.d/bwrap"]),
    ({"program": "/usr/bin/bwrap", "container": True, "stderr": "bwrap: No permissions to create new namespace"},
     ["inside a container", "--security-opt seccomp=unconfined", "No permissions to create new namespace"]),
    ({"program": "/usr/bin/bwrap", "missing": ["--cap-drop"]}, ["too old", "--cap-drop"]),
    ({"program": "/usr/bin/bwrap", "stderr": "bwrap: something else"},
     ["could not start a confined child", "bwrap: something else"]),
    ({"program": "/usr/bin/bwrap", "leaked": "the planted file"}, [confine.BWRAP_LEAKED, "the planted file"]),
])
def test_each_reason_says_what_is_wrong_and_how_to_fix_it_on_linux(facts: dict, said: list[str]) -> None:
    why = confine.bwrap_reason(facts)
    # fails if a reason loses its words or its fix
    assert why.startswith("unavailable:"), why
    for words in said:
        assert words in why, (words, why)


def test_an_apparmor_profile_already_in_place_is_not_the_reason() -> None:
    why = confine.bwrap_reason({"program": "/usr/bin/bwrap", "apparmor_restrict": "1", "apparmor_profile": True,
                                "stderr": "bwrap: setting up uid map: Permission denied"})
    # fails if G6 tells the person to add a profile they already have
    assert "apparmor_parser" not in why and "setting up uid map" in why


def test_a_posix_virtual_environment_names_the_base_installation_not_its_bin_folder(tmp_path) -> None:
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("home = /opt/hostedtoolcache/Python/3.12.7/x64/bin\nversion = 3.12.7\n", encoding="utf-8")
    # fails if interpreter_paths binds only the base's bin folder on POSIX, where the standard library is beside it
    # (lib/python3.12), so a child of the environment could not start in the sandbox
    assert confine._home(cfg, posix=True) == ["/opt/hostedtoolcache/Python/3.12.7/x64"]
    assert confine._home(cfg, posix=False) == ["/opt/hostedtoolcache/Python/3.12.7/x64/bin"]


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX installation's layout: bin beside lib")
def test_a_posix_interpreter_outside_a_virtual_environment_names_its_installation(tmp_path) -> None:
    exe = tmp_path / "opt" / "python3.12" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    exe.chmod(0o755)
    # fails if only bin is named for a python on PATH (a frozen app's, a G6_PYTHON one): its standard library is in
    # lib beside it, so its child could not start inside bubblewrap
    assert confine.interpreter_paths(str(exe)) == (str(exe.parent.parent.resolve()),)


def test_bubblewrap_is_linuxs_backend_and_says_how_to_install_it_where_it_is_missing(monkeypatch) -> None:
    import g6.core.outside

    monkeypatch.setattr(confine.sys, "platform", "linux")
    monkeypatch.setattr(confine, "_LINUX", None)
    monkeypatch.setattr(g6.core.outside, "find_program", lambda name: None)
    chosen, _ = confine._select()
    # fails if Linux has no backend chosen, or a missing bwrap is not said with how to install it
    assert chosen.name == confine.BUBBLEWRAP and chosen.program is None
    name, why = confine.backend(sys.executable)
    assert name is None and "bubblewrap (bwrap) is not installed" in why and "apt-get install bubblewrap" in why


# --- CI: bubblewrap installed on every Linux job, and its tests fail rather than skip without it --------------------------

ROOT = Path(__file__).resolve().parents[2]
SETUP = "bash tools/ci_bubblewrap.sh"


def _jobs(workflow: str) -> dict[str, str]:
    """Each job's text in a workflow file, by the job's name."""
    body = workflow.split("\njobs:\n", 1)[1]
    return {name: text for name, _, text in (block.partition(":") for block in
                                             re.split(r"(?m)^  (?=[A-Za-z][\w-]*:\s*$)", body)[1:])}


@pytest.mark.parametrize(("workflow", "job"), [("ci.yml", "test"), ("ci.yml", "conformance"),
                                               ("bundles.yml", "build")])
def test_every_linux_job_that_runs_g6_sets_up_bubblewrap(workflow: str, job: str) -> None:
    text = _jobs((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))[job]
    # fails if a Linux job that runs G6's tests or builds its apps leaves bubblewrap out, so the boundary would be
    # missing there and the bwrap tests would skip
    assert "ubuntu-latest" in text and SETUP in text, (workflow, job)
    assert text.index(SETUP) < max(text.find("tools/suite_split.ps1"), text.find("pytest"),
                                   text.find("packaging/smoke_bundle.py"))


def test_the_ci_setup_installs_bubblewrap_gives_it_its_apparmor_profile_and_expects_it() -> None:
    script = (ROOT / "tools" / "ci_bubblewrap.sh").read_text(encoding="utf-8")
    # fails if the setup stops installing bwrap, stops adding the AppArmor profile Ubuntu's runners need (their kernel
    # restricts unprivileged user namespaces), or stops telling the tests to expect the backend
    assert "apt-get install -y -qq bubblewrap" in script
    assert "/proc/sys/kernel/apparmor_restrict_unprivileged_userns" in script and '= "1"' in script
    assert "profile bwrap" in script and "userns," in script and "apparmor_parser -r /etc/apparmor.d/bwrap" in script
    assert 'echo "G6_EXPECT_BACKEND=bubblewrap" >> "$GITHUB_ENV"' in script


class _Unready:
    name = confine.BUBBLEWRAP

    def __init__(self, program: str | None = None) -> None:
        pass

    def ready(self, python: str, *, probe: bool = True) -> str:
        return "unavailable: switched off by this test"


def _unready_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine.sys, "platform", "linux")
    monkeypatch.setattr(confine, "_LINUX", None)
    monkeypatch.setattr(confine, "_Bubblewrap", _Unready)


def test_the_bwrap_fixture_fails_rather_than_skips_where_ci_expects_bubblewrap(monkeypatch, request) -> None:
    _unready_linux(monkeypatch)
    monkeypatch.setenv("G6_EXPECT_BACKEND", "bubblewrap")
    # fails if a CI job that expects bubblewrap lets its tests skip when it cannot confine a run
    with pytest.raises(pytest.fail.Exception, match="switched off by this test"):
        request.getfixturevalue("bwrap_backend")


def test_the_bwrap_fixture_skips_where_nothing_expects_bubblewrap(monkeypatch, request) -> None:
    _unready_linux(monkeypatch)
    monkeypatch.delenv("G6_EXPECT_BACKEND", raising=False)
    with pytest.raises(pytest.skip.Exception, match="switched off by this test"):
        request.getfixturevalue("bwrap_backend")
