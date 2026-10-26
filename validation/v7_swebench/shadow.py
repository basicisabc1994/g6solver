# SPDX-License-Identifier: Apache-2.0
"""V7's shadow learning (owner, 2026-10-24): after each task a model writes one lesson from G6's own check of the
stage-1 patch into a theory store, as a candidate. No lesson reaches Claude Code in this study, and no grade reaches a
lesson.

The owner asked "can you use T2 learning component for the learning process (i.e. theory formation) so there is
continual learning during the run as well if this possible?" and chose shadow learning. ``g6.learning``'s T2
(``baselines.TemplateStrategy``, T1 and T2 merged) picks one of seven fixed hints keyed to question-and-answer failure
codes, none of which fits a patch, so here a model writes the lesson, through ``g6.core.llm``.

- **When:** in ``run_arm.run_task``, after both stage-2 passes and before the task is graded, and only when G6's check
  found something: no finding, no model call.
- **What it sees** (:data:`INPUT_FIELDS`): the task id, which of G6's checks fired, G6's findings as Claude Code was
  given them (cut to :data:`MAX_FINDINGS_CHARS`), G's outcome as anyone sees it without the grader (whether G acted,
  how its run ended, whether its patch differs from stage 1's), and the store's lessons so far. Never a grade, the
  grader's output, the hidden tests, the task's own text or G6's raw replies.
- **What it writes:** a candidate :class:`g6.learning.Theory` in the phase's store (``<archive>/shadow/<phase>.db``,
  scope ``v7-<phase>``). A failure code new to the store becomes a candidate; one the store holds is recorded as a
  recurrence and adds nothing. Nothing credits, promotes or activates a theory: ``g6.learning`` needs paired held-out
  evidence and a person's acceptance for that, and this study has neither.
- **What it keeps:** a run folder of its own (arm ``lesson``): the input, the prompt, the full API response, the parsed
  lesson and the store's theories after it, and its cost, which counts toward the cap (``v7common.RESERVE_LESSON``
  is reserved with each task). A lesson that fails or cannot be read is archived as such and changes nothing else.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for _path in (HERE, HERE.parents[1] / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import v7common as c  # noqa: E402
from g6.core.llm import AnthropicCaller  # noqa: E402
from g6.learning import Theory, TheoryStore  # noqa: E402

ARM = "lesson"
INPUT_FIELDS = ("task", "checks", "findings_text", "g_acted", "g_status", "g_changed_patch")
# Cuts are in UTF-8 bytes: a token covers at least one byte, so the input's bytes bound its tokens and its cost.
MAX_FINDINGS_BYTES = 12_000
MAX_THEORIES = 40          # the store's lessons shown to the next one, oldest first
MAX_THEORY_BYTES = 300
MAX_TOKENS = 1_200
TIMEOUT_S = 120.0          # one request, no retry: every request the API answers is one archived response
ANTHROPIC_URL = "https://api.anthropic.com"
_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,47}")
_LOCK = threading.Lock()   # two tasks run at a time: one lesson reads or writes the store at once

SYSTEM = """You study one coding agent's mistakes across many repository tasks. For one task you are given what G6, a \
checking tool, found wrong with the agent's patch, how the agent's second pass (directed by those findings) ended, and \
the lessons recorded so far. Write the one lesson this task teaches about the agent's failures that would help it on a \
later, different task.

Reply with one JSON object and nothing else:
{"failure_code": "<UPPER_SNAKE_CASE name for the kind of failure>", "instruction": "<one sentence a coding agent could \
follow on a later task to avoid it>", "rationale": "<one or two sentences: what in these findings shows it>"}

If a lesson so far names the same kind of failure, reuse its failure_code exactly. If the findings teach nothing \
general, reply {"none": "<why>"}. The lesson is kept for a later study; the agent does not see it now."""


def _cut(text: str, limit: int, mark: str = "\n[cut]") -> str:
    data = text.encode("utf-8")
    return text if len(data) <= limit else data[:limit].decode("utf-8", errors="ignore") + mark


def lesson_input(task: str, finding: Mapping[str, Any], g: Mapping[str, Any], stage1_patch_sha256: str) -> dict:
    """What the lesson may see (:data:`INPUT_FIELDS`), from G6's check and G's run record only. The task id is kept
    for the archive and is not put in the prompt (:func:`user_text`)."""
    patch = g.get("patch_sha256")
    return {"task": task, "checks": list(dict.fromkeys(str(f.get("check")) for f in finding.get("findings") or ())),
            "findings_text": _cut(str(finding.get("findings_text") or ""), MAX_FINDINGS_BYTES),
            "g_acted": bool(g.get("acted")), "g_status": str(g.get("status") or ""),
            "g_changed_patch": bool(patch) and patch != stage1_patch_sha256}


def user_text(given: Mapping[str, Any], theories: list[Theory]) -> str:
    """The prompt: what G6 found, what became of it, the lessons so far. Never the task id, which names a public task
    the model may have seen."""
    shown = sorted(theories, key=lambda t: (t.created_at, t.theory_id))[:MAX_THEORIES]
    second = (f"G6's findings were given to the agent in a second pass, which ended {given['g_status'] or 'unrecorded'} "
              "and " + ("changed its patch." if given["g_changed_patch"] else "left its patch as it was.")
              if given["g_acted"] else "The agent was not given G6's findings: no second pass was run.")
    more = f", the first {MAX_THEORIES} shown" if len(theories) > MAX_THEORIES else ""
    lines = [f"G6's checks that found something: {', '.join(given['checks']) or 'none'}", "What G6 found:",
             given["findings_text"], "", second, "", f"Lessons so far ({len(theories)}{more}):"]
    lines += [f"- {t.failure_code}: {_cut(t.instruction, MAX_THEORY_BYTES, ' [cut]')}" for t in shown] or ["- none yet"]
    return "\n".join(lines)


def parse(text: str) -> dict[str, str]:
    """The model's lesson, or ``{"none": why}``; ValueError when the reply is not one."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except ValueError:
        raise ValueError("the reply is not a JSON object") from None
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    if "none" in data:
        return {"none": str(data["none"])[:800]}
    code, instruction = data.get("failure_code"), data.get("instruction")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ValueError(f"failure_code {code!r} is not an UPPER_SNAKE_CASE name")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("the lesson has no instruction")
    return {"failure_code": code, "instruction": instruction.strip()[:400],
            "rationale": str(data.get("rationale") or "").strip()[:800]}


def store(archive: c.Archive, phase: str) -> TheoryStore:
    """The phase's theory store, beside its runs (the archived truth is each lesson's ``theories.json``)."""
    folder = archive.root.resolve() / "shadow"   # absolute: a relative path would be put under G6_HOME
    folder.mkdir(parents=True, exist_ok=True)
    return TheoryStore(folder / f"{phase}.db", scope=f"v7-{phase}")


def recorded_cost(folder: Path) -> float:
    """What the responses archived in a lesson's folder cost (for a lesson the runner did not finish)."""
    cost = 0.0
    for path in sorted(folder.glob("response*.json")):
        try:
            cost += c.usage_cost(json.loads(path.read_text(encoding="utf-8")).get("usage") or {})
        except (OSError, ValueError, AttributeError):
            continue
    return round(cost, 6)


class Recorder:
    """An Anthropic SDK client whose ``messages.create`` keeps every response it returns, as the API sent it."""

    def __init__(self, client: Any) -> None:
        self._client, self.responses = client, []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        response = self._client.messages.create(**kwargs)
        dump = getattr(response, "model_dump", None)
        self.responses.append(dump(mode="json") if callable(dump) else {"repr": repr(response)})
        return response


def anthropic_client(key: str) -> Any:
    """The SDK client for the lesson, at Anthropic's own address whatever the environment says, with one request and a
    short timeout (the SDK's own defaults are 600 seconds and two retries the archive would not see)."""
    import anthropic

    return anthropic.Anthropic(api_key=key, base_url=ANTHROPIC_URL, timeout=TIMEOUT_S, max_retries=0)


def summary(archive: c.Archive, phase: str, tasks: Sequence[str]) -> dict[str, Any]:
    """The lessons of ``tasks`` (the analysis's completed tasks), counted: no test is run on them."""
    runs = [run["record"] for task in tasks if (run := archive.latest(phase, task, ARM)) is not None]
    recurs = Counter(r.get("failure_code") for r in runs if r.get("recurs"))
    return {"lessons": len(runs), "by_status": dict(Counter(r.get("status") for r in runs)),
            "candidates": sum(bool(r.get("added")) for r in runs), "recurrences": dict(recurs),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in runs), 6),
            "note": ("Shadow learning, descriptive only: each lesson a model wrote from G6's check of the stage-1 patch "
                     "and G's outcome; never given to Claude Code, never credited, promoted or activated.")}


def after_task(phase: str, task: str, finding: Mapping[str, Any], g: Mapping[str, Any], stage1_patch_sha256: str, *,
               archive: c.Archive, client: Callable[[str], Any], key: str,
               begin: Callable[[str], c.RunFolder], end: Callable[..., dict]) -> dict[str, Any]:
    """One task's lesson, archived as its own run; returns what the task's record keeps. Never raises: a lesson that
    fails, even before its run starts, is recorded as an error and the task goes on to its grading."""
    if not finding.get("findings"):
        return {"status": "skipped", "why": "G6's check found nothing to learn from"}
    body: dict[str, Any] = {"stage": "lesson", "model": c.MODEL, "agent_started": False, "cost_usd": 0.0,
                            "g6_code": "the runner's own oss/src (g6.core.llm, g6.learning), not the containers' wheel"}
    status, run = "error", None
    try:
        given = lesson_input(task, finding, g, stage1_patch_sha256)
        run = begin(ARM)
        run.write_json("lesson_input.json", given)
        with _LOCK, store(archive, phase) as theories:
            user = user_text(given, theories.theories())
        run.write("prompt.txt", f"{SYSTEM}\n\n---\n\n{user}")
        recorder = Recorder(client(key))
        try:   # the model call holds no lock: a slow one never holds up the other task's lesson
            text = AnthropicCaller(model=c.MODEL, client=recorder, fallbacks=False).complete(
                SYSTEM, user, max_tokens=MAX_TOKENS)
        finally:   # the cost first, so a billed call is counted even if archiving its response fails
            body["cost_usd"] = round(sum(c.usage_cost(r.get("usage") or {}) for r in recorder.responses), 6)
            for i, response in enumerate(recorder.responses):
                run.write_json("response.json" if i == 0 else f"response_{i}.json", response)
        try:
            lesson = parse(text)
        except ValueError as exc:
            lesson, status, body["why"] = None, "unparsed", str(exc)
            run.write_json("lesson.json", {"unparsed": text[:4000], "why": str(exc)})
        else:
            run.write_json("lesson.json", lesson)
            status = "finished"
        with _LOCK, store(archive, phase) as theories:   # look up and add at once: a code is added only once
            if lesson and "none" in lesson:
                body["none"] = lesson["none"]
            elif lesson:
                body["failure_code"] = lesson["failure_code"]
                known = theories.find_by_failure_code(lesson["failure_code"])
                if known:
                    body["recurs"] = known[0].theory_id
                else:
                    [added] = theories.add_candidates([Theory.new(
                        lesson["failure_code"], lesson["instruction"], rationale=lesson["rationale"],
                        source_instances=[task], proposer=f"{c.MODEL} (V7 shadow lesson)")])
                    body["added"] = added.theory_id
            kept = [asdict(t) for t in sorted(theories.theories(), key=lambda t: (t.created_at, t.theory_id))]
        run.write_json("theories.json", kept)
    except Exception as exc:  # noqa: BLE001 - a lesson never changes the task; its failure is archived
        status, body["error"] = "error", archive.redact(f"{type(exc).__name__}: {exc}")[:2000]
    if run is None:
        return {"status": "error", "error": body.get("error", "")}
    try:
        done = end(run, {**body, "released": True}, status)
    except Exception as exc:  # noqa: BLE001 - left open, the run is ended as interrupted when the batch next starts
        return {"status": "error", "run": run.rel, "error": archive.redact(f"{type(exc).__name__}: {exc}")[:500]}
    return {k: done[k] for k in ("status", "run", "cost_usd", "failure_code", "added", "recurs", "none", "why", "error")
            if k in done}
