# SPDX-License-Identifier: Apache-2.0
"""The persistent-meaning spike's instrument (protocol 05): a missing or partial run stays in the denominator and
fails its candidate, and a candidate with no weights is reported unavailable, never as zero. No weights are loaded."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "context_engine" / "colbert_spike"))

import candidates  # noqa: E402
import report  # noqa: E402
import run as spike_run  # noqa: E402


def inside(cid, size, platform, **over):
    measures = {"cold_p50_s": 0.5, "cold_p95_s": 0.9, "warm_p50_s": 0.1, "warm_p95_s": 0.4, "peak_rss_bytes": 2 * 2**30,
                "disk_bytes": 10_000_000, "recall_at_10": 0.95, "ingest_per_s": 50.0, "update_per_s": 40.0,
                "delete_per_s": 400.0, "re_encoded_after_restart": 0, "partial_after_kill": 0, **over}
    return {"candidate": cid, "size": size, "platform": platform, "state": "ran", "measures": measures}


def grid(cid, **over):
    return [inside(cid, size, platform, **over) for size in candidates.SIZES for platform in candidates.PLATFORMS]


def test_report_refuses_a_run_with_missing_cells():
    whole = report.decide(grid("c") + grid("d", peak_rss_bytes=3 * 2**30))
    assert whole["decision"] == "c" and whole["cells"] == 36 and whole["cells_missing_or_failed"] == 18
    one_missing = report.decide(grid("c")[:-1] + grid("d", peak_rss_bytes=3 * 2**30))
    assert one_missing["decision"] == "d", "a candidate with a missing cell is not inside the envelope"
    partial = grid("c")
    del partial[0]["measures"]["disk_bytes"]
    assert report.decide(partial)["decision"] == "none: repair and rerun"
    assert report.decide([])["decision"] == "none: repair and rerun" and report.decide([])["cells_missing_or_failed"] == 36
    for bad in ({"peak_rss_bytes": 5 * 2**30}, {"warm_p95_s": 2.5}, {"recall_at_10": 0.5},
                {"re_encoded_after_restart": 3}, {"partial_after_kill": 1}, {"disk_bytes": 3 * 2**30}):
        assert report.decide(grid("c", **bad))["decision"] == "none: repair and rerun", bad
    failed = grid("c")
    failed[4] = {**failed[4], "state": "failed", "reason": "MemoryError"}
    found = report.decide(failed)
    assert found["decision"].startswith("none") and found["cells_missing_or_failed"] == 28
    assert any(row["problems"] == ["failed: MemoryError"] for row in found["table"])


def test_no_weights_is_reported_unavailable_not_zero(tmp_path, monkeypatch):
    stub = candidates.Candidate("c", "stub", lambda: "the weights are not in the local cache", "g6")
    monkeypatch.setitem(candidates.CANDIDATES, "c", stub)
    out = tmp_path / "c.json"
    assert spike_run.main(["--candidate", "c", "--size", "2000", "--out", str(out)]) == 2
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["state"] == "unavailable" and result["measures"] == {} and "local cache" in result["reason"]
    assert report.cell_problems(result, 2000, report.thresholds()) == ["unavailable: the weights are not in the local cache"]
    for cid in ("a", "b"):
        assert candidates.CANDIDATES[cid].ready(), "an adapter that is owed says so, and is never run as if it existed"


def test_the_workload_is_fixed():
    assert candidates.corpus(50) == candidates.corpus(50) and candidates.queries(50) == candidates.queries(50)
    texts = candidates.corpus(50)
    assert all(len(t.split()) == candidates.WORDS_PER_PASSAGE for t in texts)
    for query, eligible in candidates.queries(50, count=10):
        rare = [w for w in query.split() if w[1:2].isdigit()]
        assert rare and all(w in texts[eligible] and sum(w in t.split() for t in texts) == 1 for w in rare)
    limits = report.thresholds()
    assert limits["peak_rss_bytes_max"] == 4 * 2**30 and limits["recall_at_10_vs_exhaustive_maxsim_min"] == 0.9