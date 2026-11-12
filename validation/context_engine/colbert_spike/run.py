# SPDX-License-Identifier: Apache-2.0
"""One cell group of the persistent-meaning spike (protocol 05): one candidate at one corpus size on this platform.

    python validation/context_engine/colbert_spike/run.py --candidate c --size 2000 --out c-2000-windows.json

It writes a result even when the candidate cannot run: ``state`` is then ``unavailable`` with the reason, and every
measure is absent, never zero. It loads no weights that are not already in the local cache and reaches no network.
Run it on its own: the larger sizes need the machine's memory to themselves.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [p for p in (str(HERE), str(HERE.parent), str(ROOT / "src"), str(ROOT)) if p not in sys.path]

import candidates  # noqa: E402
import frozen  # noqa: E402

PROTOCOL = ROOT / "docs" / "measurements" / "context-engine" / "05-scale-and-meaning.protocol.md"


def platform_name() -> str:
    system = platform.system().lower()
    if system == "linux" and "microsoft" in platform.release().lower():
        return "wsl"
    return {"windows": "windows", "linux": "linux"}.get(system, system)


def peak_rss() -> int | None:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak) * (1 if sys.platform == "darwin" else 1024)
    except ImportError:
        try:
            import psutil

            return int(getattr(psutil.Process().memory_info(), "peak_wset", 0)) or None
        except ImportError:
            return None


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def measure(candidate: candidates.Candidate, size: int) -> dict:
    """The measures for a candidate that runs on G6's rungs: lexical candidates, then the rung's scores."""
    from g6.knowledge._types import Document, Source
    from g6.knowledge.ctx_colbert.retrieve import rank

    texts = candidates.corpus(size)
    passages = []
    for n, text in enumerate(texts):
        source = Source.of(f"spike://{n}", text.encode("utf-8"), retrieved_at="2026-11-11T00:00:00Z")
        passages.append(Document(text=text, source=source).passage(0, len(text)))
    rung = {"c": "colbert", "d": "embeddings"}[candidate.id]
    started = time.perf_counter()
    latencies, hits = [], 0
    for query, eligible in candidates.queries(size):
        words = set(query.split())
        pool = [p for p in passages if words & set(p.text.split())][:2_000]   # the bounded candidate stage
        t0 = time.perf_counter()
        ranked = rank(query, pool, rung=rung, requires_dense=True)
        latencies.append(time.perf_counter() - t0)
        top = [h.passage.id for h in ranked.searched.hits[:10]]
        hits += passages[eligible].id in top
    asked = len(latencies)
    return {"warm_p50_s": percentile(latencies[1:] or latencies, 0.5),
            "warm_p95_s": percentile(latencies[1:] or latencies, 0.95), "cold_p50_s": latencies[0],
            "cold_p95_s": latencies[0], "recall_at_10": hits / asked if asked else None,
            "peak_rss_bytes": peak_rss(), "wall_s": time.perf_counter() - started,
            "note": "this in-memory pass measures scoring only; the persistence measures (disk, update, delete, "
                    "restart, kill) are measured on slice 7's store and are absent here, which fails the envelope"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--candidate", required=True, choices=sorted(candidates.CANDIDATES))
    parser.add_argument("--size", required=True, type=int, choices=candidates.SIZES)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    candidate = candidates.CANDIDATES[args.candidate]
    result = {"protocol": "05-scale-and-meaning", "candidate": candidate.id, "size": args.size,
              "platform": platform_name(), "python": sys.version.split()[0], "machine": platform.machine(),
              "state": "unavailable", "reason": "", "measures": {},
              # the pins of the model that ran; empty until the run's adapter reads them from the weights it loaded,
              # and report.py names no winner from a run without them
              "pins": dict(getattr(candidate, "pins", None) or {}),
              "thresholds_sha256": frozen.digest(frozen.block(PROTOCOL)["thresholds"])}
    why = candidate.ready()
    if why:
        result["reason"] = why
    else:
        try:
            result["measures"] = {k: v for k, v in measure(candidate, args.size).items() if v is not None}
            result["state"] = "ran"
        except Exception as exc:  # noqa: BLE001 - a failed cell is recorded as failed, with why, never dropped
            result.update(state="failed", reason=f"{type(exc).__name__}: {exc}"[:300])
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("candidate", "size", "platform", "state", "reason")}))
    return 0 if result["state"] == "ran" else 2


if __name__ == "__main__":
    raise SystemExit(main())