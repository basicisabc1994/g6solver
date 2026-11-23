# SPDX-License-Identifier: Apache-2.0
"""The runner producer retains typed phase outcomes and exact check identities."""
import hashlib

import pytest

from g6.core.ledger import digest
from g6.evidence.custody import RunReference, _observe_pytest, resolve
from tests.harness.efu2_cases import NODE, PASSED, report


def test_the_host_producer_binds_test_bytes_scope_and_environment(tmp_path):
    from g6.evidence.custody import run_environment

    source = b"def test_add():\n    assert True\n"
    (tmp_path / "test_calc.py").write_bytes(source)
    before = {"test_calc.py": hashlib.sha256(source).hexdigest()}
    raw = report(PASSED, loaded_complete=True, loaded_outside={})
    receipt = _observe_pytest(tmp_path, before, before, [NODE], raw)
    held = resolve(RunReference(receipt, digest(before)))
    assert held is not None
    assert held[0]["checks"][NODE] == {"sha256": digest([NODE, hashlib.sha256(source).hexdigest()]), "result": "pass"}
    assert held[0]["environment_sha256"] == digest(run_environment(raw))


@pytest.mark.parametrize("fault", ["changed", "loading_unknown", "no_boundary", "setup_error", "untyped"])
def test_absent_or_unknown_observations_cannot_be_recorded_as_a_pass(tmp_path, fault):
    (tmp_path / "test_calc.py").write_text("def test_add(): pass\n", encoding="utf-8")
    before, after = {"test_calc.py": "c" * 64}, {"test_calc.py": "c" * 64}
    raw = report(PASSED, loaded_complete=True, loaded_outside={})
    if fault == "changed":
        after = {"test_calc.py": "d" * 64}
    elif fault == "loading_unknown":
        raw["loaded_complete"] = False
    elif fault == "no_boundary":
        raw["isolation"] = {"backend": ""}
    elif fault == "setup_error":
        raw = report({"setup": "failed", "teardown": "passed"}, loaded_complete=True, loaded_outside={})
    else:
        raw["collected_ids"] = None
    receipt = _observe_pytest(tmp_path, before, after, [NODE], raw)
    held = resolve(RunReference(receipt, digest(before))) if receipt else None
    assert held is None or held[0]["checks"][NODE]["result"] == "unknown"
