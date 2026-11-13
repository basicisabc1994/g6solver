# SPDX-License-Identifier: Apache-2.0
"""The real-cluster check of the managed Elasticsearch writer and reader (protocol 03, slice 5).

    python tools/elastic_real_cluster_check.py --out docs/measurements/context-engine/03-retrieval.real-cluster.result.json

Run by a person, on its own: no builder, outside run or review beside it. It expects a single-node Elasticsearch the
operator started on this machine with a 1 GiB heap (``ES_JAVA_OPTS=-Xms1g -Xmx1g``, ``discovery.type=single-node``),
reachable on loopback; it starts, installs and stops nothing. It refuses to run with less than 4 GiB of free memory.
It writes synthetic passages only (the test corpus), into the index G6 manages for a scratch project, reads them
back, searches, counts, removes them again, and writes what it measured: the server's version, which of the features
the managed path uses answered, per-item acknowledgements, read-back, and time and index bytes per 10,000 passages.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_FREE_BYTES = 4 * 1024 ** 3
FEATURES = ("_bulk", "_mget", "alias", "term", "terms", "multi_match", "value_count", "terms_aggregation")


def free_memory() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except ImportError:
        try:
            import os

            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, ValueError, OSError):
            return None


def refusal(free: int | None) -> str:
    """Why the check will not run now; ``""`` when it may."""
    if free is None:
        return "free memory cannot be read on this machine, so the check does not start"
    if free < MIN_FREE_BYTES:
        return f"{free} bytes of memory are free; the check needs {MIN_FREE_BYTES} and runs on its own"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--passages", type=int, default=10_000)
    args = parser.parse_args(argv)
    why = refusal(free_memory())
    if why:
        print(why)
        return 2
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from g6.knowledge._types import Document, Source
    from g6.knowledge.ctx_elastic import elastic, managed, writer
    from g6._context_engine import _jobs

    project = "real-cluster-check-" + str(int(time.time()))
    result: dict = {"protocol": "03-retrieval", "kind": "real cluster", "passages": args.passages, "features": {},
                    "state": "failed"}
    try:
        result["server"] = elastic._call("GET", "/", None, None).get("version", {})
        reader = managed.ManagedElasticIndex(project, 1)
        if reader.ready():
            raise SystemExit("no Elasticsearch answered on loopback; start one and run again")
        target = writer.pin(project)
        result["features"]["alias"] = True
        passages = []
        for n in range(args.passages):
            text = f"synthetic passage {n} about the quokka convention number {n % 97}"
            source = Source.of(f"g6-knowledge://src-{'0' * 16}/p{n}.md", text.encode(), retrieved_at="2026-11-11T00:00:00Z")
            passages.append(Document(text=text, source=source).passage(0, len(text)))
        records = [managed.encode(p, "src-" + "0" * 16, 1) for p in passages]
        started = time.perf_counter()
        acks = writer.write(target, records, gate=_jobs.elastic_gate())
        result["write_seconds"] = round(time.perf_counter() - started, 3)
        result["acknowledged"] = sum(a.state == "acknowledged" for a in acks)
        result["features"]["_bulk"] = result["acknowledged"] == len(records)
        elastic._call("POST", f"/{target.concrete_index}/_refresh", None, None)
        back = writer.read_back(target, [p.id for p in passages])
        result["read_back"] = sum(back.values())
        result["features"]["_mget"] = result["read_back"] == len(passages)
        found = managed.ManagedElasticIndex(project, 1, registrations=["src-" + "0" * 16]).find("quokka convention", 10)
        result["search_outcome"] = found.outcome
        result["features"].update({"multi_match": found.outcome == "ok", "term": found.outcome == "ok",
                                   "terms": found.outcome == "ok"})
        stats = elastic._call("GET", f"/{target.concrete_index}/_stats/store", None, None)
        result["index_bytes"] = stats["_all"]["primaries"]["store"]["size_in_bytes"]
        consent = writer.DeletionConsent(registration_id="src-" + "0" * 16, attribution_sha256="0" * 64,
                                         passage_ids=tuple(p.id for p in passages))
        removed = writer.remove(target, [p.id for p in passages], consent=consent, gate=_jobs.elastic_gate())
        result["removed"] = sum(a.state == "acknowledged" for a in removed)
        result["state"] = "ran"
        result["not_exercised_here"] = ["value_count", "terms_aggregation", "API keys"]
    except Exception as exc:  # noqa: BLE001 - the result file says what stopped the check
        result["stopped_by"] = f"{type(exc).__name__}: {exc}"[:300]
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("state", "server", "acknowledged", "read_back", "stopped_by")}))
    return 0 if result["state"] == "ran" else 1


if __name__ == "__main__":
    raise SystemExit(main())