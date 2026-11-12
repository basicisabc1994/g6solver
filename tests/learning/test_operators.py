# SPDX-License-Identifier: Apache-2.0
"""Content-changing operators under trusted lineage caps (0.3 design 14.2): a mapped operator changes the instruction
and so the digest, an unmapped one makes no child, caps are read from the ledger, and a child starts unmeasured."""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.learning import operators
from g6.learning.operators import (LINEAGE, LINEAGE_KIND, MAPPINGS, MAX_CHILDREN, MAX_GENERATION, OPERATOR_TEXT, Lineage,
                                   LineageError, derive, lineage, proposal_only)
from g6.learning.paired_eval import PairedEvaluator, receipts
from g6.learning.schema import TaskInstance
from g6.learning.strategy import MAX_INSTRUCTION, StrategyArtefact, StrategyStore
from g6.learning.worth import WorthPolicy, worth

ROOT = StrategyArtefact(instruction="Propagate stated constraints before searching.", trigger="always", scope="search")
EASY = WorthPolicy(min_complete_cases=2, min_discordant=1, alpha=0.5, max_regressions=0, max_cost_ratio=5.0,
                   max_incomplete_share=0.5)


def rig(tmp_path: Path) -> tuple[StrategyStore, ChainLedger, str]:
    store = StrategyStore(tmp_path / "strategies")
    return store, ChainLedger(tmp_path / "lineage.jsonl"), store.put(ROOT)


def test_each_mapped_operator_changes_the_instruction_and_the_digest(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    assert set(MAPPINGS) == set(OPERATOR_TEXT) == {"regression", "boundary", "counterexample", "metamorphic",
                                                    "decompose"}
    digests = {root}
    for operator in ("regression", "boundary", "counterexample", "metamorphic"):
        child = derive(root, operator, store=store, ledger=ledger)
        assert child.instruction == ROOT.instruction + "\n" + OPERATOR_TEXT[operator]
        assert dataclasses.replace(child, instruction=ROOT.instruction) == ROOT          # every other field copied
        assert store.resolve(child.content_digest) == child and child.content_digest not in digests
        digests.add(child.content_digest)
        assert lineage(ledger)[child.content_digest] == Lineage(child.content_digest, root, operator, 1)
    again = derive(root, "regression", store=store, ledger=ledger)                       # the same child, kept once
    assert again.content_digest in digests and len(lineage(ledger)) == 4


def test_an_unmapped_operator_makes_no_child(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    for operator in ("prompt_revision", "invented_operator", ""):
        assert proposal_only(operator)
        with pytest.raises(LineageError) as caught:
            derive(root, operator, store=store, ledger=ledger)
        assert caught.value.code == LINEAGE
    assert store.digests() == (root,) and lineage(ledger) == {} and not proposal_only("boundary")


def test_generation_and_child_caps_come_from_the_ledger(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    for operator in ("regression", "boundary", "counterexample", "metamorphic"):
        derive(root, operator, store=store, ledger=ledger)
    with pytest.raises(LineageError, match="children"):                    # a fifth child of one parent
        derive(root, "decompose", store=store, ledger=ledger)
    assert MAX_CHILDREN == 4 and MAX_GENERATION == 3

    first = derive(root, "regression", store=store, ledger=ledger)
    second = derive(first.content_digest, "boundary", store=store, ledger=ledger)
    third = derive(second.content_digest, "counterexample", store=store, ledger=ledger)
    assert lineage(ledger)[third.content_digest].generation == 3
    with pytest.raises(LineageError, match="generation"):                  # a fourth generation
        derive(third.content_digest, "metamorphic", store=store, ledger=ledger)
    # the caller passes only a digest: nothing in the parent's text, and no argument, can hide its ancestry
    assert list(inspect.signature(derive).parameters) == ["parent_digest", "operator", "store", "ledger"]
    restored = StrategyStore(tmp_path / "strategies").put(third)           # stored again "as a root": the same bytes
    assert restored == third.content_digest
    with pytest.raises(LineageError, match="generation"):
        derive(restored, "metamorphic", store=store, ledger=ledger)
    before = store.digests()
    with pytest.raises(LineageError):
        derive(third.content_digest, "decompose", store=store, ledger=ledger)
    assert store.digests() == before                                       # a refused child is not stored


def test_an_instruction_past_the_limit_makes_no_child(tmp_path: Path) -> None:
    store, ledger, _ = rig(tmp_path)
    long = store.put(dataclasses.replace(ROOT, instruction="x" * (MAX_INSTRUCTION - 10)))
    with pytest.raises(LineageError, match="not a strategy artefact"):
        derive(long, "regression", store=store, ledger=ledger)
    assert lineage(ledger) == {} and len(store.digests()) == 2


def test_an_unknown_or_changed_parent_makes_no_child(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    with pytest.raises(ValueError, match="G6_E_STRATEGY_UNKNOWN"):
        derive("0" * 64, "regression", store=store, ledger=ledger)
    path = tmp_path / "strategies" / f"{root}.json"
    path.write_bytes(path.read_bytes().replace(b"before searching", b"after searching"))
    with pytest.raises(ValueError, match="G6_E_STRATEGY_BYTES"):
        derive(root, "regression", store=store, ledger=ledger)


def test_a_child_starts_unmeasured(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    receipts_ledger = ChainLedger(tmp_path / "receipts.jsonl")
    cases = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"c{n}")
             for n in range(1, 5)]

    def agent(prompt: str) -> str:
        return str(2 * int(prompt.rsplit("What is ", 1)[1].split(" ")[0])) if "Optional hints" in prompt else "?"

    ticks = iter(range(10_000))
    evaluator = PairedEvaluator(store=store, ledger=receipts_ledger, agent=agent, agent_id="a", cohort_id="c",
                                split="heldout", seed=1, budget={"max_calls": 99, "max_seconds": 9, "max_usd": 9},
                                environment_digest="e" * 64, evaluator_ref="test://e",
                                clock=lambda: float(next(ticks)))
    evaluator.run(cases, candidate=root, replay_id="r1")
    child = derive(root, "boundary", store=store, ledger=ledger)
    kept = receipts(receipts_ledger)
    assert worth(kept, root, EASY).status == "measured" and worth(kept, root, EASY).meets_bar
    inherited = worth(kept, child.content_digest, EASY)
    assert inherited.status == "unmeasured" and inherited.cases == 0 and not inherited.meets_bar


def test_lineage_cannot_be_asserted(tmp_path: Path) -> None:
    store, ledger, root = rig(tmp_path)
    deep = derive(derive(derive(root, "regression", store=store, ledger=ledger).content_digest, "boundary",
                         store=store, ledger=ledger).content_digest, "counterexample", store=store, ledger=ledger)
    claimed = Lineage(deep.content_digest, root, "counterexample", 1)      # built by hand, never appended
    assert lineage(ledger)[deep.content_digest].generation == 3 and claimed.generation == 1
    with pytest.raises(LineageError, match="generation"):
        derive(deep.content_digest, "metamorphic", store=store, ledger=ledger)
    other = ChainLedger(tmp_path / "another.jsonl")                        # an empty ledger knows no lineage at all:
    assert derive(deep.content_digest, "metamorphic", store=store, ledger=other).content_digest in store.digests()
    assert lineage(other)[next(iter(lineage(other)))].generation == 1     # which is why admission names its ledger
    assert all(entry["kind"] == LINEAGE_KIND for entry in ledger.entries())


def test_operators_call_no_model_and_keep_no_gate() -> None:
    tree = ast.parse(inspect.getsource(operators))
    imported = " ".join(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))
    for module in ("g6.core.llm", "g6.llm_router", "g6.agents", "consent_callers", "host_models"):
        assert module not in imported, module
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert not [name for name in names | set(operators.__all__)
                if any(word in name.lower() for word in ("promote", "assess", "approve"))]


def test_a_lineage_row_written_by_hand_and_another_ledger_do_not_lift_a_cap(tmp_path: Path) -> None:
    """The caps are read from the strategy's own bytes and from the store's kept files. A ledger whose only row
    calls a third-generation strategy a first generation, an empty ledger, and another store that holds the same
    bytes each leave the fourth generation refused."""
    store, ledger, root = rig(tmp_path)
    deep = derive(derive(derive(root, "regression", store=store, ledger=ledger).content_digest, "boundary",
                         store=store, ledger=ledger).content_digest, "counterexample", store=store, ledger=ledger)
    forged = ChainLedger(tmp_path / "forged.jsonl")                        # ChainLedger.append takes no key
    forged.append(LINEAGE_KIND, deep.content_digest,
                  dataclasses.asdict(Lineage(deep.content_digest, root, "counterexample", 1)))
    before = store.digests()
    with pytest.raises(LineageError, match="generation"):
        derive(deep.content_digest, "metamorphic", store=store, ledger=forged)
    with pytest.raises(LineageError, match="generation"):
        derive(deep.content_digest, "metamorphic", store=store, ledger=ChainLedger(tmp_path / "empty.jsonl"))
    elsewhere = StrategyStore(tmp_path / "elsewhere")
    elsewhere.put(deep)
    with pytest.raises(LineageError, match="generation"):
        derive(deep.content_digest, "metamorphic", store=elsewhere, ledger=ChainLedger(tmp_path / "third.jsonl"))
    assert store.digests() == before and elsewhere.digests() == (deep.content_digest,)
    with pytest.raises(LineageError, match="not what the stored bytes give|does not read again"):
        lineage(forged, store)                                             # the row is held against the bytes
    assert lineage(ledger, store) == lineage(ledger)                       # the real log reads again
    # a fifth child is counted from the files the store keeps, whatever a ledger holds
    for operator in ("regression", "boundary", "counterexample", "metamorphic"):
        derive(root, operator, store=store, ledger=ledger)
    with pytest.raises(LineageError, match="children"):
        derive(root, "decompose", store=store, ledger=ChainLedger(tmp_path / "fourth.jsonl"))
