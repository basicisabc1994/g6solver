# SPDX-License-Identifier: Apache-2.0
"""Study V6b workflows: V6's four public datasets as ``g6.metaharness`` workflows, with the recovery remedies declared
(PROTOCOL.md §1-§4).

Copied from ``oss/validation/v6_metaharness/workflows.py`` (V6's frozen file is unchanged) and adapted:

* fresh ids only: every id V6 drew (any split) is excluded, and the draw uses seed 20261020;
* MBPP uses the full ``mbpp.jsonl`` (task ids 11-510) instead of the sanitized file;
* the declared options add the recovery remedies: MASSIVE labelled examples (``Workflow.examples``, a fixed sample of
  the MASSIVE en-US train partition), SciFact ``steps.second_look`` for each label, MBPP ``steps.repair_with_tests``
  (3 rounds and 1 round), and ``exemplars`` (0 or 8) in every search space;
* a hand-written prompt per workflow (arm H), written before any split was read.

The data folder (``$V6B_DATA``) holds the files exactly as downloaded; it is read as data and never executed. Model
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
from g6.metaharness import steps as library_steps

SEED = 20261020
DEV_SIZE, EVAL_SIZE = 20, 100
GATE_SIZE = {"gsm8k": 70, "massive": 70, "scifact": 163, "mbpp": 70}   # recovery.cases_needed on the pilot effects
SPLITS = ("dev", "gate", "eval")
MASSIVE_EXAMPLES = 3000                  # labelled examples sampled from the MASSIVE en-US train partition
EXAMPLES_SEED = f"V6b:{SEED}:massive-examples"
V6_SPLITS = Path(__file__).resolve().parents[2] / "docs" / "validation" / "results" / "V6" / "splits.json"


def sizes(key: str) -> dict[str, int]:
    return {"dev": DEV_SIZE, "gate": GATE_SIZE[key], "eval": EVAL_SIZE}


# --------------------------------------------------------------------------------------------------------------------
# Answer reading shared by checks, oracles and normalisers (V6's rules). None of it reads an expected answer.
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
def v6_ids(key: str) -> set[str]:
    """Every id V6 drew for this workflow, in any split (V6b excludes them all)."""
    splits = json.loads(V6_SPLITS.read_text(encoding="utf-8"))["splits"]
    return {i for split in splits.get(key, {}).values() for i in split}


def draw(key: str, pool_ids: Sequence[str]) -> dict[str, list[str]]:
    """The seeded draw (PROTOCOL.md §2): V6's ids removed, the sorted rest shuffled by the seed, then cut in order into
    dev, gate and eval. A prefix of the order never depends on the later sizes."""
    rng = random.Random(f"V6b:{SEED}:{key}")
    chosen = sorted(set(pool_ids) - v6_ids(key))
    rng.shuffle(chosen)
    out, at = {}, 0
    for split, size in sizes(key).items():
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


# MBPP, full file (task ids 11-510 are the test split, as the dataset's README specifies) -----------------------------
def _mbpp(root: Path) -> list[dict]:
    return _jsonl(root / "mbpp" / "mbpp.jsonl")


def mbpp_pool(root: Path) -> list[str]:
    return [f"mbpp-{int(r['task_id']):03d}" for r in _mbpp(root) if 11 <= int(r["task_id"]) <= 510]


def mbpp_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    by_id = {f"mbpp-{int(r['task_id']):03d}": r for r in _mbpp(root)}
    out = []
    for case_id in ids:
        r = by_id[case_id]
        text = f"{r['text'].strip()}\n\nYour code should pass this example test:\n{r['test_list'][0].strip()}"
        out.append(SuiteCase(case_id, text, {"setup": r.get("test_setup_code") or "",
                                             "tests": [t.strip() for t in r["test_list"]]},
                             {"reference": r["code"]}))
    return out


# MASSIVE 1.1, en-US: test partition (cases) and train partition (the declared labelled examples) ---------------------
def _massive(root: Path) -> list[dict]:
    return _jsonl(root / "massive" / "extracted" / "1.1" / "data" / "en-US.jsonl")


def massive_pool(root: Path) -> list[str]:
    return [f"massive-test-{int(r['id']):05d}" for r in _massive(root) if r["partition"] == "test"]


def massive_input(utterance: str) -> str:
    return f"Utterance: {utterance.strip()}\n\nIntent labels: {', '.join(MASSIVE_LABELS)}"


def massive_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    by_id = {f"massive-test-{int(r['id']):05d}": r for r in _massive(root) if r["partition"] == "test"}
    out = []
    for case_id in ids:
        row = by_id[case_id]
        out.append(SuiteCase(case_id, massive_input(row["utt"]), row["intent"], {"reference": row["intent"]}))
    return out


def utterance_key(text: str) -> str:
    """An utterance compared for duplicates: lower case, words only."""
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def massive_examples(root: Path, exclude: set[str]) -> tuple[tuple[SuiteCase, ...], dict[str, int]]:
    """The declared labelled examples (PROTOCOL.md §4): ``MASSIVE_EXAMPLES`` train-partition rows sampled by
    ``EXAMPLES_SEED`` from the sorted train ids, minus any whose utterance (``utterance_key``) equals one in ``exclude``
    (the study's dev, gate and eval utterances), so no example repeats a case. Returns the examples and the counts."""
    train = sorted((r for r in _massive(root) if r["partition"] == "train"), key=lambda r: int(r["id"]))
    rng = random.Random(EXAMPLES_SEED)
    sample = rng.sample(train, MASSIVE_EXAMPLES)
    kept = [r for r in sample if utterance_key(r["utt"]) not in exclude]
    examples = tuple(SuiteCase(f"massive-train-{int(r['id']):05d}", massive_input(r["utt"]), r["intent"])
                     for r in sorted(kept, key=lambda r: int(r["id"])))
    return examples, {"train_partition": len(train), "sampled": len(sample),
                      "removed_as_duplicates_of_study_cases": len(sample) - len(kept), "kept": len(kept)}


# SciFact (train claims; the pair is the claim and its first cited abstract) ------------------------------------------
def _scifact_dir(root: Path) -> Path:
    return root / "scifact" / "extracted" / "data"


def scifact_pool(root: Path) -> list[str]:
    return [f"scifact-train-{c['id']:04d}" for c in _jsonl(_scifact_dir(root) / "claims_train.jsonl")
            if c.get("cited_doc_ids")]


SCIFACT_NEI = "NOT_ENOUGH_INFO"
SCIFACT_LABELS = ("SUPPORT", "CONTRADICT", SCIFACT_NEI)


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
# Checks, oracles and normalisers (V6's).
# --------------------------------------------------------------------------------------------------------------------
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


def run_tests(code: str, setup: str, tests: Sequence[str], *, timeout_s: float = 15.0):
    """The item's tests against the code, out of process. As in MBPP's own evaluation, the item's setup code, the code
    and the tests share one namespace: the setup and the code form ``solution.py``, and the tests import it whole."""
    from g6.core.sandbox import run_script

    module = f"{setup}\n{code}" if setup.strip() else code
    source = SANDBOX_PRELUDE + "from solution import *\n" + "".join(f"{t}\n" for t in tests)
    return run_script(source, {"solution.py": module}, timeout_s=timeout_s, mem_mb=512)


def mbpp_oracle(output: str, expected: Any) -> bool:
    if not _parses_with_def(output):
        return False
    ran = run_tests(extract_code(output), expected.get("setup", ""), expected["tests"])
    return ran.status == "ok"


def mbpp_norm(output: str) -> str | None:
    code = extract_code(output).strip()
    return "\n".join(line.rstrip() for line in code.splitlines() if line.strip()) or None


# --------------------------------------------------------------------------------------------------------------------
# Optional pipeline steps: V6's self_review and ground, and the library's recovery steps (g6.metaharness.steps).
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


def _step(name: str, component: str, op: str, run: Callable[[str, Any, Any], str]) -> PipelineStep:
    return PipelineStep(name, component, op, run, authority="A2", effect="read")


SELF_REVIEW = _step("self_review", "reasoning", "revise", self_review)
GROUND = _step("ground", "grounding", "check_claim", ground_support)
SECOND_LOOKS = {s.name: s for s in (library_steps.second_look(label, SCIFACT_LABELS) for label in SCIFACT_LABELS)}
REPAIR3 = library_steps.repair_with_tests(rounds=3)
REPAIR1 = library_steps.repair_with_tests(rounds=1, name="repair1")


# --------------------------------------------------------------------------------------------------------------------
# Arm H: one hand-written prompt per workflow, written by the study author from the task description only, before any
# V6b split was read (PROTOCOL.md §5 says what the author knew from V6).
# --------------------------------------------------------------------------------------------------------------------
HAND_PROMPTS = {
    "gsm8k": "Solve the problem step by step, showing each calculation, and check the arithmetic. On the last line "
             "write only the final number as 'Answer: <number>'.",
    "massive": "Work out what the user wants done. Each label is a domain and an action (domain_action): first pick "
               "the domain the request belongs to, then the action within it. Reply with exactly one label, written "
               "as in the list, and nothing else.",
    "scifact": "Decide from the abstract only. SUPPORT: the abstract's findings show the claim is true. CONTRADICT: "
               "its findings show the claim is false. NOT_ENOUGH_INFO: the abstract does not test or state the claim. "
               "Do not answer SUPPORT just because the topic matches. Reply with one label only.",
    "mbpp": "Use exactly the function name and arguments the example test uses. Make sure the function returns (not "
            "prints) the value the test expects, and handle edge cases. Reply with the complete code in one Python "
            "code block, including any imports.",
}


# --------------------------------------------------------------------------------------------------------------------
# The four workflow specifications.
# --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    key: str
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
    declares_examples: bool = False                # MASSIVE: Workflow.examples from the train partition

    def space(self) -> dict[str, list[Any]]:
        """The SearchStrategy options (PROTOCOL.md §4): prompt x pipeline (none or one step) x extra checks (none or
        one) x exemplars (0 or 8)."""
        return {"prompt": list(self.prompts),
                "pipeline": [[]] + [[n] for n in self.pipeline_steps],
                "extra_checks": [[]] + [[k] for k in self.optional_checks],
                "exemplars": [0, 8]}

    def space_size(self) -> int:
        size = 1
        for values in self.space().values():
            size *= len(values)
        return size

    def everything(self) -> dict[str, Any]:
        """Arm G: every optional pipeline step (in declared order), every optional check and 8 exemplars."""
        return {"pipeline": list(self.pipeline_steps), "extra_checks": list(self.optional_checks), "exemplars": 8}


def _mbpp_reference(case: SuiteCase) -> str:
    return f"```python\n{case.meta['reference']}\n```"


SPECS: dict[str, Spec] = {
    "gsm8k": Spec(
        key="gsm8k", name="v6b-gsm8k", dataset="GSM8K test (Cobbe et al. 2021; MIT)",
        task="Solve this grade-school math word problem. Give the final answer as a number.\n\nProblem: {input}",
        checks=(GSM_NUMBER,), optional_checks={"whole-number": GSM_WHOLE},
        pipeline_steps={"self_review": SELF_REVIEW}, oracle=gsm8k_oracle, norm=gsm8k_norm,
        prompts=("",
                 "Work through the problem step by step. Then write the final answer on its own last line as "
                 "'Answer: <number>'.",
                 "Give only the final number: no units, no words, no working."),
        pool=gsm8k_pool, cases=gsm8k_cases),
    "massive": Spec(
        key="massive", name="v6b-massive", dataset="MASSIVE 1.1 en-US test (FitzGerald et al. 2022; CC BY 4.0)",
        task="Classify the utterance into exactly one of the intent labels listed. Reply with the label.\n\n{input}",
        checks=(INTENT_ONE,), optional_checks={"label-only": INTENT_ONLY},
        pipeline_steps={"self_review": SELF_REVIEW}, oracle=massive_oracle, norm=massive_norm,
        prompts=("",
                 "Choose the single intent that best matches what the user wants. Reply with that label exactly as it "
                 "is written in the list, and nothing else.",
                 "Think briefly about what the user wants, then write the final line as 'Answer: <label>'."),
        pool=massive_pool, cases=massive_cases, declares_examples=True),
    "scifact": Spec(
        key="scifact", name="v6b-scifact",
        dataset="SciFact train claims (Wadden et al. 2020; claims CC BY 4.0, abstracts ODC-By 1.0)",
        task="Read the claim and the abstract and decide, from the abstract alone, which label applies. Reply with "
             "the label.\n\n{input}",
        checks=(SF_ONE,), optional_checks={"label-only": SF_ONLY},
        pipeline_steps={"self_review": SELF_REVIEW, "ground": GROUND, **SECOND_LOOKS}, oracle=scifact_oracle,
        norm=scifact_norm,
        prompts=("",
                 "Use only the abstract, not outside knowledge. If the abstract does not address the claim directly, "
                 "the label is NOT_ENOUGH_INFO. Reply with the label only.",
                 "Reason briefly about what the abstract says, then write the final line as 'Answer: <label>'."),
        pool=scifact_pool, cases=scifact_cases),
    "mbpp": Spec(
        key="mbpp", name="v6b-mbpp", dataset="MBPP full, test task ids 11-510 (Austin et al. 2021; CC BY 4.0)",
        task="Write a Python function for this task.\n\n{input}",
        checks=(MBPP_DEF,), optional_checks={"imports-cleanly": MBPP_RUNS},
        pipeline_steps={"self_review": SELF_REVIEW, REPAIR3.name: REPAIR3, REPAIR1.name: REPAIR1},
        oracle=mbpp_oracle, norm=mbpp_norm,
        prompts=("",
                 "Reply with one Python code block that contains the complete function and any imports it needs. "
                 "No explanation.",
                 "Think about edge cases in a sentence or two, then give the complete function in one Python code "
                 "block."),
        pool=mbpp_pool, cases=mbpp_cases, reference=_mbpp_reference),
}

ORDER = ("gsm8k", "massive", "scifact", "mbpp")
TARGETS = ("massive", "scifact", "mbpp")          # the three workflows V6 left inconclusive
