# SPDX-License-Identifier: Apache-2.0
"""V10's secondary measures that read more than the primary ones (docs/validation/patch_review_search/PROTOCOL.md,
version 2, "Secondary measures" and "Analysis"). Each reads only what the record holds, and says what it could not read.

**Before any label** (the driver records them before the record's hash, so they are frozen before the labels are read):

- ``template_applicability``: for each template, the patches on which arm T applied it, refused it (with G6's reasons)
  or did not choose it, from T's reports.
- ``formal_path``: from T's activities on ``state_invariant.v1`` instances (and ``access_rule.v1``'s multi-step form,
  which goes through it): the state models built, refused for exceeding the limits, or found vacuous or conflicting;
  the traces found; the traces reproduced on the code; ``model_only``; ``not_replayable``.

**After the labels** (the analysis):

- ``adequacy``: per patch, whether any mutant of the changed lines survived the stated checks (T's
  ``test_adequacy``), against the label, with a row for patches with no mutation run; the difference in the defective
  share between patches with and without surviving mutants, with Newcombe's hybrid score interval for two independent
  proportions (``newcombe_independent``, method 10 of his 1998 paper). In this version of G6 test adequacy changes no
  outcome.
- ``reason_codes``: for each reason code G6 gave a finding, per arm (A, C and T), the number of findings and the shares
  on defective patches, reproducible, compatible with the reference fix and not comparable, each with the bootstrap's
  interval (``share_interval``: tasks resampled within each repository with all their findings, the same task sets as
  V9's bootstrap for the same seed).
- ``measure_similarity``: in the label step, inside the vault, after the arms' record is hashed: the similarity of each
  model-written check the record holds to its task's hidden tests (``similarity``, by the measure and threshold the
  freeze entry sets, ``check_similarity``). The findings in a check above the threshold are listed, and the analysis
  gives the headline counts without them too.
"""
from __future__ import annotations

import difflib
import io
import math
import random
import re
import tokenize
from statistics import NormalDist

from patch_review_search.runner import PROPOSED_FINDINGS
from patch_review_utility import stats

CONFIDENCE = 0.95
Z95 = NormalDist().inv_cdf(0.5 + CONFIDENCE / 2)
#: A template's name, as G6's registry gives it (``example.v1``); a proposer stage of another name (``scenarios``,
#: ``free_form``) is not a template.
TEMPLATE = re.compile(r"[a-z_]+\.v\d+")
FORMAL = "state_invariant.v1"
#: Over the limits, read from G6's structured record where it has one: a model check refused with one of these codes
#: (``g6.model_checking.envelope``; the coordinator's ruling on item 81).
OVER_LIMIT_CODES = frozenset({"G6_E_MODEL_CHECK_INPUT_TOO_LARGE", "G6_E_MODEL_CHECK_OUTPUT_TOO_LARGE",
                              "G6_E_MODEL_CHECK_TIME_LIMIT_TOO_LONG"})
#: Over the limits, read from G6's own words where its record has no code: a refused instance's reason as
#: ``verification_templates._state_invariant`` writes it (pinned by the tests and by ``freeze_check``, so a change of
#: wording fails loudly).
OVER_LIMIT_REASONS = (re.compile(r"the model is over model_checking's limits: "),
                      re.compile(r"depth \d+ is above the limit of \d+ steps for a search whose traces are replayed"),
                      re.compile(r"traces \d+ is above the limit of \d+ replayed traces"),
                      re.compile(r"the model has \d+ state variables; with the \d+ G6 adds to keep each trace's history "
                                 r"\(a step counter and one per step\), that is over model_checking's limit of \d+"))
#: The words of ``OVER_LIMIT_REASONS`` as G6's source writes them (``freeze_check`` requires each at the frozen commit).
G6_LIMIT_WORDS = ("the model is over model_checking's limits: ",
                  "is above the limit of {MAX_FORMAL_DEPTH} steps for a search whose traces ",
                  "is above the limit of {MAX_TRACES} replayed traces",
                  "state variables; with the {1 + b.depth} G6 adds to ",
                  "keep each trace's history (a step counter and one per step), that is over model_checking's ",
                  "limit of {MAX_VARIABLES}")
ACCESS = "access_rule.v1"
#: The reason code of a rerun recorded without one (a record written before the driver kept it).
NO_CODE = "not recorded"
ADEQUACY_NOTE = "in this version of G6 test adequacy changes no outcome (PROTOCOL.md, version 2)"
#: The similarity measures a freeze entry may name.
MEASURES = ("token_containment.v1",)
#: The scripts' proposal for the freeze entry (interpretation 81): a check is above the threshold when more than half of
#: its tokens lie in runs of at least 8 tokens its task's hidden tests hold in the same order.
PROPOSED_SIMILARITY = {"measure": "token_containment.v1", "min_run": 8, "threshold": 0.5}


def _activities(found) -> list[dict]:
    completion = ((found or {}).get("assessment") or {}).get("completion") if isinstance(found, dict) else None
    activities = completion.get("activities") if isinstance(completion, dict) else None
    return [a for a in activities if isinstance(a, dict)] if isinstance(activities, list) else []


def _t_report(row: dict):
    return ((row.get("arms") or {}).get("T") or {}).get("report")


# --- before any label ------------------------------------------------------------------------------------------------

def template_applicability(rows: list[dict]) -> dict:
    """For each template, the patches on which arm T applied it, refused it (no instance applied; G6's reasons) or
    did not choose it (its proposer stage replied with no instance, or was skipped, with G6's reason), from the
    driver's ``rows`` (each ``{patch, arms: {T: {report}}}``); a refusal on a patch where another instance of the
    template applied is listed too (``refused_too``). A patch with no T report is counted (``no_report``)."""
    per: dict[str, dict] = {}
    no_report = []
    for row in rows:
        found = _t_report(row)
        if not isinstance(found, dict):
            no_report.append(row["patch"])
            continue
        activities = _activities(found)
        applied = {str(a.get("template")) for a in activities if a.get("activity") == "template instance"
                   and TEMPLATE.fullmatch(str(a.get("template") or ""))}
        refused: dict[str, list[str]] = {}
        for a in activities:
            if a.get("activity") == "proposal" and a.get("result") == "refused" and TEMPLATE.fullmatch(
                    str(a.get("stage") or "")):
                refused.setdefault(str(a["stage"]), []).append(str(a.get("reason") or ""))
        stages = {str(a["stage"]): a for a in activities if a.get("activity") == "proposer stage"
                  and TEMPLATE.fullmatch(str(a.get("stage") or ""))}
        for name in sorted(applied | set(refused) | set(stages)):
            entry = per.setdefault(name, {"applied": [], "refused": [], "not_chosen": [], "refused_too": []})
            if name in applied:
                entry["applied"].append(row["patch"])
                if name in refused:
                    entry["refused_too"].append({"patch": row["patch"], "reasons": refused[name]})
            elif name in refused:
                entry["refused"].append({"patch": row["patch"], "reasons": refused[name]})
            else:
                stage = stages[name]
                why = (f"its proposer stage was skipped: {stage.get('reason') or 'no reason given'}"
                       if stage.get("result") == "skipped" else
                       f"its proposer stage ended {stage.get('result')}: {stage.get('reason') or 'no reason given'}"
                       if stage.get("result") not in (None, "replied") else
                       "its proposer stage replied with no instance of it")
                entry["not_chosen"].append({"patch": row["patch"], "why": why})
    return {"templates": per, "no_report": no_report,
            "note": "from arm T's reports, recorded before any label was read"}


COUNTS = ("models_built", "refused_over_limits", "refused_other", "vacuous", "conflicting", "undecided", "searched",
          "traces_found", "traces_reproduced", "model_only", "not_replayable", "not_replayed")


def _formal_records(activities: list[dict]) -> dict[str, dict]:
    found = {}
    for a in activities:
        part = a.get("formal")
        specification = (part or {}).get("specification") if isinstance(part, dict) else None
        if not isinstance(specification, dict):
            continue
        if specification.get("template") != FORMAL and specification.get("through") != FORMAL:
            continue
        found.setdefault(str(a.get("check") or a.get("instance") or len(found)), part)
    return found


def over_limits_by_reason(reason: str) -> bool:
    """Whether a refusal's reason is G6's own words for a state model over the limits (``OVER_LIMIT_REASONS``)."""
    return any(pattern.search(str(reason or "")) for pattern in OVER_LIMIT_REASONS)


def _refusal(row: dict, a: dict) -> dict | None:
    """A refusal on the formal path, read from G6's code where the activity has one and from its words otherwise; None
    for an activity that is not one."""
    kind, reason = a.get("activity"), str(a.get("reason") or "")
    template = str(a.get("template") or a.get("stage") or "")
    if kind == "model check" and a.get("verdict") == "refused" and a.get("code"):
        over, read_from = a["code"] in OVER_LIMIT_CODES, "code"
    elif kind == "template instance" and a.get("result") == "refused" and (
            template == FORMAL or (template == ACCESS and over_limits_by_reason(reason))):
        over, read_from = over_limits_by_reason(reason), "reason text"
    elif kind == "proposal" and a.get("result") == "refused" and a.get("stage") == FORMAL:
        over, read_from = over_limits_by_reason(reason), "reason text"
    else:
        return None
    return {"patch": row["patch"], "instance": a.get("instance") or a.get("check"), "requirement": a.get("requirement"),
            "code": a.get("code"), "reason": reason[:300], "over_limits": over, "read_from": read_from}


def formal_path(rows: list[dict]) -> dict:
    """The formal path's counts per patch and in total (the module docstring), from arm T's activities, and every
    refusal on it: a refused model check, read by its code; a refused instance or proposal, read by G6's words."""
    per_patch, refusals = {}, []
    for row in rows:
        activities = _activities(_t_report(row))
        counts = dict.fromkeys(COUNTS, 0)
        for part in _formal_records(activities).values():
            contract = (part.get("specification") or {}).get("contract") or {}
            model, correspondence = part.get("model") or {}, part.get("correspondence") or {}
            counts["models_built"] += 1
            for key in ("vacuous", "conflicting", "undecided"):
                counts[key] += bool(contract.get(key))
            counts["searched"] += model.get("verdict") not in (None, "not_searched")
            counts["traces_found"] += len(model.get("traces") or [])
            result = correspondence.get("result")
            if result == "reproduced":
                counts["traces_reproduced"] += len(correspondence.get("traces_reproduced") or [])
            counts["model_only"] += result == "not_reproduced" and correspondence.get("reason") == "model_only"
            counts["not_replayable"] += result == "not_replayable"
            counts["not_replayed"] += result == "not_replayed"
        for a in activities:
            found = _refusal(row, a)
            if found is not None:
                counts["refused_over_limits" if found["over_limits"] else "refused_other"] += 1
                refusals.append(found)
        per_patch[row["patch"]] = counts
    total = {key: sum(counts[key] for counts in per_patch.values()) for key in COUNTS}
    return {"per_patch": per_patch, "total": total, "refusals": refusals,
            "note": "from arm T's activities, recorded before any label was read; a refusal is over the limits by G6's "
                    "code where its record has one, and by G6's own words where it has none"}


# --- after the labels: test adequacy ---------------------------------------------------------------------------------

def newcombe_independent(x1: int, n1: int, x2: int, n2: int, z: float = Z95) -> tuple[float, float] | None:
    """Newcombe's hybrid score interval for p1 - p2 of two independent proportions (method 10, 1998), from Wilson's
    interval for each; None when a denominator is zero."""
    if not n1 or not n2:
        return None
    p1, p2 = x1 / n1, x2 / n2
    (l1, u1), (l2, u2) = stats.wilson(x1, n1, z), stats.wilson(x2, n2, z)
    low = (p1 - p2) - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = (p1 - p2) + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, low), min(1.0, high)


def _adequacy_state(row: dict) -> str:
    found = ((row.get("outputs") or {}).get("T") or {}).get("report")
    completion = ((found or {}).get("assessment") or {}).get("completion") if isinstance(found, dict) else None
    entries = (completion or {}).get("test_adequacy") if isinstance(completion, dict) else None
    ran = [e for e in entries or [] if isinstance(e, dict) and isinstance(e.get("survived"), int)
           and isinstance(e.get("killed"), int) and e["survived"] + e["killed"] > 0]
    if not ran:
        return "no_mutation_run"
    return "survived" if any(e["survived"] > 0 for e in ran) else "none_survived"


def adequacy(rows: list[dict]) -> dict:
    """The test adequacy table against the label (the module docstring), from the analysis's ``rows`` (each with its
    ``label`` and the arms' ``outputs``)."""
    states = ("survived", "none_survived", "no_mutation_run")
    table = {state: {"defective": 0, "correct": 0} for state in states}
    patches = {}
    for row in rows:
        state = patches[row["id"]] = _adequacy_state(row)
        table[state][row["label"]] += 1
    with_, without = table["survived"], table["none_survived"]
    n1, n2 = with_["defective"] + with_["correct"], without["defective"] + without["correct"]
    interval = newcombe_independent(with_["defective"], n1, without["defective"], n2)
    return {"table": table, "patches": patches,
            "difference": {"with": {"count": with_["defective"], "of": n1, "share": with_["defective"] / n1 if n1
                                    else None},
                           "without": {"count": without["defective"], "of": n2,
                                       "share": without["defective"] / n2 if n2 else None},
                           "difference": with_["defective"] / n1 - without["defective"] / n2 if n1 and n2 else None,
                           "newcombe": list(interval) if interval else None},
            "note": ADEQUACY_NOTE}


# --- after the labels: the reason codes --------------------------------------------------------------------------------

def share_interval(tasks: list[tuple[str, int, int]], *, seed: int, resamples: int = stats.RESAMPLES,
                   confidence: float = CONFIDENCE) -> dict:
    """The bootstrap's percentile interval of sum(numerators) / sum(denominators), resampling ``(repository,
    numerator, denominator)`` tasks within each repository (repositories in name order, each keeping its size), as
    V9's ``stratified_bootstrap`` resamples them, so the same seed gives the same task sets. A resample with no
    denominator is left out and counted (``undefined``); the interval is None when every one is."""
    strata: dict[str, list[tuple[int, int]]] = {}
    for repository, numerator, denominator in tasks:
        strata.setdefault(repository, []).append((int(numerator), int(denominator)))
    groups = [strata[name] for name in sorted(strata)]
    rng, values, undefined = random.Random(seed), [], 0
    for _ in range(resamples):
        picked = [one for group in groups for one in rng.choices(group, k=len(group))]
        below = sum(d for _, d in picked)
        if below:
            values.append(sum(n for n, _ in picked) / below)
        else:
            undefined += 1
    if not values:
        return {"interval": None, "undefined": undefined}
    values.sort()
    tail = round((1 - confidence) / 2, 12)
    return {"interval": [stats._quantile(values, tail), stats._quantile(values, 1 - tail)], "undefined": undefined}


def reason_codes(rows: list[dict], *, seed: int, resamples: int = stats.RESAMPLES) -> dict:
    """For each reason code G6 gave a finding, per arm A, C and T (the analysis's ``rows``, each finding a rerun with
    its ``code``, its result before the labels and its ``reference`` result after them): the number of findings and
    the shares on defective patches, reproducible, compatible with the reference fix and not comparable, each with
    ``share_interval``'s interval over every analysed task; and the number of findings per patch."""
    found, per_patch = {}, {}
    for arm in ("A", "C", "T"):
        codes = sorted({str(item.get("code") or NO_CODE) for row in rows for item in row["findings"].get(arm, [])})
        found[arm] = {}
        for code in codes:
            def of(row, test, arm=arm, code=code):
                items = [item for item in row["findings"].get(arm, []) if str(item.get("code") or NO_CODE) == code]
                return sum(bool(test(row, item)) for item in items), len(items)
            tests = {"on_defective": lambda row, item: row["label"] == "defective",
                     "reproducible": lambda row, item: (item.get("result") or {}).get("reproducible") is True,
                     "compatible_with_reference": lambda row, item: (item.get("reference") or {}).get(
                         "compatible") is True,
                     "not_comparable": lambda row, item: (item.get("reference") or {}).get("not_comparable") is True}
            entry = {"findings": sum(of(row, lambda r, i: True)[1] for row in rows)}
            for name, test in tests.items():
                per = [(row["repository"], *of(row, test)) for row in rows]
                hits, total = sum(p[1] for p in per), sum(p[2] for p in per)
                entry[name] = {"count": hits, "share": hits / total if total else None,
                               **share_interval(per, seed=seed, resamples=resamples)}
            found[arm][code] = entry
        every = [len(row["findings"].get(arm, [])) for row in rows]
        per_patch[arm] = {"mean": round(sum(every) / len(every), 3) if every else None,
                          "most": max(every) if every else None}
    return {**found, "findings_per_patch": per_patch,
            "note": "the shares are over findings; each interval resamples tasks within each repository with all their "
                    "findings"}


# --- similarity to the hidden tests -------------------------------------------------------------------------------------

def check_similarity(spec) -> dict:
    """The freeze entry's similarity measure, checked: ``measure`` one of ``MEASURES``, ``min_run`` a whole number of
    tokens of at least 1, and ``threshold`` a share above 0 and at most 1."""
    if not isinstance(spec, dict) or spec.get("measure") not in MEASURES:
        raise ValueError(f"the similarity measure is one of {MEASURES}")
    run, threshold = spec.get("min_run"), spec.get("threshold")
    if isinstance(run, bool) or not isinstance(run, int) or run < 1:
        raise ValueError("the similarity measure's min_run is a whole number of tokens of at least 1")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ValueError("the similarity threshold is a share above 0 and at most 1")
    return {"measure": spec["measure"], "min_run": run, "threshold": float(threshold)}


_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER,
         tokenize.ENCODING}


def _tokens(text: str) -> list[str]:
    """Python's tokens of ``text`` without comments, layout or line ends; words and symbols when it does not
    tokenize."""
    try:
        return [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in _SKIP]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return re.findall(r"\w+|[^\w\s]", text)


def similarity(check: str, hidden: list[str], spec: dict) -> float:
    """``token_containment.v1``: the largest, over the task's hidden test texts, share of the check's tokens that lie
    in runs of at least ``min_run`` consecutive tokens the hidden text holds in the same order (``difflib``'s matching
    blocks, with no junk heuristic). 0.0 for a check with no token or a task with no hidden test text."""
    spec = check_similarity(spec)
    mine = _tokens(check or "")
    if not mine:
        return 0.0
    best = 0.0
    for text in hidden:
        blocks = difflib.SequenceMatcher(None, mine, _tokens(text or ""), autojunk=False).get_matching_blocks()
        best = max(best, sum(b.size for b in blocks if b.size >= spec["min_run"]) / len(mine))
    return best


def hidden_texts(contract: dict) -> list[str]:
    """The texts of a label contract's hidden tests (``labels.load``'s): each hidden test file, and for a test patch
    the lines it adds, file by file."""
    found = []
    for group in contract.get("groups") or []:
        if group.get("origin") != "hidden_tests":
            continue
        overlay = group.get("overlay") or {}
        found += [text for _, text in sorted((overlay.get("files") or {}).items())]
        added: dict[str, list[str]] = {}
        current = None
        for line in (overlay.get("patch") or "").splitlines():
            if line.startswith("+++ "):
                current = line[4:].strip()
                added.setdefault(current, [])
            elif current is not None and line.startswith("+"):
                added[current].append(line[1:])
        found += ["\n".join(lines) + "\n" for _, lines in sorted(added.items()) if lines]
    return found


def _written(arm: str, output: dict) -> list[dict]:
    """The model-written checks the record holds for an arm on a patch, each ``{check, file, text, nodes}``: every
    proposed check a search arm ran, with the findings from it (the coordinator's ruling on item 81; a record from
    before it holds only the findings from proposed checks, which are measured then); every kept file of arms E and
    R, with the findings in it."""
    if arm in ("C", "T") and isinstance(output.get("checks"), list):
        findings = [f for f in output.get("findings") or [] if isinstance(f, dict)]
        return [{"check": one.get("check"), "file": one.get("placed"), "text": one.get("text") or "",
                 "nodes": [f.get("node") for f in findings if f.get("check") == one.get("check")]}
                for one in output["checks"] if isinstance(one, dict)]
    if arm in ("C", "T"):
        return [{"file": f.get("file") or str(f.get("node") or ""), "text": f.get("text") or "",
                 "nodes": [f.get("node")]} for f in output.get("findings") or []
                if isinstance(f, dict) and f.get("code") in PROPOSED_FINDINGS]
    findings = [f for f in output.get("findings") or [] if isinstance(f, dict)]
    return [{"file": one.get("placed"), "text": one.get("text") or "",
             "nodes": [f.get("node") for f in findings if f.get("file") == one.get("placed")
                       or str(f.get("node") or "").partition("::")[0] == one.get("placed")]}
            for one in output.get("kept") or [] if isinstance(one, dict)]


def measure_similarity(outputs: dict[str, dict], hidden: dict[str, list[str]], spec: dict) -> dict:
    """``similarity`` of every model-written check the record holds (``outputs``: each patch's arms' outputs) to its
    task's hidden tests (``hidden``: each patch's ``hidden_texts``): per patch and arm the checks with their similarity
    and whether it is above the threshold, and the findings in those above it (``above``: ``[patch, arm, node]``).
    A search arm's checks are every proposed check it ran, with their texts (``rehearsal.observed_checks``)."""
    spec = check_similarity(spec)
    patches, above = {}, []
    for patch, arms in outputs.items():
        patches[patch] = {}
        for arm in ("C", "T", "E", "R"):
            measured = []
            for check in _written(arm, arms.get(arm) or {}):
                score = similarity(check["text"], hidden.get(patch) or [], spec)
                over = score > spec["threshold"]
                measured.append({**({"check": check["check"]} if check.get("check") else {}), "file": check["file"],
                                 "similarity": round(score, 6), "above": over,
                                 "findings": [n for n in check["nodes"] if n]})
                above += [[patch, arm, node] for node in check["nodes"] if node and over]
            patches[patch][arm] = measured
    return {"spec": spec, "patches": patches, "above": above,
            "note": "measured in the label step, inside the vault, after the arms' record was hashed, on every "
                    "proposed check the search arms ran and every test arms E and R kept"}
