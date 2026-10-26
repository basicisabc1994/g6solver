# SPDX-License-Identifier: Apache-2.0
"""Development pilot (not a study): which G6 components lift MASSIVE, SciFact and MBPP above static G6?

Cases: V6's dev + gate ids (60 per workflow), all seen in V6, so nothing here is a claim. Every arm runs through
``g6.metaharness.evaluate.run_case`` (the harness intake and checks, the workflow's oracle), with the V6-F2 fix
(``accept_risky``) on every arm. Usage: python -I pilot.py <oss> <data> <out_dir> [workflow,...]
"""
import collections
import json
import math
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

OSS, DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ONLY = sys.argv[4].split(",") if len(sys.argv) > 4 else ["massive", "scifact", "mbpp"]
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("G6_HOME", str(OUT / "g6home"))
sys.path.insert(0, str(OSS / "validation" / "v6_metaharness"))
import run as R  # noqa: E402
import workflows as W  # noqa: E402
from g6.core.llm import OllamaCaller  # noqa: E402
from g6.metaharness import PipelineStep  # noqa: E402

ACCEPT = "pilot-script (development run; not a person)"
splits = R.load_splits(OSS / "docs" / "validation" / "results" / "V6")
tap = R.Tap(OllamaCaller(R.MODEL, base_url=R.OLLAMA))
rows_out = (OUT / "pilot_rows.jsonl").open("a", encoding="utf-8")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with (OUT / "pilot.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def step(name, component, op, fn):
    return PipelineStep(name, component, op, fn, authority="A2", effect="read")


# --- MASSIVE: retrieved labelled examples (the g6.context "exemplar" idea) and a vote (g6.reasoning) -----------------
_TOK = re.compile(r"[a-z0-9']+")
_bank = None


def bank():
    global _bank
    if _bank is None:
        rows = [r for r in W._massive(DATA) if r["partition"] == "train"]
        docs = [(set(_TOK.findall(r["utt"].lower())), r["utt"].strip(), r["intent"]) for r in rows]
        df = collections.Counter(t for toks, _, _ in docs for t in toks)
        idf = {t: math.log(len(docs) / c) for t, c in df.items()}
        _bank = (docs, idf)
    return _bank


def nearest(utterance, k=8):
    docs, idf = bank()
    q = set(_TOK.findall(utterance.lower()))
    scored = sorted(docs, key=lambda d: -sum(idf.get(t, 0.0) for t in q & d[0]))
    return [(u, lab) for _, u, lab in scored[:k]]


def examples_factory():
    from g6.metaharness.executor import DEFAULT_SYSTEM

    def factory(ctx):
        def execute(contract):
            utt = contract.task.split("Utterance:", 1)[-1].split("\n\nIntent labels:", 1)[0].strip()
            shots = "\n".join(f"- \"{u}\" -> {lab}" for u, lab in nearest(utt))
            user = f"Labelled examples of similar utterances (reference data):\n{shots}\n\n{contract.task}"
            return ctx.caller("answer").complete(DEFAULT_SYSTEM, user)
        return execute
    return factory


# --- SciFact: grounding.check_claim's conflict reading, and a targeted second look -----------------------------------
def _claim_abstract(task):
    claim = re.search(r"CLAIM:\s*(.*?)\n\s*\nABSTRACT:", task, re.DOTALL)
    abstract = re.search(r"ABSTRACT:\s*(.*)\Z", task, re.DOTALL)
    return (claim.group(1).strip(), abstract.group(1).strip()) if claim and abstract else (None, None)


def conflict_to_contradict(output, contract, ctx):
    """grounding.check_claim: a SUPPORT whose claim the abstract contradicts (a negation or a wrong figure) becomes
    CONTRADICT. Anything else is left as it is (V6's `ground` step turned every unfound SUPPORT into NEI)."""
    from g6.grounding import Passage, check_claim

    claim, abstract = _claim_abstract(contract.task)
    if not claim or W.labels_named(output, W.SCIFACT_PATTERNS) != ["SUPPORT"]:
        return output
    status = check_claim(claim, [Passage(abstract, "abstract")]).status
    return "CONTRADICT" if status == "conflict" else output


VERIFY_SYSTEM = ("A first reader labelled this claim SUPPORT. Check it against the abstract alone. Reply CONTRADICT if "
                 "the abstract's findings go against the claim, NOT_ENOUGH_INFO if the abstract does not directly test "
                 "or state the claim, and SUPPORT only if the abstract directly shows it. Reply with the label only.")


def verify_support(output, contract, ctx):
    if W.labels_named(output, W.SCIFACT_PATTERNS) != ["SUPPORT"]:
        return output
    second = ctx.caller("answer").complete(VERIFY_SYSTEM, contract.task)
    return second if len(W.labels_named(second, W.SCIFACT_PATTERNS)) == 1 else output


# --- MBPP: a test-and-repair loop on the shown example (the cegis / coding_agent_gate pattern) -----------------------
def repair_loop(output, contract, ctx, rounds=3):
    shown = re.search(r"Your code should pass this example test:\n(.*)\Z", contract.task, re.DOTALL)
    if not shown:
        return output
    lines = [line for line in shown.group(1).splitlines() if line.strip()]
    imports = [line for line in lines if re.match(r"^\s*(import|from)\s", line)]
    tests = [line for line in lines if line not in imports]
    for _ in range(rounds):
        if not W._parses_with_def(output):
            error = "the reply has no Python function that parses"
        else:
            ran = W.run_tests(W.extract_code(output), imports, tests, timeout_s=10)
            if ran.status == "ok":
                return output
            error = (ran.stderr or ran.status)[-600:]
        try:
            fixed = ctx.caller("answer").complete(
                W.REPAIR_SYSTEM, f"TASK:\n{contract.task}\n\nCODE:\n{output}\n\nIt fails the example test. Error:\n{error}")
        except Exception:  # noqa: BLE001 - the budget ran out: keep the last answer
            return output
        if not fixed.strip():
            return output
        output = fixed
    return output


EXTRA_STEPS = {
    "scifact": {"conflict": step("conflict", "grounding", "check_claim", conflict_to_contradict),
                "verify": step("verify", "reasoning", "revise", verify_support)},
    "mbpp": {"repair3": step("repair3", "coding_agent_gate", "verify_answer", repair_loop)},
}


def workflow(key, cases):
    spec = W.SPECS[key]
    wf = R.build_workflow(spec, cases, (), tap)
    return replace(wf, accept_risky=ACCEPT, pipeline_steps={**wf.pipeline_steps, **EXTRA_STEPS.get(key, {})}), spec


def arms(key, wf, spec):
    base = R.base_profile()
    out = {"B": lambda cs: R.measure_profile(wf, base, cs, arm="B", workflow=key)}
    if key == "massive":
        out["examples"] = lambda cs: R.measure_profile(wf, base, cs, arm="examples", workflow=key,
                                                       factory=examples_factory())
        out["vote3"] = lambda cs: R.measure_profile(wf, base, cs, arm="vote3", workflow=key, repeat=3, norm=spec.norm)
    if key == "scifact":
        for name in ("conflict", "verify"):
            out[name] = (lambda n: lambda cs: R.measure_profile(wf, R.with_fields(base, pipeline=[n]), cs, arm=n,
                                                                workflow=key))(name)
        out["vote3"] = lambda cs: R.measure_profile(wf, base, cs, arm="vote3", workflow=key, repeat=3, norm=spec.norm)
    if key == "mbpp":
        out["repair3"] = lambda cs: R.measure_profile(wf, R.with_fields(base, pipeline=["repair3"]), cs, arm="repair3",
                                                      workflow=key)
        out["repair1"] = lambda cs: R.measure_profile(wf, R.with_fields(base, pipeline=["example_repair"]), cs,
                                                      arm="repair1", workflow=key)
    return out


summary = {}
for key in ONLY:
    ids = splits[key]["dev"] + splits[key]["gate"]
    cases = R.cases_for(W.SPECS[key], DATA, ids)
    wf, spec = workflow(key, cases)
    results = {}
    for arm, fn in arms(key, wf, spec).items():
        tap.phase = f"pilot-{key}-{arm}"
        t0 = time.time()
        rows = fn(cases)
        for r in rows:
            rows_out.write(json.dumps(r) + "\n")
        rows_out.flush()
        results[arm] = {r["case_id"]: r for r in rows}
        n = len(rows)
        log(f"{key} {arm}: success {sum(r['success'] for r in rows)}/{n}, false accepts "
            f"{sum(r['false_accept'] for r in rows)}, blocked {sum(not r['released'] for r in rows)}, calls/case "
            f"{sum(r['model_calls'] for r in rows) / n:.2f}, {time.time() - t0:.0f}s")
    b = results["B"]
    summary[key] = {}
    for arm, got in results.items():
        if arm == "B":
            continue
        wins = sum(got[c]["success"] and not b[c]["success"] for c in b)
        losses = sum(b[c]["success"] and not got[c]["success"] for c in b)
        k, m = min(wins, losses), wins + losses
        p = sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m * 2 if m else 1.0
        summary[key][arm] = {"wins": wins, "losses": losses, "p_two_sided": round(min(1.0, p), 4)}
        log(f"{key} {arm} vs B: {wins} wins, {losses} losses, sign test p {min(1.0, p):.4f}")
(OUT / f"pilot_summary_{'_'.join(ONLY)}.json").write_text(json.dumps(summary, indent=1))
log(f"calls by phase: {dict(tap.calls)}")
