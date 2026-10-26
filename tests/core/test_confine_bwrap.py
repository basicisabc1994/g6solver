# SPDX-License-Identifier: Apache-2.0
"""G6's Linux backend, bubblewrap (``g6.core.confine._Bubblewrap``): the arguments it gives ``bwrap``, the flags it
reads from ``bwrap --help`` and the reason it gives when it cannot confine a run. These need no Linux: the arguments,
flags and reasons are built from facts the tests hand over. What a confined run can and cannot reach on Linux is in
``tests/core/test_confine_bwrap_linux.py``, through the ``bwrap_backend`` fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from g6.core import confine

#: A merged /usr, as Debian 12 and Ubuntu lay it out: /bin, /lib and /lib64 are links into /usr; /libx32 is absent.
MERGED = {"/bin": "usr/bin", "/sbin": "usr/sbin", "/lib": "usr/lib", "/lib32": None, "/lib64": "usr/lib64",
          "/libx32": None}
#: An older layout: /lib is a folder of its own.
SPLIT = {"/bin": "", "/sbin": "", "/lib": "", "/lib32": None, "/lib64": "", "/libx32": None}


def _args(confinement: confine.Confinement, reads: tuple[str, ...] = ("/opt/py",), *, cwd: str | None = "/tmp/run",
          disable_userns: bool = True, layout: dict | None = None) -> list[str]:
    return confine.bwrap_arguments(confinement, reads, uid=1000, gid=1000, cwd=cwd, disable_userns=disable_userns,
                                   layout=MERGED if layout is None else layout)


def _pairs(args: list[str], flag: str) -> list[str]:
    """The destination of every ``flag SRC DEST`` (or ``flag DEST``) in ``args``."""
    width = {"--ro-bind": 2, "--ro-bind-try": 2, "--bind": 2, "--symlink": 2, "--proc": 1, "--dev": 1,
             "--chdir": 1, "--remount-ro": 1}[flag]
    return [args[i + width] for i, arg in enumerate(args) if arg == flag]


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
    # fails if /usr, the interpreter or the code under test is not bound read only, or anything but the run folder is
    # bound writable
    assert set(_pairs(args, "--ro-bind")) == {"/usr", "/opt/py", "/srv/repo"}
    assert _pairs(args, "--bind") == ["/tmp/run"]
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


def _stand_in_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("G6_HOME", str(home / ".g6"))


def test_the_builder_binds_no_home_no_g6_folder_and_none_of_run_var_or_tmp(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _stand_in_home(monkeypatch, home)
    args = _args(RUN)
    bound = set(_pairs(args, "--ro-bind")) | set(_pairs(args, "--ro-bind-try")) | set(_pairs(args, "--bind"))
    # fails if the builder binds the owner's home, G6's own folder, or the host's /run, /var or /tmp
    assert not bound & {str(home), str(home / ".g6"), "/run", "/var", "/tmp", "/home", "/root", "/"}


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


def test_bubblewrap_is_built_but_not_yet_chosen(monkeypatch) -> None:
    monkeypatch.setattr(confine.sys, "platform", "linux")
    # fails if bubblewrap is selected before its integration tests run (A2 step 4)
    assert confine._select()[0] is None
