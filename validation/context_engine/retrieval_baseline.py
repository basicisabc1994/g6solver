# SPDX-License-Identifier: Apache-2.0
"""Row 03, lexical part: the Context Engine's FTS5 search against a plain substring scan of the same files.

    python validation/context_engine/retrieval_baseline.py --labels tests/knowledge/labels/retrieval.v1.json --out result.json

Both arms see exactly the same files: the ones the walker lists for the synthetic corpus (``files_seen`` is in the
result for each arm, and the run refuses when they differ). For each frozen query the engine arm returns the files
of its top passages, and the baseline arm ranks files as a person with a text search would: by how many of the
query's words occur in the file, then by how often, then by path. The scores are recall of the labelled eligible
files at 5 and at 10, the count of unsupported citations (a returned item that holds none of the query's words, or
an engine item not marked intact) and, for queries labelled as having no eligible file, how many items were returned
anyway. Nothing here calls a model or leaves the machine. The corpus and the order of everything are fixed, so two
runs give the same numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [p for p in (str(Path(__file__).resolve().parent),) if p not in sys.path]

import frozen  # noqa: E402

PROTOCOL = ROOT / "docs" / "measurements" / "context-engine" / "03-retrieval.protocol.md"
#: The digest of row 03's frozen thresholds. A protocol file whose thresholds differ is not scored against.
THRESHOLDS_SHA256 = "8517f837f79c00aacf962a4677ff4e65b15fa93debaa852aaa9790ad4e70b1f7"
_WORD = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    return list(dict.fromkeys(w.lower() for w in _WORD.findall(text)))


def substring_scan(files: dict[str, str], query: str, top_k: int) -> list[str]:
    """The files that hold any word of ``query``, best first: more distinct words, then more occurrences, then path."""
    words = _words(query)
    scored = []
    for relative, text in files.items():
        folded = text.lower()
        counts = [folded.count(w) for w in words]
        if any(counts):
            scored.append((-sum(1 for c in counts if c), -sum(counts), relative))
    return [relative for _, _, relative in sorted(scored)[:top_k]]


def _recall(returned: list[str], eligible: list[str], k: int) -> float | None:
    if not eligible:
        return None
    return len(set(returned[:k]) & set(eligible)) / len(set(eligible))


def run(labels: dict, workdir: Path) -> dict:
    """Both arms over the corpus built under ``workdir``; the result is plain data."""
    sys.path[:0] = [p for p in (str(ROOT / "src"), str(ROOT)) if p not in sys.path]
    from g6._context_engine import _engine as engine
    from g6._context_engine._envelope import SearchRequest
    from g6._context_engine._scope import ROOTS_ENV, access, host_binding
    from g6._context_engine._walk import walk
    from tests.knowledge import corpus

    project = workdir / "project"
    folder = project / "knowledge"
    corpus.build(folder)
    previous = os.environ.get("G6_HOME")
    os.environ["G6_HOME"] = str(workdir / "home")
    try:
        binding = host_binding(environ={ROOTS_ENV: str(project)}, start=project)
        walked = walk(binding, folder)
        files = {f.relative: (folder / f.relative).read_bytes().decode("utf-8", "replace") for f in walked.files}
        shown = engine.preview(binding, str(folder))
        engine.commit(binding, shown, "retrieval baseline")
        acc = access(binding, "search", "local")
        indexed = sorted(f.relative for f in shown.files)
        if indexed != sorted(files):
            raise SystemExit("the two arms do not see the same files; the comparison is refused")
        rows = []
        for item in labels["queries"]:
            query, eligible = item["query"], item["eligible_files"]
            response, _ = engine.search(acc, SearchRequest(query=query, top_k=10))
            items = response.payload["items"]
            engine_files = list(dict.fromkeys(i["path"] for i in items))
            scan_files = substring_scan(files, query, 10)
            words = _words(query)
            unsupported_engine = sum(1 for i in items if i["checked"]["outcome"] != "intact" or not any(
                w in files[i["path"]][i["start"]:i["end"]].lower() for w in words))
            unsupported_scan = sum(1 for f in scan_files if not any(w in files[f].lower() for w in words))
            rows.append({"id": item["id"], "eligible": len(eligible),
                         "fts5": {"returned": len(engine_files), "recall_at_5": _recall(engine_files, eligible, 5),
                                  "recall_at_10": _recall(engine_files, eligible, 10),
                                  "unsupported_citations": unsupported_engine,
                                  "state": response.backends[0].state},
                         "substring": {"returned": len(scan_files), "recall_at_5": _recall(scan_files, eligible, 5),
                                       "recall_at_10": _recall(scan_files, eligible, 10),
                                       "unsupported_citations": unsupported_scan}})
    finally:
        if previous is None:
            os.environ.pop("G6_HOME", None)
        else:
            os.environ["G6_HOME"] = previous

    def summary(arm: str) -> dict:
        answerable = [r for r in rows if r["eligible"]]
        absent = [r for r in rows if not r["eligible"]]
        mean = lambda key: round(sum(r[arm][key] for r in answerable) / len(answerable), 6)  # noqa: E731
        return {"queries": len(rows), "with_an_eligible_file": len(answerable), "recall_at_5": mean("recall_at_5"),
                "recall_at_10": mean("recall_at_10"),
                "unsupported_citations": sum(r[arm]["unsupported_citations"] for r in rows),
                "returned_for_queries_with_no_eligible_file": sum(r[arm]["returned"] for r in absent),
                "files_seen": len(files)}

    return {"protocol": "03-retrieval", "labels": labels["version"], "labelled_by": labels["labelled_by"],
            "fts5": summary("fts5"), "substring": summary("substring"), "rows": rows}


def admitted(result: dict) -> tuple[bool, list[str]]:
    """The protocol's lexical admission rule on a result, and each reason it is not met. A result admits only on
    independently written labels, with every measure a finite number: a missing or unknown value passes nothing."""
    why = []
    if not frozen.independent(result.get("labelled_by")):
        why.append("the labels are not independently written: a development-cohort result admits nothing")
    engine_arm, scan = result.get("fts5") or {}, result.get("substring") or {}
    measures = [arm.get(name) for arm in (engine_arm, scan) for name in ("recall_at_10", "unsupported_citations", "files_seen")]
    if not all(frozen.finite(value) for value in measures):
        return False, [*why, "a measure is missing or is not a finite number"]
    if engine_arm["recall_at_10"] < scan["recall_at_10"]:
        why.append("FTS5 recall@10 is below the substring scan's")
    if engine_arm["unsupported_citations"]:
        why.append("FTS5 returned a citation that does not hold the query's words or was not intact")
    if engine_arm["files_seen"] != scan["files_seen"]:
        why.append("the arms did not see the same files")
    return not why, why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    pinned = frozen.check(PROTOCOL, THRESHOLDS_SHA256, Path(args.labels))   # refuses a changed bar or label file
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as scratch:
        result = run(labels, Path(scratch))
    result["frozen"] = {k: pinned[k] for k in ("row", "thresholds_sha256", "labels_sha256")}
    ok, why = admitted(result)
    result["admission"] = {"met": ok, "not_met_because": why}
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("fts5", "substring", "admission")}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
