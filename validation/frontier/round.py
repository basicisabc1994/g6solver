# SPDX-License-Identifier: Apache-2.0
"""One development round of the frontier programme (design §6). Development, not a validation: nothing here is a claim.

    python validation/frontier/round.py draw --round 1
    python validation/frontier/round.py devcheck --round 1 [--model llama3.1:8b]
    python validation/frontier/round.py run --round 1 [--model llama3.1:8b] [--reflector gpt-oss:20b] [--workflows k,k]

``draw`` writes ``splits.json`` (dev, selection, confirmation, dev-eval per workflow, from the pool ledger) before any
model call, so the ledger counts those ids even if a run dies. ``run`` measures, per workflow, on the dev-eval split:

* A  one direct call with the default system prompt;  B  G6 as built;  H  a hand-written prompt (h_prompts.json);
* G  every prebuilt component on at once (``FrontierSpec.everything``, V6b's arm G);
* D  ``tune`` with ``Method()`` (v0): recovery and reflection (gpt-oss:20b) proposing, racing on selection, one sequential
  confirmation, a scripted approval; then its head profile;
* DG the same ``tune``, from a workflow confirmed with G as its initial profile: both modes together;
* F  ``tune`` with random edits from the same space at the same budgets; then its head profile.

Which arms a round runs, and its call budget, are its design (``DESIGNS``).

It writes, as each workflow finishes, ``rows/<key>.jsonl`` (one row per arm and case: no model text, only its hash) and
``tuning/<key>-<arm>.json`` (the TuningRun and the head profile), then ``scoreboard.json`` and ``README.md``, both
recomputed from the rows. A rerun skips what is already written. The reflector sits behind a canary that records any
selection, confirmation or dev-eval input it is shown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # oss/validation: `frontier` and `v6b_metaharness`

from frontier import data, ledger, workflows  # noqa: E402
from frontier.workflows import APPROVER, CALLS_PER_CASE, SEED, SIZES, FrontierSpec  # noqa: E402

ARMS = ("A", "B", "H", "G", "D", "DG", "F")         # every arm a round may run, in report order
NAMES = {"A": "A direct", "B": "B as built", "H": "H hand-written", "G": "G every component on",
         "D": "D meta-harness", "DG": "DG meta-harness from G", "F": "F random edits"}
REPEAT = "B2"                                    # B measured again last: a control, not an arm
# Rounds 1 and 2 ran A, B, H, D and F at 3 calls per case. Round 3 tests the two modes together (owner, 2026-10-19):
# every prebuilt component on (G, V6b's arm G, so at V6b's call budget) and self-tuning from there (DG), beside
# self-tuning from G6 as built (D), on the four workflows where V6b measured G. MBPP had 133 fresh ids left, fewer than
# a registered study's 150, so round 3 spends them all: a smaller dev-eval, and a confirmation split still above the
# evidence policy's minimum.
DESIGNS: dict[int, dict[str, Any]] = {3: {"arms": ("A", "B", "G", "D", "DG"), "calls": 8, "mean_calls": 4.0,
                                          "workflows": ("gsm8k", "massive", "scifact", "mbpp"),
                                          "sizes": {"mbpp": {"dev": 20, "selection": 33, "confirmation": 40,
                                                             "dev_eval": 40}}}}
LABEL = "Development, not a validation"
OLLAMA = "http://127.0.0.1:11434"
HERE = Path(__file__).resolve().parent
SYSTEMS = {"current": None, "do-the-task": "Do the task.",
           "final-line": "Do the task. End with the final answer on its own line."}


def design(n: int) -> dict[str, Any]:
    return DESIGNS.get(n, {"arms": ("A", "B", "H", "D", "F"), "calls": CALLS_PER_CASE,
                           "mean_calls": float(CALLS_PER_CASE)})


def round_dir(n: int) -> Path:
    return ledger.ROUNDS / f"round-{n:02d}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------------------------------------------------
def draw_splits(n: int, keys: Sequence[str], *, root: Path, out: Path, sizes: Mapping[str, int] = SIZES,
                by_workflow: Mapping[str, Mapping[str, int]] | None = None) -> dict[str, dict[str, list[str]]]:
    """This round's splits: read from ``out/splits.json``, any workflow missing from it drawn now and written before
    anything else runs. ``by_workflow`` sizes a workflow differently (round 3: MBPP's last fresh ids)."""
    path = out / "splits.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "round": n, "seed": SEED, "label": f"round-{n:02d}", "sizes": dict(sizes), "splits": {},
        **({"sizes_by_workflow": {k: dict(v) for k, v in by_workflow.items()}} if by_workflow else {})}
    missing = [k for k in keys if k not in record["splits"]]
    for key in missing:
        spec = workflows.SPECS[key]
        cut = record.get("sizes_by_workflow", {}).get(key, record["sizes"])
        record["splits"][key] = ledger.draw(key, workflows.distinct_pool(spec, root), cut, seed=SEED,
                                            label=record["label"])
    if missing or not path.is_file():
        _write_json(path, record)
    return {k: record["splits"][k] for k in keys}


def load(spec: FrontierSpec, root: Path, ids: Mapping[str, Sequence[str]]) -> dict[str, list]:
    return {split: spec.cases(root, list(case_ids)) for split, case_ids in ids.items()}


# --------------------------------------------------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------------------------------------------------
class Tap:
    """Counts calls; records (as a hash) any forbidden snippet that appears in a prompt, as written or JSON-escaped
    (the reflector reads its failing cases as JSON). A snippet already in text the reflector may be shown (``allowed``)
    would fire on that text, not on a leak, so it is set aside and counted in ``blind`` (round 1: the held-out
    utterance "s." is in the reflection prompt's own "already pass.")."""

    def __init__(self, inner: Any, forbid: Sequence[str] = (), allowed: Sequence[str] = ()) -> None:
        forbid = list(forbid)
        self.inner, self.calls, self.hits = inner, 0, []
        self.forbid = [s for s in forbid if not any(s in a for a in allowed)]
        self.blind = len(forbid) - len(self.forbid)

    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        self.calls += 1
        text = f"{system}\n{user}"
        self.hits += [hashlib.sha256(s.encode()).hexdigest()[:16] for s in self.forbid
                      if s in text or json.dumps(s, ensure_ascii=False)[1:-1] in text]
        return self.inner.complete(system, user, max_tokens=max_tokens)


def allowed_text(spec: FrontierSpec, dev: Sequence) -> list[str]:
    """What the reflector may be shown: its fixed prompts, the task, the edit space and the dev cases."""
    from g6.metaharness.reflect import REFLECT_PROMPT, REFLECT_SYSTEM

    return [REFLECT_SYSTEM, REFLECT_PROMPT, spec.task, *spec.prompts, *(str(c.input) for c in dev),
            *(str(c.expected) for c in dev)]


def snippet(case_input: str) -> str:
    """The case-specific part of an input: its first block that is not a shared label or option list, without its
    field label ("Question:", "Utterance:", "CLAIM:"), so it is found however a prompt quotes it."""
    shared = ("Labels:", "Intent labels:", "Options:")
    blocks = [b.strip() for b in case_input.split("\n\n") if b.strip()]
    block = next((b for b in blocks if not b.startswith(shared)), case_input)
    return re.sub(r"^[A-Za-z ]{2,20}:\s*", "", block)[:160]


def _row(arm: str, rec: Any) -> dict[str, Any]:
    return {"arm": arm, "case_id": rec.case_id, "released": rec.released, "oracle": rec.oracle, "success": rec.success,
            "model_calls": rec.model_calls, "wall_clock_s": round(rec.wall_clock_s, 3),
            "completion_state": rec.completion_state, "output_sha256": rec.output_sha256}


def measure_profile(wf: Any, profile: Any, cases: Sequence, arm: str, *, system: str | None = None) -> list[dict]:
    from g6.metaharness.evaluate import run_case
    from g6.metaharness.executor import context_for, model_call_factory

    make = model_call_factory(system) if system else model_call_factory()
    rows = []
    for case in cases:
        contexts: list[Any] = []

        def execute(contract: Any) -> str:
            ctx = context_for(wf, profile)
            contexts.append(ctx)
            return make(ctx)(contract)

        rows.append(_row(arm, run_case(wf, execute, profile, case, arm, calls=lambda: sum(c.calls for c in contexts))))
    return rows


def measure_direct(spec: FrontierSpec, caller: Any, cases: Sequence) -> list[dict]:
    """Arm A: one call with the default system prompt and the task text; no harness, so everything returned is
    released."""
    from g6.metaharness.executor import DEFAULT_SYSTEM

    rows = []
    for case in cases:
        start = time.monotonic()
        try:
            out, state = caller.complete(DEFAULT_SYSTEM, spec.task.replace("{input}", case.input)), "direct"
        except Exception as exc:  # noqa: BLE001 - a failed call releases nothing
            out, state = "", f"error: {type(exc).__name__}"
        ok = bool(out) and spec.oracle(out, case.expected) is True
        rows.append({"arm": "A", "case_id": case.case_id, "released": bool(out), "oracle": "pass" if ok else "fail",
                     "success": ok, "model_calls": 1, "wall_clock_s": round(time.monotonic() - start, 3),
                     "completion_state": state, "output_sha256": hashlib.sha256(out.encode()).hexdigest()})
    return rows


class Rows:
    """``rows/<key>.jsonl``: one row per arm and case, appended as measured."""

    def __init__(self, out: Path, key: str) -> None:
        self.path = out / "rows" / f"{key}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict]:
        text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        return [json.loads(x) for x in text.splitlines() if x.strip()]

    def done(self, arm: str) -> set[str]:
        return {r["case_id"] for r in self.all() if r["arm"] == arm}

    def add(self, rows: Sequence[dict]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def _measure(rows: Rows, arm: str, cases: Sequence, fn: Callable[[Sequence], list[dict]], log: Callable[[str], None],
             reset: Callable[[], None] | None = None, chunk: int = 10) -> None:
    """Measure the cases not yet in the rows, from a freshly loaded model (``reset``) when there are any."""
    todo = [c for c in cases if c.case_id not in rows.done(arm)]
    if todo and reset is not None:
        reset()
    for i in range(0, len(todo), chunk):
        rows.add(fn(todo[i:i + chunk]))
    log(f"  {rows.path.stem} {arm}: {len(rows.done(arm))}/{len(cases)}")


# --------------------------------------------------------------------------------------------------------------------
# Tuning (D, F)
# --------------------------------------------------------------------------------------------------------------------
METHODS = {"D": {}, "DG": {}, "F": {"use_recovery": False, "use_reflection": False, "use_edits": True}}
REFLECTED = ("D", "DG")                          # the arms whose method reflects (with the reflector, behind its canary)


def tune_arm(arm: str, wf: Any, spec: FrontierSpec, *, work: Path, out: Path, reflector: Any,
             log: Callable[[str], None], initial: Any = None) -> Any:
    """Tune one arm in its own workspace (each spends the confirmation split once) and record it; return its head
    profile. ``initial`` is the profile the workflow is confirmed with (DG: every component on; otherwise G6 as
    built). A finished arm is read back, not tuned again."""
    from g6.metaharness import Profile, adapt
    from g6.metaharness.method import Method, tune

    path = out / "tuning" / f"{spec.key}-{arm}.json"
    if path.is_file():
        return Profile.model_validate(json.loads(path.read_text(encoding="utf-8"))["head_profile"])
    folder = work / f"{spec.key}-{arm}"
    if folder.exists():                             # a tuning run that died: start it again from nothing
        import shutil

        shutil.rmtree(folder)
    mh = adapt(wf, workspace=folder, clock=time.monotonic)
    confirmed = mh.confirm(APPROVER, initial=initial)
    method = Method(**METHODS[arm])
    started = time.monotonic()
    reflects = arm in REFLECTED
    if confirmed.get("completion_state") == "blocked-escalated":
        record = {"outcome": "blocked", "notes": [str(confirmed.get("message", ""))]}
        first = head = initial or Profile(routing={"answer": "local"})
    else:
        first = mh._head()[1]                        # study code: the profile the workflow was confirmed with
        run = tune(mh, method, approve=lambda decided: APPROVER, reflector=reflector if reflects else None,
                   space=spec.space)
        record = run.to_dict()
        head = mh._head()[1]                         # the active profile after the run
    _write_json(path, {"arm": arm, "method": method.model_dump(mode="json"), "method_digest": method.digest(),
                       "run": record, "initial_profile": first.model_dump(mode="json"),
                       "head_profile": head.model_dump(mode="json"), "head_digest": head.digest(),
                       "minutes": round((time.monotonic() - started) / 60, 1),
                       "reflector_calls": getattr(reflector, "calls", 0) if reflects else 0,
                       "canary_hits": list(getattr(reflector, "hits", [])) if reflects else [],
                       "canary_blind": getattr(reflector, "blind", 0) if reflects else 0})
    log(f"  {spec.key} {arm}: {record.get('outcome')}")
    return head


# --------------------------------------------------------------------------------------------------------------------
# One workflow, and the round
# --------------------------------------------------------------------------------------------------------------------
def run_workflow(key: str, *, root: Path, out: Path, work: Path, ids: Mapping[str, Sequence[str]], caller: Any,
                 reflector: Any, h_prompt: str, log: Callable[[str], None],
                 reset: Callable[[], None] | None = None, plan: Mapping[str, Any] | None = None) -> None:
    """The design's arms (``plan``, by default rounds 1 and 2's) on the dev-eval cases, each from a freshly loaded
    model (``reset``): at temperature 0 a model server's output still depends on the requests before it (round 1: one
    unrelated prompt first changed 4 of 100 MASSIVE outputs through Ollama's prompt cache). B is measured again last,
    as the control that shows it held."""
    from g6.metaharness import Profile

    plan = plan or design(1)
    spec = workflows.SPECS[key]
    cases = load(spec, root, ids)
    study = [c for split in cases.values() for c in split]
    examples = spec.examples(root, study) if spec.examples else ()
    wf = workflows.build(spec, cases, caller, examples=examples, calls=plan["calls"], mean_calls=plan["mean_calls"])
    problems = wf.problems()
    if problems:
        raise ValueError(f"{key}: the workflow is invalid: {problems}")
    rows, dev_eval, base = Rows(out, key), cases["dev_eval"], Profile(routing={"answer": "local"})
    every = Profile.model_validate({**base.model_dump(mode="json"), **spec.everything()})
    fixed = {"A": lambda cs: measure_direct(spec, caller, cs), "B": lambda cs: measure_profile(wf, base, cs, "B"),
             "H": lambda cs: measure_profile(wf, base.model_copy(update={"prompt": h_prompt}), cs, "H"),
             "G": lambda cs: measure_profile(wf, every, cs, "G")}
    for arm in (a for a in plan["arms"] if a in fixed):
        _measure(rows, arm, dev_eval, fixed[arm], log, reset)
    forbid = [snippet(c.input) for split in ("selection", "confirmation", "dev_eval") for c in cases[split]]
    for arm in (a for a in plan["arms"] if a in METHODS):
        tap = Tap(reflector, forbid, allowed_text(spec, cases["dev"])) if reflector is not None else None
        head = tune_arm(arm, wf, spec, work=work, out=out, reflector=tap, log=log,
                        initial=every if arm == "DG" else None)
        _measure(rows, arm, dev_eval, lambda cs, head=head, arm=arm: measure_profile(wf, head, cs, arm), log, reset)
    _measure(rows, REPEAT, dev_eval, lambda cs: measure_profile(wf, base, cs, REPEAT), log, reset)


def scoreboard(out: Path, *, meta: Mapping[str, Any] | None = None) -> dict:
    """The round's numbers, recomputed from the rows and tuning files alone."""
    per: dict[str, dict] = {}
    for path in sorted((out / "rows").glob("*.jsonl")):
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        per[path.stem] = {arm: {"success": sum(r["success"] for r in got), "n": len(got),
                                "released": sum(r["released"] for r in got),
                                "mean_calls": round(sum(r["model_calls"] for r in got) / len(got), 3)}
                          for arm in ARMS if (got := [r for r in rows if r["arm"] == arm])}
    ran = [arm for arm in ARMS if any(arm in w for w in per.values())]
    pooled = {arm: {"success": sum(w[arm]["success"] for w in per.values() if arm in w),
                    "n": sum(w[arm]["n"] for w in per.values() if arm in w)} for arm in ran}
    tuning = {}
    for path in sorted((out / "tuning").glob("*.json")):
        t = json.loads(path.read_text(encoding="utf-8"))
        tuning[path.stem] = {"outcome": t["run"].get("outcome"), "head_digest": t["head_digest"],
                             "method_digest": t["method_digest"], "canary_hits": len(t["canary_hits"]),
                             "minutes": t["minutes"]}
    return {"label": LABEL, **(meta or {}), "workflows": per, "pooled": pooled, "tuning": tuning,
            "repeat": repeats(out)}


def repeats(out: Path) -> dict[str, dict[str, int]]:
    """Per workflow, how many of B's outputs its second measurement repeated exactly."""
    found = {}
    for path in sorted((out / "rows").glob("*.jsonl")):
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        first = {r["case_id"]: r["output_sha256"] for r in rows if r["arm"] == "B"}
        again = {r["case_id"]: r["output_sha256"] for r in rows if r["arm"] == REPEAT}
        if again:
            found[path.stem] = {"identical": sum(first.get(c) == h for c, h in again.items()), "n": len(again)}
    return found


def readme(board: Mapping[str, Any]) -> str:
    ran = [a for a in ARMS if a in board["pooled"]]
    lines = [f"# Development round {board.get('round', '')}", "", f"**{LABEL}.** Nothing here is a claim; a registered "
             "study (V7) is the only place a result is claimed.", "",
             f"Task model `{board.get('model', '')}`; reflector `{board.get('reflector', '')}`; "
             f"method v0 digest `{str(board.get('method_digest', ''))[:12]}`. Dev-eval cases are fresh from the pool "
             "ledger; each cell is successes out of the cases measured.", "",
             "| Workflow | " + " | ".join(NAMES[a] for a in ran) + " |",
             "|---|" + "---|" * len(ran)]
    for key, arms in board["workflows"].items():
        cells = [f"{arms[a]['success']}/{arms[a]['n']}" if a in arms else "-" for a in ran]
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    pooled = board["pooled"]
    lines.append("| **pooled** | " + " | ".join(f"{pooled[a]['success']}/{pooled[a]['n']}" for a in ran) + " |")
    lines += ["", "| Tuning run | Outcome | Minutes | Canary hits |", "|---|---|---|---|"]
    lines += [f"| {k} | {t['outcome']} | {t['minutes']} | {t['canary_hits']} |" for k, t in board["tuning"].items()]
    if board.get("repeat"):
        lines += ["", "B measured again last, from a freshly loaded model: the control that each arm's measurement "
                  "starts from the same server state.", "", "| Workflow | Identical outputs |", "|---|---|"]
        lines += [f"| {k} | {r['identical']}/{r['n']} |" for k, r in board["repeat"].items()]
    return "\n".join(lines) + "\n"


def code_digest(folder: Path | None = None) -> str:
    """The sha256 of the method's code (every ``*.py`` under ``g6.metaharness`` by default), so a hand edit to the
    method shows in the round's record even where the ``Method`` data, and so its digest, is unchanged."""
    if folder is None:
        import g6.metaharness

        folder = Path(g6.metaharness.__file__).parent
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*.py")):
        h.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def model_digest(name: str) -> str:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as resp:
            tags = json.loads(resp.read())
    except OSError:
        return ""
    return next((m.get("digest", "") for m in tags.get("models", []) if m.get("name") == name), "")


def unload(name: str) -> None:
    """Unload a model from Ollama (``keep_alive: 0``), so its next call starts from an empty prompt cache."""
    body = json.dumps({"model": name, "keep_alive": 0}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def run_round(n: int, keys: Sequence[str], *, root: Path, out: Path, work: Path, caller: Any, reflector: Any,
              h_prompts: Mapping[str, str], meta: Mapping[str, Any], log: Callable[[str], None] = print,
              sizes: Mapping[str, int] = SIZES, reset: Callable[[], None] | None = None) -> dict:
    plan = design(n)
    outside = sorted(set(keys) - set(plan.get("workflows", keys)))
    if outside:
        raise ValueError(f"round {n} is designed for {', '.join(plan['workflows'])}, not {', '.join(outside)}")
    splits = draw_splits(n, keys, root=root, out=out, sizes=sizes, by_workflow=plan.get("sizes"))
    for key in keys:
        log(f"{key}: start")
        run_workflow(key, root=root, out=out, work=work, ids=splits[key], caller=caller, reflector=reflector,
                     h_prompt=h_prompts.get(key, ""), log=log, reset=reset, plan=plan)
        board = scoreboard(out, meta=meta)
        _write_json(out / "scoreboard.json", board)
        (out / "README.md").write_text(readme(board), encoding="utf-8")
    return scoreboard(out, meta=meta)


# --------------------------------------------------------------------------------------------------------------------
# The default system prompt check (development, on meta-train dev only)
# --------------------------------------------------------------------------------------------------------------------
def devcheck(keys: Sequence[str], *, root: Path, splits: Mapping[str, Mapping[str, Sequence[str]]], caller: Any
             ) -> dict[str, dict[str, int]]:
    """Per workflow, dev successes for A and for B under today's DEFAULT_SYSTEM and two alternatives."""
    from g6.metaharness import Profile

    found = {}
    for key in keys:
        spec = workflows.SPECS[key]
        cases = load(spec, root, splits[key])
        wf = workflows.build(spec, cases, caller)
        base = Profile(routing={"answer": "local"})
        found[key] = {"A": sum(r["success"] for r in measure_direct(spec, caller, cases["dev"]))}
        for name, system in SYSTEMS.items():
            found[key][f"B:{name}"] = sum(r["success"] for r in measure_profile(wf, base, cases["dev"], "B",
                                                                                system=system))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("draw", "devcheck", "run"))
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--reflector", default="gpt-oss:20b")
    ap.add_argument("--workflows", help="comma-separated; by default the round's design's, or all six")
    args = ap.parse_args(argv)
    from g6.core.llm import OllamaCaller

    plan = design(args.round)
    keys = [k for k in (args.workflows or ",".join(plan.get("workflows", workflows.ORDER))).split(",") if k]
    root, out = data.data_root(), round_dir(args.round)
    work = root / "work" / out.name
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "run.log"

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    if args.command == "draw":
        splits = draw_splits(args.round, keys, root=root, out=out)
        log(f"drew {', '.join(f'{k} {sum(map(len, s.values()))}' for k, s in splits.items())} -> {out / 'splits.json'}")
        return 0
    caller = OllamaCaller(args.model, temperature=0, seed=SEED)
    if args.command == "devcheck":
        found = devcheck(keys, root=root, splits=draw_splits(args.round, keys, root=root, out=out), caller=caller)
        _write_json(out / "default_prompt.json", {"label": LABEL, "model": args.model, "dev_successes": found})
        log(json.dumps(found))
        return 0
    reflector = OllamaCaller(args.reflector, temperature=0.7, seed=SEED, vary_seed=True)
    h_path = HERE / "h_prompts.json"
    h_prompts = json.loads(h_path.read_text(encoding="utf-8"))["prompts"] if h_path.is_file() else {}
    from g6.metaharness.method import Method

    meta = {"round": args.round, "model": args.model, "model_digest": model_digest(args.model),
            "reflector": args.reflector, "reflector_digest": model_digest(args.reflector),
            "method_digest": Method().digest(), "method_code_digest": code_digest(), "data": {k: data.DATASETS[k].licence for k in keys},
            "design": {"arms": list(plan["arms"]), "calls_per_case": plan["calls"], "mean_calls": plan["mean_calls"]}}
    run_round(args.round, keys, root=root, out=out, work=work, caller=caller, reflector=reflector,
              h_prompts=h_prompts, meta=meta, log=log, reset=lambda: unload(args.model))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
