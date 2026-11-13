# SPDX-License-Identifier: Apache-2.0
"""Row 07: the search planner against the lexical route alone and the fixed fusion of the ready routes, paired.

    python validation/context_engine/planner_pairs.py --labels tests/knowledge/labels/retrieval.v1.json --out result.json

Every arm reads the same published generation of the same files through the same access, with the same ``top_k``,
the same limits and no model call; ``information`` and ``budget`` are in the result for each arm, and the run
refuses when they differ. An arm whose route is not ready on this corpus (the meaning route, the relations route
without labelled relation queries) is reported ``evidence_pending`` and is never counted as having found nothing.
Nothing here calls a model or leaves the machine. The corpus and the order of everything are fixed, so two runs
give the same scores; the seconds they took are reported apart from the scores.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [p for p in (str(Path(__file__).resolve().parent),) if p not in sys.path]

import frozen  # noqa: E402

PROTOCOL = ROOT / "docs" / "measurements" / "context-engine" / "07-planner.protocol.md"
#: The digest of row 07's frozen thresholds. A protocol file whose thresholds differ is not scored against.
THRESHOLDS_SHA256 = "54bddab076b4286545450542e1e0f53ef01275281e113f52016fc09fc9396a5c"
TOP_K = 10
PENDING = {"dense": "evidence_pending: the meaning route is not ready on this corpus",
           "graph": "evidence_pending: the labels hold no relation queries"}


def _recall(returned: list[str], eligible: list[str]) -> float | None:
    return len(set(returned[:TOP_K]) & set(eligible)) / len(set(eligible)) if eligible else None


def run(labels: dict, workdir: Path) -> dict:
    """The three arms that can run over the corpus built under ``workdir``; the result is plain data."""
    sys.path[:0] = [p for p in (str(ROOT / "src"), str(ROOT)) if p not in sys.path]
    from g6._context_engine import _dispatch, _planner
    from g6._context_engine import _engine as engine
    from g6._context_engine._envelope import PassageItem, SearchRequest
    from g6._context_engine._fuse import tokens_of
    from g6._context_engine._registry import ProjectStore
    from g6._context_engine._scope import ROOTS_ENV, access, host_binding
    from tests.knowledge import corpus

    project = workdir / "project"
    folder = project / "knowledge"
    corpus.build(folder)
    previous = os.environ.get("G6_HOME")
    os.environ["G6_HOME"] = str(workdir / "home")
    try:
        binding = host_binding(environ={ROOTS_ENV: str(project)}, start=project)
        shown = engine.preview(binding, str(folder))
        engine.commit(binding, shown, "planner pairs")
        store = ProjectStore(binding)
        limits = _planner.Limits()
        ready = tuple(route.name for route, why in _dispatch.routes(store) if not why and route.name != "relations")

        budget = {"top_k": TOP_K, "searches": limits.searches, "model_calls": limits.model_calls,
                  "tokens": limits.tokens}
        arms: dict[str, dict] = {name: {"budget": dict(budget), "rows": [], "generations": set(), "over_budget": 0,
                                        "searches": 0, "model_calls": 0, "tokens": 0, "seconds": 0.0}
                                 for name in ("exact", "fixed_fusion", "planner")}
        for item in labels["queries"]:
            query, eligible = item["query"], item["eligible_files"]
            for name, arm in arms.items():
                started = time.monotonic()
                if name == "planner":
                    response, event, _ = _planner.run(access(binding, "plan", "local"), query, [], limits=limits, top_k=TOP_K)
                    spent = event.costs["searches"]
                else:
                    routes = ("exact",) if name == "exact" else ready
                    response, _ = engine.search(access(binding, "search", "local"),
                                                SearchRequest(query=query, top_k=TOP_K, routes=routes))
                    spent = len(routes)
                arm["seconds"] += time.monotonic() - started
                items = response.payload["items"]
                files = list(dict.fromkeys(i["path"] for i in items))
                cost = sum(tokens_of(PassageItem(**i)) for i in items)
                arm["searches"] += spent
                arm["tokens"] += cost
                arm["generations"].add(response.generation)
                arm["over_budget"] += cost > limits.tokens or spent > limits.searches or bool(response.payload.get("omitted"))
                arm["rows"].append({"id": item["id"], "eligible": len(eligible), "returned": len(files),
                                    "recall_at_10": _recall(files, eligible),
                                    "not_intact": sum(1 for i in items if i["checked"]["outcome"] != "intact")})
        for arm in arms.values():   # what each arm read: the generations its replies came from, of which files
            arm["information"] = {"generations": sorted(arm.pop("generations")), "files_seen": len(shown.files),
                                  "sources": len(store.registrations()), "destination": "local"}
    finally:
        if previous is None:
            os.environ.pop("G6_HOME", None)
        else:
            os.environ["G6_HOME"] = previous
    first = arms["exact"]
    if any(arm["information"] != first["information"] or len(arm["information"]["generations"]) != 1
           or arm["budget"] != first["budget"] or arm["over_budget"] for arm in arms.values()):
        raise SystemExit("the arms did not have the same information and budget; the comparison is refused")
    out: dict = {"protocol": "07-planner", "labels": labels["version"], "labelled_by": labels["labelled_by"],
                 "arms": {}, "seconds": {}}
    for name, arm in arms.items():
        answerable = [r for r in arm["rows"] if r["eligible"]]
        absent = [r for r in arm["rows"] if not r["eligible"]]
        out["arms"][name] = {
            "information": arm["information"], "budget": arm["budget"], "queries": len(arm["rows"]),
            "recall_at_10": round(sum(r["recall_at_10"] for r in answerable) / len(answerable), 6),
            "returned_for_queries_with_no_eligible_file": sum(r["returned"] for r in absent),
            "not_intact": sum(r["not_intact"] for r in arm["rows"]),
            "searches": arm["searches"], "model_calls": arm["model_calls"], "tokens": arm["tokens"]}
        out["seconds"][name] = round(arm["seconds"], 3)
    out["arms"].update({name: {"state": why} for name, why in PENDING.items()})
    return out


def admitted(result: dict) -> tuple[bool, list[str]]:
    """The protocol's rule on a result, and each reason it is not met. A development cohort admits nothing."""
    planner, fusion = (result.get("arms") or {}).get("planner") or {}, (result.get("arms") or {}).get("fixed_fusion") or {}
    why = []
    if not frozen.independent(result.get("labelled_by")):
        why.append("the labels are the development cohort's: a development result admits nothing")
    if not all(frozen.finite(arm.get(name)) for arm in (planner, fusion) for name in ("recall_at_10", "searches")):
        return False, [*why, "a measure is missing or is not a finite number"]
    limits = (result.get("frozen") or {}).get("thresholds") or {}
    if set(limits) != {"arms_with_unequal_information_or_budget", "safety_cases_failed_or_errored",
                       "planner_recall_at_10_minus_fixed_fusion_recall_at_10_min",
                       "planner_searches_minus_fixed_fusion_searches_max"}:
        return False, [*why, "the result does not record the frozen thresholds it was scored against"]
    if planner["recall_at_10"] - fusion["recall_at_10"] < limits["planner_recall_at_10_minus_fixed_fusion_recall_at_10_min"]:
        why.append("the planner's recall@10 is below fixed fusion's by more than the frozen bar")
    if planner["searches"] - fusion["searches"] > limits["planner_searches_minus_fixed_fusion_searches_max"]:
        why.append("the planner spent more searches than fixed fusion, over the frozen bar")
    if result.get("safety_cases_failed_or_errored") != limits["safety_cases_failed_or_errored"]:
        why.append("the declared safety suites' count of failed or errored cases is not recorded as the frozen bar")
    if not (planner["recall_at_10"] > fusion["recall_at_10"] or planner["searches"] < fusion["searches"]):
        why.append("no named outcome improved and no cost fell at equal recall")
    if any(arm.get("not_intact") for arm in result["arms"].values()):
        why.append("an arm returned a passage that was not intact")
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
    result["frozen"] = pinned
    # the declared safety suites are run by the test runner, not here: until their count is recorded, it is unknown
    result["safety_cases_failed_or_errored"] = "evidence_pending: the declared suites' result is recorded with the run"
    ok, why = admitted(result)
    result["admission"] = {"met": ok, "not_met_because": why}
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
