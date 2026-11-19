# SPDX-License-Identifier: Apache-2.0
"""Evaluator lifecycle, census and public-record boundaries using synthetic tasks."""
import importlib
import json
from io import BytesIO

import pytest

from g6.core.adaptation_gate import protected_hits
from g6.core.vault import AccessDenied, guarded_read
from g6.metaharness.domain import SealedSplit
from g6.recursion.tasks import TaskSpec
from g6.rsi.licences import licence_for
from g6.rsi.records import CheckSpec


@pytest.fixture
def api():
    class API:
        def __getattr__(self, key):
            name = "g6.recursion.held_out_slicer"
            assert importlib.util.find_spec(name) is not None, "S3 sealed slicer is missing"
            return getattr(importlib.import_module(name), key)

    return API()


def authority():
    from g6.recursion.evaluator_entry import evaluator_capability

    return evaluator_capability(BytesIO(b"0123456789abcdef" * 2))


def pool():
    return tuple(TaskSpec(f"livecodebench:PRIVATE-ID-{i}", f"PRIVATE-FAMILY-{i // 3}", "livecodebench",
                          licence_for("livecodebench"), f"PRIVATE-PROMPT-{i}",
                          CheckSpec("stdin_stdout", inputs=(f"PRIVATE-INPUT-{i}",), outputs=(f"PRIVATE-LABEL-{i}",)),
                          f"PRIVATE-REF-{i}", "medium", "2024-10-01", "held_out") for i in range(180))


def make(api, tmp_path, **kwargs):
    return api.HeldOutSlicer.register(tmp_path / "evaluator", kwargs.pop("tasks", pool()), seed=17,
                                     detectors=("weakening", "mutation", "shortcut"), role="evaluator", capability=authority(), **kwargs)


def freeze(api, generation=0):
    return api.FreezeRecord(generation, "a" * 40, {name: "b" * 64 for name in api.FROZEN_COMPONENTS})


def opened(api, tmp_path, generation=0):
    store = make(api, tmp_path)
    record = freeze(api, generation)
    receipt = store.record_freeze(record)
    return store, store.cut(generation, receipt)


def clean_screen(store, slice_id):
    from g6.recursion.contamination_gate import MembershipProbe

    tasks = store.evaluator_items(slice_id, role="evaluator", capability=authority())
    return store.screen(slice_id, (), membership={t.task_id: MembershipProbe(1, 1, (1, 1), (1, 1)) for t in tasks})


def test_nothing_opens_before_committed_matching_generation_freeze(api, tmp_path):
    store = make(api, tmp_path)
    with pytest.raises(api.HeldOutError, match="FREEZE_REQUIRED"):
        store.cut(0, "c" * 64)
    receipt = store.record_freeze(freeze(api))
    with pytest.raises(api.HeldOutError, match="FREEZE_MISMATCH"):
        store.cut(1, receipt)
    item = store.cut(0, receipt)
    assert item.generation == 0 and item.freeze_digest == receipt
    assert item.to_record()["cut_after_generation"] == 0
    assert store.evaluator_items(item.slice_id, role="evaluator", capability=authority())


def test_seed_rule_registered_before_cut_and_same_seed_pool_same_partitions(api, tmp_path):
    first = make(api, tmp_path / "first")
    second = make(api, tmp_path / "second", tasks=tuple(reversed(pool())))
    assert first.registration()["rule"] == "cut_pool-strata-six-sha256-v1"
    assert first.registration()["partition_seed_commitment"] == authority().commitment("partition-seed", 17)
    assert "seed" not in first.registration()
    assert first.registration()["partition_digest"] == second.registration()["partition_digest"]
    all_ids, families = set(), set()
    for generation in range(6):
        left = first.cut(generation, first.record_freeze(freeze(api, generation)))
        right = second.cut(generation, second.record_freeze(freeze(api, generation)))
        litems = first.evaluator_items(left.slice_id, role="evaluator", capability=authority())
        ritems = second.evaluator_items(right.slice_id, role="evaluator", capability=authority())
        assert {t.task_id for t in litems} == {t.task_id for t in ritems}
        assert not all_ids.intersection(t.task_id for t in litems)
        assert not families.intersection(t.family for t in litems)
        all_ids.update(t.task_id for t in litems)
        families.update(t.family for t in litems)
    assert all_ids == {t.task_id for t in pool()}


def test_seed_and_rule_are_durable_before_partition_code_runs(api, tmp_path, monkeypatch):
    module = importlib.import_module("g6.recursion.held_out_slicer")
    original = module.partition_pool
    observed = []

    def partition(tasks, seed):
        rule = json.loads((tmp_path / "evaluator" / "rule.json").read_text(encoding="utf-8"))
        observed.append(rule)
        assert seed == 17 and rule["rule"] == module.RULE
        assert "seed" not in rule
        assert rule["partition_seed_commitment"] == authority().commitment("partition-seed", seed)
        return original(tasks, seed)

    monkeypatch.setattr(module, "partition_pool", partition)
    make(api, tmp_path)
    assert len(observed) == 1


def test_reopen_and_spend_survive_restart(api, tmp_path):
    store, item = opened(api, tmp_path)
    store.spend(item.slice_id)
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.spent(item.slice_id)
    with pytest.raises(api.HeldOutError, match="SLICE_REUSED"):
        again.cut(0, item.freeze_digest)
    with pytest.raises(api.HeldOutError, match="SLICE_CLOSED"):
        again.evaluator_items(item.slice_id, role="evaluator", capability=authority())


@pytest.mark.parametrize("role", ["actor", "proposer", "trainer", "unknown"])
def test_roles_cannot_open_evaluator_store_or_read_items_canaries_or_sealed_refs(api, tmp_path, role):
    store, item = opened(api, tmp_path)
    with pytest.raises(api.HeldOutError, match="EVALUATOR_ONLY"):
        api.HeldOutSlicer(tmp_path / "evaluator", role=role)
    for getter in (store.evaluator_items, store.canaries, store.sealed):
        with pytest.raises(api.HeldOutError, match="EVALUATOR_ONLY"):
            getter(item.slice_id, role=role)


def test_only_keyed_references_cross_boundary_and_vault_seam_is_used(api, tmp_path, caplog):
    store, item = opened(api, tmp_path)
    sealed = store.sealed(item.slice_id, role="evaluator", capability=authority())
    assert isinstance(sealed, SealedSplit)
    with pytest.raises(AccessDenied):
        guarded_read(sealed.vault, "learner", tmp_path / "evaluator" / "pool.json")
    report = clean_screen(store, item.slice_id)
    public = json.dumps([store.registration(), item.to_record(), report.to_record(), store.records()])
    public += repr(item) + repr(store) + repr(sealed) + caplog.text
    assert "PRIVATE-" not in public
    for marker in store.canaries(item.slice_id, role="evaluator", capability=authority()):
        assert marker not in public


@pytest.mark.parametrize("source", ["training", "output"])
def test_canary_void_is_durable_and_recorded_without_the_marker(api, tmp_path, source):
    from g6.metaharness.schema import SuiteCase

    store, item = opened(api, tmp_path)
    from g6.recursion.contamination_gate import MembershipProbe
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    tasks = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())
    report = store.screen(item.slice_id, (SuiteCase("train", marker, "answer"),) if source == "training" else (),
                          model_outputs=(marker,) if source == "output" else (),
                          membership={t.task_id: MembershipProbe(1, 1, (1, 1), (1, 1)) for t in tasks})
    assert report.voided
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    assert any(r["kind"] == "contamination" for r in again.evaluator_records(role="evaluator", capability=authority()))
    assert marker not in json.dumps(again.records())


def test_pass_without_census_fails_and_complete_census_runs_every_detector(api, tmp_path):
    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    missing = store.evaluate_pass(item.slice_id, task.task_id, "output", arm="G0", attempt="one",
                                  anchor_pass=True, detectors=None)
    assert not missing.passed and missing.reason == "census_missing"
    called = []

    def detector(task, output):
        called.append((task.task_id, output))
        return False

    complete = store.evaluate_pass(item.slice_id, task.task_id, "output", arm="G0", attempt="two",
                                   anchor_pass=True, detectors={name: detector for name in store.detector_names})
    assert complete.passed and complete.census_count == 3 and len(called) == 3
    assert any(r["kind"] == "detector_census" for r in store.evaluator_records(role="evaluator", capability=authority()))


@pytest.mark.parametrize("mode", ["partial", "hack", "raises", "unknown", "nonbool"])
def test_partial_hacking_crashed_or_unknown_detector_census_fails_closed(api, tmp_path, mode):
    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]

    def bad(task, output):
        if mode == "raises":
            raise ValueError("PRIVATE-ERROR")
        return None if mode == "nonbool" else mode == "hack"

    detectors = {name: (lambda task, output: False) for name in store.detector_names}
    if mode == "partial":
        detectors.pop("mutation")
    elif mode == "unknown":
        detectors["extra"] = bad
    else:
        detectors["mutation"] = bad
    result = store.evaluate_pass(item.slice_id, task.task_id, "PRIVATE-OUTPUT", arm="G0", attempt="one",
                                 anchor_pass=True, detectors=detectors)
    assert not result.passed
    assert "PRIVATE-" not in json.dumps(store.records()) + repr(result)


def test_new_runtime_paths_are_already_blocked_by_unchanged_protected_rule():
    paths = ("src/g6/recursion/held_out_slicer.py", "src/g6/recursion/contamination_gate.py")
    assert protected_hits(paths) == list(paths)


def test_pool_change_after_registration_refuses_cut(api, tmp_path):
    store = make(api, tmp_path)
    path = tmp_path / "evaluator" / "pool.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(api.HeldOutError, match="POOL_CHANGED"):
        store.cut(0, store.record_freeze(freeze(api)))


def test_freeze_requires_full_state_and_copies_the_callers_mapping(api):
    values = {name: "b" * 64 for name in api.FROZEN_COMPONENTS}
    record = api.FreezeRecord(0, "a" * 40, values)
    values["library"] = "c" * 64
    assert record.state_digests["library"] == "b" * 64
    with pytest.raises(TypeError):
        record.state_digests["library"] = "c" * 64
    for name in api.FROZEN_COMPONENTS:
        missing = dict(record.state_digests)
        missing.pop(name)
        with pytest.raises(api.HeldOutError, match="FREEZE_INVALID"):
            api.FreezeRecord(0, "a" * 40, missing)


@pytest.mark.parametrize("generation,commit", [(True, "a" * 40), (-1, "a" * 40), (6, "a" * 40),
                                             (0, "PRIVATE-COMMIT"), (0, "A" * 40)])
def test_freeze_rejects_unknown_generation_or_non_digest_commit(api, generation, commit):
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_FREEZE_INVALID$"):
        api.FreezeRecord(generation, commit, {name: "b" * 64 for name in api.FROZEN_COMPONENTS})


def test_pure_partition_is_seeded_order_invariant_and_keeps_families_whole(api):
    tasks = pool()
    parts = api.partition_pool(tasks, 17)
    assert parts == api.partition_pool(tuple(reversed(tasks)), 17)
    assert parts != api.partition_pool(tasks, 18)
    assert len(parts) == 6 and all(parts)
    assert {t.task_id for part in parts for t in part} == {t.task_id for t in tasks}
    for index, part in enumerate(parts):
        assert not {t.family for t in part}.intersection(t.family for other in parts[index + 1:] for t in other)


def test_pure_partition_refuses_duplicate_and_train_only_tasks(api):
    with pytest.raises(api.HeldOutError, match="POOL_INVALID"):
        api.partition_pool((pool()[0], pool()[0]), 17)
    for seed in (True, "17", None):
        with pytest.raises(api.HeldOutError, match="POOL_INVALID"):
            api.partition_pool(pool(), seed)
    training = TaskSpec("apps:train-only", "train", "apps", licence_for("apps"), "Training task",
                        CheckSpec("stdin_stdout", inputs=("1",), outputs=("2",)), "", "easy", "", "train",
                        source_split="train", train_ok=True)
    with pytest.raises(api.HeldOutError, match="POOL_INVALID"):
        api.partition_pool((training,), 17)


@pytest.mark.parametrize("mode", ["clean", "partial", "hack", "raises", "unknown", "nonbool"])
def test_pure_census_runs_every_registered_detector_and_requires_exact_coverage(api, mode):
    names = ("weakening", "mutation", "shortcut")
    calls = []

    def detector(task, output):
        calls.append(task.task_id)
        if mode == "raises":
            raise ValueError("PRIVATE-ERROR")
        return None if mode == "nonbool" else mode == "hack"

    detectors = {name: detector for name in names}
    if mode == "partial":
        detectors.pop("mutation")
    elif mode == "unknown":
        detectors["extra"] = detector
    result, observations = api.detector_census(pool()[0], "PRIVATE-OUTPUT", names, detectors)
    assert result.passed is (mode == "clean")
    assert result.census_count == (0 if mode in ("partial", "unknown") else 3)
    assert len(calls) == result.census_count
    if result.census_count:
        assert [o["name"] for o in observations] == list(names)
    assert "PRIVATE-" not in json.dumps(observations) + repr(result)


def test_registered_census_cannot_be_emptied_by_a_handle_attribute(api, tmp_path):
    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    try:
        store.detector_names = ()
    except AttributeError:
        pass
    result = store.evaluate_pass(item.slice_id, task.task_id, "output", arm="G0", attempt="one",
                                 anchor_pass=True, detectors={})
    assert not result.passed and result.reason == "census_missing"


def test_canary_is_recorded_without_membership_and_prevents_any_later_open(api, tmp_path):
    store, item = opened(api, tmp_path)
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    result = store.screen(item.slice_id, (), model_outputs=(marker,), membership={})
    assert result.voided and store.status(item.slice_id) == "void"
    assert any(r["kind"] == "contamination" for r in store.evaluator_records(role="evaluator", capability=authority()))


def test_model_output_canary_voids_its_target_even_before_target_is_opened(api, tmp_path):
    store, first = opened(api, tmp_path)
    # This fixture's evaluator reads its own synthetic marker, without opening slice 1.
    marker = json.loads((tmp_path / "evaluator" / "pool.json").read_text(encoding="utf-8"))["canaries"][1]
    receipt = store.record_freeze(freeze(api, 1))
    result = store.screen(first.slice_id, (), model_outputs=(marker,), membership={})
    assert result.voided and store.status(first.slice_id) == "void"
    with pytest.raises(api.HeldOutError, match="SLICE_REUSED"):
        store.cut(1, receipt)


@pytest.mark.parametrize("closed", [False, True])
def test_canary_output_is_recorded_even_for_repeated_or_spent_attempts(api, tmp_path, closed):
    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    detectors = {n: lambda t, o: False for n in store.detector_names}
    assert store.evaluate_pass(item.slice_id, task.task_id, "clean", arm="G0", attempt="one",
                               anchor_pass=True, detectors=detectors).passed
    if closed:
        store.spend(item.slice_id)
    result = store.evaluate_pass(item.slice_id, task.task_id, marker, arm="G0", attempt="one",
                                 anchor_pass=True, detectors=detectors)
    assert not result.passed and result.reason == "contamination"
    assert store.status(item.slice_id) == "void"


def test_excluded_item_is_counted_and_cannot_be_rescued_by_a_later_clean_screen(api, tmp_path):
    from g6.metaharness.schema import SuiteCase

    store, item = opened(api, tmp_path)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    from g6.recursion.contamination_gate import MembershipProbe

    probes = {t.task_id: MembershipProbe(1, 1, (1, 1), (1, 1))
              for t in store.evaluator_items(item.slice_id, role="evaluator", capability=authority())}
    report = store.screen(item.slice_id, (SuiteCase(task.task_id, "other", "other"),), membership=probes)
    assert report.identity_excluded == report.excluded_count == 1
    store.screen(item.slice_id, (), membership=probes)
    assert task.task_id not in {t.task_id for t in store.evaluator_items(item.slice_id, role="evaluator", capability=authority())}
    result = store.evaluate_pass(item.slice_id, task.task_id, "output", arm="G0", attempt="one",
                                 anchor_pass=True, detectors={n: lambda t, o: False for n in store.detector_names})
    assert not result.passed and result.reason == "item_excluded"


def encoded_marker(marker, mode):
    if mode == "uppercase":
        return marker.upper()
    if mode == "nfkc":
        return "".join(chr(ord(c) + 0xFEE0) for c in marker)
    if mode == "unicode_escape":
        return "".join(f"\\u{ord(c):04x}" for c in marker)
    if mode == "json_newline":
        return marker[:16] + r"\n" + marker[16:]
    if mode == "double_unicode":
        return json.dumps("".join(f"\\u{ord(c):04x}" for c in marker))[1:-1]
    return "".join(f"%{ord(c):02X}" for c in marker)


@pytest.mark.parametrize("mode", ["uppercase", "nfkc", "unicode_escape", "percent", "json_newline", "double_unicode"])
@pytest.mark.parametrize("boundary", ["screen", "evaluate"])
def test_encoded_canary_durably_voids_through_both_apis(api, tmp_path, mode, boundary):
    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    output = encoded_marker(marker, mode)
    if boundary == "screen":
        assert store.screen(item.slice_id, (), model_outputs=(output,), membership={}).voided
    else:
        result = store.evaluate_pass(item.slice_id, task.task_id, output, arm="G0", attempt="one",
                                     anchor_pass=True, detectors={n: lambda t, o: False for n in store.detector_names})
        assert not result.passed and result.reason == "contamination"
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    with pytest.raises(api.HeldOutError, match="SLICE_CLOSED"):
        again.evaluate_pass(item.slice_id, task.task_id, "clean", arm="G0", attempt="later",
                            anchor_pass=True, detectors={n: lambda t, o: False for n in again.detector_names})


def test_nested_fragment_canary_durably_voids(api, tmp_path):
    from g6.metaharness.schema import SuiteCase

    store, item = opened(api, tmp_path)
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    report = store.screen(item.slice_id, (SuiteCase("train", "other", (marker[:16], [marker[16:]])),),
                          membership={})
    assert report.voided and store.status(item.slice_id) == "void"


@pytest.mark.parametrize("refusal", ["spent", "malformed_output", "malformed_training"])
def test_screen_refusals_commit_known_canary_incidents(api, tmp_path, refusal):
    from g6.metaharness.schema import SuiteCase
    from g6.recursion.contamination_gate import ContaminationError

    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    marker = json.loads((tmp_path / "evaluator" / "pool.json").read_text(encoding="utf-8"))["canaries"][1]
    outputs, training = (marker,), ()
    if refusal == "spent":
        store.spend(item.slice_id)
    elif refusal == "malformed_output":
        outputs = (marker, None)
    else:
        training = (SuiteCase("train", marker, {"bad": object()}),)
        outputs = ()
    with pytest.raises((api.HeldOutError, ContaminationError)):
        store.screen(item.slice_id, training, model_outputs=outputs, membership={})
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    assert any(r["kind"] == "contamination" for r in again.evaluator_records(role="evaluator", capability=authority()))
    with pytest.raises(api.HeldOutError, match="SLICE_REUSED"):
        again.cut(1, again.record_freeze(freeze(api, 1)))
    with pytest.raises(api.HeldOutError, match="SLICE_CLOSED"):
        again.evaluate_pass(item.slice_id, task.task_id, "clean", arm="G0", attempt="later",
                            anchor_pass=True, detectors={n: lambda t, o: False for n in again.detector_names})


@pytest.mark.parametrize("mapping_kind", ["dict", "ordered", "proxy"])
@pytest.mark.parametrize("bad_first", [False, True])
def test_mapping_marker_incident_is_durable_after_refusal(api, tmp_path, mapping_kind, bad_first):
    from collections import OrderedDict
    from types import MappingProxyType
    from g6.metaharness.schema import SuiteCase
    from g6.recursion.contamination_gate import ContaminationError

    store, item = opened(api, tmp_path)
    marker = json.loads((tmp_path / "evaluator" / "pool.json").read_text(encoding="utf-8"))["canaries"][1]
    pairs = [("marker", marker), ("bad", set())]
    payload = dict(reversed(pairs) if bad_first else pairs)
    payload = {"dict": dict, "ordered": OrderedDict, "proxy": MappingProxyType}[mapping_kind](payload)
    with pytest.raises(ContaminationError, match="SCREEN_INVALID"):
        store.screen(item.slice_id, (SuiteCase("t", "other", payload),), membership={})
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    with pytest.raises(api.HeldOutError, match="SLICE_REUSED"):
        again.cut(1, again.record_freeze(freeze(api, 1)))
    assert sum(r["kind"] == "contamination"
               for r in again.evaluator_records(role="evaluator", capability=authority())) == 2


def test_learner_cannot_claim_evaluator_role_at_constructor(api, tmp_path):
    opened(api, tmp_path)
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED$"):
        api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator")


@pytest.mark.parametrize("getter", ["evaluator_items", "canaries", "sealed"])
def test_learner_cannot_claim_evaluator_role_at_getters(api, tmp_path, getter):
    store, item = opened(api, tmp_path)
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED$"):
        getattr(store, getter)(item.slice_id, role="evaluator")


def test_public_attempt_and_item_refs_resist_candidate_id_enumeration(api, tmp_path):
    from g6.rsi.records import content_digest

    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    sealed = store.sealed(item.slice_id, role="evaluator", capability=authority())
    store.evaluate_pass(item.slice_id, task.task_id, "clean", arm="G0", attempt="one",
                        anchor_pass=True, detectors={n: lambda t, o: False for n in store.detector_names})
    public = json.dumps([store.records(), [dict(r) for r in sealed.refs]])
    for candidate in pool():
        assert content_digest([candidate.task_id, "G0", "one"]) not in public
        assert content_digest(candidate.task_id) not in public


def test_registration_refuses_source_wide_infeasible_families(api, tmp_path):
    from dataclasses import replace

    tasks = tuple(replace(t, family="livecodebench", provenance=None) for t in pool())
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_PARTITION_INFEASIBLE$"):
        make(api, tmp_path, tasks=tasks)
    assert not (tmp_path / "evaluator" / "pool.json").exists()
    assert not (tmp_path / "evaluator" / "state.sqlite3").exists()


def test_pure_partition_refuses_source_wide_infeasible_families(api):
    from dataclasses import replace

    tasks = tuple(replace(t, family="livecodebench", provenance=None) for t in pool())
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_PARTITION_INFEASIBLE$"):
        api.partition_pool(tasks, 17)


def test_role_claim_is_rejected_before_store_access(api):
    with pytest.raises(api.HeldOutError, match="^G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED$"):
        api.HeldOutSlicer(".build/absent-learner-store", role="evaluator")


def test_host_capability_binds_secret_and_store_and_hides_secret(api):
    from pathlib import Path
    from g6.recursion.evaluator_entry import evaluator_capability

    host = authority()
    other = evaluator_capability(BytesIO(b"fedcba9876543210" * 2))
    store = object.__new__(api.HeldOutSlicer)
    store._root = Path(".build/synthetic-authority-scope")
    data = {"authority_binding": host.binding(store._root)}
    store._authorize("evaluator", host, data)
    with pytest.raises(api.HeldOutError, match="EVALUATOR_CAPABILITY_REQUIRED"):
        store._authorize("evaluator", other, data)
    store._root = Path(".build/different-authority-scope")
    with pytest.raises(api.HeldOutError, match="EVALUATOR_CAPABILITY_REQUIRED"):
        store._authorize("evaluator", host, data)
    assert "0123456789abcdef" not in repr(host)


@pytest.mark.parametrize("boundary", ["screen", "evaluate", "nested_training"])
def test_fixed_whitespace_canary_view_durably_voids(api, tmp_path, boundary):
    from g6.metaharness.schema import SuiteCase

    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    marker = store.canaries(item.slice_id, role="evaluator", capability=authority())[0]
    output = marker[:16] + " \n " + marker[16:]
    if boundary == "evaluate":
        result = store.evaluate_pass(item.slice_id, task.task_id, output, arm="G0", attempt="one",
                                     anchor_pass=True, detectors={n: lambda t, o: False for n in store.detector_names})
        assert not result.passed and result.reason == "contamination"
    elif boundary == "screen":
        assert store.screen(item.slice_id, (), model_outputs=(output,), membership={}).voided
    else:
        assert store.screen(item.slice_id, (SuiteCase("train", "other", {"nested": [output]}),),
                            membership={}).voided
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"


@pytest.mark.parametrize("location", ["training", "output", "membership", "probes"])
def test_raw_container_marker_incident_survives_schema_refusal(api, tmp_path, location):
    from g6.recursion.contamination_gate import ContaminationError

    store, item = opened(api, tmp_path)
    marker = json.loads((tmp_path / "evaluator" / "pool.json").read_text(encoding="utf-8"))["canaries"][1]
    args = {"training": (), "membership": {}, "model_outputs": (), "canary_probes": ()}
    args[location if location in ("training", "membership") else
         "model_outputs" if location == "output" else "canary_probes"] = ({"input": [marker]},)
    with pytest.raises((api.HeldOutError, ContaminationError)):
        store.screen(item.slice_id, **args)
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    with pytest.raises(api.HeldOutError, match="SLICE_REUSED"):
        again.cut(1, again.record_freeze(freeze(api, 1)))


@pytest.mark.parametrize("closed", [False, True])
def test_positive_probe_incident_survives_unrelated_refusal(api, tmp_path, closed):
    from g6.recursion.contamination_gate import CanaryProbe, ContaminationError

    store, item = opened(api, tmp_path)
    clean_screen(store, item.slice_id)
    task = store.evaluator_items(item.slice_id, role="evaluator", capability=authority())[0]
    if closed:
        store.spend(item.slice_id)
    with pytest.raises((api.HeldOutError, ContaminationError)):
        store.screen(item.slice_id, (), membership={}, model_outputs=(None,),
                     canary_probes=(None, CanaryProbe(.2, .1)))
    again = api.HeldOutSlicer(tmp_path / "evaluator", role="evaluator", capability=authority())
    assert again.status(item.slice_id) == "void"
    with pytest.raises(api.HeldOutError, match="SLICE_CLOSED"):
        again.evaluate_pass(item.slice_id, task.task_id, "clean", arm="G0", attempt="later",
                            anchor_pass=True, detectors={n: lambda t, o: False for n in again.detector_names})


def test_public_content_commitments_use_sealed_secret_and_domains(api, tmp_path):
    from g6.rsi.records import content_digest

    tasks = tuple(pool()[i * 3] for i in range(6))
    seed = next(seed for seed in range(10000) if _feasible(api, tasks, seed))
    store = api.HeldOutSlicer.register(tmp_path / "singletons", tasks, seed=seed, detectors=("clean",),
                                      role="evaluator", capability=authority())
    for generation in range(6):
        item = store.cut(generation, store.record_freeze(freeze(api, generation)))
        assert item.count == 1
        public = json.dumps([item.to_record(), store.records(), store.registration()])
        assert all(content_digest([t.to_record()]) not in public for t in tasks)
        data = json.loads((tmp_path / "singletons" / "pool.json").read_text(encoding="utf-8"))
        records = [data["tasks"][i] for i in data["partitions"][generation]]
        assert item.opened_digest == authority().commitment("slice-content", records)
        assert item.seed_commitment != item.opened_digest


def _feasible(api, tasks, seed):
    try:
        api.partition_pool(tasks, seed)
        return True
    except api.HeldOutError:
        return False


def test_public_records_hide_partition_seed_from_candidate_observer(api, tmp_path):
    from g6.rsi.records import content_digest

    tasks = tuple(pool()[i] for i in (0, 3, 6, 9, 12, 15))
    store = api.HeldOutSlicer.register(tmp_path / "observer", tasks, seed=96, detectors=("clean",),
                                      role="evaluator", capability=authority())
    cuts = [store.cut(g, store.record_freeze(freeze(api, g))) for g in range(6)]
    assert all(item.count == 1 for item in cuts)
    public = [store.registration(), store.records(), [item.to_record() for item in cuts]]
    public_text = json.dumps(public)
    assert "seed" not in store.registration()
    assert store.registration()["partition_seed_commitment"] == authority().commitment("partition-seed", 96)
    assert authority().commitment("partition-seed", 96) != authority().commitment("slice-seed", 96)
    assert all(content_digest(seed) not in public_text for seed in range(100))
    assert all(task.task_id not in public_text for task in tasks)
    # The public field inventory contains no raw seed, membership or unkeyed seed digest.
    assert set(store.registration()) == {
        "rule", "partition_seed_commitment", "pool_digest", "detectors", "partition_digest",
        "rule_sha256", "pool_sha256", "vault_digest", "vault_id", "salt_commitment",
    }
