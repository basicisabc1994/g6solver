# SPDX-License-Identifier: Apache-2.0
"""The frontier programme's six meta-train workflows (design §5): V6b's GSM8K, MASSIVE, SciFact and MBPP, built from
its frozen study code (imported, never edited), and ARC-Challenge and banking77, new. Each spec adds what the frontier
library reads:

* ``feedback(output, case)``: why a dev answer failed, in words a proposer can act on (the expected answer; for MBPP the
  first of the item's tests that fails). The library calls it on dev cases only (``propose.build_view``).
* ``space``: the edit space (``pool.EditSpace``): prompt options, pipeline (none or one declared step), extra checks,
  exemplars (0 or 8) and retries (0 or 1).

``build`` makes the workflow for one round's splits: dev, selection (ranks candidates) and confirmation (held out, spent
once, sequentially). The dev-eval split is measured by the round runner, outside the workflow. Data is read from
``data.data_root()`` as data; model code (MBPP) runs only out of process (``g6.core.sandbox``).
"""
from __future__ import annotations

import csv
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from g6.harness import Check
from g6.metaharness import EvidencePolicy, Model, Objective, PipelineStep, Suite, SuiteCase, Workflow
from g6.metaharness import steps as library_steps
from g6.metaharness.pool import EditSpace
from v6b_metaharness import workflows as v6b

SEED = 20261019
SIZES = {"dev": 20, "selection": 60, "confirmation": 80, "dev_eval": 100}
EXAMPLES = 3000                                  # labelled examples sampled from a train partition (MASSIVE, banking77)
CALLS_PER_CASE = 3                               # design §5: every arm's program makes at most 3 calls per case
APPROVER = "frontier-dev-scripted-approval (study script, not a person)"
ORDER = ("gsm8k", "massive", "scifact", "mbpp", "arc", "banking77")
def objectives(mean_calls: float = float(CALLS_PER_CASE)) -> tuple[Objective, ...]:
    return (Objective("success", "max", unit="share of held-out cases"),
            Objective("model_calls", "bound", bound=mean_calls, unit="calls per case", provenance="specified"),
            Objective("wall_clock_s", "bound", bound=120.0, unit="seconds per case", provenance="specified"))


OBJECTIVES = objectives()
POLICY = EvidencePolicy(min_cases=30, max_evaluations=1, looks=4)


@dataclass(frozen=True)
class FrontierSpec:
    key: str
    dataset: str
    task: str
    checks: tuple[Check, ...]
    optional_checks: Mapping[str, Check]
    pipeline_steps: Mapping[str, PipelineStep]
    searchable_steps: tuple[str, ...]            # the steps the edit space offers; recovery may add any declared one
    oracle: Callable[[str, Any], bool]
    norm: Callable[[str], str | None]
    prompts: tuple[str, ...]
    pool: Callable[[Path], list[str]]
    cases: Callable[[Path, Sequence[str]], list[SuiteCase]]
    feedback: Callable[[str, SuiteCase], str]
    reference: Callable[[SuiteCase], str]
    examples: Callable[[Path, Sequence[SuiteCase]], tuple[SuiteCase, ...]] | None = None

    @property
    def name(self) -> str:
        return f"frontier-{self.key}"

    @property
    def space(self) -> EditSpace:
        return EditSpace({"prompt": list(self.prompts),
                          "pipeline": [[]] + [[n] for n in self.searchable_steps],
                          "extra_checks": [[]] + [[k] for k in self.optional_checks],
                          "exemplars": [0, 8], "retries": [0, 1]})

    def everything(self) -> dict[str, Any]:
        """Arm G, every prebuilt component on at once, as V6b's arm G: every declared pipeline step in order, every
        optional check and 8 exemplars. The edit space offers one step at a time, so where a workflow declares several
        steps (SciFact, MBPP) tuning cannot reach this profile."""
        return {"pipeline": list(self.pipeline_steps), "extra_checks": list(self.optional_checks), "exemplars": 8}


def distinct_pool(spec: FrontierSpec, root: Path) -> list[str]:
    """The pool, sorted, keeping the first id of each repeated input: the same text in two splits would make the
    workflow refuse itself (``Suite.problems``), and real pools repeat a few (MASSIVE 4, SciFact 3, MBPP, ARC and
    banking77 one each)."""
    ids = sorted(spec.pool(root))
    seen: set[str] = set()
    kept = []
    for case in spec.cases(root, ids):
        if case.input not in seen:
            seen.add(case.input)
            kept.append(case.case_id)
    return kept


def _strip(cases: Sequence[SuiteCase]) -> tuple[SuiteCase, ...]:
    return tuple(replace(c, meta={}) for c in cases)


def build(spec: FrontierSpec, splits: Mapping[str, Sequence[SuiteCase]], caller: Any, *,
          examples: Sequence[SuiteCase] = (), calls: int = CALLS_PER_CASE, mean_calls: float = float(CALLS_PER_CASE)
          ) -> Workflow:
    """The workflow for one round: ``splits`` holds dev, selection and confirmation cases (the confirmation split is
    the held-out one). Case metadata (reference answers) never reaches the workflow. ``calls`` caps one case's model
    calls; ``mean_calls`` bounds their mean, an objective a change must keep."""
    return Workflow(name=spec.name, task=spec.task, checks=spec.checks, oracle=spec.oracle,
                    suite=Suite(dev=_strip(splits["dev"]), held_out=_strip(splits["confirmation"]),
                                selection=_strip(splits["selection"]),
                                provenance={"dev": "independent", "held_out": "independent"}),
                    models={"local": Model(caller, max_calls_per_run=calls)}, objectives=objectives(mean_calls),
                    policy=POLICY, optional_checks=dict(spec.optional_checks), pipeline_steps=dict(spec.pipeline_steps),
                    accept_risky=APPROVER, examples=_strip(examples), feedback=spec.feedback)


# --------------------------------------------------------------------------------------------------------------------
# Feedback (dev only)
# --------------------------------------------------------------------------------------------------------------------
def _number_feedback(output: str, case: SuiteCase) -> str:
    got = v6b.gsm8k_norm(output)
    return f"The expected answer is {case.expected}; the reply's final number was {got if got is not None else 'missing'}."


def _label_feedback(norm: Callable[[str], str | None]) -> Callable[[str, SuiteCase], str]:
    def feedback(output: str, case: SuiteCase) -> str:
        got = norm(output)
        return f"The expected label is {case.expected}; the reply gave {got if got else 'no single label'}."
    return feedback


def mbpp_feedback(output: str, case: SuiteCase) -> str:
    if not v6b._parses_with_def(output):
        return "The reply has no Python function that parses."
    code = v6b.extract_code(output)
    for test in case.expected["tests"]:
        ran = v6b.run_tests(code, case.expected.get("setup", ""), [test])
        if ran.status != "ok":
            return f"It fails the item's test `{test}`: {(ran.stderr or ran.status).strip()[-300:]}"
    return "Its tests pass; the reply failed a check or ran out of time."


# --------------------------------------------------------------------------------------------------------------------
# Labelled examples from a train partition, minus any repeating a study case's text
# --------------------------------------------------------------------------------------------------------------------
def _sample_examples(rows: Sequence[SuiteCase], study: Sequence[SuiteCase], key: str,
                     text: Callable[[str], str]) -> tuple[SuiteCase, ...]:
    exclude = {v6b.utterance_key(text(c.input)) for c in study}
    rows = sorted(rows, key=lambda c: c.case_id)
    sample = random.Random(f"frontier:{SEED}:{key}-examples").sample(rows, min(EXAMPLES, len(rows)))
    return tuple(sorted((c for c in sample if v6b.utterance_key(text(c.input)) not in exclude),
                        key=lambda c: c.case_id))


def _first_line(prefix: str) -> Callable[[str], str]:
    return lambda text: text.split("\n\n", 1)[0].removeprefix(prefix)


def massive_examples(root: Path, study: Sequence[SuiteCase]) -> tuple[SuiteCase, ...]:
    train = [SuiteCase(f"massive-train-{int(r['id']):05d}", v6b.massive_input(r["utt"]), r["intent"])
             for r in v6b._massive(root) if r["partition"] == "train"]
    return _sample_examples(train, study, "massive", _first_line("Utterance: "))


# --------------------------------------------------------------------------------------------------------------------
# SciFact: train and dev claims (V6 and V6b drew train claims only; the dev claims widen the pool for later studies)
# --------------------------------------------------------------------------------------------------------------------
def scifact_pool(root: Path) -> list[str]:
    folder = v6b._scifact_dir(root)
    return [f"scifact-{part}-{c['id']:04d}" for part in ("train", "dev")
            for c in v6b._jsonl(folder / f"claims_{part}.jsonl") if c.get("cited_doc_ids")]


def scifact_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    folder = v6b._scifact_dir(root)
    by_id = {c.case_id: c for c in v6b.scifact_cases(root, [i for i in ids if i.startswith("scifact-train-")])}
    dev = [i for i in ids if i.startswith("scifact-dev-")]
    if dev:
        claims = {f"scifact-dev-{c['id']:04d}": c for c in v6b._jsonl(folder / "claims_dev.jsonl")}
        corpus = {int(d["doc_id"]): d for d in v6b._jsonl(folder / "corpus.jsonl")}
        for case_id in dev:                      # the same pair as V6b's: the claim and its first cited abstract
            c = claims[case_id]
            doc_id = int(c["cited_doc_ids"][0])
            doc = corpus[doc_id]
            rationales = (c.get("evidence") or {}).get(str(doc_id)) or []
            gold = rationales[0]["label"] if rationales else v6b.SCIFACT_NEI
            abstract = " ".join(s.strip() for s in doc["abstract"])
            text = (f"Labels: SUPPORT, CONTRADICT, NOT_ENOUGH_INFO\n\nCLAIM: {c['claim'].strip()}\n\n"
                    f"ABSTRACT: {doc['title'].strip()}. {abstract}")
            by_id[case_id] = SuiteCase(case_id, text, gold, {"doc_id": doc_id, "reference": gold})
    return [by_id[i] for i in ids]


def mbpp_pool(root: Path) -> list[str]:
    """Task ids 11-974: the test, validation and train partitions (all carry tests; 1-10 are few-shot prompts)."""
    return [f"mbpp-{int(r['task_id']):03d}" for r in v6b._mbpp(root) if 11 <= int(r["task_id"]) <= 974]


# --------------------------------------------------------------------------------------------------------------------
# ARC-Challenge: one multiple-choice science question; the options are lettered A-E by position
# --------------------------------------------------------------------------------------------------------------------
LETTERS = ("A", "B", "C", "D", "E")
_ARC_WHOLE = re.compile(r"\W*\(?([A-E])\)?\W*")
_ARC_START = re.compile(r"\s*\(?([A-E])[.):]\s")
_ARC_INLINE = re.compile(r"(?<![A-Za-z])\(([A-E])\)(?![A-Za-z])"
                         r"|(?i:\b(?:answer|option|choice)\b(?:\s+is)?\s*[:\-]?\s*\(?)([A-E])(?![A-Za-z0-9])")


def arc_choice(text: str) -> str | None:
    """The one option letter a reply gives (after its last 'Answer:' line, if any), or None."""
    seg = v6b.final_segment(text or "").strip()
    found = _ARC_WHOLE.fullmatch(seg) or _ARC_START.match(seg)
    if found:
        return found.group(1)
    letters = {a or b for a, b in _ARC_INLINE.findall(seg)}
    return letters.pop() if len(letters) == 1 else None


def _arc_rows(root: Path) -> list[dict]:
    return [r for split in ("train", "validation", "test")
            for r in v6b._jsonl(root / "arc" / f"ARC-Challenge-{split}.jsonl")]


def arc_pool(root: Path) -> list[str]:
    ids = [f"arc-{r['id']}" for r in _arc_rows(root)]
    if len(set(ids)) != len(ids):
        raise ValueError("ARC-Challenge ids repeat across splits")
    return ids


def arc_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    by_id = {f"arc-{r['id']}": r for r in _arc_rows(root)}
    out = []
    for case_id in ids:
        r = by_id[case_id]
        texts, labels = r["choices"]["text"], r["choices"]["label"]
        options = "\n".join(f"{LETTERS[i]}. {t.strip()}" for i, t in enumerate(texts))
        gold = LETTERS[labels.index(r["answerKey"])]
        out.append(SuiteCase(case_id, f"Question: {r['question'].strip()}\n\nOptions:\n{options}", gold,
                             {"reference": gold}))
    return out


def _one_letter(out: str) -> bool:
    return arc_choice(out) is not None


def _only_a_letter(out: str) -> bool:
    return bool(_ARC_WHOLE.fullmatch((out or "").strip().strip("`'\"").strip()))


ARC_ONE = Check("the reply gives exactly one option letter", _one_letter, check_id="one-letter",
                known_bad=("", "A or B"), known_good="B")
ARC_ONLY = Check("the reply is a single option letter and nothing else", _only_a_letter, check_id="letter-only",
                 known_bad=("The answer is B because heat rises.", ""), known_good="C")


def arc_oracle(output: str, expected: Any) -> bool:
    return arc_choice(output) == str(expected)


ARC_LOOKS = {s.name: s for s in (library_steps.second_look(x, LETTERS) for x in LETTERS[:4])}

# --------------------------------------------------------------------------------------------------------------------
# banking77: one of 77 intents for a customer's banking query (the labels as categories.json lists them)
# --------------------------------------------------------------------------------------------------------------------
BANKING77_LABELS = (
    "card_arrival", "card_linking", "exchange_rate", "card_payment_wrong_exchange_rate", "extra_charge_on_statement",
    "pending_cash_withdrawal", "fiat_currency_support", "card_delivery_estimate", "automatic_top_up",
    "card_not_working", "exchange_via_app", "lost_or_stolen_card", "age_limit", "pin_blocked",
    "contactless_not_working", "top_up_by_bank_transfer_charge", "pending_top_up", "cancel_transfer", "top_up_limits",
    "wrong_amount_of_cash_received", "card_payment_fee_charged", "transfer_not_received_by_recipient",
    "supported_cards_and_currencies", "getting_virtual_card", "card_acceptance", "top_up_reverted",
    "balance_not_updated_after_cheque_or_cash_deposit", "card_payment_not_recognised", "edit_personal_details",
    "why_verify_identity", "unable_to_verify_identity", "get_physical_card", "visa_or_mastercard",
    "topping_up_by_card", "disposable_card_limits", "compromised_card", "atm_support",
    "direct_debit_payment_not_recognised", "passcode_forgotten", "declined_cash_withdrawal", "pending_card_payment",
    "lost_or_stolen_phone", "request_refund", "declined_transfer", "Refund_not_showing_up", "declined_card_payment",
    "pending_transfer", "terminate_account", "card_swallowed", "transaction_charged_twice", "verify_source_of_funds",
    "transfer_timing", "reverted_card_payment?", "change_pin", "beneficiary_not_allowed", "transfer_fee_charged",
    "receiving_money", "failed_transfer", "transfer_into_account", "verify_top_up", "getting_spare_card",
    "top_up_by_cash_or_cheque", "order_physical_card", "virtual_card_not_working",
    "wrong_exchange_rate_for_cash_withdrawal", "get_disposable_virtual_card", "top_up_failed",
    "balance_not_updated_after_bank_transfer", "cash_withdrawal_not_recognised", "exchange_charge",
    "top_up_by_card_charge", "activate_my_card", "cash_withdrawal_charge", "card_about_to_expire",
    "apple_pay_or_google_pay", "verify_my_identity", "country_support")
B77_PATTERNS = {v6b.label_key(lab): v6b._label_pattern(lab) for lab in BANKING77_LABELS}


def banking77_input(text: str) -> str:
    return f"Query: {text.strip()}\n\nIntent labels: {', '.join(BANKING77_LABELS)}"


def _b77_rows(root: Path, split: str) -> list[dict]:
    with (root / "banking77" / f"{split}.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def banking77_pool(root: Path) -> list[str]:
    return [f"banking77-test-{i:04d}" for i in range(len(_b77_rows(root, "test")))]


def banking77_cases(root: Path, ids: Sequence[str]) -> list[SuiteCase]:
    rows = _b77_rows(root, "test")
    out = []
    for case_id in ids:
        r = rows[int(case_id.rsplit("-", 1)[1])]
        out.append(SuiteCase(case_id, banking77_input(r["text"]), r["category"], {"reference": r["category"]}))
    return out


def banking77_examples(root: Path, study: Sequence[SuiteCase]) -> tuple[SuiteCase, ...]:
    train = [SuiteCase(f"banking77-train-{i:05d}", banking77_input(r["text"]), r["category"])
             for i, r in enumerate(_b77_rows(root, "train"))]
    return _sample_examples(train, study, "banking77", _first_line("Query: "))


def banking77_oracle(output: str, expected: Any) -> bool:
    return v6b.labels_named(output, B77_PATTERNS) == [v6b.label_key(str(expected))]


def banking77_norm(output: str) -> str | None:
    named = v6b.labels_named(output, B77_PATTERNS)
    return named[0] if len(named) == 1 else None


B77_ONE = Check("the reply names exactly one of the listed intent labels",
                lambda out: len(v6b.labels_named(out, B77_PATTERNS)) == 1, check_id="one-label",
                known_bad=("", "card_arrival or card_linking"), known_good="card_arrival")
B77_ONLY = Check("the reply is a single label and nothing else", lambda out: v6b.only_a_label(out, B77_PATTERNS),
                 check_id="label-only", known_bad=("The customer asks about card_arrival.", ""),
                 known_good="card_arrival")
B77_LOOKS = {s.name: s for s in (library_steps.second_look(lab, BANKING77_LABELS) for lab in BANKING77_LABELS)}


# --------------------------------------------------------------------------------------------------------------------
# The six specifications
# --------------------------------------------------------------------------------------------------------------------
def _from_v6b(key: str, *, feedback: Callable[[str, SuiteCase], str], dataset: str | None = None, **changes: Any
              ) -> FrontierSpec:
    s = v6b.SPECS[key]
    fields = {"key": key, "dataset": dataset or s.dataset, "task": s.task, "checks": s.checks,
              "optional_checks": dict(s.optional_checks), "pipeline_steps": dict(s.pipeline_steps),
              "searchable_steps": tuple(s.pipeline_steps), "oracle": s.oracle, "norm": s.norm, "prompts": s.prompts,
              "pool": s.pool, "cases": s.cases, "feedback": feedback, "reference": s.reference}
    return FrontierSpec(**{**fields, **changes})


def _gold(case: SuiteCase) -> str:
    return str(case.meta.get("reference", case.expected))


SPECS: dict[str, FrontierSpec] = {
    "gsm8k": _from_v6b("gsm8k", feedback=_number_feedback),
    "massive": _from_v6b("massive", feedback=_label_feedback(v6b.massive_norm), examples=massive_examples),
    "scifact": _from_v6b("scifact", feedback=_label_feedback(v6b.scifact_norm), pool=scifact_pool, cases=scifact_cases,
                         dataset="SciFact train and dev claims (Wadden et al. 2020; claims CC BY 4.0, abstracts ODC-By 1.0)"),
    "mbpp": _from_v6b("mbpp", feedback=mbpp_feedback, pool=mbpp_pool,
                      dataset="MBPP full, task ids 11-974 (Austin et al. 2021; CC BY 4.0)"),
    "arc": FrontierSpec(
        key="arc", dataset="ARC-Challenge, all splits (Clark et al. 2018; CC BY-SA 4.0)",
        task="Answer this multiple-choice science question. Reply with the letter of the correct option.\n\n{input}",
        checks=(ARC_ONE,), optional_checks={"letter-only": ARC_ONLY},
        pipeline_steps={"self_review": v6b.SELF_REVIEW, **ARC_LOOKS}, searchable_steps=("self_review", *ARC_LOOKS),
        oracle=arc_oracle, norm=arc_choice,
        prompts=("",
                 "Think step by step about the science involved, then write the final line as 'Answer: <letter>'.",
                 "Reply with the letter of the correct option only."),
        pool=arc_pool, cases=arc_cases, feedback=_label_feedback(arc_choice), reference=_gold),
    "banking77": FrontierSpec(
        key="banking77", dataset="banking77 test (Casanueva et al. 2020; CC BY 4.0)",
        task="Classify the customer's banking query into exactly one of the intent labels listed. Reply with the "
             "label.\n\n{input}",
        checks=(B77_ONE,), optional_checks={"label-only": B77_ONLY},
        pipeline_steps={"self_review": v6b.SELF_REVIEW, **B77_LOOKS}, searchable_steps=("self_review",),
        oracle=banking77_oracle, norm=banking77_norm,
        prompts=("",
                 "Choose the single intent that best matches what the customer wants. Reply with that label exactly as "
                 "it is written in the list, and nothing else.",
                 "Think briefly about what the customer wants, then write the final line as 'Answer: <label>'."),
        pool=banking77_pool, cases=banking77_cases, feedback=_label_feedback(banking77_norm), reference=_gold,
        examples=banking77_examples),
}
