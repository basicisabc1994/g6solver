# SPDX-License-Identifier: Apache-2.0
"""Bound hostile scans in a child so a regressed expression cannot hang the suite."""
import multiprocessing

from g6.guards import scan_personal_data


def _scan_bounds(results):
    import dataclasses
    from time import monotonic

    started = monotonic()
    scans = [
        scan_personal_data("!" * 200_000),
        scan_personal_data("plain" * 10, max_chars=49),
        scan_personal_data("a@example.invalid b@example.invalid c@example.invalid", max_findings=2),
        # Exercise the expression itself with a host-raised size limit, not just the refusal path.
        scan_personal_data("!" * 200_000 + " a@example.invalid", max_chars=300_000),
        scan_personal_data("a@example.invalid b@example.invalid", max_findings=2),
    ]
    results.put((monotonic() - started, [
        {**dataclasses.asdict(scan), "decision": scan.decision, "flagged": scan.flagged} for scan in scans
    ]))


def test_personal_data_scan_is_bounded_and_incomplete_is_not_clean():
    # Removal oracle: pre-scan size refusal, finding cap and the full email-candidate start boundary.
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    worker = context.Process(target=_scan_bounds, args=(results,))
    try:
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive(), "personal-data scan exceeded its bounded deadline"
        assert worker.exitcode == 0
        elapsed, scans = results.get(timeout=2)
        assert elapsed < 5
        for scan in scans[:3]:
            assert scan["complete"] is False and scan["reason"]
            assert scan["flagged"] and scan["decision"] == "BLOCK"
        assert scans[0]["findings"] == scans[1]["findings"] == ()
        assert scans[0]["code"] == scans[1]["code"] == "G6_E_PERSONAL_DATA_TOO_LARGE"
        assert len(scans[2]["findings"]) == 2 and scans[2]["code"] == "G6_E_PERSONAL_DATA_FINDING_LIMIT"
        assert scans[3]["complete"] and scans[3]["flagged"] and len(scans[3]["findings"]) == 1
        assert scans[4]["complete"] and len(scans[4]["findings"]) == 2
    finally:
        if worker.is_alive():
            worker.terminate()
        if worker.pid is not None:
            worker.join(timeout=5)
        results.close()
        results.join_thread()