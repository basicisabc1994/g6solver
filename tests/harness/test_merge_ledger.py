# SPDX-License-Identifier: Apache-2.0
"""Analytics read the harness database, counting failed checks and complete run denominators."""
import pytest

from g6.harness import Check, Memory, Standard, fail_rate, failure_counts, run


def test_reliability_ledger_d1_counts_recurring_checks_across_families(tmp_path):
    # Removal makes this fail: `HAVING COUNT(*) >= ?` in Memory.failure_counts.
    path = tmp_path / "harness.db"
    memory = Memory(path)
    check = Check("states the right value", lambda o: o == "42")
    try:
        for family, output in (("one", "41"), ("one", "42"), ("two", "43")):
            run(Standard("Summarise the refund policy", (check,), family=family), lambda c, o=output: o, memory=memory)
    finally:
        memory.close()
    reopened = Memory(path)
    try:
        assert failure_counts("check", 2, memory=reopened)["counts"] == {check.key: 2}
        assert failure_counts("family", 2, memory=reopened)["counts"] == {}
        assert failure_counts("family", 1, memory=reopened)["counts"] == {"one": 1, "two": 1}
        report = fail_rate(" ONE ", memory=reopened)
        assert (report["runs"], report["failed_runs"], report["rate"]) == (2, 1, 0.5)
    finally:
        reopened.close()


def test_reliability_ledger_01_rows_never_earn_verified():
    # Removal makes this fail: `return _report("failure counts", counts=counts, by=by)` in failure_counts.
    memory = Memory(":memory:")
    try:
        run(Standard("Summarise the refund policy", (Check("right value", lambda o: False),)),
            lambda c: "41", memory=memory)
        assert failure_counts(memory=memory)["completion_state"] == "qualified-draft"
    finally:
        memory.close()


def test_no_runs_is_not_a_zero_failure_rate():
    # Removal makes this fail: `rate=failed / len(rows) if rows else None` in fail_rate.
    memory = Memory(":memory:")
    try:
        report = fail_rate("unseen", memory=memory)
        assert report["rate"] is None and report["status"] == "no runs"
        assert report["completion_state"] == "qualified-draft"
    finally:
        memory.close()


def test_pre_execution_blocks_count_in_the_denominator():
    # Removal makes this fail: `memory._remember_run(standard.family_key, reply)` in run.
    memory = Memory(":memory:")
    try:
        run(Standard("Summarise the refund policy", (), family="missing"), None, memory=memory)
        assert fail_rate("missing", memory=memory)["rate"] == 1.0
    finally:
        memory.close()


@pytest.mark.parametrize("by,count", [("sql", 1), ("check", 0), ("family", True)])
def test_ledger_parameters_are_refused(by, count):
    # Removal makes this fail: `raise ValueError("G6_E_HARNESS_ANALYTICS: invalid grouping or min_count")`.
    memory = Memory(":memory:")
    try:
        with pytest.raises(ValueError, match="G6_E_HARNESS_ANALYTICS"):
            failure_counts(by, count, memory=memory)
    finally:
        memory.close()
