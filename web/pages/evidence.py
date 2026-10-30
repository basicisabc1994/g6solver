# SPDX-License-Identifier: Apache-2.0
"""The Research page's evidence, read from the committed study results at render time.

Every number shown comes from a file in ``oss/docs/validation/results/`` (registered studies) or
``oss/web/data/archive_benchmarks.json`` (G6's earlier benchmark studies, as audited); none is typed into a
template. A missing file raises, so a build can never show an empty or partial wall.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, replace
from math import comb
from pathlib import Path
from typing import Literal

VALIDATION_DIR = Path(__file__).resolve().parents[2] / "docs" / "validation"   # read at call time, so tests can move it
ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "archive_benchmarks.json"
REPO = "https://github.com/basicisabc1994/g6solver/blob/main/"
# What the 2026 audits of G6's earlier benchmark studies concluded about each result (data/README.md).
AUDIT_STATUSES = ("sound", "attribution disputed", "framing corrected", "corrected", "withdrawn", "null")

# The model alone is the comparator; every other adaptation arm is G6 (owner, 2026-10-19: the hand-written prompt runs
# on G6's own profile, so it is a G6 variant, not a separate category).
ARMS = {
    "A": "The model alone", "B": "G6 as built", "D": "G6, self-tuned", "F": "G6, random changes, same gate",
    "G": "G6, every component on", "H": "G6, a person's prompt", "DG": "G6, self-tuned from every component on",
    "check_claim": "G6 check_claim", "relevance": "Relevance score", "nli_llama": "One model call (llama3.1:8b)",
    "nli_20b": "One model call (gpt-oss:20b)",
    "gate_declared": "G6 action gate", "allow_list": "Read-only allow-list", "judge": "Model judge (gpt-oss:20b)",
    "fixed": "Cases fixed", "broken": "Cases broken",
}
G6_ARMS = {"B", "D", "DG", "F", "G", "H", "check_claim", "gate_declared"}
MODEL = "llama3.1:8b"                                     # the local model (Ollama) of every adaptation study so far
WORKFLOWS = {"gsm8k": "GSM8K", "massive": "MASSIVE", "scifact": "SciFact", "mbpp": "MBPP", "arc": "ARC-Challenge",
             "banking77": "banking77"}
# Each development round's finding, read with its scoreboard (rounds/round-NN/README.md has the root causes).
ROUND_NOTES = {
    "round-01": "Tuning gained 99 cases over G6 as built, and random edits from the same options gained as many: on "
                "GSM8K and banking77 one option did the work (working step by step; labelled examples), and on the "
                "other four nothing was approved. Runs that changed nothing still moved by up to 4 cases, from the "
                "model server's prompt cache; later rounds measure every arm from a freshly loaded model.",
    "round-02": "Self-tuning was approved on three of five datasets and gained 100 cases over G6 as built: GSM8K 26 to "
                "90, banking77 52 to 81 and MASSIVE 67 to 74. On ARC, where the model alone already scored 82, and on "
                "MBPP, the gate refused every change it was offered. Random changes through the same gate were "
                "approved once, on banking77 (78); like self-tuning's change there, they show the model eight labelled "
                "examples. Every arm started from a freshly loaded model, and G6 as built, measured again last, "
                "repeated all 500 outputs exactly.",
    "round-03": "The two modes did not stack. Self-tuning from G6 as built took GSM8K 21 to 81, and every prebuilt "
                "component switched on took MASSIVE 65 to 78; but self-tuning that started from every component on "
                "had no change approved on any task set. On SciFact every component on did worse than the model alone "
                "(57 against 64): it asks every answer a second time with a fixed question, so no instruction can "
                "reach the final label, and the three the reflector proposed there changed no task. The gate also "
                "refused a SciFact change that won 15 to 1 on the tasks it was chosen on and went 3 to 5 on fresh "
                "ones. MBPP had 40 fresh tasks left, too few for the gate to decide. G6 as built, measured again "
                "last, repeated all 340 outputs exactly.",
}
ROUND_QUESTIONS = {"round-03": "Do the two modes work together: does self-tuning that starts from every prebuilt "
                               "component beat each mode alone?"}


@dataclass(frozen=True)
class Bar:
    arm: str
    label: str
    value: int
    n: int | None                                        # None: a count with no fixed total (the pilot's wins)
    highlight: bool = False                              # one of G6's own arms


@dataclass(frozen=True)
class Group:
    label: str
    bars: tuple[Bar, ...]
    better: Literal["higher", "lower"] = "higher"

    @property
    def rows(self) -> list[tuple[Bar, str]]:
        """Each bar with its CSS width: its share of its own total, or of the group's largest count when it has none."""
        top = max((b.value for b in self.bars), default=0) or 1
        return [(b, f"{100.0 * b.value / (b.n or top):.1f}%") for b in self.bars]

    @property
    def by_arm(self) -> dict[str, Bar]:
        return {b.arm: b for b in self.bars}

    @property
    def best(self) -> Bar | None:
        """G6's winning option in this group (the earlier of ``G6_OPTIONS`` on a tie), or None when it has none."""
        options = [b for b in self.bars if b.arm in G6_OPTIONS]
        return max(options, key=lambda b: (b.value, -G6_OPTIONS.index(b.arm)), default=None)


TONES = {"component_validated_no_transfer": "good", "unsafe_or_regressive": "bad", "contaminated_evaluation": "warn",
         "inconclusive": "neutral"}


@dataclass(frozen=True)
class Study:
    id: str
    title: str
    question: str
    kind: Literal["registered", "planned", "development"]
    status: str                                          # "ran 2026-10-19", "not run"
    verdict: str | None = None
    note: str = ""
    groups: tuple[Group, ...] = ()
    results_url: str = ""
    registered: str | None = None
    datasets: str = ""

    @property
    def tone(self) -> str:
        """How the verdict badge is coloured: good, bad, warn, neutral; planned and development work have their own."""
        return TONES.get(self.verdict or "", "neutral") if self.kind == "registered" else self.kind


def _json(*parts: str) -> dict:
    return json.loads((VALIDATION_DIR / "results").joinpath(*parts).read_text(encoding="utf-8"))


def _bar(arm: str, value: int, n: int | None) -> Bar:
    return Bar(arm, ARMS[arm], int(value), n, arm in G6_ARMS)


def _registry() -> dict[str, tuple[str, str]]:
    """Study id -> (title, registration date), from the registry's headings."""
    text = (VALIDATION_DIR / "REGISTRY.md").read_text(encoding="utf-8")
    return {m[1]: (m[2], m[3]) for m in re.finditer(r"^## (V\w+) — (.+?) \(registered (\d{4}-\d{2}-\d{2})\)", text,
                                                      re.MULTILINE)}


def _adaptation_groups(analysis: dict, arms: tuple[str, ...]) -> tuple[Group, ...]:
    return tuple(Group(WORKFLOWS[wf], tuple(_bar(a, analysis["arms"][wf][a]["success"], analysis["arms"][wf][a]["n"])
                                            for a in arms))
                 for wf in WORKFLOWS if wf in analysis["arms"])


def _v1() -> tuple[Group, ...]:
    small, large = _json("V1", "v1_report_llama3.1_8b.json"), _json("V1", "v1_report_gpt-oss_20b.json")
    arms = (("check_claim", small["arms"]["check_claim"]), ("relevance", small["arms"]["relevance"]),
            ("nli_llama", small["arms"]["nli"]), ("nli_20b", large["arms"]["nli"]))
    strata = (("negated", "Negated claims", "lower"), ("wrong_figure", "Claims with a wrong figure", "lower"),
              ("quoted", "Quoted claims", "higher"), ("paraphrased", "Paraphrased claims", "higher"))
    return tuple(Group(label, tuple(_bar(arm, a["strata"][s]["said_supported"], a["strata"][s]["n"]) for arm, a in arms),
                       better)
                 for s, label, better in strata)


def _v2() -> tuple[Group, ...]:
    arms = _json("V2", "v2_report.json")["arms"]
    order = ("gate_declared", "allow_list", "judge")
    return (Group("Harmful calls let through", tuple(_bar(a, arms[a]["missed_harm"]["k"], arms[a]["missed_harm"]["n"])
                                                    for a in order), "lower"),
            Group("Harmless calls stopped for a person",
                  tuple(_bar(a, arms[a]["false_escalation"]["k"], arms[a]["false_escalation"]["n"]) for a in order),
                  "lower"))


def _pilot() -> tuple[Group, ...]:
    pilot = _json("V6", "post_run", "pilot_summary.json")
    best = {"massive": ("examples", "labelled examples"), "scifact": ("verify", "a second look"),
            "mbpp": ("repair3", "test and repair")}
    return tuple(Group(f"{WORKFLOWS[wf]}, with {what}", (_bar("fixed", pilot[wf][key]["wins"], None),
                                                         _bar("broken", pilot[wf][key]["losses"], None)))
                 for wf, (key, what) in best.items())


def _results(study_id: str) -> str:
    return f"{REPO}docs/validation/results/{study_id}/README.md"


def studies() -> list[Study]:
    """Every registered study (with its own verdict), the planned ones, and the post-V6 development pilot."""
    registry = _registry()
    v6, v6b = _json("V6", "analysis.json"), _json("V6b", "analysis.json")
    registered = {
        "V1": Study("V1", "Grounding", "Does grounding's check_claim refuse to call a wrong claim supported?",
                    "registered", "ran", _json("V1", "v1_report_llama3.1_8b.json")["verdict"],
                    "It never called a negated or wrong-figure claim supported, but found support for few paraphrases.",
                    _v1(), _results("V1"), datasets="VitaminC"),
        "V2": Study("V2", "Action gating", "Does the action gate stop harmful agent actions without stopping everything?",
                    "registered", "ran", _json("V2", "v2_report.json")["verdict"],
                    "It let one harmful call run: a password change. That case now goes to a person (V2-F1); the "
                    "fix is not re-validated.", _v2(), _results("V2"), datasets="AgentDojo"),
        "V6": Study("V6", "Meta-harness, first run", "Does tuning beat G6 as built, extra compute and random search?",
                    "registered", "ran", v6["verdict"],
                    "One approved change (GSM8K); the gate approved nothing on the other three.",
                    _adaptation_groups(v6, ("B", "D", "F", "A")), _results("V6"),
                    datasets="GSM8K, MASSIVE, SciFact, MBPP; llama3.1:8b; 60 held-out tasks each"),
        "V6b": Study("V6b", "Meta-harness, recovery loop", "On fresh cases, does tuning with root-cause recovery beat "
                     "G6 as built?", "registered", "ran", v6b["verdict"],
                     "The verdict comes from a false positive in the study's own leak check (V6b-F3); by the other "
                     "rules it is inconclusive. Tuning matched a hand-written prompt on GSM8K and SciFact, and found "
                     "nothing it could prove on MASSIVE and MBPP.",
                     _adaptation_groups(v6b, ("B", "H", "D", "F", "G", "A")), _results("V6b"),
                     datasets="GSM8K, MASSIVE, SciFact, MBPP; llama3.1:8b; 100 fresh held-out tasks each"),
    }
    missing = set(registry) - set(registered)
    if missing:
        raise LookupError(f"registered studies with no tile: {sorted(missing)}")
    shown = [replace(s, registered=registry[s.id][1], status=f"ran, registered {registry[s.id][1]}")
             for s in registered.values()]
    planned = [
        Study("V3", "Static composition", "The pre-registered mission-bridge experiment.", "planned", "not run",
              note="Waits on the mission-bridge port."),
        Study("V4", "Governed adaptation", "Does evidence-constrained harness adaptation produce one verified "
              "learning event on TimeMachine-bench?", "planned", "not run"),
        Study("V5", "Bundles in use", "Descriptive case tables for the bundles with a public dataset.", "planned",
              "not run"),
    ]
    pilot = Study("V6-pilot", "After V6: root cause and recovery", "Do the G6 parts V6 never offered fix its failures?",
                  "development", "development measurement", None,
                  "Development, not a validation: these cases were seen in V6. V6b then tested the remedies on fresh "
                  "cases.", _pilot(), f"{REPO}docs/validation/results/V6/post_run/README.md")
    v6b, *earlier = reversed(shown)                      # newest first; the pilot ran between V6 and V6b
    return [v6b, pilot, *earlier, *planned]


def rounds() -> list[Study]:
    """Each development round in ``docs/validation/rounds/``, newest first, with its scoreboard's numbers."""
    found = []
    for path in sorted((VALIDATION_DIR / "rounds").glob("round-*/scoreboard.json"), reverse=True):
        name, board = path.parent.name, json.loads(path.read_text(encoding="utf-8"))
        splits = path.parent / "splits.json"             # drawn for every planned dataset before the round starts
        planned = len(json.loads(splits.read_text(encoding="utf-8"))["splits"]) if splits.is_file() else 0
        measured = len(board["workflows"])
        running = measured < planned                     # a round rewrites its scoreboard after each dataset
        if name not in ROUND_NOTES and not running:
            raise LookupError(f"a development round with no note: {name}")
        note = ROUND_NOTES.get(name) or (f"In progress: {measured} of {planned} datasets measured so far, each "
                                         f"published as it finishes; the write-up follows when the round ends.")
        n = {arms["B"]["n"] for arms in board["workflows"].values()}
        ran = tuple(a for a in ("A", "B", "G", "H", "F", "D", "DG") if any(a in w for w in board["workflows"].values()))
        found.append(Study(name, f"Development round {board['round']}", ROUND_QUESTIONS.get(
                           name, "On fresh cases, how far does G6 tune itself beyond the model alone and its own other "
                                 "variants?"), "development",
                           f"in progress, {measured} of {planned} datasets" if running
                           else "worked example", note=note,
                           groups=_adaptation_groups({"arms": board["workflows"]}, ran),
                           results_url=f"{REPO}docs/validation/rounds/{name}/README.md",
                           datasets=f"{', '.join(WORKFLOWS[k] for k in board['workflows'])}; {board['model']}; "
                                    f"{'/'.join(map(str, sorted(n)))} fresh cases each"))
    return found


def both_modes() -> dict:
    """Development round 3's answer to whether the two modes stack: how many changes self-tuning had approved when it
    started from every prebuilt component, and the task set where every component on did worst against the model
    alone, task by task (from the round's scoreboard and rows)."""
    folder = VALIDATION_DIR / "rounds" / "round-03"
    board = json.loads((folder / "scoreboard.json").read_text(encoding="utf-8"))
    flows, rows = board["workflows"], {}
    for path in sorted((folder / "rows").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows.setdefault((path.stem, row["arm"]), {})[row["case_id"]] = bool(row["success"])
    wf = min(flows, key=lambda k: flows[k]["G"]["success"] - flows[k]["A"]["success"])
    gained, lost, _ = paired(rows, wf, "G", "A")
    return {"round": board["round"], "datasets": len(flows),
            "approved_from_components": sum(1 for key, run in board["tuning"].items()
                                            if key.endswith("-DG") and run["outcome"] == "activated"),
            "worst": {"dataset": WORKFLOWS[wf], "alone": flows[wf]["A"]["success"],
                      "components": flows[wf]["G"]["success"], "n": flows[wf]["G"]["n"], "gained": gained,
                      "lost": lost}}


def headline(research: bool = False) -> tuple[str, tuple[Group, ...]]:
    """The latest registered adaptation study, per dataset: the model alone, then G6 as built, with every component on,
    with a person's prompt, with random changes through the same gate, and self-tuned. ``research`` is kept for
    callers; both pages now show the same options."""
    # The home page shows the same options as the Research page, so their winning numbers agree (owner, 2026-10-20).
    return "V6b", _adaptation_groups(_json("V6b", "analysis.json"), ("A", "B", "G", "H", "F", "D"))


def latest_round_speed() -> dict:
    """Every change the latest development round's own loop approved, with the minutes it took to find, test and
    approve it (from the round's scoreboard, so it updates as the round runs)."""
    paths = sorted((VALIDATION_DIR / "rounds").glob("round-*/scoreboard.json"))
    if not paths:
        return {"round": None, "changes": []}
    board = json.loads(paths[-1].read_text(encoding="utf-8"))
    changes = []
    for key, run in board["tuning"].items():
        wf, _, arm = key.rpartition("-")
        if arm in ("D", "DG") and run["outcome"] == "activated" and arm in board["workflows"].get(wf, {}):
            arms = board["workflows"][wf]
            changes.append({"dataset": WORKFLOWS[wf], "minutes": run["minutes"], "alone": arms["A"]["success"],
                            "tuned": arms[arm]["success"], "n": arms[arm]["n"], "from_components": arm == "DG"})
    return {"round": board["round"], "changes": changes}


def learned_change() -> dict | None:
    """One change the latest development round's own loop made to its prompt and kept, as its tuning record holds it:
    the prompt before and after, the held-out confirmation that recommended it, and the minutes it took. The home page
    sets it beside a model's weights to show what learning through the harness means."""
    paths = sorted((VALIDATION_DIR / "rounds").glob("round-*/scoreboard.json"))
    if not paths:
        return None
    board = json.loads(paths[-1].read_text(encoding="utf-8"))
    for key, run in board["tuning"].items():
        wf, _, arm = key.rpartition("-")
        if arm != "D" or run["outcome"] != "activated":
            continue
        record = json.loads((paths[-1].parent / "tuning" / f"{key}.json").read_text(encoding="utf-8"))
        before, after = record["initial_profile"]["prompt"], record["head_profile"]["prompt"]
        if before == after:
            continue
        confirmation = record["run"]["confirmation"]
        return {"round": board["round"], "dataset": WORKFLOWS[wf], "before": before, "after": after,
                "held_out": confirmation["n"], "wins": confirmation["success"]["wins"],
                "losses": confirmation["success"]["losses"], "verdict": record["run"]["decision"]["verdict"],
                "minutes": record["minutes"]}
    return None


# The winning approach for each task set (owner, 2026-10-20: show G6's best option and the actual improvement). It is
# picked after the fact from G6's own options; what each winner changed is checked against its approved profile by
# tests/site/test_evidence.py, and its paired counts come from the study's rows.
G6_OPTIONS = ("D", "DG", "G", "H", "F", "B")              # on a tie, the earlier one
WINNING_CHANGE = {
    ("gsm8k", "D"): ("Self-tuning", "the model shows its working step by step, with the answer on its own last line"),
    ("scifact", "D"): ("Self-tuning", "the model judges the claim from the abstract alone, not outside knowledge"),
    ("massive", "F"): ("A prebuilt component", "eight labelled examples, found by G6's search and passed by its gate"),
    ("mbpp", "G"): ("Every prebuilt component", "self-review, test-and-repair, an import check and eight labelled "
                                                "examples"),
}
TASK_SHAPE = {"gsm8k": "Multi-step arithmetic word problems", "scifact": "Judge a scientific claim against one abstract",
              "massive": "Pick one of 60 similar intent labels", "mbpp": "Write Python that passes hidden tests"}


def _rows(study_id: str) -> dict[tuple[str, str], dict[str, bool]]:
    """(workflow, arm) -> case id -> solved, from a study's committed rows."""
    found: dict[tuple[str, str], dict[str, bool]] = {}
    path = VALIDATION_DIR / "results" / study_id / f"{study_id.lower()}_rows.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            found.setdefault((r["workflow"], r["arm"]), {})[r["case_id"]] = r["success"] in ("True", "true", "1")
    return found


def paired(rows: dict, wf: str, arm: str, other: str) -> tuple[int, int, float]:
    """Cases ``arm`` solved and ``other`` did not, the reverse, and the exact one-sided sign test's p-value."""
    x, y = rows[(wf, arm)], rows[(wf, other)]
    wins = sum(1 for case, ok in x.items() if ok and not y.get(case))
    losses = sum(1 for case, ok in x.items() if not ok and y.get(case))
    n = wins + losses
    return wins, losses, (sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n if n else 1.0)


def winners() -> list[dict]:
    """Per task set of the headline study: its shape, G6's winning option, what it changed, and how it did against
    the model alone, task by task."""
    arms, rows = _json("V6b", "analysis.json")["arms"], _rows("V6b")
    found = []
    for wf in (w for w in WORKFLOWS if w in arms):
        best = max((a for a in G6_OPTIONS if a in arms[wf]), key=lambda a: (arms[wf][a]["success"], -G6_OPTIONS.index(a)))
        approach, change = WINNING_CHANGE[(wf, best)]
        wins, losses, p = paired(rows, wf, best, "A")
        found.append({"key": wf, "dataset": WORKFLOWS[wf], "shape": TASK_SHAPE[wf], "arm": best, "label": ARMS[best],
                      "selection": "post-hoc exploratory; not a registered policy",
                      "approach": approach, "change": change, "alone": arms[wf]["A"]["success"],
                      "value": arms[wf][best]["success"], "n": arms[wf][best]["n"], "wins": wins, "losses": losses,
                      "p": p, "p_scope": "unadjusted; selected after observing outcomes"})
    return found


def headline_facts() -> dict:
    """What the headline study's numbers say, computed from its analysis so the sentences cannot drift from them."""
    arms = _json("V6b", "analysis.json")["arms"]
    total = {arm: sum(arms[wf][arm]["success"] for wf in arms) for arm in ("A", "B", "D", "F", "G")}
    top = max(arms, key=lambda wf: arms[wf]["D"]["success"] - arms[wf]["A"]["success"])
    rows = _rows("V6b")
    return {"n": sum(arms[wf]["A"]["n"] for wf in arms), "total": total, "model": MODEL,
            "primary_total": total["D"], "primary_arm": "D",
            "best_total": sum(w["value"] for w in winners()),
            # where the prebuilt components won, against self-tuning, task by task (the study's own D_vs_G comparison)
            "components_vs_tuning": [(WORKFLOWS[wf], *paired(rows, wf, "G", "D")[:2]) for wf in ("massive", "mbpp")],
            "largest": (WORKFLOWS[top], arms[top]["A"]["success"], arms[top]["D"]["success"]),
            "datasets": len(arms), "per_dataset": arms["gsm8k"]["A"]["n"],
            "tuned_calls": max(arms[wf]["D"]["calls_mean"] for wf in arms),
            "every_component_beats_alone": [WORKFLOWS[wf] for wf in WORKFLOWS
                                            if wf in arms and arms[wf]["G"]["success"] > arms[wf]["A"]["success"]],
            "untuned": [WORKFLOWS[wf] for wf in WORKFLOWS if wf in arms and arms[wf]["D"]["same_profile_as"] == "B"],
            # the two modes (owner, 2026-10-19): where self-tuning from scratch proved no change, the prebuilt
            # components still lifted the model, a finding about when to use which
            "components_where_untuned": [(WORKFLOWS[wf], arms[wf]["A"]["success"], arms[wf]["G"]["success"])
                                         for wf in WORKFLOWS if wf in arms and arms[wf]["D"]["same_profile_as"] == "B"
                                         and arms[wf]["G"]["success"] > arms[wf]["A"]["success"]],
            "voting": {"tuned": arms["gsm8k"]["D"]["success"], "voting": arms["gsm8k"]["E_tok"]["success"],
                       "alone": arms["gsm8k"]["A"]["success"]}}


# --- Every study run, as one ledger: the same model without G6 and with it (owner, 2026-10-19) ---------------------
Scope = Literal["core", "core + extensions", "extensions"]
Outcome = Literal["gain", "no difference", "lower", "specialised", "no baseline"]
SCOPES: tuple[tuple[Scope, str], ...] = (("core", "The core harness"), ("core + extensions", "Core with extensions"),
                                         ("extensions", "Extensions"))


@dataclass(frozen=True)
class Entry:
    name: str
    did: str                                             # what G6 engineered
    measure: str
    model: str
    baseline: str                                        # what "without" was
    without: str
    with_g6: str
    without_pct: float | None
    with_pct: float | None
    outcome: Outcome
    scope: Scope
    source: Literal["registered", "development", "earlier"]
    note: str
    url: str = ""
    better: Literal["higher", "lower"] = "higher"

    @property
    def bars(self) -> list[tuple[str, str, bool]]:
        """(label, CSS width, is G6) for the two bars; none when either side has no share to draw."""
        if self.without_pct is None or self.with_pct is None:
            return []
        return [(self.baseline, f"{self.without_pct:.1f}%", False), ("G6", f"{self.with_pct:.1f}%", True)]


def _outcome(without: float | None, with_g6: float | None, better: str = "higher") -> Outcome:
    if without is None or with_g6 is None:
        return "no baseline"
    if with_g6 == without:
        return "no difference"
    return "gain" if (with_g6 > without) == (better == "higher") else "lower"


def _count(k: int, n: int) -> tuple[str, float]:
    return f"{k} of {n}", 100.0 * k / n


def _adaptation_entry(name: str, did: str, arms: dict, *, source: str, url: str, measure: str) -> Entry:
    """The model alone (A) against G6, self-tuned (D), summed over the datasets, each dataset named in the note."""
    n = sum(arms[wf]["A"]["n"] for wf in arms)
    a, d = (sum(arms[wf][arm]["success"] for wf in arms) for arm in ("A", "D"))
    (without, wp), (with_g6, gp) = _count(a, n), _count(d, n)
    note = ", ".join(f"{WORKFLOWS[wf]} {arms[wf]['A']['success']} → {arms[wf]['D']['success']}"
                     for wf in WORKFLOWS if wf in arms)
    return Entry(name, did, measure, f"{MODEL}, local", "The model alone", without, with_g6, wp, gp,
                 _outcome(wp, gp), "core", source, note, url)  # type: ignore[arg-type]


def _registered_entries() -> list[Entry]:
    v6, v6b = _json("V6", "analysis.json")["arms"], _json("V6b", "analysis.json")["arms"]
    tuned = "The meta-harness tuned G6 to each dataset: it proposed changes, tested each one and kept what its gate passed"
    small = _json("V1", "v1_report_llama3.1_8b.json")["arms"]
    wrong = ("negated", "wrong_figure")
    said = {arm: sum(small[arm]["strata"][s]["said_supported"] for s in wrong) for arm in ("check_claim", "nli")}
    n_wrong = sum(small["nli"]["strata"][s]["n"] for s in wrong)
    (v1_without, v1_wp), (v1_with, v1_gp) = _count(said["nli"], n_wrong), _count(said["check_claim"], n_wrong)
    v2 = _json("V2", "v2_report.json")["arms"]
    gate, allow = v2["gate_declared"], v2["allow_list"]
    harm_n = gate["missed_harm"]["n"]
    (v2_with, v2_gp) = _count(harm_n - gate["missed_harm"]["k"], harm_n)
    return [
        _adaptation_entry("Self-tuning on four public datasets", tuned, v6b, source="registered", url=_results("V6b"),
                          measure="tasks solved, 100 fresh held-out tasks per dataset"),
        _adaptation_entry("Self-tuning, first run", tuned, v6, source="registered", url=_results("V6"),
                          measure="tasks solved, 60 held-out tasks per dataset"),
        Entry("Claim checking", "G6's source check (grounding) judged each claim against its source text",
              "wrong claims called supported (negated, or with a wrong figure)", f"{MODEL}, local", "One model call",
              v1_without, v1_with, v1_wp, v1_gp, _outcome(v1_wp, v1_gp, "lower"), "core", "registered",
              f"It confirmed fewer reworded true claims than a model call "
              f"({small['check_claim']['strata']['paraphrased']['said_supported']} against "
              f"{small['nli']['strata']['paraphrased']['said_supported']} of 100).", _results("V1"), "lower"),
        Entry("Action gate", "G6's action gate decided each tool call an agent wanted to make: allow, ask a person, "
              "or block", "harmful agent calls stopped", "AgentDojo's tool calls", "No gate", f"0 of {harm_n}",
              v2_with, 0.0, v2_gp, _outcome(0.0, v2_gp), "core", "registered",
              f"A read-only allow-list stopped all {harm_n - allow['missed_harm']['k']}; the one G6 let through, a "
              f"password change, now goes to a person. It held {round(100 * gate['false_escalation']['rate'])}% of "
              f"harmless calls for a person.", _results("V2")),
    ]


def _pilot_entry() -> Entry:
    rows = [json.loads(line) for line in
            (VALIDATION_DIR / "results" / "V6" / "post_run" / "pilot_rows.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    remedy = {"massive": "examples", "scifact": "verify", "mbpp": "repair3"}   # the remedy each root cause called for

    def wins(arm_of) -> tuple[int, int]:
        picked = [r for r in rows if r["arm"] == arm_of(r["workflow"])]
        return sum(r["success"] for r in picked), len(picked)

    (without, wp), (with_g6, gp) = _count(*wins(lambda wf: "B")), _count(*wins(remedy.get))
    return Entry("Root-cause remedies", "G6 diagnosed why each answer failed and applied the remedy for that cause: "
                 "labelled examples, a second look, or running the shown tests and repairing",
                 "tasks solved on MASSIVE, SciFact and MBPP", f"{MODEL}, local", "G6 as built", without, with_g6, wp, gp,
                 _outcome(wp, gp), "core", "development",
                 "Development: measured on cases an earlier run had seen; the next registered study tested the "
                 "remedies on fresh cases.", f"{REPO}docs/validation/results/V6/post_run/README.md")


def _pct(text: str) -> float | None:
    found = re.match(r"\s*([\d.]+)%", text)
    return float(found[1]) if found else None


def _scope(in_library: str) -> Scope:
    return "core" if in_library.startswith("yes") else "extensions" if in_library.startswith("no") else "core + extensions"


def _archive_entry(row: dict) -> Entry:
    without_pct = _pct(row["baseline"])
    specialised = row.get("specialised")
    with_text = specialised or row["g6"]
    with_pct = _pct(with_text)
    outcome: Outcome = "specialised" if specialised else _outcome(without_pct, with_pct)
    short = lambda text: f"{_pct(text):g}%" if _pct(text) is not None else text    # noqa: E731
    return Entry(row["benchmark"], row["what_g6_did"], row["metric"], row["model"], "The same model without G6",
                 short(row["baseline"]), with_text if specialised else short(row["g6"]), without_pct, with_pct, outcome,
                 _scope(row["in_library"]), "earlier", row["note"])     # the limit its audit left standing


def _round_measure(flows: dict) -> str:
    """'tasks solved, 100 fresh cases per dataset', naming any dataset that had fewer (round 3's MBPP had 40 left)."""
    usual = max(arms["A"]["n"] for arms in flows.values())
    fewer = [f"{WORKFLOWS[wf]} {arms['A']['n']}" for wf, arms in flows.items() if arms["A"]["n"] != usual]
    return f"tasks solved, {usual} fresh cases per dataset" + (f" ({', '.join(fewer)})" if fewer else "")


def ledger() -> list[Entry]:
    """Every study run: the registered studies, the development work, and the earlier audited benchmark studies."""
    boards = {r.id: json.loads((VALIDATION_DIR / "rounds" / r.id / "scoreboard.json").read_text(encoding="utf-8"))
              for r in rounds()}
    rounds_ = [_adaptation_entry(r.title, "G6's own loop found, tested and approved each change",
                                 boards[r.id]["workflows"], source="development", url=r.results_url,
                                 measure=_round_measure(boards[r.id]["workflows"]))
               for r in rounds() if not r.status.startswith("in progress")]      # counted once the round ends
    order: tuple[Outcome, ...] = ("gain", "specialised", "no difference", "lower", "no baseline")
    earlier = sorted((_archive_entry(row) for row in archive()["rows"]),
                    key=lambda e: (order.index(e.outcome), -((e.with_pct or 0) - (e.without_pct or 0))))
    return [*_registered_entries(), _pilot_entry(), *rounds_, *earlier]


def ledger_by_scope() -> list[tuple[str, list[Entry]]]:
    rows = ledger()
    return [(label, [r for r in rows if r.scope == scope]) for scope, label in SCOPES]


# --- What each harness option does on its own (round 1's single-change sweep) --------------------------------------
_CHANGE_ORDER = ("prompt", "exemplars", "pipeline", "extra_checks", "retries")
_STEPS = {"self_review": "Review its own answer once", "ground": "Check a “supports” verdict against the abstract",
          "repair1": "Run the shown test and repair, once",
          "repair_with_tests": "Run the shown tests and repair, up to three rounds"}
_LOOKS = {"contradict": "“contradicts”", "support": "“supports”", "not_enough_info": "“not enough info”"}
_CHECKS = {"label-only": "the answer is one label", "letter-only": "the answer is one letter",
           "whole-number": "the answer is a whole number", "imports-cleanly": "the code imports cleanly"}


def _change_label(field: str, value) -> str:
    if field == "prompt":
        text = " ".join(str(value).split())
        return f"Prompt: “{text[:64].rstrip()}{'…' if len(text) > 64 else ''}”"
    if field == "exemplars":
        return f"{value} labelled examples in the prompt"
    if field == "retries":
        return f"Retry {'once' if value == 1 else f'{value} times'} when a check fails"
    if field == "extra_checks":
        return "Extra check: " + ", ".join(_CHECKS.get(c, c) for c in value)
    step = value[0]
    if step.startswith("second_look_"):
        which = step.removeprefix("second_look_")
        return f"A second look at answers of {_LOOKS.get(which, f'“{which.upper()}”')}"
    return _STEPS.get(step, step.replace("_", " "))


def component_sweep() -> dict:
    """Every single change from G6 as built that round 1 measured, as a matrix: rows are changes, columns datasets,
    cells the change in tasks solved (of 100) against G6 as built, gains and losses alike."""
    sweep = json.loads((VALIDATION_DIR / "rounds" / "round-01" / "single_change_sweep.json").read_text(encoding="utf-8"))
    keys = list(sweep["workflows"])
    base = {wf: next(p for p in ps if not p["diff"])["dev_eval"] for wf, ps in sweep["workflows"].items()}
    rows: dict[str, dict] = {}
    for wf, profiles in sweep["workflows"].items():
        for p in profiles:
            if not p["diff"]:
                continue
            (field, value), = p["diff"].items()
            label = _change_label(field, value)
            row = rows.setdefault(label, {"label": label, "field": field, "cells": [None] * len(keys)})
            row["cells"][keys.index(wf)] = p["dev_eval"] - base[wf]
    ordered = sorted(rows.values(), key=lambda r: _CHANGE_ORDER.index(r["field"]))
    for row in ordered:
        if row["field"] == "prompt":                     # how a sentence names the change
            words = row["label"].removeprefix("Prompt: “").split()
            row["short"] = f"the prompt “{' '.join(words[:7]).rstrip('.,…”')}…”"
        else:
            row["short"] = row["label"][0].lower() + row["label"][1:]
    for row in ordered:                                  # what each cell shows, and its tone: a gain, a loss or none
        row["shown"] = [("" if c is None else f"+{c}" if c > 0 else f"−{-c}" if c < 0 else "0",
                         "" if c is None else "up" if c > 0 else "down" if c < 0 else "flat") for c in row["cells"]]
    return {"workflows": [WORKFLOWS[k] for k in keys], "base": [base[k] for k in keys], "rows": ordered,
            "model": MODEL, "about": sweep["what"]}


def sweep_extremes() -> tuple[dict, dict]:
    """The largest gain and the largest loss any single change made: quoted together, never one without the other."""
    matrix = component_sweep()
    cells = [{"value": c, "shown": shown, "label": row["label"], "short": row["short"], "workflow": wf}
             for row in matrix["rows"] for c, (shown, _), wf in zip(row["cells"], row["shown"], matrix["workflows"])
             if c is not None]
    return max(cells, key=lambda c: c["value"]), min(cells, key=lambda c: c["value"])


def archive() -> dict:
    """G6's earlier benchmark studies, each with what its audit concluded (``data/archive_benchmarks.json``)."""
    data = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    unknown = {row["audit_status"] for row in data["rows"]} - set(AUDIT_STATUSES)
    if unknown:
        raise ValueError(f"archive rows with an unknown audit status: {sorted(unknown)}")
    return data


def context() -> dict:
    """What ``pages/research.html`` renders."""
    from pages import theory

    rows = ledger()
    _, headline_groups = headline(research=True)
    return {"theory": theory.DOCUMENTS, "theory_repo": theory.REPO,
            "rounds": rounds(), "headline_groups": headline_groups, "facts": headline_facts(), "winners": winners(),
            "both": both_modes(),
            "ledger": ledger_by_scope(), "matrix": component_sweep(),
            "counts": [("Studies run", len(rows)),
                       *((label, sum(r.scope == scope for r in rows)) for scope, label in
                         (("core", "On the core harness"), ("core + extensions", "Core with extensions"),
                          ("extensions", "On extensions"))),
                       ("Showed a gain", sum(r.outcome == "gain" for r in rows))]}
