# SPDX-License-Identifier: Apache-2.0
"""Study V6 workflows: four third-party public datasets turned into ``g6.metaharness`` workflows (PROTOCOL.md §2-§3).

Each workflow names the task template, the standard check (mandatory), the optional checks and pipeline steps a profile
may add, the oracle (the only code that reads an answer), an answer normaliser for voting (it never reads an answer),
and the options the study declares for the search proposer. Everything here is frozen and hashed before any arm reads
the gate split or the held-out split.

The data folder (``$V6_DATA``) holds the files exactly as downloaded; it is read as data and never executed. Model
code (MBPP) runs only out of process through ``g6.core.sandbox.run_script``.
"""
from __future__ import annotations

import ast
import json
import random
import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from g6.harness import Check
from g6.metaharness import PipelineStep, SuiteCase

SEED = 20261019
SIZES = {"dev": 20, "gate": 40, "eval": 60}      # proposer-dev, the meta-gate's own held-out, the study's held-out
SPLITS = tuple(SIZES)

# --------------------------------------------------------------------------------------------------------------------
# Answer reading shared by checks, oracles and normalisers. None of it reads an expected answer.
# --------------------------------------------------------------------------------------------------------------------
_ANSWER_LINE = re.compile(r"(?im)^[ \t>*_#-]*answer[ \t*_]*[:：]")


def final_segment(text: str) -> str:
    """The text after the last line that starts with ``Answer:``; the whole text when there is none."""
    text = text or ""
    hits = list(_ANSWER_LINE.finditer(text))
    return text[hits[-1].end():] if hits else text


_NUMBER = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")


def final_number(text: str) -> Decimal | None:
    """The last number in the final segment (else in the whole text), commas and a leading $ removed."""
    for part in (final_segment(text), text or ""):
        found = _NUMBER.findall(part)
        if found:
            raw = found[-1].replace(",", "").replace("$", "")
            try:
                return Decimal(raw)
            except InvalidOperation:
                return None
    return None


def label_key(label: str) -> str:
    return label.strip().lower().rstrip("?")


def labels_named(text: str, patterns: Mapping[str, re.Pattern[str]]) -> list[str]:
    """Distinct labels whose pattern matches the final segment, keeping maximal matches only (a label inside a longer
    label's match does not count)."""
    seg = final_segment(text)
    spans = [(m.start(), m.end(), key) for key, pat in patterns.items() for m in pat.finditer(seg)]
    keep = [s for s in spans if not any(o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0])
                                        for o in spans)]
    return sorted({k for _, _, k in keep})


def only_a_label(text: str, patterns: Mapping[str, re.Pattern[str]]) -> bool:
    """The whole reply (quotes, backticks and a final full stop aside) is one label."""
    bare = (text or "").strip().strip("`'\"").strip().rstrip(".").strip()
    return bool(bare) and any(p.fullmatch(bare) for p in patterns.values())


# --------------------------------------------------------------------------------------------------------------------
# Data: ids first (no answer is read to draw), then cases for named ids.
# --------------------------------------------------------------------------------------------------------------------
def draw(name: str, pool_ids: Sequence[str]) -> dict[str, list[str]]:
    """The seeded draw (PROTOCOL.md §3): the sorted pool shuffled by the seed, then cut in order into dev, gate and
    eval. A prefix of the order never depends on the later sizes."""
    rng = random.Random(f"V6:{SEED}:{name}")
    chosen = sorted(pool_ids)
    rng.shuffle(chosen)
    out, at = {}, 0
    for split, size in SIZES.items():
        out[split] = chosen[at:at + size]
        at += size
    return out


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# GSM8K ---------------------------------------------------------------------------------------------------------------
def gsm8k_pool(root: Path) -> list[str]:
    with (root / "gsm8k" / "test.jsonl").open(encoding="utf-8") as fh:
        n = sum(1 for line in fh if line.strip())
    return [f"gsm8k-test-{i:04d}" for i in range(n)]


def gsm8k_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    rows = _jsonl(root / "gsm8k" / "test.jsonl")
    out = []
    for case_id in ids:
        row = rows[int(case_id.rsplit("-", 1)[1])]
        gold = row["answer"].split("####")[-1].strip().replace(",", "")
        out.append(SuiteCase(case_id, row["question"].strip(), gold, {"reference": row["answer"]}))
    return out


# MBPP (sanitized; the test split is task ids 11-510, as the dataset's README specifies) ------------------------------
def _mbpp(root: Path) -> list[dict]:
    return json.loads((root / "mbpp" / "sanitized-mbpp.json").read_text(encoding="utf-8"))


def mbpp_pool(root: Path) -> list[str]:
    return [f"mbpp-{r['task_id']:03d}" for r in _mbpp(root) if 11 <= int(r["task_id"]) <= 510]


def mbpp_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    by_id = {f"mbpp-{r['task_id']:03d}": r for r in _mbpp(root)}
    out = []
    for case_id in ids:
        r = by_id[case_id]
        imports = [i for i in (r.get("test_imports") or []) if i.strip()]
        uses = "".join(f"{i}\n" for i in imports)
        text = f"{r['prompt'].strip()}\n\nYour code should pass this example test:\n{uses}{r['test_list'][0]}"
        out.append(SuiteCase(case_id, text, {"imports": imports, "tests": list(r["test_list"])},
                             {"reference": r["code"]}))
    return out


# MASSIVE 1.1, en-US, test partition ------------------------------------------------------------------------------------
def _massive(root: Path) -> list[dict]:
    return _jsonl(root / "massive" / "extracted" / "1.1" / "data" / "en-US.jsonl")


def massive_pool(root: Path) -> list[str]:
    return [f"massive-test-{int(r['id']):05d}" for r in _massive(root) if r["partition"] == "test"]


def massive_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    by_id = {f"massive-test-{int(r['id']):05d}": r for r in _massive(root) if r["partition"] == "test"}
    labels = ", ".join(MASSIVE_LABELS)
    out = []
    for case_id in ids:
        row = by_id[case_id]
        text = f"Utterance: {row['utt'].strip()}\n\nIntent labels: {labels}"
        out.append(SuiteCase(case_id, text, row["intent"], {"reference": row["intent"]}))
    return out


# SciFact (train claims; the pair is the claim and its first cited abstract) ------------------------------------------
def _scifact_dir(root: Path) -> Path:
    return root / "scifact" / "extracted" / "data"


def scifact_pool(root: Path) -> list[str]:
    return [f"scifact-train-{c['id']:04d}" for c in _jsonl(_scifact_dir(root) / "claims_train.jsonl")
            if c.get("cited_doc_ids")]


SCIFACT_NEI = "NOT_ENOUGH_INFO"


def scifact_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    claims = {f"scifact-train-{c['id']:04d}": c for c in _jsonl(_scifact_dir(root) / "claims_train.jsonl")}
    corpus = {int(d["doc_id"]): d for d in _jsonl(_scifact_dir(root) / "corpus.jsonl")}
    out = []
    for case_id in ids:
        c = claims[case_id]
        doc_id = int(c["cited_doc_ids"][0])
        doc = corpus[doc_id]
        rationales = (c.get("evidence") or {}).get(str(doc_id)) or []
        gold = rationales[0]["label"] if rationales else SCIFACT_NEI
        abstract = " ".join(s.strip() for s in doc["abstract"])
        text = (f"Labels: SUPPORT, CONTRADICT, NOT_ENOUGH_INFO\n\nCLAIM: {c['claim'].strip()}\n\n"
                f"ABSTRACT: {doc['title'].strip()}. {abstract}")
        out.append(SuiteCase(case_id, text, gold, {"doc_id": doc_id, "reference": gold}))
    return out


# --------------------------------------------------------------------------------------------------------------------
# Checks, oracles and normalisers.
# --------------------------------------------------------------------------------------------------------------------
# GSM8K
def _has_number(out: str) -> bool:
    return final_number(out) is not None


def _whole_number(out: str) -> bool:
    n = final_number(out)
    return n is not None and n == n.to_integral_value()


GSM_NUMBER = Check("the reply contains a number", _has_number, check_id="has-number",
                   known_bad=("", "I cannot tell."), known_good="42")
GSM_WHOLE = Check("the final answer is a whole number", _whole_number, check_id="whole-number",
                  known_bad=("about 3.5", "no idea"), known_good="Answer: 12")


def gsm8k_oracle(output: str, expected: Any) -> bool:
    got = final_number(output)
    return got is not None and got == Decimal(str(expected))


def gsm8k_norm(output: str) -> str | None:
    got = final_number(output)
    return None if got is None else format(got.normalize(), "f")


# MASSIVE (the 60 en-US intents; the oracle and checks compare label keys)
MASSIVE_LABELS = (
    "alarm_query", "alarm_remove", "alarm_set", "audio_volume_down", "audio_volume_mute", "audio_volume_other",
    "audio_volume_up", "calendar_query", "calendar_remove", "calendar_set", "cooking_query", "cooking_recipe",
    "datetime_convert", "datetime_query", "email_addcontact", "email_query", "email_querycontact", "email_sendemail",
    "general_greet", "general_joke", "general_quirky", "iot_cleaning", "iot_coffee", "iot_hue_lightchange",
    "iot_hue_lightdim", "iot_hue_lightoff", "iot_hue_lighton", "iot_hue_lightup", "iot_wemo_off", "iot_wemo_on",
    "lists_createoradd", "lists_query", "lists_remove", "music_dislikeness", "music_likeness", "music_query",
    "music_settings", "news_query", "play_audiobook", "play_game", "play_music", "play_podcasts", "play_radio",
    "qa_currency", "qa_definition", "qa_factoid", "qa_maths", "qa_stock", "recommendation_events",
    "recommendation_locations", "recommendation_movies", "social_post", "social_query", "takeaway_order",
    "takeaway_query", "transport_query", "transport_taxi", "transport_ticket", "transport_traffic", "weather_query")


def _label_pattern(label: str) -> re.Pattern[str]:
    words = label_key(label).split("_")
    body = r"[ _\-]+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![a-z0-9_]){body}(?![a-z0-9_])", re.IGNORECASE)


MASSIVE_PATTERNS = {label_key(lab): _label_pattern(lab) for lab in MASSIVE_LABELS}


def _one_intent(out: str) -> bool:
    return len(labels_named(out, MASSIVE_PATTERNS)) == 1


def _only_an_intent(out: str) -> bool:
    return only_a_label(out, MASSIVE_PATTERNS)


INTENT_ONE = Check("the reply names exactly one of the listed intent labels", _one_intent, check_id="one-label",
                   known_bad=("", "alarm_set or alarm_query"), known_good="alarm_set")
INTENT_ONLY = Check("the reply is a single label and nothing else", _only_an_intent, check_id="label-only",
                    known_bad=("I think the user wants alarm_set because they name a time.", ""),
                    known_good="alarm_set")


def massive_oracle(output: str, expected: Any) -> bool:
    return labels_named(output, MASSIVE_PATTERNS) == [label_key(str(expected))]


def massive_norm(output: str) -> str | None:
    named = labels_named(output, MASSIVE_PATTERNS)
    return named[0] if len(named) == 1 else None


# SciFact
SCIFACT_PATTERNS = {
    "SUPPORT": re.compile(r"(?<![A-Za-z_])SUPPORT(?:S|ED)?(?![A-Za-z_])", re.IGNORECASE),
    "CONTRADICT": re.compile(r"(?<![A-Za-z_])CONTRADICT(?:S|ED|ION)?(?![A-Za-z_])", re.IGNORECASE),
    "NOT_ENOUGH_INFO": re.compile(r"(?<![A-Za-z_])NOT[ _\-]+ENOUGH[ _\-]+INFO(?:RMATION)?(?![A-Za-z_])", re.IGNORECASE),
}


def _one_scifact_label(out: str) -> bool:
    return len(labels_named(out, SCIFACT_PATTERNS)) == 1


def _only_a_scifact_label(out: str) -> bool:
    return only_a_label(out, SCIFACT_PATTERNS)


SF_ONE = Check("the reply names exactly one of the three labels", _one_scifact_label, check_id="one-label",
               known_bad=("", "SUPPORT or CONTRADICT"), known_good="CONTRADICT")
SF_ONLY = Check("the reply is a single label and nothing else", _only_a_scifact_label, check_id="label-only",
                known_bad=("The abstract shows a strong effect, so SUPPORT.", ""), known_good="SUPPORT")


def scifact_oracle(output: str, expected: Any) -> bool:
    return labels_named(output, SCIFACT_PATTERNS) == [str(expected)]


def scifact_norm(output: str) -> str | None:
    named = labels_named(output, SCIFACT_PATTERNS)
    return named[0] if len(named) == 1 else None


# MBPP
_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_DEF = re.compile(r"(?m)^[ \t]*def[ \t]+\w+[ \t]*\(")


def extract_code(text: str) -> str:
    """The first fenced block that defines a function, else the first fenced block, else the whole reply."""
    blocks = [m.group(2) for m in _FENCE.finditer(text or "")]
    for block in blocks:
        if _DEF.search(block):
            return block
    return blocks[0] if blocks else (text or "")


def _parses_with_def(out: str) -> bool:
    code = extract_code(out)
    if not _DEF.search(code):
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # e.g. an invalid escape in a string is a warning, not an error
            ast.parse(code)                      # parsed, never run in process
    except (SyntaxError, ValueError):
        return False
    return True


SANDBOX_PRELUDE = "import sys\nsys.path.insert(0, '.')\n"


def _imports_cleanly(out: str) -> bool:
    if not _parses_with_def(out):
        return False
    from g6.core.sandbox import run_script

    ran = run_script(SANDBOX_PRELUDE + "import solution\n", {"solution.py": extract_code(out)}, timeout_s=10,
                     mem_mb=512)
    return ran.status == "ok"


MBPP_DEF = Check("the reply contains Python code that parses and defines a function", _parses_with_def,
                 check_id="defines-function", known_bad=("", "def broken(:\n    pass"),
                 known_good="def f(x):\n    return x")
MBPP_RUNS = Check("the code imports without error when run out of process", _imports_cleanly, check_id="imports-cleanly",
                  known_bad=("", "def f(:"))


def run_tests(code: str, imports: Sequence[str], tests: Sequence[str], *, timeout_s: float = 15.0):
    from g6.core.sandbox import run_script

    source = SANDBOX_PRELUDE + "".join(f"{i}\n" for i in imports) + "from solution import *\n" + \
        "".join(f"{t}\n" for t in tests)
    return run_script(source, {"solution.py": code}, timeout_s=timeout_s, mem_mb=512)


def mbpp_oracle(output: str, expected: Any) -> bool:
    if not _parses_with_def(output):
        return False
    ran = run_tests(extract_code(output), expected["imports"], expected["tests"])
    return ran.status == "ok"


def mbpp_norm(output: str) -> str | None:
    code = extract_code(output).strip()
    return "\n".join(line.rstrip() for line in code.splitlines() if line.strip()) or None


# --------------------------------------------------------------------------------------------------------------------
# Optional pipeline steps (acting: each may revise the output; none judges it).
# --------------------------------------------------------------------------------------------------------------------
REVIEW_SYSTEM = ("You check a draft answer to a task. If the draft is right, reply with the draft unchanged. If it is "
                 "wrong, reply with a corrected answer in the same format as the draft. Reply with the answer only.")


def self_review(output: str, contract: Any, ctx: Any) -> str:
    revised = ctx.caller("answer").complete(REVIEW_SYSTEM, f"TASK:\n{contract.task}\n\nDRAFT ANSWER:\n{output}")
    return revised if revised.strip() else output


def ground_support(output: str, contract: Any, ctx: Any) -> str:
    """``g6.grounding.check_claim``: a SUPPORT label the abstract does not ground lexically becomes NOT_ENOUGH_INFO."""
    from g6.grounding import Passage, check_claim

    task = contract.task
    claim = re.search(r"CLAIM:\s*(.*?)\n\s*\nABSTRACT:", task, re.DOTALL)
    abstract = re.search(r"ABSTRACT:\s*(.*)\Z", task, re.DOTALL)
    if not (claim and abstract) or labels_named(output, SCIFACT_PATTERNS) != ["SUPPORT"]:
        return output
    status = check_claim(claim.group(1).strip(), [Passage(abstract.group(1).strip(), "abstract")]).status
    return output if status in ("quoted", "supported") else SCIFACT_NEI


REPAIR_SYSTEM = ("You fix Python code. Reply with the complete corrected code in one Python code block, and nothing "
                 "else.")


def example_repair(output: str, contract: Any, ctx: Any) -> str:
    """Runs the one example test the task shows (never a hidden test) out of process; on a failure, asks once for a
    fix."""
    task = contract.task
    shown = re.search(r"Your code should pass this example test:\n(.*)\Z", task, re.DOTALL)
    if not shown or not _parses_with_def(output):
        return output
    lines = [line for line in shown.group(1).splitlines() if line.strip()]
    imports = [line for line in lines if re.match(r"^\s*(import|from)\s", line)]
    tests = [line for line in lines if line not in imports]
    code = extract_code(output)
    ran = run_tests(code, imports, tests, timeout_s=10)
    if ran.status == "ok":
        return output
    error = (ran.stderr or ran.status)[-600:]
    fixed = ctx.caller("answer").complete(
        REPAIR_SYSTEM, f"TASK:\n{task}\n\nCODE:\n```python\n{code}\n```\n\nIt fails the example test. Error:\n{error}")
    return fixed if fixed.strip() else output


def _step(name: str, component: str, op: str, run: Callable[[str, Any, Any], str]) -> PipelineStep:
    return PipelineStep(name, component, op, run, authority="A2", effect="read")


SELF_REVIEW = _step("self_review", "reasoning", "revise", self_review)
GROUND = _step("ground", "grounding", "check_claim", ground_support)
EXAMPLE_REPAIR = _step("example_repair", "reasoning", "repair_from_example", example_repair)


# --------------------------------------------------------------------------------------------------------------------
# The four workflow specifications.
# --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    name: str
    dataset: str
    task: str
    checks: tuple[Check, ...]
    optional_checks: Mapping[str, Check]
    pipeline_steps: Mapping[str, PipelineStep]
    oracle: Callable[[str, Any], bool]
    norm: Callable[[str], str | None]
    prompts: tuple[str, ...]                       # the search options for the profile's prompt ("" is the default)
    pool: Callable[[Path], list[str]]
    cases: Callable[[Path, Sequence[str]], list[SuiteCase]]
    reference: Callable[[SuiteCase], str] = field(default=lambda c: str(c.meta.get("reference", "")))

    def space(self) -> dict[str, list[Any]]:
        """The SearchStrategy options (PROTOCOL.md §4, arm D): prompt x pipeline (none or one step) x extra checks."""
        return {"prompt": list(self.prompts),
                "pipeline": [[]] + [[n] for n in self.pipeline_steps],
                "extra_checks": [[]] + [[k] for k in self.optional_checks]}

    def everything(self) -> dict[str, list[str]]:
        """Arm G: every optional pipeline step and every optional check on."""
        return {"pipeline": list(self.pipeline_steps), "extra_checks": list(self.optional_checks)}


def _mbpp_reference(case: SuiteCase) -> str:
    return f"```python\n{case.meta['reference']}\n```"


SPECS: dict[str, Spec] = {
    "gsm8k": Spec(
        name="v6-gsm8k", dataset="GSM8K test (Cobbe et al. 2021; MIT)",
        task="Solve this grade-school math word problem. Give the final answer as a number.\n\nProblem: {input}",
        checks=(GSM_NUMBER,), optional_checks={"whole-number": GSM_WHOLE},
        pipeline_steps={"self_review": SELF_REVIEW}, oracle=gsm8k_oracle, norm=gsm8k_norm,
        prompts=("",
                 "Work through the problem step by step. Then write the final answer on its own last line as "
                 "'Answer: <number>'.",
                 "Give only the final number: no units, no words, no working."),
        pool=gsm8k_pool, cases=gsm8k_cases),
    "massive": Spec(
        name="v6-massive", dataset="MASSIVE 1.1 en-US test (FitzGerald et al. 2022; CC BY 4.0)",
        task="Classify the utterance into exactly one of the intent labels listed. Reply with the label.\n\n{input}",
        checks=(INTENT_ONE,), optional_checks={"label-only": INTENT_ONLY},
        pipeline_steps={"self_review": SELF_REVIEW}, oracle=massive_oracle, norm=massive_norm,
        prompts=("",
                 "Choose the single intent that best matches what the user wants. Reply with that label exactly as it "
                 "is written in the list, and nothing else.",
                 "Think briefly about what the user wants, then write the final line as 'Answer: <label>'."),
        pool=massive_pool, cases=massive_cases),
    "scifact": Spec(
        name="v6-scifact", dataset="SciFact train claims (Wadden et al. 2020; claims CC BY 4.0, abstracts ODC-By 1.0)",
        task="Read the claim and the abstract and decide, from the abstract alone, which label applies. Reply with "
             "the label.\n\n{input}",
        checks=(SF_ONE,), optional_checks={"label-only": SF_ONLY},
        pipeline_steps={"self_review": SELF_REVIEW, "ground": GROUND}, oracle=scifact_oracle, norm=scifact_norm,
        prompts=("",
                 "Use only the abstract, not outside knowledge. If the abstract does not address the claim directly, "
                 "the label is NOT_ENOUGH_INFO. Reply with the label only.",
                 "Reason briefly about what the abstract says, then write the final line as 'Answer: <label>'."),
        pool=scifact_pool, cases=scifact_cases),
    "mbpp": Spec(
        name="v6-mbpp", dataset="MBPP sanitized, test task ids 11-510 (Austin et al. 2021; CC BY 4.0)",
        task="Write a Python function for this task.\n\n{input}",
        checks=(MBPP_DEF,), optional_checks={"imports-cleanly": MBPP_RUNS},
        pipeline_steps={"self_review": SELF_REVIEW, "example_repair": EXAMPLE_REPAIR}, oracle=mbpp_oracle,
        norm=mbpp_norm,
        prompts=("",
                 "Reply with one Python code block that contains the complete function and any imports it needs. "
                 "No explanation.",
                 "Think about edge cases in a sentence or two, then give the complete function in one Python code "
                 "block."),
        pool=mbpp_pool, cases=mbpp_cases, reference=_mbpp_reference),
}

ORDER = ("gsm8k", "massive", "scifact", "mbpp")
