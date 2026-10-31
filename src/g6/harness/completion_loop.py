# SPDX-License-Identifier: Apache-2.0
"""The completion loop: when a coding agent says it has finished, test the work with checks it never saw, send failures
back in the same session until the work holds or the stop rule fires, and say what was tested and what remains unknown.

It is the worked examples' arm L9 (``validation/worked_examples/run.py``), moved into the product. Its text is data
(``HARNESS``, read by ``harness``; a test holds it to ``run.py``'s), and a later trained harness needs no code: a file
with the fields ``trained`` holds, the parts ``TRAINED`` names, adds to those parts as ``run.py``'s ``trained`` does.

``prepare`` runs the scouts at a session's first task, before any edit, as L9 does, and gives what the agent starts with: the
fixer text and its scout's work. ``advance`` takes a repository, the task's text, the base commit and a session's state
folder, and says what comes next: continue, with the text that goes back to the agent, or stop, with the report. It makes
any set ``prepare`` could not, on a copy of the base commit, and the report says those came late. Each call is one hook of
one session, so what must last between them is in the folder's ``state.json``. Python only: with none, nothing is spent.

The scouts. L9's scouts read the code with tools, and that reading matters, so ``verification_proposer`` cannot host them:
it asks for text about a diff, and a scout works before any fix, across the code. Each is a session on the person's own
Claude Code (``ToolSession``), in a scratch copy of the base commit with no history, so it sees no fix. It has no shell:
a shell would run model-written tests outside G6's boundary. So the probes it writes are run by G6, and what is wrong with
them goes back to it once, where L9's scout ran them itself. All work under the session's folder, which the agent is never
told of: it reads a temporary copy of its own scout's work. That keeps hidden probes from being read by accident or habit.
It is no protection against code running as the same user.

The evidence. A model wrote the probes, so they never pass a requirement: they run only through
``verification_search._run_check``, confined, in scratch copies with no network, and they never reach the review. With no
operating-system boundary the stage is refused and reported, and no model is asked. What stops the loop comes last:
``patch_review.review_patch`` assesses the patch on the task's text, with no stated check, so the outcome is at most
``insufficient_evidence``, and ``patch_report.completion_report`` puts what the probes did above its report.

The stop rule. ``g6.cegis.cegis`` holds it, as in L9: at most ``MAX_REPAIRS`` repairs, and an end when the agent leaves the
code as it was. L9's runner waits for the agent inside the loop, and a hook cannot, so each hook runs ``cegis`` again:
``Repairs`` replays the rounds in the state, and hands back when a new round's failures must go to the agent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from g6.cegis import InputDomain, SearchResult, cegis
from g6.core import host_models, keys
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.core.llm import LLMCaller, LLMError, _start_model_call, provider_from_env
from g6.harness import patch_report
from g6.harness.completion_hook import CHILD
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.harness.patch_review import _in_repository, _loaded_elsewhere, review_patch
from g6.workforce import repo_tests

HARNESS = Path(__file__).with_name("data") / "completion_harness.json"
#: The parts of the loop harness training can add to: never the sets, the scoring, the reply forms or the stop rule.
TRAINED = ("fixer", "scout", "repair", "critic")
CONTINUE, STOP = "continue", "stop"
MAX_REPAIRS, MAX_CROSS, MIN_RANDOM, MAX_SHOWN, MAX_CASE_CHARS = 3, 200, 300, 12, 900
SCOUT_CAP_USD, CRITIC_SECONDS = 2.0, 1500
#: One scout's session, its turns, one probe file's run, the final review and the critic's pack.
SCOUT_SECONDS, SCOUT_TURNS, RUN_S, REVIEW_S, MAX_PACK_CHARS = 1200, 200, 300, 600, 120_000
#: One hook's time for the scouts, before the fix or at the stop. The Stop hook's whole time, inside the plugin's 5400-second
#: timeout, as a hook that times out loses its report. The whole loop, from the first stop on (L9's wall clock). The least
#: time a scout is started with. What a hook adds to the agent's context: past 10,000 characters Claude only gets a preview.
SCOUTS_S, STOP_S, LOOP_S, MIN_SCOUT_S, MAX_CONTEXT_CHARS = 2400, 5100, 120 * 60, 60, 9_500
BEFORE, AT_STOP = "before the fix", "at the stop"
NO_TIME = "the time for scouts ran out before this one could start"
#: The line in the person's key file that keeps the scouts until the agent stops: ``at_stop``. Scouts before the fix are
#: what L9 measured, but they spend at each session's first prompt, whatever it asks.
WHEN_LINE = "G6_LOOP_SCOUTS"
#: G6's clock for the scouts' and the runs' deadlines (a test replaces it).
_clock = time.monotonic
#: A scout may read and write files, and nothing else: no shell, so no model-written test runs outside G6's boundary.
SCOUT_TOOLS = "Read,Glob,Grep,Write"
SCOUT_SETTINGS = {"disableAllHooks": True, "permissions": {"deny": [f"Read(~/{folder}/**)" for folder in (
    ".g6", ".claude", ".codex", ".ssh")]}}
#: The line in the person's key file that approves Codex as the reviewer from another model family.
CRITIC_LINE = "G6_LOOP_CRITIC"
#: The one requirement the final review assesses: the task's text, which no stated check tests.
REQUIREMENT = "task"
RUN_BY_G6 = "G6 runs them for you when you finish, confined, with no network, and sends back what each did"
REFUSED = "\n\nThe runner checked your work and refused it:\n- {wrong}\n{did}Put this right and finish."
NO_BOUNDARY = ("G6's completion loop did not run: no operating-system boundary can confine model-written tests here "
               "({why}), so G6 ran none of them and asked no model. Nothing was tested. g6 doctor says what this computer "
               "needs for a boundary.")
NOT_APPROVED = ("G6's completion loop did not run: its scouts use your Claude Code sign-in, and nothing in your key file "
                "approves that. Add the line G6_LLM_PROVIDER=claude_code to {path} to turn it on. Nothing was tested.")
NO_TASK = ("G6's completion loop did not run: it could not tell what you asked for, so there was nothing to test the work "
           "against. Nothing was tested.")
STOPPED = {"no_counterexample_in_domain": "no probe failed", "timeout": "the loop's time ran out",
           "counterexample_budget_exhausted": "the stop rule: every repair the loop allows has been sent"}
#: Where L9's text names its container: its folders, and the sentence that runs the probes there.
_RUN = re.compile(r"run them from the repository with\s+`[^`]*`")
_TREE = re.compile(r"/opt/we/(?:hidden\d*_)?tree\.json")
_PROBES = re.compile(r"/opt/we/(?:probes|hidden\d*)")
#: Where ``verification_search`` places a check in its copy: left out of a case's name, as it says nothing of the case.
_PLACE = re.compile(r"^.*?g6_check_[0-9a-f]{12}/")
#: A scout's session: what it is asked, where it starts, the one folder it may write to, and its time.
Host = Callable[[str, Path, Path, float], str]


def literal(text: str) -> str:
    """``text`` for a template that ``format`` fills."""
    return text.replace("{", "{{").replace("}", "}}")


@dataclass(frozen=True)
class Harness:
    """The loop's text: each part with what training added to it."""
    scout: str
    hidden_scouts: tuple[str, ...]
    fixer: str
    repair: str
    critic: str
    critic_note: str


def harness(path: Path = HARNESS) -> Harness:
    """The harness in ``path``. Training can only add to a part's instructions: anything else in ``trained`` is refused."""
    data = json.loads(path.read_text(encoding="utf-8"))
    added = data.get("trained") or {}
    odd = sorted(set(added) - set(TRAINED))
    if odd:
        raise ValueError(f"harness training cannot change {odd}")

    def scout(text: str) -> str:
        add = added.get("scout")
        return text.replace("5. Critique:", f"   {literal(add)}\n5. Critique:", 1) if add else text

    fixer, repair, critic = added.get("fixer"), added.get("repair"), added.get("critic")
    return Harness(scout(data["scout"]), tuple(scout(text) for text in data["hidden_scouts"]),
                   data["fixer"] + (f"\n\n{fixer}" if fixer else ""),
                   data["repair"] + (f"\n\n{literal(repair)}" if repair else ""),
                   data["critic"].replace("</task>", f"4. {critic}\n</task>", 1) if critic else data["critic"],
                   data["critic_note"])


@dataclass
class ProbeRun:
    """One run of a probe set: pytest's own count of each case, so no reading of its console can hide a failure."""
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


def split_failures(now: ProbeRun, base: ProbeRun | None) -> tuple[list[str], list[str]]:
    """G6's plan rule 3 (``classify``) on each failing case: one that passed at the base and fails now is a counterexample
    on what worked before; any other is what a reader says the fix still owes."""
    regressions, owed = [], []
    for case in sorted(k for k, v in now.results.items() if v in ("failed", "error")):
        before = base.results.get(case) if base else None
        verdict = vs.classify("preserve", {"result": "failed", "detail": ""},
                              {"result": before, "detail": ""} if before else None)
        (regressions if verdict["result"] == vs.COUNTEREXAMPLE else owed).append(case)
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
    for heading, cases in (("Passed before your change and fail now:", regress),
                           ("Failed before your change and still fail:", owed)):
        if cases:
            shown = cases[:left]
            left -= len(shown)
            parts.append("\n".join([heading, *(f"- {label}:{case}\n{text[:MAX_CASE_CHARS]}" for label, case, text in shown),
                                    *([f"(and {len(cases) - len(shown)} more)"] if len(cases) > len(shown) else [])]))
    return failing, "\n\n".join(parts)


def with_critic(found: tuple[set[str], str], critic: Mapping[str, str], note: str) -> tuple[set[str], str]:
    """What fails, with the critic's rule, reasons and test before it if the critic's own test is among the failures."""
    failing, text = found
    if not any(case.startswith("critic:") for case in failing):
        return failing, text
    return failing, note.format(**{k: critic.get(k, "") for k in ("rule", "why", "test")}) + "\n\n" + text


def critic_report(said: str, *, proof: str = "") -> dict[str, str]:
    """The critic's reply: its rule, verdict, reasons and test. Only what follows any progress lines counts, and a test
    comes with a verdict of defect or not at all. ``proof``: the line it must quote from its pack, because a critic that
    could not read the pack still answers, and "none" from it looks like a clean review."""
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


def scout_gate(raw: str, *, exit_code: int, collected: int, broken: int = 0, randoms: int | None = None,
               executed: int | None = None) -> list[str]:
    """What is wrong with a scout's probes, by G6's own count: ``InputDomain`` says how many combinations the declared
    dimensions make, up to ``MAX_CROSS``. Empty when nothing is."""
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


def located(text: str, where: Path) -> str:
    """L9's text on this machine: its container's folders become ``where``'s, and G6 runs the probes, not the reader."""
    text = _TREE.sub(lambda _: (where / "tree.json").as_posix(), _RUN.sub(RUN_BY_G6, text))
    return _PROBES.sub(lambda _: (where / "probes").as_posix(), text)


# --- the sessions: the person's own sign-ins, each only with the line in their key file that approves it --------------------

class ToolSession(host_models.ClaudeCodeCaller):
    """Claude Code, signed in as the person, with ``SCOUT_TOOLS``. What ``host_models`` gives a text completion, it gives
    this: the program found the same way, a clean environment, the prompt on stdin, a bounded run, and nothing kept on
    disk. It starts in ``cwd``, may write only there and in ``where``, and runs no hook and no MCP server."""

    def run(self, prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        _start_model_call()
        program = self.locate()
        with host_models._folders() as (_, files):
            (files / "no_mcp_servers.json").write_text(host_models.NO_SERVERS, encoding="utf-8")
            (files / "g6_settings.json").write_text(json.dumps(SCOUT_SETTINGS), encoding="utf-8")
            argv = [program, "-p", "--output-format", "json", "--tools", SCOUT_TOOLS, "--permission-mode", "acceptEdits",
                    "--add-dir", host_models._plain(where), "--strict-mcp-config",
                    "--mcp-config", host_models._plain(files / "no_mcp_servers.json"),
                    "--settings", host_models._plain(files / "g6_settings.json"), "--setting-sources", "user",
                    "--no-session-persistence", "--max-turns", str(SCOUT_TURNS),
                    "--max-budget-usd", f"{SCOUT_CAP_USD:.2f}", *(("--model", self.model) if self.model else ())]
            env = host_models._environment(self.keep_env, files / "tmp") | {CHILD: "1"}
            code, printed, errors = host_models._run(argv, prompt, cwd, files, env, seconds, self.program.name)
            return self._reply(code, printed, errors, files, (prompt,))


def scout_host() -> Host | None:
    """The scouts' session, when the key file's own ``G6_LLM_PROVIDER`` line approves Claude Code; else None."""
    caller = proposer._backend(provider_from_env())
    return ToolSession(caller.model).run if isinstance(caller, host_models.ClaudeCodeCaller) else None


def critic_host() -> LLMCaller | None:
    """Codex as the reviewer from another model family, when the key file's own ``CRITIC_LINE`` approves it; else None.
    It takes no tools (``host_models``), so it reads what ``_pack`` gives it and no file of the person's."""
    if (keys.get(CRITIC_LINE) or "").strip().lower() != "codex":
        return None
    return host_models.CodexCaller(timeout=CRITIC_SECONDS)


# --- the probes: G6's rules for a check a model proposed, and its confined runs --------------------------------------------

def _checks(where: Path, root: Path) -> tuple[list[vs.ProposedCheck], dict[str, str], list[str]]:
    """The test files in ``where`` a proposed check may be, their places in a copy, and why each other file is refused."""
    checks, refused = [], []
    for path in sorted(item for item in where.iterdir() if item.is_file()) if where.is_dir() else ():
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            if proposer._PLUGIN.search(source):
                raise ValueError("it declares a pytest plugin or a conftest, which G6 refuses")
            checks += vs.proposed_checks([{"id": path.stem, "requirement_id": REQUIREMENT, "intent": "change",
                                           "file": path.name, "source": source}], {REQUIREMENT})
        except ValueError as exc:
            refused.append(f"{path.name}: {exc}"[:300])
    refused += [f"{one.file}: past the limit of {vs.MAX_CHECKS} files" for one in checks[vs.MAX_CHECKS:]]
    checks = checks[:vs.MAX_CHECKS]
    try:
        return checks, vs.placements(root, checks), refused
    except ValueError as exc:
        return [], {}, [*refused, str(exc)[:300]]


def _outcome(phases: Mapping[str, str]) -> str:
    return "error" if "failed" in (phases.get("setup"), phases.get("teardown")) else phases.get("call") or "skipped"


def _read(change: repo_tests.Change, runs: Iterable[dict]) -> ProbeRun:
    """A set's runs, one for each file, as one ``ProbeRun``, read from pytest's per-case outcomes: ``reading`` judges a
    check as a whole, and calls a file of passes and skips no result, where L9's probes skip the cases that cannot occur.
    A case that failed on an operating-system error is unknown, as a refused access looks like one. A run that did not
    finish, or tested code from outside its copy, leaves the set unusable."""
    code: int | None = 0
    results, texts, tail = {}, {}, []
    for ran in runs:
        elsewhere, verdict = _loaded_elsewhere(change, ran), ran.get("verdict")
        if elsewhere or verdict not in repo_tests.RAN:
            code = None
            tail.append(vs.LOADED_ELSEWHERE.format(elsewhere) if elsewhere else f"{verdict}: {ran.get('said') or ''}")
            continue
        code = None if code is None else max(code, int(verdict == repo_tests.FAILED))
        full, refused = _in_repository(ran), set(ran.get("os_errors") or ())
        for named, phases in (ran.get("test_outcomes") or {}).items():
            results[_PLACE.sub("", full(named))] = "unknown" if named in refused else _outcome(phases)
        texts.update({_PLACE.sub("", full(named)): text for named, text in (ran.get("failure_messages") or {}).items()})
    return ProbeRun(code, results, texts, "\n".join(tail))


def run_set(change: repo_tests.Change, probes: Path, *, at_base: bool, deadline: float) -> ProbeRun:
    """The probes in ``probes`` on the tree or at the base commit: each file in its own confined copy, with no network. A
    file with no time left before ``deadline`` is not started, so the set is unusable and says nothing of the code."""
    checks, placed, _ = _checks(probes, change.repo)
    if not checks:
        return ProbeRun(None, {}, {}, "no test file G6 could run")
    runs = []
    for one in checks:
        left = int(min(RUN_S, deadline - _clock()))
        if left < vs.MIN_RUN_S:
            runs.append({"verdict": "timed_out", "said": "G6's time for this hook ran out before this file ran"})
            break
        runs.append(vs._run_check(change, placed[one.id], one.source, placed[one.id], at_base=at_base, timeout_s=left))
    return _read(change, runs)


def _gate(tree: Path, got: ProbeRun) -> list[str]:
    """``scout_gate`` on a set's run at the base commit."""
    def cases(test: str) -> list[str]:
        return [v for k, v in got.results.items() if k.rsplit("::", 1)[-1].split("[")[0] == test]

    crashed = sum(v == "error" or (v == "failed" and not re.match(r"assert|AssertionError|Failed", got.texts.get(k, "")))
                  for k, v in got.results.items())
    return scout_gate(tree.read_text(encoding="utf-8", errors="replace") if tree.is_file() else "",
                      exit_code=2 if got.exit is None else got.exit, collected=len(cases("test_cross")), broken=crashed,
                      randoms=sum(v in ("passed", "failed") for v in cases("test_random")),
                      executed=sum(v in ("passed", "failed") for v in cases("test_cross")))


@contextmanager
def _base_copy(change: repo_tests.Change, seconds: float):
    """A scratch copy of the base commit's code: no history, and nothing of the change."""
    deadline, report = time.monotonic() + seconds + 120, {}
    with repo_tests.repository_copy(change.repo, deadline=deadline, report=report, base=change.base_commit) as copy:
        with repo_tests._base_revision(change, copy, deadline, materialised=report.get("links_materialised")):
            yield copy


def _scout(change: repo_tests.Change, task: str, text: str, where: Path, host: Host, deadline: float) -> dict:
    """One scout's set: its session, its probes run at the base commit, and, if they are refused, one more try, told what
    G6 counted and what they did, as it could not run them itself. A set that exercised nothing is left out."""
    (where / "probes").mkdir(parents=True, exist_ok=True)
    asked = located(text.format(number=1, title=task.splitlines()[0][:200], text=task), where)
    failed, again = "", ""
    got = None
    for _ in range(2):
        left = min(SCOUT_SECONDS, deadline - _clock() - RUN_S)      # its run at the base commit still has to fit
        if left < MIN_SCOUT_S:
            failed = failed or NO_TIME
            break
        try:
            with _base_copy(change, left) as copy:
                host(asked + again, copy, where, left)
        except (LLMError, repo_tests.CopyNotRun, OSError) as exc:
            failed = f"{type(exc).__name__}: {exc}"[:400]
        got = run_set(change, where / "probes", at_base=True, deadline=deadline)
        wrong = _gate(where / "tree.json", got)
        if failed or not wrong:
            break
        did = [f"- {case}: {got.texts.get(case, '')[:MAX_CASE_CHARS]}" for case, said in got.results.items()
               if said in ("failed", "error")][:MAX_SHOWN]
        again = REFUSED.format(wrong="\n- ".join(wrong), did=f"What they did on the base commit:\n{chr(10).join(did)}\n"
                               if did else "")
    usable = got is not None and got.usable
    return {"where": str(where), "at_base": asdict(got) if got else None, "refused": _checks(where / "probes", change.repo)[2],
            "left_out": "" if usable else failed or "it exercised nothing on the base commit"}


def _pack(change: repo_tests.Change, task: str, proof: str) -> str:
    """What L9's critic reads in its folder, as text, since it has no tools: the issue, the patch, and of the base commit
    the files the patch touches."""
    parts = [f"issue.txt:\n{proof}\n\n{task}", f"candidate.patch:\n{repo_tests.diff_text(change)}"]
    for rel in change.files:
        text = repo_tests.read_at(change.repo, change.base_commit, rel)
        if text is not None:
            parts.append(f"parent/{rel}:\n{text}")
    return "\n\n".join(parts)[:MAX_PACK_CHARS]


def run_critic(change: repo_tests.Change, task: str, caller: LLMCaller, system: str, where: Path) -> dict:
    """L9's critic: another model family reads the finished patch once. One that fails is recorded and the loop goes on. Its
    test, if it has one, waits in ``where`` to run with the probes."""
    found = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    proof = f"review {uuid.uuid4().hex}"
    try:
        found = critic_report(caller.complete(system, _pack(change, task, proof)), proof=proof)
    except (LLMError, OSError) as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
    if found["test"]:
        where.mkdir(parents=True, exist_ok=True)
        (where / "test_critic.py").write_text(found["test"] + "\n", encoding="utf-8")
    return found


# --- the loop --------------------------------------------------------------------------------------------------------------

class WorkingTree:
    """Stands where ``cegis`` has a sketch: the candidate is the agent's working tree, which ``Repairs`` looks at."""

    @staticmethod
    def instantiate(control: dict[int, str]) -> str:
        return control[0]


class Repairs:
    """G6's CEGIS over the agent's work, as L9's ``Repairs``: ``search`` is the agent, told what failed, and ``evaluate``
    the probes. A hook cannot wait for the agent, so ``rounds``, which the state keeps, replays what is done: a round
    whose failures have not gone back ends this hook with ``pending``, and a failure that survives a repair goes back, up
    to the cap, unless the agent left the code as it was."""

    def __init__(self, rounds: list[dict], check: Callable[[], tuple[set[str], str]], tree: Callable[[], str]) -> None:
        self.rounds, self.check, self.tree = rounds, check, tree
        self.round, self.pending, self.why = 0, "", ""

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> SearchResult:
        if self.round and self.round == len(self.rounds):
            last, tree = self.rounds[-1], self.tree()
            if last["tree"] is None:
                last["tree"], self.pending = tree, last["text"]
                return SearchResult(None, "the agent has what failed", False, self.round)
            if last["tree"] == tree:
                self.why = "the agent left the code as it was"
                return SearchResult(None, self.why, False, self.round)
        self.round += 1
        return SearchResult({0: f"round {self.round}"}, "agent", False, self.round)

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        if self.round > len(self.rounds):
            failing, text = self.check()
            self.rounds.append({"failing": sorted(failing), "text": text, "tree": None})
        return [[{"ok": not self.rounds[self.round - 1]["failing"]} for _ in inputs]]


@dataclass(frozen=True)
class Step:
    """What comes next: ``CONTINUE``, with the text for the agent, or ``STOP``, with the report ("" when there is none)."""
    action: str
    text: str = ""


def _load(session: Path) -> dict:
    path = session / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(session: Path, state: dict) -> None:
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    (session / "state.json.new").write_text(json.dumps(state), encoding="utf-8")
    os.replace(session / "state.json.new", session / "state.json")


def _finish(session: Path, state: dict, report: str) -> Step:
    """The end of the loop for this session: the report, which is kept beside the state."""
    state["done"] = True
    _save(session, state)
    (session / "report.md").write_text(report, encoding="utf-8")
    if state.get("visible"):
        shutil.rmtree(state["visible"], ignore_errors=True)
    return Step(STOP, report)


def _report(root: Path, task: str, base: str, state: dict, reason: str) -> str:
    """What the probes did, above the patch review of what stopped the loop. They are not given to the review."""
    reply = review_patch(root, task=task, requirements=[{"id": REQUIREMENT, "text": task[:20_000], "checks": []}], base=base,
                         timeout_s=REVIEW_S)
    review = (reply.get("assessment") or {}).get("report") or ("The patch review gave no report: " + patch_report.code(
        (reply.get("warning_card") or {}).get("message", ""), 300))
    rounds, ran = state.get("rounds") or [], state.get("ran") or {}
    return patch_report.completion_report({
        "reason": reason, "repairs": sum(one["tree"] is not None for one in rounds), "max_repairs": MAX_REPAIRS,
        "failing": rounds[-1]["failing"] if rounds else [], "critic": state.get("critic"),
        "late_because": state.get("late") or "no hook ran them before the agent started",
        "sets": [{"label": label, "hidden": label != "scout", "ran": ran.get(label, 0), "left_out": one["left_out"],
                  "refused": one["refused"], "late": one.get("when") != BEFORE} for label, one in state["sets"].items()]},
        review)


def _scouts(change: repo_tests.Change, task: str, session: Path, state: dict, shipped: Harness, host: Host,
            when: str) -> None:
    """The scouts' sets still to make, each kept as it ends, so a hook cut short loses only the one it was in. The agent's
    own scout comes first, as what it is told is that scout's work. Before the fix, one with no time left waits for the
    Stop hook, which works on a copy of the base commit too."""
    deadline = _clock() + SCOUTS_S
    for label, text in [("scout", shipped.scout), *((f"held{i}", one) for i, one in enumerate(shipped.hidden_scouts))]:
        if label in state["sets"]:
            continue
        made = _scout(change, task, text, session / "sets" / label, host, deadline)
        if when == BEFORE and made["left_out"] == NO_TIME:
            state["late"] = "the time for scouts before the fix ran out"
            break
        state["sets"][label] = {**made, "when": when}
        _save(session, state)


def _told(state: dict, shipped: Harness) -> str:
    """What the agent reads first, once: the fixer text and its scout's work, or "" with no such work. It gets a copy, so
    that the probes that run stay out of its reach, as L9 locks them. The text itself holds the work, up to what a hook may
    add, as a session started with ``-p`` cannot read a folder outside its own, and no plugin can open one for it."""
    one = state["sets"].get("scout")
    if state.get("visible") or not one or one["left_out"]:
        return ""
    visible = Path(tempfile.mkdtemp(prefix="g6_scout_"))
    state["visible"] = str(visible)
    shutil.copytree(one["where"], visible, dirs_exist_ok=True)
    parts = [located(shipped.fixer, visible), *(f"{path.name}:\n{path.read_text(encoding='utf-8', errors='replace')}"
                                                for path in [visible / "tree.json", *sorted((visible / "probes").glob("*.py"))]
                                                if path.is_file())]
    return "\n\n".join(parts)[:MAX_CONTEXT_CHARS]


def prepare(repo: str | Path, task: str, base: str, session: str | Path, *, scout: Host | None = None) -> str:
    """The scouts before the fix, at a session's first task, as L9 runs them; what the agent is told before it starts ("" for
    nothing). Whatever keeps them from running, the agent's turn goes ahead, and ``advance`` does what is left, or says
    what did not happen. Nothing here is spent on a repository with no Python, or where ``WHEN_LINE`` says ``at_stop``."""
    session = Path(session)
    root = repo_tests.checked_repo(repo)
    python = any(rel.endswith(".py") for rel in repo_tests._paths(repo_tests._git(root, "ls-files", "-z")))
    if not python or (keys.get(WHEN_LINE) or "").strip().lower() == "at_stop" or proposer.boundary(root):
        return ""
    scout = scout or scout_host()
    if scout is None:
        return ""
    shipped, state = harness(), _load(session)
    state.setdefault("sets", {})
    try:
        _scouts(repo_tests.change(root, base), task, session, state, shipped, scout, BEFORE)
    except repo_tests.RepoRefused as exc:
        if exc.code != ISOLATION_UNAVAILABLE:
            raise
        state["late"] = str(exc)[:300]
    told = _told(state, shipped)
    _save(session, state)
    return told


def advance(repo: str | Path, task: str, base: str, session: str | Path, *, scout: Host | None = None,
            critic: LLMCaller | None = None) -> Step:
    """One hook of the loop, for the repository's working tree against ``base``: see the module's docstring. ``scout`` and
    ``critic``: the sessions to use, else the ones the person's key file approves."""
    session = Path(session)
    state = _load(session)
    if state.get("done"):
        return Step(STOP)
    root = repo_tests.checked_repo(repo)
    change = repo_tests.change(root, base)
    if not change.sources:
        return Step(STOP)
    if not task.strip():
        return _finish(session, state, NO_TASK)
    why = proposer.boundary(root)
    if why:
        return _finish(session, state, NO_BOUNDARY.format(why=why))
    scout = scout or scout_host()
    if scout is None:
        return _finish(session, state, NOT_APPROVED.format(path=keys.path()))
    critic = critic or critic_host()
    shipped = harness()
    state.setdefault("started", time.time())
    state.setdefault("sets", {})
    rounds = state.setdefault("rounds", [])
    ends = _clock() + STOP_S - REVIEW_S

    def check() -> tuple[set[str], str]:
        if critic is not None and "critic" not in state:
            state["critic"] = run_critic(change, task, critic, shipped.critic, session / "sets" / "critic")
        seen = []
        state["ran"] = {}
        for label, one in state["sets"].items():
            if not one["left_out"]:
                got = run_set(change, Path(one["where"]) / "probes", at_base=False, deadline=ends)
                state["ran"][label] = got.ran
                seen.append((label, got, ProbeRun(**one["at_base"])))
        if (state.get("critic") or {}).get("test"):
            got = run_set(change, session / "sets" / "critic", at_base=False, deadline=ends)
            if got.usable:     # a critic's test that will not run says nothing of the patch
                state["ran"]["critic"] = got.ran
                seen.append(("critic", got, None))
        return with_critic(failures_of(seen), state.get("critic") or {}, shipped.critic_note)

    def tree() -> str:
        return digest(repo_tests.snapshot(root))

    try:
        _scouts(change, task, session, state, shipped, scout, AT_STOP)
        if all(one["left_out"] for one in state["sets"].values()):
            return _finish(session, state, _report(root, task, base, state, "no probe set could run"))
        loop = Repairs(rounds, check, tree)
        got = cegis(WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                    max_iterations=MAX_REPAIRS + 1, timeout_s=LOOP_S - (time.time() - state["started"]))
    except repo_tests.RepoRefused as exc:
        if exc.code != ISOLATION_UNAVAILABLE:
            raise
        return _finish(session, state, NO_BOUNDARY.format(why=exc))
    if loop.pending:
        told, first = shipped.repair.format(text=loop.pending), _told(state, shipped)   # first: no hook could say it before
        _save(session, state)
        return Step(CONTINUE, f"{first}\n\n{told}" if first else told)
    return _finish(session, state, _report(root, task, base, state, loop.why or STOPPED.get(got.status, got.reason)))
