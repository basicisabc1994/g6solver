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
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import observe  # noqa: E402
import selection  # noqa: E402
import wecommon as c  # noqa: E402

REPO_ROOT = HERE.parents[1]
TASKS = HERE / "tasks.json"
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
PROBE_RUN = f".venv/bin/python -m pytest {WE}/probes -q --tb=short -p no:cacheprovider --rootdir={WE}/probes"
PROBE_COLLECT = PROBE_RUN.replace(" -q --tb=short", " --collect-only -q")
HIDDEN, HIDDEN_TREE, HELD_COPY = f"{WE}/hidden", f"{WE}/hidden_tree.json", "/tmp/we-held"
MAX_STOPS, SCOUT_CAP_USD, MAX_THREAD_CHARS, MAX_CROSS, MAX_REPAIRS, MIN_RANDOM = 4, 2.0, 12000, 200, 3, 300
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
    for task in load_tasks(path):
        if task.id == task_id:
            return task
    raise SystemExit(f"no task {task_id!r} in {path.name}")


def hidden_at(i: int) -> tuple[str, str]:
    """Where hidden scout ``i`` works: its probes' folder and its tree."""
    place = f"{HIDDEN}{i + 1}" if i else HIDDEN
    return place, f"{place}_tree.json"


def hidden_scouts(arm: str) -> int:
    return 2 if int(arm[1:]) >= 6 else 1


def scout_prompt(arm: str, *, hidden: bool = False, at: int = 0) -> str:
    """The scout's prompt for arm ``L<n>`` or ``M<n>``: ``SCOUT_V2`` from generation 2 and ``SCOUT_V3`` from 3, then
    ``SCOUT_V5`` alone from 5 and ``SCOUT_V6`` from 6, before its critique. ``hidden``: hidden scout ``at``'s, whose work
    the agent never sees."""
    n = int(arm[1:])
    extra = SCOUT_V6 if n >= 6 else SCOUT_V5 if n == 5 else (SCOUT_V2 if n >= 2 else "") + (SCOUT_V3 if n >= 3 else "")
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
    the probes, which the runner holds. A failure the agent has already seen is not sent again: it has decided."""

    def __init__(self, check: Callable[[], tuple[set[str], str]], ask: Callable[[str, int], Any]) -> None:
        self.check, self.ask = check, ask
        self.round, self.failing, self.text, self.sent = 0, set(), "", set()

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> Any:
        from g6.cegis import SearchResult

        if self.round:
            if self.failing <= self.sent:
                return SearchResult(None, "nothing new fails", False, self.round)
            self.sent |= self.failing
            self.ask(COUNTEREXAMPLES.format(text=self.text), self.round)
        self.round += 1
        return SearchResult({0: f"round {self.round}"}, "agent", False, self.round)

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        self.failing, self.text = self.check()
        return [[{"ok": not self.failing} for _ in inputs]]


def has_critic(arm: str) -> bool:
    return int(arm[1:]) == 4


def critic_prompt(number: int, title: str, text: str) -> str:
    return CRITIC_PROMPT.format(number=number, title=title, text=text)


def scout_gate(raw: str, *, exit_code: int, collected: int, broken: int = 0, randoms: int | None = None) -> list[str]:
    """What is wrong with the scout's probes, by the runner's own count: G6's ``InputDomain`` says how many combinations
    the declared dimensions make, up to ``MAX_CROSS``. Empty when the fix may start."""
    from g6.cegis import InputDomain

    wrong = []
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
    """Whether a run's issue carries its discussion: arm L, and arm T, the comparison for it (PLAN.md, 2026-10-31)."""
    return re.fullmatch(r"T|[LM]\d+", arm) is not None


def thread_text(comments: Iterable[Mapping[str, Any]], until: str) -> str:
    """The discussion a person would read, cut where the fix's pull request opens: ISO times compare as text."""
    kept = [f"{str(n.get('author_association') or 'NONE').lower()} on {str(n['created_at'])[:10]}: {n.get('body') or ''}"
            for n in comments if n["created_at"] < until and (n.get("user") or {}).get("type") != "Bot"]
    body = "\n\n".join(kept)[:MAX_THREAD_CHARS]
    return f"\n\nThe issue's discussion so far:\n\n{body}" if body else ""


def prompt(task: Task, title: str, text: str, arm: str = "") -> str:
    """The run's prompt: round 1's (and round 3's arm B), round 3's arm A without the G6 instruction, or arm C with the
    addendum."""
    texts = {"": PROMPT, "B": PROMPT, "A": PROMPT_A, "C": PROMPT_C, "D": PROMPT_D, "E": PROMPT_D, "T": PROMPT_A}
    if re.fullmatch(r"[GH]\d+", arm):
        harness = (HARNESS / f"G{arm[1:]}.txt").read_text(encoding="utf-8").strip()
        texts[arm] = PROMPT.replace("\nIssue #{number}", "\n" + harness.replace("{", "{{").replace("}", "}}")
                                    + "\n\nIssue #{number}", 1)
    if re.fullmatch(r"[LM]\d+", arm):
        added = LOOP_ADDENDUM
        if holds_loop(arm):
            added = (HARNESS / "G1.txt").read_text(encoding="utf-8").strip() + "\n\n" + LOOP_ADDENDUM_5
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


def r3_loop_key(key: tuple[str, str, str, int]) -> bool:
    """A run of the distillation loop: Sonnet, twice. A and E on any bug, ``G<n>`` on training bugs, ``H<n>`` (the same
    text) on the held-out ones. The final comparison runs A and ``H<n>`` five times on each held-out bug."""
    model, task, arm, repeat = key
    held_out = task in R3_HELD and (arm in ("A", "T") or arm[0] in "HM")
    if model != R3_MODELS[1] or repeat not in (range(1, R3_HELD_REPEATS + 1) if held_out else (1, 2)):
        return False
    if arm in ("A", "E", "T"):
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


def r3_done(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str, int], str]:
    """The round 3 runs whose agent started, by (model, task, arm, repeat), with how each ended."""
    ended = {r["run"]: r for r in rows if r.get("event") == "ended"}
    done = {}
    for r in rows:
        end = ended.get(r.get("run"))
        if r.get("event") == "started" and r.get("kind") == "run" and r.get("arm") and end and end.get("agent_started"):
            done[(str(r.get("model")), str(r.get("task")), str(r.get("arm")), int(r.get("repeat") or 1))] = end["status"]
    return done


def r3_refusal(ctx: Context, task: Task) -> tuple[str, float, float]:
    """Round 3's own rules: the arm, model and repeat are scheduled, not run already, and in order (no Sonnet run while
    a Haiku run is left, no Fable run while a Sonnet one is); the cap; and the $100 stop. Returns (why, cap, stop)."""
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
    done = r3_done(rows)
    if key in done and not (ctx.rerun_why and done[key] == "error"):
        return f"run {key} was already made ({done[key]}); no run is made again for a better result", 0.0, 0.0
    earlier = [k for k in sched if R3_MODELS.index(k[0]) < R3_MODELS.index(ctx.model) and k not in done]
    if earlier and key not in R3_SMOKE and key not in R3_PROBE and not r3_loop_key(key):
        return f"out of order: {len(earlier)} run(s) of an earlier model are left, first {earlier[0]}", 0.0, 0.0
    spent = c.spent(rows)
    fable_left = sum(1 for k in sched if k[0] == R3_MODELS[2] and k not in done)
    cap, stop = r3_caps(ctx.model, spent=spent, fable_left=fable_left)
    ok, why = c.may_start(spent, cap=R3_CAP_USD, need=stop)
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
    with open(archive.root / "ledger.jsonl", "a", encoding="utf-8", newline="\n") as stream:
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


def status_of(stop: str, tally: c.Tally) -> str:
    if stop:
        return stop
    if tally.result is None:
        return "error"
    sub = str(tally.result.get("subtype") or "")
    if "max_turns" in sub:
        return "cap_turns"
    if "budget" in sub:
        return "cap_cost"
    return "error" if tally.result.get("is_error") else "finished"


KILL_AGENT = (f'for p in /proc/[0-9]*; do [ "$(readlink $p/exe 2>/dev/null)" = {CLAUDE} ] && '
              'kill -9 "${p#/proc/}" 2>/dev/null; done; true')


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
    variant: str = ""            # "r2": round 2, G6 with its own model; "r3": round 3, formal methods (PLAN.md section 9)
    prior_spent: float = 0.0     # what earlier rounds spent, under the same $100 stop
    arm: str = ""                # round 3: "A" (no G6), "B" (G6 as shipped) or "C" (G6 and the formal addendum)
    model: str = c.MODEL         # round 3: the agent's model
    repeat: int = 1              # round 3: which repeat of the same model, task and arm (Sonnet's arm A: 1 or 2)
    tasks: Sequence[Task] | None = None   # round 3's schedule's tasks (default: tasks.json)
    rerun_why: str = ""          # round 3: a run whose agent started and ended in "error" may be made again, saying why

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
                       "--log", "/opt/egress.jsonl"), "proxy start")
    return net, px


def egress(ctx: Context, px: str, dest: Path) -> dict[str, Any]:
    ctx.docker("cp", f"{px}:/opt/egress.jsonl", str(dest), timeout=120)
    entries = []
    if dest.is_file():
        entries = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines() if line.strip()]
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
        if not re.fullmatch(r"[A-ET]|[GHLM]\d+", str(ctx.arm)) or ctx.model not in R3_MODELS:
            why = why or f"round 3 needs an arm ({', '.join(R3_ARMS)}) and a model ({', '.join(R3_MODELS)})"
        elif dry:
            cap, stop = r3_caps(ctx.model, spent=0.0, fable_left=1)
        elif not why:
            why, cap, stop = r3_refusal(ctx, task)
    if why:
        return {"status": "refused", "why": why, "task": task.id}
    body = _run_task(ctx, task, dry=dry, cap=cap, stop=stop)
    if round3 and not dry:
        body["ledger"] = r3_ledger(ctx.archive, body)
    return body


def run_session(ctx: Context, run: Any, agent: str, command: list[str], key: str, tally: c.Tally, text_in: str, name: str,
                *, seconds: float, stop_usd: float) -> str:
    """One Claude Code session in the container, its stream saved as ``name``: how it ended, as ``read_stream`` says."""
    proc = ctx.docker.popen(*command, env=ctx.client_env(key))
    stream = open(run.fresh_path(name), "w", encoding="utf-8", newline="\n")
    try:
        proc.stdin.write(text_in.encode("utf-8"))
        proc.stdin.close()
        return read_stream(iter(proc.stdout.readline, b""), tally, deadline=time.monotonic() + seconds,
                           write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()),
                           stop_usd=stop_usd)
    finally:
        stream.close()
        if proc.poll() is None:
            ctx.docker.sh(agent, KILL_AGENT, user="root", timeout=60)
            proc.kill()
        proc.wait(timeout=60)


def probe_gate(ctx: Context, task: Task, agent: str, where: str, tree: str) -> list[str]:
    """``scout_gate`` on the probes in ``where``: pytest's exit, the cross test's cases, and failures that are not
    assertions, as the container reports them."""
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
    ended = session(args, asked, "scout_stream.jsonl")
    wrong = probe_gate(ctx, task, agent, *visible) if int(ctx.arm[1:]) >= 3 else []
    refused = list(wrong)
    if wrong:
        ended, wrong = checked_again(wrong, "scout_stream_2.jsonl", *visible)
    if has_critic(ctx.arm):   # a fresh session: it has read neither the scout's reasoning nor any patch
        restore()
        ended = session(args, critic_prompt(task.issue, title, text), "critic_stream.jsonl")
        wrong = probe_gate(ctx, task, agent, *visible)
        if wrong:
            refused += wrong
            ended, wrong = checked_again(wrong, "critic_stream_2.jsonl", *visible)
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
            session(args, second, f"{tag}_stream.jsonl")
            left = probe_gate(ctx, task, agent, where, tree_at)
            refused += left
            if left:
                _, left = checked_again(left, f"{tag}_stream_2.jsonl", where, tree_at)
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
    return {"ended": ended, "usd": round(tally.cost_usd, 6), "refused": refused, "still_wrong": wrong, "hidden": hidden,
            **{k: tree.get(k) for k in ("shapes", "open", "error")}}


def repair_loop(ctx: Context, task: Task, run: Any, agent: str, docker_args: list[str], key: str, tally: c.Tally,
                stop: float, started: float) -> dict[str, Any]:
    """G6's ``cegis`` over the agent's finished work: the runner runs both scouts' probes and sends failures back in the
    same session. The hidden set is copied in for the run and removed, so the agent can never read it."""
    from g6.cegis import InputDomain, cegis

    rounds = [0]

    def check() -> tuple[set[str], str]:
        rounds[0] += 1
        failing, said = set(), []
        for label, where in (("scout", f"{WE}/probes"),
                             *((f"held{i}", hidden_at(i)[0]) for i in range(hidden_scouts(ctx.arm)))):
            held = where != f"{WE}/probes"
            if held:
                ctx.docker.sh(agent, f"rm -rf {HELD_COPY}; [ -d {where} ] && cp -r {where} {HELD_COPY} && "
                                     f"chown -R {USER} {HELD_COPY}; true", user="root", timeout=60)
            got = out(ctx.docker.sh(agent, f"COLUMNS=300 {probes_at(HELD_COPY if held else where)} --maxfail=10 -rfE 2>&1 "
                                           "| tail -c 12000", workdir=task.repo, timeout=300))
            if held:
                ctx.docker.sh(agent, f"rm -rf {HELD_COPY}", user="root", timeout=60)
            run.write(f"repair_check_{rounds[0]}_{label}.txt", got)
            ids = failed_ids(got)
            failing |= {f"{label}:{i}" for i in ids}
            if ids:
                said.append(got)
        return failing, "\n\n".join(said)

    def ask(text: str, n: int) -> None:
        run_session(ctx, run, agent, scout_again(docker_args), key, tally, text, f"repair_stream_{n}.jsonl",
                    seconds=max(60.0, c.WALL_CLOCK_S - (time.monotonic() - started)), stop_usd=stop)

    loop = Repairs(check, ask)
    got = cegis(WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                max_iterations=MAX_REPAIRS + 1, timeout_s=float(c.WALL_CLOCK_S))
    return {"status": got.status, "rounds": loop.round, "reason": got.reason, "failing": sorted(loop.failing)[:40]}


def _run_task(ctx: Context, task: Task, *, dry: bool, cap: float, stop: float) -> dict[str, Any]:
    round3 = ctx.variant == ROUND3
    looped = round3 and re.fullmatch(r"[LM]\d+", ctx.arm) is not None
    model = ctx.model if round3 else c.MODEL
    no_g6 = round3 and ctx.arm in ("A", "T")       # Claude Code alone: no G6 server, skill or command
    key = DRY_KEY if dry else ctx.key_reader()
    ctx.archive.redact = c.Redactor(key, *ctx.archive.redact.secrets)
    task_image, _ = image_names(task, str(ctx.g6["commit"]), ctx.variant)
    tag = uuid.uuid4().hex[:10]
    common = {"g6_commit": ctx.g6.get("commit"), "g6_image": ctx.g6.get("image"), "task_image": task_image,
              "model": model}
    if round3:
        common.update(arm=ctx.arm, repeat=ctx.repeat, cap_usd=cap, stop_usd=stop, label="development",
                      **({"rerun_why": ctx.rerun_why} if ctx.rerun_why else {}))
    run = ctx.archive.start("dryrun" if dry else "run", task.id, **common)
    record: dict[str, Any] = {**common, "task": task.id, "run": run.rel, "agent_started": False, "cost_usd": 0.0}
    agent, net, px, status = "", "", "", "error"
    try:
        title, text = ctx.issue_reader(task)
        if round3 and gets_thread(ctx.arm):
            text += read_thread(task)
        record["image_id"] = out(ctx.docker("image", "inspect", task_image, "--format", "{{.Id}}"))
        round2 = ctx.variant == ROUND2
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
            return run.finish(record, status)
        if not round2:
            net, px = start_net(ctx, tag)
        agent = f"g6we-run-{task.id}-{tag}"
        checked(ctx.docker("run", "-d", "--name", agent, "--label", "g6we=1", "--network", net, *LIMITS, *SECURITY,
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
            return run.finish(record, status)
        base = str(state["head"])
        record["base_commit"] = base
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
                return run.finish(record, status)
        if round2:
            write_key_file(ctx.docker, agent, key)     # G6's model and its key, where g6.core.keys reads them
        write_in(ctx.docker, agent, f"{WE}/round_hook.sh", hook_script(task.repo, base), mode="755")
        write_in(ctx.docker, agent, f"{HOME}/.claude/settings.json", hook_settings())
        if looped and has_hook(ctx.arm):
            write_in(ctx.docker, agent, f"{WE}/stop_hook.sh", stop_hook(task.repo), user="root", mode="755")
        if dry:
            record["hook_selftest"] = hook_selftest(ctx, agent, task)
        text_in = prompt(task, title, text, ctx.arm if round3 else "")
        run.write("prompt.txt", text_in)
        args = claude_args(model, cap)
        way_out = DEAD_PROXY if dry else f"http://{px}:{PROXY_PORT}"
        env_names = ["ANTHROPIC_API_KEY", f"HTTPS_PROXY={way_out}", f"HTTP_PROXY={way_out}",
                     "NO_PROXY=localhost,127.0.0.1", f"HOME={HOME}", "DISABLE_AUTOUPDATER=1",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "DISABLE_TELEMETRY=1", "DISABLE_ERROR_REPORTING=1"]
        docker_args = ["exec", "-i", "-u", USER, "-w", task.repo, *[a for e in env_names for a in ("-e", e)], agent,
                       *args]
        run.write_json("command.json", {"docker": docker_args, "caps": {"usd": cap, "stop_usd": stop,
                                        "turns": c.MAX_TURNS, "wall_clock_s": c.WALL_CLOCK_S},
                                        "key": "passed by name; its value only in the docker client's environment"})
        if looped and not dry:   # the hook arrives after the scout, so it cannot stop the scout
            record["agent_started"] = True
            record["scout"] = run_scout(ctx, task, run, agent, base, docker_args, key, title, text)
        if looped and has_hook(ctx.arm):
            write_in(ctx.docker, agent, f"{HOME}/.claude/settings.json", hook_settings(stop=True))
        tally = c.Tally(count_reported=round3)
        started = time.monotonic()
        proc = ctx.docker.popen(*docker_args, env=ctx.client_env(key))
        record["agent_started"] = True
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b"") if proc.stderr else None,
                                 daemon=True)
        drain.start()
        stream = open(run.fresh_path("stream.jsonl"), "w", encoding="utf-8", newline="\n")
        try:
            proc.stdin.write(text_in.encode("utf-8"))
            proc.stdin.close()
            ended = read_stream(iter(proc.stdout.readline, b""), tally, deadline=started + c.WALL_CLOCK_S,
                                write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()),
                                stop_usd=stop)
        finally:
            stream.close()
            if proc.poll() is None:
                ctx.docker.sh(agent, KILL_AGENT, user="root", timeout=60)
                proc.kill()
            proc.wait(timeout=60)
        drain.join(timeout=30)
        run.write("stderr.txt", b"".join(errors).decode("utf-8", errors="replace"))
        if looped and holds_loop(ctx.arm) and not dry and not ended:
            record["repairs"] = repair_loop(ctx, task, run, agent, docker_args, key, tally, stop, started)
        result = tally.result or {}
        record.update(status_detail=status_of(ended, tally), agent_usd=tally.cost_usd,
                      cost_usd=round(tally.cost_usd + float(record.get("cost_usd") or 0.0)
                                     + float((record.get("scout") or {}).get("usd") or 0.0), 6),
                      stream_usd=round(tally.stream_usd, 6),
                      result_usd=tally.result_usd, reported_usd=result.get("total_cost_usd"),
                      model_calls=tally.model_calls, num_turns=result.get("num_turns"), usage=tally.usage,
                      result_subtype=result.get("subtype"), session_id=tally.session_id,
                      seconds=round(time.monotonic() - started, 1))
        run.write("final_report.txt", str(result.get("result") or ""))
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
        for line in (run.path / "stream.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                objects.append(json.loads(line))
            except ValueError:
                continue
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
        record["egress"] = egress(ctx, px, run.fresh_path("egress.jsonl"))
        status = record["status_detail"]
    except Exception as exc:  # noqa: BLE001 - every failure is recorded and the containers are removed
        record["error"] = ctx.archive.redact(f"{type(exc).__name__}: {exc}")[:2000]
        status = "error"
    finally:
        for name in (agent, px):
            if name:
                ctx.docker("rm", "-f", name, timeout=300)
        if net:
            ctx.docker("network", "rm", net, timeout=120)
    return run.finish(record, status)


# --- validation, the independent check and replays ------------------------------------------------------------------------
def _oracle_run(ctx: Context, task: Task, *, patch: Path | None = None, fix: bool = False, tests: bool = True,
                extra: Mapping[str, Path] | None = None, modules: Sequence[str] | None = None,
                folder: c.RunFolder | None = None, label: str = "") -> dict[str, Any]:
    """In a fresh container from the oracle image, with no network: the code (the parent's, the maintainers' fix or
    a patch), the maintainers' test files when ``tests``, extra files, then pytest on ``modules``."""
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


def check(ctx: Context, task: Task, run_rel: str | None = None) -> dict[str, Any]:
    v = latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("validate the task first")
    src = ctx.archive.root / run_rel if run_rel else (latest(ctx.archive, "run", task.id) or {}).get("path")
    if src is None:
        raise SystemExit("no run to check")
    src = Path(src)
    f2p, p2p = v["record"]["fail_to_pass"], v["record"]["pass_to_pass"]
    folder = ctx.archive.start("check", task.id, checked_run=str(src.relative_to(ctx.archive.root)).replace("\\", "/"))
    patches = sorted((src / "rounds").glob("**/round-*.patch"), key=lambda p: int(p.stem.split("-")[1]))
    rows = []
    for patch in [*patches, src / "final.patch"]:
        label = patch.stem
        body = _oracle_run(ctx, task, patch=patch, folder=folder, label=label)
        rows.append({"patch": label, "bytes": patch.stat().st_size if patch.exists() else 0,
                     "patch_applied": body.get("patch_applied", True), **verdict(body.get("results") or {}, f2p, p2p)})
    return folder.finish({"checks": rows, "fail_to_pass": f2p, "pass_to_pass_count": len(p2p)}, "finished")


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
        body["egress"] = egress(ctx, px, folder.fresh_path("egress.jsonl"))
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
    return {k: v for k, v in record.items() if k not in ("results", "base", "fixed", "usage")}


def progress(archive: c.Archive, task: str) -> dict[str, Any]:
    """An unfinished (or finished) run's progress, read from its stream: model calls, cost so far, rounds and their
    outcomes, and the last tool the agent used. Reads only the archive."""
    runs = sorted(p for p in (archive.root / "run" / task).glob("*") if p.is_dir()) if (archive.root / "run" / task).is_dir() else []
    if not runs:
        return {"run": None}
    folder = runs[-1]
    tally, objects, last_tool = c.Tally(), [], ""
    stream = folder / "stream.jsonl"
    for line in (stream.read_text(encoding="utf-8").splitlines() if stream.is_file() else []):
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
                                        "run", "check", "replay", "g6replay", "show", "progress", "rounds", "schedule"))
    ap.add_argument("--args", type=Path, help="g6replay: a JSON file of the review's arguments")
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
    args = ap.parse_args(argv)
    first = c.Archive(c.archive_root(args.archive))
    variant = {2: ROUND2, 3: ROUND3}.get(args.round, "")
    archive = c.Archive(first.root / {ROUND2: "round2", ROUND3: "round3"}[variant]) if variant else first
    prior = c.spent(first.rows()) if variant == ROUND2 else 0.0     # one $100 stop for rounds 1 and 2
    if args.command == "schedule":      # round 3's runs in order, each with how it ended, and the money
        rows = archive.rows()
        done = r3_done(rows)
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
        objects = [json.loads(line) for line in (folder / "stream.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip().startswith("{")]
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
                      repeat=args.repeat, rerun_why=args.rerun_why)
        result = {"validate": lambda: validate(ctx, task), "selfcheck": lambda: selfcheck(ctx, task),
                  "freecheck": lambda: freecheck(ctx, task), "modelcheck": lambda: modelcheck(ctx, task),
                  "dryrun": lambda: run_task(ctx, task, dry=True),
                  "run": lambda: run_task(ctx, task), "check": lambda: check(ctx, task, args.run_rel),
                  "replay": lambda: replay(ctx, task, args.code, args.test),
                  "g6replay": lambda: g6replay(ctx, task, args.code, args.args)}[args.command]()
    print(json.dumps(brief(args.command, result), indent=1, default=str)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
