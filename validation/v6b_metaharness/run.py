# SPDX-License-Identifier: Apache-2.0
"""Study V6b runner: the meta-harness's recovery loop on fresh cases (PROTOCOL.md). Run from ``oss/``:

    python validation/v6b_metaharness/run.py draw     --data $V6B_DATA --out docs/validation/results/V6b
    python validation/v6b_metaharness/run.py devcheck --data $V6B_DATA --splits docs/validation/results/V6b --out <dir>
                                                      --work $V6B_WORK                         (dev split only)
    python validation/v6b_metaharness/run.py freeze   (prints the SHA-256 the registry entry must hold)
    python validation/v6b_metaharness/run.py run      --data $V6B_DATA --out docs/validation/results/V6b --work $V6B_WORK
    python validation/v6b_metaharness/analyze.py      --out docs/validation/results/V6b

``run`` refuses to start unless REGISTRY.md holds the current SHA-256 of every frozen file (validation rule 9). It is
resumable: finished adaptations and per-case rows are kept, and a rerun skips them. Long runs write ``progress.log``.

Arms (PROTOCOL.md §5): A one direct call; B the default profile; B2 the A/A rerun of B; D the meta-harness (K cycles of
``RecoveryStrategy`` and ``SearchStrategy``, the gate, scripted approval, frozen); E compute-matched self-consistency
(calls) and E_tok (tokens); F random search through the same gate; H a hand-written prompt; G every option on; NULL the
suite's negative control. Item text is never written to the results folder: rows carry ids, outcomes, counts and
digests only. Model outputs are kept in the work folder for the gaming checks.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workflows import (  # noqa: E402
    HAND_PROMPTS,
    ORDER,
    SEED,
    SPECS,
    V6_SPLITS,
    Spec,
    draw,
    massive_examples,
    sizes,
    utterance_key,
)

HERE = Path(__file__).resolve().parent
OSS = HERE.parent.parent
REGISTRY = OSS / "docs" / "validation" / "REGISTRY.md"
FROZEN = ("PROTOCOL.md", "workflows.py", "run.py", "analyze.py", "plumb.py")

MODEL = "llama3.1:8b"
OLLAMA = "http://127.0.0.1:11434"    # the local Ollama by its loopback address (PROTOCOL.md §11 item 6)
K_CYCLES = 3
DEV_BUDGET = 4000                    # model calls one propose() or diagnose() may spend on dev
APPROVER = "v6b-scripted-approval (study script, not a person)"
RANDOM_PROPOSER = "v6b-random-search"
MAX_VOTES = 8                        # the most samples E or E_tok draws per case
COUNTS = ("calls_tap", "prompt_tokens", "output_tokens", "prompt_chars", "output_chars")


def sha256_text(path: Path) -> str:
    """SHA-256 of a text file with LF line endings (the registry's convention)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_hashes() -> dict[str, str]:
    return {name: sha256_text(HERE / name) for name in FROZEN}


def log(out: Path, message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}"
    print(line, flush=True)
    with (out / "progress.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --------------------------------------------------------------------------------------------------------------------
# The model, behind a tap that counts calls and tokens by phase and watches for split leaks.
# --------------------------------------------------------------------------------------------------------------------
def counting_caller(model: str, base_url: str) -> Any:
    """``g6.core.llm.OllamaCaller`` whose ``complete`` is the library's own (same endpoint, same request body), and which
    also keeps the token counts in the same reply (``prompt_eval_count``, ``eval_count``)."""
    from g6.core import llm

    class CountingOllama(llm.OllamaCaller):
        last: tuple[int, int] = (0, 0)

        def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
            llm._start_model_call()
            out = llm._post_json(f"{self.base_url}/api/chat", {
                "model": self.model, "stream": False, "options": {"num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }, {}, self.timeout)
            self.last = (int(out.get("prompt_eval_count") or 0), int(out.get("eval_count") or 0))
            return str((out.get("message") or {}).get("content") or "")

    return CountingOllama(model, base_url=base_url)


class Tap:
    """Wraps the model caller. ``phase`` names what is running; ``forbid`` maps a split name to case snippets that must
    not appear in any prompt while that split is forbidden (the leakage canary). ``total`` counts calls, tokens and
    characters, so a measurement can attribute them to each case."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.phase = inner, "init"
        self.start_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{random.getrandbits(32):08x}"
        self.calls: Counter[str] = Counter()
        self.tokens: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.forbid: dict[str, list[str]] = {}
        self.hits: list[dict[str, str]] = []
        self.total = dict.fromkeys(COUNTS, 0)

    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        self.calls[self.phase] += 1
        self.total["calls_tap"] += 1
        self.total["prompt_chars"] += len(system) + len(user)
        for split, snippets in self.forbid.items():
            for snip in snippets:
                if appears(snip, user) or appears(snip, system):
                    self.hits.append({"phase": self.phase, "split": split,
                                      "snippet_sha256": hashlib.sha256(snip.encode()).hexdigest()})
                    break
        try:
            out = self.inner.complete(system, user, max_tokens=max_tokens)
        except Exception:
            self.errors[self.phase] += 1
            raise
        prompt_tokens, output_tokens = getattr(self.inner, "last", (0, 0))
        self.total["prompt_tokens"] += prompt_tokens
        self.total["output_tokens"] += output_tokens
        self.total["output_chars"] += len(out)
        self.tokens[self.phase] += prompt_tokens + output_tokens
        return out

    def snapshot(self) -> dict[str, int]:
        return dict(self.total)


def case_text(spec_key: str, text: str) -> str:
    """The case-specific part of an input (shared label lists and abstracts left out)."""
    if spec_key == "massive":
        return text.split("Utterance:", 1)[-1].split("\n\nIntent labels:", 1)[0].strip()
    if spec_key == "scifact":
        return text.split("CLAIM:", 1)[-1].split("\n\nABSTRACT:", 1)[0].strip()
    if spec_key == "mbpp":
        return text.split("\n\nYour code should pass", 1)[0].strip()
    return text.strip()


PROMPT_FIELD = {"gsm8k": "Problem: ", "massive": "Utterance: ", "scifact": "CLAIM: ", "mbpp": ""}


def appears(snip: str, text: str) -> bool:
    """``snip`` occurs in ``text`` as a whole input field: followed by a line break or the end of the text."""
    return bool(snip) and ((snip + "\n") in text or text.rstrip().endswith(snip))


def in_view(bare: str, view_text: str) -> bool:
    """The case-specific text inside a proposer view's JSON text (strings escaped, ``ensure_ascii=False``)."""
    return bool(bare) and json.dumps(bare, ensure_ascii=False)[1:-1] in view_text


def ollama_digest(model: str) -> str:
    import urllib.request

    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30) as resp:  # noqa: S310 - the local Ollama only
        tags = json.loads(resp.read().decode("utf-8"))
    return next((m["digest"] for m in tags.get("models", []) if m["name"] == model), "")


# --------------------------------------------------------------------------------------------------------------------
# Splits and workflows.
# --------------------------------------------------------------------------------------------------------------------
def load_splits(folder: Path) -> dict[str, dict[str, list[str]]]:
    return json.loads((folder / "splits.json").read_text(encoding="utf-8"))["splits"]


def cases_for(spec: Spec, data: Path, ids: list[str]):
    return spec.cases(data, ids)


def strip_meta(cases):
    from dataclasses import replace

    return tuple(replace(c, meta={}) for c in cases)


def objectives():
    from g6.metaharness import Objective

    return (Objective("success", "max", unit="share of held-out cases"),
            Objective("model_calls", "bound", bound=4.0, unit="calls per case", provenance="specified"),
            Objective("wall_clock_s", "bound", bound=120.0, unit="seconds per case", provenance="specified"))


def policy():
    from g6.metaharness import EvidencePolicy

    return EvidencePolicy(min_cases=30, min_discordant=5, alpha=0.05, max_evaluations=6, call_tolerance=1.0,
                          max_eval_model_calls=10_000)


def build_workflow(spec: Spec, dev, held_out, caller, examples=()):
    from g6.metaharness import Model, Suite, Workflow

    return Workflow(name=spec.name, task=spec.task, checks=spec.checks, oracle=spec.oracle,
                    suite=Suite(dev=strip_meta(dev), held_out=strip_meta(held_out),
                                provenance={"dev": "independent", "held_out": "independent"}),
                    models={"local": Model(caller, max_calls_per_run=8)}, objectives=objectives(), policy=policy(),
                    optional_checks=dict(spec.optional_checks), pipeline_steps=dict(spec.pipeline_steps),
                    accept_risky=APPROVER, examples=strip_meta(examples))


def examples_for(spec: Spec, data: Path, cases) -> tuple[tuple, dict | None]:
    """MASSIVE's declared labelled examples, minus any repeating a study case's utterance; () elsewhere (the exemplar
    bank is then the dev split, the library's default)."""
    if not spec.declares_examples:
        return (), None
    exclude = {utterance_key(case_text(spec.key, c.input)) for c in cases}
    return massive_examples(data, exclude)


def base_profile():
    from g6.metaharness import Profile

    return Profile(routing={"answer": "local"})


def with_fields(profile, **fields):
    from g6.metaharness import Profile

    return Profile.model_validate({**profile.model_dump(mode="json"), **fields})


# --------------------------------------------------------------------------------------------------------------------
# Measuring one profile on cases (the evaluator's own machinery: g6.metaharness.evaluate.run_case).
# --------------------------------------------------------------------------------------------------------------------
class Outputs:
    """Model outputs, kept in the work folder only (gaming checks)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(workflow: str, arm: str, rec: Any) -> dict[str, Any]:
    return {"workflow": workflow, "arm": arm, "case_id": rec.case_id, "released": rec.released, "oracle": rec.oracle,
            "success": rec.success, "false_accept": rec.false_accept, "model_calls": rec.model_calls,
            "wall_clock_s": rec.wall_clock_s, "completion_state": rec.completion_state, "error": rec.error,
            "output_sha256": rec.output_sha256}


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {k: after[k] - before[k] for k in COUNTS}


def measure_profile(wf, profile, cases, *, arm: str, workflow: str, tap: Tap | None = None, factory=None,
                    outputs: Outputs | None = None, repeat: int = 1, norm=None) -> list[dict[str, Any]]:
    from g6.metaharness.evaluate import run_case
    from g6.metaharness.executor import context_for, model_call_factory, null_factory
    from g6.reasoning import Candidate, weighted_vote

    make = factory or model_call_factory()
    rows = []
    for case in cases:
        contexts: list[Any] = []
        kept: dict[str, Any] = {"output": "", "errors": []}

        def execute(contract):
            if arm == "NULL":
                ctx = context_for(wf, profile)
                contexts.append(ctx)
                return null_factory(ctx)(contract)
            outs = []
            for _ in range(repeat):
                ctx = context_for(wf, profile)
                contexts.append(ctx)
                try:
                    outs.append(make(ctx)(contract))
                except Exception as exc:  # noqa: BLE001 - a failed sample abstains (E); a single failure is raised
                    kept["errors"].append(f"{type(exc).__name__}: {exc}"[:300])
                    if repeat == 1:
                        raise
                    outs.append("")
            if repeat == 1:
                out = outs[0]
            else:   # E: g6.reasoning.weighted_vote over the samples, answers compared by the workflow's normaliser
                vote = weighted_vote([Candidate(o) for o in outs],
                                     equivalent=lambda a, b: norm(a) is not None and norm(a) == norm(b))
                out = vote.winner if vote.winner is not None else next((o for o in outs if o.strip()), "")
            kept["output"] = out
            return out

        before = tap.snapshot() if tap is not None else dict.fromkeys(COUNTS, 0)
        rec = run_case(wf, execute, profile, case, arm, calls=lambda: sum(c.calls for c in contexts))
        after = tap.snapshot() if tap is not None else dict.fromkeys(COUNTS, 0)
        row = _row(workflow, arm, rec) | _delta(before, after)
        rows.append(row)
        if outputs is not None and arm != "NULL":
            outputs.add({**row, "output": kept["output"], "sample_errors": kept["errors"]})
    return rows


def measure_direct(spec: Spec, tap: Tap, cases, *, workflow: str, outputs: Outputs | None = None) -> list[dict]:
    """Arm A: one direct call per case with the default system prompt; no harness, so everything returned is released."""
    from g6.metaharness.executor import DEFAULT_SYSTEM

    rows = []
    for case in cases:
        start = time.monotonic()
        before = tap.snapshot()
        try:
            out, released, error = tap.complete(DEFAULT_SYSTEM, spec.task.replace("{input}", case.input)), True, ""
        except Exception as exc:  # noqa: BLE001 - a failed call releases nothing
            out, released, error = "", False, f"{type(exc).__name__}"
        verdict = "unknown"
        if released:
            try:
                verdict = "pass" if spec.oracle(out, case.expected) is True else "fail"
            except Exception:  # noqa: BLE001
                verdict = "unknown"
        row = {"workflow": workflow, "arm": "A", "case_id": case.case_id, "released": released, "oracle": verdict,
               "success": released and verdict == "pass", "false_accept": released and verdict == "fail",
               "model_calls": 1, "wall_clock_s": round(time.monotonic() - start, 6),
               "completion_state": "direct", "error": error,
               "output_sha256": hashlib.sha256(out.encode("utf-8")).hexdigest()} | _delta(before, tap.snapshot())
        rows.append(row)
        if outputs is not None:
            outputs.add({**row, "output": out})
    return rows


class RowStore:
    """Per-arm JSONL rows in the results folder; a rerun skips cases already measured."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def path(self, arm: str) -> Path:
        return self.folder / f"{arm}.jsonl"

    def rows(self, arm: str) -> list[dict]:
        p = self.path(arm)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def done(self, arm: str) -> set[str]:
        return {r["case_id"] for r in self.rows(arm)}

    def add(self, arm: str, rows: list[dict]) -> None:
        with self.path(arm).open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")


def measure_arm(store: RowStore, arm: str, cases, fn, out: Path, *, chunk: int = 10) -> list[dict]:
    """Measure the cases not yet in the store, ``chunk`` at a time, so a crash loses at most one chunk."""
    todo = [c for c in cases if c.case_id not in store.done(arm)]
    for i in range(0, len(todo), chunk):
        store.add(arm, fn(todo[i:i + chunk]))
        log(out, f"  {store.folder.name}/{arm}: {len(store.done(arm))}/{len(cases)}")
    by_id = {r["case_id"]: r for r in store.rows(arm)}
    return [by_id[c.case_id] for c in cases]


# --------------------------------------------------------------------------------------------------------------------
# The meta-harness arms (D, F).
# --------------------------------------------------------------------------------------------------------------------
class Recorder:
    """Keeps what a strategy was shown (the leakage canary reads it) and, for the recovery proposer, the diagnosis
    ``g6.metaharness.recovery.diagnose`` makes of that same view (no model call)."""

    def __init__(self, inner: Any, *, diagnose_with: Any = None) -> None:
        self.inner, self.name, self.seen, self.diagnoses = inner, inner.name, [], []
        self.diagnose_with = diagnose_with

    def propose(self, view, runner):
        self.seen.append(view.text())
        if self.diagnose_with is not None:
            from g6.metaharness import recovery

            wf, mh = self.diagnose_with
            self.diagnoses.append(recovery.diagnose(view, wf, mh.ledger.rows("decision")).to_dict())
        return self.inner.propose(view, runner)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _report_summary(report) -> dict[str, Any]:
    return {"n": report.n, "success": report.success, "false_accepts": report.false_accepts,
            "estimates": report.estimates, "negative_control": report.negative_control, "control": report.control,
            "power": report.power, "partial": report.partial, "stopped": report.stopped,
            "model_calls_used": report.model_calls_used, "reuse_index": report.reuse_index, "notes": report.notes,
            "base_success": sum(1 for c in report.cases if c["base"]["success"]),
            "candidate_success": sum(1 for c in report.cases if c["candidate"]["success"]),
            "digest": report.digest()}


def _gate_cycle(mh, proposals, cycle_log: dict) -> None:
    """Evaluate each proposal on the gate split, decide, and apply the scripted approval rule (PROTOCOL.md §5 D)."""
    decided = []
    for proposal in proposals:
        ev = mh.evaluate(proposal)
        entry = {"candidate_digest": proposal.candidate_digest, "strategy": proposal.strategy,
                 "proposer": proposal.proposer, "diff": _jsonable(proposal.diff),
                 "evaluate_code": ev["warning_card"]["code"]}
        if "report" not in ev:
            entry["refused"] = ev.get("message", "")
            entry["recovery"] = _jsonable(ev.get("recovery"))
            cycle_log["evaluations"].append(entry)
            continue
        report = ev["report"]
        entry["report"] = _report_summary(report)
        dec = mh.decide(report, proposal)
        decision = dec.get("decision")
        entry["decision"] = ({"verdict": decision.verdict, "code": decision.code, "reasons": list(decision.reasons),
                              "checks": _jsonable(decision.checks)}
                             if decision is not None else {"verdict": "none", "code": dec["warning_card"]["code"]})
        if dec.get("recovery"):
            entry["recovery"] = _jsonable(dec["recovery"])
        cycle_log["evaluations"].append(entry)
        if decision is not None and decision.verdict == "recommend":
            s = report.success
            decided.append((s["wins"] - s["losses"], -len(decided), decision, proposal))
    if decided:
        decided.sort(key=lambda t: (t[0], t[1]), reverse=True)
        _, _, decision, proposal = decided[0]
        act = mh.activate(decision, accepted_by=APPROVER)
        cycle_log["activation"] = {"code": act["warning_card"]["code"], "head_version": act.get("head_version"),
                                   "candidate_digest": proposal.candidate_digest, "strategy": proposal.strategy,
                                   "diff": _jsonable(proposal.diff),
                                   "rule": "scripted: approve the recommended candidate with the most net wins on the "
                                           "gate split (ties: the first proposed)"}


def run_meta(spec: Spec, wf, tap: Tap, work: Path, *, forbid_propose: dict, forbid_gate: dict, out: Path) -> dict:
    """Arm D: K cycles; each runs mh.diagnose(), then RecoveryStrategy and SearchStrategy on dev (one new candidate
    each), every candidate through the meta-gate, and the scripted approval."""
    from g6.metaharness import RecoveryStrategy, SearchStrategy, adapt, judging_sha256
    from g6.metaharness.executor import model_call_factory

    work.mkdir(parents=True, exist_ok=True)
    mh = adapt(wf, model_call_factory(), workspace=work / "mh")
    result: dict[str, Any] = {"adapt_code": mh.envelope["warning_card"]["code"], "cycles": [], "seen": []}
    if mh.envelope["completion_state"] == "blocked-escalated":
        result["problem"] = mh.envelope["message"]
        return result
    conf = mh.confirm(APPROVER)
    result["confirm"] = {k: conf.get(k) for k in ("code", "workflow_digest", "judging_sha256", "held_out_digest",
                                                  "accept_risky")}
    recorders = []
    for cycle in range(1, K_CYCLES + 1):
        log(out, f"  D cycle {cycle}")
        cycle_log: dict[str, Any] = {"cycle": cycle, "offers": [], "evaluations": []}
        tap.phase, tap.forbid = f"D-diagnose-{cycle}", forbid_propose
        diag = mh.diagnose(budget=DEV_BUDGET)
        cycle_log["diagnose"] = {"code": diag["warning_card"]["code"], "summary": diag.get("message", ""),
                                 "recovery": _jsonable(diag.get("recovery")), "dev_success": diag.get("dev_success"),
                                 "dev_cases": diag.get("dev_cases"), "dev_calls_used": diag.get("dev_calls_used")}
        proposals = []
        strategies = (("recovery", RecoveryStrategy(wf)),
                      ("search", SearchStrategy(spec.space(), max_candidates=3, max_profiles=spec.space_size())))
        for phase, strategy in strategies:
            tap.phase = f"D-{phase}-{cycle}"
            rec = Recorder(strategy, diagnose_with=(wf, mh) if phase == "recovery" else None)
            recorders.append(rec)
            got = mh.propose(rec, budget=DEV_BUDGET, max_candidates=1)
            offer = {"strategy": strategy.name, "code": got["warning_card"]["code"], "message": got.get("message", ""),
                     "dev_success": got.get("dev_success"), "dev_calls_used": got.get("dev_calls_used"),
                     "candidates": [p.candidate_digest for p in got.get("proposals", [])],
                     "diffs": [_jsonable(p.diff) for p in got.get("proposals", [])],
                     "refused": got.get("refused", []), "recovery": _jsonable(got.get("recovery"))}
            if rec.diagnoses:
                offer["diagnosis_of_view"] = rec.diagnoses[-1]
            cycle_log["offers"].append(offer)
            proposals += list(got.get("proposals", []))
        tap.phase, tap.forbid = f"D-gate-{cycle}", forbid_gate
        _gate_cycle(mh, proposals, cycle_log)
        version, head = mh.store.head()
        cycle_log["head_version"], cycle_log["head_profile"] = version, head.model_dump(mode="json")
        cycle_log["head_digest"] = head.digest()
        result["cycles"].append(cycle_log)
    result["seen"] = [s for r in recorders for s in r.seen]
    result["report"] = _jsonable(mh.report())
    result["inspect_versions"] = _jsonable(mh.store.versions())
    result["judging_sha256_end"] = judging_sha256()
    return result


def run_random(spec: Spec, wf, tap: Tap, work: Path, per_cycle: list[int], *, forbid_gate: dict, out: Path) -> dict:
    """Arm F: the same number of candidate evaluations per cycle as D, drawn at random from the same declared options,
    through the same gate and the same scripted approval. It reads no dev case."""
    from g6.metaharness import adapt
    from g6.metaharness.executor import model_call_factory

    work.mkdir(parents=True, exist_ok=True)
    mh = adapt(wf, model_call_factory(), workspace=work / "mh")
    result: dict[str, Any] = {"adapt_code": mh.envelope["warning_card"]["code"], "cycles": [], "per_cycle": per_cycle}
    if mh.envelope["completion_state"] == "blocked-escalated":
        result["problem"] = mh.envelope["message"]
        return result
    mh.confirm(APPROVER)
    rng = random.Random(f"V6b:{SEED}:{spec.name}:F")
    space = spec.space()
    keys = sorted(space)
    combos = [dict(zip(keys, values)) for values in itertools.product(*(space[k] for k in keys))]
    tried: set[str] = set()
    for cycle, count in enumerate(per_cycle, start=1):
        log(out, f"  F cycle {cycle} ({count} candidates)")
        cycle_log: dict[str, Any] = {"cycle": cycle, "evaluations": []}
        tap.phase, tap.forbid = f"F-gate-{cycle}", forbid_gate
        _, base = mh.store.head()
        base_json = base.model_dump(mode="json")
        pool = [c for c in combos if {**base_json, **c} != base_json and json.dumps({**base_json, **c},
                                                                                    sort_keys=True) not in tried]
        picks = rng.sample(pool, min(count, len(pool)))
        proposals = []
        for pick in picks:
            data = {**base_json, **pick}
            tried.add(json.dumps(data, sort_keys=True))
            made = mh.candidate(data, proposer=RANDOM_PROPOSER, strategy="random")
            if "proposal" in made:
                proposals.append(made["proposal"])
            else:
                cycle_log["evaluations"].append({"strategy": "random", "diff": pick, "refused": made.get("message"),
                                                 "evaluate_code": made["warning_card"]["code"]})
        _gate_cycle(mh, proposals, cycle_log)
        version, head = mh.store.head()
        cycle_log["head_version"], cycle_log["head_profile"] = version, head.model_dump(mode="json")
        cycle_log["head_digest"] = head.digest()
        result["cycles"].append(cycle_log)
    result["report"] = _jsonable(mh.report())
    result["inspect_versions"] = _jsonable(mh.store.versions())
    return result


# --------------------------------------------------------------------------------------------------------------------
# Commands.
# --------------------------------------------------------------------------------------------------------------------
def cmd_draw(args) -> int:
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pools = {key: SPECS[key].pool(data) for key in ORDER}
    record = {"seed": SEED, "sizes": {k: sizes(k) for k in ORDER},
              "rule": "workflows.draw: every V6 id removed, the sorted rest shuffled by the seed, cut dev|gate|eval",
              "v6_splits_sha256": sha256_text(V6_SPLITS),
              "splits": {k: draw(k, pools[k]) for k in ORDER},
              "pool_sizes": {k: len(pools[k]) for k in ORDER},
              "fresh_pool_sizes": {k: len(set(pools[k]) - _v6(k)) for k in ORDER}}
    (out / "splits.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(json.dumps(record["fresh_pool_sizes"]), sha256_text(out / "splits.json"))
    return 0


def _v6(key: str) -> set[str]:
    from workflows import v6_ids

    return v6_ids(key)


def cmd_freeze(args) -> int:
    for name, digest in frozen_hashes().items():
        print(f"oss/validation/v6b_metaharness/{name} {digest}")
    return 0


def _registered() -> list[str]:
    text = REGISTRY.read_text(encoding="utf-8") if REGISTRY.exists() else ""
    section = text.split("## V6b", 1)[1] if "## V6b" in text else ""
    return [name for name, digest in frozen_hashes().items() if digest not in section]


def cmd_devcheck(args) -> int:
    """Development on the dev split only: A, B, a B rerun and the recovery remedies on the 20 dev cases, the library's
    diagnosis and RecoveryStrategy's offer (no evaluation), with calls, tokens and seconds."""
    from g6.metaharness import RecoveryStrategy, adapt
    from g6.metaharness.executor import model_call_factory
    from g6.metaharness.propose import WORKING_PROMPT

    data, out, work = Path(args.data), Path(args.out), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("G6_HOME", str(work / "g6home"))
    splits = load_splits(Path(args.splits))
    tap = Tap(counting_caller(args.model, OLLAMA))
    remedies = {"gsm8k": {"R_working": {"prompt": WORKING_PROMPT}, "X8": {"exemplars": 8}},
                "massive": {"X8": {"exemplars": 8}},
                "scifact": {"SL_support": {"pipeline": ["second_look_support"]}, "X8": {"exemplars": 8}},
                "mbpp": {"repair3": {"pipeline": ["repair_with_tests"]}, "repair1": {"pipeline": ["repair1"]}}}
    summary: dict[str, Any] = {}
    for key in args.workflows.split(","):
        spec = SPECS[key]
        dev = cases_for(spec, data, splits[key]["dev"])
        dummy = cases_for(spec, data, plumbing_ids(data, key, splits, 30))   # never run: adapt needs a held-out split
        examples, counts = examples_for(spec, data, [*dev, *dummy])
        wf = build_workflow(spec, dev, dummy, tap, examples)
        outputs = Outputs(work / f"devcheck-{key}.jsonl")
        base = base_profile()
        arms = {"A": lambda cs: measure_direct(spec, tap, cs, workflow=key, outputs=outputs),
                "B": lambda cs: measure_profile(wf, base, cs, arm="B", workflow=key, tap=tap, outputs=outputs),
                "B2": lambda cs: measure_profile(wf, base, cs, arm="B2", workflow=key, tap=tap, outputs=outputs)}
        for name, fields in remedies[key].items():
            arms[name] = (lambda p, n: lambda cs: measure_profile(wf, p, cs, arm=n, workflow=key, tap=tap,
                                                                  outputs=outputs))(with_fields(base, **fields), name)
        summary[key] = {"examples": counts}
        for arm, fn in arms.items():
            tap.phase = f"devcheck-{key}-{arm}"
            start = time.monotonic()
            rows = fn(dev)
            n = len(rows)
            summary[key][arm] = {"n": n, "success": sum(r["success"] for r in rows),
                                 "released": sum(r["released"] for r in rows),
                                 "false_accepts": sum(r["false_accept"] for r in rows),
                                 **{f"{k}_mean": round(sum(r[k] for r in rows) / n, 3)
                                    for k in ("model_calls", "wall_clock_s", *COUNTS)},
                                 "seconds": round(time.monotonic() - start, 1),
                                 "blocked_codes": dict(Counter(r["error"] for r in rows if not r["released"])),
                                 "per_case": {r["case_id"]: r["success"] for r in rows}}
            log(out, f"devcheck {key} {arm}: {summary[key][arm]['success']}/{n} released "
                     f"{summary[key][arm]['released']} calls {summary[key][arm]['model_calls_mean']} "
                     f"tokens {summary[key][arm]['prompt_tokens_mean'] + summary[key][arm]['output_tokens_mean']:.0f} "
                     f"{summary[key][arm]['seconds']}s")
        tap.phase = f"devcheck-{key}-recovery"
        mh = adapt(wf, model_call_factory(), workspace=work / f"devcheck-{key}-mh")
        mh.confirm(APPROVER)
        diag = mh.diagnose(budget=DEV_BUDGET)
        got = mh.propose(RecoveryStrategy(wf), budget=DEV_BUDGET, max_candidates=3)
        summary[key]["diagnose"] = {"summary": diag.get("message"), "causes": [
            {"code": c["code"], "cases": c["cases"]} for c in (diag.get("recovery") or {}).get("causes", [])],
            "remedies": [(r["who"], r["component"], r["action"][:80]) for r in
                         (diag.get("recovery") or {}).get("remedies", [])]}
        summary[key]["recovery_offers"] = {"code": got["warning_card"]["code"],
                                           "diffs": [_jsonable(p.diff) for p in got.get("proposals", [])]}
        log(out, f"devcheck {key} recovery offers: {summary[key]['recovery_offers']}")
    summary["calls_by_phase"] = dict(tap.calls)
    (out / "devcheck.json").write_text(json.dumps({"model": args.model, "summary": summary}, indent=1),
                                       encoding="utf-8")
    return 0


def plumbing_ids(data: Path, key: str, splits: dict, n: int) -> list[str]:
    """Pool ids outside every drawn id (V6's and V6b's), in a seeded order: the only items plumbing reads."""
    drawn = {i for split in splits[key].values() for i in split} | _v6(key)
    rest = sorted(set(SPECS[key].pool(data)) - drawn)
    random.Random(f"V6b:{SEED}:{key}:plumbing").shuffle(rest)
    return rest[:n]


def oracle_selftest(spec: Spec, cases) -> dict[str, Any]:
    """The oracle on each case's own reference answer (it must pass) and on an empty answer (it must fail)."""
    ref_fail, empty_pass = [], []
    for c in cases:
        try:
            if spec.oracle(spec.reference(c), c.expected) is not True:
                ref_fail.append(c.case_id)
        except Exception:  # noqa: BLE001
            ref_fail.append(c.case_id)
        try:
            if spec.oracle("", c.expected) is True:
                empty_pass.append(c.case_id)
        except Exception:  # noqa: BLE001
            pass
    return {"n": len(cases), "reference_failed": ref_fail, "empty_passed": empty_pass}


def cmd_run(args) -> int:
    missing = _registered()
    if missing:
        print(f"refused: REGISTRY.md has no V6b entry holding the current SHA-256 of {missing}")
        return 2
    data, out, work = Path(args.data), Path(args.out), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.environ["G6_HOME"] = str(work / "g6home")
    splits = load_splits(out)
    tap = Tap(counting_caller(args.model, OLLAMA))
    record_start(out, data, args.model, args.workflows, digest=ollama_digest(args.model))
    for key in args.workflows.split(","):
        spec = SPECS[key]
        log(out, f"workflow {key}: start")
        try:
            run_workflow(spec, key, data, out, work / key, splits[key], tap)
        except Exception as exc:  # noqa: BLE001 - one workflow's crash is logged; the next one runs
            crash = work / f"crash-{key}-{time.strftime('%Y%m%dT%H%M%S')}.txt"   # the traceback names local paths
            crash.write_text(traceback.format_exc(), encoding="utf-8")
            log(out, f"workflow {key}: CRASH {type(exc).__name__} (traceback in the work folder: {crash.name})")
        write_tap(out, tap)
    record_end(out, data, digest=ollama_digest(args.model))
    return 0


def record_start(out: Path, data: Path, model: str, workflows: str, *, digest: str) -> None:
    """Pins the frozen files, the G6 source, the judging code, the data files and the model digest at a start."""
    meta_path = out / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"starts": []}
    meta["starts"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model, "model_digest": digest,
                           "frozen": frozen_hashes(), "g6_manifest_sha256": g6_manifest(),
                           "judging_sha256": _judging(), "data": data_hashes(data),
                           "v6_splits_sha256": sha256_text(V6_SPLITS), "workflows": workflows})
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def record_end(out: Path, data: Path, *, digest: str) -> None:
    """The same pins again at the end of the start that is running."""
    meta_path = out / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["starts"][-1].update({"ended": time.strftime("%Y-%m-%dT%H:%M:%S"), "model_digest_end": digest,
                               "frozen_end": frozen_hashes(), "g6_manifest_sha256_end": g6_manifest(),
                               "judging_sha256_end": _judging(), "data_end": data_hashes(data)})
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def write_tap(out: Path, tap: Tap) -> None:
    """Calls and tokens by phase, errors and leak hits, merged with what an earlier start recorded."""
    path = out / "tap.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    starts = [s for s in old.get("starts", []) if s.get("id") != tap.start_id]
    starts.append({"id": tap.start_id, "calls_by_phase": dict(tap.calls), "tokens_by_phase": dict(tap.tokens),
                   "errors": dict(tap.errors), "leak_hits": tap.hits})
    merged = {"starts": starts, "calls_by_phase": dict(sum((Counter(s["calls_by_phase"]) for s in starts), Counter())),
              "tokens_by_phase": dict(sum((Counter(s["tokens_by_phase"]) for s in starts), Counter())),
              "errors": dict(sum((Counter(s["errors"]) for s in starts), Counter())),
              "leak_hits": [h for s in starts for h in s["leak_hits"]]}
    path.write_text(json.dumps(merged, indent=1), encoding="utf-8")


def _judging() -> str:
    from g6.metaharness import judging_sha256

    return judging_sha256()


def g6_manifest() -> str:
    """``g6.integrity.manifest_sha256`` over every ``oss/src/g6/**/*.py``, each file hashed with LF line endings."""
    import g6
    from g6.integrity import manifest_sha256

    root = Path(g6.__file__).resolve().parent
    return manifest_sha256({p.relative_to(root).as_posix(): sha256_text(p) for p in sorted(root.glob("**/*.py"))
                            if "__pycache__" not in p.parts})


DATA_FILES = ("gsm8k/test.jsonl", "mbpp/mbpp.jsonl", "massive/amazon-massive-dataset-1.1.tar.gz",
              "massive/extracted/1.1/data/en-US.jsonl", "scifact/data.tar.gz",
              "scifact/extracted/data/claims_train.jsonl", "scifact/extracted/data/corpus.jsonl")


def data_hashes(data: Path) -> dict[str, str]:
    return {name: sha256_file(data / name) for name in DATA_FILES if (data / name).exists()}


# What a gaming check looks for in generated code (validation rule 8): reaching files, processes, the network or the
# interpreter's import machinery, or naming the files the oracle writes.
GAMING = {"file access": r"\bopen\s*\(|pathlib|os\.(remove|unlink|rename|replace|walk|listdir|system|popen|chdir)",
          "process": r"\bsubprocess\b|os\.exec|os\.spawn|multiprocessing",
          "network": r"\bsocket\b|urllib|requests\b|http\.client",
          "import machinery": r"__import__|importlib|sys\.modules|builtins|globals\(\)\s*\[",
          "oracle files": r"solution\.py|_g6_script|sys\.argv|sys\.exit|os\._exit"}


def gaming_scan(outputs_path: Path, workflow: str) -> dict[str, Any]:
    import re

    found: dict[str, list[str]] = {k: [] for k in GAMING}
    n = 0
    if outputs_path.exists():
        for line in outputs_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("workflow") != workflow:
                continue
            n += 1
            for kind, pattern in GAMING.items():
                if re.search(pattern, row.get("output") or ""):
                    found[kind].append(f"{row['arm']}:{row['case_id']}")
    return {"outputs_scanned": n, "matches": {k: sorted(set(v)) for k, v in found.items()}}


def _same_as(profile, other) -> bool:
    return profile.digest() == other.digest()


def _fresh(folder: Path, out: Path) -> int:
    """An adaptation that did not finish left a workspace whose ledger counts its gate reads: move it aside (kept for
    audit) so the rerun starts clean, and return how many earlier attempts there were."""
    earlier = sorted(folder.parent.glob(folder.name + "-abandoned-*"))
    if folder.exists():
        target = folder.parent / f"{folder.name}-abandoned-{len(earlier) + 1}"
        folder.rename(target)
        log(out, f"  restart: {folder.name} did not finish before; moved aside to {target.name}")
        earlier.append(target)
    return len(earlier)


def _mean(rows: list[dict], key: str) -> float:
    return sum(r[key] for r in rows) / max(1, len(rows))


def run_workflow(spec: Spec, key: str, data: Path, out: Path, work: Path, split: dict, tap: Tap) -> None:
    from g6.metaharness import Profile

    rows_dir = out / "rows" / key
    store = RowStore(rows_dir)
    dev = cases_for(spec, data, split["dev"])
    gate = cases_for(spec, data, split["gate"])
    held = cases_for(spec, data, split["eval"])
    examples, example_counts = examples_for(spec, data, [*dev, *gate, *held])
    wf = build_workflow(spec, dev, gate, tap, examples)
    adapt_dir = out / "adaptation"
    adapt_dir.mkdir(parents=True, exist_ok=True)
    outputs = Outputs(work / "outputs.jsonl")
    work.mkdir(parents=True, exist_ok=True)

    # the leakage canary: case-specific text of each split, minus any that also occurs in an earlier split
    dev_bare = {case_text(key, c.input) for c in dev}
    gate_bare = sorted({case_text(key, c.input) for c in gate} - dev_bare)
    eval_bare = sorted({case_text(key, c.input) for c in held} - dev_bare - set(gate_bare))
    dupes = {"gate_in_dev": len({case_text(key, c.input) for c in gate} & dev_bare),
             "eval_in_dev_or_gate": len({case_text(key, c.input) for c in held} & (dev_bare | set(gate_bare)))}
    gate_snips = [PROMPT_FIELD[key] + t for t in gate_bare]
    eval_snips = [PROMPT_FIELD[key] + t for t in eval_bare]
    forbid_propose = {"gate": gate_snips, "eval": eval_snips}
    forbid_gate = {"eval": eval_snips}

    selftest_path = adapt_dir / f"{key}-oracle_selftest.json"
    if not selftest_path.exists():
        study = {utterance_key(t) for t in (*dev_bare, *gate_bare, *eval_bare)}
        bank = {"examples": example_counts,
                "examples_repeating_a_study_case": sum(utterance_key(case_text(key, e.input)) in study
                                                       for e in examples)} if examples else None
        selftest_path.write_text(json.dumps({"gate": oracle_selftest(spec, gate), "eval": oracle_selftest(spec, held),
                                             "duplicates": dupes, "example_bank": bank}, indent=1), encoding="utf-8")

    # D
    d_path = adapt_dir / f"{key}-D.json"
    if not d_path.exists():
        log(out, f"{key}: D (meta-harness) adaptation")
        restarts = _fresh(work / "D", out)
        d = run_meta(spec, wf, tap, work / "D", forbid_propose=forbid_propose, forbid_gate=forbid_gate, out=out)
        d["restarts"] = restarts
        seen = d.pop("seen", [])
        d["canary"] = {"views": len(seen),
                       "gate_snippets_in_views": sum(any(in_view(s, v) for v in seen) for s in gate_bare),
                       "eval_snippets_in_views": sum(any(in_view(s, v) for v in seen) for s in eval_bare),
                       "dev_snippets_in_views": sum(any(in_view(s, v) for v in seen) for s in dev_bare),
                       "dev_cases": len(dev_bare)}
        d_path.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        write_tap(out, tap)
    d = json.loads(d_path.read_text(encoding="utf-8"))
    base = base_profile()
    d_heads = [Profile.model_validate(c["head_profile"]) for c in d.get("cycles", [])]
    d_final = d_heads[-1] if d_heads else base
    per_cycle = [len(c["evaluations"]) for c in d.get("cycles", [])]

    # F
    f_path = adapt_dir / f"{key}-F.json"
    if not f_path.exists():
        log(out, f"{key}: F (random search) adaptation, per cycle {per_cycle}")
        restarts = _fresh(work / "F", out)
        f = run_random(spec, wf, tap, work / "F", per_cycle, forbid_gate=forbid_gate, out=out)
        f["restarts"] = restarts
        f_path.write_text(json.dumps(f, indent=1, default=str), encoding="utf-8")
        write_tap(out, tap)
    f = json.loads(f_path.read_text(encoding="utf-8"))
    f_final = Profile.model_validate(f["cycles"][-1]["head_profile"]) if f.get("cycles") else base

    # measurement on the held-out split (every arm is frozen by now)
    tap.forbid = {}

    def prof(arm: str, profile, repeat: int = 1):
        tap.phase = f"measure-{key}-{arm}"
        return measure_arm(store, arm, held, lambda cs: measure_profile(
            wf, profile, cs, arm=arm, workflow=key, tap=tap, outputs=outputs, repeat=repeat, norm=spec.norm), out)

    tap.phase = f"measure-{key}-NULL"
    measure_arm(store, "NULL", held, lambda cs: measure_profile(wf, base, cs, arm="NULL", workflow=key, tap=tap), out)
    tap.phase = f"measure-{key}-A"
    measure_arm(store, "A", held, lambda cs: measure_direct(spec, tap, cs, workflow=key, outputs=outputs), out)
    b_rows = prof("B", base)
    prof("B2", base)
    prof("H", with_fields(base, prompt=HAND_PROMPTS[key]))
    d_rows = prof("D", d_final) if not _same_as(d_final, base) else b_rows
    if not _same_as(f_final, base):
        prof("F", f_final)
    prof("G", with_fields(base, **spec.everything()))
    for n, head in enumerate(d_heads[:-1], start=1):          # the learning gradient: earlier distinct heads
        if not _same_as(head, base) and not _same_as(head, d_final):
            prof(f"D@{n}", head)
    d_calls = _mean(d_rows, "model_calls")
    n_e = min(MAX_VOTES, max(1, math.ceil(d_calls - 1e-9)))
    d_tokens = _mean(d_rows, "prompt_tokens") + _mean(d_rows, "output_tokens")
    b_tokens = _mean(b_rows, "prompt_tokens") + _mean(b_rows, "output_tokens")
    n_tok = min(MAX_VOTES, max(1, math.ceil(d_tokens / max(b_tokens, 1e-9) - 1e-9)))
    (adapt_dir / f"{key}-E.json").write_text(json.dumps({"d_mean_calls": d_calls, "n": n_e, "d_mean_tokens": d_tokens,
                                                         "b_mean_tokens": b_tokens, "n_tok": n_tok}, indent=1),
                                             encoding="utf-8")
    if n_e > 1:
        prof("E", base, repeat=n_e)
    if n_tok > 1 and n_tok != n_e:
        prof("E_tok", base, repeat=n_tok)
    (adapt_dir / f"{key}-gaming.json").write_text(json.dumps(gaming_scan(work / "outputs.jsonl", key), indent=1),
                                                  encoding="utf-8")
    log(out, f"workflow {key}: done")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--data", required=True)
    d.add_argument("--out", required=True)
    sub.add_parser("freeze")
    v = sub.add_parser("devcheck")
    v.add_argument("--data", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--work", required=True)
    v.add_argument("--splits", required=True, help="the folder holding splits.json")
    v.add_argument("--model", default=MODEL)
    v.add_argument("--workflows", default=",".join(ORDER))
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--model", default=MODEL)
    r.add_argument("--workflows", default=",".join(ORDER))
    args = ap.parse_args(argv)
    return {"draw": cmd_draw, "freeze": cmd_freeze, "devcheck": cmd_devcheck, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
