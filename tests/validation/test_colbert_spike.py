# SPDX-License-Identifier: Apache-2.0
"""The persistent-meaning spike's instrument (protocol 05): a missing or partial run stays in the denominator and
fails its candidate, and a candidate with no weights is reported unavailable, never as zero. No weights are loaded."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPIKE = ROOT / "validation" / "context_engine" / "colbert_spike"
sys.path.insert(0, str(SPIKE))

import candidates  # noqa: E402
import report  # noqa: E402

# The spike's ``run.py`` under a name of its own, never as ``run``: the worked examples' tests import their own ``run``
# by that name in the same process, and whichever was imported first would be the one they all got.
_spec = importlib.util.spec_from_file_location("colbert_spike_run", SPIKE / "run.py")
spike_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spike_run)


PINS = {name: f"pinned-{name}" for name in candidates.REQUIRED_PINS}
NONE = "none: repair and rerun"


def inside(cid, size, platform, **over):
    measures = {"cold_p50_s": 0.5, "cold_p95_s": 0.9, "warm_p50_s": 0.1, "warm_p95_s": 0.4, "peak_rss_bytes": 2 * 2**30,
                "disk_bytes": 10_000_000, "recall_at_10": 0.95, "ingest_per_s": 50.0, "update_per_s": 40.0,
                "delete_per_s": 400.0, "re_encoded_after_restart": 0, "partial_after_kill": 0, **over}
    return {"candidate": cid, "size": size, "platform": platform, "state": "ran", "measures": measures,
            "pins": dict(PINS), "thresholds_sha256": report.THRESHOLDS_SHA256}


def grid(cid, **over):
    return [inside(cid, size, platform, **over) for size in candidates.SIZES for platform in candidates.PLATFORMS]


def whole(**over):
    return [cell for cid in sorted(candidates.CANDIDATES) for cell in grid(cid, **over)]


def test_report_names_a_winner_only_from_a_whole_grid_inside_the_frozen_envelope():
    found = report.decide(whole())
    assert found["decision"] == "a" and found["cells"] == 36 and found["cells_missing_or_failed"] == 0
    assert found["refused_because"] == [] and found["thresholds_sha256"] == found["frozen_thresholds_sha256"]
    cheaper = [c for cid in "abc" for c in grid(cid, peak_rss_bytes=3 * 2**30)] + grid("d")
    assert report.decide(cheaper)["decision"] == "d", "the cheapest candidate inside the whole envelope"
    # cells missing or failed are over the frozen bar of 0: no winner, whatever the cells that ran say
    half = report.decide(grid("c") + grid("d", peak_rss_bytes=3 * 2**30))
    assert half["decision"] == NONE and half["cells_missing_or_failed"] == 18 and half["inside_the_envelope"] == ["c", "d"]
    assert any("missing or failed" in why for why in half["refused_because"])
    assert report.decide([])["decision"] == NONE and report.decide([])["cells_missing_or_failed"] == 36
    partial = whole()
    del partial[0]["measures"]["disk_bytes"]
    assert "a" not in report.decide(partial)["inside_the_envelope"]
    for bad in ({"peak_rss_bytes": 5 * 2**30}, {"warm_p95_s": 2.5}, {"recall_at_10": 0.5},
                {"re_encoded_after_restart": 3}, {"partial_after_kill": 1}, {"disk_bytes": 3 * 2**30}):
        assert report.decide(whole(**bad))["decision"] == NONE, bad
    failed = whole()
    failed[4] = {**failed[4], "state": "failed", "reason": "MemoryError"}
    found = report.decide(failed)
    assert found["decision"] == NONE and found["cells_missing_or_failed"] == 1
    assert any(row["problems"] == ["failed: MemoryError"] for row in found["table"])


def test_report_refuses_runs_without_equal_complete_pins_and_the_frozen_thresholds(tmp_path, monkeypatch):
    def changed(**update):
        return [{**cell, **update} for cell in whole()]

    no_pins = [{k: v for k, v in cell.items() if k != "pins"} for cell in whole()]
    assert report.decide(no_pins)["decision"] == NONE and "required pin" in report.decide(no_pins)["refused_because"][0]
    assert report.decide(changed(pins={"model": "m"}))["decision"] == NONE, "a pin that lacks a required field"
    two = whole()
    two[0] = {**two[0], "pins": {**PINS, "weights_sha256": "another"}}
    assert "different pins" in " ".join(report.decide(two)["refused_because"]) and report.decide(two)["decision"] == NONE
    assert report.decide(changed(thresholds_sha256="0" * 64))["decision"] == NONE
    for value in (float("nan"), float("inf"), True, "0.95", None):
        found = report.decide(json.loads(json.dumps(whole(recall_at_10=value))))
        assert found["decision"] == NONE and found["inside_the_envelope"] == [], repr(value)
    # a bar edited in the protocol file after the freeze decides nothing: the digest pinned in the code is compared
    text = report.PROTOCOL.read_text(encoding="utf-8")
    edited = tmp_path / "05.md"
    edited.write_text(text.replace('"warm_p95_seconds_max": 2.0', '"warm_p95_seconds_max": 20.0'), encoding="utf-8")
    monkeypatch.setattr(report, "PROTOCOL", edited)
    found = report.decide(whole())
    assert found["decision"] == NONE and "not the frozen ones" in found["refused_because"][0]
    assert found["thresholds_sha256"] != found["frozen_thresholds_sha256"]

def test_no_weights_is_reported_unavailable_not_zero(tmp_path, monkeypatch):
    stub = candidates.Candidate("c", "stub", lambda: "the weights are not in the local cache", "g6")
    monkeypatch.setitem(candidates.CANDIDATES, "c", stub)
    out = tmp_path / "c.json"
    assert spike_run.main(["--candidate", "c", "--size", "2000", "--out", str(out)]) == 2
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["state"] == "unavailable" and result["measures"] == {} and "local cache" in result["reason"]
    assert result["pins"] == {} and result["thresholds_sha256"] == report.THRESHOLDS_SHA256
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