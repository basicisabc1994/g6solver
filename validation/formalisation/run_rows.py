# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Run one section 15.7 row on one frozen manifest; write the card and every item's receipt.

The rows that need no model run here on the mutation cohort: ``proposal`` (checks 1 and 2 against right formulas and
labelled mutants), ``rendering`` (its deterministic half: a right formula with wrong words, and a wrong formula with
right words, must both fail the scope binding), ``correspondence`` (right and wrong code, and a true model result
with code that runs otherwise) and ``archive_defects``. ``questions`` and ``cost`` need model drafts and a person:
their items are recorded ``unrun``. No row calls a model here; ``callers`` is accepted so a test can show that.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

if __package__ in (None, ""):   # run as a script: load this folder as a package first, then run its main
    import importlib.util
    import sys

    _root = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location("g6_validation_formalisation", _root / "__init__.py",
                                                   submodule_search_locations=[str(_root)])
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _package
    _spec.loader.exec_module(_package)
    raise SystemExit(_package.run_rows.main(sys.argv[1:]))

from g6.harness.formalisation import smt_adapter, smt_ir
from g6.harness.formalisation.adapters import ExampleObservation
from g6.harness.formalisation.checks import check_domain, check_examples
from g6.harness.formalisation.code_check import check_code
from g6.harness.formalisation.render import render
from g6.harness.formalisation.scope import bind_scope, check_scope
from g6.harness.confirmed_expectation import ExpectationStandard

from . import cards, mutations
from .manifest import RETIRED, Manifest, confirmatory, load

#: The four archive defect fixture groups of design 15.4, and the slice that makes each runnable.
ARCHIVE_GROUPS = ("smt_parse_only", "lean_unrelated_definition", "tlc_unavailable", "curry_howard")


def _statement(case, text: str | None = None, domain=None):
    return smt_adapter.statement(entry_point=f"cases.{case.case_id}", inputs=case.inputs, result_sort=case.result_sort,
                                 relation=case.reference if text is None else text,
                                 domain=case.domain if domain is None else domain)


def _example(case, reference: smt_ir.Term) -> ExampleObservation | None:
    """The first table row as the confirmed example, with a rejected outcome the reference excludes there."""
    for inputs, expected in case.table:
        for other in ([not expected] if type(expected) is bool else [expected + 1, expected - 1]):
            if not smt_ir.evaluate(reference, {**inputs, "result": other}):
                return ExampleObservation(expectation_id=f"{case.case_id}:example", answer_sha256="", function="",
                                          args=tuple(inputs[name] for name, _ in case.inputs), kwargs=(),
                                          accepted={"returns": expected}, rejected=({"returns": other},))
    return None


def _decision(*outcomes) -> str:
    statuses = [one.status for one in outcomes]
    if "failed" in statuses:
        return "refused"
    if all(status == "passed" for status in statuses):
        return "accepted"
    if "unsupported" in statuses:
        return "unsupported"
    if "unavailable" in statuses:
        return "unavailable"
    timed = any("TIMEOUT" in q.code for one in outcomes for q in one.queries)
    return "timed_out" if timed else "unrun"


def _proposal(manifest: Manifest) -> list[dict]:
    out = []
    for case in manifest.cases:
        sorts = {**dict(case.inputs), "result": case.result_sort}
        reference = smt_ir.read(case.reference, sorts)
        example = _example(case, reference)
        items = [("reference", "right", "", _statement(case), None)]
        for kind, mutant in mutations.mutants(reference, sorts):
            label = mutations.labelled(reference, mutant, case.domain, sorts=sorts, table=case.table,
                                       example=dict(zip([n for n, _ in case.inputs], example.args)) if example else None)
            verdict = {"wrong": "wrong", "equivalent": "right", "unlabelled": "unlabelled"}[label["label"]]
            items.append((kind, verdict, label["stratum"], _statement(case, smt_ir.write(mutant)), label["witness"]))
        for kind, verdict, stratum, statement, witness in items:
            first = check_examples(statement, [example] if example else [])
            second = check_domain(statement, [example] if example else [])
            out.append({"case_id": case.case_id, "item": kind, "label": verdict, "stratum": stratum,
                        "decision": _decision(first, second), "statement_sha256": statement.sha256,
                        "after_check_1": first.status, "after_check_2": second.status, "witness": witness})
    return out


def _rendering(manifest: Manifest) -> list[dict]:
    out = []
    standard = ExpectationStandard(())
    for case in manifest.cases:
        statement = _statement(case)
        right = render(statement)
        binding = bind_scope(statement, right, standard, ())

        def scored(item, label, shown, bound=binding, statement=statement):
            found = check_scope(dataclasses.replace(bound, rendering_sha256=shown.sha256), statement, shown, standard)
            out.append({"case_id": case.case_id, "item": item, "label": label, "stratum": "",
                        "decision": "accepted" if found.status == "passed" else "refused",
                        "rendering_sha256": shown.sha256})

        scored("right formula, right words", "right", right)
        for kind, forged in mutations.wrong_renderings(statement):   # a right formula shown with a mutant's words
            scored(f"right formula, wrong words ({kind})", "wrong", forged)
        sorts = smt_adapter.ADAPTER.sorts(statement)
        for kind, mutant in mutations.mutants(smt_ir.read(statement.text, sorts), sorts):
            wrong = _statement(case, smt_ir.write(mutant))           # a wrong formula shown with the right words
            shown = dataclasses.replace(right, formal_text=wrong.text, statement_sha256=wrong.sha256)
            found = check_scope(dataclasses.replace(binding, statement_sha256=wrong.sha256,
                                                    rendering_sha256=shown.sha256), wrong, shown, standard)
            out.append({"case_id": case.case_id, "item": f"wrong formula, right words ({kind})", "label": "wrong",
                        "stratum": "", "decision": "accepted" if found.status == "passed" else "refused",
                        "rendering_sha256": shown.sha256})
    return out


def _runner(source: str, name: str):
    space: dict[str, Any] = {}
    exec(compile(source, f"<fixture {name}>", "exec"), space)   # noqa: S102 - the manifest's own reference functions

    def run(call):
        try:
            return {"returns": space[name](*call["args"], **call["kwargs"])}
        except Exception as exc:   # noqa: BLE001 - the observation is the raised type's name
            return {"raises": type(exc).__name__}
    return run


def _correspondence(manifest: Manifest) -> list[dict]:
    out = []
    for case in manifest.cases:
        statement = _statement(case)
        name = case.case_id
        if not case.code:
            out.append({"case_id": name, "item": "no reference code", "label": "right", "stratum": "",
                        "decision": "unsupported"})
            continue

        def scored(item, label, source, run, statement=statement, name=name):
            found = check_code(statement, source, name, run=run)
            decision = {"pass": "accepted", "fail": "refused"}.get(found.result, "unsupported")
            if found.result == "not_established" and found.replayed == "not_reproduced":
                decision = "refused"   # the check declined to carry a model result over to code that ran otherwise
            out.append({"case_id": name, "item": item, "label": label, "stratum": "", "decision": decision,
                        "result": found.result, "replayed": found.replayed, "why": found.why})

        scored("right code", "right", case.code, _runner(case.code, name))
        for i, wrong in enumerate(case.wrong_code):
            scored(f"wrong code {i}", "wrong", wrong, _runner(wrong, name))
            scored(f"true model result, code {i} ran instead", "wrong", case.code, _runner(wrong, name))
    return out


def _archive_defects(manifest: Manifest) -> list[dict]:
    from g6.harness.formalisation.query import witness

    read = smt_ir.read("(assert false)", {})
    asked = witness({}, [smt_ir.write(read)], statement_sha256="0" * 64)
    minted = hasattr(read, "reading") or hasattr(read, "status")
    out = [{"case_id": "smt_parse_only", "item": "parse success mints no receipt", "label": "wrong", "stratum": "",
            "decision": "accepted" if minted else "refused"},
           {"case_id": "smt_parse_only", "item": "a real consistency query records UNSAT", "label": "right",
            "stratum": "", "decision": "accepted" if asked.native == "unsat" else
            "unavailable" if asked.reading == "unavailable" else "refused", "native": asked.native}]
    out += [{"case_id": group, "item": "not built in this version of the row", "label": "wrong", "stratum": "",
             "decision": "unrun"} for group in ARCHIVE_GROUPS[1:]]
    return out


def _needs_people(manifest: Manifest) -> list[dict]:
    return [{"case_id": case.case_id, "item": "needs model drafts and a person", "label": "right", "stratum": "",
             "decision": "unrun"} for case in manifest.cases]


ROWS = {"proposal": _proposal, "rendering": _rendering, "correspondence": _correspondence,
        "archive_defects": _archive_defects, "questions": _needs_people, "cost": _needs_people}


def run(manifest_path: Path, *, out_dir: Path, registry: Path = RETIRED, confirm: bool = False,
        callers: Any = None) -> cards.Card:
    """Run the frozen manifest's row and write ``card.md``, ``card.json`` and ``receipts.json`` under ``out_dir``.
    With ``confirm`` the manifest must be a final cohort that was never retired. ``callers`` is never called."""
    manifest = load(manifest_path)
    if confirm:
        confirmatory(manifest, registry=registry)
    results = ROWS[manifest.row](manifest)
    made = cards.card(results, manifest)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "receipts.json").write_text(json.dumps(results, indent=1, sort_keys=True, default=str) + "\n",
                                           encoding="utf-8", newline="\n")
    (out_dir / "card.json").write_text(json.dumps(dataclasses.asdict(made), indent=1, sort_keys=True) + "\n",
                                       encoding="utf-8", newline="\n")
    (out_dir / "card.md").write_text(cards.render_markdown(made), encoding="utf-8", newline="\n")
    return made


def main(argv: list[str]) -> int:
    """``python validation/formalisation/run_rows.py <manifest> <out dir> [--confirm]``"""
    if len(argv) < 2:
        print(main.__doc__)
        return 2
    print(cards.render_markdown(run(Path(argv[0]), out_dir=Path(argv[1]), confirm="--confirm" in argv)))
    return 0