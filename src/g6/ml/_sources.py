# SPDX-License-Identifier: Apache-2.0
"""Where every rule the ml family's fits and scores apply comes from: the committed source register, or G6's own.

``_data/source_register.json`` holds one entry per primary source (the scikit-learn User Guide of the release series
``g6[ml]`` installs): issuing body, title, edition, publication date, jurisdiction, URL, licence and redistribution
terms, the date it was retrieved, the SHA-256 of the retrieved page and the exact clause used. :data:`RULES` names each
rule and where it comes from: a register entry; a guard of G6's own, which says what it can do to an answer (refuse,
escalate, lower a label or add a note) and cites the entry it rests on, or ``"g6"`` when no source sets its threshold;
or the caller's input. None can raise a label. Each rule but a caller's names its tier and the function that applies
it, which is what its certificate checks.

:func:`stale_sources` raises a finding for an entry older than its review window. ``tools/check_sources.py`` re-fetches
each entry on a schedule and compares the hash; the default test suite stays offline. Each rule has an independent
reader's pass in its component's certificate; no qualified person has reviewed them (``g6.core.vetting``).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

from g6.core import vetting

#: The fields every register entry fills in.
FIELDS = ("id", "issuing_body", "title", "edition", "published", "jurisdiction", "url", "licence", "retrieved",
          "sha256", "clause", "review_months")


@dataclass(frozen=True)
class Rule:
    """One rule a fit or a score applies. ``source`` is a register entry's id; for a guard on a threshold of G6's own,
    ``"g6"``; for the caller's input, the fields it names. ``effect`` is what a guard (``own``) can do to an answer:
    refuse, escalate, lower_label or note, never more. ``tier`` is what the rule's certificate checks (computes,
    property or guard) and ``implements`` the function that applies it (``path:function``); ``own_choice`` marks a
    register rule applying a threshold its source does not set (G6's own choice, which holds its certificate at
    qualified-draft)."""

    rule: str
    kind: Literal["register", "own", "caller"]
    source: str | tuple[str, ...]
    effect: str = ""
    tier: str = ""
    implements: str = ""
    own_choice: bool = False


RULES: dict[str, Rule] = {
    "held_out": Rule("a score is measured on rows held out of training, which the extension chooses itself from the "
                     "rows' own hash, so the same rows always give the same held-out rows whatever the seed",
                     "register", "sklearn-1.9-cv-3.1", tier="computes", implements="g6/ml/_types.py:make_split"),
    "stratified": Rule("for classification each class keeps its share of the rows on both sides of the split",
                       "register", "sklearn-1.9-cv-3.1.2.2", tier="computes", implements="g6/ml/_types.py:make_split"),
    "grouped": Rule("rows with identical features form one group and stay on one side of every fold (G6's grouping "
                    "of identical rows; the source groups dependent samples), the folds dealt in an order the seed "
                    "shuffles", "register", "sklearn-1.9-cv-3.1.2.4", tier="computes",
                    implements="g6/ml/_fit.py:folds"),
    "grouped_hold_out": Rule("rows with identical features also stay on one side of the hold-out split, so no "
                             "held-out row repeats a training row (G6's grouping of identical rows; the source groups "
                             "dependent samples)", "register", "sklearn-1.9-cv-3.1.2.4", tier="property",
                             implements="g6/ml/_types.py:make_split"),
    "cv_training_part": Rule("candidates are compared by k-fold cross-validation inside the training part only; the "
                             "held-out rows score the chosen candidate once and choose nothing", "register",
                             "sklearn-1.9-cv-3.1", tier="property", implements="g6/ml/_fit.py:cross_validate"),
    "search_training_part": Rule("a search compares settings by k-fold cross-validation inside the training part "
                                 "only, with the family's pipeline and folds and an equal share of the trial cap and "
                                 "of the time budget for each candidate; the held-out rows score the chosen setting "
                                 "once and choose nothing", "register", "sklearn-1.9-cv-3.1", tier="property",
                                 implements="g6/ml/adapt_automl/flaml_child.py:search"),
    "preprocessing_in_pipeline": Rule("imputation, standard scaling (only for estimators flagged scale) and one-hot "
                                      "encoding (at most 50 categories a column, the rarer ones grouped as "
                                      "infrequent: G6's own cap) are fitted on training rows only, as steps of one "
                                      "pipeline, also inside each fold", "register", "sklearn-1.9-pitfalls-12.2",
                                      tier="property", implements="g6/ml/_fit.py:_pipeline", own_choice=True),
    "seed": Rule("one integer seed sets the folds, the resamples and every estimator that takes a random_state; it "
                 "does not choose the held-out rows", "register", "sklearn-1.9-pitfalls-12.3.3.1", tier="property",
                 implements="g6/ml/_fit.py:_estimator"),
    "baseline": Rule("a baseline that ignores the features (the most frequent training class, or the mean training "
                     "target) is scored on the same held-out rows; a tie between classes goes to the first label in "
                     "text order (G6's own; the source is silent on ties)", "register", "sklearn-1.9-metrics-3.4.8",
                     tier="property", implements="g6/ml/_metrics.py:baseline"),
    "accuracy": Rule("accuracy: the share of predictions equal to the true class", "register",
                     "sklearn-1.9-metrics-3.4.4.2", tier="computes", implements="g6/ml/_metrics.py:accuracy"),
    "balanced_accuracy": Rule("balanced accuracy: the mean over classes of the share of each class predicted as "
                              "itself", "register", "sklearn-1.9-metrics-3.4.4.4", tier="property",
                              implements="g6/ml/_metrics.py:balanced_accuracy"),
    "r2": Rule("R squared: one less the residual over the total sum of squares about the mean; undefined, and "
               "reported so, when the held-out targets are all equal (the original definition, not a forced finite "
               "value)", "register", "sklearn-1.9-metrics-3.4.6.1", tier="computes",
               implements="g6/ml/_metrics.py:r2"),
    "mae": Rule("mean absolute error", "register", "sklearn-1.9-metrics-3.4.6.2", tier="computes",
                implements="g6/ml/_metrics.py:mae"),
    "rmse": Rule("root mean squared error, in the target's units", "register", "sklearn-1.9-metrics-3.4.6.3",
                 tier="computes", implements="g6/ml/_metrics.py:rmse"),
    "headline_imbalance": Rule("the headline is balanced accuracy when the smallest class holds under 10% of the "
                               "training rows (a threshold of G6's own; the held-out labels choose nothing), accuracy "
                               "otherwise; both are always reported", "register", "sklearn-1.9-metrics-3.4.4.4",
                               tier="property", implements="g6/ml/_types.py:class_balance", own_choice=True),
    "held_out_from_rows": Rule("each scoring of a held-out set is recorded by its hash, and every scored reply says "
                               "how many times it was scored before; once a set has been scored before, the reply "
                               "says a result kept after several tries on it is optimistic", "own",
                               "g6", "note", tier="guard", implements="g6/ml/_report.py:_caution"),
    "leakage_probes": Rule("a feature equal to the target, with Pearson or Spearman |r| of 0.99 or more against a "
                           "numeric target (or a two-class numeric classification target) on the training rows, or "
                           "predicting it alone for every row from the others (leave-one-out) at 0.99 or better and "
                           "above a baseline, is refused as a suspected leak; the probes need at least 5 rows (10 for "
                           "the one-feature probe) before they find anything (thresholds of G6's own)", "own", "g6",
                           "refuse", tier="guard",
                           implements="g6/ml/_types.py:_leakage_findings"),
    "too_good": Rule("a held-out headline of 0.99 or more, once rounded to 6 places as it is reported, is refused as "
                     "a suspected leak the features carry between them (a total and its parts), which the one-feature "
                     "probes cannot see; it is never reported as a measurement (a threshold of G6's own)", "own", "g6",
                     "refuse",
                     tier="guard", implements="g6/ml/_report.py:_too_good"),
    "min_held_out": Rule("fewer than 10 independent held-out rows (rows with identical features count once), a class "
                         "missing from either side of the split, or a class with fewer than 2 distinct rows, is "
                         "refused", "own", "g6", "refuse", tier="guard", implements="g6/ml/_types.py:prepare"),
    "interval": Rule("the model is said to beat the baseline only when the lower end of the lift's 95% percentile "
                     "interval is above zero; the interval comes from 1,000 seeded resamples of the held-out rows by "
                     "group (rows with identical features drawn together: a cluster bootstrap), fewer when resamples "
                     "times held-out rows would pass 5,000,000, as the interval says; otherwise the result says the "
                     "model does not clearly beat the baseline", "own", "g6", "note", tier="guard",
                     implements="g6/ml/_metrics.py:compare"),
    "overfit_gap": Rule("the train score less the held-out score is reported; a gap of 0.10 or more (rounded to 6 "
                        "places) adds a note", "own", "g6", "note", tier="guard",
                        implements="g6/ml/_report.py:_gap_note"),
    "search_bounds": Rule("a search's trial cap (at most 200) and time budget (at most 300 s) are the caller's, "
                          "refused beyond them by the typed input; the search's process is stopped at 1.5 times the "
                          "budget plus 60 s, and the result says what ended it", "caller", ("trials", "time_budget_s")),
    "limits": Rule("the other sizes and limits one request may carry are the caller's within the typed input's ranges "
                   "(20,000 rows, 200 features, 1,000 rows to predict, a fit deadline timeout_s of at most 600 s, "
                   "each estimator's parameters within their ranges), refused beyond them before anything is "
                   "prepared; the fit's process is stopped at its deadline and its memory capped at 1,536 MB",
                   "caller", ("rows", "features", "predict_rows", "timeout_s", "params")),
    "bounds": Rule("a request is refused before anything is prepared when a row has a column that is neither the "
                   "target nor a feature, a text cell holds more than 10,000 characters, the text cells hold more "
                   "than 5,000,000 characters in all, or a classification target has more than 100 classes; the rows to "
                   "predict are held to the same column and text limits", "own", "g6", "refuse", tier="guard",
                   implements="g6/ml/_types.py:_bounds"),
}


def register() -> dict[str, Any]:
    """The committed register, read as package data."""
    text = resources.files("g6.ml").joinpath("_data/source_register.json").read_text(encoding="utf-8")
    return json.loads(text)


def entries() -> dict[str, dict[str, Any]]:
    """Each register entry by its id."""
    return {e["id"]: e for e in register()["entries"]}


def register_problems() -> list[str]:
    """What is wrong with the register or the rule table, or ``[]``: an entry with a field missing or empty, a hash
    that is not SHA-256, a date that is not one, a rule naming an entry the register does not have, an entry no rule
    uses, or a rule of G6's own that does not say what it can do to an answer."""
    out: list[str] = []
    found = entries()
    for entry_id, entry in found.items():
        missing = [f for f in FIELDS if entry.get(f) in (None, "")]
        if missing:
            out.append(f"{entry_id}: {', '.join(missing)} not filled in")
        clause_hash = hashlib.sha256(str(entry.get("clause", "")).encode("utf-8")).hexdigest()
        field = "sha256" if entry.get("hash_scope") == "clause_utf8" else "clause_sha256"
        if clause_hash != entry.get(field):
            out.append(f"{entry_id}: recorded clause hash differs")
        sha = str(entry.get("sha256", ""))
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            out.append(f"{entry_id}: sha256 is not a SHA-256 hex digest")
        try:
            _dt.date.fromisoformat(str(entry.get("retrieved", "")))
        except ValueError:
            out.append(f"{entry_id}: retrieved is not a date")
    used = {r.source for r in RULES.values() if r.kind != "caller"}
    for name, rule in RULES.items():
        if rule.kind != "caller" and rule.source not in found and not (rule.kind == "own" and rule.source == "g6"):
            out.append(f"rule {name}: {rule.source} is not in the register")
        if rule.kind == "own" and rule.effect not in ("refuse", "escalate", "lower_label", "note"):
            out.append(f"rule {name}: a guard of G6's own must say what it can do to an answer (refuse, escalate, "
                       "lower_label or note)")
    out += [f"{entry_id}: no rule uses it" for entry_id in sorted(set(found) - used)]
    return out


def _add_months(day: _dt.date, months: int) -> _dt.date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return day.replace(year=year, month=month, day=min(day.day, last))
        except ValueError:
            continue
    raise ValueError(f"no date {months} months after {day}")  # pragma: no cover - every month has a 28th


def stale_sources(today: _dt.date | None = None) -> list[dict[str, str]]:
    """A finding for each register entry retrieved longer ago than its review window."""
    today = today or _dt.date.today()
    out = []
    for entry_id, entry in sorted(entries().items()):
        due = _add_months(_dt.date.fromisoformat(entry["retrieved"]), int(entry["review_months"]))
        if today > due:
            out.append({"finding": "stale_source", "source": entry_id, "retrieved": entry["retrieved"],
                        "review_due": due.isoformat(),
                        "message": f"{entry_id} was retrieved on {entry['retrieved']} and was due for review on "
                                   f"{due.isoformat()}: re-read it against its URL before relying on this rule"})
    return out


def cited(rules: tuple[str, ...]) -> dict[str, Any]:
    """The rules a result applied, each with where it comes from, as evidence carries them, and any stale source."""
    found = entries()
    items = []
    for name in rules:
        rule = RULES[name]
        item: dict[str, Any] = {"rule": name, "says": rule.rule, "kind": rule.kind}
        if rule.kind == "caller":
            item["caller_fields"] = list(rule.source)
        elif rule.source in found:
            entry = found[rule.source]
            item.update(source=entry["id"], title=entry["title"], url=entry["url"], clause=entry["clause"])
        else:
            item["source"] = rule.source
        if rule.kind == "own":
            item["effect"] = rule.effect
        items.append(item)
    wanted = {RULES[n].source for n in rules if RULES[n].kind != "caller" and RULES[n].source in found}
    stale = [s for s in stale_sources() if s["source"] in wanted]
    return {"kind": "ml_rules", "rules": items, "stale_sources": stale,
            "review": vetting.NO_QUALIFIED_REVIEW}


__all__ = ["FIELDS", "RULES", "Rule", "cited", "entries", "register", "register_problems", "stale_sources"]
