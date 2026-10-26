# SPDX-License-Identifier: Apache-2.0
"""How ``component_catalogue.json`` was made: transformed once from the extension tracker (2026-10-20, FW-04).

The tracker (``docs/transition/extension_tracker/*.csv``, DECISIONS #37) lives in the source repository, outside the
public tree, so this script runs only there. It is kept beside the data as the record of how the data was made; the
data is not regenerated from the archive any more (``scripts/oss_transition/component_catalogue.py`` is retired).

What it does to the catalogue, row by row, sorted by name:

- a tracker DROP is removed; its name is kept in ``dropped`` so the site tests can check none is listed;
- the owner's HOLD (``mastery_advisor``) and every catalogue row the tracker does not have are kept but hidden
  (``hidden`` names why), with no family and no package;
- every other row takes its ``family`` (one of the 15, or ``core`` for a row planned into a core package) and its
  ``package`` (where its code lands) from the tracker. A MERGE row names the package it joins; the page shows it under
  that package, never as an extension of its own;
- ``summary``, ``failure_mode`` and ``needs`` are the catalogue's existing words. Nothing says whether a row is in the
  library: the page asks ``g6.core.registry`` when it renders, and an entry in the library is described from its own
  package;
- ``vetting`` is the tracker's ``vetting_required`` kinds (handoff §4): a row in one of those areas is listed as in the
  library only once its package's certificate is current (``g6.core.vetting``; no qualified person's review is awaited,
  DECISIONS #39), and a package that leaves its declaration out still waits.

    python -I oss/web/data/from_tracker.py [--tracker DIR] [--catalogue PATH]

Running it again on its own output writes the same file.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TRACKER = REPO / "docs" / "transition" / "extension_tracker"
CATALOGUE = HERE / "component_catalogue.json"
MADE = "2026-10-20"
VERDICT = "verdict_after_owner_2026_10_20"
FAMILIES = ("media", "ml", "agents", "knowledge", "memory", "sources", "deploy", "workspace", "code", "embodied",
            "cogarch", "provers", "domains", "jobs", "integrations")


def read_tracker(folder: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for path in sorted(folder.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["component"] in rows:
                    raise SystemExit(f"{path.name}: {row['component']} is in the tracker twice")
                rows[row["component"]] = row
    return rows


def _hidden(old: dict, held: bool) -> str:
    if held:
        return "held by the owner"
    if old.get("hidden"):                       # already transformed: keep the reason
        return old["hidden"]
    why = old.get("hold") or ("already in the core harness" if old.get("kind") == "core" else "no reason recorded")
    return f"not in the extension tracker: {why}"


def transform(tracker: dict[str, dict[str, str]], catalogue: dict) -> dict:
    old = {row["name"]: row for row in catalogue["components"]}
    dropped = sorted({*catalogue.get("dropped", ()), *(n for n, r in tracker.items() if r[VERDICT] == "DROP")})
    held = {n for n, r in tracker.items() if r[VERDICT] == "HOLD"}
    missing = sorted(set(tracker) - set(old) - set(dropped))
    if missing:
        raise SystemExit(f"tracker rows with no catalogue text: {missing}")
    rows = []
    for name in sorted(set(old) - set(dropped)):
        text = {"summary": old[name]["summary"], "failure_mode": old[name]["failure_mode"],
                "needs": list(old[name]["needs"])}
        if name in tracker and name not in held:
            row = tracker[name]
            vetting = sorted(k.strip() for k in row["vetting_required"].split(";") if k.strip())
            rows.append({"name": name, "family": row["family"], "package": row["package"], **text,
                         "vetting": vetting, "hidden": None})
        else:
            rows.append({"name": name, "family": None, "package": None, **text, "vetting": [],
                         "hidden": _hidden(old[name], name in held)})
    listed = [r for r in rows if not r["hidden"]]
    for r in listed:
        family, package, name = r["family"], r["package"], r["name"]
        top = package.split(".")[1]
        assert family in FAMILIES or (family == "core" and top not in FAMILIES), name
    own = {r["package"] for r in listed if r["family"] in FAMILIES and r["package"].rsplit(".", 1)[-1] == r["name"]}
    for r in listed:                            # a row that is not its own extension joins one, or its family
        if r["family"] in FAMILIES and r["package"] not in own:
            assert r["package"] == f"g6.{r['family']}", r["name"]
    counts = {"rows": len(rows), "listed": len(listed), "hidden": len(rows) - len(listed), "dropped": len(dropped),
              "family": dict(sorted(Counter(r["family"] for r in listed).items()))}
    return {"generated": MADE, "source": "the extension tracker (DECISIONS #37), by data/from_tracker.py",
            "counts": counts, "dropped": dropped, "components": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--tracker", type=Path, default=TRACKER)
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    args = ap.parse_args(argv)
    doc = transform(read_tracker(args.tracker), json.loads(args.catalogue.read_text(encoding="utf-8")))
    args.catalogue.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(doc["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
