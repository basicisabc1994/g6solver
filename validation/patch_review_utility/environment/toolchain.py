# SPDX-License-Identifier: Apache-2.0
"""G6's pinned toolchain for V9's arms that run G6 (arm 3, arm 4, and later ones): a self-contained Python 3.12,
G6 installed from a wheel built at the frozen commit, git 2.31 or newer, and bubblewrap, all under ``/opt/g6``.

The task images keep the repositories' own interpreters and their dependencies (the system Python, no virtual
environment), and some carry a git older than G6 accepts. The toolchain is therefore built apart and mounted
read-only at ``/opt/g6``; the image itself is never changed. Its ``bin`` folder holds only git and bubblewrap, so the
image's own ``python`` stays the one on ``PATH``; G6's interpreter is ``/opt/g6/python/bin/python3``.

**Sources** (``toolchain.json``): every file with its URL, its size and the SHA-256 its publisher gives; the two builder
images by digest. ``fetch`` puts them in a new, empty folder (from a local copy when one is given, else from the URL)
and checks each against its hash and size before anything uses it; a file that does not match is removed. ``build``
checks them again.

**Build** (``build``, Docker): the G6 wheel is built with ``uv build`` from ``git archive`` of the frozen commit,
never from a working tree. Git and bubblewrap are compiled statically in the pinned Alpine image
(``build_static.sh``), so they run whatever C library the task image has. The Python build is unpacked and the verified
wheels installed, offline, in the pinned glibc image (``install_python.sh``). Everything lands in a new Docker
volume, which must not exist yet. The lock file records the commit, the wheel's hash, the tools' versions and the
tree digest of the volume (``digest``, computed inside a container by the toolchain's own Python); ``check`` compares a
volume with its lock before a study uses it.

    python toolchain.py fetch --into <new folder> [--cache <folder of copies>]
    python toolchain.py build --sources <that folder> --commit <frozen commit> --volume <new volume name> \\
        --lock <lock file> [--repository <G6 checkout>]
    python toolchain.py check --volume <name> --lock <lock file>
    python toolchain.py digest <folder>

Standard library only: the ``digest`` command runs inside a container under the toolchain's Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "toolchain.json"
MOUNT = "/opt/g6"
PYTHON = f"{MOUNT}/python/bin/python3"
#: The scripts that build the toolchain, whose hashes the lock records.
SCRIPTS = ("build_static.sh", "install_python.sh", "toolchain.py")
_HEX = re.compile(r"[0-9a-f]{64}")


def sources(manifest: dict) -> list[dict]:
    """Every source file of the manifest, in a fixed order: Python, git, bubblewrap, then the wheels."""
    return [manifest["python"], manifest["git"], manifest["bubblewrap"], *manifest["wheels"]]


def load(path: str | Path = MANIFEST) -> dict:
    """The manifest, or ``ValueError`` when any source or builder is not pinned."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        entries = sources(manifest)
        builders = manifest["builders"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"the manifest lacks {exc}") from exc
    for entry in entries:
        if not (isinstance(entry.get("file"), str) and "/" not in entry["file"] and "\\" not in entry["file"]
                and str(entry.get("url", "")).startswith("https://") and _HEX.fullmatch(str(entry.get("sha256", "")))
                and isinstance(entry.get("size"), int) and entry["size"] > 0 and entry.get("role")):
            raise ValueError(f"source {entry.get('file')!r} is not pinned by an https URL, a SHA-256 and a size")
    if set(builders) != {"static", "glibc"} or not all("@sha256:" in image for image in builders.values()):
        raise ValueError("both builder images are pinned by digest")
    if len({entry["file"] for entry in entries}) != len(entries):
        raise ValueError("two sources share a file name")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(manifest: dict, folder: str | Path) -> list[str]:
    """What is wrong with the sources in ``folder``: each missing file, wrong size or wrong SHA-256."""
    problems = []
    for entry in sources(manifest):
        path = Path(folder) / entry["file"]
        if not path.is_file():
            problems.append(f"{entry['file']} is missing")
        elif path.stat().st_size != entry["size"]:
            problems.append(f"{entry['file']} has {path.stat().st_size} bytes, not {entry['size']}")
        elif _sha256(path) != entry["sha256"]:
            problems.append(f"{entry['file']} does not have its pinned SHA-256")
    return problems


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "g6-v9-toolchain"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def fetch(manifest: dict, into: str | Path, *, cache: str | Path | None = None, download=_download) -> list[dict]:
    """Each source into ``into`` (new, or empty), from ``cache`` when it holds the file, else downloaded; each checked
    against its size and SHA-256 at once, and removed, with nothing else fetched, when it does not match."""
    into = Path(into)
    if into.exists() and any(into.iterdir()):
        raise ValueError(f"{into} must be a new or empty folder: downloads go to their own folder")
    into.mkdir(parents=True, exist_ok=True)
    found = []
    for entry in sources(manifest):
        target = into / entry["file"]
        copy = Path(cache) / entry["file"] if cache else None
        if copy is not None and copy.is_file():
            shutil.copyfile(copy, target)
            origin = "local copy"
        else:
            target.write_bytes(download(entry["url"]))
            origin = entry["url"]
        size, digest = target.stat().st_size, _sha256(target)
        if size != entry["size"] or digest != entry["sha256"]:
            target.unlink()
            raise ValueError(f"{entry['file']} from {origin} does not match its pinned SHA-256 and size; removed")
        found.append({"file": entry["file"], "sha256": digest, "size": size, "from": origin})
    return found


def tree_digest(root: str | Path) -> str:
    """SHA-256 over every entry under ``root``, in path order: a folder by its name, a link by its target, a file by
    its contents and whether it is executable."""
    root = Path(root)
    records = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                records.append(f"{rel}\0link\0{os.readlink(path)}")
            elif path.is_dir():
                records.append(f"{rel}\0dir\0")
            else:
                executable = bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                records.append(f"{rel}\0file\0{int(executable)}\0{_sha256(path)}")
    return hashlib.sha256("\n".join(sorted(records)).encode("utf-8", "surrogateescape")).hexdigest()


# --- the build, in Docker ---------------------------------------------------------------------------------------------

def builder_commands(manifest: dict, *, sources: Path, wheels: Path, volume: str, scripts: Path = HERE) -> list[list[str]]:
    """The two builder runs: static git and bubblewrap (Alpine, which needs its package mirror), then Python and the
    wheels (glibc, offline). Sources and scripts are read-only; only the new volume is written."""
    builders = manifest["builders"]
    return [
        ["docker", "run", "--rm", "-v", f"{sources}:/sources:ro", "-v", f"{scripts}:/scripts:ro",
         "-v", f"{volume}:{MOUNT}", builders["static"], "sh", "/scripts/build_static.sh", manifest["git"]["file"],
         manifest["bubblewrap"]["file"]],
        ["docker", "run", "--rm", "--network", "none", "-v", f"{sources}:/sources:ro", "-v", f"{wheels}:/wheels:ro",
         "-v", f"{scripts}:/scripts:ro", "-v", f"{volume}:{MOUNT}", builders["glibc"], "sh",
         "/scripts/install_python.sh", manifest["python"]["file"]],
    ]


def _inspect_command(manifest: dict, volume: str, *words: str) -> list[str]:
    return ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:{MOUNT}:ro", "-v", f"{HERE}:/scripts:ro",
            manifest["builders"]["glibc"], *words]


def volume_digest(manifest: dict, volume: str, run=subprocess.run) -> str:
    done = run(_inspect_command(manifest, volume, PYTHON, "-I", "/scripts/toolchain.py", "digest", MOUNT),
               capture_output=True, text=True, check=True)
    return done.stdout.strip()


def versions(manifest: dict, volume: str, run=subprocess.run) -> dict:
    said = run(_inspect_command(manifest, volume, "sh", "-c",
                                f"{MOUNT}/bin/git --version; {MOUNT}/bin/bwrap --version; {PYTHON} -I -c "
                                "\"import importlib.metadata as m, sys; print(sys.version.split()[0]); "
                                "print(m.version('g6'))\""),
               capture_output=True, text=True, check=True).stdout.split("\n")
    return {"git": said[0].strip(), "bubblewrap": said[1].strip(), "python": said[2].strip(), "g6": said[3].strip()}


def _wheel(repository: Path, commit: str, out: Path, run=subprocess.run) -> Path:
    """G6's wheel from ``git archive`` of ``commit``, never from a working tree."""
    with tempfile.TemporaryDirectory(prefix="v9-g6-src-") as folder:
        archive, source = Path(folder) / "g6.tar", Path(folder) / "src"
        run(["git", "-C", str(repository), "archive", "--format=tar", "-o", str(archive), commit], check=True)
        with tarfile.open(archive) as handle:
            handle.extractall(source, filter="data")
        run(["uv", "build", "--wheel", "--out-dir", str(out), str(source)], check=True, capture_output=True)
    built = sorted(out.glob("g6-*.whl"))
    if len(built) != 1:
        raise ValueError("the G6 build made no single wheel")
    return built[0]


def build(manifest: dict, *, downloads: Path, commit: str, volume: str, lock: Path, repository: Path,
          run=subprocess.run) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("the commit is the full 40-character hash of G6's frozen commit")
    problems = verify(manifest, downloads)
    if problems:
        raise ValueError("the sources do not match the manifest: " + "; ".join(problems))
    if run(["docker", "volume", "inspect", volume], capture_output=True).returncode == 0:
        raise ValueError(f"the volume {volume} exists already: a toolchain is built into a new one")
    with tempfile.TemporaryDirectory(prefix="v9-wheels-") as folder:
        wheels = Path(folder)
        wheel = _wheel(repository, commit, wheels, run)
        for entry in manifest["wheels"]:
            shutil.copyfile(Path(downloads) / entry["file"], wheels / entry["file"])
        run(["docker", "volume", "create", volume], check=True, capture_output=True)
        for command in builder_commands(manifest, sources=Path(downloads), wheels=wheels, volume=volume):
            run(command, check=True)
        found = {"study": "V9", "commit": commit, "volume": volume,
                 "manifest_sha256": _sha256(MANIFEST), "sources": [{"file": e["file"], "sha256": e["sha256"]}
                                                                    for e in sources(manifest)],
                 "scripts_sha256": {name: _sha256(HERE / name) for name in SCRIPTS},
                 "builders": manifest["builders"], "g6_wheel": {"file": wheel.name, "sha256": _sha256(wheel)},
                 "versions": versions(manifest, volume, run), "tree_sha256": volume_digest(manifest, volume, run)}
    Path(lock).write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return found



def check(manifest: dict, *, volume: str, lock: Path, run=subprocess.run) -> list[str]:
    """What differs between a toolchain volume and its lock (nothing, when it may be used)."""
    recorded = json.loads(Path(lock).read_text(encoding="utf-8"))
    problems = []
    if recorded.get("volume") != volume:
        problems.append(f"the lock is for the volume {recorded.get('volume')!r}, not {volume!r}")
    digest = volume_digest(manifest, volume, run)
    if digest != recorded.get("tree_sha256"):
        problems.append("the volume's tree digest is not the one its lock records")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("fetch")
    one.add_argument("--into", required=True)
    one.add_argument("--cache")
    two = commands.add_parser("build")
    for flag in ("--sources", "--commit", "--volume", "--lock"):
        two.add_argument(flag, required=True)
    two.add_argument("--repository", help="the G6 checkout holding the commit (default: the one holding this script)")
    three = commands.add_parser("check")
    three.add_argument("--volume", required=True)
    three.add_argument("--lock", required=True)
    four = commands.add_parser("digest")
    four.add_argument("folder")
    args = parser.parse_args(argv)
    if args.command == "digest":
        print(tree_digest(args.folder))
        return 0
    manifest = load()
    if args.command == "fetch":
        print(json.dumps(fetch(manifest, args.into, cache=args.cache), indent=2))
    elif args.command == "build":
        repository = Path(args.repository) if args.repository else HERE.parents[2]
        print(json.dumps(build(manifest, downloads=Path(args.sources), commit=args.commit, volume=args.volume,
                               lock=Path(args.lock), repository=repository), indent=2))
    else:
        problems = check(manifest, volume=args.volume, lock=Path(args.lock))
        print("\n".join(problems) or "the volume matches its lock")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
