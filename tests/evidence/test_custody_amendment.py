# SPDX-License-Identifier: Apache-2.0
"""Amendment regressions: observation identity, stop signals and legacy formal inputs."""
from asyncio import CancelledError

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.evidence import custody, mapping
from tests.evidence.observed_fixtures import issue_observed_records
from tests.evidence.test_execution_custody import FACTS, SUBJECT, record


def test_reissuing_one_observed_fixture_keeps_its_host_identity():
    first = issue_observed_records(FACTS, SUBJECT, [record()])
    second = issue_observed_records(FACTS, SUBJECT, [record()], extra={"note": "kept"})
    own = first.ledger.get(mapping.PROVENANCE_KIND, "run")
    assert second.ledger.get(mapping.PROVENANCE_KIND, "run") == {**own, "note": "kept"}


@pytest.mark.parametrize("error", [ExecutionInterrupted, ContainmentFailure, KeyboardInterrupt,
                                  SystemExit, GeneratorExit, CancelledError])
@pytest.mark.parametrize("stage", ["check", "store", "entry"])
def test_custody_resolution_lets_execution_control_reach_its_caller(monkeypatch, error, stage):
    def stop(*args, **kwargs):
        raise error("stop")

    if stage == "check":
        monkeypatch.setattr(custody.receipt_store, "check", stop)
    elif stage == "store":
        monkeypatch.setattr(custody.receipt_store, "store_path", stop)
    else:
        body = {"schema": custody.RUN_SCHEMA, "source_after": SUBJECT.sha256}
        monkeypatch.setattr(custody.receipt_store, "check", lambda *a, **kw: ("matched", body))
        monkeypatch.setattr(custody.ChainLedger, "entry", stop)
    with pytest.raises(error):
        custody.resolve(custody.RunReference("receipt", SUBJECT.sha256))


@pytest.mark.parametrize("result", ["pass", "fail", "unknown"])
def test_every_legacy_formal_result_is_contained_at_the_rig_boundary(monkeypatch, result):
    from g6.research.mission_bridge import FLAG
    from tests.research.mission_bridge.test_mission_bridge_csir_certificate import (
        good_check, issue, receipt,
    )

    monkeypatch.setenv(FLAG, "1")
    formal = good_check("chk-a", ["p-a"], kind="formal_checked", certificate="ef" * 32)
    other = good_check("chk-b", ["p-b"])
    cert = issue([formal, other], [receipt(formal, result=result), receipt(other)])
    assert not cert.formal and not cert.predicates[0].support
    assert not cert.predicates[0].refute
    assert cert.state != "verified"
    assert bool(cert.predicates[0].caveats) == (result != "pass")
    # A separately earned executable result still supplies its own kind of support.
    executable = good_check("chk-a", ["p-a"])
    assert issue([executable, other], [receipt(executable), receipt(other)]).predicates[0].support
