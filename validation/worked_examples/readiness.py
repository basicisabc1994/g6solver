# SPDX-License-Identifier: Apache-2.0
"""The replay cohort's readiness report (the design's section 3.6: a read-only report comes first). It says, before any run,
whether this machine can make the replay's runs, and how many cases it can make them on:

- Python: its version and implementation;
- Docker: whether its client is on the path, whether its daemon answers, its version, and the memory and CPUs it is given;
- the memory available now, against the run drivers' floor (``cohorts.FLOOR_GB``);
- per cohort, each bug's task and oracle images present or missing, and whether the oracle is an image of its own;
- whether a subscription sign-in for Claude Code exists: its existence only, never its contents, and never its path;
- per cohort, the count of usable cases, from the newest manifest written for it (``cohorts.write_manifest``).

It changes nothing: it runs only ``docker version``, ``docker info`` and ``docker image inspect``, checks one file's existence, and
reads the cohort manifests. It starts no daemon, container, model, ``claude`` or ``codex``.

``python readiness.py [--archive DIR]`` prints the report as JSON.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cohorts  # noqa: E402

#: The only docker commands the report runs, by their leading words: each reads, none changes anything.
READ_ONLY = (("version",), ("info",), ("image", "inspect"))


def read_only(run: Callable[[Sequence[str]], subprocess.CompletedProcess]) -> Callable[..., Any]:
    """A docker client, called as ``run.Docker`` is, that runs a command only when its leading words are one of ``READ_ONLY``;
    any other raises before anything runs."""
    def docker(*args: str, timeout: float | None = None, **kw: Any) -> Any:
        if not any(tuple(args[:len(words)]) == words for words in READ_ONLY):
            raise RuntimeError(f"the readiness report runs no docker {' '.join(args[:2])}")
        return run(["docker", *args])
    return docker


def sign_in_present(home: Path, exists: Callable[[Path], bool] = Path.exists) -> bool:
    """Whether Claude Code's subscription sign-in exists for this user: its credentials file is there (``.claude`` in the home
    folder). Only its existence is asked; it is never opened, read, copied or printed, and neither is its path."""
    return bool(exists(home / ".claude" / ".credentials.json"))


def docker_state(run: Callable[[Sequence[str]], subprocess.CompletedProcess], which: Callable[[str], str | None]) -> dict[str, Any]:
    """The docker client and daemon: whether the client is on the path, and, when the daemon answers, its version and the memory
    (GB) and CPUs it is given. Only ``docker version`` and ``docker info`` run."""
    if which("docker") is None:
        return {"client": False, "daemon": False, "why": "no docker client on the path"}
    got = run(["docker", "version", "--format", "{{.Server.Version}}"])
    version = (got.stdout or b"").decode("utf-8", "replace").strip()
    if got.returncode != 0 or not version:
        return {"client": True, "daemon": False, "why": "the docker daemon does not answer (is Docker Desktop running?)"}
    info = run(["docker", "info", "--format", "{{.MemTotal}} {{.NCPU}}"])
    memory, cpus = None, None
    if info.returncode == 0:
        parts = (info.stdout or b"").decode("utf-8", "replace").split()
        try:
            memory, cpus = round(int(parts[0]) / 2 ** 30, 2), int(parts[1])
        except (IndexError, ValueError):
            pass
    return {"client": True, "daemon": True, "version": version, "memory_gb": memory, "cpus": cpus}


def images(docker: Callable[..., Any]) -> dict[str, Any]:
    """Per cohort, the bugs whose task and oracle images docker holds (``cohorts.image_ids``), the bugs missing either, and the
    bugs whose oracle is not an image of its own (the same id as its task image, so the oracle would not be kept apart)."""
    out: dict[str, Any] = {}
    for name, bugs in cohorts.COHORTS.items():
        present, missing, shared = [], [], []
        for bug in bugs:
            ids = cohorts.image_ids(bug, docker)
            if ids["task_image_id"] and ids["oracle_image_id"]:
                present.append(bug)
                if ids["task_image_id"] == ids["oracle_image_id"]:
                    shared.append(bug)
            else:
                missing.append(bug)
        out[name] = {"bugs": len(bugs), "present": len(present), "missing": missing, "oracle_not_its_own": shared}
    return out


def usable(root: Path) -> dict[str, Any]:
    """Per cohort, the count of usable cases in its newest manifest in ``root`` (``cohorts.write_manifest``'s files), with the
    manifest's name and whether it holds its own digest (``cohorts.holds``); a cohort with no manifest says so."""
    out: dict[str, Any] = {}
    for name in cohorts.COHORTS:
        found = sorted(root.glob(f"{name}-*.json"), key=lambda p: p.stat().st_mtime) if root.is_dir() else []
        if not found:
            out[name] = {"manifest": None, "usable": None, "why": "no manifest has been written"}
            continue
        try:
            body = json.loads(found[-1].read_text("utf-8"))
        except (OSError, ValueError) as exc:
            out[name] = {"manifest": found[-1].name, "usable": None, "why": f"the manifest cannot be read: {exc}"}
            continue
        ok = cohorts.holds(body)
        out[name] = {"manifest": found[-1].name, "holds_its_digest": ok, "usable": len(body.get("usable") or []) if ok else None,
                     "excluded": len(body.get("excluded") or []), **({} if ok else {"why": "its digest is not its own"})}
    return out


def report(*, root: Path, home: Path, run: Callable[[Sequence[str]], subprocess.CompletedProcess],
           which: Callable[[str], str | None] = shutil.which, free: Callable[[], float | None] = cohorts.free_gb,
           exists: Callable[[Path], bool] = Path.exists) -> dict[str, Any]:
    """The whole report, from its probes. ``run`` runs one command and is given only the read-only docker commands; ``root`` is
    the cohort records' folder (``cohorts.controls_root``)."""
    docker = read_only(run)
    state = docker_state(lambda argv: docker(*argv[1:]), which)
    available = free()
    return {"python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                       "supported": sys.version_info >= (3, 11)},
            "docker": state,
            "memory": {"available_gb": None if available is None else round(available, 2), "floor_gb": cohorts.FLOOR_GB,
                       "above_floor": available is not None and available >= cohorts.FLOOR_GB},
            "images": images(docker) if state.get("daemon") else "not read: the docker daemon does not answer",
            "subscription_sign_in": "present" if sign_in_present(home, exists) else "absent",
            "usable_cases": usable(root)}


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """One command, its output as bytes, as ``run.Docker`` gives it; a command that cannot start is a failure, never a raise."""
    try:
        return subprocess.run(list(argv), capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(list(argv), 1, b"", str(exc).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", type=Path, default=None)
    args = ap.parse_args(argv)
    print(json.dumps(report(root=cohorts.controls_root(args.archive), home=Path.home(), run=_run), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
