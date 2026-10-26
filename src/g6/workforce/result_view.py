# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The result contract: one renderer for every user-facing mission or task return (Phase E Part 2, E8a).

The UI is the MCP return rendered in the user's chat agent (founder, 2026-10-03). A result is said in Design §5's
layers, in this order:
* the answer;
* the trust state, with the checks actually performed;
* what remains uncertain or failed (Design §23), or disputed (§24);
* the evidence (§13, §25) and what the work took (Doctrine §18).
Analysis and technical details are never in the default layers; ``more`` says how to ask for them.

Every rendering keeps these rules (plan rulings P2.18 to P2.22):
* three states, each as text plus a glyph, never colour alone (Design §10, §21); the wire values are unchanged (T1);
* confidence as words, never a percentage (§11);
* each default layer within its token budget (founder, 2026-10-04), cut at whole lines with the cut said;
* G6's own words avoid Design §19's list; a content line using one moves to technical details and is counted;
* every content line sits behind G6's own marker, so content can never forge the status line;
* nothing is invented: what the caller did not record is not shown.

The Trust Report (marketing spec §10) is one rendering of the same view. Standard library only.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from g6.workforce.result_card import (
    BLOCKED_ESCALATED,
    DEFAULT_LIMITATION,
    PRIVATE_TASK,
    QUALIFIED_DRAFT,
    STATES,
    VERIFIED,
    clean_task_title,
)

#: Design §10 and §21: each wire state as the person reads it (plan ruling P2.18).
LABELS = {VERIFIED: ("✓", "Verified"), QUALIFIED_DRAFT: ("⚠", "Use with caution"),
          BLOCKED_ESCALATED: ("⛔", "Could not verify")}
SUPPORT = {
    VERIFIED: "G6 completed the task and its independent checks support the result.",
    QUALIFIED_DRAFT: "G6 produced an answer, but some important parts could not be fully verified.",
    BLOCKED_ESCALATED: "G6 could not establish a sufficiently reliable answer from the available information.",
}
NOT_FINISHED = "Not finished yet"
GLYPHS = frozenset(glyph for glyph, _ in LABELS.values())
#: Other spellings envelopes carry; each reads as the wire state it names (down-only).
ALIASES = {"verified_ready": VERIFIED, "qualified_draft": QUALIFIED_DRAFT, "blocked_escalated": BLOCKED_ESCALATED,
           "awaiting_cost_confirmation": QUALIFIED_DRAFT}
#: Design §11's words (plan ruling P2.19); "None ran" when no independent check ran at all.
COVERAGE = ("Strong", "Partial", "Weak")
AGREEMENT = ("Agreement", "Mixed", "Disagreement", "None ran")
#: How a stored comparison is shown (founder ruling C5, 2026-10-09). The stored values above are kept so nothing that reads
#: them breaks; reviewers being consistent is not independent checking and is not proof of correctness, so the line never
#: says "independent" and never says "agreement". A value not stored reads "not recorded".
COMPARISON_WORDS = {"Agreement": "Consistent", "Mixed": "Mixed", "Disagreement": "Conflicting", "None ran": "Not evaluated"}
#: Design §25's labels, and who supplied an item (§13).
PROVENANCE = ("Source", "Calculation", "Inference", "Assumption", "Unverified claim")
SUPPLIERS = ("you", "your chat", "retrieved", "G6", "")
#: Design §19's words to avoid by default, and §3's and §1's mechanism terms (P2.21): a content line holding one moves
#: to technical details, counted.
_DESIGN_TERMS = (r"byzantine|epistemic|metacognit|swarm|quorum|lineage|four-valued|modal reasoning|"
                 r"recursive evaluator|orchestration|multi-agent|consensus|evaluator ensemble|self-improvement|"
                 r"grounding pipeline|decomposition tree|self-critique")
JARGON = re.compile(_DESIGN_TERMS, re.IGNORECASE)
#: The workforce's internal words: G6 says them plainly in its own text (``workforce/view.py``), and never filters
#: the owner's content by them ("door counts were estimated" is content; fix pass I5).
INTERNAL = re.compile(r"\boracles?\b|\brungs?\b|\bdoors?\b", re.IGNORECASE)
#: Every word G6's own text avoids (both lists).
AVOID = re.compile(f"{_DESIGN_TERMS}|{INTERNAL.pattern}", re.IGNORECASE)
#: Markdown that turns content into emphasis: escaped, so content never renders as a bold state (fix pass M1). An
#: underscore inside a word (``G6_E_RETRY``, ``snake_case``) is not emphasis and is left as it is.
_EMPHASIS = re.compile(r"[\\*]|(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])")
#: What a failure's reason says when it is given only in technical terms (fix pass I5: the failure itself stays).
TRIMMED = "its reason is given in technical terms only"
#: Tokens per default layer, and for the whole default ``result`` (founder 2026-10-04; plan ruling P2.20).
BUDGETS = {"answer": 500, "trust": 250, "caveats": 300, "evidence": 250}
RESULT_BUDGET = 1500
#: A failure surface's statuses (FE §12; E10c): challenged and held, challenged and failed, not tested.
SURFACE_STATUSES = ("passed", "failed", "not_tested")
#: How long one of the Trust Report's own lines may be (the standard, the challenges, what was not tested), so the
#: verdict always fits within the trust budget (ruling E10.17).
_REPORT_LINE = 50
LAYERS = tuple(BUDGETS)
_MARKS = {True: "✓", False: "✗", None: "–"}
_HINT_CHARS = 160
_COUNT_WORDS = {2: "Two", 3: "Three", 4: "Four"}


def wire_tokens(value: Any) -> int:
    """A conservative token count for a value as a chat agent receives it: one token per three characters of its JSON
    form (non-ASCII escaped, as every G6 return is sent), rounded up. No tokenizer dependency."""
    return math.ceil(len(json.dumps(value, default=str)) / 3)


def estimate_tokens(text: Any) -> int:
    return wire_tokens(str(text))


def _clip(text: str, tokens: int) -> str:
    """The longest start of ``text`` that fits in ``tokens`` with its ellipsis."""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid].rstrip() + "…") <= tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + "…"


def fit(lines: Iterable[str], budget: int, hint: str) -> list[str]:
    """``lines`` within ``budget`` tokens, cut at whole lines (a line too long alone is clipped). The cut is said, with
    how to ask for the rest, never silently dropped. Any surface may use it for its own budget (the discovery tools
    included)."""
    lines = list(lines)
    if estimate_tokens("\n".join(lines)) <= budget:
        return lines
    kept: list[str] = []
    for index, line in enumerate(lines):
        rest = len(lines) - index
        note = f"_({rest} more line{'' if rest == 1 else 's'} not shown in full: {hint})_"
        room = budget - estimate_tokens("\n".join([*kept, note])) - 1
        if estimate_tokens(line) <= room:
            kept.append(line)
            continue
        if room > 8:
            kept.append(_clip(line, room))
        return [*kept, note]
    return kept


@dataclass(frozen=True)
class Check:
    """One check actually performed (Design §12), in plain words; ``passed`` None: it ran and could not decide."""

    what: str
    passed: bool | None
    mechanism: str = ""


@dataclass(frozen=True)
class Problem:
    """Design §23: what failed, why, how material it is, what would resolve it, and the stable code last."""

    what: str
    why: str
    material: str
    resolve: str
    code: str = ""

    def text(self) -> str:
        code = f" (code {self.code})" if self.code else ""
        return f"{self.what}: {self.why}. {self.material} To resolve: {self.resolve}{code}"


@dataclass(frozen=True)
class Interpretation:
    """One reading of a result that the checks could not settle (Design §24)."""

    label: str
    text: str


@dataclass(frozen=True)
class Evidence:
    """One item behind the answer: its provenance label (Design §25) and who supplied it (§13; "" not recorded)."""

    label: str
    text: str
    supplied_by: str = ""

    def __post_init__(self) -> None:
        if self.label not in PROVENANCE:
            raise ValueError(f"a provenance label is one of {PROVENANCE}, got {self.label!r}")
        if self.supplied_by not in SUPPLIERS:
            raise ValueError(f"supplied_by is one of {SUPPLIERS}, got {self.supplied_by!r}")


@dataclass(frozen=True)
class Surface:
    """One way the work could fail that G6 tried to break it on, or did not (FE §12; E10c): ``what`` in plain words (a
    claim when it was challenged, a question when it was not), ``status`` passed, failed or not_tested."""

    what: str
    status: str

    def __post_init__(self) -> None:
        if self.status not in SURFACE_STATUSES:
            raise ValueError(f"a surface's status is one of {SURFACE_STATUSES}, got {self.status!r}")


@dataclass(frozen=True)
class ResultView:
    """Everything one result says, in the caller's recorded values; a field left empty is not recorded, never 0."""

    title: str
    answer: str
    state: str
    checks: tuple[Check, ...] = ()
    coverage: str = ""
    agreement: str = ""
    caveats: tuple[str, ...] = ()
    problems: tuple[Problem, ...] = ()
    interpretations: tuple[Interpretation, ...] = ()
    unresolved: str = ""
    evidence: tuple[Evidence, ...] = ()
    attempts: int | None = None
    cost: str = ""
    attention: str = ""
    tests: tuple[int, int] | None = None
    #: The standard the work was held to, in plain words (TS §28; "" not recorded), and its failure surfaces (FE §12).
    standard: str = ""
    surfaces: tuple[Surface, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in (*STATES, ""):
            raise ValueError(f"state is a wire value {STATES} or '' (not finished), got {self.state!r}")
        if self.coverage not in (*COVERAGE, ""):
            raise ValueError(f"coverage is one of {COVERAGE} or '', got {self.coverage!r}")
        if self.agreement not in (*AGREEMENT, ""):
            raise ValueError(f"agreement is one of {AGREEMENT} or '', got {self.agreement!r}")


def _flat(text: Any) -> str:
    """Content on one line: control characters and line breaks become spaces."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text)).split())


def _text(value: Any) -> str:
    """Content on one line with its markdown emphasis escaped: it renders as the text it is, so no title, check,
    caveat or reason can read as G6's status line (fix pass M1). The answer is quoted as it is (``_quote``)."""
    return _EMPHASIS.sub(lambda found: "\\" + found.group(0), _flat(value))


def _item(text: Any) -> str:
    return f"- {_text(text)}"


def _quote(text: str) -> list[str]:
    """The answer as a quoted block: each line begins with G6's own marker (plan ruling P2.22)."""
    out = []
    for raw in str(text).splitlines() or [""]:
        line = "".join(ch if ch.isprintable() or ch == "\t" else " " for ch in raw).rstrip()
        out.append(f"> {line}" if line else ">")
    return out


def _plain(lines: Iterable[str]) -> tuple[list[str], int]:
    """The lines a person reads by default, and how many moved to technical details (plan ruling P2.21)."""
    kept, moved = [], 0
    for line in lines:
        if JARGON.search(line):
            moved += 1
        else:
            kept.append(line)
    return kept, moved


def _hint(more: str) -> str:
    return _flat(more)[:_HINT_CHARS] or "ask for the technical details"


def comparison_of(agreement: str) -> str:
    """The reviewer comparison as it is shown: the founder's words for a stored value, "not recorded" for none."""
    return COMPARISON_WORDS.get(agreement, "not recorded")


def render_layers(view: ResultView, *, more: str = "") -> dict[str, list[str]]:
    """The four default layers as markdown lines, each within its budget (Design §5, §9; plan ruling P2.20)."""
    hint = _hint(more)
    title = clean_task_title(view.title)
    answer = [f"**Task:** {_text(title)}"] if title else []
    if view.answer.strip():
        answer += _quote(view.answer)
    elif view.state == BLOCKED_ESCALATED:
        answer.append("_No answer is shown: see what failed below._")
    else:
        answer.append("_No answer yet._")
    if view.state:
        glyph, label = LABELS[view.state]
        # COPY: the result's reviewer-comparison line, in the founder's words (ruling C5).
        trust = [f"**{glyph} {label}** — {SUPPORT[view.state]}",
                 (f"Evidence coverage: **{view.coverage or 'not recorded'}** · "
                  f"Reviewer comparison: **{comparison_of(view.agreement)}**")]
    else:
        trust = [f"**{NOT_FINISHED}**"]
    checks, moved = _plain(f"- {_MARKS[c.passed]} {_text(c.what)}" for c in view.checks)
    if checks:
        trust += ["Checks performed:", *checks]
    elif moved:  # checks ran, but each is described in technical terms: never "none recorded"
        trust.append(f"Checks performed: {moved}, described in technical terms ({hint})")
    elif view.state:
        trust.append("Checks performed: none recorded")
    # COPY: the result's labels for the failure challenges and for what was not tested.
    tried = [s for s in view.surfaces if s.status != "not_tested"]
    if tried:  # E10c: the ways G6 tried to break the work, each with how it came out
        trust.append("Failure challenges: " + "; ".join(f"{_MARKS[s.status == 'passed']} {_text(s.what)}"
                                                         for s in tried))
    items, cut = _plain(_item(c) for c in view.caveats)
    moved += cut
    for problem in view.problems:  # a failure is never moved out of the default layers; only its reason is trimmed
        if JARGON.search(problem.why):
            problem, moved = replace(problem, why=TRIMMED), moved + 1
        items.append(_item(problem.text()))
    if view.state == BLOCKED_ESCALATED and not items:  # a result that could not be verified always says what failed
        items.append(_item(f"G6 recorded no reason for this result ({hint})"))
    untried = [_text(s.what) for s in view.surfaces if s.status == "not_tested"]
    if untried:  # E10c: never a check mark for what no check exercised
        items.append(f"- Not tested: {'; '.join(untried)}")
    heading = {QUALIFIED_DRAFT: "**What remains uncertain**", BLOCKED_ESCALATED: "**What failed**"}.get(
        view.state, "**Worth knowing**")
    caveats = [heading, *items] if items else []
    if view.interpretations:
        readings, cut = _plain([*(_item(f"{i.label}: {i.text}") for i in view.interpretations),
                                *([_item(f"Why G6 could not resolve this: {view.unresolved}")]
                                  if view.unresolved else [])])
        moved += cut
        count = len(view.interpretations)
        caveats += [f"**{_COUNT_WORDS.get(count, str(count))} interpretations remain**", *readings]
    shown, cut = _plain(_item(f"{e.label}: {e.text}" + (f" (supplied by {e.supplied_by})" if e.supplied_by else ""))
                        for e in view.evidence)
    moved += cut
    evidence = ["**Evidence**", *shown] if shown else []
    took = [f"{view.attempts} attempt{'' if view.attempts == 1 else 's'}" if view.attempts is not None else "",
            f"cost: {_text(view.cost)}" if view.cost else "", f"your time: {_text(view.attention)}"
            if view.attention else ""]
    if any(took):
        evidence.append("**What it took:** " + " · ".join(part for part in took if part))
    if moved:
        evidence.append(f"_{moved} more line{'' if moved == 1 else 's'} use{'s' if moved == 1 else ''} technical "
                        f"terms: {hint}._")
    layers = {"answer": answer, "trust": trust, "caveats": caveats, "evidence": evidence}
    return {name: fit(lines, BUDGETS[name], hint) for name, lines in layers.items()}


def render(view: ResultView, *, more: str = "") -> dict:
    """The default ``result`` a return carries: the trust state as structured words, and the four layers as markdown
    (answer first). Everything else waits to be asked for (``more``)."""
    layers = render_layers(view, more=more)
    glyph, label = LABELS.get(view.state, ("", NOT_FINISHED))
    return {"state": view.state, "label": label, "glyph": glyph,
            "confidence": {"answer_status": label, "evidence_coverage": view.coverage or "not recorded",
                           "independent_checks": view.agreement or "not recorded",  # kept for compatibility (C5)
                           "reviewer_comparison": comparison_of(view.agreement)},
            "markdown": "\n\n".join("\n".join(lines) for lines in layers.values() if lines),
            "more": _hint(more)}


def render_trust_report(view: ResultView) -> str:
    """The G6 Trust Report (marketing spec §10; TS §28): one rendering of the same view, each line a recorded value or
    "not recorded" (never a default). When the view records them it also says the standard the work was held to, the
    ways G6 tried to break it and what it did not try (E10c); those lines are clipped and the whole report fits the
    trust budget, any cut said (ruling E10.17). Sharing it, redacted, is a later task (L1)."""
    glyph, label = LABELS.get(view.state, ("", NOT_FINISHED))
    if view.tests is not None:
        tests = f"{view.tests[0]} of {view.tests[1]} passed"
    elif view.checks:
        tests = f"none run; {sum(1 for c in view.checks if c.passed)} of {len(view.checks)} checks passed"
    else:
        tests = "not recorded"
    count = len(view.evidence)

    def own(text: str) -> str:
        return text if estimate_tokens(text) <= _REPORT_LINE else _clip(text, _REPORT_LINE)

    tried = [s for s in view.surfaces if s.status != "not_tested"]
    untried = [s for s in view.surfaces if s.status == "not_tested"]
    # COPY: the Trust Report's labels for the standard, the failure challenges and what was not tested.
    lines = ["G6 Trust Report", own(f"Task: {_text(clean_task_title(view.title)) or PRIVATE_TASK}"),
             *([own(f"Standard: {_text(view.standard)}")] if view.standard else []),
             f"Verdict: {glyph} {label}" if glyph else f"Verdict: {label}", f"Tests: {tests}",
             *([own("Failure challenges: " + "; ".join(f"{_MARKS[s.status == 'passed']} {_text(s.what)}"
                                                       for s in tried))] if tried else []),
             *([own("Not tested: " + "; ".join(_text(s.what) for s in untried))] if untried else []),
             f"Attempts: {view.attempts if view.attempts is not None else 'not recorded'}",
             f"Cost: {_text(view.cost) or 'not recorded'}",
             f"Human intervention: {_text(view.attention) or 'not recorded'}",
             f"Evidence: {count} item{'' if count == 1 else 's'}" if count else "Evidence: none recorded"]
    return "\n".join(fit(lines, BUDGETS["trust"], "ask for the full result"))


# --- job-pack, coder and coding-agent envelopes (Task 11) --------------------------------------------------------------

#: Where an envelope may declare a state besides its top level; the worst any of them declares wins (down-only).
_HOLDERS = ("trust", "reliability_envelope")
_ORDER = (BLOCKED_ESCALATED, QUALIFIED_DRAFT, VERIFIED)
_ANSWER_KEYS = ("answer", "business_summary", "summary")
_RESOLVE_KEYS = ("recommended_next_step", "safe_next_action", "required_human_decision", "next_step",
                 "suggested_action")
#: An independent evaluation the envelope recorded (``result_card.VERDICTS``): its words and its check mark.
_EVALUATED = {"passed": ("Agreement", True), "passed-with-limits": ("Mixed", True),
              "failed": ("Disagreement", False), "not-run": ("None ran", None)}
#: How to ask for more on an envelope surface: the rest of the same return.
ENVELOPE_MORE = "the other fields of this return hold the full detail"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return []


def _state(envelope: Mapping) -> str:
    """The worst state the envelope or a holder beside it declares, read down-only. None declared, one not recognised,
    or a failed call is blocked-escalated (Review Focus 3)."""
    declared = []
    for holder in (envelope, *(envelope.get(key) for key in _HOLDERS)):
        if isinstance(holder, Mapping) and holder.get("completion_state") not in (None, ""):
            raw = str(holder.get("completion_state")).strip().lower()
            declared.append(ALIASES.get(raw, raw))
    if not declared or envelope.get("ok") is False or any(state not in STATES for state in declared):
        return BLOCKED_ESCALATED
    return min(declared, key=_ORDER.index)


def _cards(envelope: Mapping) -> list[Mapping]:
    """The warning cards it carries: the job pack's own (under ``trust``), then the surface's."""
    out: list[Mapping] = []
    for holder in (envelope.get("trust"), envelope):
        card = holder.get("warning_card") if isinstance(holder, Mapping) else None
        if isinstance(card, Mapping) and not any(card is seen for seen in out):
            out.append(card)
    return out


def _first(sources: Iterable[Mapping], keys: Iterable[str]) -> str:
    for source in sources:
        for key in keys:
            text = source.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _answer_of(envelope: Mapping) -> str:
    text = _first([envelope], _ANSWER_KEYS)
    if text:
        return text
    for key in ("deliverable", "artifact"):
        found = envelope.get(key)
        if isinstance(found, Mapping) and isinstance(found.get("summary"), str) and found["summary"].strip():
            return found["summary"].strip()
    result = envelope.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    verdict = envelope.get("verdict")
    if isinstance(verdict, str) and verdict.strip():  # the coding-agent check's own answer is its finding
        return f"The answer was checked, and the check returned {verdict.strip().lower()!r}."
    return ""


def _evidence_of(envelope: Mapping) -> list[Evidence]:
    out = []
    items = envelope.get("evidence")
    for entry in items if isinstance(items, list) else []:
        if isinstance(entry, Mapping):
            kind = str(entry.get("kind") or "")
            receipt = entry.get("tool_execution_id") or entry.get("execution_id")
            text = str(entry.get("source") or kind or "an item") + (f" (receipt {receipt})" if receipt else "")
            out.append(Evidence("Calculation" if kind in ("tool_execution", "backend_execution") else "Source", text))
        elif isinstance(entry, str) and entry.strip():
            out.append(Evidence("Source", entry.strip()))
    count = envelope.get("evidence_sources")
    if not out and isinstance(count, int) and not isinstance(count, bool) and count > 0:
        out.append(Evidence("Source", f"{count} source{'' if count == 1 else 's'} recorded"))
    return out


def from_envelope(envelope: Mapping) -> ResultView:
    """A job-pack, coder or coding-agent envelope as a view (plan rulings P2.19, P2.23): only what it recorded, its
    state never better than the worst it declares, a withheld answer never shown. A stored job (``job_get_result``)
    holds its own return under ``response``: the view reads it there, the surface's state one more holder (fix I3)."""
    surface = None
    if isinstance(envelope.get("response"), Mapping):
        surface = envelope
        envelope = {**{key: value for key, value in envelope.items() if key != "response"}, **envelope["response"]}
    state = _state(envelope)
    if surface is not None:
        state = min((state, _state(surface)), key=_ORDER.index)
    cards = _cards(envelope)
    withheld = any(card.get("deliverable_withheld") is True for card in cards)
    report = envelope.get("verification_report") if isinstance(envelope.get("verification_report"), Mapping) else {}
    nested = envelope.get("reliability_envelope") if isinstance(envelope.get("reliability_envelope"), Mapping) else {}
    holders = [envelope, *(h for h in (envelope.get("evidence"), nested.get("evidence")) if isinstance(h, Mapping))]
    missing = list(dict.fromkeys(t for h in holders for t in _strings(h.get("missing_evidence"))))
    findings = list(dict.fromkeys(t for h in holders for t in _strings(h.get("findings"))))
    verified = list(dict.fromkeys(t for card in cards for t in _strings(card.get("verified"))))
    unverified = list(dict.fromkeys(t for card in cards for t in _strings(card.get("not_verified"))))
    failed = _strings(report.get("checks_failed"))
    checks = [Check(text, True) for text in verified] + [Check(f"A check failed: {text}", False) for text in failed]
    agreement, not_run = "", []
    evaluation = envelope.get("independent_evaluation")
    if isinstance(evaluation, Mapping) and evaluation.get("verdict") in _EVALUATED:
        agreement, passed = _EVALUATED[evaluation["verdict"]]
        by = str(evaluation.get("by") or "").strip()
        if evaluation["verdict"] != "not-run":  # only a review that ran is a check performed (Design §12; fix I4)
            checks.append(Check("Checked by a separate reviewer" + (f" ({by})" if by else ""), passed))
        else:
            not_run = [f"A separate review did not run: {why}" for why in _strings(evaluation.get("reason"))]
    if report.get("deterministic") is True and isinstance(report.get("verdict"), str) and report["verdict"]:
        checks.append(Check(f"A deterministic check of the answer returned {report['verdict']!r}", None))
    caveats = [*(t for t in _strings(envelope.get("known_limitations")) if t != DEFAULT_LIMITATION), *unverified,
               *(t for card in cards for t in _strings(card.get("main_risks"))),
               *_strings(envelope.get("missing_checks")),
               *(f"Missing evidence: {t}" for t in missing), *(f"Finding: {t}" for t in findings), *not_run]
    if state == QUALIFIED_DRAFT and not caveats:
        caveats = [t for card in cards for t in _strings(card.get("why_this_status"))] or _strings(
            _first(cards, ("message",)))
    if state == QUALIFIED_DRAFT:  # what the check asks for is what would resolve the doubt (fix (d))
        fixes = (_strings(envelope.get("required_revisions")) or _strings(envelope.get("required_next_actions"))
                 or _strings(_first(cards, ("recommended_next_step",))))
        if fixes:
            caveats.append("What would resolve it: " + "; ".join(t.rstrip(".") for t in fixes[:3]))
    problems: tuple[Problem, ...] = ()
    if state == BLOCKED_ESCALATED:
        why = "; ".join([*_strings(envelope.get("block_reason")),
                         *(t for card in cards for t in _strings(card.get("why_this_status")))][:6])
        why = why or _first([*cards, envelope], ("why", "message", "text", "error")) or "no reason was recorded"
        resolve = " ".join(part for part in (_first([*cards, envelope], _RESOLVE_KEYS),
                                             _first(cards, ("how_to_override",)) if withheld else "") if part)
        resolve = resolve or "no resolution was recorded; the other fields of this return hold the detail"
        code = next((str(c.get("code")) for c in cards if str(c.get("code") or "").startswith("G6_")), "") or next(
            (str(envelope.get(k)) for k in ("error_code", "code") if str(envelope.get(k) or "").startswith("G6_")), "")
        if withheld:
            problems = (Problem("This answer is withheld", why, "All of it: none of the answer is shown, so do not "
                                "act on any part of it.", resolve, code or "G6_E_DELIVERABLE_WITHHELD"),)
        else:
            problems = (Problem("G6 could not verify this result", why, "Do not rely on any part of it until this is "
                                "resolved.", resolve, code),)
    coverage = ""
    if verified or unverified or failed:
        coverage = "Strong" if verified and not unverified and not failed else ("Partial" if verified else "Weak")
    title = envelope.get("task_title")
    return ResultView(title=title if isinstance(title, str) else "", answer="" if withheld else _answer_of(envelope),
                      state=state, checks=tuple(checks), coverage=coverage, agreement=agreement,
                      caveats=tuple(dict.fromkeys(caveats)), problems=problems, evidence=tuple(_evidence_of(envelope)))


def attach_result_view(envelope: Any, *, more: str = ENVELOPE_MORE) -> Any:
    """``envelope`` with the result contract added as ``result_view`` (E8a), every other field untouched (additions
    only). A call site calls it LAST, after every step that may change the envelope's state. A view that cannot be
    built reads "Could not verify", never better; this never raises."""
    if not isinstance(envelope, dict):
        return envelope
    try:
        envelope["result_view"] = render(from_envelope(envelope), more=more)
    except Exception as exc:  # noqa: BLE001 - a return is never lost to its own summary
        envelope["result_view"] = unbuilt(exc, more=more)
    return envelope


def unbuilt(exc: BaseException, *, more: str = ENVELOPE_MORE) -> dict:
    """The rendered result of a view that could not be built: "Could not verify", never better. Every surface that
    builds a view falls back to it, so a return is never lost to its own summary."""
    failed = Problem("G6 could not summarise this result", type(exc).__name__,
                     "The full result is in the other fields of this return.",
                     "read the other fields, or report this with them")
    return render(ResultView(title="", answer="", state=BLOCKED_ESCALATED, problems=(failed,)), more=more)


__all__ = ["AGREEMENT", "ALIASES", "AVOID", "BLOCKED_ESCALATED", "BUDGETS", "COMPARISON_WORDS", "COVERAGE", "ENVELOPE_MORE", "GLYPHS",
           "INTERNAL", "JARGON", "LABELS", "LAYERS", "NOT_FINISHED", "PROVENANCE", "QUALIFIED_DRAFT", "RESULT_BUDGET",
           "STATES", "SUPPLIERS", "SUPPORT", "SURFACE_STATUSES", "TRIMMED", "VERIFIED", "Check", "Evidence",
           "Interpretation", "Problem", "ResultView", "Surface", "attach_result_view", "comparison_of", "estimate_tokens", "fit", "from_envelope", "render", "render_layers",
           "render_trust_report", "unbuilt", "wire_tokens"]
