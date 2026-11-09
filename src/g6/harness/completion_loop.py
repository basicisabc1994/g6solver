# SPDX-License-Identifier: Apache-2.0
"""The completion loop: when a coding agent says it has finished, test the work with checks it never saw, send failures
back in the same session until the work holds or the stop rule fires, and say what was tested and what remains unknown.

Two loops share this module. Once a critic is approved it is the worked examples' arm K (``reviews_until_clean`` in
``validation/worked_examples/run.py``), the review-until-clean loop: another model family reads the finished patch without the
agent's reasoning, and must state a rule, reasons and a test. If that test fails on the patch, the three go back in the same
session, and a fresh review reads the new patch, up to ``MAX_REPAIRS`` repairs. The agent may reject a finding by leaving the
code as it was. No scout and no probe: ``advance`` needs the boundary and the critic and nothing else, and a review that fails
or cannot be read ends the loop with no clean review. Each review's test stays beside the earlier ones (``critic_file``) to
catch a repair that undoes it, and runs alone, so one that will not run hides no other. Its reviewer is told why once, and a
defect no test could show ends the loop with no clean review, where K calls it clean. A run that ends leaves a row in
``FAILURES`` for the next cycle of the self-improvement loop.

``MODE_LINE`` in the person's key file, or no critic, gives the other loop, described from here on.

It is the worked examples' arm L9 (``validation/worked_examples/run.py``), moved into the product. Its text is data
(``HARNESS``, read by ``harness``; a test holds it to ``run.py``'s), and a later trained harness needs no code: a file
with the fields ``trained`` holds, the parts ``TRAINED`` names, adds to those parts as ``run.py``'s ``trained`` does.

``advance`` takes a repository, the task's text, the base commit and a session's state folder, and says what comes next:
continue, with the text that goes back to the agent, or stop, with the report. By default it makes the scouts' sets itself,
on a copy of the base commit, once the session has changed Python: a cheaper loop than the one L9 measured, and the report
says so. ``prepare``, with ``G6_LOOP_SCOUTS=before_fix`` in the person's key file, runs them at a session's first task,
before any edit, as L9 does, and gives what the agent starts with: the fixer text and its scout's work. ``advance`` then
makes only what ``prepare`` could not. Each call is one hook of one session, so what must last between them is in the
folder's ``state.json``. Python only: with none, nothing is spent.

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
from g6.context.redact import MASK, mask_paths, scrub, secret_spans
from g6.core import host_models, keys
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.core.llm import LLMCaller, LLMError, _start_model_call, provider_from_env
from g6.core.storage import state_path
from g6.harness import patch_report
from g6.harness.completion_hook import CHILD
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.harness.patch_review import _in_repository, _loaded_elsewhere, review_patch
from g6.workforce import repo_tests
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

HARNESS = Path(__file__).with_name("data") / "completion_harness.json"
#: The parts of the loop harness training can add to: never the sets, the scoring, the reply forms or the stop rule.
TRAINED = ("fixer", "scout", "repair", "critic")
CONTINUE, STOP, WAIT = "continue", "stop", "wait"
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
#: The line in the person's key file that runs the scouts before the fix: ``before_fix``, what L9 measured. It holds the first
#: prompt and spends there, whatever the prompt asks, so by default they wait until the session has changed Python.
WHEN_LINE = "G6_LOOP_SCOUTS"
NOT_ASKED = f"by default they run when the agent stops; {WHEN_LINE}=before_fix in your key file runs them first"
#: G6's clock for the scouts' and the runs' deadlines (a test replaces it).
_clock = time.monotonic
#: A scout may read and write files, and nothing else: no shell, so no model-written test runs outside G6's boundary.
SCOUT_TOOLS = "Read,Glob,Grep,Write"
SCOUT_SETTINGS = {"disableAllHooks": True, "permissions": {"deny": [f"Read(~/{folder}/**)" for folder in (
    ".g6", ".claude", ".codex", ".ssh")]}}
#: The line in the person's key file that approves Codex as the reviewer from another model family.
CRITIC_LINE = "G6_LOOP_CRITIC"
#: The line in the person's key file that keeps the scouts' loop where a critic is approved: ``scouts``.
MODE_LINE = "G6_LOOP_MODE"
#: How the review-until-clean loop ends, ``reviews_until_clean``'s words where it has them; what a review that was read says.
CLEAN, NO_REVIEW, RAN_OUT = "a review reproduced nothing", "a review failed", "the repairs ran out with a defect open"
LEFT, WITHDRAWN, READ_VERDICTS = "the agent left the code as it was", "the agent took its change back", ("none", "defect")
#: A clean review of a patch that is no longer the patch: what it says of the code that is there is nothing.
MOVED = "the patch changed after the review that read it"
#: A reviewer named a defect, its test would not run even once it had been told why, and K has no such end: it says nothing of
#: such a test, so its review stands as clean. A named defect that nothing could show is not clean, and it is not a finding.
UNREPRODUCED = "a defect was claimed and could not be reproduced"
#: What goes back to that reviewer, once, after the pack: the pack's own limit is for what a reviewer must read whole.
#: What follows a pack in which G6 masked what looks like a secret (``context.redact.scrub``), so the reviewer, another
#: vendor's model, never reads one, and knows the mask for what it is.
MASKED_NOTE = (f"G6 put {MASK} in place of each value above that looks like a secret, so none of them left this machine. {MASK} "
               "is never the code's own value: write no test that reads it or compares with it.")
WOULD_NOT_RUN = ("\n\nThe test in your last reply would not run, so it showed nothing. G6 ran it and got:\n{error}\n\nAnswer again "
                 "in the same form: a test that runs, or VERDICT: none if you no longer find a defect.")
#: What a file of a reviewer's test says when it showed nothing.
SHOWED_NOTHING = ("did not run", patch_report.NOT_JUDGED)
NO_REASON = "no reason was given"
#: A stop that leaves the patch with no review that came back clean, whatever earlier reviews said.
NO_CLEAN_REVIEW = "G6_E_NO_CLEAN_REVIEW"
#: A stop on a defect a reviewer named and no test could show (``UNREPRODUCED``).
NOT_REPRODUCED = "G6_E_DEFECT_NOT_REPRODUCED"
#: A call with no Python changed against the base, so nothing was tested, and no loop began.
NO_PYTHON = "G6_E_NO_PYTHON_CHANGED"
#: A call that found another step of its session running: the code the self-improvement loop gives the same refusal.
BUSY = "G6_E_LOOP_BUSY"
ALREADY_RUNNING = "Another step of G6's completion loop is running for this session, so this one did nothing."
#: A reply that edited nothing and asks something: it repairs nothing and rejects nothing, so the person holds the next move.
WAITING_ON_PERSON = "G6_E_LOOP_WAITING_ON_PERSON"
ASKED = ("G6's completion loop is waiting for your answer: the agent asked a question, so it neither repaired the finding nor "
         "rejected it, and the finding stays open. The loop goes on when the agent next stops.")
#: A question mark that ends a sentence, past any closing marks, and the ones that stand alone in other scripts.
_ASKS = re.compile(r"[\uff1f\u061f]|\?[*_'\"\u201d\u2019)\]]*(?:\s|$)")
#: Code a reply quotes: a question mark there asks nobody anything.
_QUOTED = re.compile(r"```.*?```|`[^`\n]*`", re.S)
#: Why a run ended, from what stopped it: the row's stable cause, for ``metaharness.domain.FailureRecord``.
OPEN, FIXED, CONTESTED = "review_finding_open", "review_finding_fixed", "review_finding_contested"
FOUND_NOTHING, UNREACHABLE, UNREADABLE, OVERSIZE = ("review_found_nothing", "reviewer_unreachable", "reviewer_unreadable",
                                                    "review_too_long")
UNCONFINED, UNASKED, UNAPPROVED, MASKED_LINES = "no_boundary", "no_task", "no_reviewer_approved", "review_masked_lines"
#: The causes a profile cannot change: nothing failed, or only a person can put it right.
NOT_CHANGEABLE = frozenset({FOUND_NOTHING, UNREACHABLE, UNCONFINED, UNASKED, UNAPPROVED, MASKED_LINES})
#: Where those rows go, beside the sessions' folders: local, and never pushed.
FAILURES = ("completion_loop", "failures.jsonl")
#: An empty file named for each run that owes a row, as what a run owes must outlive that run's own calls (``settle``).
OWING = ("completion_loop", "owing")
#: The one requirement the final review assesses: the task's text, which no stated check tests.
REQUIREMENT = "task"
RUN_BY_G6 = "G6 runs them for you when you finish, confined, with no network, and sends back what each did"
REFUSED = "\n\nThe runner checked your work and refused it:\n- {wrong}\n{did}Put this right and finish."
NO_BOUNDARY = ("G6's completion loop did not run: no operating-system boundary can confine model-written tests here "
               "({why}), so G6 ran none of them and asked no model. Nothing was tested. g6 doctor says what this computer "
               "needs for a boundary.")
NOT_APPROVED = ("G6's completion loop did not run: its scouts use your Claude Code sign-in, and nothing in your key file "
                "approves that. Add the line G6_LLM_PROVIDER=claude_code to {path} to turn it on. Nothing was tested.")
NOT_ON = ("G6's completion loop did not run: nothing in your key file approves a reviewer. Add the line "
          f"{CRITIC_LINE}=codex to {{path}} to turn on the review-until-clean loop, on your Codex sign-in. The line "
          "G6_LLM_PROVIDER=claude_code turns on the scouts' loop instead. Nothing was tested.")
NO_TASK = ("G6's completion loop did not run: it could not tell what you asked for, so there was nothing to test the work "
           "against. Nothing was tested.")
#: The lines a hook shows where a loop did not run and so no report came: one for each reason.
NOTHING_CHANGED = "G6's completion loop did not run: no Python changed against the base commit, so nothing was tested."
ALREADY_READ = ("G6's completion loop did not run: the patch has not changed since a review of this session read it, so no "
                "review was spent on it.")
ALREADY_ENDED = ("G6's completion loop did not run: it has already ended for this task, and its report stands. Your next "
                 "prompt starts a new loop.")
TOO_LONG = (f"the task and the patch are past the {MAX_PACK_CHARS:,} characters a review can read whole, so no reviewer was "
            "asked")
#: A clean reply from a reviewer that was not sent every line of the task and the patch: no clean review. Under the 200
#: characters the report shows of a review's error.
HIDDEN = ("what looks like a private key runs over lines of the task or the patch, and G6 masked it, so the reviewer did not "
          "read those lines and its clean reply is no clean review")
STOPPED = {"no_counterexample_in_domain": "no probe failed", "timeout": "the loop's time ran out",
           "counterexample_budget_exhausted": "the stop rule: every repair the loop allows has been sent"}
#: The stops that are themselves a run's cause.
CAUSES = {LEFT: CONTESTED, WITHDRAWN: "change_withdrawn", UNREPRODUCED: "review_test_unrunnable", MOVED: "patch_moved",
          STOPPED["timeout"]: "loop_time_ran_out"}
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
    review_ask: str


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
                   data["critic_note"], data["review_ask"])


@dataclass
class ProbeRun:
    """One run of a probe set: pytest's own count of each case, so no reading of its console can hide a failure."""
    exit: int | None
    results: dict[str, str]
    texts: dict[str, str]
    tail: str = ""
    boundary: str = ""     # what confined it, as the run says

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


def review_note(found: Mapping[str, str], shipped: Harness) -> str:
    """What goes back to the agent in the review-until-clean loop, as ``reviews_until_clean`` sends it."""
    return shipped.critic_note.format(**{k: found.get(k, "") for k in ("rule", "why", "test")}) + shipped.review_ask


def asks(said: str) -> bool:
    """Whether the agent's reply asks anything: a question mark that ends a sentence, anywhere in it, outside quoted code. It
    reads punctuation and no wording, as a list of what people say around a question knows only the sayings on the list.

    It leans one way on purpose. Read as a wait, a refusal costs a person one look. Read as a refusal, a question writes a false
    rejection into the record the loop learns from. So a refusal that asks anything on its way to its reason waits, and if no
    one answers, that run has no end and no row until its next stop.

    What it still gets wrong: a request with no question mark ("I need your decision") reads as a refusal. Its words stay in
    the row for the maintainers' label to put right, and a person's yes to it is reviewed by the loop their prompt arms."""
    return bool(_ASKS.search(_QUOTED.sub(" ", said)))


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
            code, printed, errors = self._launch(argv, prompt, cwd, files, env, seconds)
            return self._reply(code, printed, errors, files, (prompt,))

    def _launch(self, argv: list[str], stdin: str, cwd: Path, files: Path, env: dict[str, str],
                seconds: float) -> tuple[int, str, str]:
        """The one step that starts the program, so a caller that checks what is sent sees it whole, as it leaves."""
        _start_model_call()
        return host_models._run(argv, stdin, cwd, files, env, seconds, self.program.name)


def scout_host() -> Host | None:
    """The scouts' session, when the key file's own ``G6_LLM_PROVIDER`` line approves Claude Code; else None."""
    caller = proposer._backend(provider_from_env())
    return ToolSession(caller.model).run if isinstance(caller, host_models.ClaudeCodeCaller) else None


def _named(caller: Any) -> str:
    """The model a caller was given, else its program's name, as ``critic_host`` gives Codex none and its default is known only
    to Codex. Nothing is guessed: "" with neither."""
    return getattr(caller, "model", "") or getattr(getattr(caller, "program", None), "name", "")


def critic_host() -> LLMCaller | None:
    """Codex as the reviewer from another model family, when the key file's own ``CRITIC_LINE`` approves it; else None.
    It takes no tools (``host_models``), so it reads what ``_pack`` gives it and no file of the person's."""
    if (keys.get(CRITIC_LINE) or "").strip().lower() != "codex":
        return None
    return host_models.CodexCaller(timeout=CRITIC_SECONDS)


# --- the probes: G6's rules for a check a model proposed, and its confined runs --------------------------------------------

def _checks(where: Path, root: Path, only: str = "") -> tuple[list[vs.ProposedCheck], dict[str, str], list[str]]:
    """The test files in ``where`` (``only`` that one, if named) a proposed check may be, their places in a copy, and why each
    other file is refused."""
    checks, refused = [], []
    for path in sorted(item for item in where.iterdir() if item.is_file() and only in ("", item.name)) if where.is_dir() else ():
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
    results, texts, tail, boundary = {}, {}, [], ""
    for ran in runs:
        boundary = (ran.get("isolation") or {}).get("backend") or boundary
        elsewhere, verdict = _loaded_elsewhere(change, ran), ran.get("verdict")
        if elsewhere or verdict not in repo_tests.RAN:
            code = None
            tail.append(vs.LOADED_ELSEWHERE.format(elsewhere) if elsewhere else f"{verdict}: {ran.get('said') or ''}")
            texts.update(ran.get("failure_messages") or {})     # pytest's own words: no case has them, so only ``_unrun`` reads them
            continue
        code = None if code is None else max(code, int(verdict == repo_tests.FAILED))
        full, refused = _in_repository(ran), set(ran.get("os_errors") or ())
        for named, phases in (ran.get("test_outcomes") or {}).items():
            results[_PLACE.sub("", full(named))] = "unknown" if named in refused else _outcome(phases)
        texts.update({_PLACE.sub("", full(named)): text for named, text in (ran.get("failure_messages") or {}).items()})
    return ProbeRun(code, results, texts, "\n".join(tail), boundary)


def run_set(change: repo_tests.Change, probes: Path, *, at_base: bool, deadline: float, only: str = "") -> ProbeRun:
    """The probes in ``probes`` (``only`` that file, if named) on the tree or at the base commit: each file in its own confined
    copy, with no network. A file with no time left before ``deadline`` is not started, so the set is unusable and says
    nothing of the code."""
    checks, placed, refused = _checks(probes, change.repo, only)
    if not checks:
        return ProbeRun(None, {}, {}, "; ".join(refused) or "no test file G6 could run")
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


def _issue(task: str, proof: str) -> str:
    """The pack's first part: the token a reply must quote, then the task."""
    return f"issue.txt:\n{proof}\n\n{task}"


def _head(patch: str, task: str, proof: str) -> str:
    """The start of the pack, what a review must read whole: the issue, then the patch."""
    return f"{_issue(task, proof)}\n\ncandidate.patch:\n{patch}"


def _parents(change: repo_tests.Change) -> list[str]:
    """Of the base commit, each file the patch touches that it holds, as the pack shows it."""
    return [f"parent/{rel}:\n{text}" for rel in change.files
            if (text := repo_tests.read_at(change.repo, change.base_commit, rel)) is not None]


def _cut(head: str, parents: list[str], fits: bool, mask: Callable[[str], str] = lambda text: text) -> str:
    """The pack from its parts, cut to what a review reads. The cut falls on the parent files only, so a head that fits stays
    whole as ``_read_whole`` measured it; ``mask`` is applied again to the part the cut falls in, and to no other."""
    room, pack = max(MAX_PACK_CHARS, len(head)) if fits else MAX_PACK_CHARS, head
    for part in parents:
        left = room - len(pack) - 2
        if left <= 0:
            break
        pack += "\n\n" + (part if len(part) <= left else mask(part[:left]))
    return pack[:room]


def _pack(change: repo_tests.Change, patch: str, task: str, proof: str) -> str:
    """What L9's critic reads in its folder, as text, since it has no tools: the issue, the patch, and of the base commit
    the files the patch touches. ``patch`` is the caller's read: reading it here would make a second patch. This is the
    pack before anything is masked; ``_masked_pack`` is what is sent."""
    head = _head(patch, task, proof)
    return _cut(head, _parents(change), len(head) <= MAX_PACK_CHARS)


#: A hunk's head, with how many lines of the old side and of the new side follow it (one where git leaves a count out).
_HUNK_AT = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def _sides(lines: list[str]) -> list[tuple[list[int], list[int]]]:
    """For each file of a patch's ``lines``, the lines of its old side and of its new side, as indices, read by each hunk's
    counts: a context line is of both, a removed line of the old, an added line of the new. A line no count allows ends its
    hunk, so a file's head is never read as a line of it, and a line that starts ``\\`` (git's note of a missing last newline,
    or G6's naming of bytes) is of neither."""
    files, old, new, joined = [], 0, 0, False
    for i, line in enumerate(lines):
        mark = line[:1]
        if old or new:
            if mark == "\\":
                continue
            took_old, took_new = (mark == "-" and old > 0) or (mark == " " and old > 0 and new > 0), \
                (mark == "+" and new > 0) or (mark == " " and old > 0 and new > 0)
            if took_old or took_new:
                if took_old:
                    files[-1][0].append(i)
                    old -= 1
                if took_new:
                    files[-1][1].append(i)
                    new -= 1
                continue
            old = new = 0
        head = _HUNK_AT.match(line)
        if head:
            if not joined:
                files.append(([], []))
            joined = True
            old, new = (int(head[n]) if head[n] is not None else 1 for n in (1, 2))
        elif mark != "\\":
            joined = False
    return files


def _masked_patch(patch: str, parents: list[str]) -> tuple[str, bool]:
    """``patch`` with each value G6's scrubber finds masked where it stands, and whether a mask ran over a line of it, so a
    review of it did not read it whole. The values are found in each side of each file as the patch shows it, its lines
    without their marks, and in each parent file whole: not in the diff's text, where a mark parts a name from its value on
    the next line, and where a rule that reads on to the end would read from one file into the next. A line of a side the
    parent holds whole, a value in it found there, is masked where it stands too, so a value whose name the hunk leaves out
    is still found. The patch's other lines, its heads and G6's own, are scrubbed one by one."""
    lines, cuts, hidden = patch.split("\n"), {}, False
    files = _sides(lines)
    owned = {i for old, new in files for i in (*old, *new)}

    def place(text: str, rows: list[str]):
        """Each span the scrubber finds in ``text``, cut by row: (row, start, end, whether the span runs over a line)."""
        starts = [0]
        for row in rows[:-1]:
            starts.append(starts[-1] + len(row) + 1)
        for begin, end in secret_spans(text):
            over = "\n" in text[begin:end]
            for row, start in enumerate(starts):
                a, b = max(begin, start), min(end, start + len(rows[row]))
                if a < b:
                    yield row, a - start, b - start, over

    for side in (side for both in files for side in both):
        rows = [lines[i][1:] for i in side]
        for row, a, b, over in place("\n".join(rows), rows):
            cuts.setdefault(side[row], []).append((a, b))
            hidden = hidden or over
    seen: dict[str, list[tuple[int, int, bool]]] = {}
    for text in parents:
        rows = text.split("\n")
        for row, a, b, over in place(text, rows):
            seen.setdefault(rows[row].rstrip("\r"), []).append((a, b, over))
    for i in owned:
        for a, b, over in seen.get(lines[i][1:].rstrip("\r"), ()):
            cuts.setdefault(i, []).append((a, b))
            hidden = hidden or over
    for i, line in enumerate(lines):
        if i not in owned:
            lines[i] = scrub(line)
        elif i in cuts:
            merged: list[list[int]] = []
            for a, b in sorted(cuts[i]):
                if merged and a <= merged[-1][1]:   # overlapping or touching values are masked as one
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            body, kept, at = line[1:], [], 0
            for a, b in merged:
                kept += [body[at:a], MASK]
                at = b
            lines[i] = line[0] + "".join(kept) + body[at:]
    return "\n".join(lines), hidden


def _masked_pack(change: repo_tests.Change, patch: str, task: str, proof: str) -> tuple[str, bool]:
    """The pack as it is sent, and whether a mask ran over a line of the task or the patch, the parts a review must read
    whole. What looks like a secret is masked in each part alone (the issue, the patch, each parent file), so a rule that
    reads on to the end never reads from one part into the next, and in the patch where it stands (``_masked_patch``)."""
    issue, parents = _issue(task, proof), _parents(change)
    shown, hidden = _masked_patch(patch, [part.partition("\n")[2] for part in parents])
    hidden = hidden or any("\n" in issue[begin:end] for begin, end in secret_spans(issue))
    head = f"{scrub(issue)}\n\ncandidate.patch:\n{shown}"
    return _cut(head, [scrub(part) for part in parents], len(_head(patch, task, proof)) <= MAX_PACK_CHARS, scrub), hidden


def critic_file(n: int) -> str:
    """Where review ``n`` keeps its test, beside the earlier ones: an earlier test stays to catch a repair that undoes it."""
    return "test_critic.py" if n == 1 else f"test_critic_{n}.py"


def run_critic(change: repo_tests.Change, task: str, caller: LLMCaller, system: str, where: Path, n: int = 1,
               told: str = "", *, patch: str) -> dict:
    """L9's critic: another model family reads the finished patch once, and K's, ``n`` the review it is. One that fails is
    recorded and L9 goes on. Its test, if it has one, waits in ``where`` to run with the probes. ``told``: what follows the
    pack, for a reviewer sent back. ``patch``: what it reads, as the caller read it. What it gives names the model called,
    here where the call is made: who is approved may change, and a later step calls no one. What looks like a secret is
    masked before it is sent (``_masked_pack``), while the head the review must read whole stays whole; ``masked`` counts
    the values, for the report. A mask that ran over a line of the task or the patch left the reviewer short of reading
    them whole, so a clean reply from it is a failed review (``HIDDEN``); a defect it names stands as any other."""
    found = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": ""}
    proof = f"review {uuid.uuid4().hex}"
    pack = _pack(change, patch, task, proof)
    sent, hidden = _masked_pack(change, patch, task, proof)
    masked = max(sent.count(MASK) - pack.count(MASK), 1) if sent != pack else 0
    try:
        found = critic_report(caller.complete(system, sent + (f"\n\n{MASKED_NOTE}" if masked else "") + told), proof=proof)
    except (LLMError, OSError) as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"[:400]
    if hidden and found["verdict"] == "none":
        found = {**found, "verdict": "failed", "error": HIDDEN}
    if found["test"]:
        where.mkdir(parents=True, exist_ok=True)
        (where / critic_file(n)).write_text(found["test"] + "\n", encoding="utf-8")
    return {**found, "reviewer": _named(caller), **({"masked": masked} if masked else {})}


def _read_whole(patch: str, task: str) -> bool:
    """Whether the pack holds the whole task and the whole patch: that its head fits. This is a length and never a search of
    the text, as a task may quote the patch. The pack is cut from its end, so the parent files go first, and a new file
    ``diff_text`` cuts is past this limit already. A reader of part of either has no clean review to give."""
    return len(_head(patch, task, f"review {'0' * 32}")) <= MAX_PACK_CHARS


def _unrun(got: ProbeRun) -> str:
    """Why a run showed nothing, in its own words: what stopped it, then what each case with no result said. This text goes to
    the reviewer, who is told nothing else of this machine, so it leaves with no secret and no home folder in it."""
    said = [text for case, text in got.texts.items() if got.results.get(case, "unknown") == "unknown"]
    text = "\n".join(part for part in (got.tail, *said) if part)
    return mask_paths(scrub(text))[-MAX_CASE_CHARS:] or "no case of it ran to a result"


def review_round(change: repo_tests.Change, task: str, critic: LLMCaller, shipped: Harness, session: Path, reviews: list[dict],
                 deadline: float) -> tuple[set[str], str]:
    """One round of the review-until-clean loop: a fresh review of the patch as it stands, kept in ``reviews``, then every
    review's test, each alone. It gives what fails and the words of the latest review whose test fails. A review that failed
    ends the loop whatever fails, as the patch it should have read was not read, so it gives nothing to send back. A hook cut
    short repeats its review, and that review's test is the only one with its number. The patch is read once, here: what is
    kept, what the reviewer is sent and the digest the loop's end compares are that one read, so a clean stop is about what
    the reviewer read, with no second read to differ from it. It is kept first, byte for byte, as ``run.py`` keeps
    ``round-n.patch``: a later repair must not lose what a finding was about. A file counts as
    passed only if each case that ever failed in it has passed by its own id: a skip, a rename or a case no longer collected
    fixes nothing. This decides what the report may call fixed, and what fails, and so what goes back, is as it was.

    Here the loop parts from K. A review that names a defect whose test shows nothing is told why once, in this round, so no
    repair is spent: only where nothing else failed, as otherwise what failed goes back and a fresh review reads the repair.
    A review's ``unrun`` says why its test shows nothing on the latest run, so a later run updates it, or takes it away."""
    n, results, failed, failing, where = len(reviews) + 1, {}, {}, set(), session / "sets" / "critic"
    patch, kept, mine = repo_tests.diff_text(change), session / "rounds" / f"round-{n}.patch", critic_file(n)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(patch.encode("utf-8"))
    (where / mine).unlink(missing_ok=True)
    said = (run_critic(change, task, critic, shipped.critic, where, n, patch=patch) if _read_whole(patch, task) else
            {"verdict": "failed", "rule": "", "why": "", "test": "", "error": TOO_LONG})

    def run(name: str) -> None:
        got = run_set(change, where, at_base=False, deadline=deadline, only=name)
        found = failures_of([("critic", got, None)])[0] if got.usable else set()    # one that will not run says nothing
        failing.update(found)
        failed[name] = sorted(case.removeprefix("critic:") for case in found)
        owed = {case for one in reviews for case in one["failed"].get(name, ())}
        results[name] = ("failed" if found else "did not run" if not got.usable else
                         patch_report.NOT_PASSED if any(got.results.get(case) != "passed" for case in owed) else
                         patch_report.NOT_JUDGED if "unknown" in got.results.values() else "passed")
        said["boundary"] = got.boundary or said.get("boundary", "")
        owner = next((one for one in reviews if one["file"] == name), said)
        if results[name] in SHOWED_NOTHING:
            owner["unrun"] = _unrun(got)
        else:
            owner.pop("unrun", None)

    for name in [critic_file(i) for i in range(1, n + 1) if (where / critic_file(i)).is_file()]:
        run(name)
    if said.get("unrun") and said["verdict"] == "defect" and not failing:
        told, claimed = said.pop("unrun"), said["rule"]
        (where / mine).unlink(missing_ok=True)
        del results[mine], failed[mine]
        said.update(run_critic(change, task, critic, shipped.critic, where, n, told=WOULD_NOT_RUN.format(error=told),
                               patch=patch), told=told, claimed=claimed)
        if (where / mine).is_file():
            run(mine)
    said.update(n=n, patch=digest(patch), file=mine, results=results, failed=failed)
    reviews.append(said)
    if said["verdict"] not in READ_VERDICTS or not failing:
        return set(), ""
    return failing, review_note(next(one for one in reversed(reviews) if results.get(one["file"]) == "failed"), shipped)


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
    to the cap, unless the agent left the code as it was. ``answered``: whether this call is the agent's reply. With none,
    an unchanged tree says nothing of what it decided, so what failed goes back as it was, and no round is spent. ``asked``:
    whether that reply asks anything. An unchanged tree then decides nothing either, but nothing goes back, as sending it
    would hold the question from the person who must answer it: ``waiting`` says so, and no round is spent."""

    def __init__(self, rounds: list[dict], check: Callable[[], tuple[set[str], str]], tree: Callable[[], str],
                 answered: bool = True, asked: bool = False) -> None:
        self.rounds, self.check, self.tree, self.answered, self.asked = rounds, check, tree, answered, asked
        self.round, self.pending, self.why, self.waiting = 0, "", "", False

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> SearchResult:
        if self.round and self.round == len(self.rounds):
            last, tree = self.rounds[-1], self.tree()
            if last["tree"] is None:
                last["tree"], self.pending = tree, last["text"]
                return SearchResult(None, "the agent has what failed", False, self.round)
            if last["tree"] == tree and not self.answered:
                self.pending = last["text"]
                return SearchResult(None, "the agent has not answered", False, self.round)
            if last["tree"] == tree and self.asked:
                self.waiting = True
                return SearchResult(None, "the agent asked a question", False, self.round)
            if last["tree"] == tree:
                self.why = LEFT
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
    """What comes next: ``CONTINUE``, with the text for the agent; ``STOP``, with the report ("" when there is none); or
    ``WAIT``, when this step did nothing, as another of the session was running or the person must answer first. ``code``: the
    stable code of a call that tested nothing or left no clean review, for a surface with G6's envelope. ``note``: with no
    report, the line a hook shows the person in its place, as a tool's reply has its own words for what did not run.
    ``ending``: on a stop, how the run ended, one of ``patch_report.ENDINGS``."""
    action: str
    text: str = ""
    code: str = ""
    note: str = ""
    ending: str = ""


def _load(session: Path) -> dict:
    path = session / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(session: Path, state: dict) -> None:
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    (session / "state.json.new").write_text(json.dumps(state), encoding="utf-8")
    os.replace(session / "state.json.new", session / "state.json")


def _finish(session: Path, state: dict, report: str, code: str = "", ending: str = "", *, patch: str = "") -> Step:
    """The end of the loop for this session: the report, which is kept beside the state, then the state that says so, with
    the code and the ending a later call repeats, and ``patch``, the digest of the patch as the loop ended, which a repeat
    compares with whatever ended it. A hook cut short between them ends the loop again, so no session ends with no report."""
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    (session / "report.md").write_text(report, encoding="utf-8")
    state.update(done=True, code=code, ending=ending, ended_patch=patch)
    _save(session, state)
    if state.get("visible"):
        shutil.rmtree(state["visible"], ignore_errors=True)
    return Step(STOP, report, code, ending=ending)


def _before_fix() -> bool:
    return (keys.get(WHEN_LINE) or "").strip().lower() == "before_fix"


def _scouts_chosen() -> bool:
    return (keys.get(MODE_LINE) or "").strip().lower() == "scouts"


def _final_review(root: Path, task: str, base: str) -> str:
    """The patch review of what stopped the loop, on the task's text alone: what the loop ran is not given to it."""
    reply = review_patch(root, task=task, requirements=[{"id": REQUIREMENT, "text": task[:20_000], "checks": []}], base=base,
                         timeout_s=REVIEW_S)
    return (reply.get("assessment") or {}).get("report") or ("The patch review gave no report: " + patch_report.code(
        (reply.get("warning_card") or {}).get("message", ""), 300))


def _report(root: Path, task: str, base: str, state: dict, reason: str) -> str:
    """What the probes did, above the patch review of what stopped the loop. They are not given to the review."""
    review = _final_review(root, task, base)
    rounds, ran = state.get("rounds") or [], state.get("ran") or {}
    return patch_report.completion_report({
        "reason": reason, "repairs": sum(one["tree"] is not None for one in rounds), "max_repairs": MAX_REPAIRS,
        "failing": rounds[-1]["failing"] if rounds else [], "critic": state.get("critic"),
        "late_because": state.get("late") or ("no hook ran them before the agent started" if _before_fix() else NOT_ASKED),
        "boundary": state.get("boundary"),
        "sets": [{"label": label, "hidden": label != "scout", "ran": ran.get(label, 0), "left_out": one["left_out"],
                  "refused": one["refused"], "late": one.get("when") != BEFORE} for label, one in state["sets"].items()]},
        review)


def _cause(state: dict, fixed: list[dict], still: list[dict]) -> str:
    """Why a run ended, from what stopped it, so a review that failed with a finding open is a failed review: the findings speak
    only where nothing else ended the loop."""
    stop, last = state["stop"], (state.get("reviews") or [{}])[-1]
    if stop == NO_REVIEW:
        return (OVERSIZE if last.get("error") == TOO_LONG else MASKED_LINES if last.get("error") == HIDDEN else
                UNREACHABLE if last.get("verdict") == "failed" else UNREADABLE)
    return CAUSES.get(stop) or (OPEN if still else FIXED if fixed else FOUND_NOTHING)


def _round_outcome(one: dict) -> str:
    """What one round came to."""
    if one["verdict"] not in READ_VERDICTS:
        return "review_failed"
    if "failed" in one["results"].values():
        return "test_failed"
    return "test_showed_nothing" if one["results"].get(one["file"]) in SHOWED_NOTHING else "nothing_reproduced"


def _failure_row(task: str, base: str, session: Path, state: dict, now: str, cause: str = "") -> dict:
    """A run's row for the next cycle of the self-improvement loop: ``metaharness.domain.FailureRecord``'s fields first, as
    a cause must be a stable code. ``independently_observed`` means only that G6 saw a test fail: it ran another family's test,
    apart from the agent, so a run where none failed observed nothing, whatever else it says. ``session`` names the folder
    beside it that holds each round's patch. ``label`` stays empty here: it is where the maintainers' verdict joins later. A
    refused run has no reviews. ``now``: the digest of the patch as the caller read it, "" if it could not. Nothing here reads
    the repository, which could fail: with no ``now``, ``tree_changed`` is None, unknown, and the run still has its row.
    ``cause``: a refusal's own, for this row alone. It is no part of the state, which a killed hook leaves for a run that goes
    on, and that run's row must say how that run ended."""
    reviews = state.get("reviews") or []
    fixed, still = patch_report.findings(reviews)
    cause = cause or _cause(state, fixed, still)
    return {"case_id": f"{digest(task)[:16]}:{base[:12]}", "cause": cause, "changeable": cause not in NOT_CHANGEABLE,
            "note": (still or fixed)[0]["rule"] if still or fixed else state["stop"],
            "independently_observed": bool(fixed or still), "task": digest(task), "base": base, "label": "",
            "models": {"agent": state.get("agent", ""),
                       "reviewer": next((one["reviewer"] for one in reversed(reviews) if one.get("reviewer")), "")},
            "session": session.name, "rounds": len(reviews),
            "reviews": [{**{k: one[k] for k in ("verdict", "rule", "why", "test", "patch", "results")},
                         "outcome": _round_outcome(one), "told": one.get("told", ""), "reviewer": one.get("reviewer", "")}
                        for one in reviews],
            "stop": state["stop"], "left": state.get("left", ""),
            "tree_changed": (now != reviews[0]["patch"] if now else None) if reviews else False}


def _row_of(line: str, session: Path) -> bool:
    """Whether ``line`` is a whole row of this session. A line a killed hook tore does not parse, so it is no row."""
    try:
        return json.loads(line).get("session") == session.name
    except (ValueError, AttributeError):
        return False


def _write_row(session: Path, state: dict) -> None:
    """``row_due`` added to ``FAILURES``, and taken off the state. A folder is one run, so one whole row, and that row says
    how the run ended: a hook cut short after its row adds no second, one cut short inside it adds the row again, and a run
    that went on after one has its row replaced, whole, by a new file. A row starts a line of its own after a torn one,
    which would take it down too. The log has a lock of its own, as an append is not atomic on every platform. A row that
    cannot be written costs no report: it stays in the state, with why in ``row``, and its mark stays, so a later call tries
    again (``settle``)."""
    try:
        path = state_path(*FAILURES)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with Coordination(path.with_name(".failures")).mutex():
            log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            lines = [line for line in log.split("\n") if line]
            others = [line for line in lines if not _row_of(line, session)]
            if len(others) == len(lines):
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a", encoding="utf-8",
                               newline="\n") as out:
                    out.write(("\n" if log and not log.endswith("\n") else "") + state["row_due"] + "\n")
            elif state["row_due"] not in lines:
                fresh = path.with_name(f"{path.name}.new")
                with os.fdopen(os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8",
                               newline="\n") as out:
                    out.write("\n".join([*others, state["row_due"]]) + "\n")
                os.replace(fresh, path)
        del state["row_due"]
        state.pop("row", None)
        state_path(*OWING, session.name).unlink(missing_ok=True)
    except (OSError, ValueError, WorkspaceRefused) as exc:
        state["row"] = f"{type(exc).__name__}: {exc}"[:300]


def _owe_row(task: str, base: str, session: Path, state: dict, now: str, cause: str = "") -> None:
    """The run's row, owed from here on: built, marked in ``OWING`` and saved with the state, before anything that can fail, so
    what follows can delay the row and cannot lose it. ``_write_row`` delivers it. It is JSON in ASCII, so it is one line
    whatever its text holds. A mark that cannot be made leaves the row to the run's own later calls."""
    state["row_due"] = json.dumps(_failure_row(task, base, session, state, now, cause))
    try:
        mark = state_path(*OWING, session.name)
        mark.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mark.touch()
    except OSError as exc:
        state["row"] = f"{type(exc).__name__}: {exc}"[:300]
    _save(session, state)


def _patch_now(root: Path, base: str) -> str:
    """The digest of the patch as it stands, or "" if it cannot be read. It takes a fresh ``change``: ``diff_text`` reads the
    tree, but of the new files only those its ``change`` holds, so one taken earlier misses a file added since."""
    try:
        return digest(repo_tests.diff_text(repo_tests.change(root, base)))
    except (OSError, ValueError):     # a RepoRefused is a ValueError
        return ""


def _unreproduced(reviews: list[dict]) -> bool:
    """Whether a review named a defect that nothing shows as the loop ends: no case of its test ever failed, so it is no
    finding, and on the last run the test showed nothing, or its reviewer, told why, kept the claim and gave no test. A defect
    that never had a test is K's: it asked nothing of G6."""
    last, found = reviews[-1]["results"], patch_report.findings(reviews)
    return any(one["verdict"] == "defect" and one not in (*found[0], *found[1])
               and (last.get(one["file"]) in SHOWED_NOTHING or (one.get("told") and one["file"] not in last)) for one in reviews)


def _ending(reason: str, reviews: list[dict]) -> str:
    """How a run ended, as the reply and the report label it. ``reason`` says what stopped the loop, and K's words for that stay
    as they are; this says where it left the patch. A finding the agent rejected is contested, and one with no shown pass is
    open, whatever stopped the loop. Any other stop but a clean one came to no result. So did a review that named a defect
    and wrote no test, though K's stop for it reads clean: its reply has a claim in it, and nothing tested the claim."""
    fixed, still = patch_report.findings(reviews)
    if reason == LEFT:
        return "contested"
    if still:
        return "open"
    if reason != CLEAN or (reviews[-1]["verdict"] == "defect" and reviews[-1]["file"] not in reviews[-1]["results"]):
        return "untested"
    return "fixed" if fixed else "clean"


def _review_end(root: Path, task: str, base: str, session: Path, state: dict, reason: str, said: str = "") -> Step:
    """The end of the review-until-clean loop: what the reviews found above the patch review, then the row. Only a review that
    was read and reproduced nothing is clean, and running out of repairs or a finding the agent rejected is a result. The
    row is owed and the state saved before the patch review, which is slow and can fail: a hook cut short there repeats no
    review, leaves the row owed, and ends as this would,
    unless the patch has changed since a clean review read it, which ``_advance`` then has reviewed again. A clean stop holds
    only for the patch there as the loop ends, so that is read last of all, after every wait. A patch that moved under a
    running step is not reviewed again here: an edit that keeps coming would spend a review each time."""
    state["stop"] = reason
    state["left"] = (" ".join(said.split())[:MAX_CASE_CHARS] or state.get("left") or NO_REASON) if reason == LEFT else ""
    reviews = state.get("reviews") or []
    _owe_row(task, base, session, state, _patch_now(root, base))
    review = _final_review(root, task, base)
    _write_row(session, state)
    # The last read of the patch: nothing this step does waits between here and the clean result, as the slow review and the
    # log's lock are behind it. It promises only that the patch at this read is the one the last review read. It does not hold
    # the tree still: an edit a moment later is in no report, and no check can rule that out. The session's next loop reads such
    # an edit, as it is another patch.
    now = _patch_now(root, base)
    if reason == CLEAN and now != reviews[-1]["patch"]:
        reason = state["stop"] = MOVED
        _owe_row(task, base, session, state, now)
        _write_row(session, state)      # this wait protects no clean stop: there is none left to protect
    code = "" if reason in (CLEAN, RAN_OUT, LEFT) else NOT_REPRODUCED if reason == UNREPRODUCED else NO_CLEAN_REVIEW
    ending = _ending(reason, reviews)
    return _finish(session, state, _reviews_report(state, reason, code, ending, review), code, ending, patch=now)


def _reviews_report(state: dict, reason: str, code: str, ending: str, review: str) -> str:
    """The report of a loop that reviewed: what its reviews found, why it stopped, then ``review``, the patch review's."""
    reviews = state["reviews"]
    return patch_report.completion_report({
        "reason": reason, "reviews": reviews, "repairs": sum(one["tree"] is not None for one in state["rounds"]),
        "max_repairs": MAX_REPAIRS, "left": state.get("left", ""), "no_clean": bool(code), "ending": ending,
        "boundary": next((one["boundary"] for one in reversed(reviews) if one.get("boundary")), "")}, review)


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
    """The scouts before the fix, at a session's first task, as L9 runs them, when ``WHEN_LINE`` in the person's key file says
    ``before_fix``; what the agent is told before it starts ("" for nothing). With no such line nothing here runs or is
    spent. Whatever keeps them from running, the agent's turn goes ahead, and ``advance`` does what is left, or says what
    did not happen. Nothing is spent on a repository with no Python, or where the review-until-clean loop will run."""
    if not _before_fix() or (critic_host() is not None and not _scouts_chosen()):
        return ""
    session = Path(session)
    root = repo_tests.checked_repo(repo)
    python = any(rel.endswith(".py") for rel in repo_tests._paths(repo_tests._git(root, "ls-files", "-z")))
    if not python or proposer.boundary(root):
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


def _held(session: Path) -> Any:
    """One step of a session at a time. The lock is the operating system's, so it dies with its process, and a hook that is
    killed leaves no session stuck. A call that cannot take it at once is refused (``WorkspaceRefused``): it does not wait
    its turn, as the step it found has not reached the agent yet, and a tree the agent never had the chance to touch would
    read as a finding it rejected."""
    return Coordination(session / ".step").mutex(wait=0)


def settle() -> None:
    """Every row a run still owes, whichever run owes it. A run's own calls end at the next prompt, and its session may never
    call again, so what is owed is found by its mark, and any call of any session tries it. A run with a step going on is
    passed over, as that step has its row in hand, and so is one that cannot be read, which holds up no other. A row the log
    keeps out stays owed and ends the round: the rest would only wait for the same lock in turn."""
    owing = state_path(*OWING)
    for mark in sorted(owing.iterdir()) if owing.is_dir() else ():
        run = owing.parent / mark.name
        try:
            with _held(run):
                state = _load(run)
                if "row_due" not in state:
                    mark.unlink(missing_ok=True)
                    continue
                _write_row(run, state)
                _save(run, state)
        except (WorkspaceRefused, OSError, ValueError):
            continue
        if "row_due" in state:
            return


def advance(repo: str | Path, task: str, base: str, session: str | Path, *, scout: Host | None = None,
            critic: LLMCaller | None = None, said: str = "", answered: bool = True, agent: str = "") -> Step:
    """One hook of the loop, for the repository's working tree against ``base``: see the module's docstring. ``scout`` and
    ``critic``: the sessions to use, else the ones the person's key file approves. ``said``: the agent's words as it stopped,
    kept as its reason if it left the code as it was. ``agent``: its model, for the row, where the caller knows it.

    ``answered``: whether this call is the agent's reply to what the last one sent back. A Stop hook is: it fires only once
    the agent has taken its turn, so an unchanged tree there is the agent's decision. A call over MCP may be a client's retry
    of a reply it lost, so there only a stated reason rejects a finding, and with none what failed goes back as it was. On
    either, a reply with the code unchanged that asks anything (``asks``) rejects nothing: the step says ``WAIT``, with
    ``WAITING_ON_PERSON``, and the loop goes on when the agent next stops.

    A step reads its round from the state and then writes that round's files, so two at once would undo each other: a call
    that finds another running does nothing, and says ``WAIT``."""
    session = Path(session)
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    settle()
    try:
        with _held(session):
            return _advance(repo, task, base, session, scout, critic, said, answered, agent)
    except WorkspaceRefused:
        return Step(WAIT, ALREADY_RUNNING, BUSY)


def _advance(repo: str | Path, task: str, base: str, session: Path, scout: Host | None, critic: LLMCaller | None,
             said: str, answered: bool, agent: str) -> Step:
    """One step, with the session held: the state is read here, so a step sees what the one before it wrote."""
    state = _load(session)
    if state.get("done"):
        if "row_due" in state:
            _write_row(session, state)
            _save(session, state)
        return Step(STOP, code=state.get("code", ""), note=ALREADY_ENDED, ending=state.get("ending", ""))
    state["agent"] = agent or state.get("agent", "")
    root = repo_tests.checked_repo(repo)
    change = repo_tests.change(root, base)

    def refused(text: str, code: str, cause: str) -> Step:
        state["stop"] = text
        now = _patch_now(root, base)
        _owe_row(task, base, session, state, now, cause)
        _write_row(session, state)
        reviews = state.get("reviews") or []
        if not reviews:
            return _finish(session, state, text, code, "untested", patch=now)
        ending = _ending(text, reviews)      # what earlier rounds found stands: the refusal is why the loop stopped
        return _finish(session, state, _reviews_report(state, text, code, ending, ""), code, ending, patch=now)

    if not change.sources:
        return (_review_end(root, task, base, session, state, WITHDRAWN) if state.get("reviews") else
                Step(STOP, code=NO_PYTHON, note=NOTHING_CHANGED, ending="untested"))
    if state.get("reviewed") and "started" not in state and state["reviewed"] == digest(repo_tests.diff_text(change)):
        return Step(STOP, note=ALREADY_READ)
    if not task.strip():
        return refused(NO_TASK, "G6_E_INPUT_VALIDATION", UNASKED)
    why = proposer.boundary(root)
    if why:
        return refused(NO_BOUNDARY.format(why=why), ISOLATION_UNAVAILABLE, UNCONFINED)
    critic = critic or critic_host()
    reviewing = critic is not None and not _scouts_chosen()
    if not reviewing:
        scout = scout or scout_host()
        if scout is None:
            return refused((NOT_APPROVED if _scouts_chosen() else NOT_ON).format(path=keys.path()), keys.KEY_MISSING, UNAPPROVED)
    shipped = harness()
    state.setdefault("started", time.time())
    state.setdefault("sets", {})
    rounds, reviews = state.setdefault("rounds", []), state.setdefault("reviews", [])
    if state.get("stop") == CLEAN and reviews[-1]["patch"] != digest(repo_tests.diff_text(change)):
        del rounds[-1], reviews[-1], state["stop"]      # a clean stop no report carried speaks only for the patch it read
    ends = _clock() + STOP_S - REVIEW_S

    def reviewed() -> tuple[set[str], str]:
        return review_round(change, task, critic, shipped, session, reviews, ends)

    def check() -> tuple[set[str], str]:
        if critic is not None and "critic" not in state:
            state["critic"] = run_critic(change, task, critic, shipped.critic, session / "sets" / "critic",
                                         patch=repo_tests.diff_text(change))
        seen = []
        state["ran"] = {}
        for label, one in state["sets"].items():
            if not one["left_out"]:
                got = run_set(change, Path(one["where"]) / "probes", at_base=False, deadline=ends)
                state["ran"][label], state["boundary"] = got.ran, got.boundary or state.get("boundary", "")
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
        if not reviewing:
            _scouts(change, task, session, state, shipped, scout, AT_STOP)
            if all(one["left_out"] for one in state["sets"].values()):
                return _finish(session, state, _report(root, task, base, state, "no probe set could run"),
                               patch=_patch_now(root, base))
        loop = Repairs(rounds, reviewed if reviewing else check, tree, answered, asks(said))
        got = cegis(WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                    max_iterations=MAX_REPAIRS + 1, timeout_s=LOOP_S - (time.time() - state["started"]))
    except repo_tests.RepoRefused as exc:
        if exc.code != ISOLATION_UNAVAILABLE:
            raise
        return refused(NO_BOUNDARY.format(why=exc), ISOLATION_UNAVAILABLE, UNCONFINED)
    if loop.waiting:
        _save(session, state)
        return Step(WAIT, ASKED, WAITING_ON_PERSON)
    if loop.pending and reviewing:
        _save(session, state)
        return Step(CONTINUE, loop.pending)
    if loop.pending:
        told, first = shipped.repair.format(text=loop.pending), _told(state, shipped)   # first: no hook could say it before
        _save(session, state)
        return Step(CONTINUE, f"{first}\n\n{told}" if first else told)
    reason = loop.why or STOPPED.get(got.status, got.reason)
    if not reviewing:
        return _finish(session, state, _report(root, task, base, state, reason), patch=_patch_now(root, base))
    if got.status == "no_counterexample_in_domain":
        reason = (NO_REVIEW if reviews[-1]["verdict"] not in READ_VERDICTS else UNREPRODUCED if _unreproduced(reviews) else CLEAN)
    elif got.status == "counterexample_budget_exhausted":
        reason = RAN_OUT
    return _review_end(root, task, base, session, state, reason, said)
