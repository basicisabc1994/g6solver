"""Acquisition membership is checked before a cohort or image is opened."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import allocation


def acquisition_bug(manifest: dict, bug_id: str) -> str:
    """Return the registered ID, or refuse without inspecting any bug body."""
    try:
        errors = allocation.check(manifest)
        if manifest.get("cohort") != "development":
            errors.append("development allocation required")
    except (KeyError, TypeError, ValueError, AttributeError):
        errors = ["malformed manifest"]
    if errors:
        raise ValueError("invalid allocation: " + "; ".join(errors))
    repo = allocation.repository_of(bug_id)
    for role, group in manifest["roles"].items():
        if repo in group["repositories"]:
            if role != "acquisition":
                raise ValueError(f"acquisition access refused: role={role}")
            if bug_id not in group["bugs"]:
                break
            return bug_id
    raise ValueError("acquisition access refused: role=unallocated")


def load_bug(manifest: dict, cohort_path: Path, bug_id: str) -> dict:
    acquisition_bug(manifest, bug_id)
    path = Path(cohort_path)
    if path.name.lower().startswith("final-"):
        raise ValueError("the final cohort is sealed; only the owner opens it")
    if path.is_symlink() or path.is_junction():
        raise ValueError("linked cohort refused")
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("cohort") != "development" or body.get("digest") != manifest["cohort_digest"]:
        raise ValueError("cohort differs from allocation")
    # Other rows supply only IDs for the envelope binding; their payloads are never returned.
    ids = [row["id"] for row in body["bugs"]]
    registered = [b for group in manifest["roles"].values() for b in group["bugs"]]
    if len(ids) != len(set(ids)) or set(ids) != set(registered):
        raise ValueError("cohort differs from allocation: bug IDs")
    return next(row for row in body["bugs"] if row["id"] == bug_id)


def write_json(path: Path, body) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def arguments(description: str, *, convert: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("allocation.json"))
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--bug", action="append", required=convert)
    parser.add_argument("--output", type=Path, required=not convert)
    return parser


def selected(manifest: dict, requested: list[str] | None) -> list[str]:
    # Do not reach into even a malformed role map before the common check.
    try:
        first = next(iter(manifest["roles"]["acquisition"]["bugs"]))
    except (KeyError, TypeError, StopIteration):
        first = "missing-0"
    acquisition_bug(manifest, first)
    ids = list(requested if requested is not None else manifest["roles"]["acquisition"]["bugs"])
    for bug_id in ids:
        acquisition_bug(manifest, bug_id)
    return ids


def main(argv=None) -> int:
    parser = arguments(__doc__)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        ids = selected(manifest, args.bug)
        rows = [load_bug(manifest, args.cohort, bug)["id"] for bug in ids]
        write_json(args.output, {"acquisition": rows})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
