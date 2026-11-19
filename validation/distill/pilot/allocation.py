"""Allocate the development cohort's repositories to the M1 pilot's four roles, by a rule fixed in advance.

The roles (owner ruling 2026-11-19, pilot first):
- acquisition: the only repositories whose bugs the census, the case converter and concept learning may read;
- admission: the pilot gate's acceptance evidence;
- monitor: the gate's monitor evidence, sharing no repository with admission (acceptance_gate checks this);
- reuse: untouched until M1's reuse step.

The draw orders repositories by SHA-256 of a fixed label, the cohort's own digest and the repository name, then
fills the roles in order. Nothing in it can be chosen after seeing a result: the label and role sizes are constants
here, and the digest belongs to a cohort frozen before this file existed. ``check`` re-derives the draw from the
manifest alone, so a hand edit to the manifest fails.

Usage: ``python -m validation.distill.pilot.allocation --write <development cohort json> <manifest json>`` and
``python -m validation.distill.pilot.allocation --check <manifest json>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

LABEL = "g6-pilot-m1-allocation-v1"
ROLES = (("acquisition", 6), ("admission", 5), ("monitor", 3), ("reuse", 4))


def repository_of(bug_id: str) -> str:
    """A cohort bug id is ``<repository>-<issue number>``."""
    repo, _, number = bug_id.rpartition("-")
    if not repo or not number.isdigit():
        raise ValueError(f"bug id without a repository: {bug_id!r}")
    return repo


def allocate(cohort_digest: str, repositories) -> dict[str, list[str]]:
    repos = sorted(set(repositories))
    if len(repos) != sum(n for _, n in ROLES):
        raise ValueError(f"{len(repos)} repositories; the registered roles hold {sum(n for _, n in ROLES)}")
    order = sorted(repos, key=lambda r: hashlib.sha256(f"{LABEL}|{cohort_digest}|{r}".encode()).hexdigest())
    out, i = {}, 0
    for role, n in ROLES:
        out[role] = sorted(order[i:i + n])
        i += n
    return out


def manifest_from_cohort(path) -> dict:
    path = Path(path)
    if path.name.startswith("final-"):
        raise ValueError("the final cohort is sealed; only the owner opens it")
    cohort = json.loads(path.read_text(encoding="utf-8"))
    if cohort.get("cohort") != "development":
        raise ValueError("the pilot allocates the development cohort only")
    bugs = sorted(b["id"] for b in cohort["bugs"])
    roles = allocate(cohort["digest"], (repository_of(b) for b in bugs))
    return {
        "cohort": "development",
        "cohort_digest": cohort["digest"],
        "label": LABEL,
        "roles": {role: {"repositories": repos, "bugs": [b for b in bugs if repository_of(b) in repos]}
                  for role, repos in roles.items()},
        "note": "Exploratory pilot (M1). Allocated before any distillation run read a bug. These bugs were used "
                "earlier by the worked-example study (coding agent runs), never by the distillation pipeline.",
    }


def check(manifest: dict) -> list[str]:
    errors = []
    roles = manifest.get("roles", {})
    if [r for r, _ in ROLES] != list(roles):
        return ["roles differ from the registered ones"]
    if manifest.get("label") != LABEL:
        errors.append("label differs from the registered one")
    bugs = [b for r in roles.values() for b in r["bugs"]]
    if len(bugs) != len(set(bugs)):
        errors.append("a bug sits in more than one role")
    try:
        expected = allocate(manifest["cohort_digest"], (repository_of(b) for b in bugs))
    except (KeyError, ValueError) as exc:
        return errors + [str(exc)]
    for role, repos in expected.items():
        if roles[role]["repositories"] != repos:
            errors.append(f"{role}: repositories differ from the draw")
        if {repository_of(b) for b in roles[role]["bugs"]} - set(repos):
            errors.append(f"{role}: holds a bug from another role's repository")
    if set(roles["admission"]["repositories"]) & set(roles["monitor"]["repositories"]):
        errors.append("admission and monitor share a repository")
    return errors


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", nargs=2, metavar=("COHORT", "MANIFEST"))
    g.add_argument("--check", metavar="MANIFEST")
    a = p.parse_args(argv)
    if a.write:
        m = manifest_from_cohort(a.write[0])
        Path(a.write[1]).write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8", newline="\n")
        print({r: len(v["bugs"]) for r, v in m["roles"].items()})
        return 0
    errors = check(json.loads(Path(a.check).read_text(encoding="utf-8")))
    print("\n".join(errors) or "ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
