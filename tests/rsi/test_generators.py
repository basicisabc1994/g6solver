# SPDX-License-Identifier: Apache-2.0
"""Synthetic local families exercise admission without model or remote calls."""
from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import replace

import pytest

from g6.recursion.tasks import CheckSpec, TaskError, TaskSpec, licence_for, to_suite


def api():
    assert importlib.util.find_spec("g6.recursion.generators") is not None, "S4 generator is missing"
    return importlib.import_module("g6.recursion.generators")


def family(*, split="train", suffix="a"):
    g = api()
    task = TaskSpec(
        f"generated:{suffix}", f"clamp:{suffix}", "generated", licence_for("generated"),
        "Repair clamp.", CheckSpec("function", tests=("assert clamp(3) == 3",)),
        "def clamp(x):\n    return 0 if x < 1 else x\n", "introductory", "", split,
        source_split="train" if split != "held_out" else "test", train_ok=split != "held_out",
    )
    return g.FamilySpec(
        task=task, repository_id=f"fixture-{suffix}", bug_class="boundary",
        repository_licence="MIT", hidden_tests=CheckSpec("function", tests=(
            "assert clamp(-2) == 0", "assert clamp(0) == 0", "assert clamp(1) == 1", "assert clamp(4) == 4")),
        primary_origin=g.ReferenceOrigin("human", "author-a"),
        second_reference=g.ReferenceSolution(
            "def clamp(x):\n    return max(x, 0)\n", g.ReferenceOrigin("dataset", "solution-b")),
        issue=g.IssueTemplate("clamp boundary handling", "negative input must return zero"),
    )


def local_run(code, checks, timeout_s):
    assert timeout_s > 0
    namespace = {}
    exec(compile(code, "<synthetic-reference>", "exec"), namespace)
    passed = failed = 0
    for text in checks.tests:
        try:
            exec(compile(text, "<synthetic-check>", "exec"), namespace)
            passed += 1
        except (AssertionError, TypeError, NameError):
            failed += 1
    return {"verdict": "failed" if failed else "passed", "passed": passed, "failed": failed,
            "not_passed": 0, "deselected": 0, "degraded": False, "setup_errors": {},
            "isolation": {"backend": "synthetic-fixture"}}


def test_generated_tasks_are_s1_records_with_separate_hidden_checks():
    g, spec = api(), family()
    result = g.generate_family(spec, seed=19, run=local_run)
    assert result.tasks and result.seed == 19
    assert result.hidden_tests == spec.hidden_tests
    assert result.second_reference == spec.second_reference
    assert result.strength["tried"] == result.strength["rejected"] > 0
    for task in result.tasks:
        assert type(task) is TaskSpec
        assert TaskSpec.from_record(json.loads(json.dumps(task.to_record()))) == task
        assert task.public_tests == spec.task.public_tests
        assert task.hidden_ref == spec.task.hidden_ref
        assert task.family == spec.task.family
        assert "Repair" in task.prompt
        assert "assert clamp(-2)" not in task.prompt
        assert local_run(task.hidden_ref, result.hidden_tests, 10)["verdict"] == "passed"
        assert local_run(result.second_reference.code, result.hidden_tests, 10)["verdict"] == "passed"
        assert result.second_reference.code != task.hidden_ref
        assert to_suite([task], split="train").dev[0].case_id == task.task_id


def test_held_out_issue_uses_template_provenance_and_no_actor_text():
    g, spec = api(), family(split="held_out")
    spec = replace(spec, actor_outputs=("actor-only repair narrative",))
    result = g.generate_family(spec, seed=6, run=local_run)
    assert result.issue_provenance == "template"
    for task in result.tasks:
        assert "actor-only" not in task.prompt
        assert task.split == "held_out" and not task.train_ok
        assert to_suite([task], split="held_out").held_out


@pytest.mark.parametrize("field", ["provenance", "behaviour", "message", "primary", "second", "suite"])
def test_actor_material_refused_before_execution(field):
    g, spec = api(), family(split="held_out")
    text = "actor supplied sentinel"
    issue = spec.issue
    if field == "provenance":
        issue = replace(issue, provenance="actor")
    elif field in ("behaviour", "message"):
        issue = replace(issue, **{field: "ACTOR  supplied\nSENTINEL"})
    elif field == "primary":
        spec = replace(spec, primary_origin=g.ReferenceOrigin("actor", "actor-a"))
    elif field == "second":
        spec = replace(spec, second_reference=replace(
            spec.second_reference, origin=g.ReferenceOrigin("actor", "actor-b")))
    else:
        spec = replace(spec, suite_origin="actor")
    spec = replace(spec, issue=issue, actor_outputs=(text,))
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_ORIGIN"):
        g.generate_family(spec, seed=6, run=lambda *a: pytest.fail("must screen before running"))


def test_actor_fragment_screen_catches_long_output_excerpt():
    g, spec = api(), family(split="held_out")
    words = [f"word{i}" for i in range(35)]
    spec = replace(spec, actor_outputs=(" ".join(words),),
                   issue=replace(spec.issue, message=" ".join(words[8:22])))
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_ORIGIN"):
        g.generate_family(spec, seed=6, run=local_run)


def test_surviving_mutant_refuses_the_whole_weak_family(monkeypatch):
    from g6.workforce import mutation_check

    g, spec = api(), family()
    survivors = mutation_check.Mutant(2, "return x", "return x + 0", spec.task.hidden_ref + "\n")
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **k: [survivors])
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_WEAK_SUITE"):
        g.generate_family(spec, seed=1, run=local_run)


@pytest.mark.parametrize("mode", ["no-mutants", "partial", "not-run", "timed-out", "collection", "zero", "skipped"])
def test_incomplete_mutation_evidence_is_refused(monkeypatch, mode):
    from g6.workforce import mutation_check

    g, spec = api(), family()
    if mode == "no-mutants":
        monkeypatch.setattr(mutation_check, "mutants", lambda *a, **k: [])
    if mode == "partial":
        monkeypatch.setattr(mutation_check, "check", lambda *a, **k: {
            "ran": True, "tried": 1, "rejected": 1, "survived": [], "stopped": "budget"})

    def run(code, checks, timeout_s):
        if code in (spec.task.hidden_ref, spec.second_reference.code):
            return local_run(code, checks, timeout_s)
        raw = local_run(code, checks, timeout_s)
        if mode in ("not-run", "timed-out", "collection"):
            raw["verdict"] = mode
        elif mode == "zero":
            raw.update(passed=0, failed=0)
        elif mode == "skipped":
            raw["not_passed"] = 1
        return raw

    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_(WEAK_SUITE|RUN)"):
        g.generate_family(spec, seed=1, run=run)


@pytest.mark.parametrize("mode", ["same-bytes", "same-ast", "same-origin", "derived", "fails", "missing"])
def test_second_reference_must_be_independent_and_pass(mode):
    g, spec = api(), family()
    second = spec.second_reference
    if mode == "same-bytes":
        second = replace(second, code=spec.task.hidden_ref)
    elif mode == "same-ast":
        second = replace(second, code=spec.task.hidden_ref + "\n# spacing\n")
    elif mode == "same-origin":
        second = replace(second, origin=spec.primary_origin)
    elif mode == "derived":
        second = replace(second, origin=g.ReferenceOrigin("human", "author-b", ("author-a",)))
    elif mode == "missing":
        second = replace(second, code="")
    else:
        second = replace(second, code="def clamp(x):\n    return x\n")
    spec = replace(spec, second_reference=second)
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_REFERENCE"):
        g.generate_family(spec, seed=3, run=local_run)


def test_generation_replays_from_recorded_seed():
    g, spec = api(), family()
    first = g.generate_family(spec, seed=914, run=local_run)
    second = g.generate_family(spec, seed=first.seed, run=local_run)
    assert first.to_record() == second.to_record()
    assert json.loads(json.dumps(first.to_record())) == first.to_record()
    assert first.family_digest == second.family_digest


@pytest.mark.parametrize("seed", [True, -1, "1", 1.5])
def test_invalid_seed_refused(seed):
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_INPUT"):
        api().generate_family(family(), seed=seed, run=local_run)


def test_generator_reuses_both_mutation_entry_points(monkeypatch):
    from g6.workforce import mutation_check

    g, spec = api(), family()
    seen = []
    original_mutants, original_check = mutation_check.mutants, mutation_check.check

    def mutants(*args, **kwargs):
        seen.append("mutants")
        return original_mutants(*args, **kwargs)

    def check(*args, **kwargs):
        seen.append("check")
        assert kwargs["candidates"]
        return original_check(*args, **kwargs)

    monkeypatch.setattr(mutation_check, "mutants", mutants)
    monkeypatch.setattr(mutation_check, "check", check)
    g.generate_family(spec, seed=1, run=local_run)
    assert seen == ["mutants", "check"]


def test_family_bank_is_disjoint_by_id_and_content_digest():
    g = api()
    bank_api = importlib.import_module("g6.rsi.family_bank")
    bank = bank_api.FamilyBank()
    for split in ("train", "dev", "held_out"):
        spec = family(split=split, suffix=split)
        # Distinct content is required as well as distinct family names.
        code = f"def clamp(x):\n    return {len(split)} if x < 0 else x\n"
        checks = CheckSpec("function", tests=(f"assert clamp(-2) == {len(split)}", "assert clamp(0) == 0",
                                             "assert clamp(4) == 4"))
        spec = replace(spec, task=replace(spec.task, hidden_ref=code, provenance=None), hidden_tests=checks,
                       second_reference=replace(spec.second_reference, code=f"def clamp(x):\n    return max(x, {len(split)}) if x < 0 else x\n"))
        bank.add(g.generate_family(spec, seed=2, run=local_run))
    record = bank.to_record()
    assert len(record["families"]) == 3
    assert {r["split"] for r in record["families"]} == {"train", "dev", "held_out"}


@pytest.mark.parametrize("collision", ["id", "digest", "formatting"])
def test_family_bank_refuses_overlap_atomically(collision):
    g, spec = api(), family()
    bank = importlib.import_module("g6.rsi.family_bank").FamilyBank()
    first = g.generate_family(spec, seed=2, run=local_run)
    bank.add(first)
    other = family(split="dev", suffix="renamed")
    if collision == "id":
        other = replace(other, task=replace(other.task, family=spec.task.family, provenance=None))
    elif collision == "formatting":
        other = replace(other, task=replace(other.task, hidden_ref=other.task.hidden_ref + "\n", provenance=None))
    before = bank.to_record()
    with pytest.raises(TaskError, match="G6_E_RSI_FAMILY_OVERLAP"):
        bank.add(g.generate_family(other, seed=5, run=local_run))
    assert bank.to_record() == before


def test_default_runner_reuses_s1_confined_checks(monkeypatch):
    from g6.recursion import tasks

    g, spec = api(), family()
    seen = []

    def run_checks(task, code, *, repo, timeout_s):
        assert repo == ".build"
        assert task.public_tests in (spec.hidden_tests, spec.task.public_tests)
        seen.append(code)
        return local_run(code, task.public_tests, timeout_s)

    monkeypatch.setattr(tasks, "run_checks", run_checks)
    result = g.generate_family(spec, seed=5, repo=".build")
    assert result.tasks and spec.second_reference.code in seen


def test_runner_refusal_propagates_without_retry():
    class Refusal(RuntimeError):
        pass

    calls = []

    def refuse(*args):
        calls.append(args)
        raise Refusal("synthetic runner refusal")

    with pytest.raises(Refusal, match="synthetic runner refusal"):
        api().generate_family(family(), seed=3, run=refuse)
    assert len(calls) == 1


def test_generated_source_does_not_widen_other_source_policies():
    assert licence_for("generated").train_ok
    spec = family(split="held_out")
    assert "train" not in spec.task.split_eligibility
    with pytest.raises(TaskError):
        replace(spec.task, split="train")
    assert not licence_for("livecodebench").train_ok


def test_both_references_must_pass_public_checks_too():
    g, spec = api(), family()
    spec = replace(spec, task=replace(spec.task, public_tests=CheckSpec(
        "function", tests=("assert clamp(3) == 99",)), provenance=None))
    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_REFERENCE"):
        g.generate_family(spec, seed=3, run=local_run)


@pytest.mark.parametrize("field,value", [("missing_modules", ["dependency"]), ("os_errors", ["denied"]),
                                        ("collection_stopped", True), ("omitted_ids", ["test_hidden"])])
def test_infrastructure_errors_do_not_count_as_mutant_kills(field, value):
    g, spec = api(), family()

    def run(code, checks, timeout_s):
        raw = local_run(code, checks, timeout_s)
        if code not in (spec.task.hidden_ref, spec.second_reference.code):
            raw[field] = value
        return raw

    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_RUN"):
        g.generate_family(spec, seed=3, run=run)


def test_references_are_checked_three_times_for_stable_results():
    g, spec = api(), family()
    calls = []

    def run(code, checks, timeout_s):
        calls.append((code, checks))
        return local_run(code, checks, timeout_s)

    g.generate_family(spec, seed=3, run=run)
    for code in (spec.task.hidden_ref, spec.second_reference.code):
        assert calls.count((code, spec.hidden_tests)) == 3
        assert calls.count((code, spec.task.public_tests)) == 3


def test_changed_test_count_refuses_mutation_evidence():
    g, spec = api(), family()

    def run(code, checks, timeout_s):
        raw = local_run(code, checks, timeout_s)
        if code not in (spec.task.hidden_ref, spec.second_reference.code):
            raw.update(passed=0, failed=1)
        return raw

    with pytest.raises(TaskError, match="G6_E_RSI_GENERATOR_RUN"):
        g.generate_family(spec, seed=3, run=run)


def test_content_digest_ignores_a_clone_with_extra_checks():
    g, spec = api(), family()
    bank = importlib.import_module("g6.rsi.family_bank").FamilyBank()
    bank.add(g.generate_family(spec, seed=3, run=local_run))
    clone = family(split="dev", suffix="clone")
    clone = replace(clone, hidden_tests=CheckSpec("function", tests=clone.hidden_tests.tests + (
        "assert clamp(8) == 8",)))
    with pytest.raises(TaskError, match="G6_E_RSI_FAMILY_OVERLAP"):
        bank.add(g.generate_family(clone, seed=3, run=local_run))


def test_bank_restores_split_ownership_and_returns_detached_records():
    g = api()
    bank_api = importlib.import_module("g6.rsi.family_bank")
    bank = bank_api.FamilyBank()
    bank.add(g.generate_family(family(), seed=3, run=local_run))
    record = bank.to_record()
    restored = bank_api.FamilyBank.from_record(json.loads(json.dumps(record)))
    record["families"][0]["split"] = "held_out"
    assert restored.to_record() == bank.to_record()
    with pytest.raises(TaskError, match="G6_E_RSI_FAMILY_OVERLAP"):
        restored.add(g.generate_family(family(split="dev"), seed=3, run=local_run))


@pytest.mark.parametrize("kind", ["duplicate-id", "duplicate-digest", "split", "digest", "extra"])
def test_corrupt_bank_records_are_refused(kind):
    g = api()
    bank_api = importlib.import_module("g6.rsi.family_bank")
    bank = bank_api.FamilyBank()
    bank.add(g.generate_family(family(), seed=3, run=local_run))
    record = bank.to_record()
    if kind.startswith("duplicate"):
        copy = dict(record["families"][0])
        copy["split"] = "held_out"
        if kind == "duplicate-digest":
            copy["family_id"] = "renamed"
        record["families"].append(copy)
    elif kind == "extra":
        record["unknown"] = True
    else:
        record["families"][0][kind if kind == "split" else "content_digest"] = "invalid"
    with pytest.raises(TaskError, match="G6_E_RSI_FAMILY_(INVALID|OVERLAP)"):
        bank_api.FamilyBank.from_record(record)


@pytest.mark.parametrize("split,bug_class", [("train", "boundary"), ("dev", "other-bug")])
def test_bank_retains_repository_and_bug_family_ownership(split, bug_class):
    g = api()
    bank = importlib.import_module("g6.rsi.family_bank").FamilyBank()
    bank.add(g.generate_family(family(), seed=3, run=local_run))
    other = family(split=split, suffix="alias")
    other = replace(other, repository_id="fixture-a", bug_class=bug_class,
                    task=replace(other.task, hidden_ref="def clamp(x):\n    return 7 if x < 0 else x\n",
                                 provenance=None),
                    hidden_tests=CheckSpec("function", tests=("assert clamp(-2) == 7",
                                                             "assert clamp(0) == 0", "assert clamp(4) == 4")),
                    second_reference=replace(other.second_reference,
                                             code="def clamp(x):\n    return x if x >= 0 else 7\n"))
    with pytest.raises(TaskError, match="G6_E_RSI_FAMILY_OVERLAP"):
        bank.add(g.generate_family(other, seed=3, run=local_run))
