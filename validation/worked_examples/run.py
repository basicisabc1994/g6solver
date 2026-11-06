# SPDX-License-Identifier: Apache-2.0
"""The worked examples' runner (PLAN.md sections 3 to 5): images, validation, the key check, one run in the product's
real flow, and the independent check. Every step writes a write-once folder in the archive (``wecommon.Archive``),
outside the repository.

Commands (``python -I validation/worked_examples/run.py <command>``, from the repository's root):

* ``base [--ref origin/claude/launch]``: G6's base image from that ref's commit: G6 as the README installs it
  (``python -m venv --copies``, ``pip install "<source>[all]"``) and Claude Code 2.1.309, on ``g6-a2-linux``.
* ``images --task <id> --g6 <commit>``: the task's image (the project at the parent commit, history-free, with its
  ``.venv``) and its oracle image (the same, plus the maintainers' fix kept apart in ``/oracle``).
* ``validate --task <id> --g6 <commit>``: FAIL_TO_PASS and PASS_TO_PASS, with no network and no model.
* ``freecheck --task <id> --g6 <commit>``: one confined review with the README install and a linked ``.venv``, no key.
* ``run --task <id> --g6 <commit>``: the key check, then Claude Code headless with the using-g6 skill (paid).
* ``check --task <id> [--run <folder>]``: the independent check of a run's final patch and each round's patch.
* ``replay --task <id> --code fix|<patch file> --test <test file>``: a reproduction run on the maintainers' fix or on a
  patch, for judging a finding.

Docker containers, images and networks this script makes are named ``g6we*``; it stops and removes only those it
started, by name. The key is read only through ``g6.core.keys`` and passed by name into the environment of the one
``docker exec`` that starts Claude Code (and of the key check's driver); every archived text is masked for it.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import box_mcp  # noqa: E402
import observe  # noqa: E402
import selection  # noqa: E402
import wecommon as c  # noqa: E402

REPO_ROOT = HERE.parents[1]
TASKS = HERE / "tasks.json"
TASKS_FRESH = HERE / "tasks_fresh.json"   # bugs the loop has not seen (PLAN.md, 2026-10-31)
TASKS_UNSEEN = HERE / "tasks_unseen.json"   # stage 2: bugs in repositories never used in training (PLAN.md, 2026-10-31 evening)
TASKS_STAGE4 = HERE / "tasks_stage4.json"   # stage 4: unseen bugs drawn by stage 2's rule from ranks 45 to 100 (PLAN.md, 2026-11-04)
TASKS_DEV = HERE / "tasks_dev.json"   # stage 3: new bugs for G6 to learn from, none of them judged on
AMENDMENTS = HERE / "amendments"   # the owner's scoring change: one test diff per bug its task does not determine
PROXY = REPO_ROOT / "validation" / "v7_swebench" / "egress_proxy.py"
FROM_IMAGE = "g6-a2-linux"
SECURITY = ("--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined")
LIMITS = ("--cpus", "4", "--memory", "8g")
USER = "tester"
HOME = f"/home/{USER}"
WORK = f"{HOME}/work"
CLAUDE = "/usr/local/bin/claude"
CLAUDE_SHA256 = "4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658"
G6SRC = "/opt/g6src"
G6PY = f"{G6SRC}/.venv/bin/python3"
WE = "/opt/we"
PROXY_PORT = 3128
MODEL_HOSTS = ("api.anthropic.com:443",)
TURN_BACKSTOP = 2
#: The repository's state before a paid run: its HEAD, HEAD's tree, how many paths are not clean, how many commits.
TREE_STATE = ("echo we-tree-state >/dev/null; printf '{\"head\": \"%s\", \"tree\": \"%s\", \"dirty\": %s, "
              "\"commits\": %s}\\n' \"$(git rev-parse HEAD)\" \"$(git rev-parse 'HEAD^{tree}')\" "
              "\"$(git status --porcelain | wc -l)\" \"$(git rev-list --all --count)\"")

PROMPT = """You are working in a clone of {owner}/{name}, at the commit just before the maintainers fixed the issue below.
The project's development environment is the virtual environment in .venv (its Python is .venv/bin/python).
There is no network apart from the model.

Fix the issue. Deliver the fix the way the using-g6 skill says: write the requirements and their checks,
review the patch with g6_review_patch, show its report, and repair what G6 reproduces. Nobody is available to
answer questions during this run: where the skill says to ask the person, put the questions in your final
report and carry on with the most likely reading. Leave your changes uncommitted in the working tree. End with
your final report.

Issue #{number}: {title}

{text}
"""

#: Round 3, arm A (PLAN.md section 9, "Round 3 (2026-10-30)"): round 1's prompt without the G6 instruction.
PROMPT_A = """You are working in a clone of {owner}/{name}, at the commit just before the maintainers fixed the issue below.
The project's development environment is the virtual environment in .venv (its Python is .venv/bin/python).
There is no network apart from the model.

Fix the issue. Nobody is available to answer questions during this run: put any questions in your final
report and carry on with the most likely reading. Leave your changes uncommitted in the working tree. End with
your final report.

Issue #{number}: {title}

{text}
"""

#: Round 3, arm C: added after round 1's instruction, before the issue. It names no bug, project or diagnosis.
ADDENDUM_C = """Before you say the fix is done, also check it with G6's formal tools:
1. State each requirement of the issue as a property: what must hold, and for which inputs.
2. Check each property with G6. Compare the patch with the base code on the inputs the issue names and on variants
   of them: a differential.v1 template instance in g6_review_patch (intent preserve where behaviour must stay, change
   where it must change). Where a property is over whole numbers and booleans, or a small state machine, also check
   it through g6_invoke: component formal (find_counterexample, check_validity, check_equivalence) or model_checking
   (check_invariant, prove_invariant); g6_describe gives each op's parameters. For behaviour over several steps, use a
   state_invariant.v1 template instance.
3. Repair what a check refutes, then review again with g6_review_patch. Stop when a review reproduces no new
   counterexample and every property you stated has been checked, or when the budget ends.
In your final report, list each property, the tool that checked it, and what it gave."""
#: Round 3, arm D (PLAN.md, 2026-10-30): C, and what to do when a template cannot say the check.
ADDENDUM_D = ADDENDUM_C + """
If a template instance is refused, or cannot express the inputs (they are not JSON values, or the function exists only
in the patch), check the property with a proposed_checks test file instead. Every check, in whichever form, must assert
a specific expected value taken from the issue, not from what the patched code returns, so that it can fail."""
#: Round 3, arm L (PLAN.md, 2026-10-31): the probes one reader wrote before any patch; run in the repository.
PROBE_RUN = f".venv/bin/python -m pytest {WE}/probes -q --tb=short -p no:cacheprovider -p no:pretty --rootdir={WE}/probes"
PROBE_COLLECT = PROBE_RUN.replace(" -q --tb=short", " --collect-only -q")
HIDDEN, HIDDEN_TREE, HELD_COPY = f"{WE}/hidden", f"{WE}/hidden_tree.json", "/tmp/we-held"
MAX_STOPS, SCOUT_CAP_USD, MAX_THREAD_CHARS, MAX_CROSS, MAX_REPAIRS, MIN_RANDOM = 4, 2.0, 12000, 200, 3, 300
JUNIT_OUT, JUNIT_MARK, MAX_SHOWN, MAX_CASE_CHARS = "/tmp/we-probes.xml", "=== junit ===", 12, 900
CRITIC_AT, CRITIC_SECONDS = f"{WE}/critic", 1500
FAILED = re.compile(r"^(?:FAILED|ERROR) \S*?([^/\s]+\.py(?:::\S+)?)", re.M)
SCOUT_PROMPT = f"""You are the scout for a bug fix someone else will write. Do not fix anything. Read the issue below and
the code it concerns, in the current directory, and work out what a complete fix must do.

1. Constraints: list what the issue requires, and separately what you only infer.
2. Heuristics: for the code path involved, list the ways a fix that handles only the issue's example would still be
   wrong: sibling constructs handled by the same code, other scopes, nesting, line endings and whitespace, empty and
   boundary inputs, what the same feature does elsewhere in this repository.
3. Decompose: write a goal tree of required behaviours, each with the concrete input shapes that test it.
4. Probes: for each shape, write a pytest test under {WE}/probes/ that asserts what the issue and the repository's own
   conventions require. Keep them short and call the project's public interface. Write nothing inside the repository.
5. Critique: run them from the repository with `{PROBE_RUN}`. Reproduce the symptom with at least one probe. A probe for
   behaviour that must stay should pass now. Delete any probe whose expectation you cannot justify from the issue or the
   repository: a wrong probe costs the fix's author more than a missing one.

Then write {WE}/tree.json:
{{{{"goal": "...", "branches": [{{{{"goal": "a required behaviour", "shapes": [{{{{"shape": "...", "probe": "test_x.py::test_y"}}}}]}}}}]}}}}
Use an empty probe for a shape you could not test.

Issue #{{number}}: {{title}}

{{text}}
"""
#: Arm L2 on (PLAN.md, 2026-10-31): L1's smoke run probed each dimension alone and left "unclear" shapes unprobed.
SCOUT_V2 = """   Treat the shapes as dimensions, and combine them: every pair of dimensions that can occur together gets a probe,
   with parametrized tests where that is shorter. A bug that survives a fix usually lives where two dimensions cross.
   Never leave a shape as unclear. Where the issue states a principle, apply it uniformly to the shape unless the
   repository's own code, tests or documentation say otherwise, and write the probe that follows.
"""
#: Arm L3 on: L2's scouts skipped pairs they were asked for, so the runner now counts them (``scout_gate``).
SCOUT_V3 = f"""   In {WE}/tree.json also give "dimensions": an object from each dimension's name to its list of values, as short
   strings. Write one test named test_cross, parametrized with itertools.product over every dimension, that builds its
   input from each combination's values. Skip a combination that cannot occur with pytest.skip inside the test; never
   leave it out. The runner counts these cases and refuses the probes if any are missing.
"""
#: Arm L4 on: which dimensions a scout thinks of varies by run, so a second reader attacks the tree (PR #4's critique).
CRITIC_PROMPT = f"""You are a critic. A scout read the issue below and wrote what a complete fix must handle: the goal tree
and dimensions in {WE}/tree.json and the probes in {WE}/probes. Read them and the code involved. Do not fix anything.

Find what they miss. Imagine a fix that passes every probe yet is still wrong for a user of this feature. Ask which
construct, scope, kind of neighbouring content, or form of input reaches this code path and appears in no dimension, and
which value of one dimension was never tried with the others. Look at how the code's own branches differ, not at the
issue's example.

Add each thing you find as a new value or a new dimension in {WE}/tree.json, and extend test_cross to match, so that it
stays the full product. Add a separate probe where a product cannot say it. Keep every expectation justified by the issue
or the repository, and delete none of the scout's probes unless the repository contradicts it. Then run them from the
repository with `{PROBE_RUN}`.

Issue #{{number}}: {{title}}

{{text}}
"""
#: Arm L5 on: L2's "never leave a shape unclear" made scouts invent expectations, and only exact assertions had power.
SCOUT_V5 = f"""   Treat the shapes as dimensions. In {WE}/tree.json also give "dimensions": an object from each dimension's name to
   its list of values, as short strings. Write one test named test_cross, parametrized with itertools.product over every
   dimension, that builds its input from each combination's values. Skip a combination that cannot occur with pytest.skip
   inside the test; never leave it out. The runner counts these cases and refuses the probes if any are missing.
   Assert the exact value required: compare whole outputs, not normalised forms, predicates or parts of them.
   Where the issue and the repository are silent on what a shape should do, write no probe for it and keep it out of the
   dimensions: list it with an empty probe. A wrong expectation is worse than a missing one.
"""
#: Arm L6 on: every failing patch on the hardest training bug failed where two dimensions cross that most scouts never
#: listed, and the rule on silence made them list less. So dimensions come from the code, and a generator mixes them.
SCOUT_V6 = f"""   Find the dimensions in the code as well as in the issue: read the functions the symptom passes through, and list
   each kind of thing that can reach that point and each branch it can take there. In {WE}/tree.json also give
   "dimensions": an object from each dimension's name to its list of values, as short strings. Write one test named
   test_cross, parametrized with itertools.product over every dimension, that builds its input from each combination's
   values. Skip a combination that cannot occur with pytest.skip inside the test; never leave it out.
   Write one test named test_random, parametrized over at least {MIN_RANDOM} seeds: each seed composes an input at random
   from the building blocks of all the dimensions, freely mixed, at random positions and depths, and checks the rule on
   it. It finds the crossings nobody listed. The runner counts both tests and refuses the probes if cases are missing.
   Assert the exact value required: compare whole outputs, not normalised forms, predicates or parts of them.
   Apply the issue's principle to every shape the code treats alike. Leave a shape out only where the repository shows it
   is meant to behave differently, and list it with an empty probe.
"""
LOOP_ADDENDUM_5 = f"""A scout has read this issue, without seeing any fix, and written the behaviours and input shapes a
complete fix must handle in {WE}/tree.json, with runnable probes in {WE}/probes (run them from the repository with
`{PROBE_RUN}`). Read them before you edit. When you finish, the runner runs these and further checks you cannot see on
your code, and sends back any that fail. A failing check is one careful reader's view: repair the cause if the issue or
the repository supports it, and otherwise leave the code alone and say why in one line. One g6_review_patch at the end is
enough."""
COUNTEREXAMPLES = """The runner ran the scout's probes and further checks on your code, and these fail:

{text}

Take each in turn. If the issue or the repository supports what it expects, repair the cause and rerun your own checks.
If they do not, leave the code alone and say why in one line. Then finish."""
#: Arm L8 on (PLAN.md, 2026-10-31): a case that passed before the change stands on the code's own past behaviour.
COUNTEREXAMPLES_8 = """The runner ran the scouts' probes on your code, and these fail:

{text}

A case that passed before your change and fails now is a regression: repair it unless the issue asks for that change. A case
that failed before and still fails is one careful reader's view of what the issue requires: repair the cause if the issue or
the repository supports it, and otherwise leave that case and say why in one line. Rerun your own checks, then finish."""
#: Arm L8 on: a reviewer from another model family reads the finished patch (PLAN.md, 2026-10-31). The text is the one the
#: blind experiment measured, for one bug.
PATCH_CRITIC = """<task>
You are reviewing a candidate bug fix written by someone else. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.
- candidate.patch: the candidate fix, applied on top of parent/.

The candidate may be complete or not. You are not told which. Judge it on its merits.

1. State the general rule the issue implies, in one or two sentences. Derive it from the issue and the repository's own code, tests
   and documentation, not from the patch.
2. Read the patch against that rule. Look for where it handles the issue's example but not the rule: a hard-coded name or value that
   should be derived, a branch of the same code path left out, another scope or nesting level, a sibling construct, an input position,
   or behaviour that used to work and now does not.
3. Write a pytest file that calls the project's public interface. Each test must assert an exact expected value that the issue or the
   repository justifies, and must fail on the candidate if you are right. Write nothing whose expectation you cannot justify: a wrong
   test is worse than none. If you find no defect, say so and write no test. Not finding one is a valid and useful answer.
</task>

<hard_limits>
Read-only, and only inside your cwd. No writes, no git writes, no docker, no network, no model or API calls. You cannot run code, so
reason carefully about what each test will do.
</hard_limits>

<structured_output_contract>
In this exact form:

READ: <the first line of issue.txt, exactly>
RULE: <one or two sentences>
VERDICT: defect or none
WHY: <the concrete input, what the candidate does, what it should do, and the evidence in issue.txt or parent/ with file and line>
```python
<a complete pytest file, or nothing if VERDICT is none>
```

Name each test test_<something>. The test file must import only the project and the standard library, and pytest if needed.
</structured_output_contract>

<grounding_rules>
Every expectation must point at the issue or at a file and line in parent/. If you are unsure of an expected value, drop that test.
</grounding_rules>

<default_follow_through_policy>
Do not ask questions. Finish.
</default_follow_through_policy>
"""
CRITIC_NOTE = """A reviewer from another team read your patch, without seeing your reasoning or how you got there, and says:

{rule}

{why}

Their test, which fails on your code:

```python
{test}
```

They can be wrong, as anyone can. Check their expectation against the issue and the repository before you act on it."""
REVIEW_ASK = ("\n\nIf the issue or the repository supports their expectation, repair the cause and rerun your own checks. If not, "
              "leave the code alone and say why in one line. Then finish.")
CONVERT_ARMS = ("X1", "X2")     # a saved finding and a fresh session, on the patch or on the parent (PLAN.md, 2026-11-01)
CONVERT_CASES = HERE / "convert_cases.json"
CONVERT_ON_PATCH = "\n\nA change for this issue is already in the working tree, uncommitted. Treat it as your patch.\n\n"
CONVERT_ON_PARENT = """

An earlier attempt at this issue was thrown away, and its patch is not in the tree. A reviewer from another team read it and says:

{rule}

{why}

Their test, which failed on that attempt:

```python
{test}
```

They can be wrong, as anyone can. Check their expectation against the issue and the repository before you act on it."""
LOOP_ADDENDUM = f"""A scout has read this issue, without seeing any fix, and written the behaviours and input shapes a
complete fix must handle in {WE}/tree.json, with runnable probes in {WE}/probes (run them from the repository with
`{PROBE_RUN}`). Read them before you edit. When you try to finish, the runner runs the probes on your code, and a failing
probe sends you back here. Repair the cause of what fails. A probe is one reader's view and can be wrong: if you are sure
of that, add a line to {WE}/waived.txt with the test's id, then a space, then the reason and what you observed. Never
remove a feature to settle a conflict. One g6_review_patch at the end is enough."""
PROMPT_C = PROMPT.replace("\nIssue #{number}", "\n" + ADDENDUM_C.replace("{", "{{").replace("}", "}}")
                          + "\n\nIssue #{number}", 1)

PROMPT_D = PROMPT.replace("\nIssue #{number}", "\n" + ADDENDUM_D.replace("{", "{{").replace("}", "}}")
                          + "\n\nIssue #{number}", 1)


# --- tasks ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Task:
    id: str
    project: str
    full_name: str
    licence: str
    pr: int
    issue: int
    merge_commit: str
    parent: str
    merged_at: str
    test_modules: tuple[str, ...]
    install: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    @property
    def repo(self) -> str:
        return f"{WORK}/{self.name}"


def load_tasks(path: Path = TASKS) -> list[Task]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Task(**{**t, "test_modules": tuple(t["test_modules"]), "install": tuple(t["install"])})
            for t in data["tasks"]]


def task_by_id(task_id: str, path: Path = TASKS) -> Task:
    for task in (*load_tasks(path), *load_tasks(TASKS_FRESH), *load_tasks(TASKS_UNSEEN), *load_tasks(TASKS_DEV),
                 *load_tasks(TASKS_STAGE4)):
        if task.id == task_id:
            return task
    raise SystemExit(f"no task {task_id!r} in {path.name}")


def fresh_sets() -> tuple[Path, ...]:
    """Every set of bugs judged on and never trained on: one list, read by ``is_fresh`` and by harness training's guard, from
    this module's names when called."""
    return (TASKS_FRESH, TASKS_UNSEEN, TASKS_STAGE4)


def is_fresh(task_id: str) -> bool:
    return any(task.id == task_id for path in fresh_sets() for task in load_tasks(path))


def is_dev(task_id: str) -> bool:
    return any(task.id == task_id for task in load_tasks(TASKS_DEV))


def amendment(task_id: str) -> Path | None:
    """The bug's test diff (PLAN.md, 2026-10-31), or None: every other bug keeps the maintainers' tests exactly."""
    found = AMENDMENTS / f"{task_id}.patch"
    return found if found.is_file() else None


def hidden_at(i: int) -> tuple[str, str]:
    """Where hidden scout ``i`` works: its probes' folder and its tree."""
    place = f"{HIDDEN}{i + 1}" if i else HIDDEN
    return place, f"{place}_tree.json"


def hidden_scouts(arm: str) -> int:
    return 2 if int(arm[1:]) >= 6 else 1


def trained(arm: str) -> dict[str, str]:
    """What harness training added for arm ``L<n>`` or ``M<n>`` from 10, or ``K<n>`` from 2 (PLAN.md, stage 3), committed before it ran. It can only
    add to a part's instructions: the sets, the scoring, the reply forms and the stop rule are not among them."""
    if re.fullmatch(r"K\d+", arm) and int(arm[1:]) >= 2:
        name = arm
    elif re.fullmatch(r"[LM]\d+", arm) and int(arm[1:]) >= 10:
        name = f"L{arm[1:]}"
    else:
        return {}
    text = json.loads((HARNESS / f"{name}.json").read_text(encoding="utf-8"))
    odd = sorted(set(text) - set(TRAINED) - set(STRUCTURAL))
    if odd:
        raise ValueError(f"harness training cannot change {odd}")
    bad = sorted(k for k in STRUCTURAL if k in text and text[k] not in STRUCTURAL[k])
    if bad:
        raise ValueError(f"harness training gave {bad} a value outside {[STRUCTURAL[k] for k in bad]}")
    return text


def literal(text: str) -> str:
    """``text`` for a template that ``format`` fills."""
    return text.replace("{", "{{").replace("}", "}}")


def repair_text(arm: str) -> str:
    add = trained(arm).get("repair")
    return COUNTEREXAMPLES_8 + (f"\n\n{literal(add)}" if add else "")


def critic_text(arm: str) -> str:
    add = trained(arm).get("critic")
    return PATCH_CRITIC.replace("</task>", f"4. {add}\n</task>", 1) if add else PATCH_CRITIC


def scout_prompt(arm: str, *, hidden: bool = False, at: int = 0) -> str:
    """The scout's prompt for arm ``L<n>`` or ``M<n>``: ``SCOUT_V2`` from generation 2 and ``SCOUT_V3`` from 3, then
    ``SCOUT_V5`` alone from 5 and ``SCOUT_V6`` from 6, before its critique. ``hidden``: hidden scout ``at``'s, whose work
    the agent never sees."""
    n = int(arm[1:])
    extra = SCOUT_V6 if n >= 6 else SCOUT_V5 if n == 5 else (SCOUT_V2 if n >= 2 else "") + (SCOUT_V3 if n >= 3 else "")
    add = trained(arm).get("scout")
    if add:
        extra += f"   {literal(add)}\n"
    text = SCOUT_PROMPT.replace("5. Critique:", extra + "5. Critique:", 1)
    if hidden:
        place, tree = hidden_at(at)
        text = text.replace(f"{WE}/probes", place).replace(f"{WE}/tree.json", tree)
    return text


def probes_at(where: str) -> str:
    return PROBE_RUN.replace(f"{WE}/probes", where)


def failed_ids(output: str) -> set[str]:
    """The tests pytest's short summary names as failed or in error."""
    return set(FAILED.findall(output))


def probe_failures(said: str, label: str) -> set[str]:
    """What failed in a run reported with its ``exit=`` line. pytest's exit decides: names that cannot be read never make
    a failing run look clean, and a run that reported no exit is not a pass. 0 is clean, 5 is no tests collected."""
    named = {f"{label}:{i}" for i in failed_ids(said)}
    code = re.search(r"^exit=(\d+)$", said, re.M)
    if named or (code and code[1] in ("0", "5")):
        return named
    return {f"{label}:exit {code[1]}" if code else f"{label}:no exit"}


def gate_flags() -> str:
    """Added to a probe run for the gate. Never ``--tb=no``: the summary then drops the reason ``crashes`` reads."""
    return "-rf"


def crashes(output: str) -> int:
    """How many summary lines say a test failed with something other than an assertion or ``pytest.fail``."""
    return sum(1 for line in output.splitlines() if line.startswith("FAILED ")
               and not re.search(r" - (?:assert|AssertionError|Failed)", line))


def random_cases(said: str) -> int:
    got = re.search(r"^random=(\d+)$", said, re.M)
    return int(got[1]) if got else 0


def gate_numbers(said: str) -> tuple[int, int, int]:
    """What the container reported: pytest's exit (2 when it reported none), the cross test's cases, the crashes."""
    code, cross = re.search(r"^exit=(\d+)$", said, re.M), re.search(r"^cross=(\d+)$", said, re.M)
    return int(code[1]) if code else 2, int(cross[1]) if cross else 0, crashes(said)


def qualified(arm: str) -> bool:
    """From L8 a probe set is read from pytest's own report, and a set that exercised nothing is unknown, never clean."""
    return int(arm[1:]) >= 8


@dataclass
class ProbeRun:
    """One run of a probe set, from pytest's own report, so no reading of its console can cut an id or hide a failure."""
    exit: int | None
    results: dict[str, str]
    texts: dict[str, str]
    tail: str = ""

    @property
    def ran(self) -> int:
        return sum(v in ("passed", "failed") for v in self.results.values())

    @property
    def usable(self) -> bool:
        return self.exit in (0, 1) and self.ran > 0


def probe_script(where: str) -> str:
    return (f"rm -f {JUNIT_OUT}; {probes_at(where)} --junitxml={JUNIT_OUT} -o junit_family=xunit2 > /tmp/we-console.txt 2>&1; "
            f'code=$?; tail -c 4000 /tmp/we-console.txt; echo "exit=$code"; echo "{JUNIT_MARK}"; cat {JUNIT_OUT} 2>/dev/null; '
            f"rm -f {JUNIT_OUT} /tmp/we-console.txt")


def failure_texts(xml: str) -> dict[str, str]:
    """Each failing case's message and traceback, keyed as ``parse_junit`` keys it."""
    found: dict[str, str] = {}
    for case in ET.fromstring(xml).iter("testcase"):
        for child in case:
            if child.tag in ("failure", "error"):
                found[f"{case.get('classname', '')}::{case.get('name', '')}"] = (f"{child.get('message', '')}\n"
                                                                                  f"{child.text or ''}").strip()
    return found


def read_probes(said: str) -> ProbeRun:
    """What ``probe_script`` printed. A report that cannot be read has no results, so the run is not usable."""
    head, _, xml = said.partition(JUNIT_MARK)
    code = re.search(r"^exit=(\d+)$", head, re.M)
    try:
        results, texts = parse_junit(xml.strip()), failure_texts(xml.strip())
    except ET.ParseError:
        results, texts = {}, {}
    return ProbeRun(int(code[1]) if code else None, results, texts, head)


def split_failures(now: ProbeRun, base: ProbeRun | None) -> tuple[list[str], list[str]]:
    """G6's plan rule 3 (``classify``) on each failing case: one that passed at the base and fails now is a counterexample
    on what worked before; any other is what a reader says the fix still owes."""
    from g6.harness.verification_search import COUNTEREXAMPLE, classify

    regressions, owed = [], []
    for case in sorted(k for k, v in now.results.items() if v in ("failed", "error")):
        before = base.results.get(case) if base else None
        verdict = classify("preserve", {"result": "failed", "detail": ""}, {"result": before, "detail": ""} if before else None)
        (regressions if verdict["result"] == COUNTEREXAMPLE else owed).append(case)
    return regressions, owed


def failures_of(sets: Iterable[tuple[str, ProbeRun, ProbeRun | None]]) -> tuple[set[str], str]:
    """The failing ids of ``(label, run now, run at the base)``, and what the agent reads: regressions first, each case in
    its own words, ``MAX_SHOWN`` at most. A set that no longer runs is reported, never clean."""
    failing: set[str] = set()
    broke, regress, owed = [], [], []
    for label, now, base in sets:
        if not now.usable:
            failing.add(f"{label}:exit {now.exit}" if now.exit is not None else f"{label}:no exit")
            broke.append(f"- the {label} probes no longer run on your code:\n{now.tail[-MAX_CASE_CHARS:]}")
            continue
        lost, still = split_failures(now, base)
        failing |= {f"{label}:{case}" for case in [*lost, *still]}
        regress += [(label, case, now.texts.get(case, "")) for case in lost]
        owed += [(label, case, now.texts.get(case, "")) for case in still]
    parts, left = list(broke), MAX_SHOWN
    for heading, cases in (("Passed before your change and fail now:", regress), ("Failed before your change and still fail:", owed)):
        if cases:
            shown = cases[:left]
            left -= len(shown)
            parts.append("\n".join([heading, *(f"- {label}:{case}\n{text[:MAX_CASE_CHARS]}" for label, case, text in shown),
                                    *([f"(and {len(cases) - len(shown)} more)"] if len(cases) > len(shown) else [])]))
    return failing, "\n\n".join(parts)


def critic_report(said: str, *, proof: str = "") -> dict[str, str]:
    """The critic's reply: its rule, verdict, reasons and test. Progress lines say nothing, so only what follows them counts,
    and a test comes with a verdict of defect or not at all. ``proof``: the line it must quote from inside the pack, because
    a critic that could not read the pack still answers, and "none" from it looks like a clean review (L8's smoke run)."""
    body = said[said.rfind("[codex]"):].split("\n", 1)[-1] if "[codex]" in said else said
    if proof and not re.search(rf"^READ:\s*{re.escape(proof)}\s*$", body, re.M):
        return {"verdict": "unread", "rule": "", "why": "", "test": "", "error": f"the reply does not quote {proof}"}
    verdict = re.search(r"^VERDICT:\s*(\w+)", body, re.M)
    rule = re.search(r"^RULE:\s*(.*?)(?=^VERDICT:)", body, re.M | re.S)
    why = re.search(r"^WHY:\s*(.*?)(?=^```|\Z)", body, re.M | re.S)
    test = re.search(r"^```python\n(.*?)^```", body, re.M | re.S)
    said_defect = bool(verdict) and verdict[1].lower() == "defect"
    return {"verdict": verdict[1].lower() if verdict else "unreadable", "rule": rule[1].strip() if rule else "",
            "why": why[1].strip() if why else "", "test": test[1].strip() if test and said_defect else "", "error": ""}


def run_codex(pack: Path, timeout: float | None = None) -> str:
    """One read-only Codex task in ``pack``, on the owner's sign-in: what it printed (``codex_task``). ``timeout``: arm K3's
    watchdog's, what is left of the run; without it the call is the frozen review's own, as it was."""
    got = codex_task(pack) if timeout is None else codex_task(pack, timeout)
    return got.stdout.decode("utf-8", errors="replace")


def codex_task(pack: Path, timeout: float = CRITIC_SECONDS) -> subprocess.CompletedProcess:
    """``run_codex``'s call, as it ended: what Codex printed and how it exited. ``G6_CODEX_COMPANION``: the Codex plugin's
    companion script."""
    companion = os.environ.get("G6_CODEX_COMPANION")
    if not companion:
        raise RuntimeError("G6_CODEX_COMPANION is not set")
    try:
        got = subprocess.run(["node", companion, "task", "--fresh", "--effort", "high", "--cwd", str(pack), "--prompt-file",
                              str(pack / "critic_prompt.md")], capture_output=True, timeout=timeout, cwd=pack)
    finally:
        end_codex(pack)
    return got


def end_codex(pack: Path) -> None:
    """Every process of the Codex task in ``pack`` ended: the companion leaves a broker and a codex process for each workspace,
    0.4 GB a time, and they hold the pack open."""
    subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{pack.name}*' }} | "
                    "ForEach-Object { taskkill /T /F /PID $_.ProcessId | Out-Null }"], capture_output=True, timeout=60)


def with_critic(found: tuple[set[str], str], critic: Mapping[str, str]) -> tuple[set[str], str]:
    """What fails, with the critic's rule, reasons and test before it if the critic's own test is among the failures."""
    failing, text = found
    if not any(case.startswith("critic:") for case in failing):
        return failing, text
    return failing, CRITIC_NOTE.format(**{k: critic.get(k, "") for k in ("rule", "why", "test")}) + "\n\n" + text


def has_hook(arm: str) -> bool:
    """Arms L1 to L4: the Stop hook. From L5 the runner holds the loop itself."""
    return int(arm[1:]) < 5


def holds_loop(arm: str) -> bool:
    return int(arm[1:]) >= 5


class WorkingTree:
    """Stands where ``cegis`` has a sketch: the candidate is the agent's working tree, which ``Repairs`` looks at."""

    @staticmethod
    def instantiate(control: dict[int, str]) -> str:
        return control[0]


class Repairs:
    """G6's CEGIS over the agent's work (PLAN.md, 2026-10-31): ``search`` is the agent, told what failed; ``evaluate`` is
    the probes, which the runner holds. A failure the agent has already seen is not sent again: it has decided. With
    ``state``, from L8, what says the agent has decided is that it left the code as it was: a failure that survives a
    repair goes back, up to the cap."""

    def __init__(self, check: Callable[[], tuple[set[str], str]], ask: Callable[[str, int], Any],
                 state: Callable[[], str] | None = None, told: str = "") -> None:
        self.check, self.ask, self.state, self.told = check, ask, state, told
        self.round, self.failing, self.text, self.sent, self.tree = 0, set(), "", set(), None

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> Any:
        from g6.cegis import SearchResult

        if self.round:
            tree = self.state() if self.state else None
            if self.state and tree == self.tree:
                return SearchResult(None, "the agent left the code as it was", False, self.round)
            if not self.state and self.failing <= self.sent:
                return SearchResult(None, "nothing new fails", False, self.round)
            self.sent |= self.failing
            self.tree = tree
            told = self.told or (COUNTEREXAMPLES_8 if self.state else COUNTEREXAMPLES)
            self.ask(told.format(text=self.text), self.round)
        self.round += 1
        return SearchResult({0: f"round {self.round}"}, "agent", False, self.round)

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        self.failing, self.text = self.check()
        return [[{"ok": not self.failing} for _ in inputs]]


def has_critic(arm: str) -> bool:
    return int(arm[1:]) == 4


def critic_prompt(number: int, title: str, text: str) -> str:
    return CRITIC_PROMPT.format(number=number, title=title, text=text)


def scout_gate(raw: str, *, exit_code: int, collected: int, broken: int = 0, randoms: int | None = None,
               executed: int | None = None) -> list[str]:
    """What is wrong with the scout's probes, by the runner's own count: G6's ``InputDomain`` says how many combinations
    the declared dimensions make, up to ``MAX_CROSS``. Empty when the fix may start."""
    from g6.cegis import InputDomain

    wrong = []
    if executed == 0:
        wrong.append("no case of test_cross ran: a skipped case tests nothing")
    if randoms is not None and randoms < MIN_RANDOM:
        wrong.append(f"test_random has {randoms} of {MIN_RANDOM} generated inputs")
    if broken:
        wrong.append(f"{broken} probes fail with an error, not an assertion: they test nothing until they run")
    if exit_code not in (0, 1):
        wrong.append(f"the probes could not run (pytest exit {exit_code}): make them importable from the repository")
    elif exit_code == 0:
        wrong.append("every probe passes on the unfixed code, so none fails: reproduce the issue's symptom")
    try:
        dimensions = json.loads(raw).get("dimensions") or {}
        domain = InputDomain.of(dimensions, max_inputs=MAX_CROSS)
    except (ValueError, TypeError, AttributeError):
        dimensions, domain = {}, None
    if domain is None or len(dimensions) < 2:
        wrong.append('tree.json needs "dimensions" with at least two dimensions')
    elif collected < min(domain.size, MAX_CROSS):
        wrong.append(f"test_cross has {collected} of {min(domain.size, MAX_CROSS)} combinations of the dimensions")
    return wrong


def scout_again(args: Sequence[str]) -> list[str]:
    """The same command, continuing the scout's session: the flag that takes a list stays last."""
    at = args.index("--disallowedTools")
    return [*args[:at], "--continue", *args[at:]]


def gets_thread(arm: str) -> bool:
    """Whether a run's issue carries its discussion: arm L, and arm T, the comparison for it (PLAN.md, 2026-10-31). K0 is K1's
    loop without it (PLAN.md, 2026-11-02). Arms V3 and V4 have it, as K1 does (2026-11-04)."""
    return re.fullmatch(r"T|R|K(?!0$)\d+|V\d+|X\d+|[LM]\d+", arm) is not None


def convert_cases() -> list[dict[str, Any]]:
    """The saved patches arms X1 and X2 start from, fixed by a rule before any of their runs."""
    return json.loads(CONVERT_CASES.read_text(encoding="utf-8"))


def convert_start(root: Path, task: str, repeat: int) -> tuple[Path, dict[str, str]]:
    """The saved patch a conversion run starts from, and the rule, reasons and test of the saved review of it."""
    case = next(x for x in convert_cases() if (x["task"], x["repeat"]) == (task, repeat))
    row = json.loads((root / "loop" / f"{case['arm']}-{task}-{case['of']}.json").read_text("utf-8"))
    patch = root / row["run"] / "final.patch"
    found = json.loads((root / "loop" / "final_review" / f"{c.sha256_file(patch)}.json").read_text("utf-8"))
    if found.get("said") != "reproduced":
        raise ValueError(f"{case['arm']}-{case['of']} of {task} has no reproduced finding")
    return patch, {k: str(found.get(k) or "") for k in ("rule", "why", "test")}


def convert_note(arm: str, found: Mapping[str, str]) -> str:
    """What a conversion run is told: the review's own words, with no verdict on any patch."""
    if arm == "X1":
        return CONVERT_ON_PATCH + CRITIC_NOTE.format(**found) + REVIEW_ASK
    return CONVERT_ON_PARENT.format(**found)


def thread_text(comments: Iterable[Mapping[str, Any]], until: str) -> str:
    """The discussion a person would read, cut where the fix's pull request opens: ISO times compare as text."""
    kept = [f"{str(n.get('author_association') or 'NONE').lower()} on {str(n['created_at'])[:10]}: {n.get('body') or ''}"
            for n in comments if n["created_at"] < until and (n.get("user") or {}).get("type") != "Bot"]
    body = "\n\n".join(kept)[:MAX_THREAD_CHARS]
    return f"\n\nThe issue's discussion so far:\n\n{body}" if body else ""


def prompt(task: Task, title: str, text: str, arm: str = "") -> str:
    """The run's prompt: round 1's (and round 3's arm B), round 3's arm A without the G6 instruction, or arm C with the
    addendum."""
    texts = {"": PROMPT, "B": PROMPT, "A": PROMPT_A, "C": PROMPT_C, "D": PROMPT_D, "E": PROMPT_D, "T": PROMPT_A, "R": PROMPT_A,
             "S": PROMPT_A, **dict.fromkeys(CONVERT_ARMS, PROMPT_A)}
    if re.fullmatch(r"[KV]\d+", arm):
        texts[arm] = PROMPT_A
    if re.fullmatch(r"[GH]\d+", arm):
        harness = (HARNESS / f"G{arm[1:]}.txt").read_text(encoding="utf-8").strip()
        texts[arm] = PROMPT.replace("\nIssue #{number}", "\n" + harness.replace("{", "{{").replace("}", "}}")
                                    + "\n\nIssue #{number}", 1)
    if re.fullmatch(r"[LM]\d+", arm):
        added = LOOP_ADDENDUM
        if holds_loop(arm):
            added = (HARNESS / "G1.txt").read_text(encoding="utf-8").strip() + "\n\n" + LOOP_ADDENDUM_5
        add = trained(arm).get("fixer")
        if add:
            added += f"\n\n{add}"
        texts[arm] = PROMPT.replace("\nIssue #{number}", "\n" + added.replace("{", "{{").replace("}", "}}")
                                    + "\n\nIssue #{number}", 1)
    if arm not in texts:
        raise ValueError(f"no arm {arm!r}")
    owner, name = task.project.split("/", 1)
    return texts[arm].format(owner=owner, name=name, number=task.issue, title=title, text=text)


# --- docker ---------------------------------------------------------------------------------------------------------------
class Docker:
    """The docker CLI through ``subprocess`` (list arguments, no shell)."""

    def __init__(self, exe: str = "docker", runner: Callable[..., Any] = subprocess.run) -> None:
        self.exe, self.runner = exe, runner

    def __call__(self, *args: str, input: bytes | None = None, timeout: float | None = None,
                 env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
        return self.runner([self.exe, *args], input=input, capture_output=True, timeout=timeout,
                           env=dict(env) if env is not None else None)

    def sh(self, ctr: str, script: str, *args: str, timeout: float = 600, user: str = USER,
           workdir: str | None = None) -> subprocess.CompletedProcess:
        extra = ["-w", workdir] if workdir else []
        return self("exec", "-u", user, "-e", f"HOME={HOME}", *extra, ctr, "sh", "-c", script, "we", *args,
                    timeout=timeout)

    def popen(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        return subprocess.Popen([self.exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env) if env is not None else None)


def out(cp: Any) -> str:
    return (cp.stdout or b"").decode("utf-8", errors="replace").strip()


def err(cp: Any) -> str:
    return (cp.stderr or b"").decode("utf-8", errors="replace").strip()


def checked(cp: Any, what: str) -> str:
    if cp.returncode != 0:
        raise RuntimeError(f"{what} failed ({cp.returncode}): {(out(cp) + ' ' + err(cp))[-600:]}")
    return out(cp)


def write_in(docker: Docker, ctr: str, dest: str, text: str, *, user: str = USER, mode: str = "644") -> None:
    checked(docker("exec", "-i", "-u", user, ctr, "sh", "-c",
                   f'mkdir -p "$(dirname "{dest}")" && cat > "{dest}" && chmod {mode} "{dest}"',
                   input=text.encode("utf-8")), f"write {dest}")


# --- images ---------------------------------------------------------------------------------------------------------------
def base_dockerfile(variant: str = "") -> str:
    """G6 as the README installs it at the commit run (PLAN.md section 9): in G6's source folder, its own environment
    ``.venv`` made with ``python -m venv --copies``, then ``pip install ".[all]"`` there (``g6`` on the path stands
    in for activating it); Claude Code 2.1.309; on ``g6-a2-linux``. Round 2 (``variant`` "r2") first makes its one
    G6-side change (``round2_g6.py``) to the source G6 is installed from."""
    round2 = (f" && python3 /tmp/round2_g6.py {G6SRC}/src/g6/harness && rm /tmp/round2_g6.py \\\n"
              if variant == ROUND2 else "")
    copy = "COPY round2_g6.py /tmp/round2_g6.py\n" if variant == ROUND2 else ""
    return f"""FROM {FROM_IMAGE}
COPY claude {CLAUDE}
COPY g6src.tar /tmp/g6src.tar
{copy}RUN chmod 755 {CLAUDE} \\
 && mkdir -p {G6SRC} && tar -xf /tmp/g6src.tar -C {G6SRC} && rm /tmp/g6src.tar \\
{round2} && cd {G6SRC} && python3 -m venv --copies .venv \\
 && {G6SRC}/.venv/bin/pip install -q --no-cache-dir ".[all]" \\
 && ln -s {G6SRC}/.venv/bin/g6 /usr/local/bin/g6 \\
 && mkdir -p {WE}/rounds && chown -R 1000:1000 {WE} \\
 && {G6PY} -c "import g6; print(g6.__version__)"
"""


def task_dockerfile(task: Task, base_image: str) -> str:
    """The project's repository holding upstream's base commit alone (fetched with ``--depth=1 --no-tags`` and checked
    out detached, as ``arm_r.base_only`` does), so git tracks exactly what upstream tracks and nothing after the base
    is in reach (PLAN.md section 9, from run 6); then its ``.venv``, made the project's normal way, with links. Its
    dependencies are the versions published by the time the fix was merged (``uv``'s exclude-newer), so a later
    release cannot break the project's own tests; the installer is removed from the image afterwards."""
    env = " ".join(f"{k}={v}" for k, v in sorted({**task.env, "UV_EXCLUDE_NEWER": task.merged_at,
                                                  "UV_NO_CACHE": "1"}.items()))
    install = " && ".join(f"{env} {cmd}" for cmd in task.install)
    return f"""FROM {base_image}
USER {USER}
ENV HOME={HOME}
RUN mkdir -p {task.repo} && cd {task.repo} && git -c init.defaultBranch=main init -q \\
 && git fetch -q --depth=1 --no-tags https://github.com/{task.full_name}.git {task.parent} \\
 && git -c advice.detachedHead=false checkout -q --detach {task.parent} \\
 && rm -f .git/FETCH_HEAD \\
 && test "$(git rev-parse HEAD)" = {task.parent} && test "$(git rev-list --all --count)" = 1
WORKDIR {task.repo}
RUN printf '/.venv/\\n' >> .git/info/exclude \\
 && python3 -m venv .venv \\
 && python3 -m venv /tmp/we-uv && /tmp/we-uv/bin/pip install -q --no-cache-dir uv \\
 && export PATH="/tmp/we-uv/bin:$PATH" \\
 && {install} \\
 && rm -rf /tmp/we-uv \\
 && git status --porcelain | sed -n 's|^?? |/|p' >> .git/info/exclude \\
 && test -z "$(git status --porcelain)"
"""


def oracle_dockerfile(task: Task, task_image: str) -> str:
    """The task's image plus the maintainers' fix, kept apart in ``/oracle`` (the tree at the merge commit, the diff
    from the parent and its file list). Used only by the validation and the checks, never by the agent."""
    return f"""FROM {FROM_IMAGE} AS fetch
RUN git clone -q --filter=blob:none --no-checkout https://github.com/{task.full_name}.git /src \\
 && git -C /src -c advice.detachedHead=false checkout -q {task.merge_commit} \\
 && git -C /src diff --binary --no-renames {task.parent} {task.merge_commit} > /fix.patch \\
 && git -C /src diff --name-status --no-renames {task.parent} {task.merge_commit} > /fix.files \\
 && rm -rf /src/.git
FROM {task_image}
USER root
COPY --from=fetch /src /oracle/fixed
COPY --from=fetch /fix.patch /fix.files /oracle/
RUN chmod -R a+rX /oracle
USER {USER}
"""


def image_names(task: Task, g6_commit: str, variant: str = "") -> tuple[str, str]:
    short = g6_commit[:12] + (f"-{variant}" if variant else "")
    return f"g6we-task:{task.id}-{short}", f"g6we-oracle:{task.id}-{short}"


def base_tag(g6_commit: str, variant: str = "") -> str:
    return f"g6we-base:{g6_commit[:12]}" + (f"-{variant}" if variant else "")


# --- round 2: G6 with its own model (PLAN.md section 9) -------------------------------------------------------------------
ROUND2 = "r2"
G6_MODEL = "claude-sonnet-5-5"
G6_ALLOWANCE_USD = 5.00      # what a round 2 run may spend on G6's own model calls, on top of the agent's stop
KEY_FILE = f"{HOME}/.g6/.env"
ROUND2_PATCH = HERE / "round2_g6.py"


def g6_key_file(key: str) -> str:
    """G6's key file in a round 2 container: its model, through the anthropic provider, and the key."""
    return "\n".join(["G6_LLM_PROVIDER=anthropic", f"G6_LLM_MODEL={G6_MODEL}", f"ANTHROPIC_API_KEY={key}"]) + "\n"


def write_key_file(docker: Any, ctr: str, key: str) -> None:
    """G6's key file, owner-only, written through standard input: the key is never on a command line."""
    checked(docker("exec", "-i", "-u", USER, ctr, "sh", "-c",
                   f'umask 077 && mkdir -p "{HOME}/.g6" && chmod 700 "{HOME}/.g6" && cat > "{KEY_FILE}" && '
                   f'chmod 600 "{KEY_FILE}"', input=g6_key_file(key).encode("utf-8")), "G6's key file")


def _completions(obj: Any) -> list[list]:
    """Every ``completion.activities`` list under ``obj``, outermost first."""
    found = []
    if isinstance(obj, dict):
        comp = obj.get("completion")
        if isinstance(comp, dict) and isinstance(comp.get("activities"), list):
            found.append(comp["activities"])
        for key, value in obj.items():
            if key != "completion":
                found += _completions(value)
    elif isinstance(obj, list):
        for value in obj:
            found += _completions(value)
    return found


#: The proposer's reply limit in round 2 (``round2_g6.py``): the most output a call can be billed for, its thinking
#: included, which the reply's text does not show.
G6_MAX_OUTPUT_TOKENS = 4_096
#: At most this many characters to a token, for a call whose token counts G6's caller did not report: an upper bound
#: (code and English run nearer four).
CHARS_PER_TOKEN_LOW = 2


def g6_model_cost(assessments: Iterable[Any]) -> dict[str, Any]:
    """G6's own model calls in the given full assessments (one ``completion.activities`` list each), priced at the
    published prices with no cache discount assumed (PLAN.md section 9). G6's Anthropic caller reports no token
    counts, so a call without them is counted at an upper bound: its request's characters over
    ``CHARS_PER_TOKEN_LOW`` in, and the reply limit (``G6_MAX_OUTPUT_TOKENS``) out."""
    calls, bounded, tokens_in, tokens_out, usd = 0, 0, 0, 0, 0.0
    for assessment in assessments:
        lists = _completions(assessment)
        for activity in (lists[0] if lists else []):
            if not isinstance(activity, dict) or activity.get("activity") != "model call":
                continue
            calls += 1
            if activity.get("input_tokens") is None or activity.get("output_tokens") is None:
                bounded += 1
                usage = {"input_tokens": -(-int(activity.get("input_chars") or 0) // CHARS_PER_TOKEN_LOW),
                         "output_tokens": G6_MAX_OUTPUT_TOKENS}
            else:
                usage = {k: activity.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens")}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            answered = str(activity.get("answered_by") or "")
            usd += c.usage_cost(usage, answered if answered in c.PRICES else G6_MODEL)
    return {"calls": calls, "calls_counted_at_upper_bound": bounded, "input_tokens": tokens_in,
            "output_tokens": tokens_out, "usd": round(usd, 6)}


# --- round 3: formal methods (PLAN.md section 9, "Round 3 (2026-10-30)") ----------------------------------------------
ROUND3 = "r3"
R3_MODELS = ("claude-haiku-4-5", "claude-sonnet-5-5", "claude-fable-5-1")   # in the order they run
# PLAN.md, 2026-10-30, the smoke test: Haiku's pilot never called G6, so these go before Haiku's runs are done
R3_SMOKE = {(R3_MODELS[1], "black-5634", "B", 1), (R3_MODELS[1], "black-5634", "C", 1),
            (R3_MODELS[1], "attrs-1593", "C", 1), (R3_MODELS[1], "click-3678", "C", 1),
            (R3_MODELS[1], "pydantic-14052", "C", 1)}
R3_PROBE = {(R3_MODELS[1], task, "D", 1) for task in ("black-5634", "attrs-1593", "click-3678", "pydantic-14052")}
R3_D_G6 = "f50953c"          # arm D's G6, reviewed (PLAN.md, 2026-10-30): its refusals name proposed_checks
HARNESS = Path(__file__).resolve().parent / "harness"   # the distilled text of each generation
TRAINED = ("fixer", "scout", "repair", "critic")   # the parts of the loop harness training can add to
#: The structural choices a generation may make, each from a closed list (PLAN.md, 2026-11-04, evening). ``finding``: which of a
#: reviewer's failing cases go back to the agent: any, as K1 sends them, or only those that failed on the parent too.
STRUCTURAL = {"finding": ("any", "parent")}
R3_LOOP_G6 = "c53d310"       # the distillation loop's G6 (PLAN.md, 2026-10-30)
R3_HELD = ("black-5521", "click-3769", "starlette-3718")   # by hash, before any tuning; never read by the distiller
R3_HELD_REPEATS = 5          # the final comparison, set before any generation ran (PLAN.md, 2026-10-30)
R3_ARMS = ("A", "B", "C")
R3_REPEATS = {("claude-sonnet-5-5", "A"): 2}    # the noise control: Sonnet's arm A twice per bug
R3_CAP_USD = 180.00          # round 3's own hard stop, agent and G6 together (the owner, 2026-10-30)
R3_RUN_CAP_USD = 10.00       # each run's cap (Claude Code's --max-budget-usd); Fable's from the budget left
R3_MARGIN = 1.05             # the runner's own stop is the cap and 5% (round 1: $10 and $10.50)
G6_BIN = "/usr/local/bin/g6"
#: What the agent's container holds of G6 for Claude Code: the user-level MCP servers and skills (arm check).
ARM_STATE = ("echo we-arm-state >/dev/null; python3 -I -c 'import json, os\n"
             "p = os.path.expanduser(\"~/.claude.json\")\n"
             "d = json.load(open(p)) if os.path.isfile(p) else {}\n"
             "s = os.path.expanduser(\"~/.claude/skills\")\n"
             "print(json.dumps({\"mcp_servers\": sorted(d.get(\"mcpServers\") or {}), "
             "\"skills\": sorted(os.listdir(s)) if os.path.isdir(s) else []}))'")


def screened(task_id: str) -> bool:
    """Whether arm S may run on a bug: stage 2's bugs and stage 3's development pool."""
    return any(task.id == task_id for task in (*load_tasks(TASKS_UNSEEN), *load_tasks(TASKS_DEV)))


def r3_loop_key(key: tuple[str, str, str, int]) -> bool:
    """A run of the distillation loop: Sonnet, twice. A and E on any bug, ``G<n>`` on training bugs, ``H<n>`` (the same
    text) on the held-out ones. The final comparison runs A and ``H<n>`` five times on each held-out bug."""
    model, task, arm, repeat = key
    if arm == "S":      # screening has its own runs: never rows of the comparison, and only where no arm has run (PLAN.md, 2026-11-01)
        return model == R3_MODELS[1] and repeat in (1, 2, 3) and screened(task)
    if arm in CONVERT_ARMS:      # development: never a bug kept for measurement
        return (model == R3_MODELS[1] and task not in R3_HELD and not is_fresh(task)
                and any((x["task"], x["repeat"]) == (task, repeat) for x in convert_cases()))
    held_out = task in R3_HELD and (arm in ("A", "T", "R") or arm[0] in "HKM")
    if model != R3_MODELS[1] or repeat not in (range(1, R3_HELD_REPEATS + 1) if held_out else (1, 2)):
        return False
    if is_fresh(task):
        return arm in ("A", "T", "R") or re.fullmatch(r"[KMV]\d+", arm) is not None
    if arm in ("A", "E", "T", "R") or re.fullmatch(r"[KV]\d+", arm):
        return True
    return re.fullmatch(r"[GHLM]\d+", arm) is not None and (arm[0] in "HM") == (task in R3_HELD)


def r3_schedule(tasks: Sequence[Task]) -> list[tuple[str, str, str, int]]:
    """Every round 3 run, in order, as (model, task, arm, repeat): Haiku's, then Sonnet's, then Fable's; within a model,
    the tasks in the plan's order, each with arms A, B and C."""
    return [(model, task.id, arm, repeat) for model in R3_MODELS for task in tasks for arm in R3_ARMS
            for repeat in range(1, R3_REPEATS.get((model, arm), 1) + 1)]


def r3_caps(model: str, *, spent: float, fable_left: int) -> tuple[float, float]:
    """A run's cap and the runner's stop: $10 and $10.50; for Fable, the budget left over the Fable runs left (this
    one included), at most those, the cap rounded down to the cent."""
    stop = R3_RUN_CAP_USD * R3_MARGIN
    if model == R3_MODELS[2]:
        stop = min(stop, max(0.0, R3_CAP_USD - spent) / max(1, fable_left))
    cap = int(stop / R3_MARGIN * 100 + 1e-9) / 100
    return cap, round(cap * R3_MARGIN, 6)


def r3_done(rows: Iterable[Mapping[str, Any]], root: Path) -> dict[tuple[str, str, str, int], str]:
    """The round 3 runs whose agent started, by (model, task, arm, repeat), with how each ended, as its folder under the archive
    ``root`` says (``wecommon.ending``): a host run's ending file, which a stop before the index's end of the run leaves too."""
    rows = list(rows)
    done = {}
    for r in rows:
        if not (r.get("event") == "started" and r.get("kind") == "run" and r.get("arm")):
            continue
        facts = c.run_facts(root, str(r["run"]), rows)      # the one source of the run's facts, from these rows of the index
        end = facts.ending if facts is not None else None
        if end and end.get("agent_started") and end.get("status"):
            done[(str(r.get("model")), str(r.get("task")), str(r.get("arm")), int(r.get("repeat") or 1))] = str(end["status"])
    return done


def r3_refusal(ctx: Context, task: Task) -> tuple[str, float, float]:
    """Round 3's own rules: the arm, model and repeat are scheduled, not run already, and in order (no Sonnet run while
    a Haiku run is left, no Fable run while a Sonnet one is); the cap; and the $100 stop, which a host run, spending none of the
    API's money, never asks. Returns (why, cap, stop)."""
    rows = ctx.archive.rows()
    sched = r3_schedule(ctx.tasks if ctx.tasks is not None else load_tasks())
    key = (ctx.model, task.id, ctx.arm, ctx.repeat)
    if ctx.model not in R3_MODELS:
        return f"round 3 has no model {ctx.model!r}", 0.0, 0.0
    if key not in sched and key not in R3_PROBE and not r3_loop_key(key):
        return f"round 3 schedules no run {key}", 0.0, 0.0
    if ctx.arm not in R3_ARMS[:3] and ctx.arm != "D" and not str(ctx.g6.get("commit")).startswith(R3_LOOP_G6):
        return f"the loop's arms run only on the reviewed G6, {R3_LOOP_G6}", 0.0, 0.0
    if ctx.arm == "D" and not str(ctx.g6.get("commit")).startswith(R3_D_G6):
        return f"arm D runs only on the reviewed G6, {R3_D_G6}", 0.0, 0.0
    done = r3_done(rows, ctx.archive.root)
    # a host run that met the subscription's usage limit, or that G6 refused at its session's start, gave no verdict, so it is
    # made again, as a killed run is; a run a model refusal ended was made (PLAN.md, 2026-11-04; ``wecommon.ending``). A K3 run
    # whose agent started and that kept no baseline (``NO_FIRST_PATCH``) was made too: it is no row, and never made again
    made = key in done and (c.scoreable({"status": done[key]}) or done[key] == NO_FIRST_PATCH)
    if made and not (ctx.rerun_why and done[key] == "error"):
        return f"run {key} was already made ({done[key]}); no run is made again for a better result", 0.0, 0.0
    earlier = [k for k in sched if R3_MODELS.index(k[0]) < R3_MODELS.index(ctx.model) and k not in done]
    if earlier and key not in R3_SMOKE and key not in R3_PROBE and not r3_loop_key(key):
        return f"out of order: {len(earlier)} run(s) of an earlier model are left, first {earlier[0]}", 0.0, 0.0
    if getattr(ctx, "executor", "container") == "host":     # its API need is nothing (``wecommon.api_need``): it asks no API budget,
        return "", R3_RUN_CAP_USD, round(R3_RUN_CAP_USD * R3_MARGIN, 6)    # neither the round's stop nor Fable's share of it
    spent = c.spent(rows)
    fable_left = sum(1 for k in sched if k[0] == R3_MODELS[2] and k not in done)
    cap, stop = r3_caps(ctx.model, spent=spent, fable_left=fable_left)
    ok, why = c.may_start(spent, cap=R3_CAP_USD, need=c.api_need({"executor": getattr(ctx, "executor", "container"),
                                                                  "stop_usd": stop}))
    if not ok or cap <= 0:
        return f"the round's ${R3_CAP_USD:.0f} stop: {why or 'no budget left'}", cap, stop
    return "", cap, stop


def r3_ledger(archive: c.Archive, record: Mapping[str, Any]) -> dict[str, Any]:
    """One line of round 3's ledger after each run: what it cost and what the round has spent since it began."""
    used = (record.get("observed") or {}).get("tool_use") or {}
    row = {"at": c.now(), "run": record.get("run"), "task": record.get("task"), "model": record.get("model"),
           "arm": record.get("arm"), "repeat": record.get("repeat"), "status": record.get("status"),
           "g6_calls": used.get("g6_calls"), "tool_search": used.get("tool_search"),
           "cost_usd": float(record.get("cost_usd") or 0.0), "reported_usd": record.get("reported_usd"),
           "cap_usd": record.get("cap_usd"), "spent_after": c.spent(archive.rows()), "stop_usd": R3_CAP_USD}
    archive.root.mkdir(parents=True, exist_ok=True)
    with open(archive.root / c.NDJSON["ledger"], "a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# --- the hook that saves each round's patch, and the patch capture -------------------------------------------------------
def diff_script(repo: str, base: str, dest: str) -> str:
    """The working tree's diff against ``base``, new files included, through a separate index: the repository's own
    index is never touched."""
    return (f'idx=$(mktemp) && cd "{repo}" && GIT_INDEX_FILE="$idx" git read-tree {base} >/dev/null 2>&1; '
            f'GIT_INDEX_FILE="$idx" git add -A >/dev/null 2>&1; '
            f'GIT_INDEX_FILE="$idx" git diff --cached --binary {base} > "{dest}" 2>/dev/null; rm -f "$idx"')


def hook_script(repo: str, base: str) -> str:
    return f"""#!/bin/sh
# Worked examples (PLAN.md section 4): before each g6_review_patch call, the call's input and the working tree's diff
# against the base commit are saved. Nothing is printed, so the agent is told nothing.
n=$(ls {WE}/rounds 2>/dev/null | grep -c '\\.patch$')
n=$((n + 1))
cat > {WE}/rounds/round-$n.input.json 2>/dev/null
{diff_script(repo, base, WE + "/rounds/round-$n.patch")}
exit 0
"""


def hook_settings(*, stop: bool = False) -> str:
    hooks: dict[str, Any] = {"PreToolUse": [{"matcher": "mcp__g6__g6_review_patch", "hooks": [
        {"type": "command", "command": f"{WE}/round_hook.sh", "timeout": 120}]}]}
    if stop:
        hooks["Stop"] = [{"hooks": [{"type": "command", "command": f"{WE}/stop_hook.sh", "timeout": 300}]}]
    return json.dumps({"hooks": hooks}, indent=1)


def stop_hook(repo: str) -> str:
    """Arm L's hook: only a failing probe (pytest's exit 1) blocks the stop; it lets go after ``MAX_STOPS`` blocks."""
    return f"""#!/bin/sh
cat >/dev/null
cd {repo} || exit 0
mkdir -p {WE}/rounds
n=$(cat {WE}/rounds/stops 2>/dev/null || echo 0)
[ "$n" -ge {MAX_STOPS} ] && exit 0
args=""
[ -f {WE}/waived.txt ] && args=$(awk 'NF{{print "--deselect " $1}}' {WE}/waived.txt)
timeout 240 {PROBE_RUN} $args > {WE}/rounds/stop-$n.txt 2>&1
code=$?
if [ "$code" -ne 1 ] && [ "$code" -ne 2 ]; then
  echo "exit $code" >> {WE}/rounds/stop-$n.txt
  exit 0
fi
echo $((n + 1)) > {WE}/rounds/stops
tail -c 6000 {WE}/rounds/stop-$n.txt | python3 -I -c 'import json, sys
print(json.dumps({{"decision": "block", "reason": "The scout probes fail on your code. Repair the cause, or waive a wrong "
                  "probe as your instructions say, then finish again.\\n\\n" + sys.stdin.read()}}))'
"""


def scout_tree(raw: str) -> dict[str, Any]:
    """The scout's ``tree.json`` as PR #5's ``GoalTree``: its text, how many shapes, and which have no probe."""
    from g6.goal_engine.goal_tree import GoalTree

    try:
        data = json.loads(raw)
        tree = GoalTree(str(data["goal"]), branching_factor=12, max_depth=2, max_nodes=200)
        branches = data["branches"]
        if not branches:
            raise ValueError("the tree has no branches")
        ids = tree.add_subgoals("root", [str(b["goal"]) for b in branches])
        shapes = [s for b in branches for s in b["shapes"]]
        for ident, branch in zip(ids, branches):
            tree.add_subgoals(ident, [str(s["shape"]) for s in branch["shapes"]])
    except (ValueError, TypeError, KeyError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "text": "", "shapes": 0, "open": []}
    return {"text": tree.visualize(), "shapes": len(shapes),
            "open": [str(s["shape"]) for s in shapes if not s.get("probe")]}


# --- tests: maintainers' test files, junit, FAIL_TO_PASS -----------------------------------------------------------------
def test_changes(name_status: str) -> list[tuple[str, str]]:
    """The fix's changes to test files (PLAN.md section 2's T3 definition), from ``git diff --name-status``."""
    rows = []
    for line in name_status.splitlines():
        status, _, path = line.partition("\t")
        if path and selection.is_test(path):
            rows.append((status.strip()[:1], path.strip()))
    return rows


APPLY_TESTS = """set -e
cd "$1"; shift
while [ $# -gt 1 ]; do st=$1; p=$2; shift 2
  case "$st" in D) rm -f -- "$p" ;; *) mkdir -p -- "$(dirname -- "$p")"; cp -- "/oracle/fixed/$p" "$p" ;; esac
done"""
JUNIT_IN = "/tmp/we-junit.xml"


def pytest_script(modules: Sequence[str], timeout_s: int = 1800) -> str:
    files = " ".join(f'"{m}"' for m in modules)
    return (f'rm -f {JUNIT_IN}; timeout {timeout_s} .venv/bin/python -m pytest -p no:cacheprovider -q -rN '
            f'--junitxml={JUNIT_IN} -o junit_family=xunit2 {files} > /tmp/we-pytest.txt 2>&1; echo "exit=$?"')


def parse_junit(text: str) -> dict[str, str]:
    """Each test case's outcome (passed, failed, error or skipped), keyed ``classname::name``; the worst one when a
    case appears twice."""
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    found: dict[str, str] = {}
    for case in ET.fromstring(text).iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        outcome = ("error" if "error" in tags else "failed" if "failure" in tags else
                   "skipped" if "skipped" in tags else "passed")
        if rank[outcome] >= rank.get(found.get(key, "passed"), 0) or key not in found:
            found[key] = outcome
    return found


def fail_to_pass(base: Mapping[str, str], fixed: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """FAIL_TO_PASS: failing (or absent) with the maintainers' tests on the parent's code, passing with their fix.
    PASS_TO_PASS: passing in both (PLAN.md section 3)."""
    f2p = sorted(k for k, v in fixed.items() if v == "passed" and base.get(k) != "passed" and base.get(k) != "skipped")
    p2p = sorted(k for k, v in fixed.items() if v == "passed" and base.get(k) == "passed")
    return f2p, p2p


def verdict(results: Mapping[str, str], f2p: Sequence[str], p2p: Sequence[str]) -> dict[str, Any]:
    """The independent check (PLAN.md section 5): every FAIL_TO_PASS and PASS_TO_PASS test passes."""
    failing = sorted(k for k in [*f2p, *p2p] if results.get(k) != "passed")
    return {"passes": bool(f2p) and not failing, "failing": failing,
            "fail_to_pass_failing": sorted(k for k in f2p if results.get(k) != "passed"),
            "pass_to_pass_failing": sorted(k for k in p2p if results.get(k) != "passed")}


# --- the key check -------------------------------------------------------------------------------------------------------
KEYPROBE_TEST = '''import glob
import os

PATHS = ("~/.g6/.env", "@HOME@/.g6/.env", "/root/.g6/.env")


def seen():
    found = set()
    if "ANTHROPIC_API_KEY" in os.environ:
        found.add("own_env_name")
    if any(value.startswith("sk-ant-") for value in os.environ.values()):
        found.add("own_env_value")
    for path in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(path, "rb") as stream:
                data = stream.read()
        except OSError:
            continue
        if b"ANTHROPIC_API_KEY=" in data:
            found.add("proc_env_name")
        if b"sk-ant-" in data:
            found.add("proc_env_value")
    for path in PATHS:
        if os.path.exists(os.path.expanduser(path)):
            found.add("key_file")
    return sorted(found)


def test_no_key_visible():
    channels = seen()
    assert not channels, "seen: " + ",".join(channels)
'''.replace("@HOME@", HOME)   # the container's home folder, written once (HOME)


def keyprobe_driver() -> str:
    """Run with G6's Python in a process that holds the key in its environment, as Claude Code does: a scratch
    repository with an ordinary (linked) ``.venv`` whose one test looks for the key; G6 reviews it confined, then the
    same test runs unconfined as the control. Prints names and outcomes only, never a value."""
    return "PROBE = " + json.dumps(KEYPROBE_TEST) + '''
import json, os, re, shutil, subprocess, sys, pathlib
root = pathlib.Path("@HOME@/keyprobe/repo")
shutil.rmtree(root.parent, ignore_errors=True)
root.mkdir(parents=True)
(root / "mod.py").write_text("def f():\\n    return 1\\n")
def git(*a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
git("init", "-q"); git("add", "."); git("-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "commit", "-qm", "base")
(root / "mod.py").write_text("def f():\\n    return 2\\n")
(root / "test_keyprobe.py").write_text(PROBE)
subprocess.run(["/usr/local/bin/python3", "-m", "venv", "--system-site-packages", str(root / ".venv")], check=True)
(root / ".git" / "info" / "exclude").write_text("/.venv/\\n")
from g6.harness.patch_review import review_patch
got = review_patch(root, task="key check", requirements=[{"id": "R1", "text": "a confined test cannot read the key",
                   "checks": ["test_keyprobe.py::test_no_key_visible"]}], timeout_s=600)
a = got.get("assessment") or {}
status = {r.get("id"): r.get("status") for r in a.get("requirements") or []}
report = a.get("report") or ""
backend = "bubblewrap" if "isolation `bubblewrap`" in report else None
done = subprocess.run([str(root / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "test_keyprobe.py"], cwd=root, capture_output=True, text=True)
said = re.findall(r"AssertionError: seen: ([a-z_,]+)", done.stdout + done.stderr)
known = {"own_env_name", "own_env_value", "proc_env_name", "proc_env_value", "key_file"}
channels = sorted(set(",".join(said).split(",")) & known) if said else []   # pytest's summary line cuts names short
acts = ((a.get("completion") or {}).get("activities") or [])
calls = [{k: x.get(k) for k in ("input_tokens", "output_tokens", "input_chars", "output_chars", "answered_by", "stage",
                                 "result")} for x in acts
         if isinstance(x, dict) and x.get("activity") == "model call"]
print(json.dumps({"confined": {"R1": status.get("R1"), "outcome": a.get("outcome"), "backend": backend,
                               "code": (got.get("warning_card") or {}).get("code")},
                  "control": {"exit": done.returncode, "channels": [x for x in channels if x]},
                  "g6_model_calls": calls}))
'''.replace("@HOME@", HOME)


def key_check_ok(found: Mapping[str, Any], *, key_file: bool = False) -> bool:
    """The confined test passed under bubblewrap, and the unconfined control saw the key by name (and, in round 2,
    G6's key file too)."""
    confined, control = found.get("confined") or {}, found.get("control") or {}
    seen = control.get("channels") or []
    return (confined.get("R1") == "passed_on_stated_checks" and confined.get("backend") == "bubblewrap"
            and control.get("exit") not in (0, None) and "own_env_name" in seen
            and (not key_file or "key_file" in seen))


# --- the rounds, read from the transcript ---------------------------------------------------------------------------------
def _reports(obj: Any) -> list[dict[str, Any]]:
    """Every dict under ``obj`` with a ``report`` and an ``outcome`` (G6's assessment), outermost first."""
    found = []
    if isinstance(obj, dict):
        if "report" in obj and "outcome" in obj:
            found.append(obj)
        for value in obj.values():
            found += _reports(value)
    elif isinstance(obj, list):
        for value in obj:
            found += _reports(value)
    return found


def _result_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


SAVED = re.compile(r"Output has been saved to (/\S+?\.txt)")


def archived_resolver(folder: Path) -> Callable[[str], str | None]:
    """Where a reply Claude Code saved to a file in the container is in the run's archive (``claude_projects``)."""
    def resolve(path: str) -> str | None:
        marker = "/.claude/projects/"
        if marker not in path:
            return None
        local = folder / "claude_projects" / path.split(marker, 1)[1]
        return local.read_text(encoding="utf-8", errors="replace") if local.is_file() else None
    return resolve


def extract_rounds(objects: Iterable[Mapping[str, Any]],
                   resolve: Callable[[str], str | None] | None = None) -> list[dict[str, Any]]:
    """Each ``g6_review_patch`` call is a round: its input, the polls of ``metaharness_job`` after it, and the last
    assessment G6 returned before the next round (outcome and report, verbatim). A reply too large for Claude Code's
    transcript is read from the file Claude Code saved it to, through ``resolve``."""
    rounds: list[dict[str, Any]] = []
    tools: dict[str, str] = {}
    for obj in objects:
        msg = obj.get("message") or {}
        for block in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools[str(block.get("id"))] = name
                if name.endswith("g6_review_patch"):
                    rounds.append({"round": len(rounds) + 1, "input": block.get("input"), "polls": 0,
                                   "outcome": None, "report": None})
                elif name.endswith("metaharness_job") and rounds:
                    rounds[-1]["polls"] += 1
            elif block.get("type") == "tool_result" and rounds:
                name = tools.get(str(block.get("tool_use_id")), "")
                if not name.startswith("mcp__g6__"):
                    continue
                text = _result_text(block)
                saved = SAVED.search(text)
                if saved and resolve is not None and (found_text := resolve(saved.group(1))) is not None:
                    text = found_text
                    rounds[-1]["saved_to_file"] = True
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                if name.endswith("g6_review_patch") and isinstance(parsed, dict) and parsed.get("job_id"):
                    rounds[-1]["job_id"] = str(parsed["job_id"])
                for found in _reports(parsed)[:1]:
                    rounds[-1].update(outcome=found.get("outcome"), report=found.get("report"), assessment=found)
                inner = parsed.get("result") if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict) \
                    else parsed
                if isinstance(inner, dict) and inner.get("error") and not rounds[-1].get("report"):
                    rounds[-1]["error"] = str(inner["error"])      # a review G6 refused, with its reason
    return rounds


# --- the stream ----------------------------------------------------------------------------------------------------------
def claude_args(model: str = c.MODEL, cap_usd: float = c.RUN_CAP_USD) -> list[str]:
    """PLAN.md section 4 (round 3: the run's model and cap). The flag that takes a list goes last; the prompt comes on
    standard input."""
    return [CLAUDE, "-p", "--model", model, "--output-format", "stream-json", "--verbose", "--max-turns",
            str(c.MAX_TURNS), "--max-budget-usd", f"{cap_usd:.2f}", "--dangerously-skip-permissions",
            "--disallowedTools", "WebFetch", "WebSearch"]


def read_stream(lines: Iterable[bytes], tally: c.Tally, *, deadline: float, write: Callable[[str], None],
                clock: Callable[[], float] = time.monotonic, stop_usd: float = c.RUN_STOP_USD) -> str:
    """Feed the stream to ``tally`` until it ends ("") or a cap is reached ("timeout", "cap_turns", "cap_cost")."""
    q: queue.Queue = queue.Queue()

    def pump() -> None:
        try:
            for line in lines:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        left = deadline - clock()
        if left <= 0:
            return "timeout"
        try:
            line = q.get(timeout=min(left, 1.0))
        except queue.Empty:
            continue
        if line is None:
            return ""
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        write(text)
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict):
            tally.feed(obj)
        if tally.model_calls > c.MAX_TURNS * TURN_BACKSTOP:
            return "cap_turns"
        if tally.cost_usd > stop_usd:
            return "cap_cost"


def status_of(stop: str, tally: Any) -> str:
    """How a session ended: its stream's own stop when it has one, else what its result says (``result_status``)."""
    return stop or result_status(tally.result)


def result_status(result: Mapping[str, Any] | None) -> str:
    """The one reader of a session's result, for every arm and executor (``status_of``, so ``host_ending`` too, and K3's
    ``step_ending``): "finished" only for a result Claude Code marks as a success, its subtype "success" and its ``is_error``
    False; "cap_turns" or "cap_cost" for the error subtypes that name a cap (max_turns, a budget), as before; anything else
    "error": no result, an error, a success with no word on error, and a subtype nobody knows."""
    if not isinstance(result, Mapping):
        return "error"
    sub = str(result.get("subtype") or "")
    if "max_turns" in sub:
        return "cap_turns"
    if "budget" in sub:
        return "cap_cost"
    return "finished" if sub == "success" and result.get("is_error") is False else "error"


KILL_AGENT = (f'for p in /proc/[0-9]*; do [ "$(readlink $p/exe 2>/dev/null)" = {CLAUDE} ] && '
              'kill -9 "${p#/proc/}" 2>/dev/null; done; true')

# --- the host executor (PLAN.md, 2026-11-04): Claude Code on the host, on the subscription's sign-in; every command it runs, in
# the bug's container through the box (``box_mcp.py``). The sign-in never enters a container, and the container has no network.
HOST_CLAUDE = "claude"
#: The flags probed with Claude Code 2.1.318 on the host, in the order probed; ``{...}`` is a run's own value (``host_args``). No
#: Bash or PowerShell tool: the box's ``run`` is the only way to run anything. A repair adds ``--resume <session>``.
HOST_FLAGS = ("-p", "--restricted", "--tools", "Read,Edit,Write,Glob,Grep", "--mcp-config", "{mcp}", "--strict-mcp-config",
              "--allowedTools", "mcp__box__run", "--settings", "{settings}", "--permission-mode", "acceptEdits",
              "--permission-prompts", "none", "--output-format", "stream-json", "--verbose", "--model", "{model}",
              "--max-turns", "{turns}")
HOST_SETTINGS = {"permissions": {"blockReadsOutsideWorkingDirectories": True, "blockWritesOutsideWorkingDirectories": True},
                 "disableAllHooks": True}
#: What the host session keeps of the driver's environment, by name in any case: what a program needs to run on Windows or POSIX
#: and to find the user's own sign-in, the proxy and CA variables, and the four that say which docker daemon to reach. Nothing
#: else passes: not the driver's own Claude Code session's variables, nor a key or token, which would sign in in place of the
#: subscription; and no name a prefix admits passes by the prefix alone (``HOST_ENV_SECRET``).
HOST_ENV_NAMES = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "USER",
    "LOGNAME", "SHELL", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "TEMP", "TMP",
    "TMPDIR", "LANG", "TERM", "OS", "NUMBER_OF_PROCESSORS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"})
HOST_ENV_PREFIXES = ("LC_", "PROCESSOR_")
#: A name that may carry a key, a token or a password: dropped whatever admitted it, before ``HOST_FIXED_ENV`` is added.
HOST_ENV_SECRET = re.compile(r"TOKEN|KEY|SECRET|PASS|AUTH|CREDENTIAL", re.I)
#: An allowed variable programs read as a URL with a scheme or without one (a proxy given as ``user:password@host:port``; docker
#: reads ``host:port`` as tcp): its value is also read as a URL's authority with no scheme (``host_value_holds_userinfo``).
HOST_ENV_URLS = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "DOCKER_HOST"})
#: Set in every host session, after the allowlist: no claude.ai connectors (2.1.318 lets them through --strict-mcp-config), no
#: retry of a refused request on another model, and the container runs' no updates, telemetry, error reports, other traffic or
#: memory, so the version stays fixed through the study.
HOST_FIXED_ENV = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1", "DISABLE_AUTOUPDATER": "1",
                  "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                  "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
#: Exactly what a host session's init may name: the five file tools and the box's ``run``, and the box as its one MCP server.
HOST_TOOLS = frozenset({"Edit", "Glob", "Grep", "Read", "Write", "mcp__box__run"})
HOST_SERVERS = frozenset({"box"})
HOST_EXIT_S = 30           # how long a session whose stream has ended is given to exit by itself before it is stopped
#: Claude Code's word that the subscription's usage limit is reached, with when it resets when it says.
HOST_LIMIT = re.compile(r"You(?:'|’)ve hit your (?:[\w-]+ ){0,3}limit\b\W{0,4}(resets\b[^\n|]*)?", re.I)
#: A name part Windows would refuse or change (a device name, a reserved character, a trailing dot or space): the box's one rule,
#: so the export to the host and the pack from it read the same names.
HOST_UNSAFE = box_mcp.HOST_UNSAFE
BOX_TIMEOUT_S = 600        # one command in the box, at most: Claude Code's own Bash tool stops one at ten minutes


def host_args(mcp_file: Path, settings_file: Path, model: str, *, resume: str = "") -> list[str]:
    """``HOST_FLAGS`` with the run's own values: the box's MCP configuration and the settings, both in the run's folder."""
    values = {"mcp": str(mcp_file), "settings": str(settings_file), "model": model, "turns": str(c.MAX_TURNS)}
    return [flag.format(**values) for flag in HOST_FLAGS] + (["--resume", resume] if resume else [])


def host_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The host session's environment: what ``HOST_ENV_NAMES`` and ``HOST_ENV_PREFIXES`` allow of ``environ``, never a name that
    starts with CLAUDE or ANTHROPIC, nor one that may carry a key or token (``HOST_ENV_SECRET``), in any case, nor a value that
    may carry one (``host_value_holds_userinfo``), then ``HOST_FIXED_ENV``."""
    kept = {k: v for k, v in environ.items() if (k.upper() in HOST_ENV_NAMES or k.upper().startswith(HOST_ENV_PREFIXES))
            and not k.upper().startswith(("CLAUDE", "ANTHROPIC")) and not HOST_ENV_SECRET.search(k)
            and not host_value_holds_userinfo(k, v)}
    return {**kept, **HOST_FIXED_ENV}


def host_value_holds_userinfo(name: str, value: str) -> bool:
    """Whether an allowed variable's value may carry a credential in a URL: a user or a password as the URL format's own parser
    reads them (``urlsplit``), in the value read whole, in each URL the value holds read from its "://" on (a URL in a list, a
    path or a proxy alike), and, in a variable programs read as a URL with no scheme (``HOST_ENV_URLS``), in the value read as an
    authority. A value the parser cannot read is taken to carry one. No pattern of G6's own decides where a user or password
    ends: a password with a space is still one, and an "@" in a pipe's or a socket's path is none. Such a variable is dropped
    whole, never passed with the user and password taken out: taking them out needs a parser that reads the URL exactly as each
    program that reads the variable does, and a credential the two read differently would pass, while a dropped variable is
    plainly not there."""
    readings = [value, *(value[m.start() + 1:] for m in re.finditer("://", value))]
    if name.upper() in HOST_ENV_URLS:
        readings.append("//" + value)
    try:
        return any(parts.username is not None or parts.password is not None for parts in map(urlsplit, readings))
    except ValueError:
        return True


def host_init(obj: Any) -> dict[str, Any] | None:
    """A stream record that is the session's init: its tool and MCP server names, and why the session may not go on ("" when it
    may): any tools or MCP servers but exactly ``HOST_TOOLS`` and ``HOST_SERVERS``, one more or one fewer."""
    if not (isinstance(obj, dict) and obj.get("type") == "system" and obj.get("subtype") == "init"):
        return None
    tools = [str(t) for t in obj.get("tools") or []]
    servers = [str(s.get("name") if isinstance(s, dict) else s) for s in obj.get("mcp_servers") or []]
    more = sorted({*tools} - HOST_TOOLS) + sorted({*servers} - HOST_SERVERS)
    fewer = sorted(HOST_TOOLS - {*tools}) + sorted(HOST_SERVERS - {*servers})
    why = (f"the host session's init was not exactly the five file tools and the box: it had {', '.join(more) or 'nothing more'} "
           f"and lacked {', '.join(fewer) or 'nothing'}") if more or fewer else ""
    return {"tools": tools, "servers": servers, "why": why}


def model_refusal(obj: Any) -> dict[str, Any] | None:
    """Whether a stream record says the model refused a request, and what it says of it; None when it does not. The one
    recogniser of a model refusal in the worked examples, which every reader of a host stream asks: a system record whose subtype
    names a refusal (its subtype, category and models), or an assistant message whose stop reason is "refusal" (that, and its
    model), the record V7's ``run_arm.Tally`` reads; a test holds the two to one answer on every record V7 reads."""
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "system" and "refusal" in str(obj.get("subtype") or ""):
        return {k: obj.get(k) for k in ("subtype", "apiRefusalCategory", "originalModel", "fallbackModel")}
    msg = obj.get("message")
    if obj.get("type") == "assistant" and isinstance(msg, dict) and msg.get("stop_reason") == "refusal":
        return {"stop_reason": "refusal", "model": msg.get("model")}
    return None


def host_facts(path: Path, model: str) -> dict[str, Any]:
    """What a saved host stream says of its session, its lines as ``c.read_stream`` frames them: its init (``host_init``): every
    init record is read, and the first one that is not exactly the box's and the five file tools is the session's, else the
    first; its last result, the first usage limit Claude Code gave in it (``limit_of``), the models that wrote its messages
    (Claude Code's own synthetic ones apart), and each record of a refused request (``model_refusal``). A model is the one asked
    for only when it is that name, or that name and a date (``-YYYYMMDD``)."""
    init, result, limit, seen, refusals = None, None, None, set(), []
    for line in c.read_stream(path) if path.is_file() else []:
        limit = limit if limit is not None else limit_of(line)
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        found = host_init(obj)
        if found and (init is None or (found["why"] and not init["why"])):
            init = found
        result = obj if obj.get("type") == "result" else result
        msg = obj.get("message")
        if obj.get("type") == "assistant" and isinstance(msg, dict) and msg.get("model") and msg.get("model") != "<synthetic>":
            seen.add(str(msg["model"]))
        if (refused := model_refusal(obj)) is not None:
            refusals.append(refused)
    asked = re.compile(re.escape(model) + r"(?:-\d{8})?")
    return {"init": init, "result": result, "limit": limit, "models": seen, "refusals": refusals,
            "switched": any(not asked.fullmatch(m) for m in seen)}


def host_ending(stop: str, facts: Mapping[str, Any], *, code: int | None, limit: str | None, by_g6: bool,
                left: str = "") -> tuple[str, str]:
    """How a host session ended, decided once from all of its evidence: ("", "") when cleanly, else its status and why. In this
    order: "limited" when Claude Code said the usage limit was reached (with the reset it gave); "refused", G6's own refusal of
    the session, when its stream has no init or an init that is not exactly the box's and the five file tools (``host_init``);
    "model_refused" at a record of a request the model refused (``model_refusal``), a run made as Claude Code alone would leave
    it (PLAN.md, runtime-k ec0f761), with nothing resent. Then, once the session's own result was read, what it says (``status_of``),
    whatever ended the process after it; before any result, the stream's own stop ("timeout", "cap_turns") or "error". Then
    "error" for a process that ended by itself with a code other than 0; ``by_g6``: G6 ended the process, its code says nothing.
    Last, ``left``: why the session's processes could not be confirmed ended (``end_session``), which makes any ending but
    "limited" and "refused" "error", never one more scoreable."""
    if limit is not None:
        return "limited", limit
    init, result = facts.get("init"), facts.get("result")
    if init is None:
        return "refused", "the host session's stream had no init, so what tools it had is not known"
    if init["why"]:
        return "refused", init["why"]
    if left:
        return "error", left
    if facts.get("refusals"):
        return "model_refused", ""
    got = status_of("" if result else stop, SimpleNamespace(result=result))
    if got != "finished":
        return got, ""
    return ("error", "") if code != 0 and not by_g6 else ("", "")


def limit_of(line: str) -> str | None:
    """Whether a stream line says the subscription's usage limit is reached (``HOST_LIMIT``): when the limit resets as Claude Code
    gave it ("" when it did not), or None. Only what Claude Code itself marks as its own is read: the text of a result that is an
    error (``is_error``, or an error subtype), a synthetic message, or a line that is not JSON. A successful result's text is the
    model's answer, and the model's messages and the tools' output are never read either."""
    try:
        obj = json.loads(line)
    except ValueError:
        texts = [line]
    else:
        msg = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(obj, dict) and obj.get("type") == "result":
            failed = obj.get("is_error") is True or str(obj.get("subtype") or "").startswith("error")
            texts = [str(obj.get("result") or "")] if failed else []
        elif isinstance(obj, dict) and obj.get("type") == "assistant" and isinstance(msg, dict) and msg.get("model") == "<synthetic>":
            texts = [str(b.get("text") or "") for b in msg.get("content") or [] if isinstance(b, dict)]
        else:
            return None
    for text in texts:
        found = HOST_LIMIT.search(text)
        if found:
            return (found.group(1) or "").strip()
    return None


def host_start(start: Callable[..., Any], argv: list[str], **kw: Any) -> Any:
    """A host session's launcher, started by ``start`` (as ``subprocess.Popen`` is called) in a process tree of G6's own
    (``g6.core.process_tree._Tree``, as ``g6.core.component_process`` and ``g6.core.confine`` use it): on Windows suspended
    inside a Job Object that kills every member when it is closed, on POSIX leading a new session whose group is killed. Its
    pipes carry bytes. ``kw`` is the tree's (``env``, ``cwd`` and the three pipes). Returns the tree; its ``proc`` is the
    launcher, and ``end_session`` ends every process in it."""
    from g6.core.process_tree import _Tree

    return _Tree(argv, spawn=lambda args, **given: start(args, **{**given, "text": False, "encoding": None}), **kw)


def end_session(proc: Any, workspace: Path, tree: Any = None) -> str:
    """Every process of the host session in ``workspace`` whose launcher is ``proc``, ended: its process tree (``host_start``)
    killed, strictly, so each of its members, a launcher that ended first included, is confirmed gone. No process is chosen by
    its name, its command line or its folder. Returns "" when all are confirmed ended, else why not, which ``host_ending`` makes
    the session's "error". A launcher started outside a tree of G6's own is ended alone, and that is said."""
    from g6.core.process_tree import ProcessTreeError

    if tree is None:
        if proc.poll() is None:
            proc.kill()
        return "the session was not started in a process tree of G6's own: only its launcher could be ended"
    try:
        tree.kill(strict=True)
    except (ProcessTreeError, OSError, subprocess.TimeoutExpired) as exc:
        return f"the session's processes could not be confirmed ended: {type(exc).__name__}: {exc}"
    return ""


# --- the run -------------------------------------------------------------------------------------------------------------
@dataclass
class Context:
    archive: c.Archive
    docker: Any
    key_reader: Callable[[], str]
    issue_reader: Callable[[Task], tuple[str, str]]
    g6: Mapping[str, Any]                 # {"commit", "image", "contains_required"}
    proxy_source: Path = PROXY
    environ: Mapping[str, str] | None = None
    tree_reader: Callable[[Task], str] | None = None    # upstream's tree for the base commit (default: GitHub's API)
    critic: Callable[[Path], str] | None = None         # the patch's reviewer, given its pack (default: ``run_codex``)
    variant: str = ""            # "r2": round 2, G6 with its own model; "r3": round 3, formal methods (PLAN.md section 9)
    prior_spent: float = 0.0     # what earlier rounds spent, under the same $100 stop
    arm: str = ""                # round 3: "A" (no G6), "B" (G6 as shipped) or "C" (G6 and the formal addendum)
    model: str = c.MODEL         # round 3: the agent's model
    repeat: int = 1              # round 3: which repeat of the same model, task and arm (Sonnet's arm A: 1 or 2)
    tasks: Sequence[Task] | None = None   # round 3's schedule's tasks (default: tasks.json)
    rerun_why: str = ""          # round 3: a run whose agent started and ended in "error" may be made again, saying why
    executor: str = "container"  # "host": Claude Code on the host, on the subscription's sign-in (PLAN.md, 2026-11-04)
    starter: Callable[..., Any] | None = None    # how the host session's process starts (default: ``subprocess.Popen``)
    watchdog: Any = None         # arm K3's steps: the run's one watchdog (``Watchdog``), which ends what they start at the deadline

    def client_env(self, key: str | None = None) -> dict[str, str]:
        base = dict(self.environ) if self.environ is not None else {
            k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        return {**base, "ANTHROPIC_API_KEY": key} if key else base


def latest(archive: c.Archive, kind: str, task: str) -> dict[str, Any] | None:
    """The newest finished run of ``kind`` for ``task``, with its record."""
    ended = [r for r in archive.rows() if r.get("event") == "ended" and r.get("status") == "finished"
             and str(r.get("run", "")).startswith(f"{kind}/{task}/")]
    if not ended:
        return None
    rel = sorted(r["run"] for r in ended)[-1]
    return {"run": rel, "path": archive.root / rel,
            "record": json.loads((archive.root / rel / "run.json").read_text(encoding="utf-8"))}


def redact_tree(folder: Path, redact: c.Redactor) -> int:
    """Mask the key in every file under ``folder`` (files copied out of a container); returns how many changed."""
    changed = 0
    for path in folder.rglob("*") if folder.is_dir() else ():
        if not path.is_file():
            continue
        data = path.read_bytes()
        text = data.decode("utf-8", errors="surrogateescape")
        masked = redact(text)
        if masked != text:
            path.write_bytes(masked.encode("utf-8", errors="surrogateescape"))
            changed += 1
    return changed


def copy_out(docker: Any, ctr: str, src: str, dest: Path) -> bool:
    cp = docker("cp", f"{ctr}:{src}", str(dest), timeout=900)
    return cp.returncode == 0 and dest.exists()


def key_check(ctx: Context, task: Task, image: str, key: str, tag: str, *, net: str = "",
              px: str = "") -> dict[str, Any]:
    """PLAN.md section 4: in a container of its own from the run's image, with no network; in round 2 with G6's key
    file in place, on the run's network (``net``, way out through ``px``), so its review can ask G6's model."""
    name = f"g6we-keyprobe-{tag}"
    try:
        # round 2's check reviews with G6's model, so it sits on the run's network, whose one way out is the proxy
        network = ["--network", net] if net else ["--network", "none"]
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", *network, *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", image, "tail", "-f", "/dev/null", timeout=600), "key check container")
        if ctx.variant == ROUND2:
            write_key_file(ctx.docker, name, key)     # round 2: G6's own key file, which the confined test must not see
        proxy = ([a for e in (f"HTTPS_PROXY=http://{px}:{PROXY_PORT}", f"HTTP_PROXY=http://{px}:{PROXY_PORT}",
                              "NO_PROXY=localhost,127.0.0.1") for a in ("-e", e)] if px else [])
        done = ctx.docker("exec", "-i", "-u", USER, "-e", f"HOME={HOME}", "-e", "ANTHROPIC_API_KEY", *proxy, name,
                          G6PY, "-I", "-", input=keyprobe_driver().encode("utf-8"), env=ctx.client_env(key),
                          timeout=1800)
        try:
            found = json.loads(out(done).splitlines()[-1])
        except (ValueError, IndexError):
            found = {"error": f"the driver printed nothing readable (exit {done.returncode})"}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    found["ok"] = key_check_ok(found, key_file=ctx.variant == ROUND2)
    return found


def start_net(ctx: Context, tag: str) -> tuple[str, str]:
    net, px = f"g6wenet-{tag}", f"g6wepx-{tag}"
    checked(ctx.docker("network", "create", "--internal", "--label", "g6we=1", net), "network create")
    checked(ctx.docker("run", "-d", "--name", px, "--label", "g6we=1", FROM_IMAGE, "tail", "-f", "/dev/null",
                       timeout=600), "proxy container")
    checked(ctx.docker("cp", str(ctx.proxy_source), f"{px}:/opt/egress_proxy.py", timeout=120), "proxy copy")
    checked(ctx.docker("network", "connect", net, px), "network connect")
    allow = [a for h in MODEL_HOSTS for a in ("--allow", h)]
    checked(ctx.docker("exec", "-d", px, "python3", "-I", "/opt/egress_proxy.py", "--port", str(PROXY_PORT), *allow,
                       "--log", f"/opt/{c.NDJSON['egress']}"), "proxy start")
    return net, px


def egress(ctx: Context, px: str, dest: Path) -> dict[str, Any]:
    ctx.docker("cp", f"{px}:/opt/{c.NDJSON['egress']}", str(dest), timeout=120)
    entries = []
    if dest.is_file():
        entries = [json.loads(line) for line in c.read_stream(dest) if line.strip()]
    return {"requests": len(entries),
            "allowed": sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is True}),
            "refused": sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is False})}


def refusal(ctx: Context, task: Task) -> str:
    """Why the run may not start, or "". A file ``HOLD`` at the archive's root holds every run (its text says why)."""
    hold = ctx.archive.root / "HOLD"
    if hold.is_file():
        return "held: " + hold.read_text(encoding="utf-8").strip()[:300]
    if not ctx.g6.get("contains_required"):
        return f"G6's commit {str(ctx.g6.get('commit'))[:12]} lacks a required commit ({', '.join(c.REQUIRED_G6_COMMITS)})"
    v = latest(ctx.archive, "validate", task.id)
    if v is None or not v["record"].get("ok") or not v["record"].get("fail_to_pass"):
        return "the task has no finished validation with a FAIL_TO_PASS test"
    _, oracle = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    if v["record"].get("oracle_image_id") != out(ctx.docker("image", "inspect", oracle, "--format", "{{.Id}}")):
        return "the task's validation was not made on the images this run would use"
    if ctx.variant == ROUND3:
        return ""     # round 3's own stop, order and caps: r3_refusal
    need = c.RUN_STOP_USD + (G6_ALLOWANCE_USD if ctx.variant == ROUND2 else 0.0)
    ok, why = c.may_start(c.spent(ctx.archive.rows()) + ctx.prior_spent, need=need)
    return "" if ok else f"the $100 stop: {why}"


DRY_KEY = "placeholder-not-a-key-0000"
DEAD_PROXY = "http://127.0.0.1:9"


def hook_selftest(ctx: Context, agent: str, task: Task) -> dict[str, Any]:
    """The dry run's check of the round hook: a change in the working tree, the hook run as Claude Code runs it, then
    the saved patch holds the change and the repository's index is as it was."""
    script = (f'cd "{task.repo}" && before=$(git diff --cached --name-only | wc -l) && echo "# dry run" >> we_dry_run.txt'
              f' && echo "{{}}" | {WE}/round_hook.sh > /tmp/we-hook-out.txt 2>&1; code=$?;'
              f' after=$(git diff --cached --name-only | wc -l); saved=$(grep -c we_dry_run.txt {WE}/rounds/round-1.patch);'
              f' said=$(wc -c < /tmp/we-hook-out.txt); rm -f we_dry_run.txt {WE}/rounds/round-1.*;'
              f' echo "{{\\"exit\\": $code, \\"index_before\\": $before, \\"index_after\\": $after, '
              f'\\"patch_names_change\\": $saved, \\"printed_bytes\\": $said}}"')
    try:
        return json.loads(out(ctx.docker.sh(agent, script, workdir=task.repo)).splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "the hook self-test printed nothing readable"}


def run_task(ctx: Context, task: Task, *, dry: bool = False) -> dict[str, Any]:
    """One run (PLAN.md section 4). Refused before anything starts when G6's commit, the validation or the stop says
    so; a failed key check starts no agent. ``dry``: the same path with no key and no way out (a placeholder in the
    key's place, Claude Code's proxy a dead port), plus a self-test of the round hook; nothing can be spent."""
    round3 = ctx.variant == ROUND3
    why = "" if dry else refusal(ctx, task)
    cap, stop = c.RUN_CAP_USD, c.RUN_STOP_USD
    if round3:
        if not re.fullmatch(r"[A-ERST]|[GHKLMVX]\d+", str(ctx.arm)) or ctx.model not in R3_MODELS:
            why = why or f"round 3 needs an arm ({', '.join(R3_ARMS)}) and a model ({', '.join(R3_MODELS)})"
        elif dry:
            cap, stop = r3_caps(ctx.model, spent=0.0, fable_left=1)
        elif not why:
            why, cap, stop = r3_refusal(ctx, task)
    if not why and getattr(ctx, "executor", "container") != "container":
        why = host_refusal(ctx, dry=dry)
    if why:
        return {"status": "refused", "why": why, "task": task.id}
    body = _run_task(ctx, task, dry=dry, cap=cap, stop=stop)
    if round3 and not dry:
        body["ledger"] = r3_ledger(ctx.archive, body)
    return body


def run_session(ctx: Context, run: Any, agent: str, command: list[str], key: str, tally: c.Tally, text_in: str, name: str,
                *, seconds: float, stop_usd: float, codes: list[int | None] | None = None,
                deadline: Any = None) -> str:
    """One Claude Code session in the container, its stream saved as ``name``: how it ended, as ``read_stream`` says. ``codes``,
    when given, receives the session's exit code, which arm K3's steps read (``step_ending``). ``deadline``, when given, is the
    run's watchdog (``Watchdog``, or its deadline): it ends the session's processes when it fires, no prompt goes in once it has
    fired, and the stream ends at its deadline however long the process took to start. Without it the session has ``seconds``
    from when its prompt has gone in, as before."""
    dog = None if deadline is None else Watchdog.of(deadline)
    raw = unbounded(ctx.docker)
    proc = ctx.docker.popen(*command, env=ctx.client_env(key))
    stream = open(run.fresh_path(name), "w", encoding="utf-8", newline="\n")

    def end() -> None:               # Claude Code in the container, then its client, by the helpers every session ends by
        raw.sh(agent, KILL_AGENT, user="root", timeout=60)
        proc.kill()

    try:
        with (dog.watching(end) if dog is not None else contextlib.nullcontext()):
            if dog is not None and not dog.may_start("prompt"):
                return "timeout"     # no prompt goes in after the deadline: the process is ended below
            proc.stdin.write(text_in.encode("utf-8"))
            proc.stdin.close()
            return read_stream(iter(proc.stdout.readline, b""), tally,
                               deadline=dog.deadline if dog is not None else time.monotonic() + seconds,
                               write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()),
                               stop_usd=stop_usd)
    finally:
        stream.close()
        if proc.poll() is None:
            raw.sh(agent, KILL_AGENT, user="root", timeout=60)
            proc.kill()
        code = proc.wait(timeout=60)
        if codes is not None:
            codes.append(code)


def host_refusal(ctx: Context, *, dry: bool) -> str:
    """Why a run cannot be made by the host executor, or "". It makes round 3's runs of Claude Code alone, in one session (arms A,
    S and T) or in arm K's loop, whose repairs it makes on the host too; never a dry run, which on the host would spend the
    subscription."""
    if ctx.executor != "host":
        return f"there is no executor {ctx.executor!r}"
    if dry:
        return "a dry run is never made on the host: its Claude Code would spend the subscription"
    if ctx.variant != ROUND3 or not (ctx.arm in ("A", "S", "T") or re.fullmatch(r"K\d+", ctx.arm)):
        return "the host executor makes round 3's runs of arms A, S, T and K only"
    return ""


def host_workspace(ctx: Context, tag: str) -> Path:
    """The folder the host's Claude Code works in, beside the archive's critic packs, removed when the run ends. Claude Code
    refuses edits under a ``.claude`` folder, so an archive under one is refused before the agent starts."""
    folder = (ctx.archive.root / "workspaces" / tag).resolve()
    if any(part.lower() == ".claude" for part in folder.parts):
        raise RuntimeError(f"the workspace {folder} is under a .claude folder, where Claude Code refuses edits")
    folder.mkdir(parents=True)
    return folder


def workspace_from(ctx: Context, task: Task, agent: str, dest: Path) -> int:
    """The parent's tree, from the agent's container into ``dest`` on the host, where the host's Claude Code works on it: no
    ``.git`` and no ``.venv``, which stay in the container. It comes out by the one export (``box_mcp.EXPORT``) and goes in by the
    one rule every tree from the container is brought into the workspace by, a command's after each call of the box among them
    (``box_mcp.bring_in``): only plain files and folders come out, and an absolute name, a ``..`` part, a link, a device, or a
    name the host cannot hold as it is (two differing only in case, or one Windows would refuse or change) refuses the whole
    tree before anything is written. Then each member is checked against what was written: it is there under its exact name,
    and no two files are one file (the same device and inode, which NTFS gives too), so a short-name alias or any other
    collision refuses the whole tree, and ``dest`` is left empty, before the agent starts. First of all, ``dest`` itself must be
    a path the host holds as named, by the box's one check (``box_mcp.root_named``), so nothing is exported into ``workspace``
    for ``workspace.``. Returns how many files came out."""
    box_mcp.root_named(dest)
    done = ctx.docker("exec", "-u", USER, "-w", task.repo, agent, *box_mcp.EXPORT, timeout=900)
    if done.returncode != 0:
        raise RuntimeError(f"the workspace's export failed ({done.returncode}): {err(done)[-600:]}")
    try:
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            members = box_mcp.export_members(tar)
    except (box_mcp.Refused, tarfile.TarError) as exc:
        raise RuntimeError(f"the workspace's export was refused, and nothing of it written: {exc}") from exc
    try:
        box_mcp.bring_in(dest, done.stdout)
    except box_mcp.Refused as exc:
        for child in Path(dest).iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        raise RuntimeError(f"the workspace's export was refused, and nothing of it kept: {exc}") from exc
    return sum(member.isfile() for member in members.values())


#: The one check of a tree brought into the workspace, by its exact names and its files' identities: the box's.
written = box_mcp.written


def box_state_of(workspace: Path) -> Path:
    """The box's state file for the run whose workspace is ``workspace``: beside it, outside it, so the agent's tools never reach it
    and no mirror or pull reads it (``box_mcp.write_state``)."""
    return workspace.parent / f"{workspace.name}.box.json"


def host_files(ctx: Context, run: Any, task: Task, agent: str, workspace: Path) -> tuple[Path, Path]:
    """The host session's MCP configuration, the box and nothing else, and its settings (``HOST_SETTINGS``), in the run's folder.
    The box keeps each call's phase in its state file (``box_state_of``), where the runner finds a call it did not finish."""
    exe = getattr(ctx.docker, "exe", "docker")
    box = {"command": sys.executable, "args": ["-I", str(HERE / "box_mcp.py"), "--container", agent, "--repo", task.repo,
                                               "--workspace", str(workspace), "--user", USER, "--timeout", str(BOX_TIMEOUT_S),
                                               "--docker", shutil.which(exe) or exe, "--state", str(box_state_of(workspace))]}
    return run.write_json("host_mcp.json", {"mcpServers": {"box": box}}), run.write_json("host_settings.json", HOST_SETTINGS)


def host_session(ctx: Context, run: Any, workspace: Path, mcp_file: Path, settings_file: Path, text: str, name: str, *,
                 resume: str = "", seconds: float, tally: c.Tally, stop_usd: float | None = None, deadline: Any = None) -> str:
    """One Claude Code session on the host, on the subscription's sign-in, its stream saved as ``name`` as ``run_session`` saves
    one; its folder the workspace, and the bug's container reached only through the box. ``resume``: the session a repair goes
    on with. Its clock starts before its prompt goes in, which a thread writes; its turns and time are capped. ``stop_usd``: what
    it may spend of the API's money, its run's need (``wecommon.api_need``): a host run's is nothing, and the stream's dollars are
    the subscription's estimate, so a need of nothing (or none given) stops nothing. The stream stops at once at an init that is
    not exactly the box's (``host_init``) or at the usage limit. Whatever happened, every process the session started is then
    ended: it started in a process tree of its own (``host_start``), killed strictly (``end_session``); its exit code, whether G6
    had to end it, and why its processes could not be confirmed ended ("" when they were) are saved beside the stream. Returns
    how it ended, as ``host_ending`` decides from all of it ("" when cleanly), with why G6 refused it, or when the limit resets,
    saved there too; an ending that is not clean is its run's, written there at once (``RunFolder.end``). ``deadline``: arm K3's
    run's watchdog (``Watchdog``), given for a resumed session as ``run_session`` is given it: while the session runs, the
    watchdog ends its processes when it fires. They are ended once, by whichever comes first, the watchdog or the session's end."""
    command = [shutil.which(HOST_CLAUDE) or HOST_CLAUDE, *host_args(mcp_file, settings_file, ctx.model, resume=resume)]
    tree = proc = stream = writer = drain = None
    errors: list[bytes] = []
    cut: list[str] = []          # the line the stream was stopped at, before its end
    stop, by_g6, left, code = "error", False, "", None
    dog = None if deadline is None else Watchdog.of(deadline)
    ended_once: list[str] = []
    once = threading.Lock()

    def end() -> str:                    # the session's process tree ended once (``end_session``), and why not when it was not
        with once:
            if not ended_once:
                ended_once.append(end_session(proc, workspace, tree) or "")
            return ended_once[0]

    def lines() -> Iterator[bytes]:      # read from a pipe of bytes, at b"\n" alone, as ``c.stream_lines`` splits a saved stream
        for line in iter(proc.stdout.readline, b""):
            yield line
            said = line.decode("utf-8", errors="replace")
            try:
                init = host_init(json.loads(said))
            except ValueError:
                init = None
            if (init and init["why"]) or limit_of(said) is not None:
                cut.append(said)
                return

    def feed() -> None:
        try:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
        except (OSError, ValueError):
            pass             # a process that ended, or was stopped, before it took its prompt

    try:        # the process starts inside the try whose finally ends it: nothing that fails after its start leaves it running
        tree = host_start(ctx.starter or subprocess.Popen, command, cwd=str(workspace),
                          env=host_env(os.environ if ctx.environ is None else ctx.environ),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc = tree.proc
        with (dog.watching(end) if dog is not None else contextlib.nullcontext()):    # K3: the run's watchdog ends it too
            drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b""), daemon=True)
            drain.start()
            stream = open(run.fresh_path(name), "w", encoding="utf-8", newline="\n")
            until = time.monotonic() + seconds
            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
            stop = read_stream(lines(), tally, deadline=until,
                               write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()),
                               stop_usd=stop_usd or float("inf"))
    finally:
        if stream is not None:
            stream.close()
        if proc is not None:
            if not stop and not cut:      # the stream came to its end: the session is given a moment to exit by itself
                try:
                    proc.wait(timeout=HOST_EXIT_S)
                except subprocess.TimeoutExpired:
                    pass
            by_g6 = proc.poll() is None        # still running: G6 ends it, and its exit code is G6's doing
            left = end()
            if proc.poll() is None:
                proc.kill()
            code = proc.wait(timeout=60)
        if writer is not None:
            writer.join(timeout=30)
    drain.join(timeout=30)
    stem, said = Path(name).stem, b"".join(errors).decode("utf-8", errors="replace")
    run.write(c.NDJSON["stderr"].format(stem=stem), said)
    run.write_json(f"{stem}_exit.json", {"exit_code": code, "ended_by_g6": by_g6, "not_ended": left})
    facts = host_facts(run.path / name, ctx.model)
    limit = facts["limit"] if facts["limit"] is not None else next((x for x in map(limit_of, c.stream_lines(said)) if x is not None),
                                                                   None)
    ended, why = host_ending(stop, facts, code=code, limit=limit, by_g6=by_g6, left=left)
    if ended in ("refused", "limited"):
        run.write(f"{stem}_{'refused' if ended == 'refused' else 'limit'}.txt", why)
    if ended:       # a session that did not end cleanly ends its run: the run's ending, before anything after it can fail
        run.end(ended, why=why)
    return ended


def probe_gate(ctx: Context, task: Task, agent: str, where: str, tree: str) -> list[str]:
    """``scout_gate`` on the probes in ``where``: pytest's exit, the cross test's cases, and failures that are not
    assertions, as the container reports them."""
    if qualified(ctx.arm):
        got = read_probes(out(ctx.docker.sh(agent, probe_script(where), workdir=task.repo, timeout=300)))

        def cases(test: str) -> list[str]:
            return [v for k, v in got.results.items() if k.rsplit("::", 1)[-1].split("[")[0] == test]

        crashed = sum(v == "error" or (v == "failed" and not re.match(r"assert|AssertionError|Failed", got.texts.get(k, "")))
                      for k, v in got.results.items())
        return scout_gate(out(ctx.docker.sh(agent, f"cat {tree} 2>/dev/null", timeout=60)),
                          exit_code=2 if got.exit is None else got.exit, collected=len(cases("test_cross")), broken=crashed,
                          randoms=sum(v in ("passed", "failed") for v in cases("test_random")),
                          executed=sum(v in ("passed", "failed") for v in cases("test_cross")))
    run_them, count = probes_at(where), PROBE_COLLECT.replace(f"{WE}/probes", where)
    said = out(ctx.docker.sh(agent, f"COLUMNS=300 {run_them} {gate_flags()} > /tmp/we-gate.txt 2>&1; code=$?; "
                                    'grep "^FAILED" /tmp/we-gate.txt | head -c 400000; echo "exit=$code"; '
                                    f'echo "cross=$({count} 2>/dev/null | grep -c test_cross)"; '
                                    f'echo "random=$({count} 2>/dev/null | grep -c test_random)"; rm -f /tmp/we-gate.txt',
                             workdir=task.repo, timeout=300))
    code, collected, broken = gate_numbers(said)
    return scout_gate(out(ctx.docker.sh(agent, f"cat {tree} 2>/dev/null", timeout=60)), exit_code=code,
                      collected=collected, broken=broken,
                      randoms=random_cases(said) if int(ctx.arm[1:]) >= 6 else None)


def probe_sets(arm: str) -> list[tuple[str, str]]:
    """Each probe set's label and place: the agent's, then the hidden ones."""
    return [("scout", f"{WE}/probes"), *((f"held{i}", hidden_at(i)[0]) for i in range(hidden_scouts(arm)))]


def run_set(ctx: Context, task: Task, agent: str, where: str) -> ProbeRun:
    """One probe set on the tree as it stands. A hidden set is copied in for the run and removed: the agent cannot read it."""
    held = where != f"{WE}/probes"
    if held:
        ctx.docker.sh(agent, f"rm -rf {HELD_COPY}; [ -d {where} ] && cp -r {where} {HELD_COPY} && chown -R {USER} {HELD_COPY}; "
                             "true", user="root", timeout=60)
    said = out(ctx.docker.sh(agent, probe_script(HELD_COPY if held else where), workdir=task.repo, timeout=300))
    if held:
        ctx.docker.sh(agent, f"rm -rf {HELD_COPY}", user="root", timeout=60)
    return read_probes(said)


def critic_files(n: int) -> tuple[str, str]:
    """Where review ``n`` keeps its reply and its test, beside the earlier ones: an earlier test stays to catch a repair that undoes it."""
    return ("critic_reply.txt", "test_critic.py") if n == 1 else (f"critic_reply_{n}.txt", f"test_critic_{n}.py")


def run_critic(ctx: Context, task: Task, run: Any, agent: str, base: str, title: str, text: str, n: int = 1) -> dict[str, Any]:
    """Arm L8's critic (PLAN.md, 2026-10-31): another model family reads the finished patch, on the host, with what the agent
    was given and the parent. It sees no maintainers' test and no fix: the tree comes from the agent's own container. A critic
    that fails is recorded and the run goes on. Its test, if it has one, waits where ``run_set`` can run it."""
    reply, test_file = critic_files(n)
    started = time.monotonic()
    found: dict[str, Any] = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    proof = f"review {uuid.uuid4().hex}"
    # an ordinary folder: mkdtemp's 0o700 is an ACL on Windows that shuts out the critic's sandbox account
    pack = ctx.archive.root / "critic_packs" / proof.split()[1]
    try:
        pack.mkdir(parents=True)
        checked(ctx.docker.sh(agent, diff_script(task.repo, base, f"{WE}/critic.patch"), workdir=task.repo, timeout=300),
                "the critic's patch")
        if not copy_out(ctx.docker, agent, f"{WE}/critic.patch", pack / "candidate.patch"):
            raise RuntimeError("the patch could not be copied")
        tree = ctx.docker("exec", "-u", USER, "-w", task.repo, agent, "git", "archive", "--format=tar", base, timeout=300)
        checked(tree, "the parent's tree")
        with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    dest = pack / "parent" / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(member).read())
        (pack / "issue.txt").write_text(f"{proof}\n\n{title}\n\n{text}", encoding="utf-8")
        (pack / "critic_prompt.md").write_text(critic_text(ctx.arm), encoding="utf-8")
        dog = getattr(ctx, "watchdog", None)
        if ctx.critic is not None or dog is None:
            said = (ctx.critic or run_codex)(pack)
        else:                         # arm K3: the run's watchdog ends the Codex task at the deadline, and it has no more
            with dog.watching(lambda: end_codex(pack)):
                said = run_codex(pack, timeout=min(CRITIC_SECONDS, dog.left()))
        run.write(reply, ctx.archive.redact(said))
        found = critic_report(said, proof=proof)
        if found["test"]:
            write_in(ctx.docker, agent, f"{CRITIC_AT}/{test_file}", found["test"] + "\n", user="root")
    except (RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        shutil.rmtree(pack, ignore_errors=True)
    return {**found, "seconds": round(time.monotonic() - started, 1)}


def run_scout(ctx: Context, task: Task, run: Any, agent: str, base: str, docker_args: list[str], key: str, title: str,
              text: str) -> dict[str, Any]:
    """Arm L's first step (PLAN.md, 2026-10-31): the scout's session, then the repository back at the base and its probes
    out of the agent's reach. From L5, a second scout whose work the agent never sees. The sessions' cost is returned, to
    count in the run."""
    owner, name = task.project.split("/", 1)
    cap_at = docker_args.index("--max-budget-usd") + 1
    args = [*docker_args[:cap_at], f"{SCOUT_CAP_USD:.2f}", *docker_args[cap_at + 1:]]
    tally = c.Tally(count_reported=True)
    filled = {"owner": owner, "name": name, "number": task.issue, "title": title, "text": text}
    asked = scout_prompt(ctx.arm).format(**filled)
    run.write("scout_prompt.txt", asked)

    def session(command: list[str], text_in: str, name: str) -> str:
        return run_session(ctx, run, agent, command, key, tally, text_in, name, seconds=1200.0,
                           stop_usd=SCOUT_CAP_USD * R3_MARGIN)

    def checked_again(wrong: list[str], name: str, where: str, tree: str) -> tuple[str, list[str]]:
        """One more try, in the same session, with what the runner counted."""
        ended = session(scout_again(args), "The runner checked your work and refused it:\n- " + "\n- ".join(wrong)
                        + "\nPut this right and finish.", name)
        return ended, probe_gate(ctx, task, agent, where, tree)

    def restore() -> None:
        checked(ctx.docker.sh(agent, f"git reset -q --hard {base} && git clean -fdq", workdir=task.repo, timeout=120),
                "restore the repository")

    visible = (f"{WE}/probes", f"{WE}/tree.json")
    ended = session(args, asked, c.NDJSON["tagged_stream"].format(tag="scout"))
    wrong = probe_gate(ctx, task, agent, *visible) if int(ctx.arm[1:]) >= 3 else []
    refused = list(wrong)
    if wrong:
        ended, wrong = checked_again(wrong, c.NDJSON["numbered_stream"].format(tag="scout", n=2), *visible)
    if has_critic(ctx.arm):   # a fresh session: it has read neither the scout's reasoning nor any patch
        restore()
        ended = session(args, critic_prompt(task.issue, title, text), c.NDJSON["tagged_stream"].format(tag="critic"))
        wrong = probe_gate(ctx, task, agent, *visible)
        if wrong:
            refused += wrong
            ended, wrong = checked_again(wrong, c.NDJSON["numbered_stream"].format(tag="critic", n=2), *visible)
    hidden: dict[str, Any] = {}
    if holds_loop(ctx.arm):
        restore()
        checked(ctx.docker.sh(agent, f"mkdir -p {WE}/probes; chown -R root:root {WE}/probes {WE}/tree.json; "
                                     f"chmod -R go-rwx {WE}/probes {WE}/tree.json; true", user="root", timeout=60),
                "keep the first scout's work from the second")
        ctx.docker.sh(agent, f"mv {HOME}/.claude/projects {WE}/scout-projects; chown -R root:root {WE}/scout-projects; "
                             f"chmod -R go-rwx {WE}/scout-projects; true", user="root", timeout=60)
        for i in range(hidden_scouts(ctx.arm)):
            where, tree_at = hidden_at(i)
            tag = f"hidden{i + 1}" if i else "hidden"
            second = scout_prompt(ctx.arm, hidden=True, at=i).format(**filled)
            run.write(f"{tag}_prompt.txt", second)
            session(args, second, c.NDJSON["tagged_stream"].format(tag=tag))
            left = probe_gate(ctx, task, agent, where, tree_at)
            refused += left
            if left:
                _, left = checked_again(left, c.NDJSON["numbered_stream"].format(tag=tag, n=2), where, tree_at)
            run.write(f"{tag}_at_base.txt", out(ctx.docker.sh(agent, f"{probes_at(where)} 2>&1 | tail -c 8000",
                                                              workdir=task.repo, timeout=300)))
            copy_out(ctx.docker, agent, where, run.path / f"{tag}_probes")
            held_tree = scout_tree(out(ctx.docker.sh(agent, f"cat {tree_at} 2>/dev/null", timeout=60)) or "no tree")
            run.write(f"{tag}_tree.txt", held_tree["text"])
            hidden[tag] = {"still_wrong": left, **{k: held_tree.get(k) for k in ("shapes", "open", "error")}}
            restore()
            checked(ctx.docker.sh(agent, f"mkdir -p {where}; chown -R root:root {where} {tree_at}; chmod -R go-rwx {where} "
                                         f"{tree_at}; mv {HOME}/.claude/projects {WE}/scout-projects-{tag}; chown -R "
                                         f"root:root {WE}/scout-projects-{tag}; chmod -R go-rwx {WE}/scout-projects-{tag}; "
                                         "true", user="root", timeout=60), "hide a scout's work from the next")
        checked(ctx.docker.sh(agent, f"mkdir -p {HOME}/.claude/projects; rm -rf {HOME}/.claude/todos "
                                     f"{HOME}/.claude/shell-snapshots /tmp/*; chown -R {USER} {HOME}/.claude/projects; "
                                     "true", user="root", timeout=120), "clear what the scouts left")
    restore()
    checked(ctx.docker.sh(agent, f"mkdir -p {WE}/probes && chown -R root:root {WE}/probes && chmod -R a+rX,go-w "
                                 f"{WE}/probes; [ -f {WE}/tree.json ] && chown root:root {WE}/tree.json "
                                 f"&& chmod a+r {WE}/tree.json; true", user="root", timeout=60), "lock the probes")
    at_base = ctx.docker.sh(agent, f"{PROBE_RUN} 2>&1 | tail -c 8000", workdir=task.repo, timeout=300)
    run.write("scout_at_base.txt", out(at_base))
    copy_out(ctx.docker, agent, f"{WE}/probes", run.path / "scout_probes")
    tree_file = run.fresh_path("tree.json")
    tree = scout_tree(tree_file.read_text("utf-8") if copy_out(ctx.docker, agent, f"{WE}/tree.json", tree_file)
                      else "no tree")
    run.write("tree.txt", tree["text"])
    sets: dict[str, ProbeRun] = {}
    if qualified(ctx.arm):   # on the clean base: what each case did before any fix, and which sets can be trusted to run
        sets = {label: run_set(ctx, task, agent, where) for label, where in probe_sets(ctx.arm)}
        run.write_json("probes_at_base.json", {label: {"exit": got.exit, "ran": got.ran, "results": got.results}
                                               for label, got in sets.items()})
    return {"ended": ended, "usd": round(tally.cost_usd, 6), "refused": refused, "still_wrong": wrong, "hidden": hidden,
            "quarantined": sorted(label for label, got in sets.items() if not got.usable), "at_base": sets,
            **{k: tree.get(k) for k in ("shapes", "open", "error")}}


def repair_loop(ctx: Context, task: Task, run: Any, agent: str, docker_args: list[str], key: str, tally: c.Tally,
                stop: float, started: float, at_base: Mapping[str, ProbeRun] | None = None,
                critique: Callable[[], dict[str, Any]] | None = None, base: str | None = None) -> dict[str, Any]:
    """G6's ``cegis`` over the agent's finished work: the runner runs both scouts' probes and sends failures back in the
    same session. The hidden set is copied in for the run and removed, so the agent can never read it. From L8 a set that
    exercised nothing on the base is left out, as it can say nothing of the patch, and the critic reads the patch once. Its
    status is cegis's as the one rule reads it with the patch's size against ``base`` (``loop_status``)."""
    from g6.cegis import InputDomain, cegis

    rounds = [0]
    critic: dict[str, Any] = {}

    def check() -> tuple[set[str], str]:
        rounds[0] += 1
        if qualified(ctx.arm):
            if critique and rounds[0] == 1:
                critic.update(critique())
            seen = []
            for label, where in [*probe_sets(ctx.arm), *([("critic", CRITIC_AT)] if critic.get("test") else [])]:
                base = (at_base or {}).get(label)
                if label != "critic" and (base is None or not base.usable):
                    continue
                got = run_set(ctx, task, agent, where)
                run.write_json(f"repair_check_{rounds[0]}_{label}.json", {"exit": got.exit, "ran": got.ran,
                                                                          "results": got.results, "tail": got.tail})
                if label != "critic" or got.usable:     # a critic's test that will not run says nothing of the patch
                    seen.append((label, got, base))
            return with_critic(failures_of(seen), critic)
        failing, said = set(), []
        for label, where in (("scout", f"{WE}/probes"),
                             *((f"held{i}", hidden_at(i)[0]) for i in range(hidden_scouts(ctx.arm)))):
            held = where != f"{WE}/probes"
            if held:
                ctx.docker.sh(agent, f"rm -rf {HELD_COPY}; [ -d {where} ] && cp -r {where} {HELD_COPY} && "
                                     f"chown -R {USER} {HELD_COPY}; true", user="root", timeout=60)
            got = out(ctx.docker.sh(agent, f"COLUMNS=300 {probes_at(HELD_COPY if held else where)} --maxfail=10 -rfE "
                                           "> /tmp/we-check.txt 2>&1; code=$?; tail -c 12000 /tmp/we-check.txt; "
                                           'echo "exit=$code"; rm -f /tmp/we-check.txt', workdir=task.repo, timeout=300))
            if held:
                ctx.docker.sh(agent, f"rm -rf {HELD_COPY}", user="root", timeout=60)
            run.write(f"repair_check_{rounds[0]}_{label}.txt", got)
            found = probe_failures(got, label)
            failing |= found
            if found:
                said.append(got)
        return failing, "\n\n".join(said)

    def ask(text: str, n: int) -> None:
        run_session(ctx, run, agent, scout_again(docker_args), key, tally, text, c.NDJSON["numbered_stream"].format(tag="repair", n=n),
                    seconds=max(60.0, c.WALL_CLOCK_S - (time.monotonic() - started)), stop_usd=stop)

    def state() -> str:
        return out(ctx.docker.sh(agent, "git status --porcelain; git diff", workdir=task.repo, timeout=120))

    loop = Repairs(check, ask, state if qualified(ctx.arm) else None, repair_text(ctx.arm) if qualified(ctx.arm) else "")
    got = cegis(WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                max_iterations=MAX_REPAIRS + 1, timeout_s=float(c.WALL_CLOCK_S))
    return {"status": loop_status(got.status, patch_bytes(ctx, task, agent, base)), "rounds": loop.round, "reason": got.reason,
            "failing": sorted(loop.failing)[:40], "critic": {k: critic.get(k) for k in ("verdict", "seconds", "error")}}


#: A review loop's status when nothing is open over a patch that holds a change: the loop's one "clean", written here only.
LOOP_CLEAN = "no_counterexample_in_domain"
#: A review loop's status over a tree that holds no change, whatever any review said: never clean (replay cohort, 2026-11-06;
#: K3 pilot, pygments-3158 repeat 1, whose empty patch a review called clean).
NO_PATCH = "no_patch"
PATCH_BYTES = "we-patch-bytes"      # the line ``patch_bytes`` reads its answer from


def patch_bytes(ctx: Context, task: Task, agent: str, base: str | None) -> int | None:
    """How many bytes the tree's patch against ``base`` has, in the agent's container, made as every capture makes it
    (``diff_script``, new files included): 0 for a tree with no change. None when it could not be read: no base, a docker step
    that failed or ran out of time, or a reply without the size's own line."""
    if not base:
        return None
    at = f"{WE}/we-size.patch"
    try:
        said = out(ctx.docker.sh(agent, f'{diff_script(task.repo, base, at)}; printf "{PATCH_BYTES}=%s\\n" "$(wc -c < "{at}")"; '
                                        f'rm -f "{at}"', workdir=task.repo, timeout=300))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    found = re.findall(rf"^{PATCH_BYTES}=\s*(\d+)\s*$", said, re.M)
    return int(found[-1]) if found else None


def loop_status(status: str, size: int | None) -> str:
    """The one rule every review loop's status is decided by (``reviews_until_clean``, ``review_once``, ``reviews_with_bug_test``,
    ``repair_loop``), from what the loop found and the size of the patch it read (``patch_bytes``): ``NO_PATCH`` for a tree with
    no change, whatever the loop found; ``LOOP_CLEAN`` only over a patch known to hold a change, so a size that could not be
    read is "inconclusive", never clean; any other status as the loop found it."""
    if size == 0:
        return NO_PATCH
    if status == LOOP_CLEAN and not (size and size > 0):
        return "inconclusive"
    return status


def keep_first_patch(ctx: Context, task: Task, agent: str, base: str, n: int = 1) -> None:
    """The patch before a review, where ``check`` scores each round, so that what the repair changed can be seen."""
    ctx.docker.sh(agent, diff_script(task.repo, base, f"{WE}/rounds/round-{n}.patch"), workdir=task.repo, timeout=300)


def finding_rule(arm: str) -> str:
    """Which of a reviewer's failing cases go back to the agent in arm ``arm``: "any", as K1 sends them, or "parent", only those
    that failed on the parent too (K2), as the arm's generation sets it (``STRUCTURAL``)."""
    return trained(arm).get("finding", "any")


def reviews_until_clean(ctx: Context, task: Task, run: Any, agent: str, base: str, docker_args: list[str], key: str, tally: c.Tally,
                        stop: float, started: float, critique: Callable[[int], dict[str, Any]], finding: str = "any",
                        session: Callable[[str, str], Any] | None = None, tree: Callable[[], Any] | None = None,
                        after_step: bool = False, deadline: float | None = None) -> dict[str, Any]:
    """Arm K (PLAN.md, 2026-11-01): plain Claude Code, then the patch is read by another model family, repaired in the same session if
    the reader's test fails on it, and read again, until a reader reproduces nothing. One review saw one layer of a defect, so one
    repair stopped there. The agent may reject a finding, and leaving the code as it was ends the loop. No scout, no probes, no G6.
    With ``finding`` "parent" (K2, PLAN.md, 2026-11-04, evening), each round's test is also run on the parent in a container of its
    own, and only the cases that failed there and still fail go back; a parent run with no result sends nothing back. ``session``:
    a repair as ``session(note, stream name)``, the host executor's (PLAN.md, 2026-11-04), which returns how it ended ("" when
    cleanly); by default the same session continued in the container (``run_session``), ended by its own stop, result and exit
    (``step_ending``), never by an earlier session's result. A repair that does not end cleanly, on either executor, ends the
    loop, and how it ended is returned as ``ended``. ``tree``: what says whether a repair changed anything, compared
    before and after it; by default git's display of the container's tree.
    ``after_step``: K3's rule step ran before the loop, and the patch review 1 reads is saved already as ``rule.patch``, so round 1
    saves none and ``round-1.patch`` stays the first session's. ``deadline``: K3's, the run's watchdog (``Watchdog``, or its
    deadline). Each capture, review and repair asks it at its own start and starts only while it has not fired. Each one's end is
    recorded, and a repair's session gets what is left and no minute of its own. A loop the watchdog stopped, or one with a step
    that ended late, says "timeout" (``ended``), unless a host repair ended it first. Without it, K2's loop is as it was."""
    def shown() -> str:
        return out(ctx.docker.sh(agent, "git status --porcelain; git diff", workdir=task.repo, timeout=120))

    tree = tree or shown
    dog = None if deadline is None else Watchdog.of(deadline)

    def starts(step: str) -> bool:       # K3: a step starts only while the run's watchdog has not fired
        return dog is None or dog.may_start(step)

    def ends(step: str) -> None:
        if dog is not None:
            dog.end(step)

    gated = finding == "parent"          # K2 (PLAN.md, 2026-11-04, evening)
    at_parent: dict[str, str] = {}       # every round's test on the parent, in a container of its own, by case
    rounds, failing, reason, verdicts, seconds, broken, ended, timed_out = 0, [], "", [], 0.0, False, "", False
    # the container's repairs continue the first session (``--continue``): the one whose own result the tally holds as the loop
    # starts (the first session's, or K3's rule step's, which step_ending held to the first session's), fixed once; each repair's
    # own result must name it, as on the host (``resume``), and with no id there is no session to continue and no repair starts
    first = str(((tally.result if tally is not None else None) or {}).get("session_id") or "") if session is None else ""
    for n in range(1, MAX_REPAIRS + 2):
        if n > 1 or not after_step:
            if not starts(f"capture round-{n}"):
                timed_out = True
                break
            keep_first_patch(ctx, task, agent, base, n)
            ends(f"capture round-{n}")
        if not starts(f"review {n}"):
            timed_out = True
            break
        rounds = n
        critic = critique(n)
        ends(f"review {n}")
        verdicts.append(critic.get("verdict"))
        seconds += float(critic.get("seconds") or 0.0)
        failing = []
        if critic.get("test"):
            got = run_set(ctx, task, agent, CRITIC_AT)
            run.write_json(f"repair_check_{n}_critic.json", {"exit": got.exit, "ran": got.ran, "results": got.results, "tail": got.tail})
            failing = sorted(failures_of([("critic", got, None)])[0]) if got.usable else []
            if gated and failing:
                try:
                    parent = parent_check(ctx, task, critic, SimpleNamespace(at=CRITIC_AT, file=critic_files(n)[1]))
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    parent = None
                if parent is None or parent.exit is None:
                    failing, broken = [], True
                    reason = "the review's test gave no result on the parent, so no finding could be read"
                    break
                run.write_json(f"repair_check_{n}_critic_parent.json", {"exit": parent.exit, "ran": parent.ran,
                                                                        "results": parent.results, "tail": parent.tail})
                at_parent.update(parent.results)
                failing = [case for case in failing if at_parent.get(case.split(":", 1)[1]) in ("failed", "error")]
                if not failing:
                    reason = "every case the review's test failed on the patch passed on the parent"
                    break
        if not failing:
            reason = "a review reproduced nothing" if critic.get("verdict") in ("none", "defect") else "a review failed"
            break
        if n > MAX_REPAIRS:
            reason = "the repairs ran out with a defect open"
            break
        if not starts(f"repair {n}"):    # K3: no repair starts once the watchdog has fired
            timed_out = True
            break
        before = tree()
        note = CRITIC_NOTE.format(**{k: critic.get(k, "") for k in ("rule", "why", "test")}) + REVIEW_ASK
        if session is None and not first:     # no first session to continue: no prompt to any session, and the run is "error"
            ended = "error"
        elif session is None:     # the container's repair, ended by its own stop, result, exit and session (``step_ending``)
            codes, earlier = [], tally.result
            stopped = run_session(ctx, run, agent, scout_again(docker_args), key, tally, note,
                                  c.NDJSON["numbered_stream"].format(tag="repair", n=n),
                                  seconds=max(60.0, c.WALL_CLOCK_S - (time.monotonic() - started)) if dog is None else dog.left(),
                                  stop_usd=stop, codes=codes, **({"deadline": dog} if dog is not None else {}))
            own = tally.result if tally.result is not earlier else None     # ``Tally.feed`` keeps each result as a new dict
            ended = step_ending(stopped, own, codes[-1] if codes else None, session=first)
            ended = "" if ended == "finished" else ended
        else:
            ended = session(note, c.NDJSON["numbered_stream"].format(tag="repair", n=n))
        ends(f"repair {n}")
        if ended:
            reason = {"limited": "the repair met the subscription's usage limit",
                      "refused": "the repair's session was refused: its init was not exactly the five file tools and the box",
                      "model_refused": "the model refused a request in the repair; nothing is resent"
                      }.get(ended, f"the repair's session ended {ended}")
            break
        if tree() == before:
            reason = "the agent left the code as it was"
            break
    timed_out = timed_out or (dog is not None and dog.late())    # K3: the watchdog's records, read once
    if timed_out and not ended:
        reason = "the run's deadline passed"
    clean = not timed_out and not failing and not broken and verdicts[-1] in ("none", "defect")
    ended = ended or ("timeout" if timed_out else "")      # a host repair's own ending first: it is the run's ending on record
    return {"status": loop_status(LOOP_CLEAN if clean else "inconclusive", patch_bytes(ctx, task, agent, base)), "rounds": rounds,
            "reason": reason,
            "failing": failing[:40], "critic": {"verdicts": verdicts, "seconds": round(seconds, 1)},
            **({"finding": "parent"} if gated else {}), **({"ended": ended} if ended else {})}


def review_once(ctx: Context, task: Task, run: Any, agent: str, docker_args: list[str], key: str, tally: c.Tally, stop: float,
                started: float, critique: Callable[[], dict[str, Any]], base: str | None = None) -> dict[str, Any]:
    """Arm R, the control for the loop (PLAN.md, 2026-10-31 night): one review by another model family, and one repair if its test
    fails on the patch. No scout, no probes, no G6, so what the full loop adds is the difference. A test that will not run says
    nothing of the patch. Its status is decided by the one rule, with the patch's size against ``base`` (``loop_status``)."""
    critic = critique()
    rounds, failing = 1, set()

    def check() -> set[str]:
        got = run_set(ctx, task, agent, CRITIC_AT)
        run.write_json(f"repair_check_{rounds}_critic.json", {"exit": got.exit, "ran": got.ran, "results": got.results, "tail": got.tail})
        return failures_of([("critic", got, None)])[0] if got.usable else set()

    if critic.get("test"):
        failing = check()
    if failing:
        rounds = 2
        run_session(ctx, run, agent, scout_again(docker_args), key, tally,
                    CRITIC_NOTE.format(**{k: critic.get(k, "") for k in ("rule", "why", "test")}) + REVIEW_ASK, c.NDJSON["numbered_stream"].format(tag="repair", n=1),
                    seconds=max(60.0, c.WALL_CLOCK_S - (time.monotonic() - started)), stop_usd=stop)
        failing = check()
    return {"status": loop_status("inconclusive" if failing else LOOP_CLEAN, patch_bytes(ctx, task, agent, base)), "rounds": rounds,
            "reason": "", "failing": sorted(failing)[:40], "critic": {k: critic.get(k) for k in ("verdict", "seconds", "error")}}


# --- arm K3: K2 with the author's rule step after the first patch (PLAN.md, 2026-11-05, morning, later, items 1 and 4) ----------
RULE_STEP_ARMS = c.BASELINE_ARMS   # the arms that keep a baseline, whose admission asks it (``wecommon.unscored_why``)
#: What the same session is told after its first patch is saved; its digest is pinned by a test and recorded in each run.
RULE_STEP = """Before you finish: list each behaviour your change keeps on the code path it touches. For each one, find the
reference the issue gives for it: the words of the issue or its discussion, the old version's rule, or a parent or sibling
class the issue names. Write one test that checks the whole rule against that reference, every case the issue or the reference fixes, not
only the issue's own example, and run it. Where the reference and your code disagree, fix the code. Where the issue's own
example and the reference it names disagree, say so in your reply and follow the reference. Then reply with the list and
what you changed."""
RULE_DIGEST = hashlib.sha256(RULE_STEP.encode("utf-8")).hexdigest()[:16]
FIRST_PATCH = c.FIRST_PATCH    # the first session's tree: the within-run baseline, scored by ``check`` as "round-1"
RULE_PATCH = "rule.patch"      # the tree after the step, in ``rounds`` beside each round's patch, scored by ``check`` as "rule"
BASELINE = c.BASELINE          # the run's own folder for FIRST_PATCH, outside ``rounds``: nothing after its capture writes there
#: K3's steps from its first session to its first review, in order; each goes on only from the one before's success. The rule
#: step is its session and then its patch, each read by ``step_ending``.
K3_STEPS = ("first session", "first patch", "rule step")
#: The status of a K3 run with no Claude Code baseline (``has_baseline``), whatever ended it: the report counts it by arm, and it
#: is no row and never offered to ``check``.
NO_FIRST_PATCH = "no_first_patch"
#: What a capture reports to ``step_ending`` when its file is kept: a capture has no result of its own, and this is its success.
CAPTURED = {"subtype": "success", "is_error": False}
ROUND_PATCH = re.compile(r"round-(\d+)\.patch")


def rule_led(arm: str) -> bool:
    """Arm K3: K2's loop, with the rule step between the first session and the first review."""
    return arm in RULE_STEP_ARMS


def first_prompt_check(task: Task, title: str, text: str, sent: str) -> tuple[str, str]:
    """The first prompt a K3 run sends, against what arm A's prompt builder gives for the same task, title and issue text, the
    text as this run read it (with the issue's discussion, as every K arm's: ``gets_thread``): its sha256, and "" when the two
    digests are the same, else why the run is refused. A first prompt that is not arm A's would make a first patch that is not
    Claude Code alone, so the within-run comparison could not read it."""
    got = hashlib.sha256(sent.encode("utf-8")).hexdigest()
    want = hashlib.sha256(prompt(task, title, text, "A").encode("utf-8")).hexdigest()
    if got == want:
        return got, ""
    return got, (f"the first prompt (sha256 {got[:16]}) is not arm A's for this task (sha256 {want[:16]}): its first patch "
                 "would not be Claude Code alone, so the run is refused before the agent starts and is never scored")


def deadline_of(started: float) -> float:
    """The run's one deadline, from its absolute start (``time.monotonic`` when its clock began): the first session reads it,
    and the run's ``Watchdog`` enforces it on every step after that."""
    return started + c.WALL_CLOCK_S


class Watchdog:
    """Arm K3's one watchdog: the run's deadline (``deadline_of``), enforced on every step after the first session. A process a
    step starts is ended when it fires. A session or a review registers how it is ended (``watching``). A docker call's timeout
    is at most what is left (``docker``), so its process ends at the same moment. When it fires it ends every registered
    process and sets its one flag (``fired``); no step starts once the flag is set (``may_start``). Each step's end is recorded
    (``end``), and the run's ending reads those records once (``late``, in ``ended_as``). ``start`` runs its timer in a thread."""

    def __init__(self, deadline: float, clock: Callable[[], float] | None = None) -> None:
        self.deadline, self._clock = deadline, clock
        self.fired, self._stop, self._lock = threading.Event(), threading.Event(), threading.Lock()
        self.ends: list[tuple[str, float]] = []
        self._killers: dict[int, Callable[[], Any]] = {}
        self._next = 0

    @staticmethod
    def of(deadline: Any) -> "Watchdog":
        """``deadline`` when it is the run's watchdog already, else a watchdog of its own for that deadline (a unit called alone)."""
        return deadline if isinstance(deadline, Watchdog) else Watchdog(float(deadline))

    def now(self) -> float:
        return (self._clock or time.monotonic)()

    def left(self) -> float:
        return max(0.0, self.deadline - self.now())

    def due(self) -> bool:
        """The timer's one test: when the deadline has come, it fires. Whether it has fired."""
        if not self.fired.is_set() and self.now() >= self.deadline:
            self.fire()
        return self.fired.is_set()

    def fire(self) -> None:
        """Every process registered now is ended, once, and the flag is set before any of them, so no step starts meanwhile."""
        with self._lock:
            if self.fired.is_set():
                return
            self.fired.set()
            killers = list(self._killers.values())
        for kill in killers:
            try:
                kill()
            except Exception:  # noqa: BLE001 - one process that will not end does not keep the others running
                pass

    def may_start(self, step: str) -> bool:
        """Whether ``step`` may start: never once the flag is set, which each step asks at its own start."""
        return not self.due()

    def end(self, step: str) -> bool:
        """Records when ``step`` ended; whether it ended at or after the deadline, which ends the run "timeout". After ``stop``
        the steps are over, and nothing more is recorded."""
        at = self.now()
        if self._stop.is_set():
            return False
        self.ends.append((step, round(at, 3)))
        return at >= self.deadline

    def late(self) -> bool:
        """Whether the watchdog fired or any step ended at or after the deadline: the run then ends "timeout" (``ended_as``)."""
        return self.fired.is_set() or any(at >= self.deadline for _, at in self.ends)

    @contextlib.contextmanager
    def watching(self, kill: Callable[[], Any]):
        """While the block runs, ``kill`` is how its process is ended when the watchdog fires."""
        with self._lock:
            key, self._next = self._next, self._next + 1
            self._killers[key] = kill
        try:
            yield
        finally:
            with self._lock:
                self._killers.pop(key, None)

    def docker(self, raw: Any) -> "Bounded":
        return Bounded(raw, self)

    def start(self) -> None:
        """The timer: a thread that fires the watchdog at the deadline, until ``stop``."""
        def watch() -> None:
            while not self._stop.is_set() and not self.due():
                self._stop.wait(min(1.0, max(0.01, self.deadline - self.now())))
        threading.Thread(target=watch, name="k3-watchdog", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class Bounded:
    """A docker client whose every call the run's watchdog bounds: its timeout is at most what is left of the deadline, so the
    docker process ends when the watchdog fires, and none starts after it. Removing, killing or stopping a container is cleanup,
    never a step, so it is not bounded. Neither is ``raw``, which a session's end uses (``unbounded``)."""

    CLEANUP = ("rm", "kill", "stop")

    def __init__(self, raw: Any, dog: Watchdog) -> None:
        self.raw, self.dog = raw, dog

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)

    def __call__(self, *args: Any, timeout: float | None = None, **kw: Any) -> Any:
        if args and args[0] in self.CLEANUP:
            return self.raw(*args, timeout=timeout, **kw)
        return self.raw(*args, timeout=self.bound(args, timeout), **kw)

    def sh(self, ctr: str, script: str, *args: Any, timeout: float = 600, **kw: Any) -> Any:
        return self.raw.sh(ctr, script, *args, timeout=self.bound((ctr, script), timeout), **kw)

    def popen(self, *args: Any, **kw: Any) -> Any:
        return self.raw.popen(*args, **kw)

    def bound(self, args: Sequence[Any], timeout: float | None) -> float:
        left = self.dog.left()
        if left <= 0:
            self.dog.due()
            raise subprocess.TimeoutExpired([str(a) for a in args], 0)
        return left if timeout is None else min(float(timeout), left)


def unbounded(docker: Any) -> Any:
    """The docker client under a ``Bounded`` one, for ending a process; any other client as it is."""
    return getattr(docker, "raw", docker) if isinstance(docker, Bounded) else docker


def step_ending(stop: str | None, result: Mapping[str, Any] | None, code: int | None, *, session: str | None = None) -> str:
    """How one of K3's steps ended (``K3_STEPS``), by the one rule every step's caller uses. "finished" only for a success it
    recognises: no stop, a result of the step's own that the one reader of a result calls a success (``result_status``), an
    exit of 0, and, with ``session``, the result of that session (the first session's, which the rule step continues).
    Anything else is the status the run ends with: the stop; "error" for a result of another session, or none to continue;
    else what ``result_status`` says (a cap it names, or "error"), and "error" for a success with a non-zero or unread exit."""
    if stop:
        return stop
    if session is not None and (not session or (result or {}).get("session_id") != session):
        return "error"
    got = result_status(result)
    return "error" if got == "finished" and code != 0 else got


#: K3's baseline, its links and its admission are read where every fact about a run is (``wecommon.run_facts``): these are those.
linked, plain, baseline_of = c.linked, c.plain, c.baseline_of
baseline_bytes, has_baseline = c.baseline_bytes, c.has_baseline


def kept_digest(archive: c.Archive, rel: str) -> str | None:
    """The sha256 the archive's index recorded when K3 run ``rel``'s first patch was kept (``kept``), as ``wecommon.run_facts``
    reads it, or None. It is written once, at the capture, so it holds after any stop or restart."""
    facts = c.run_facts(archive, rel)
    return facts.kept.get(FIRST_PATCH) if facts is not None else None


def kept(ctx: Context, task: Task, agent: str, base: str, run: Any, name: str, deadline: Any = math.inf) -> str:
    """One of K3's captures: the tree's patch against ``base`` made in the container, then copied at once into the archive,
    written once (``fresh_path``), before the agent works again. The first patch goes into the run's ``BASELINE`` folder, with its
    sha256 in the archive's index (``kept_digest``); the rule step's goes into ``rounds``. It keeps the patch's real bytes, as
    ``final.patch`` keeps them, so the within-run comparison scores what each session left, and a patch that holds the run's key
    is not kept. ``deadline`` is the run's watchdog (or its deadline): the capture does not start once it has fired, its docker
    processes end when it fires, and a capture that ended late is "timeout" and keeps nothing. Otherwise its ending by
    ``step_ending``: "finished" only when the command exited 0 and the file is kept."""
    dog, step = Watchdog.of(deadline), f"capture {name}"
    if not dog.may_start(step):
        return "timeout"
    docker = dog.docker(ctx.docker)
    dest = run.fresh_path(f"{BASELINE}/{name}" if name == FIRST_PATCH else f"rounds/{name}")
    try:
        done = docker.sh(agent, diff_script(task.repo, base, f"{WE}/rounds/{name}"), workdir=task.repo, timeout=300)
        ok = done.returncode == 0 and copy_out(docker, agent, f"{WE}/rounds/{name}", dest)
    except subprocess.TimeoutExpired:
        done, ok = None, False
    late = dog.end(step) or done is None
    if ok and any(secret.encode("utf-8") in dest.read_bytes() for secret in ctx.archive.redact.secrets):
        ok = False
    if (late or not ok) and os.path.lexists(dest):
        dest.unlink()
    if late:
        return "timeout"
    if ok and name == FIRST_PATCH:
        run.archive.append({"event": "kept", "run": run.rel, "patch": name, "sha256": c.sha256_file(dest)})
    return step_ending("", CAPTURED if ok else None, done.returncode)


def rule_step(ctx: Context, task: Task, run: Any, agent: str, base: str, docker_args: list[str], key: str, tally: c.Tally,
              stop: float, started: Any, session: Callable[[str, str], str] | None = None) -> str:
    """Arm K3's step, after the first patch is kept: the same session resumed with ``RULE_STEP``, as a repair resumes it, on the
    run's one tally and stop, so its turns and spend count against the run's allowance. ``started`` is the run's watchdog (or,
    for a unit called alone, the run's start, whose deadline it makes one for): the step does not start once it has fired, and
    the session gets no prompt after it and is ended by it. Its ending (``step_ending``) reads its own result only, never one an
    earlier session left in the tally, and that result must be the first session's: any other session's is "error". From a
    success, the tree is then kept as ``RULE_PATCH``, by the same watchdog. ``session``: the host executor's, as a repair's
    (``reviews_until_clean``): the same host session resumed, which returns how it ended ("" when cleanly, its exit code read
    by ``host_ending``, so a clean one is a clean exit); by default the same session continued in the container."""
    dog = started if isinstance(started, Watchdog) else Watchdog(deadline_of(started))
    if not dog.may_start("rule session"):
        return "timeout"
    before, codes = tally.result, []
    first = str((before or {}).get("session_id") or "")                # the session the step continues
    if not first:                    # a first session whose own result names none: there is no session to continue, so no
        return "error"               # prompt goes to any, resumed or fresh, and the run ends "error" with its baseline kept
    if session is None:
        stopped = run_session(ctx, run, agent, scout_again(docker_args), key, tally, RULE_STEP,
                              c.NDJSON["tagged_stream"].format(tag="rule"), seconds=dog.left(), stop_usd=stop, codes=codes, deadline=dog)
    else:
        stopped = session(RULE_STEP, c.NDJSON["tagged_stream"].format(tag="rule"))
        codes.append(None if stopped else 0)
    late = dog.end("rule session")
    own = tally.result if tally.result is not before else None      # ``Tally.feed`` keeps each result as a new dict
    ended = "timeout" if late and not stopped else step_ending(stopped, own, codes[-1] if codes else None, session=first)
    return kept(ctx, task, agent, base, run, RULE_PATCH, dog) if ended == "finished" else ended


def rule_led_start(ctx: Context, task: Task, run: Any, agent: str, base: str, docker_args: list[str], key: str, tally: c.Tally,
                   stop: float, started: Any, first: tuple[str | None, int | None],
                   session: Callable[[str, str], str] | None = None) -> dict[str, str]:
    """Arm K3 from the end of its first session to its first review (``K3_STEPS``): the first session's ending, from its stop and
    exit (``first``) and its result, the only one in the tally yet; the first patch kept; the rule step, its session and then its
    patch kept. Each runs only when the one before it ended "finished"; the first that did not is where the run ends, with its
    status ("ended") and its step ("at"). Both are "" when every step finished. ``started``: the run's watchdog (or its start).
    ``session``: the host executor's (``rule_step``). Whether the run has its baseline, and whether it ended late, is not
    decided here: the archive and the watchdog say, once, at the run's end (``ended_as``)."""
    dog = started if isinstance(started, Watchdog) else Watchdog(deadline_of(started))
    steps = (lambda: step_ending(first[0], tally.result, first[1]),
             lambda: kept(ctx, task, agent, base, run, FIRST_PATCH, dog),
             lambda: rule_step(ctx, task, run, agent, base, docker_args, key, tally, stop, dog, session))
    for at, step in zip(K3_STEPS, steps):
        ended = step()
        if ended != "finished":
            return {"ended": ended, "at": at, "instruction": RULE_DIGEST}
    return {"ended": "", "at": "", "instruction": RULE_DIGEST}


def ended_as(ctx: Context, run: Any, status: str, *, dry: bool, dog: Watchdog | None = None) -> str:
    """The status a run ends with, decided once: as it stopped, except a K3 run. A K3 run with no Claude Code baseline
    (``has_baseline``) ends ``NO_FIRST_PATCH`` whatever stopped it: a preflight check, a cap, an error or an exception. One whose
    watchdog fired, or any of whose steps ended at or after the deadline (``Watchdog.late``), ends "timeout" whatever it returned,
    and is scored as K2's timed-out runs are. A refusal at its first prompt's check keeps its own status, and a dry run is never
    scored. So does any ending with no verdict that is made again (``wecommon.UNSCORED``): a host run that met the subscription's
    usage limit, or whose session G6 refused, is "limited" or "refused" wherever it stopped, as K2's is. On the host every ending
    the run writes is decided by this rule (``RunFolder.ending_rule``). First of all, an ending already on record stands, for
    every arm: the ending decided first (``RunFolder.end``) is never written over, and this rule then gives it back, as
    ``wecommon.ending`` reads it, so what the run wrote early and what its end decides are one answer."""
    if dry:
        return status
    facts = c.run_facts(ctx.archive, run.rel)
    if facts is not None and (facts.folder / c.ENDING).exists():
        recorded = (facts.ending or {}).get("status")
        if recorded:
            return str(recorded)
    if status in c.UNSCORED or ctx.variant != ROUND3 or not rule_led(ctx.arm):
        return status
    if not has_baseline(ctx.archive, run.rel):
        return NO_FIRST_PATCH
    return "timeout" if dog is not None and dog.late() else status


def plain_files(top: Path) -> list[Path]:
    """Every regular file under ``top``, found without following a link (``linked``): a folder that is a link is never entered,
    and a file that is one is never read."""
    found, todo = [], [top]
    while todo:
        with os.scandir(todo.pop()) as entries:
            for entry in entries:
                st = entry.stat(follow_symlinks=False)
                if linked(st):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    todo.append(Path(entry.path))
                elif stat.S_ISREG(st.st_mode):
                    found.append(Path(entry.path))
    return sorted(found)


def copy_in(ctx: Context, agent: str, src: str, run: Any, name: str, *, skip: frozenset[str] = frozenset()) -> None:
    """One of a K3 run's late copies out of the container (``rounds``, Claude Code's projects, G6's home), the only way they reach
    the archive. It goes into a folder of the run's that did not exist, so ``docker cp`` never nests it. Then each regular file
    found there without following a link (``plain_files``) is masked, as every copy out of a container is, and written into the
    run's ``name`` folder as a new file (never over one), and never as a name in ``skip``. A destination whose folder resolves
    into the baseline (``BASELINE``), or anywhere outside the run's ``name`` folder, is refused (RuntimeError), so the baseline is
    never written, masked or replaced; and the baseline's readers check its bytes whatever happens here (``baseline_bytes``)."""
    late = run.path / f"{name}-late-{uuid.uuid4().hex[:8]}"
    (run.path / name).mkdir(exist_ok=True)
    fence, own = (run.path / BASELINE).resolve(), (run.path / name).resolve()
    try:
        if not copy_out(ctx.docker, agent, src, late) or not plain(late, folder=True):
            return
        for path in plain_files(late):
            rel = path.relative_to(late)
            dest = run.path / name / rel
            if rel.as_posix().casefold() in skip or os.path.lexists(dest):
                continue
            text = path.read_bytes().decode("utf-8", errors="surrogateescape")
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = ctx.archive.redact(text).encode("utf-8", errors="surrogateescape")
            where = dest.parent.resolve()
            if where == fence or fence in where.parents or not (where == own or own in where.parents):
                raise RuntimeError(f"a late copy of {name} refused {rel.as_posix()}: its folder resolves to {where}, "
                                   f"{'inside the baseline' if where == fence or fence in where.parents else 'outside ' + name}")
            with open(dest, "xb") as stream:
                stream.write(data)
    finally:
        if plain(late, folder=True):
            shutil.rmtree(late, ignore_errors=True)
        elif os.path.lexists(late):          # a link where the copy was: the link goes, never what it points at
            try:
                os.unlink(late)
            except OSError:
                os.rmdir(late)


def merge_rounds(ctx: Context, agent: str, run: Any) -> None:
    """Arm K3's late copy of the container's ``rounds``, which the loop's later rounds are in (``copy_in``), never as one of the
    two captures (``FIRST_PATCH``, ``RULE_PATCH``), which only ``kept`` writes."""
    copy_in(ctx, agent, f"{WE}/rounds", run, "rounds", skip=frozenset({FIRST_PATCH.casefold(), RULE_PATCH.casefold()}))


#: The one admission rule, beside the one source of a run's facts (``wecommon.run_facts``), which ``wecommon.verdict`` and
#: ``wecommon.holds`` ask too: ``loop.one``, ``loop.rescore``, ``check``, ``compare.saved`` and the export ask it by the run's name.
unscored_why, scored_run = c.unscored_why, c.scored_run


def ending_of(archive: c.Archive, rel: str) -> Mapping[str, Any] | None:
    """Run ``rel``'s ending as the archive holds it (``wecommon.run_facts``); None for a name no start in the index has."""
    return c.ending(archive, rel)


# --- arms V3 and V4: arm K's loop with the reviewer's test written before any patch (PLAN.md, 2026-11-04) ---------------
BUG_TEST_ARMS = ("V3", "V4")
BUG_NOTE = """A reviewer from another team read the issue and the repository, but not your patch, and wrote this test of the bug
before your fix existed. They say:

{rule}

{why}

Their test, whose cases {failing} fail on your code:

```python
{test}
```

They can be wrong, as anyone can. Check their expectation against the issue and the repository before you act on it."""


def bug_led(arm: str) -> bool:
    """Arms V3 and V4: arm K's loop, with its reviewer's test written once from the issue and the parent before the agent starts."""
    return arm in BUG_TEST_ARMS


def bug_instruction(arm: str) -> Any:
    """What arm ``arm``'s reviewer is asked: replay 3's instruction for V3, replay 4's for V4, word for word."""
    import replay3
    import replay4
    return {"V3": replay3.rule(), "V4": replay4.SYMPTOM}[arm]


def ask_bug_test(ctx: Context, task: Task, title: str, text: str, ins: Any) -> dict[str, Any]:
    """The bug's reviewer, asked exactly as replay 3 and 4 ask theirs (``final_review._step`` with no patch and nothing run):
    its own container from the bug's image, the issue and the parent, and Codex's exit read, so a failed reviewer is no
    answer. Nothing reaches the agent's container."""
    import final_review as fr

    started = time.monotonic()
    found, _, _ = fr._step(ctx, task, Path("no-patch"), title, text, ins.text, ins.at, ins.file, candidate=False, run=False)
    return {**found, "seconds": round(time.monotonic() - started, 1)}


def parent_check(ctx: Context, task: Task, found: Mapping[str, Any], ins: Any) -> ProbeRun:
    """The bug's test on the parent, as replay 3 runs it (``final_review.container`` and ``parent_run``), in a container of its
    own whose every exec is checked (``replay3.Checked``): the agent's container never holds the test before its first check,
    and its tree is never touched by this run."""
    import final_review as fr
    import replay3

    checked_ctx = copy.copy(ctx)
    checked_ctx.docker = replay3.Checked(ctx.docker)
    name = f"g6we-review-{uuid.uuid4().hex[:10]}"
    try:
        fr.container(checked_ctx, task, name)
        return fr.parent_run(checked_ctx, task, name, found["test"], ins.at, ins.file)
    finally:
        ctx.docker("rm", "-f", name, timeout=300)


def bug_test(ctx: Context, task: Task, run: Any, title: str, text: str) -> dict[str, Any]:
    """Arm V's first step, before the agent starts: the bug's test asked (``ask_bug_test``) and run on the parent
    (``parent_check``), both recorded in the run."""
    import replay3

    ins = bug_instruction(ctx.arm)
    started = time.monotonic()
    found = ask_bug_test(ctx, task, title, text, ins)
    at_parent = None
    if found.get("test") and not replay3.no_answer(found):
        try:
            at_parent = parent_check(ctx, task, found, ins)
            run.write_json("bug_test_at_parent.json", {"exit": at_parent.exit, "ran": at_parent.ran, "results": at_parent.results,
                                                       "tail": at_parent.tail})
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            found = {**found, "error": f"{type(exc).__name__}: {exc}"[:400]}
    run.write_json("bug_test.json", {k: found.get(k) for k in ("verdict", "rule", "why", "test", "error")})
    return {**found, "at_parent": at_parent, "instruction": ins.digest, "arm": ctx.arm,
            "seconds": round(time.monotonic() - started, 1)}


def reviews_with_bug_test(ctx: Context, task: Task, run: Any, agent: str, base: str, docker_args: list[str], key: str,
                          tally: c.Tally, stop: float, started: float, bug: Mapping[str, Any]) -> dict[str, Any]:
    """Arms V3 and V4 after the agent's session (PLAN.md, 2026-11-04): the bug's test, written before the patch, run on the tree
    after each session; while a case fails, the failing cases and the test go back to the same session, as arm K's reviewer's
    do, up to ``MAX_REPAIRS`` repairs. A fresh copy of the test is put in place before each check. A test that fails nothing on
    the parent says nothing of any patch, so it sends nothing back, as replays 3 and 4 read it. Each status it returns is decided
    by the one rule, with the patch's size as it stands then (``loop_status``)."""
    import replay3

    ins = bug_instruction(ctx.arm)
    summary = {"verdict": bug.get("verdict"), "instruction": bug.get("instruction"), "seconds": bug.get("seconds")}
    if not bug.get("test") or bug.get("error") or replay3.no_answer(bug):
        failed = bool(replay3.no_answer(bug)) or bool(bug.get("error"))
        return {"status": loop_status("inconclusive" if failed else LOOP_CLEAN, patch_bytes(ctx, task, agent, base)), "rounds": 0,
                "failing": [], "critic": summary,
                "reason": "the bug's reviewer failed" if failed else "no test of the bug was written"}
    at_parent = bug.get("at_parent")
    if at_parent is None or at_parent.exit is None:
        return {"status": loop_status("inconclusive", patch_bytes(ctx, task, agent, base)), "rounds": 0, "failing": [],
                "critic": summary, "reason": "the bug's test gave no result on the parent, as a container failure gives"}
    if not replay3.failed_cases(at_parent):
        return {"status": loop_status(LOOP_CLEAN, patch_bytes(ctx, task, agent, base)), "rounds": 0, "failing": [],
                "critic": summary, "reason": "the bug's test reproduces nothing on the parent"}
    checked_ctx = copy.copy(ctx)                       # every exec of a check is checked: a docker failure is no clean review
    checked_ctx.docker = replay3.Checked(ctx.docker)
    patch_at = f"{WE}/we-tree.patch"

    def tree() -> str:
        """The tree as the patch the run records (``diff_script``, new files included), by its digest."""
        return out(ctx.docker.sh(agent, f"{diff_script(task.repo, base, patch_at)}; sha256sum {patch_at}; rm -f {patch_at}",
                                 workdir=task.repo, timeout=300))

    rounds, failing, reason = 0, [], ""
    for n in range(1, MAX_REPAIRS + 2):
        rounds = n
        keep_first_patch(ctx, task, agent, base, n)
        try:     # a check copies the test's whole folder, so the folder goes first and the test is written alone (review v2)
            checked_ctx.docker.sh(agent, f"rm -rf {ins.at}", user="root", timeout=60)
            write_in(ctx.docker, agent, f"{ins.at}/{ins.file}", bug["test"] + "\n", user="root")
            got = run_set(checked_ctx, task, agent, ins.at)
        except RuntimeError as exc:
            return {"status": loop_status("inconclusive", patch_bytes(ctx, task, agent, base)), "rounds": rounds, "failing": [],
                    "critic": summary, "reason": f"a check of the bug's test failed to run: {exc}"[:300]}
        run.write_json(f"repair_check_{n}_bug.json", {"exit": got.exit, "ran": got.ran, "results": got.results, "tail": got.tail})
        if got.exit is None:
            return {"status": loop_status("inconclusive", patch_bytes(ctx, task, agent, base)), "rounds": rounds, "failing": [],
                    "critic": summary, "reason": "a check of the bug's test gave no exit, as a container failure gives"}
        failing = [case.split(":", 1)[1] for case in replay3.failed_cases(got)]
        if not failing:
            reason = "the bug's test holds" if got.usable else "the bug's test would not run on the patch"
            break
        if n > MAX_REPAIRS:
            reason = "the repairs ran out with a defect open"
            break
        before = tree()
        note = BUG_NOTE.format(rule=bug.get("rule", ""), why=bug.get("why", ""), test=bug["test"],
                               failing=", ".join(failing[:MAX_SHOWN]))
        run_session(ctx, run, agent, scout_again(docker_args), key, tally, note + REVIEW_ASK, c.NDJSON["numbered_stream"].format(tag="repair", n=n),
                    seconds=max(60.0, c.WALL_CLOCK_S - (time.monotonic() - started)), stop_usd=stop)
        if tree() == before:
            reason = "the agent left the code as it was"
            break
    return {"status": loop_status("inconclusive" if failing else LOOP_CLEAN, patch_bytes(ctx, task, agent, base)), "rounds": rounds,
            "reason": reason, "failing": failing[:40], "critic": summary}


def _run_task(ctx: Context, task: Task, *, dry: bool, cap: float, stop: float) -> dict[str, Any]:
    round3 = ctx.variant == ROUND3
    looped = round3 and re.fullmatch(r"[LM]\d+", ctx.arm) is not None
    model = ctx.model if round3 else c.MODEL
    critic_led = round3 and re.fullmatch(r"K\d+", ctx.arm) is not None
    test_led = round3 and bug_led(ctx.arm)
    no_g6 = round3 and (critic_led or test_led or ctx.arm in ("A", "S", "T", "R", *CONVERT_ARMS))   # Claude Code alone: no G6 server, skill or command
    host = getattr(ctx, "executor", "container") == "host"      # Claude Code on the host, on the subscription's sign-in: no key is read (PLAN.md, 2026-11-04)
    key = DRY_KEY if dry else "" if host else ctx.key_reader()
    ctx.archive.redact = c.Redactor(key, *ctx.archive.redact.secrets)
    task_image, _ = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    tag = uuid.uuid4().hex[:10]
    common = {"g6_commit": ctx.g6.get("commit"), "g6_image": ctx.g6.get("image"), "task_image": task_image,
              "model": model}
    if host:
        common["executor"] = "host"
    if round3:    # what the run may spend of the API's money, from its start: nothing for a host run (``wecommon.api_need``)
        common.update(arm=ctx.arm, repeat=ctx.repeat, cap_usd=cap, stop_usd=c.api_need({**common, "stop_usd": stop}),
                      label="development", **({"rerun_why": ctx.rerun_why} if ctx.rerun_why else {}))
    run = ctx.archive.start("dryrun" if dry else "run", task.id, **common)
    record: dict[str, Any] = {**common, "task": task.id, "run": run.rel, "agent_started": False, "cost_usd": 0.0}
    agent, net, px, status = "", "", "", "error"
    workspace: Path | None = None
    dog: Watchdog | None = None          # arm K3's watchdog, from the run's deadline on
    # every ending the run writes before its end (a host session's, the host's clock, a failure) is the one its end decides
    run.ending_rule = lambda status: ended_as(ctx, run, status, dry=dry, dog=dog)

    def finish(status: str) -> dict[str, Any]:
        """The run's end, on every way out of this function, decided once (``ended_as``): for a K3 run from the archive and the
        watchdog's records, with how it stopped kept beside it."""
        if dog is not None:
            dog.stop()
            record.update(step_ends=list(dog.ends), watchdog_fired=dog.fired.is_set())
        ended = ended_as(ctx, run, status, dry=dry, dog=dog)
        if ended != status:
            record["stopped_as"] = status
        return run.finish(record, ended)

    try:
        title, text = ctx.issue_reader(task)
        if round3 and gets_thread(ctx.arm):
            text += read_thread(task)
        record["image_id"] = out(ctx.docker("image", "inspect", task_image, "--format", "{{.Id}}"))
        round2 = ctx.variant == ROUND2
        if not host:     # on the host no key enters the container, which has no network: no key check and no proxy
            if round2:
                net, px = start_net(ctx, tag)      # round 2's key check asks G6's model, through the run's proxy
            record["key_check"] = key_check(ctx, task, task_image, key, tag, net=net if round2 else "",
                                            px=px if round2 else "")
            run.write_json("key_check.json", record["key_check"])
            if round2:
                record["key_check_g6"] = g6_model_cost([{"completion": {"activities": [
                    {**x, "activity": "model call"} for x in record["key_check"].get("g6_model_calls") or []]}}])
                record["cost_usd"] = record["key_check_g6"]["usd"]
            if not record["key_check"]["ok"]:
                status = "key_check_failed"
                return finish(status)
            if not round2:
                net, px = start_net(ctx, tag)
        agent = f"g6we-run-{task.id}-{tag}"
        checked(ctx.docker("run", "-d", "--name", agent, "--label", "g6we=1", "--network", "none" if host else net, *LIMITS, *SECURITY,
                           "-u", USER, "-e", f"HOME={HOME}", "-w", task.repo, task_image, "tail", "-f", "/dev/null",
                           timeout=600), "agent container")
        try:
            state = json.loads(out(ctx.docker.sh(agent, TREE_STATE, workdir=task.repo)).splitlines()[-1])
        except (ValueError, IndexError):
            state = {}
        expected = (ctx.tree_reader or read_tree)(task)
        record["tree_state"] = {**state, "upstream_tree": expected}
        if (state.get("head") != task.parent or state.get("tree") != expected or state.get("dirty") != 0
                or state.get("commits") != 1):
            status = "tree_mismatch"     # git must track exactly what upstream tracks at the base (PLAN.md section 9)
            return finish(status)
        base = str(state["head"])
        record["base_commit"] = base
        if not host:     # the host's version is read from its stream
            record["claude_version"] = out(ctx.docker("exec", "-u", USER, agent, CLAUDE, "--version", timeout=120))
        if no_g6:    # round 3, arm A: no g6 setup, and no g6 command on the agent's path
            checked(ctx.docker("exec", "-u", "root", agent, "rm", "-f", G6_BIN, timeout=60), "remove the g6 command")
            record["g6_setup_exit"] = None
        else:
            doctor = ctx.docker("exec", "-u", USER, "-e", f"HOME={HOME}", agent, "g6", "doctor", timeout=600)
            run.write("g6_doctor.txt", out(doctor) + "\n" + err(doctor))
            setup = ctx.docker("exec", "-u", USER, "-e", f"HOME={HOME}", "-w", HOME, agent,
                               "g6", "setup", "--client", "claude", "--allow", "patch_review.review", timeout=600)
            run.write("g6_setup.txt", out(setup) + "\n" + err(setup))
            record["g6_setup_exit"] = setup.returncode
            if setup.returncode != 0:
                raise RuntimeError(f"g6 setup exited {setup.returncode}")
        if round3:   # what Claude Code will find of G6: nothing in arm A; G6's server and skill in B and C
            try:
                record["arm_state"] = json.loads(out(ctx.docker.sh(agent, ARM_STATE)).splitlines()[-1])
            except (ValueError, IndexError):
                record["arm_state"] = {"mcp_servers": [], "skills": [], "error": "nothing readable"}
            servers = record["arm_state"].get("mcp_servers") or []
            if (servers != []) if no_g6 else ("g6" not in servers):
                status = "arm_mismatch"
                return finish(status)
        if round2:
            write_key_file(ctx.docker, agent, key)     # G6's model and its key, where g6.core.keys reads them
        write_in(ctx.docker, agent, f"{WE}/round_hook.sh", hook_script(task.repo, base), mode="755")
        write_in(ctx.docker, agent, f"{HOME}/.claude/settings.json", hook_settings())
        if looped and has_hook(ctx.arm):
            write_in(ctx.docker, agent, f"{WE}/stop_hook.sh", stop_hook(task.repo), user="root", mode="755")
        if dry:
            record["hook_selftest"] = hook_selftest(ctx, agent, task)
        text_in = prompt(task, title, text, ctx.arm if round3 else "")
        if round3 and ctx.arm in CONVERT_ARMS:
            start, found = convert_start(ctx.archive.root, task.id, ctx.repeat)
            text_in += convert_note(ctx.arm, found)
            if ctx.arm == "X1":
                checked(ctx.docker("cp", str(start), f"{agent}:/tmp/we-start.patch", timeout=300), "the starting patch")
                checked(ctx.docker.sh(agent, "git apply --binary --whitespace=nowarn /tmp/we-start.patch", workdir=task.repo, timeout=120),
                        "apply the starting patch")
                ctx.docker.sh(agent, "rm -f /tmp/we-start.patch", user="root", timeout=60)
        run.write("prompt.txt", text_in)
        if round3 and rule_led(ctx.arm):    # K3's first session is arm A's, byte for byte, by digest (PLAN.md, 2026-11-05)
            record["first_prompt_sha256"], why = first_prompt_check(task, title, text, text_in)
            if why:
                record["why"] = why
                status = "refused"
                return finish(status)
        args = claude_args(model, cap)
        way_out = DEAD_PROXY if dry else f"http://{px}:{PROXY_PORT}"
        env_names = ["ANTHROPIC_API_KEY", f"HTTPS_PROXY={way_out}", f"HTTP_PROXY={way_out}",
                     "NO_PROXY=localhost,127.0.0.1", f"HOME={HOME}", "DISABLE_AUTOUPDATER=1",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "DISABLE_TELEMETRY=1", "DISABLE_ERROR_REPORTING=1"]
        docker_args = ["exec", "-i", "-u", USER, "-w", task.repo, *[a for e in env_names for a in ("-e", e)], agent,
                       *args]
        if host:   # the parent's tree comes out to the host's workspace; each session's tree goes back in (``box_mcp.mirror``)
            workspace = host_workspace(ctx, tag)
            record["workspace_files"] = workspace_from(ctx, task, agent, workspace)
            mcp_file, settings_file = host_files(ctx, run, task, agent, workspace)
            run.write_json("command.json", {"host": [HOST_CLAUDE, *host_args(mcp_file, settings_file, model)],
                                            "caps": {"turns": c.MAX_TURNS, "wall_clock_s": c.WALL_CLOCK_S},
                                            "key": "none: the host's Claude Code is signed in to the subscription"})
        else:
            run.write_json("command.json", {"docker": docker_args, "caps": {"usd": cap, "stop_usd": stop,
                                            "turns": c.MAX_TURNS, "wall_clock_s": c.WALL_CLOCK_S},
                                            "key": "passed by name; its value only in the docker client's environment"})
        at_base: dict[str, ProbeRun] = {}
        if looped and not dry:   # the hook arrives after the scout, so it cannot stop the scout
            record["agent_started"] = True
            record["scout"] = run_scout(ctx, task, run, agent, base, docker_args, key, title, text)
            at_base = record["scout"].pop("at_base")
            if qualified(ctx.arm):   # one allowance for the whole run: the scouts' spend comes out of the fixer's
                stop = max(1.0, stop - float(record["scout"]["usd"]))
        if looped and has_hook(ctx.arm):
            write_in(ctx.docker, agent, f"{HOME}/.claude/settings.json", hook_settings(stop=True))
        started = time.monotonic()    # one clock for the run: arm V's bug test comes out of the same allowance (review v2)
        deadline = deadline_of(started)     # the run's one deadline, which every step after it reads
        if critic_led and rule_led(ctx.arm) and not dry:     # K3: one watchdog for the run, its timer running from now
            dog = Watchdog(deadline)
            dog.start()
        bug: dict[str, Any] = {}
        if test_led and not dry:      # the bug's test, from the issue and the parent, before the agent starts (PLAN.md, 2026-11-04)
            bug = bug_test(ctx, task, run, title, text)
            record["bug_test"] = {k: bug.get(k) for k in ("verdict", "seconds", "error", "instruction")}
        tally = c.Tally(count_reported=round3)

        def turn(note: str, name: str, resume: str = "", watchdog: Watchdog | None = None) -> str:
            """A host session, then its tree into the container before anything reads it there; how it ended (``host_session``,
            which writes the run's ending when it is not clean, before this mirror). Before the mirror, a call of the box the
            session ended before its pull is settled (``box_mcp.settle``), so what its command changed is in the workspace the
            mirror sends, and what was settled is recorded (``box_settled``); a box state that cannot be read fails the run. No
            session starts once the run's clock is spent: that is the run's "timeout", its ending at once. ``watchdog``: K3's, for
            a resumed session, as the container's resumed sessions are given it (``run_session``)."""
            left = c.WALL_CLOCK_S - (time.monotonic() - started)
            if left <= 0:
                run.end("timeout")
                return "timeout"
            ended = host_session(ctx, run, workspace, mcp_file, settings_file, note, name, resume=resume, seconds=left, tally=tally,
                                 stop_usd=c.api_need(record), deadline=watchdog)
            facts = host_facts(run.path / name, model)      # recorded whatever happens; every session's models and refusals
            if facts["init"] and "host_tools" not in record:
                record.update(host_tools=facts["init"]["tools"], host_mcp_servers=facts["init"]["servers"])
            record["models_seen"] = sorted({*record.get("models_seen", []), *facts["models"]})
            record["model_refusals"] = [*record.get("model_refusals", []), *facts["refusals"]]
            record["model_switched"] = bool(record.get("model_switched")) or facts["switched"]
            exited = json.loads((run.path / f"{Path(name).stem}_exit.json").read_text(encoding="utf-8"))
            record["ended_by_g6"] = bool(record.get("ended_by_g6")) or exited["ended_by_g6"]     # any session G6 had to end
            if ended == "refused" and "why" not in record:
                record["why"] = (run.path / f"{Path(name).stem}_refused.txt").read_text(encoding="utf-8")
            if ended == "limited" and "limit" not in record:
                record["limit"] = {"stream": name, "resets": (run.path / f"{Path(name).stem}_limit.txt").read_text(encoding="utf-8")}
            settled = box_mcp.settle(workspace, ctx.docker, state=box_state_of(workspace), container=agent, repo=task.repo,
                                     user=USER, timeout=BOX_TIMEOUT_S)
            if settled is not None:
                record["box_settled"] = [*record.get("box_settled", []), {**settled, "session": name}]
            box_mcp.mirror(workspace, ctx.docker, container=agent, repo=task.repo, user=USER, timeout=BOX_TIMEOUT_S)
            return ended

        first_session = ""       # the host's first session, by the id its own result gave: fixed once, the only one resumed

        def resume(note: str, name: str) -> str:
            """The first host session resumed, for K3's rule step and arm K's repairs alike, by ``first_session`` and never by the
            tally's latest id, which any session's stream sets. Its ending is decided by the one rule every session of the loop ends
            by (``step_ending``), from its own stop (``host_ending``'s, which read its exit), its own result and the first session's
            id; a session that ended cleanly but whose own result is not the first session's success ends its run with that ending,
            written at once, as a session that did not end cleanly does. "" when it ended cleanly."""
            if not first_session:        # the first session's own result named no session: none is resumed, and no prompt
                run.end("error", why="the first session's own result named no session, so there is none to resume")
                return "error"           # goes to a fresh one in its place
            before = tally.result
            ended = turn(note, name, first_session, dog)
            own = tally.result if tally.result is not before else None      # ``Tally.feed`` keeps each result as a new dict
            got = step_ending(ended, own, None if ended else 0, session=first_session)
            if got != "finished" and not ended:
                run.end(got, why=f"the resumed session's own result was not a success of the first session {first_session!r}: "
                                 f"it names session {(own or {}).get('session_id')!r}")
            return "" if got == "finished" else got

        # the host session resumed, for K3's rule step and arm K's repairs alike; in the container they continue it there
        resumed = resume if host else None
        if host:
            record["agent_started"] = True
            ended = turn(text_in, c.NDJSON["stream"])
            first_session = str((tally.result or {}).get("session_id") or "")
            first_code = None if ended else 0     # its exit is read by host_ending: a clean session is a clean exit
            if dog is not None:
                dog.end("first session")
        else:
            proc = ctx.docker.popen(*docker_args, env=ctx.client_env(key))
            record["agent_started"] = True
            errors: list[bytes] = []
            drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b"") if proc.stderr else None,
                                     daemon=True)
            drain.start()
            stream = open(run.fresh_path(c.NDJSON["stream"]), "w", encoding="utf-8", newline="\n")
            try:
                proc.stdin.write(text_in.encode("utf-8"))
                proc.stdin.close()
                ended = read_stream(iter(proc.stdout.readline, b""), tally, deadline=deadline,
                                    write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()),
                                    stop_usd=stop)
            finally:
                stream.close()
                if proc.poll() is None:
                    ctx.docker.sh(agent, KILL_AGENT, user="root", timeout=60)
                    proc.kill()
                first_code = proc.wait(timeout=60)
            if dog is not None:
                dog.end("first session")
            drain.join(timeout=30)
            run.write("stderr.txt", b"".join(errors).decode("utf-8", errors="replace"))
            if critic_led and not dry:   # a K arm's first session ends by the one rule each session of its loop ends by
                got = step_ending(ended, tally.result, first_code)
                ended = "" if got == "finished" else got
        if looped and holds_loop(ctx.arm) and not dry and not ended:
            record["repairs"] = repair_loop(ctx, task, run, agent, docker_args, key, tally, stop, started, at_base,
                                            lambda: run_critic(ctx, task, run, agent, base, title, text), base=base)
        if ctx.arm == "R" and not dry and not ended:
            keep_first_patch(ctx, task, agent, base)
            record["repairs"] = review_once(ctx, task, run, agent, docker_args, key, tally, stop, started,
                                            lambda: run_critic(ctx, task, run, agent, base, title, text), base=base)
        stepped, steps_ctx = dog is not None, ctx
        if stepped:      # K3: the first session, its patch kept, the rule step; each only from a recognised success (step_ending)
            steps_ctx = copy.copy(ctx)       # every docker call of K3's steps bounded by the watchdog, which their reviews see too
            steps_ctx.docker, steps_ctx.watchdog = dog.docker(ctx.docker), dog
            record["rule_step"] = rule_led_start(steps_ctx, task, run, agent, base, docker_args, key, tally, stop, dog,
                                                 (ended, first_code), session=resumed)
            ended = record["rule_step"]["ended"]
            record["first_patch_sha256"] = kept_digest(ctx.archive, run.rel)    # whether it is a baseline: has_baseline, at the end
        if critic_led and not dry and not ended:
            record["repairs"] = reviews_until_clean(steps_ctx, task, run, agent, base, docker_args, key, tally, stop, started,
                                                    lambda n: run_critic(steps_ctx, task, run, agent, base, title, text, n),
                                                    finding=finding_rule(ctx.arm),
                                                    session=resumed,
                                                    tree=(lambda: hashlib.sha256(box_mcp.pack(workspace)).hexdigest()) if host else None,
                                                    after_step=stepped, **({"deadline": dog} if stepped else {}))
            ended = record["repairs"].get("ended") or ended      # a repair that did not end cleanly, on either executor: the run
                                                                 # ends as it did
        if dog is not None:
            dog.stop()                       # the steps are over: what the run does after them is no step
        if test_led and not dry and not ended:
            record["repairs"] = reviews_with_bug_test(ctx, task, run, agent, base, docker_args, key, tally, stop, started, bug)
        result = tally.result or {}
        record.update(status_detail=status_of(ended, tally),
                      agent_usd=tally.cost_usd,
                      cost_usd=round(tally.cost_usd + float(record.get("cost_usd") or 0.0)
                                     + float((record.get("scout") or {}).get("usd") or 0.0), 6),
                      stream_usd=round(tally.stream_usd, 6),
                      result_usd=tally.result_usd, reported_usd=result.get("total_cost_usd"),
                      model_calls=tally.model_calls, num_turns=result.get("num_turns"), usage=tally.usage,
                      result_subtype=result.get("subtype"), session_id=tally.session_id,
                      seconds=round(time.monotonic() - started, 1))
        if host:   # the subscription's sessions: what the stream says they would cost, never counted as the API's spend
            record.update(executor="host", subscription_usd_estimate=record["agent_usd"], cost_usd=0.0)
            run.end(record["status_detail"])     # the run's ending, decided: before the final mirror and the patch's capture
        run.write("final_report.txt", str(result.get("result") or ""))
        if host:   # the agent's tree is the workspace, with every command's changes (``box_mcp.call``, ``turn``): into the
            # container again, over whatever a review's test left there; and the box's record of its calls kept in the run's folder
            box_mcp.mirror(workspace, ctx.docker, container=agent, repo=task.repo, user=USER, timeout=BOX_TIMEOUT_S)
            calls = box_mcp.read_state(box_state_of(workspace))
            if calls is not None:
                run.write_json("box_calls.json", calls)
        ctx.docker.sh(agent, diff_script(task.repo, base, f"{WE}/final.patch"), workdir=task.repo, timeout=600)
        final = run.fresh_path("final.patch")
        if not copy_out(ctx.docker, agent, f"{WE}/final.patch", final):
            final.write_bytes(b"")
        record["final_patch_bytes"] = final.stat().st_size
        record["final_patch_sha256"] = c.sha256_file(final)
        if round2:   # G6's key file never leaves the container
            checked(ctx.docker("exec", "-u", USER, agent, "rm", "-f", KEY_FILE, timeout=60), "remove G6's key file")
        for src, name in ((f"{WE}/rounds", "rounds"), (f"{HOME}/.claude/projects", "claude_projects"),
                          (f"{HOME}/.g6", "g6_home")):
            if round3 and rule_led(ctx.arm):     # K3: every late copy goes in by copy_in, which never follows a link
                if name == "rounds":
                    merge_rounds(ctx, agent, run)
                else:
                    copy_in(ctx, agent, src, run, name)
                continue
            copy_out(ctx.docker, agent, src, run.path / name)
            redact_tree(run.path / name, ctx.archive.redact)
        saved: dict[str, Any] = {}
        if round2:
            for path in sorted((run.path / "g6_home" / "reviews").glob("*.json")):
                try:
                    saved[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
            record["g6_model"] = {**g6_model_cost(saved.values()), "reviews_saved": len(saved)}
            record["cost_usd"] = round(record["cost_usd"] + record["g6_model"]["usd"], 6)
        objects = []
        for line in c.read_stream(run.path / c.NDJSON["stream"]):
            try:
                objects.append(json.loads(line))
            except ValueError:
                continue
        if host:
            record["claude_version"] = next((str(o["claude_code_version"]) for o in objects
                                             if o.get("type") == "system" and o.get("claude_code_version")), None)
        rounds = extract_rounds(objects, archived_resolver(run.path))
        for one in rounds:      # round 2: what G6's own model proposed in this round's review
            if one.get("job_id") in saved:
                one["proposer"] = observe.proposer_summary(saved[one["job_id"]])
                one["g6_model"] = g6_model_cost([saved[one["job_id"]]])
        run.write_json("rounds.json", rounds)
        record["rounds"] = len(rounds)
        record["outcomes"] = [r["outcome"] for r in rounds]
        try:
            record["observed"] = observe.observe_run(run.path)     # what the run shows about W1 to W5
        except (OSError, ValueError) as exc:
            record["observed"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        record["egress"] = {"network": "none"} if host else egress(ctx, px, run.fresh_path(c.NDJSON["egress"]))
        status = record["status_detail"]
    except Exception as exc:  # noqa: BLE001 - every failure is recorded and the containers are removed
        record["error"] = ctx.archive.redact(f"{type(exc).__name__}: {exc}")[:2000]
        status = "error"
        if dog is not None:              # K3: a step that failed is a step that ended, and when it ended is recorded
            dog.end("error")
        if host:     # the run's ending if none was decided, else a failure beside it, which never makes it more scoreable
            run.failed(record["error"], agent_started=bool(record["agent_started"]))
    finally:
        if dog is not None:
            dog.stop()
        for name in (agent, px):
            if name:
                ctx.docker("rm", "-f", name, timeout=300)
        if net:
            ctx.docker("network", "rm", net, timeout=120)
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
            for left in workspace.parent.glob(f"{workspace.name}.box*"):   # the box's state, its pull folder, any write left
                if left.is_dir() and not left.is_symlink():
                    shutil.rmtree(left, ignore_errors=True)
                else:
                    left.unlink(missing_ok=True)
    return finish(status)


# --- validation, the independent check and replays ------------------------------------------------------------------------
def _oracle_run(ctx: Context, task: Task, *, patch: Path | None = None, fix: bool = False, tests: bool = True,
                extra: Mapping[str, Path] | None = None, modules: Sequence[str] | None = None,
                folder: c.RunFolder | None = None, label: str = "", amended: bool = False) -> dict[str, Any]:
    """In a fresh container from the oracle image, with no network: the code (the parent's, the maintainers' fix or
    a patch), the maintainers' test files when ``tests``, the bug's amendment when ``amended``, extra files, then
    pytest on ``modules``."""
    _, oracle = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    name = f"g6we-check-{uuid.uuid4().hex[:10]}"
    body: dict[str, Any] = {"label": label}
    try:
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", "-w", task.repo, oracle, "tail", "-f", "/dev/null", timeout=600),
                "check container")
        if fix:
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /oracle/fix.patch", workdir=task.repo),
                    "the maintainers' fix")
        if patch is not None and patch.stat().st_size:
            checked(ctx.docker("cp", str(patch), f"{name}:/tmp/we.patch", timeout=300), "patch copy")
            applied = ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo)
            body["patch_applied"] = applied.returncode == 0
            if applied.returncode != 0:
                body["apply_error"] = err(applied)[-600:]
                return body
        if tests:
            files = out(ctx.docker.sh(name, "cat /oracle/fix.files"))
            pairs = [x for st, p in test_changes(files) for x in (st, p)]
            checked(ctx.docker.sh(name, APPLY_TESTS, task.repo, *pairs, workdir=task.repo), "the maintainers' tests")
        if amended:
            checked(ctx.docker("cp", str(amendment(task.id)), f"{name}:/tmp/we-amend.patch", timeout=300), "amendment copy")
            checked(ctx.docker.sh(name, "git apply --whitespace=nowarn /tmp/we-amend.patch", workdir=task.repo),
                    "the amendment")
        for dest, src in (extra or {}).items():
            checked(ctx.docker("cp", str(src), f"{name}:{task.repo}/{dest}", timeout=300), f"copy {dest}")
        ran = ctx.docker.sh(name, pytest_script(modules or task.test_modules), workdir=task.repo, timeout=2400)
        body["pytest_exit"] = out(ran).rsplit("exit=", 1)[-1] if "exit=" in out(ran) else None
        if folder is not None:
            copy_out(ctx.docker, name, "/tmp/we-pytest.txt", folder.path / f"{label}-pytest.txt")
        with tempfile.TemporaryDirectory(prefix="g6we-") as tmp:
            junit = Path(tmp) / "junit.xml"
            body["results"] = parse_junit(junit.read_text(encoding="utf-8")) if copy_out(
                ctx.docker, name, JUNIT_IN, junit) else {}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    return body


def validate(ctx: Context, task: Task) -> dict[str, Any]:
    run = ctx.archive.start("validate", task.id, g6_commit=ctx.g6.get("commit"))
    _, oracle = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    oracle_id = checked(ctx.docker("image", "inspect", oracle, "--format", "{{.Id}}"), "the oracle image")
    base = _oracle_run(ctx, task, folder=run, label="base")
    fixed = _oracle_run(ctx, task, fix=True, folder=run, label="fixed")
    f2p, p2p = fail_to_pass(base.get("results") or {}, fixed.get("results") or {})
    record = {"oracle_image_id": oracle_id, "base": base, "fixed": fixed, "fail_to_pass": f2p, "pass_to_pass": p2p,
              "modules": task.test_modules,
              "ok": bool(f2p), "why": "" if f2p else "no test fails on the parent's code and passes with the fix"}
    return run.finish(record, "finished")


def amend(ctx: Context, task: Task) -> dict[str, Any]:
    """The amended tests' own controls (no model, PLAN.md 2026-10-31): the lists an amended check scores against, and
    every maintainers' test the fix no longer passes. A diff that leaves no test telling the parent from the fix is
    refused, here and by every check that would use it."""
    diff = amendment(task.id)
    if diff is None:
        raise SystemExit(f"{task.id} has no amendment")
    v = latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("validate the task first")
    run = ctx.archive.start("amend", task.id, g6_commit=ctx.g6.get("commit"))
    base = _oracle_run(ctx, task, folder=run, label="base", amended=True)
    fixed = _oracle_run(ctx, task, fix=True, folder=run, label="fixed", amended=True)
    before, results = base.get("results") or {}, fixed.get("results") or {}
    f2p, p2p = fail_to_pass(before, results)
    seen = sum(before.get(k) in ("failed", "error") for k in f2p)   # absent from a report is not a failure anyone saw
    lost = sorted(k for k in [*v["record"]["fail_to_pass"], *v["record"]["pass_to_pass"]] if results.get(k) != "passed")
    return run.finish({"sha256": c.sha256_file(diff), "fail_to_pass": f2p, "pass_to_pass": p2p, "parent_failed": seen,
                       "lost": lost, "ok": bool(seen), "base": base, "fixed": fixed,
                       "why": "" if seen else "no amended test was seen failing on the parent's code and passing with the fix"},
                      "finished" if seen else "refused")


def amend_control(archive: c.Archive, task_id: str) -> dict[str, Any] | None:
    """The control for the diff as it stands: the newest control to end, if it passed and it ran these bytes. So neither
    a newer refusal nor a changed diff can be scored on an older list."""
    ended = [r for r in archive.rows() if r.get("event") == "ended" and str(r.get("run", "")).startswith(f"amend/{task_id}/")]
    if not ended or max(ended, key=lambda r: str(r["run"])).get("status") != "finished":
        return None
    found, diff = latest(archive, "amend", task_id), amendment(task_id)
    return found if found and diff and found["record"].get("sha256") == c.sha256_file(diff) else None


def scored_patches(rounds: Path) -> list[Path]:
    """Arm K3's patches before its final one, in the order its run made them, found and named by one rule: the files at the top
    of ``rounds``, where its captures and its late copy put them (``kept``, ``merge_rounds``), whose names are exactly
    ``round-<n>.patch``, by n, or exactly ``RULE_PATCH``, just after round 1's. Each is one label, its stem, so the rule step's
    patch is scored once; a name in another case or folder is none of them."""
    names = sorted(p.name for p in rounds.iterdir() if p.is_file()) if rounds.is_dir() else []
    order = [(int(m[1]), 0, name) for name in names if (m := ROUND_PATCH.fullmatch(name))]
    order += [(1, 1, name) for name in names if name == RULE_PATCH]
    return [rounds / name for *_, name in sorted(order)]


def patches_to_score(folder: Path, arm: str, baseline: Path | None = None) -> list[tuple[str, Path]]:
    """The one rule ``check`` selects and labels a run's patches by, before its final one. Arm K3's: its baseline labelled
    "round-1", as ``check`` read and checked it (``baseline``, a private copy of what ``baseline_bytes`` gave; the kept file when
    none is given), then its ``rounds`` (``scored_patches``) past round 1. Every other arm's exactly as before K3 (a0ad0a8): each
    ``round-*.patch`` anywhere under ``rounds``, by its number, a ``RULE_PATCH`` among them or not. Each other label is the
    patch's stem."""
    rounds = folder / "rounds"
    if rule_led(arm):
        later = [(path.stem, path) for path in scored_patches(rounds) if path.name != FIRST_PATCH]
        return [(Path(FIRST_PATCH).stem, baseline if baseline is not None else baseline_of(folder)), *later]
    return [(path.stem, path) for path in sorted(rounds.glob("**/round-*.patch"), key=lambda p: int(p.stem.split("-")[1]))]


def check(ctx: Context, task: Task, run_rel: str | None = None, *, amended: bool = False) -> dict[str, Any]:
    v = amend_control(ctx.archive, task.id) if amended else latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("this diff has no passing control: run amend" if amended else "validate the task first")
    # the run's name exactly as the command line gives it, or as the index names the bug's latest run: never a path made of it
    rel = str(run_rel) if run_rel else str((latest(ctx.archive, "run", task.id) or {}).get("run") or "")
    if not rel:
        raise SystemExit("no run to check")
    facts = c.run_facts(ctx.archive, rel)    # every fact of the run, by its exact name; its folder is opened by that name only
    why = unscored_why(ctx.archive, rel)     # the one admission rule, as loop.one asks it, from the archive alone
    if not why and facts is not None and facts.task != task.id:
        why = f"its start names task {facts.task!r}, not {task.id!r}"
    if why or facts is None:
        raise SystemExit(f"{rel}: {why}")
    src, arm, rel = facts.folder, facts.arm, facts.name
    first = None
    if rule_led(arm):                # K3: its baseline read once and its bytes checked; only those bytes are scored
        first = baseline_bytes(ctx.archive, rel)
        if first is None:            # changed since the rule above read it: never scored on round-1
            raise SystemExit(f"{rel} is arm {arm}'s: its baseline changed since it was kept, so it is never scored on round-1 "
                             "(baseline_bytes)")
    f2p, p2p = v["record"]["fail_to_pass"], v["record"]["pass_to_pass"]
    folder = ctx.archive.start("recheck" if amended else "check", task.id, checked_run=rel)
    private = folder.write(FIRST_PATCH, first) if first is not None and not amended else None
    patches = [] if amended else patches_to_score(src, arm, private)
    rows = []
    for label, patch in [*patches, ("final", src / "final.patch")]:
        body = _oracle_run(ctx, task, patch=patch, folder=folder, label=label, amended=amended)
        if body.get("patch_applied", True) and not body.get("results") and str(body.get("pytest_exit")).strip() != "124":
            raise RuntimeError(f"no test report for {label}")   # a check that did not happen is not a failed patch
        rows.append({"patch": label, "bytes": patch.stat().st_size if patch.exists() else 0,
                     "patch_applied": body.get("patch_applied", True), **verdict(body.get("results") or {}, f2p, p2p)})
    return folder.finish({"checks": rows, "fail_to_pass": f2p, "pass_to_pass_count": len(p2p), "amended": amended},
                         "finished")


def selfcheck(ctx: Context, task: Task) -> dict[str, Any]:
    """The independent check's own control (no model): the maintainers' fix, given as if it were the agent's patch,
    must pass it, and an empty patch must fail it. A check that cannot tell them apart says nothing about a run."""
    v = latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("validate the task first")
    f2p, p2p = v["record"]["fail_to_pass"], v["record"]["pass_to_pass"]
    folder = ctx.archive.start("selfcheck", task.id, g6_commit=ctx.g6.get("commit"))
    _, oracle = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    gold = folder.fresh_path("gold.patch")
    got = ctx.docker("run", "--rm", "--network", "none", oracle, "cat", "/oracle/fix.patch", timeout=600)
    checked(got, "the fix's patch")
    gold.write_bytes(got.stdout or b"")
    empty = folder.fresh_path("empty.patch")
    empty.write_bytes(b"")
    rows = {}
    for label, patch in (("gold", gold), ("empty", empty)):
        body = _oracle_run(ctx, task, patch=patch, folder=folder, label=label)
        rows[label] = {"patch_applied": body.get("patch_applied", True), **verdict(body.get("results") or {}, f2p, p2p)}
    ok = bool(rows["gold"]["passes"]) and not rows["empty"]["passes"]
    return folder.finish({"ok": ok, **{k: {"passes": r["passes"], "failing": len(r["failing"]),
                                           "patch_applied": r["patch_applied"]} for k, r in rows.items()}}, "finished")


G6_REPLAY = """import json, sys
from g6.harness.patch_review import review_patch
args = json.loads(sys.stdin.read())
got = review_patch(args.pop("repo"), **args)
a = got.get("assessment") or {}
print(json.dumps({"ok": got.get("ok"), "error": got.get("error"), "outcome": a.get("outcome"),
                  "reasons": [(r.get("stage"), r.get("code")) for r in a.get("reasons") or []],
                  "requirements": [(r.get("id"), r.get("status")) for r in a.get("requirements") or []],
                  "report": a.get("report")}))
"""


def g6replay(ctx: Context, task: Task, code: str, args_file: Path) -> dict[str, Any]:
    """For judging a finding: G6's own review, confined as in the run, of the maintainers' fix (``code`` "fix") or of
    a patch, with the review arguments in ``args_file`` (JSON: task, requirements and the like; the repository is the
    task's). No model is called."""
    folder = ctx.archive.start("g6replay", task.id, code=code, args=args_file.name)
    _, oracle = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    name = f"g6we-replay-{uuid.uuid4().hex[:10]}"
    body: dict[str, Any] = {"code": code, "args": json.loads(args_file.read_text(encoding="utf-8"))}
    try:
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", "-w", task.repo, oracle, "tail", "-f", "/dev/null", timeout=600),
                "replay container")
        if code == "fix":
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /oracle/fix.patch", workdir=task.repo),
                    "the maintainers' fix")
        else:
            checked(ctx.docker("cp", code, f"{name}:/tmp/we.patch", timeout=300), "patch copy")
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo),
                    "the patch")
        args = {**body["args"], "repo": task.repo}
        done = ctx.docker("exec", "-i", "-u", USER, "-e", f"HOME={HOME}", "-w", task.repo, name, G6PY, "-I", "-",
                          input=(G6_REPLAY.replace("sys.stdin.read()", json.dumps(json.dumps(args)))).encode("utf-8"),
                          timeout=2400)
        try:
            body["review"] = json.loads(out(done).splitlines()[-1])
        except (ValueError, IndexError):
            body["review"] = {"error": f"no readable reply (exit {done.returncode}): {err(done)[-400:]}"}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    return folder.finish(body, "finished")


def replay(ctx: Context, task: Task, code: str, test: Path) -> dict[str, Any]:
    folder = ctx.archive.start("replay", task.id, code=code, test=test.name)
    patch = None if code == "fix" else Path(code)
    body = _oracle_run(ctx, task, patch=patch, fix=code == "fix", tests=False, extra={test.name: test},
                       modules=[test.name], folder=folder, label="replay")
    return folder.finish(body, "finished")


MODELCHECK = """import json, time
from g6.core.llm import provider_from_env
llm = provider_from_env()
started = time.monotonic()
try:
    text, error = llm.complete("You answer with one word.", "Reply with the word OK.", max_tokens=256), ""
except Exception as exc:
    text, error = "", type(exc).__name__ + ": " + " ".join(str(exc).split())[:300]
inner = getattr(llm, "_inner", None) or getattr(llm, "inner", None) or llm
print(json.dumps({"caller": type(inner).__name__, "model": getattr(inner, "model", None), "reply": text[:40],
                  "error": error, "seconds": round(time.monotonic() - started, 2)}))
"""


def modelcheck(ctx: Context, task: Task) -> dict[str, Any]:
    """Round 2, before its first paid run (PLAN.md section 9): one call to G6's model from a container of the task's
    image, with G6's key file in place, through the run's proxy; its cost is counted at an upper bound."""
    key = ctx.key_reader()
    ctx.archive.redact = c.Redactor(key, *ctx.archive.redact.secrets)
    task_image, _ = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    folder = ctx.archive.start("modelcheck", task.id, g6_commit=ctx.g6.get("commit"))
    tag = uuid.uuid4().hex[:10]
    name, net, px, body = f"g6we-modelcheck-{tag}", "", "", {}
    try:
        net, px = start_net(ctx, tag)
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", net, *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", task_image, "tail", "-f", "/dev/null", timeout=600), "model check")
        write_key_file(ctx.docker, name, key)
        done = ctx.docker("exec", "-i", "-u", USER, "-e", f"HOME={HOME}", "-e", f"HTTPS_PROXY=http://{px}:{PROXY_PORT}",
                          "-e", f"HTTP_PROXY=http://{px}:{PROXY_PORT}", "-e", "NO_PROXY=localhost,127.0.0.1", name, G6PY,
                          "-I", "-", input=MODELCHECK.encode("utf-8"), timeout=600)
        try:
            body = json.loads(out(done).splitlines()[-1])
        except (ValueError, IndexError):
            body = {"error": f"no readable reply (exit {done.returncode}): {err(done)[-300:]}"}
        body["egress"] = egress(ctx, px, folder.fresh_path(c.NDJSON["egress"]))
    finally:
        for one in (name, px):
            if one:
                ctx.docker("rm", "-f", one, timeout=300)
        if net:
            ctx.docker("network", "rm", net, timeout=120)
    bound = g6_model_cost([{"completion": {"activities": [{"activity": "model call", "input_chars": 50,
                                                            "input_tokens": None, "output_tokens": None}]}}])
    body["ok"] = bool(body.get("reply")) and not body.get("error")
    return folder.finish({**body, "cost_usd": bound["usd"], "cost_source": "upper bound"}, "finished")


def freecheck(ctx: Context, task: Task) -> dict[str, Any]:
    """Before the first paid run (PLAN.md section 9): one confined review with the README's install and a linked
    ``.venv``, with a placeholder in the key's place (no key is read, no model is called)."""
    task_image, _ = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    folder = ctx.archive.start("freecheck", task.id, g6_commit=ctx.g6.get("commit"))
    found = key_check(ctx, task, task_image, "placeholder-not-a-key-0000", uuid.uuid4().hex[:10])
    return folder.finish(found, "finished")


# --- building ------------------------------------------------------------------------------------------------------------
def git_commit(ref: str) -> str:
    got = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True)
    if got.returncode != 0 or len(got.stdout.strip()) != 40:
        raise SystemExit(f"{ref!r} names no commit")
    return got.stdout.strip()


def contains_required(commit: str) -> bool:
    return all(subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", req, commit],
                              capture_output=True).returncode == 0 for req in c.REQUIRED_G6_COMMITS)


def default_claude() -> Path:
    return Path.home() / "g6-validation" / "V7-assets" / "dl_claude" / "claude-2.1.309-linux-x64"


def build(docker: Docker, context: Path, tag: str, folder: c.RunFolder, name: str, labels: Sequence[str] = ()) -> str:
    args = ["build", "-t", tag, *[a for lab in labels for a in ("--label", lab)], str(context)]
    done = docker(*args, timeout=7200)
    folder.write(f"{name}-build.txt", out(done) + "\n" + err(done))
    checked(done, f"docker build {tag}")
    return out(docker("image", "inspect", tag, "--format", "{{.Id}}"))


def build_base(archive: c.Archive, ref: str, claude: Path, docker: Docker | None = None,
               variant: str = "") -> dict[str, Any]:
    docker = docker or Docker()
    commit = git_commit(ref)
    if c.sha256_file(claude) != CLAUDE_SHA256:
        raise SystemExit("Claude Code's binary is not the registered build")
    folder = archive.start("build", "base", ref=ref, g6_commit=commit, variant=variant)
    ctx_dir = archive.root / "build" / (f"base-{commit[:12]}" + (f"-{variant}" if variant else ""))
    ctx_dir.mkdir(parents=True, exist_ok=True)
    tar = ctx_dir / "g6src.tar"
    made = subprocess.run(["git", "-C", str(REPO_ROOT), "-c", "core.autocrlf=false", "archive", "--format=tar", "-o",
                           str(tar), commit], capture_output=True)
    if made.returncode != 0:
        raise SystemExit("git archive failed")
    if not (ctx_dir / "claude").is_file():
        shutil.copyfile(claude, ctx_dir / "claude")
    if variant == ROUND2:
        shutil.copyfile(ROUND2_PATCH, ctx_dir / "round2_g6.py")
    (ctx_dir / "Dockerfile").write_bytes(base_dockerfile(variant).encode("utf-8"))
    tag = base_tag(commit, variant)
    image = build(docker, ctx_dir, tag, folder, "base", [f"we.g6.commit={commit}"])
    rec = {"ref": ref, "g6_commit": commit, "image": tag, "image_id": image, "contains_required": contains_required(commit),
           "variant": variant, "round2_patch_sha256": c.sha256_file(ROUND2_PATCH) if variant == ROUND2 else None,
           "source_tar_sha256": c.sha256_file(tar), "claude_sha256": CLAUDE_SHA256, "from": FROM_IMAGE,
           "from_id": out(docker("image", "inspect", FROM_IMAGE, "--format", "{{.Id}}"))}
    return folder.finish(rec, "finished")


def build_images(archive: c.Archive, task: Task, g6_commit: str, docker: Docker | None = None,
                 variant: str = "") -> dict[str, Any]:
    docker = docker or Docker()
    folder = archive.start("build", task.id, g6_commit=g6_commit, variant=variant)
    task_image, oracle = image_names(task, g6_commit, variant)
    rec: dict[str, Any] = {"task_image": task_image, "oracle_image": oracle}
    for tag, text, name in ((task_image, task_dockerfile(task, base_tag(g6_commit, variant)), "task"),
                            (oracle, oracle_dockerfile(task, task_image), "oracle")):
        d = archive.root / "build" / (f"{name}-{task.id}-{g6_commit[:12]}" + (f"-{variant}" if variant else ""))
        d.mkdir(parents=True, exist_ok=True)
        (d / "Dockerfile").write_bytes(text.encode("utf-8"))
        docker("image", "rm", "-f", tag, timeout=600)     # a failed build must never leave an older image in its place
        rec[f"{name}_id"] = build(docker, d, tag, folder, name)
    return folder.finish(rec, "finished")


def g6_info(g6: str) -> dict[str, Any]:
    commit = git_commit(g6)
    return {"commit": commit, "image": f"g6we-base:{commit[:12]}", "contains_required": contains_required(commit)}


def read_key() -> str:
    """The key, through ``g6.core.keys`` from this checkout's ``src`` (``python -I`` puts nothing else on the path)."""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from g6.core import keys

    return keys.require(keys.Key("ANTHROPIC_API_KEY", "the worked examples' Claude Code runs (the owner's key)",
                                 paid=True))


def read_tree(task: Task) -> str:
    """Upstream's tree for the task's base commit, from GitHub's API (read-only)."""
    got = subprocess.run(["gh", "api", f"repos/{task.full_name}/git/commits/{task.parent}", "--jq", ".tree.sha"],
                         capture_output=True, timeout=120)
    return got.stdout.decode("utf-8", errors="replace").strip() if got.returncode == 0 else ""


def read_thread(task: Task) -> str:
    """The issue's comments from before the fix's pull request was opened, as ``thread_text`` keeps them."""
    def api(path: str, *flags: str) -> Any:
        got = subprocess.run(["gh", "api", *flags, path], capture_output=True, timeout=120)
        if got.returncode != 0:
            raise RuntimeError(f"{path} could not be read: {got.stderr.decode(errors='replace')[-200:]}")
        return json.loads(got.stdout.decode("utf-8"))

    return thread_text(api(f"repos/{task.full_name}/issues/{task.issue}/comments?per_page=100"),
                       str(api(f"repos/{task.full_name}/pulls/{task.pr}")["created_at"]))


def read_issue(task: Task) -> tuple[str, str]:
    got = subprocess.run(["gh", "api", f"repos/{task.full_name}/issues/{task.issue}"], capture_output=True, timeout=120)
    if got.returncode != 0:
        raise RuntimeError(f"the issue could not be read: {got.stderr.decode(errors='replace')[-200:]}")
    data = json.loads(got.stdout.decode("utf-8"))
    return str(data.get("title") or ""), str(data.get("body") or "")


def brief(kind: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """A short view of a record, for the terminal: test results are counted, not listed."""
    if kind == "validate":
        return {"ok": record.get("ok"), "fail_to_pass": record.get("fail_to_pass"),
                "pass_to_pass": len(record.get("pass_to_pass") or []),
                **{f"{side}_{k}": (record.get(side) or {}).get(k) for side in ("base", "fixed")
                   for k in ("pytest_exit", "patch_applied")},
                **{f"{side}_results": len((record.get(side) or {}).get("results") or {}) for side in ("base", "fixed")}}
    if kind == "check":
        return {"checks": [{k: row.get(k) for k in ("patch", "bytes", "patch_applied", "passes",
                                                     "fail_to_pass_failing")} | {"failing": len(row.get("failing") or [])}
                           for row in record.get("checks") or []]}
    if kind == "amend":     # counts only: on a fresh or held-out bug the names stay unread (PLAN.md, 2026-10-31)
        return {"ok": record.get("ok"), "why": record.get("why"), "sha256": record.get("sha256"),
                "parent_failed": record.get("parent_failed"),
                **{k: len(record.get(k) or []) for k in ("fail_to_pass", "pass_to_pass", "lost")}}
    return {k: v for k, v in record.items() if k not in ("results", "base", "fixed", "usage")}


def progress(archive: c.Archive, task: str) -> dict[str, Any]:
    """An unfinished (or finished) run's progress, read from its stream: model calls, cost so far, rounds and their
    outcomes, and the last tool the agent used. Reads only the archive."""
    runs = sorted(p for p in (archive.root / "run" / task).glob("*") if p.is_dir()) if (archive.root / "run" / task).is_dir() else []
    if not runs:
        return {"run": None}
    folder = runs[-1]
    tally, objects, last_tool = c.Tally(), [], ""
    stream = folder / c.NDJSON["stream"]
    for line in (c.read_stream(stream) if stream.is_file() else []):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        tally.feed(obj)
        objects.append(obj)
        for block in ((obj.get("message") or {}).get("content") or []) if obj.get("type") == "assistant" else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                last_tool = str(block.get("name"))
    rounds = extract_rounds(objects, archived_resolver(folder))
    return {"run": folder.name, "finished": (folder / "run.json").is_file(), "model_calls": tally.model_calls,
            "cost_usd": round(tally.cost_usd, 4), "rounds": [r["outcome"] for r in rounds], "last_tool": last_tool}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("base", "images", "validate", "selfcheck", "freecheck", "modelcheck", "dryrun",
                                        "run", "check", "amend", "replay", "g6replay", "show", "progress", "rounds",
                                        "schedule"))
    ap.add_argument("--args", type=Path, help="g6replay: a JSON file of the review's arguments")
    ap.add_argument("--amended", action="store_true", help="check: score on the amended tests (PLAN.md, 2026-10-31)")
    ap.add_argument("--archive", type=Path, default=None)
    ap.add_argument("--ref", default="origin/claude/launch")
    ap.add_argument("--claude", type=Path, default=None)
    ap.add_argument("--task")
    ap.add_argument("--g6", help="G6's commit (a base image must exist for it)")
    ap.add_argument("--run", dest="run_rel")
    ap.add_argument("--code")
    ap.add_argument("--test", type=Path)
    ap.add_argument("--kind", default="validate", help="show: validate, run, check, freecheck, replay or build")
    ap.add_argument("--round", type=int, choices=(1, 2, 3), default=1,
                    help="2: round 2, G6 with its own model (its archive is round2/ inside the worked examples' one); "
                         "3: round 3, formal methods (round3/, with its own $100 stop and ledger)")
    ap.add_argument("--arm", type=str,
                    help="round 3: A (no G6), B (G6 as shipped), C (G6 and the addendum), D (C and its fallback)")
    ap.add_argument("--model", choices=R3_MODELS, help="round 3: the agent's model")
    ap.add_argument("--repeat", type=int, default=1, help="round 3: 2 for Sonnet's second arm A run of a bug")
    ap.add_argument("--rerun-why", default="", help="round 3: make again a run that ended in error, saying why")
    ap.add_argument("--executor", choices=("container", "host"), default="container",
                    help="round 3: host runs Claude Code on the host, on the subscription's sign-in, and each of its commands "
                         "in the bug's container, which has no network (box_mcp.py)")
    args = ap.parse_args(argv)
    first = c.Archive(c.archive_root(args.archive))
    variant = {2: ROUND2, 3: ROUND3}.get(args.round, "")
    archive = c.Archive(first.root / {ROUND2: "round2", ROUND3: "round3"}[variant]) if variant else first
    prior = c.spent(first.rows()) if variant == ROUND2 else 0.0     # one $100 stop for rounds 1 and 2
    if args.command == "schedule":      # round 3's runs in order, each with how it ended, and the money
        rows = archive.rows()
        done = r3_done(rows, archive.root)
        sched = r3_schedule(load_tasks())
        print(json.dumps({"spent_usd": c.spent(rows), "stop_usd": R3_CAP_USD,
                          "runs": [{"model": m, "task": t, "arm": a, "repeat": r, "ended": done.get((m, t, a, r))}
                                   for m, t, a, r in sched]}, indent=1))
        return 0
    if args.command == "base":
        print(json.dumps(build_base(archive, args.ref, args.claude or default_claude(), variant=variant), indent=1))
        return 0
    if args.command == "progress":
        print(json.dumps(progress(archive, args.task), indent=1))
        return 0
    if args.command == "rounds":
        # the rounds read again from a finished run's stream and its saved replies, into a new file of the run
        folder = archive.root / args.run_rel
        objects = [json.loads(line) for line in c.read_stream(folder / c.NDJSON["stream"]) if line.strip().startswith("{")]
        rounds = extract_rounds(objects, archived_resolver(folder))
        c.RunFolder(archive, folder, args.run_rel).write_json("rounds_resolved.json", rounds)
        print(json.dumps([{k: r.get(k) for k in ("round", "outcome", "error", "saved_to_file", "polls")}
                          for r in rounds], indent=1))
        return 0
    if args.command == "show":
        found = latest(archive, args.kind, args.task)
        print(json.dumps({"run": found and found["run"], **brief(args.kind, found["record"] if found else {})},
                         indent=1, default=str)[:6000])
        return 0
    task = task_by_id(args.task)
    g6 = g6_info(args.g6)
    if args.command == "images":
        result = build_images(archive, task, g6["commit"], variant=variant)
    else:
        if variant == ROUND3 and args.command in ("dryrun", "run") and not (args.arm and args.model):
            raise SystemExit("round 3's dryrun and run need --arm and --model")
        if variant == ROUND3 and args.command == "modelcheck":
            raise SystemExit("round 3 gives G6 no model")
        ctx = Context(archive=archive, docker=Docker(), key_reader=read_key, issue_reader=read_issue, g6=g6,
                      variant=variant, prior_spent=prior, arm=args.arm or "", model=args.model or c.MODEL,
                      repeat=args.repeat, rerun_why=args.rerun_why, executor=args.executor)
        result = {"validate": lambda: validate(ctx, task), "selfcheck": lambda: selfcheck(ctx, task),
                  "freecheck": lambda: freecheck(ctx, task), "modelcheck": lambda: modelcheck(ctx, task),
                  "dryrun": lambda: run_task(ctx, task, dry=True),
                  "run": lambda: run_task(ctx, task), "amend": lambda: amend(ctx, task),
                  "check": lambda: check(ctx, task, args.run_rel, amended=args.amended),
                  "replay": lambda: replay(ctx, task, args.code, args.test),
                  "g6replay": lambda: g6replay(ctx, task, args.code, args.args)}[args.command]()
    print(json.dumps(brief(args.command, result), indent=1, default=str)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
