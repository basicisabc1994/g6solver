# SPDX-License-Identifier: Apache-2.0
"""Two domains for the self-improvement loop's tests, on ``FakeLLM`` only.

``WordProblems`` is the non-coding domain: V6b's GSM8K workflow as the registered study ran it (the task, the number
check, the exact-answer oracle and the declared prompts, from ``validation/v6b_metaharness/workflows.py``), over
word problems generated here, since the dataset is not in the repository. ``BugFixes`` is coding-shaped: each case is a
small broken function, and its judge compares the reply's syntax tree with the maintainers' fix, so model text is
parsed and never run. The loop gets the same five things from each, and nothing else.

Each has a planted weakness a declared model shows: below a marked instruction in the system prompt it answers only a
share of cases right, by a stable hash of the case, so a profile that carries the instruction wins and never loses.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.core.llm import FakeLLM
from g6.core.vault import Vault
from g6.harness import Check
from g6.metaharness import Model, ProposerView, SuiteCase
from g6.metaharness.domain import Execution, FailureRecord, Judge, SealedSplit, reason_coded
from g6.metaharness.pool import EditSpace

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("v6b_workflows_for_the_loop",
                                               _ROOT / "validation" / "v6b_metaharness" / "workflows.py")
v6b = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(v6b, "SPECS"):
    _spec.loader.exec_module(v6b)

GSM = v6b.SPECS["gsm8k"]
WORKING, TERSE = GSM.prompts[1], GSM.prompts[2]   # "Work through ... step by step", and "Give only the final number"
UNITS = "Convert every quantity to the same unit before you add or subtract."


def stable_share(text: str) -> float:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") / 2 ** 64


def word_problem(i: int) -> tuple[str, int]:
    a, b, c = 3 + i % 11, 4 + (i * 3) % 13, 1 + i % 7
    shape = i % 4
    if shape == 0:
        return f"Mia packs {a} boxes with {b} pencils each and gives away {c}. Case {i}: how many are left?", a * b - c
    if shape == 1:
        return f"A train carries {a * 10} people. {b} get off and {c} get on. Case {i}: how many are aboard?", a * 10 - b + c
    if shape == 2:
        return f"A rope is {a} metres long and {b * 100} centimetres are added. Case {i}: how many metres is it?", a + b
    return f"A jug holds {a + c} litres and {c * 1000} millilitres are poured. Case {i}: how many litres stay?", a


def needs_units(problem: str) -> bool:
    return "metres" in problem or "litres" in problem


@dataclass
class Tutor:
    """A model that does word problems: right on a share of them, more when the system prompt asks for the working,
    and on the units shapes only when it also asks for them (``two_steps``). ``collapsed`` is a planted later failure of
    the working prompt, for the monitor."""

    answers: dict[str, int]
    weak: float = 0.3
    strong: float = 0.95
    two_steps: bool = False
    collapsed: bool = False

    def __call__(self, system: str, user: str) -> str:
        problem = user.rsplit("Problem: ", 1)[-1].strip()
        answer = self.answers.get(problem, 0)
        working = "step by step" in system
        if self.collapsed and working:
            return "no number here"
        chance = self.strong if working else self.weak
        if self.two_steps and needs_units(problem) and UNITS not in system:
            chance = self.weak
        return f"Answer: {answer if stable_share(problem) < chance else answer + 1}"


class WordProblems:
    name = "word-problems"

    def __init__(self, folder: Path, *, pool: int = 240, judged: bool = True, weak: float = 0.3,
                 two_steps: bool = False, initial_prompt: str = TERSE, sealed: bool = True) -> None:
        self.pool = [SuiteCase(f"word-{i:04d}", *_case(word_problem(i))) for i in range(pool)]
        held = [SuiteCase(f"sealed-{i:04d}", *_case(word_problem(i))) for i in range(pool, pool + 40)]
        self.sealed_cases = held
        self.tutor = Tutor({c.input: int(c.expected) for c in (*self.pool, *held)}, weak=weak, two_steps=two_steps)
        self.llm = FakeLLM(self.tutor)
        self.initial_prompt, self._judged = initial_prompt, judged
        self.sealed_file = folder / "sealed-words" / "cases.jsonl"
        self._sealed = _seal(self.sealed_file, held, self.name) if sealed else None

    def development(self) -> tuple[SuiteCase, ...]:
        return tuple(self.pool)

    def sealed(self) -> SealedSplit | None:
        return self._sealed

    def execution(self) -> Execution:
        return Execution(task=GSM.task, checks=GSM.checks, models={"local": Model(self.llm)},
                         optional_checks=GSM.optional_checks, space=EditSpace({"retries": [0, 1]}),
                         initial={"routing": {"answer": "local"}, "prompt": self.initial_prompt})

    def judge(self) -> Judge | None:
        return Judge(GSM.oracle, answers_by="independent") if self._judged else None

    def failures(self, view: ProposerView) -> tuple[FailureRecord, ...]:
        return reason_coded(view)


def _case(problem: tuple[str, int]) -> tuple[str, str]:
    return problem[0], str(problem[1])


def _seal(path: Path, cases: list[SuiteCase], name: str) -> SealedSplit:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"id": c.case_id, "input": c.input, "expected": c.expected}) + "\n"
                            for c in cases), encoding="utf-8")
    return SealedSplit(Vault.seal([path.parent], ledger=None, vault_id=name), tuple(c.ref() for c in cases))


# The coding-shaped domain --------------------------------------------------------------------------------------------
CODE_HINT = "Read the failing test first, then change only the broken line."
_OPS =(("+", "*"), ("-", "+"), ("*", "-"))


def bug(i: int) -> tuple[str, str, str]:
    """A broken function, a test it fails, and the maintainers' fix."""
    wrong, right = _OPS[i % 3]
    name = f"step_{i}"
    broken = f"def {name}(a, b):\n    return a {wrong} b + {i}"
    fixed = f"def {name}(a, b):\n    return a {right} b + {i}"
    return broken, f"{name}(2, 3) fails", fixed


def same_tree(output: str, expected: Any) -> bool:
    """The reply's syntax tree against the maintainers' fix. Model text is parsed, never run."""
    try:
        return ast.dump(ast.parse(v6b.extract_code(output))) == ast.dump(ast.parse(expected["fixed"]))
    except (SyntaxError, ValueError):
        return False


@dataclass
class Engineer:
    fixes: dict[str, str]
    weak: float = 0.3
    strong: float = 0.95

    def __call__(self, system: str, user: str) -> str:
        task = next((t for t in self.fixes if t in user), "")
        chance = self.strong if CODE_HINT in system else self.weak
        fixed = self.fixes.get(task, "def nothing():\n    return 0")
        return fixed if stable_share(task) < chance else fixed.replace("return", "return -")


class BugFixes:
    name = "bug-fixes"

    def __init__(self, folder: Path, *, pool: int = 240, judged: bool = True) -> None:
        self.pool, sealed = [], []
        for i in range(pool + 40):
            broken, failing, fixed = bug(i)
            case = SuiteCase(f"bug-{i:04d}", f"{broken}\n\n{failing}. Reply with the fixed function.",
                             {"tests": [failing], "fixed": fixed})
            (self.pool if i < pool else sealed).append(case)
        self.sealed_cases = sealed
        self.engineer = Engineer({c.input: c.expected["fixed"] for c in (*self.pool, *sealed)})
        self.llm = FakeLLM(self.engineer)
        self._judged = judged
        self.sealed_file = folder / "sealed-bugs" / "cases.jsonl"
        self._sealed = _seal(self.sealed_file, sealed, self.name)

    def development(self) -> tuple[SuiteCase, ...]:
        return tuple(self.pool)

    def sealed(self) -> SealedSplit | None:
        return self._sealed

    def execution(self) -> Execution:
        check = Check("the reply parses and defines a function", v6b._parses_with_def, check_id="defines-function",
                      known_bad=("", "def broken(:\n    pass"), known_good="def f(x):\n    return x")
        return Execution(task="Fix this function.\n\n{input}", checks=(check,), models={"local": Model(self.llm)},
                         space=EditSpace({"retries": [0, 1]}))

    def judge(self) -> Judge | None:
        return Judge(same_tree, answers_by="independent") if self._judged else None

    def failures(self, view: ProposerView) -> tuple[FailureRecord, ...]:
        names = {"wrong_answer": "accepted_defect", "check_rejected": "refused_patch"}
        return tuple(FailureRecord(r.case_id, names.get(r.cause, r.cause), r.changeable, r.note, r.independently_observed)
                     for r in reason_coded(view))


# Proposers ------------------------------------------------------------------------------------------------------------
def proposes(*changes: dict) -> FakeLLM:
    """A proposer that offers each change in turn, as if it had read the failures, then the last one again."""
    made: list[dict] = []

    def reply(system: str, user: str) -> str:
        change = changes[min(len(made), len(changes) - 1)]
        made.append(change)
        return json.dumps(change)

    return FakeLLM(reply)


class Clock:
    """Advances a fixed step per reading, so wall clock depends only on how often it is read."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 0.001
        return self.t
