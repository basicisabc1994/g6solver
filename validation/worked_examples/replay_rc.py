# SPDX-License-Identifier: Apache-2.0
"""No-spend development replay. Source records and labels never enter the task image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any

if __package__:
    from . import run as we
else:
    import run as we

c = we.c
MODES = ("agent", "hook", "neighbourhood")
AGENT_ARMS = {"B", "C", "D", "E", "F", "G", "G1", "H", "H1"}
NEIGHBOURS = ("black-5634", "starlette-3718", "pydantic-14052", "click-3769")
OUTCOMES = {"supported_within_scope", "insufficient_evidence", "defect_detected", "test_integrity_compromised"}
REGISTRATION = Path(__file__).with_name("REPLAY_RC.md")


def _normal_path(path: Path) -> Path:
    text = str(path)
    if os.name == "nt":
        if text[:8].casefold() == "\\\\?\\unc\\":
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        path = Path(text)
        parts = [part if part in (".", "..") else part.rstrip(" .") for part in
                 (path.parts[1:] if path.anchor else path.parts)]
        path = Path(path.anchor, *parts)
        return Path(os.path.normcase(str(path)))
    return path


def canonical_path(path: Path) -> Path:
    """One gate for reads and destinations, before any content access or hashing."""
    def check(candidate: Path) -> None:
        for part in re.split(r"[\\/]", str(candidate)):
            name = part.rstrip(" .").casefold()
            if name == "sealed" or re.fullmatch(r"final-.*\.json", name):
                raise ValueError(f"forbidden archive path: {candidate}")
        for part in (candidate, *candidate.parents):
            if part.is_symlink() or part.is_junction():
                raise ValueError(f"archive links are refused: {candidate}")

    path = Path(path)
    check(path)
    path = _normal_path(path).absolute()
    check(path)
    # Windows realpath expands existing 8.3 aliases, including an existing
    # ancestor when the final destination has not been created yet.
    resolved = _normal_path(path.resolve())
    check(resolved)
    return resolved


class Source:
    """Allowlisted relative reads, with no write API and no recursive discovery."""

    def __init__(self, root: Path):
        self.root = canonical_path(root)

    @staticmethod
    def _check(path: Path) -> Path:
        return canonical_path(path)

    def path(self, rel: str | Path) -> Path:
        raw = str(rel).replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in raw.split("/"):
            raise ValueError(f"archive path must stay relative: {rel}")
        path = canonical_path(self.root / raw)
        if not path.is_relative_to(canonical_path(self.root)):
            raise ValueError(f"archive path escapes source: {rel}")
        return path

    def read_bytes(self, rel: str | Path) -> bytes:
        return self.path(rel).read_bytes()

    def read_json(self, rel: str | Path) -> Any:
        return json.loads(self.read_bytes(rel).decode("utf-8"))

    def rows(self) -> list[dict]:
        index = self.path("runs.jsonl")
        if not index.is_file():
            return []
        return [json.loads(line) for line in self.read_bytes("runs.jsonl").decode("utf-8").splitlines() if line.strip()]


@dataclass
class Case:
    bug: str
    digest: str
    label: str
    patch: Path
    source: Path
    task_text: str
    arm: str
    round: int | None
    arguments: dict | None
    origins: list[dict] = field(default_factory=list)

    def saved(self) -> dict:
        return {"bug": self.bug, "digest": self.digest, "label": self.label,
                "patch": self.patch.relative_to(self.source).as_posix(), "task_text": self.task_text,
                "arm": self.arm, "round": self.round, "arguments": self.arguments, "origins": self.origins}


def _agent_origin(origin: dict) -> bool:
    return origin["archive_round"] < 3 or origin["arm"] in AGENT_ARMS


def _has_error(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).casefold() in {"error", "errors"} or _has_error(item) for key, item in value.items())
    return isinstance(value, list) and any(_has_error(item) for item in value)


def enumerate_cases(root: Path, tasks: list[we.Task]) -> tuple[list[Case], list[dict]]:
    """Refuse unsafe index paths with a caller-visible ValueError before candidate reads."""
    root = Source(root).root
    task_ids = {t.id for t in tasks}
    found: dict[tuple[str, str], Case] = {}
    excluded = []
    sources = [(number, part, Source(root / part))
               for number, part in enumerate(("", "round2", "round3"), 1)]
    # Preflight every index destination before reading even the first index.
    for _, _, source in sources:
        source.path("runs.jsonl")
    indices = []
    for archive_round, part, source in sources:
        rows = source.rows()
        starts = [r for r in rows if r.get("event") == "started"]
        # Validate paths across all rounds before reading or hashing candidates.
        for row in starts:
            source.path(row["run"])
            if row.get("checked_run"):
                source.path(row["checked_run"])
        indices.append((archive_round, part, source, rows, starts))
    for archive_round, part, source, rows, starts in indices:
        ended = {r["run"]: r for r in rows if r.get("event") == "ended"}
        for row in sorted(starts, key=lambda r: r["run"]):
            bug, rel = row.get("task"), row["run"]
            if row.get("kind") != "run" or bug not in task_ids:
                continue
            if not re.fullmatch(rf"run/{re.escape(bug)}/[A-Za-z0-9_-]+", rel):
                raise ValueError(f"invalid run path: {rel}")
            patch = source.path(f"{rel}/final.patch")
            name = patch.relative_to(root).as_posix()
            if not patch.is_file():
                excluded.append({"bug": bug, "patch": name, "reason": "no final patch"})
                continue
            labels = set()
            label_sources = []
            errored = []
            for check in starts:
                if check.get("kind") != "check" or check.get("checked_run") != rel:
                    continue
                if _has_error(check) or _has_error(ended.get(check["run"], {})):
                    errored.append(f'{part + "/" if part else ""}{check["run"]}/run.json')
                    continue
                if ended.get(check["run"], {}).get("status") != "finished":
                    continue
                record = source.read_json(f'{check["run"]}/run.json')
                if _has_error(record):
                    errored.append(f'{part + "/" if part else ""}{check["run"]}/run.json')
                    continue
                if record.get("status") != "finished" or record.get("amended"):
                    continue
                for result in record.get("checks", []):
                    if (result.get("patch") == "final" and result.get("patch_applied") is True
                            and type(result.get("passes")) is bool):
                        labels.add("right" if result["passes"] else "wrong")
                        label_sources.append(f'{part + "/" if part else ""}{check["run"]}/run.json')
            if errored:
                excluded.append({"bug": bug, "patch": name, "reason": "errored archived check", "checks": errored})
                continue
            if len(labels) != 1:
                excluded.append({"bug": bug, "patch": name,
                                 "reason": "conflicting archived labels" if labels else "no archived label"})
                continue
            rounds = source.read_json(f"{rel}/rounds.json")
            digest = hashlib.sha256(source.read_bytes(f"{rel}/final.patch")).hexdigest()
            matched = []
            for review in rounds:
                number = review["round"]
                if type(number) is not int or number < 1:
                    raise ValueError(f"invalid round in {rel}")
                round_rel = f"{rel}/rounds/round-{number}.patch"
                if source.path(round_rel).is_file() and hashlib.sha256(source.read_bytes(round_rel)).hexdigest() == digest:
                    args = review.get("input")
                    input_rel = f"{rel}/rounds/round-{number}.input.json"
                    if source.path(input_rel).is_file():
                        captured = source.read_json(input_rel)
                        captured = captured.get("tool_input", captured)
                        if args is not None and captured != args:
                            raise ValueError(f"archived review arguments disagree: {input_rel}")
                        args = captured
                    if isinstance(args, dict):
                        matched.append((number, args))
            number, arguments = max(matched, key=lambda x: x[0]) if matched else (None, None)
            prompt = source.read_bytes(f"{rel}/prompt.txt").decode("utf-8")
            issue = re.search(r"(?m)^Issue #\d+:", prompt)
            if not issue:
                raise ValueError(f"no issue text in {rel}/prompt.txt")
            origin = {"run": f'{part + "/" if part else ""}{rel}', "archive_round": archive_round,
                      "arm": row.get("arm") or "original", "round": number, "label_sources": label_sources}
            origin["arguments"] = arguments if _agent_origin(origin) else None
            origin["task_text"] = prompt[issue.start():].strip()
            candidate = Case(bug, digest, next(iter(labels)), patch, root, prompt[issue.start():].strip(),
                             origin["arm"], number, arguments if _agent_origin(origin) else None, [origin])
            key = bug, digest
            if key in found:
                previous = found[key]
                if previous.label != candidate.label:
                    raise ValueError(f"conflicting labels for {bug} digest {digest}")
                candidate.origins = previous.origins + candidate.origins
                if previous.arguments is not None or candidate.arguments is None:
                    previous.origins = candidate.origins
                    continue
            found[key] = candidate
    return sorted(found.values(), key=lambda x: (x.digest, x.bug)), excluded


def output_archive(source: Path, target: Path) -> c.Archive:
    source, target = canonical_path(source), canonical_path(target)
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("output root must be disjoint from source archive")
    if target.exists():
        raise ValueError("output archive must be a new root")
    return c.Archive(target)


@dataclass(frozen=True)
class Registration:
    sha256: str
    predictions: tuple[tuple[str, str], ...]
    limits: dict[str, int]
    neighbours: tuple[str, ...]
    no_lead_bug: str
    question_bug: str


def _parse_registration(content: bytes) -> Registration:
    """Accept the registered table grammar, extracting every adjustable limit."""
    content = content.replace(b"\r\n", b"\n")
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("unparseable registration: expected UTF-8") from exc
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) != 9 or rows[:2] != ["| # | Prediction | Kill criterion |", "|---|---|---|"]:
        raise ValueError("unparseable registration prediction table")
    predictions = []
    for number, row in enumerate(rows[2:], 1):
        cells = [cell.strip() for cell in row.split("|")]
        if len(cells) != 5 or cells[0] or cells[-1] or cells[1] != f"P{number}":
            raise ValueError("unparseable registration prediction rows")
        predictions.append((cells[2], cells[3]))
    patterns = (
        (r"Modes 1 and 2 on RC: no integrity alarm on any right patch or on the maintainers' fix, "
         r"on upstream-fetched repositories", r"Any alarm: that class is open; repair before any live run"),
        (r'Mode 1 on RC: no "supported" on a wrong patch except through the S0 hole; after S0, none',
         r'A "supported" on a wrong patch after S0: the rule has another hole'),
        (r"Mode 2 on RC: `defect_detected` on at most (?P<hook_wrong>\d+) of (?P<hook_wrong_total>\d+) "
         r"wrong patches and on (?P<hook_right>\d+) of (?P<hook_right_total>\d+) right",
         r"More than (?P<hook_kill>\d+) of (?P<hook_kill_total>\d+) right called a defect: "
         r"R1's default is not safe to ship as it stands"),
        (r"Mode 3: a lead on at least (?P<percent>\d+)% of distinct wrong patches on at least "
         r"(?P<reached>\d+) of the (?P<neighbour_total>\d+) neighbouring-case bugs \((?P<neighbours>[^()]+)\)",
         r"A lead on fewer than (?P<reached_kill>\d+) of the (?P<neighbour_kill_total>\d+) bugs: "
         r"tiers 1 and 2 do not reach what they were designed from; stop"),
        (r"Mode 3: a lead on at most (?P<right_leads>\d+) of the (?P<right_leads_total>\d+) right patches "
         r"\(4\.4%, upper 95% bound just under 10%\)",
         r"More than (?P<right_leads_kill>\d+) of (?P<right_leads_kill_total>\d+): "
         r"the lifted relations misfire; stop"),
        (r"Mode 3: no lead on (?P<no_lead_bug>[a-z]+-\d+); on (?P<question_bug>[a-z]+-\d+) "
         r"no lead and at least one question candidate",
         r"A lead on most (?P<surprise_bug>[a-z]+-\d+) patches would be a welcome surprise and is reported as one"),
        (r"Median review time under (?P<seconds>\d+) s for each patch in mode 3",
         r"Over (?P<seconds_kill>\d+) s: the tiers need a tighter exit"),
    )
    values = {}
    for number, (pair, grammar) in enumerate(zip(predictions, patterns), 1):
        for cell, pattern in zip(pair, grammar):
            match = re.fullmatch(pattern, cell)
            if not match:
                raise ValueError(f"unparseable registration P{number}")
            values.update(match.groupdict())
    neighbours = tuple(values.pop("neighbours").split(", "))
    no_lead_bug, question_bug, surprise_bug = (values.pop(key) for key in
                                              ("no_lead_bug", "question_bug", "surprise_bug"))
    limits = {key: int(value) for key, value in values.items()}
    if (not all(re.fullmatch(r"[a-z]+-\d+", bug) for bug in neighbours)
            or len(set(neighbours)) != len(neighbours)
            or limits["neighbour_total"] != len(neighbours)
            or limits["neighbour_kill_total"] != len(neighbours)
            or not 0 <= limits["percent"] <= 100
            or not 0 <= limits["reached"] <= len(neighbours)
            or not 0 <= limits["reached_kill"] <= len(neighbours)
            or no_lead_bug != surprise_bug):
        raise ValueError("inconsistent registration limits")
    return Registration(hashlib.sha256(content).hexdigest(), tuple(predictions), limits, neighbours,
                        no_lead_bug, question_bug)


def load_registration() -> Registration:
    path = canonical_path(REGISTRATION)
    root = canonical_path(we.REPO_ROOT)
    if not path.is_relative_to(root):
        raise ValueError("registration must stay inside repository")
    rel = REGISTRATION.relative_to(we.REPO_ROOT).as_posix()
    got = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, timeout=30)
    current = path.read_bytes().replace(b"\r\n", b"\n")
    if got.returncode or got.stdout.replace(b"\r\n", b"\n") != current:
        raise ValueError("commit the unchanged REPLAY_RC.md registration before any replay")
    return _parse_registration(current)


def require_registration() -> str:
    return load_registration().sha256


class _PredictionTable:
    """Compatibility view; prediction text has no second source in code."""

    def __getitem__(self, index):
        return load_registration().predictions[index]


PREDICTIONS = _PredictionTable()


def inspect_images(docker: Any, images: dict[str, str], commit: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("registered commit must be a full lowercase commit")
    pinned = {}
    for bug, image in sorted(images.items()):
        if not image.startswith(f"g6we-task:{bug}-"):
            raise ValueError("only task images are allowed")
        data = json.loads(we.checked(docker("image", "inspect", image, timeout=60), "inspect task image"))
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ValueError(f"unreadable task image commit: {image}")
        config = data[0].get("Config") or {}
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get("we.g6.commit") != commit:
            raise ValueError(f"task image G6 commit differs from registered commit: {image}")
        image_id = data[0].get("Id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError(f"task image has no immutable ID: {image}")
        pinned[bug] = image_id
    return pinned


def neighbourhood_arguments(case: Case, task: we.Task) -> None:
    """Reserved mode 3 hook. No execution until the mechanism exists."""
    return None


def review_arguments(case: Case, task: we.Task, mode: str) -> dict:
    if mode == "agent" and case.arguments is not None:
        return {**case.arguments, "repo": task.repo, "base": task.parent}
    if mode == "hook":
        return {"repo": task.repo, "task": case.task_text, "requirements": [
            {"id": "task", "text": case.task_text[:20_000], "checks": []}], "base": task.parent, "timeout_s": 600}
    raise ValueError(f"mode unavailable for {case.bug}/{case.digest}: {mode}")


def agent_views(case: Case) -> list[Case]:
    """Each eligible run keeps its own final-patch arguments; the patch is still counted once."""
    return [replace(case, arguments=origin["arguments"], arm=origin["arm"], round=origin["round"],
                    task_text=origin["task_text"], origins=[origin]) for origin in case.origins
            if origin.get("arguments") is not None]


TREE_STATE = we.TREE_STATE.replace('"dirty": %s', '"parent_tree": "%s", "oracle": %s, "dirty": %s').replace(
    '"$(git status --porcelain | wc -l)"',
    '"$(git rev-parse "$1^{tree}")" "$(test -e /oracle && echo true || echo false)" '
    '"$(git status --porcelain | wc -l)"')


def readable_review(reply: Any) -> bool:
    return (isinstance(reply, dict) and reply.get("ok") is True and not reply.get("error")
            and isinstance(reply.get("assessment"), dict) and reply["assessment"].get("outcome") in OUTCOMES
            and isinstance(reply["assessment"].get("completion", {}), dict))


def replay_case(case: Case, task: we.Task, mode: str, archive: c.Archive, docker: Any,
                image_id: str, commit: str) -> dict:
    args = review_arguments(case, task, mode)
    folder = archive.start("replay-rc", case.bug, mode=mode, digest=case.digest, g6_commit=commit)
    body = {"bug": case.bug, "digest": case.digest, "label": case.label, "mode": mode,
            "g6_commit": commit, "image_id": image_id, "origins": case.origins,
            "arguments": args, "cost_usd": 0, "result": "no review", "seconds": None}
    if mode == "agent":
        body["origin_run"] = next(o["run"] for o in case.origins if o.get("arguments") == case.arguments)
    name = f"g6we-replay-rc-{uuid.uuid4().hex[:12]}"
    status = "failed"
    attempted = False
    try:
        patch = Source(case.source).read_bytes(case.patch.relative_to(case.source))
        if hashlib.sha256(patch).hexdigest() != case.digest:
            raise ValueError("source patch digest changed after enumeration")
        snapshot = folder.write("candidate.patch", patch)
        attempted = True
        we.checked(docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.LIMITS,
                          *we.SECURITY, "-u", we.USER, "-e", f"HOME={we.HOME}",
                          "-e", "G6_HOME=/tmp/g6-replay-home", "-e", "G6_LLM_PROVIDER=none",
                          "-e", "G6_CLAUDE=/nonexistent/g6-no-host", "-e", "G6_CODEX=/nonexistent/g6-no-host",
                          "-w", task.repo, image_id, "tail", "-f", "/dev/null", timeout=600), "replay container")
        tree = json.loads(we.checked(docker.sh(name, TREE_STATE, task.parent, workdir=task.repo), "base git state"))
        body["tree_state"] = tree
        if (tree.get("head") != task.parent or tree.get("tree") != tree.get("parent_tree")
                or not re.fullmatch(r"[0-9a-f]{40}", str(tree.get("tree", "")))
                or tree.get("dirty") != 0 or tree.get("commits") != 1 or tree.get("oracle") is not False):
            raise ValueError("task image is not a clean upstream parent with one commit and no oracle")
        we.checked(docker("cp", str(snapshot), f"{name}:/tmp/we.patch", timeout=300), "patch copy")
        we.checked(docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo), "patch")
        # The only JSON sent to G6 is the review's arguments. Archive labels stay on the host.
        program = ("import json\nfrom g6.harness.patch_review import review_patch\n"
                   f"args = json.loads({json.dumps(args)!r})\n"
                   "repo = args.pop('repo')\nprint(json.dumps(review_patch(repo, **args)))\n")
        started = time.monotonic()
        reply = docker("exec", "-i", "-u", we.USER, "-e", f"HOME={we.HOME}", "-w", task.repo,
                       name, we.G6PY, "-I", "-", input=program.encode("utf-8"), timeout=2400)
        body["seconds"] = time.monotonic() - started
        folder.write("stdout.txt", reply.stdout or b"")
        folder.write("stderr.txt", reply.stderr or b"")
        we.checked(reply, "review process")
        body["review"] = json.loads(we.out(reply).splitlines()[-1])
        if not readable_review(body["review"]):
            raise ValueError("no completed review outcome (refusal or unreadable assessment)")
        status, body["result"] = "finished", "review"
    except (OSError, RuntimeError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        body["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if attempted:
            try:
                we.checked(docker("rm", "-f", name, timeout=300), "container cleanup")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                status, body["result"] = "failed", "no review"
                body["error"] = f'{body.get("error", "")} cleanup: {type(exc).__name__}: {exc}'.strip()
    return folder.finish(body, status)


def _signals(assessment: dict) -> tuple[bool, bool]:
    neighbourhood = (assessment.get("completion") or {}).get("neighbourhood") or {}
    kinds = {str(row.get("class", "")).lower().replace(" ", "_") for row in neighbourhood.get("cases", [])}
    lead = bool(kinds & {"symptom_persists", "agreement_lost", "kept_behaviour_changed"})
    return lead, bool(neighbourhood.get("question_candidates"))


def evaluate(records: list[dict], cases: list[Case] | list[dict], *, modes: list[str], s0: str = "before",
             excluded: list[dict] | None = None, registration: Registration | None = None) -> dict:
    """Score saved records against the committed registration, without reading source archives."""
    registration = registration or load_registration()
    limits = registration.limits
    cases = [case.saved() if isinstance(case, Case) else case for case in cases]
    expected = {(case["bug"], case["digest"], mode): case for case in cases for mode in modes
                if mode != "agent" or case.get("arguments") is not None}
    by_bug: dict[str, dict] = {}

    def group_for(bug, mode):
        return by_bug.setdefault(bug, {}).setdefault(mode, {"expected": 0, "reviewed": 0, "no_review": 0,
            "expected_calls": 0, "completed_calls": 0, "failed_calls": 0,
            "right": 0, "wrong": 0, "right_integrity": 0, "right_defect": 0, "wrong_defect": 0,
            "wrong_supported": 0, "right_lead": 0, "wrong_lead": 0, "questions": 0, "seconds": [], "excluded": 0})

    for bug, digest, mode in expected:
        group = group_for(bug, mode)
        group["expected"] += 1
        case = expected[bug, digest, mode]
        group["expected_calls"] += (sum(o.get("arguments") is not None for o in case.get("origins", []))
                                    if mode == "agent" else 1)
    omissions = list(excluded or []) + [
        {"bug": case["bug"], "digest": case["digest"], "mode": "agent",
         "reason": "no matching final-patch review arguments in eligible arm"}
        for case in cases if case.get("arguments") is None and "agent" in modes]
    for omission in omissions:
        for mode in modes:
            if omission.get("mode", mode) != mode:
                continue
            group = group_for(omission["bug"], mode)
            group["expected"] += 1
            group["excluded"] += 1
    seen = set()
    collected: dict[tuple, list] = {}
    for record in records:
        key = record["bug"], record["digest"], record["mode"]
        call = (*key, record.get("origin_run") if key[2] == "agent" else None)
        if key not in expected or call in seen or record.get("label") != expected[key]["label"]:
            raise ValueError("duplicate, unplanned or mislabelled replay record")
        if key[2] == "agent" and record.get("origin_run") not in {
                o["run"] for o in expected[key].get("origins", []) if o.get("arguments") is not None}:
            raise ValueError("agent record does not name planned archived arguments")
        seen.add(call)
        collected.setdefault(key, []).append(record)
    for key, attempts in collected.items():
        group = by_bug[key[0]][key[2]]
        good = [r for r in attempts if r.get("status") == "finished" and readable_review(r.get("review"))]
        group["failed_calls"] += len(attempts) - len(good)
        group["completed_calls"] += len(good)
        if not good:
            group["no_review"] += 1
            continue
        assessments = [r["review"]["assessment"] for r in good]
        outcomes, label = {a["outcome"] for a in assessments}, expected[key]["label"]
        signals = [_signals(a) for a in assessments]
        lead, question = any(s[0] for s in signals), any(s[1] for s in signals)
        group["reviewed"] += 1
        group[label] += 1
        group["right_integrity"] += label == "right" and "test_integrity_compromised" in outcomes
        group["right_defect"] += label == "right" and "defect_detected" in outcomes
        group["wrong_defect"] += label == "wrong" and "defect_detected" in outcomes
        group["wrong_supported"] += label == "wrong" and "supported_within_scope" in outcomes
        group[f"{label}_lead"] += lead
        group["questions"] += question
        times = [r["seconds"] for r in good if type(r.get("seconds")) in (int, float) and r["seconds"] >= 0]
        if len(times) == len(good):
            group["seconds"].append(median(times))

    for values in by_bug.values():
        for group in values.values():
            group["missing"] = group["expected"] - group["reviewed"] - group["no_review"]
            group["median_seconds"] = median(group["seconds"]) if group["seconds"] else None

    def groups(mode):
        return {bug: values[mode] for bug, values in by_bug.items() if mode in values}

    def complete(mode):
        return bool(groups(mode)) and all(g["reviewed"] == g["expected"] and g["completed_calls"] == g["expected_calls"]
                                          for g in groups(mode).values())

    def count(mode, field):
        return sum(g[field] for g in groups(mode).values())

    def prediction(number, met, kill, selected, *, ready=True, **details):
        enabled = any(mode in modes for mode in selected)
        done = enabled and ready and all(mode in modes and complete(mode) for mode in selected)
        missing = sum(count(mode, "missing") + count(mode, "no_review") for mode in selected)
        excluded_count = sum(count(mode, "excluded") for mode in selected)
        return {"prediction": registration.predictions[number - 1][0],
                "kill_criterion": registration.predictions[number - 1][1],
                "status": "disabled" if not enabled else "incomplete" if excluded_count else "killed" if kill else
                          "incomplete" if not done else "met" if met else "missed",
                "prediction_met": met if done or met is False else None, "kill": kill if enabled else None,
                "missing": missing, **details}

    p4 = {bug: {"wrong": groups("neighbourhood").get(bug, {}).get("wrong", 0),
                "leads": groups("neighbourhood").get(bug, {}).get("wrong_lead", 0)} for bug in registration.neighbours}
    for values in p4.values():
        values["reached_threshold"] = values["wrong"] > 0 and values["leads"] * 100 >= values["wrong"] * limits["percent"]
        if limits["percent"] == 40:
            values["reached_40_percent"] = values["reached_threshold"]
    reached = sum(v["reached_threshold"] for v in p4.values())
    p4_ready = all(bug in groups("neighbourhood") and p4[bug]["wrong"] for bug in registration.neighbours)
    black = groups("neighbourhood").get(registration.no_lead_bug, {})
    click = groups("neighbourhood").get(registration.question_bug, {})
    black_leads = black.get("right_lead", 0) + black.get("wrong_lead", 0)
    click_leads = click.get("right_lead", 0) + click.get("wrong_lead", 0)
    times = [seconds for group in groups("neighbourhood").values() for seconds in group["seconds"]]
    middle = median(times) if times else None
    wrong_supported = count("agent", "wrong_supported")
    predictions = {
        "P1": prediction(1, count("agent", "right_integrity") + count("hook", "right_integrity") == 0,
                         count("agent", "right_integrity") + count("hook", "right_integrity") > 0,
                         ["agent", "hook"], ready=False, limitation="maintainers' fix control is outside final-patch replay"),
        "P2": prediction(2, None if s0 == "before" and wrong_supported else wrong_supported == 0,
                         s0 == "after" and wrong_supported > 0, ["agent"],
                         ready=count("agent", "wrong") > 0 and (s0 == "after" or wrong_supported == 0), s0=s0,
                         attribution="S0 attribution required" if s0 == "before" and wrong_supported else None),
        "P3": prediction(3, count("hook", "wrong_defect") <= limits["hook_wrong"]
                         and count("hook", "right_defect") == limits["hook_right"],
                         count("hook", "right_defect") > limits["hook_kill"], ["hook"],
                         ready=count("hook", "wrong") > 0 and count("hook", "right") > 0),
        "P4": prediction(4, reached >= limits["reached"],
                         p4_ready and complete("neighbourhood") and reached < limits["reached_kill"],
                         ["neighbourhood"], ready=bool(p4_ready), by_bug=p4, threshold_percent=limits["percent"]),
        "P5": prediction(5, count("neighbourhood", "right_lead") <= limits["right_leads"],
                         count("neighbourhood", "right_lead") > limits["right_leads_kill"], ["neighbourhood"],
                         ready=count("neighbourhood", "right") > 0),
        "P6": prediction(6, black_leads == 0 and click_leads == 0 and click.get("questions", 0) >= 1,
                         False, ["neighbourhood"], ready=bool(black and click),
                         welcome_surprise=bool(black.get("reviewed") and black_leads * 2 > black["reviewed"])),
        "P7": prediction(7, middle is not None and middle < limits["seconds"],
                         middle is not None and middle > limits["seconds_kill"],
                         ["neighbourhood"], ready=len(times) == count("neighbourhood", "reviewed"), median_seconds=middle),
    }
    return {"standing": "development", "unit": "bug", "registration_sha256": registration.sha256,
            "by_bug": by_bug, "predictions": predictions, "excluded": omissions}


def validate_record_pins(records: list[dict], plan: dict, image_ids: dict) -> None:
    commit = plan.get("registered_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("plan has no full registered G6 commit")
    if not isinstance(image_ids, dict):
        raise ValueError("replay record image ID differs from plan")
    for record in records:
        if record.get("g6_commit") != commit:
            raise ValueError("replay record G6 commit differs from plan")
        image = image_ids.get(record.get("bug"))
        if (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
                or record.get("image_id") != image):
            raise ValueError("replay record image ID differs from plan")


def make_plan(cases: list[Case], excluded: list[dict], tasks: list[we.Task], commit: str,
              modes: list[str], variant: str) -> dict:
    by_bug = {bug: {mode: sum(case.bug == bug and (mode == "hook" or mode == "agent" and case.arguments is not None)
                             for case in cases) for mode in MODES} for bug in sorted({case.bug for case in cases})}
    return {"standing": "development", "unit": "bug", "registered_commit": commit, "by_bug": by_bug,
            "calls_by_bug": {bug: {"agent": sum(len(agent_views(case)) for case in cases if case.bug == bug),
                                   "hook": counts["hook"], "neighbourhood": 0} for bug, counts in by_bug.items()},
            "modes": modes, "neighbourhood": "disabled", "excluded": excluded,
            "unavailable": [{"bug": case.bug, "digest": case.digest, "mode": "agent",
                             "reason": "no matching final-patch review arguments in eligible arm"}
                            for case in cases if case.arguments is None],
            "images": {task.id: we.image_names(task, commit, variant)[0] for task in tasks if task.id in by_bug},
            "cases": [case.saved() for case in cases]}


def main(argv: list[str] | None = None, *, docker: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--registered-commit")
    parser.add_argument("--variant", default="")
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--s0", choices=("before", "after"), default="before")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--score", type=Path, help="rescore a saved batch run.json without containers")
    options = parser.parse_args(argv)
    try:
        if options.score:
            saved = json.loads(canonical_path(options.score).read_text(encoding="utf-8"))
            validate_record_pins(saved["records"], saved["plan"], saved.get("image_ids", {}))
            registration = load_registration()
            if saved.get("registration_sha256") != registration.sha256:
                raise ValueError("saved registration digest differs from committed registration")
            print(json.dumps(evaluate(saved["records"], saved["plan"]["cases"], modes=saved["plan"]["modes"],
                                      s0=saved["s0"], excluded=saved["plan"].get("excluded", []),
                                      registration=registration), indent=2))
            return 0
        if options.source is None or not re.fullmatch(r"[0-9a-f]{40}", options.registered_commit or ""):
            raise ValueError("--source and a full --registered-commit are required")
        if not re.fullmatch(r"[a-z0-9-]*", options.variant):
            raise ValueError("invalid image variant")
        modes = list(dict.fromkeys(options.mode or ["agent", "hook"]))
        if "neighbourhood" in modes:
            raise ValueError("neighbourhood execution is disabled until the mechanism exists")
        tasks = we.load_tasks()
        cases, excluded = enumerate_cases(options.source, tasks)
        plan = make_plan(cases, excluded, tasks, options.registered_commit, modes, options.variant)
        if options.dry:
            print(json.dumps(plan, indent=2))
            return 0
        target = os.environ.get("G6_WE_ARCHIVE", "").strip()
        if not target:
            raise ValueError("G6_WE_ARCHIVE must name a new output root")
        archive = output_archive(options.source, Path(target))
        registration = load_registration()
        archive.root.mkdir(parents=True, exist_ok=False)
        batch = archive.start("replay-rc-batch", "development", g6_commit=options.registered_commit)
        records = []
        body = {"plan": plan, "s0": options.s0, "registration_sha256": registration.sha256, "records": records}
        try:
            docker = docker if docker is not None else we.Docker()
            pinned = inspect_images(docker, plan["images"], options.registered_commit)
            body["image_ids"] = pinned
            by_id = {task.id: task for task in tasks}
            for case in cases:
                for mode in modes:
                    if mode == "agent" and case.arguments is None:
                        continue
                    for view in agent_views(case) if mode == "agent" else [case]:
                        records.append(replay_case(view, by_id[case.bug], mode, archive, docker,
                                                   pinned[case.bug], options.registered_commit))
            validate_record_pins(records, plan, pinned)
            body["evaluation"] = evaluate(records, cases, modes=modes, s0=options.s0,
                                           excluded=excluded, registration=registration)
            batch.finish(body, "finished")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            body["error"] = f"{type(exc).__name__}: {exc}"
            batch.finish(body, "failed")
            raise
        print(json.dumps(body["evaluation"], indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"replay_rc: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
