# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/freeze-holds: the ten findings this repair closes, as the reviewer reproduced them, with the
reviewer's support code (its own temporary folder aside: pytest's serves here). Findings 5, 8 and 11 are ruled out as
documented limits of the freeze's threat model, honest drift (the design's freeze section), so they are not here. FakeLLM
is the only model; finding 10 starts PowerShell once to make a directory junction, nothing else."""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import weakref
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM
from g6.core.vault import AccessDenied
from g6.metaharness import shadow
from g6.metaharness.domain import PAID_MODEL, AnswerUnidentifiable, Cut, Judge, answer_identity
from g6.metaharness.loop import (
    ANSWER_UNIDENTIFIABLE,
    CANNOT_RECORD,
    EVALUATOR_IN_REACH,
    FROZEN_CHANGED,
    ShadowLoop,
    _answer_problems,
    _Halted,
)
from g6.metaharness.schema import SuiteCase


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rig = load(Path(__file__).with_name("test_freeze_holds_review2.py"), "frz3_existing_rig")


def enrolled(folder, **kwargs):
    return rig.enrolled(folder, **kwargs)


def archive_loop(folder, monkeypatch):
    judge_dir = folder / "judge"
    judge_dir.mkdir()
    archive = judge_dir / "rules.zip"
    module_name = "frz3_zip_rules"

    def write_rule(accept):
        source = ("def accepts(output, expected):\n"
                  + ('    return output == "Answer: " + str(expected)\n' if accept else "    return False\n"))
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
            stream.writestr(module_name + ".py", source)

    write_rule(True)
    monkeypatch.syspath_prepend(str(archive))
    helper = importlib.import_module(module_name)
    judge_file = judge_dir / "judge.py"
    judge_file.write_text(f"import {module_name}\n"
                          "def verdict(output, expected):\n"
                          f"    return {module_name}.accepts(output, expected)\n", encoding="utf-8")
    module = load(judge_file, "frz3_zip_judge")
    domain = rig.domains.WordProblems(folder)
    domain.judge = lambda: Judge(module.verdict, "independent")
    loop, _ = enrolled(folder, domain=domain)

    def change():
        write_rule(False)
        importlib.invalidate_caches()
        importlib.reload(helper)

    return loop, module, change


# Findings 1, 2, 3, 12, 13: the answers -------------------------------------------------------------------------------

def test_dict_iteration_order_is_part_of_a_judge_visible_answer():
    left = {"accept": 1, "reject": 2}
    right = {"reject": 2, "accept": 1}
    judge = lambda output, expected: output == next(iter(expected))  # noqa: E731
    assert judge("accept", left) and not judge("accept", right)
    cut = Cut((), (), (SuiteCase("ordered-answer", "input", left),), ())
    if _answer_problems(cut):
        return  # Refusing an answer with no exact encoding also satisfies the contract.
    try:
        first, second = answer_identity(left), answer_identity(right)
    except AnswerUnidentifiable:
        return
    assert first != second, "judge-distinguishable dict insertion orders share an answer identity"


def test_answer_identity_preserves_shared_mutable_references():
    shared = []
    left, right = [shared, shared], [[], []]
    judge = lambda output, expected: expected[0] is expected[1]  # noqa: E731
    assert judge("", left) and not judge("", right)
    cut = Cut((), (), (SuiteCase("aliased-answer", "input", left),), ())
    if _answer_problems(cut):
        return
    try:
        first, second = answer_identity(left), answer_identity(right)
    except AnswerUnidentifiable:
        return
    assert first != second, "shared and independent mutable answer members share an answer identity"


class TextAnswer(str):
    pass


def test_builtin_subclass_value_and_state_have_separate_boundaries():
    left = TextAnswer("")
    left.extra = {}
    prefix = ("\x00\x00\x00\rbuiltins.dict" + "\x00" * 7 + "\x36"
              + "\x00\x00\x00\x0cbuiltins.str" + "\x00" * 7 + "\x05extra")
    right = TextAnswer(prefix)
    judge = lambda output, expected: str(expected) == output  # noqa: E731
    assert judge("", left) and not judge("", right)
    assert left.__dict__ != right.__dict__
    cut = Cut((), (), (SuiteCase("subclass-answer", "input", left),), ())
    if _answer_problems(cut):
        return
    try:
        first, second = answer_identity(left), answer_identity(right)
    except AnswerUnidentifiable:
        return
    assert first != second, "distinct string-subclass values and state concatenate to the same identity"


def test_type_module_and_qualified_name_have_separate_boundaries(tmp_path):
    package = tmp_path / "__init__.py"
    package.write_text("class inner:\n    class Answer(str):\n        pass\n", encoding="utf-8")
    child = tmp_path / "inner.py"
    child.write_text("class Answer(str):\n    pass\n", encoding="utf-8")
    parent_module = load(package, "frz3_types")
    child_module = load(child, "frz3_types.inner")
    left, right = parent_module.inner.Answer("42"), child_module.Answer("42")
    judge = lambda output, expected: type(expected).__module__ == "frz3_types"  # noqa: E731
    assert judge("", left) and not judge("", right)
    assert type(left) is not type(right)
    cut = Cut((), (), (SuiteCase("type-boundary-answer", "input", left),), ())
    if _answer_problems(cut):
        return
    try:
        first, second = answer_identity(left), answer_identity(right)
    except AnswerUnidentifiable:
        return
    assert first != second, "a nested type and a type from a submodule share the same dotted type identity"


class WeakList(list):
    pass


def test_answer_without_exact_encoding_is_refused_by_case_id_instead_of_raising(tmp_path):
    target = WeakList([42])
    answer = weakref.proxy(target)
    domain = rig.domains.WordProblems(tmp_path)
    domain.pool[0] = replace(domain.pool[0], expected=answer)
    error = None
    try:
        loop = ShadowLoop(domain, tmp_path / "loop", rig.SETTINGS,
                          proposer=rig.domains.proposes({"prompt": rig.domains.WORKING}),
                          now=lambda: rig.NOW, clock=rig.domains.Clock())
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        error = exc
    assert error is None, f"unidentifiable answer escaped enrolment refusal: {type(error).__name__}: {error}"
    reply = loop.enrol("Review owner")
    assert reply["warning_card"]["code"] == ANSWER_UNIDENTIFIABLE
    assert domain.pool[0].case_id in reply["message"]


# Findings 4, 6, 10: roots pinned whole ---------------------------------------------------------------------------------

def test_judge_zip_under_root_cannot_change_before_adoption(tmp_path, monkeypatch):
    loop, judge, change = archive_loop(tmp_path, monkeypatch)
    assert judge.verdict("Answer: 42", "42")
    original = shadow._measured
    edited = []

    def prepare(report):
        change()
        edited.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", prepare)
    loop.tick()
    assert edited and not judge.verdict("Answer: 42", "42")
    assert not loop.mh.adoption_rows(), "adopted after imported judge code in an in-root ZIP changed"


def test_file_backed_judging_rule_is_frozen_even_when_it_is_json(tmp_path, monkeypatch):
    judge_dir = tmp_path / "judge"
    judge_dir.mkdir()
    rules = judge_dir / "rules.json"
    rules.write_text('{"enabled": true}', encoding="utf-8")
    source = judge_dir / "judge.py"
    source.write_text("import json\nfrom pathlib import Path\n"
                      "def verdict(output, expected):\n"
                      '    enabled = json.loads(Path(__file__).with_name("rules.json").read_text())["enabled"]\n'
                      '    return enabled and output == "Answer: " + str(expected)\n', encoding="utf-8")
    judge = load(source, "frz3_data_judge")
    domain = rig.domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(judge.verdict, "independent")
    loop, _ = enrolled(tmp_path, domain=domain)
    assert judge.verdict("Answer: 42", "42")
    original = shadow._measured
    edited = []

    def prepare(report):
        rules.write_text('{"enabled": false}', encoding="utf-8")
        edited.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", prepare)
    loop.tick()
    assert edited and not judge.verdict("Answer: 42", "42")
    assert not loop.mh.adoption_rows(), "adopted after a file-backed judge rule inside its root changed"


def test_judge_root_cannot_reach_loop_folder_through_directory_junction(tmp_path):
    judge_dir, loop_dir = tmp_path / "judge", tmp_path / "loop"
    judge_dir.mkdir()
    loop_dir.mkdir()
    link = judge_dir / "writable_alias"
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "New-Item -ItemType Junction -Path $env:REVIEW_LINK -Target $env:REVIEW_TARGET | Out-Null"],
                       env={**os.environ, "REVIEW_LINK": str(link), "REVIEW_TARGET": str(loop_dir)},
                       check=True, capture_output=True, timeout=20)
    else:
        link.symlink_to(loop_dir, target_is_directory=True)
    assert link.resolve() == loop_dir.resolve()
    source = judge_dir / "judge.py"
    source.write_text("def verdict(output, expected):\n"
                      '    return output == "Answer: " + str(expected)\n', encoding="utf-8")
    judge = load(source, "frz3_junction_judge")
    domain = rig.domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(judge.verdict, "independent")
    with pytest.raises(ValueError, match=EVALUATOR_IN_REACH):
        ShadowLoop(domain, loop_dir, rig.SETTINGS,
                   proposer=rig.domains.proposes({"prompt": rig.domains.WORKING}),
                   now=lambda: rig.NOW, clock=rig.domains.Clock())


# Finding 7: the fault path -------------------------------------------------------------------------------------------

def test_fault_cycle_uses_the_same_final_freeze_check_and_coding_rule(tmp_path, monkeypatch):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    loop, _ = enrolled(tmp_path, domain_file=declaration)
    edited = []

    def prepare(report):
        declaration.write_text("# changed during row preparation\n", encoding="utf-8")
        edited.append(True)
        raise OSError("row preparation failed")

    monkeypatch.setattr(shadow, "_measured", prepare)
    # This is exactly the exception-to-fault handoff in Runner._tick, with no runner started.
    try:
        loop.tick()
    except Exception as exc:  # noqa: BLE001 - the hand-off under test
        loop.fault(exc)
    assert edited and "domain_file" in loop._changed()
    assert not loop.mh.adoption_rows()
    assert loop.log.cycles()[-1]["code"] == FROZEN_CHANGED, "fault fallback bypassed final freeze check and _coded"


# Finding 9: a safety refusal is durable ------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["unmetered", "sealed_breach", "barred"])
def test_safety_refusal_survives_cycle_write_failure_and_restart(tmp_path, monkeypatch, status):
    domain = rig.domains.WordProblems(tmp_path)
    reached = []

    def propose(system, user):
        reached.append(status)
        if status == "unmetered":
            loop.requests.unseen = True
        elif status == "sealed_breach":
            try:
                domain.sealed_file.read_text(encoding="utf-8")
            except AccessDenied:
                pass
        else:
            raise _Halted(PAID_MODEL, "fake caller changed to a paid backend")
        return '{"prompt": "Work through the problem step by step."}'

    loop, _ = enrolled(tmp_path, domain=domain, proposer=FakeLLM(propose))

    def disk_failure(row):
        raise OSError("simulated failure of the cycle append")

    monkeypatch.setattr(loop.log, "cycle", disk_failure)
    reply = loop.tick()
    assert reached and reply["warning_card"]["code"] == CANNOT_RECORD
    assert not loop.mh.adoption_rows()
    restarted = ShadowLoop(domain, tmp_path / "loop", rig.SETTINGS,
                           proposer=rig.domains.proposes({"prompt": rig.domains.WORKING}),
                           now=lambda: rig.NOW, clock=rig.domains.Clock())
    assert restarted._refused() is not None, f"{status} safety refusal was lost on restart after one failed cycle append"
