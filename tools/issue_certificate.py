# SPDX-License-Identifier: Apache-2.0
"""Issue/check committed component certificates, offline; run from oss or its parent."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from g6.core import component_certificate as records
from g6.core import vetting
from g6.core.registry import get_registry
from g6.workforce import component_certificate as certificates

today = dt.date.today


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    # No repository hooks, external diff drivers, pager, credentials or inherited GIT_* configuration.
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    done = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, env=env, timeout=30)
    if done.returncode:
        raise ValueError("certificate history unavailable: cannot read exact inputs or a recorded commit is unreachable")
    return done.stdout


def _folder_ids(top: Path, commits: list[str], folders: list[str]) -> list[tuple[str | None, ...]]:
    """Per commit, the object id of each folder (None where it is absent), read in one git call.

    The exact comparison in :func:`history_positions` reads nothing outside these folders, so a commit whose ids equal
    the commit before it gives the same answer and need not be listed again."""
    query = "".join(f"{commit}:{folder}\n" for commit in commits for folder in folders).encode("utf-8")
    lines = _git(top, "cat-file", "--batch-check=%(objectname)", stdin=query).decode("utf-8").splitlines()
    if len(lines) != len(commits) * len(folders):
        raise ValueError("certificate history unavailable: cannot read exact inputs or a recorded commit is unreachable")
    ids: list[str | None] = []
    for line in lines:
        if line.endswith(" missing"):
            ids.append(None)
        elif len(line) in (40, 64) and all(c in "0123456789abcdef" for c in line):
            ids.append(line)
        else:
            raise ValueError("certificate history unavailable: cannot read exact inputs or a recorded commit is "
                             "unreachable")
    n = len(folders)
    return [tuple(ids[i * n:(i + 1) * n]) for i in range(len(commits))]


def history_positions(bundle: certificates.Bundle) -> certificates.Positions:
    """First-parent history order first holding each exact snapshot, never filesystem dates.

    All inputs must already be committed byte-for-byte. A retrofit's artifact
    position is computed from code/data alone, so adding later checks cannot move
    its code forward. The current code's exact bytes must be in history, and its
    position is the first commit holding any of its current files: a file removed
    later (one the code no longer has) does not move it forward, and neither does an
    edit after the checks, so checks written after the code never look as if they
    came before it. The g6 modules outside the component that its implementing
    functions import are bound by their bytes (they must be committed as they are)
    but do not move the artifact's position: a later edit to a shared helper does
    not make checks written earlier look as if they were written before the code.
    Controls, positive runs and receipts occupy deterministic
    positions after those inputs; unrelated later commits do not move them.
    Tests replace this single function with explicit Positions.
    """
    if _git(bundle.root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise ValueError("certificate history unavailable: shallow repository; fetch full history")
    top = Path(_git(bundle.root, "rev-parse", "--show-toplevel").decode("utf-8").strip())
    relative_root = bundle.root.relative_to(top).as_posix()
    prefix = "" if relative_root == "." else relative_root + "/"
    outside = set(bundle.imported)
    wanted = {"artifact": {k: v for k, v in records.code_files(bundle.files, bundle.declaration).items()
                           if k not in outside},
              **certificates.check_contents(bundle)}
    found, reached = {}, {}
    born = None   # (position, commit) of the first commit holding any current code file
    # Read file bytes only for paths in these snapshots, never secrets or unrelated content.
    commits = _git(top, "rev-list", "--first-parent", "--reverse", "HEAD").decode("ascii").splitlines()
    bases = [base for base, _ in records.input_bases(bundle.root, bundle.folder, bundle.declaration)]
    folders = sorted({prefix + base.relative_to(bundle.root).as_posix() for base in bases}
                     | {(prefix + p).rpartition("/")[0] for files in wanted.values() for p in files})
    where = {folder: i for i, folder in enumerate(folders)}
    previous, cache = None, {}
    for seq, (commit, ids) in enumerate(zip(commits, _folder_ids(top, commits, folders)), 1):
        if ids == previous:
            continue
        previous = ids
        paths = set(_git(top, "ls-tree", "-r", "--name-only", commit, "--", *folders).decode("utf-8").splitlines())
        if born is None and any(prefix + p in paths for p in wanted["artifact"]):
            born = (seq, commit)
        for name, files in wanted.items():
            if name in found or not all(prefix + p in paths for p in files):
                continue
            matches = True
            for relative, expected in sorted(files.items()):
                full = prefix + relative
                # A file's bytes cannot differ while its folder's object id is unchanged.
                key = (full, ids[where[full.rpartition("/")[0]]])
                if key not in cache:
                    cache[key] = _git(top, "show", commit + ":" + full)
                if cache[key] != expected:
                    matches = False
                    break
            if matches:
                found[name] = seq
                reached[name] = commit
        if len(found) == len(wanted):
            break
    if set(found) != set(wanted):
        raise ValueError("certificate history unavailable: exact input bytes are absent from history or a recorded commit is unreachable; commit the frozen checks/code first "
                         "(line-ending conversion also changes exact bytes)")
    for name in sorted(outside):
        if _git(top, "show", commits[-1] + ":" + prefix + name) != bundle.files[name]:
            raise ValueError(f"certificate history unavailable: imported module {name} differs from its committed "
                             "bytes; commit it first")
    last = max(found.values())
    found["artifact"], reached["artifact"] = born
    return certificates.Positions(found["artifact"], {k: v for k, v in found.items() if k != "artifact"},
                                  last + 1, last + 2, last + 3, reached)


def _package(component: str) -> str:
    if component.startswith("g6."):
        return component
    ref = get_registry().refs.get(component)
    if ref is None:
        raise ValueError(f"unknown component: {component}")
    return ref.module.split(":", 1)[0]


def _vetted() -> list[str]:
    return sorted(ref.module.split(":", 1)[0] for ref in get_registry().refs.values()
                  if vetting.declared(ref.module.split(":", 1)[0]) is not None)


def _precheck(packages: list[str], sources: dict[str, bytes], quotes: list[str] | None = None) -> int:
    """Everything the issuing tool checks, before a reader is asked: no review, no history, nothing written. The
    placeholder positions decide no freeze question, so the issuer's state here means nothing; the gaps do."""
    failed = False
    for package in packages:
        try:
            bundle = certificates.load(package, source_files=sources, quotes=quotes, review=False)
            frozen = {name: 0 for name in certificates.check_contents(bundle)}
            body = certificates.issue(bundle, certificates.Positions(1, frozen, 1, 2, 3), today=today())
        except (ValueError, OSError, KeyError, TypeError, SyntaxError) as exc:
            failed = True
            print(f"{package}: {exc}", file=sys.stderr)
            continue
        issued = body["issuer"]
        receipts = {r["check_id"]: r["result"] for r in issued["receipts"]}
        for check in issued["checks"]:
            killed = sum(c["killed"] for c in check["controls"])
            print(f"{package}: {check['check_id']}: {receipts[check['check_id']]}, "
                  f"{killed} of {len(check['controls'])} seeded defects caught")
        for obligation, code, _ in issued["obligations"]:
            failed = True
            print(f"{package}: gap: {obligation}: {code}")
        failed = failed or any(r != "pass" for r in receipts.values())
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", nargs="?")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="rerun every check/control and compare both files")
    modes.add_argument("--reviewed", action="store_true", help="print the exact hashes for an independent pass")
    modes.add_argument("--content", metavar="OUT",
                       help="write the committed certificate's content (what a qualified reviewer signs) to OUT")
    modes.add_argument("--precheck", action="store_true",
                       help="run every mechanical check and seeded defect before an independent pass exists, with "
                            "placeholder positions; print each check's result and every gap; write nothing")
    parser.add_argument("--source-file", action="append", default=[], metavar="SOURCE_ID=PATH",
                        help="owner-held source bytes for an offline corpus/retrieved-file hash")
    parser.add_argument("--quotes", metavar="PATH",
                        help='the withheld quotes of sources whose text is not published, as {"quotes": [...]}: kept '
                             "outside the public copy, matched to their cases by SHA-256, never written")
    args = parser.parse_args(argv)
    if not args.component and not (args.check or args.precheck):
        parser.error("component is required unless --check or --precheck is used")
    try:
        if args.reviewed:
            print(records.canonical(records.reviewed(_package(args.component))).decode("utf-8"), end="")
            return 0
        if args.content:
            _, folder, _ = records.layout(_package(args.component))
            body = records.read_json(folder / "certificate/certificate.json")
            Path(args.content).write_bytes(records.canonical(records.content(body)))
            print(f"{args.content}: content_sha256 {records.content_sha256(body)}")
            return 0
        sources = {}
        for item in args.source_file:
            name, separator, path = item.partition("=")
            if not separator or not name or not path or name in sources:
                raise ValueError("--source-file needs a unique SOURCE_ID=PATH")
            sources[name] = Path(path).read_bytes()
        quotes = None
        if args.quotes:
            quotes = json.loads(Path(args.quotes).read_text(encoding="utf-8")).get("quotes")
            if not isinstance(quotes, list) or not all(isinstance(q, str) and q for q in quotes):
                raise ValueError('--quotes needs a JSON file {"quotes": ["...", ...]}')
        packages = [_package(args.component)] if args.component else _vetted()
        if args.precheck:
            return _precheck(packages, sources, quotes)
        failed = False
        for package in packages:
            try:
                bundle = certificates.load(package, source_files=sources, quotes=quotes)
                positions = history_positions(bundle)
                # --check reruns offline and only compares; a fresh issue needs every page or file source's bytes.
                run = certificates._reissue if args.check else certificates.issue
                body = run(bundle, positions, today=today())
                if args.check:
                    json_path = bundle.folder / "certificate/certificate.json"
                    md_path = bundle.folder / "certificate/CERTIFICATE.md"
                    old = records.read_json(json_path)
                    # Re-decision is separate from the regenerated byte comparison.
                    from g6.evidence import certificate as issuer
                    problems = issuer.verify(old["issuer"],
                        records.artifact_bytes(records.code_files(bundle.files, bundle.declaration)))
                    if (problems or json_path.read_bytes() != records.canonical(body)
                            or md_path.read_bytes() != certificates.markdown(body) or body["stale_sources"]):
                        raise ValueError("committed certificate differs from offline checks or is stale")
                else:
                    certificates.write(bundle, body)
                print(package + ": " + body["issuer"]["state"])
            except (ValueError, OSError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError) as exc:
                failed = True
                print(f"{package}: {exc}", file=sys.stderr)
        return int(failed)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
