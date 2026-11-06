# SPDX-License-Identifier: Apache-2.0
"""The replay cohorts for G6 0.3 (PLAN.md, 2026-11-06 night; the design's sections 3.6 and 7, slice 1): development, selection
and final, frozen by name, each with a manifest that records its bugs, their image digests, the control results and its own
digest, written before any slice run reads it.

- **Development:** the bugs of stages 1 and 2 (the eleven fresh bugs, the three held-out bugs and stage 2's twenty) and the K3
  pilot's seven. Every choice is made on these.
- **Selection:** stage 4's 22 whole bugs, less any that is development. A choice is scored on it once.
- **Final:** the 84 stage 5 bugs both audit families labelled determined. Only their names and image digests are read here:
  never an issue, a test or a fix.

Each development and selection bug passes four controls before it enters a cohort's runs (``controls``): the maintainers' fix
passes the maintainers' tests and the base commit fails them (``run.validate``), the fix given as a patch passes and an empty
patch fails (``run.selfcheck``), and a known patch written through the host executor's runner comes back byte for byte in the
kept patch (``round_trip``). A bug that fails a control is excluded and named, with its reason.

``python cohorts.py sizes``; ``python cohorts.py controls --cohort development|selection [--only BUG]`` (containers);
``python cohorts.py manifest --cohort NAME`` (``docker image inspect`` only, and the saved control records).
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import run as we  # noqa: E402
import wecommon as c  # noqa: E402

# --- the names, each from its stage's own saved record ------------------------------------------------------------------------
#: Stage 1 (PLAN.md, 2026-10-31 evening): the eleven fresh bugs, ``tasks_fresh.json`` in its order, and the three held-out bugs
#: (``run.R3_HELD``), "the 34 bugs of stages 1 and 2" with stage 2's (PLAN.md, 2026-11-05 morning).
STAGE_1_FRESH = ("pydantic-13243", "black-5144", "black-5148", "click-3434", "click-3404", "pydantic-13133", "starlette-3189",
                 "attrs-1509", "starlette-3166", "fastapi-14616", "fastapi-13920")
STAGE_1_HELD = ("black-5521", "click-3769", "starlette-3718")
#: Stage 2 (PLAN.md, 2026-11-02 morning): its twenty bugs in the frozen order, ``tasks_unseen.json``.
STAGE_2 = ("urllib3-5347", "urllib3-5254", "charset-normalizer-772", "charset-normalizer-715", "setuptools-5362", "setuptools-5352",
           "pygments-3204", "pygments-3158", "pluggy-809", "pluggy-799", "botocore-3852", "botocore-3835", "anyio-1424", "anyio-1354",
           "s3transfer-423", "python-dotenv-711", "python-dotenv-700", "pyjwt-1216", "pyjwt-1201", "platformdirs-666")
#: The K3 pilot (PLAN.md, 2026-11-06 afternoon): its seven bugs, in the order its fourteen runs started (round 3's index).
K3_PILOT = ("black-5148", "click-3404", "fastapi-13920", "starlette-3189", "pygments-3158", "s3transfer-423", "pydantic-settings-1089")
#: Stage 4 (PLAN.md, 2026-11-05 early): the 22 whole bugs, arm A twice and V4 twice, each run ended, in ``tasks_stage4.json``'s
#: draw order. The draw's 31 less the two whose image did not build (yarl-1852, yarl-1919), pytz-142, which preparation refused,
#: and the six that gave way at run time with no finished validation.
STAGE_4_WHOLE = ("uvicorn-3236", "uvicorn-3199", "tomlkit-552", "tomlkit-581", "markdown-it-py-416", "opentelemetry-python-5823",
                 "pip-14218", "pyasn1-108", "pyasn1-102", "websockets-1752", "websockets-1738", "pydantic-settings-1109",
                 "pydantic-settings-1089", "wheel-695", "wheel-686", "tenacity-660", "pyparsing-652", "pyparsing-649", "soupsieve-316",
                 "soupsieve-315", "openai-python-4166", "openai-python-3982")
#: Stage 5 (PLAN.md, 2026-11-05 late morning; ``selection_stage5.md`` at claude/stage5 f558566): the 84 of 130 both families
#: labelled determined, by the registered rule, from the audit's combined labels (``FINAL_SOURCE``), in ``tasks_stage5.json``'s
#: draw order. Names only.
FINAL = ("urllib3-5271", "urllib3-5212", "charset_normalizer-825", "pygments-3171", "pygments-3175", "pluggy-767", "pluggy-741",
         "anyio-1367", "aiobotocore-1704", "python-dotenv-698", "python-dotenv-680", "pyjwt-1208", "platformdirs-661", "platformdirs-663",
         "filesystem_spec-2352", "python-pathspec-162", "python-pathspec-132", "filelock-880", "filelock-827", "uvicorn-3103",
         "uvicorn-3041", "tomlkit-620", "tomlkit-563", "opentelemetry-python-5650", "pip-14231", "pip-14228", "pydantic-settings-1077",
         "pydantic-settings-1076", "s3fs-1042", "soupsieve-310", "soupsieve-308", "openai-python-3875", "openai-python-3851",
         "psutil-2965", "python-sdk-3776", "redis-py-4513", "pre-commit-3774", "pre-commit-3743", "markitdown-2412",
         "ui-ux-pro-max-skill-548", "graphify-1323", "graphify-1282", "moneyprinterturbo-1585", "moneyprinterturbo-1296",
         "browser-use-5640", "manim-2501", "learn-claude-code-603", "learn-claude-code-571", "ai-engineering-from-scratch-503",
         "llamafactory-10632", "strix-1116", "ppt-master-324", "docling-4813", "ai-job-search-686", "mem0-6559", "mem0-6547",
         "annotated_deep_learning_paper_implementations-326", "scrapy-8386", "minimind-883", "ai-hedge-fund-719", "keras-24055",
         "keras-23782", "crewai-8642", "mempalace-2632", "voicebox-895", "cli-anything-421", "deeptutor-1041", "llama_index-23223",
         "llama_index-23208", "supervision-2795", "mitmproxy-8489", "mitmproxy-8326", "claude-howto-222", "langgraph-9406",
         "langgraph-9407", "gradio-13983", "deepspeed-8882", "ifixai-350", "agno-11347", "agno-11327", "manim-5078", "manim-5040",
         "agents-666", "colossalai-6468")
FINAL_SOURCE = {"file": "audit_stage5_labels_combined.json", "sha256": "fc90565f968ed932cf930a1fc2c928f56f22e2432b548a5750222eabba78dcbf",
                "record": "selection_stage5.md at claude/stage5 f558566: Opus 86, Codex 95, both 84",
                "rule": "a bug is determined only if both families say so"}
#: What the slice 1 builder saw of a final-cohort bug beyond its name: recorded, for the owner to rule on.
FINAL_SEEN = {"strix-1116": "one line of selection_stage5.md's judgement column (a summary of its issue's report), read by the "
                            "slice 1 builder on 2026-11-06 while looking for the audit's combined file; nothing else of it was read"}

G6_COMMIT = "c53d310d65a5b5ba02c64c3faf6682dcf048888e"     # the images' G6, the K3 pilot's (round 3's index)
VARIANT = we.ROUND3
FLOOR_GB = 6.5        # the run drivers' memory floor: a container step waits while less is available


def _unique(*parts: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for part in parts:
        seen.extend(bug for bug in part if bug not in seen)
    return tuple(seen)


DEVELOPMENT = _unique(STAGE_1_FRESH, STAGE_1_HELD, STAGE_2, K3_PILOT)
#: Bugs a cohort's source names that the cohort leaves out, with why, before any control runs.
LEFT_OUT = {"selection": {bug: "in the development cohort: a K3 pilot bug, whose fourteen runs' records, issue and failing tests' "
                               "names the pilot's analysis read (PLAN.md, 2026-11-06 evening)"
                          for bug in STAGE_4_WHOLE if bug in DEVELOPMENT}}
SELECTION = tuple(bug for bug in STAGE_4_WHOLE if bug not in DEVELOPMENT)
COHORTS: dict[str, tuple[str, ...]] = {"development": DEVELOPMENT, "selection": SELECTION, "final": FINAL}
SOURCES = {"development": {"stage 1 fresh": list(STAGE_1_FRESH), "stage 1 held-out": list(STAGE_1_HELD), "stage 2": list(STAGE_2),
                           "K3 pilot": list(K3_PILOT)},
           "selection": {"stage 4 whole bugs": list(STAGE_4_WHOLE)},
           "final": {"stage 5 determined by both families": FINAL_SOURCE}}
CONTROLLED = ("development", "selection")        # the cohorts whose bugs run the controls here; the final cohort's never do


def cohort(name: str) -> tuple[str, ...]:
    if name not in COHORTS:
        raise SystemExit(f"no cohort {name!r}: {', '.join(COHORTS)}")
    return COHORTS[name]


# --- memory and images ---------------------------------------------------------------------------------------------------------
def free_gb() -> float | None:
    """The memory available now, in GB, as the run drivers read it (Windows: ``GlobalMemoryStatusEx``; elsewhere
    ``/proc/meminfo``'s MemAvailable); None when it cannot be read."""
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("pagefile", ctypes.c_ulonglong), ("avail_pagefile", ctypes.c_ulonglong),
                        ("virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
        got = Memory()
        got.length = ctypes.sizeof(Memory)
        return got.avail / 2 ** 30 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(got)) else None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2 ** 20
    except (OSError, ValueError):
        return None
    return None


class Floored(we.Docker):
    """The docker client the controls use: before each container step (a ``run``, ``exec``, ``cp`` or ``build``), it waits while
    less than ``FLOOR_GB`` is available (``free_gb``), checking every ``every`` seconds. A reading that fails waits too."""

    STEPS = ("run", "exec", "cp", "build")

    def __init__(self, *a: Any, floor: float = FLOOR_GB, every: float = 20.0, reader: Callable[[], float | None] = free_gb,
                 sleep: Callable[[float], None] = time.sleep, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.floor, self.every, self.reader, self.sleep, self.waited = floor, every, reader, sleep, 0.0

    def __call__(self, *args: str, **kw: Any) -> Any:
        if args and args[0] in self.STEPS:
            while (self.reader() or 0.0) < self.floor:
                self.sleep(self.every)
                self.waited += self.every
        return super().__call__(*args, **kw)


def image_ids(bug: str, docker: Callable[..., Any]) -> dict[str, str | None]:
    """The bug's task and oracle images as ``run.image_names`` names them for ``G6_COMMIT``, each with the id docker holds for it
    (``docker image inspect``), or None when docker holds none. Nothing else of the image is read."""
    task_image, oracle = we.image_names(type("Named", (), {"id": bug})(), G6_COMMIT, VARIANT)
    ids: dict[str, str | None] = {"task_image": task_image, "oracle_image": oracle}
    for key, tag in (("task_image_id", task_image), ("oracle_image_id", oracle)):
        got = docker("image", "inspect", tag, "--format", "{{.Id}}", timeout=120)
        ids[key] = we.out(got) if got.returncode == 0 and we.out(got).startswith("sha256:") else None
    return ids


# --- the controls -------------------------------------------------------------------------------------------------------------
def controls_root(archive_root: Path | None = None) -> Path:
    """Where the cohort records are written: a ``cohorts`` folder of the worked examples' archive, new for this slice."""
    return c.archive_root(archive_root) / "cohorts"


def g6_for(commit: str = G6_COMMIT) -> dict[str, Any]:
    return {"commit": commit, "image": we.base_tag(commit), "contains_required": True}


def recorded_tree(study: c.Archive, bug: str) -> str:
    """Upstream's tree for the bug's base commit, as an earlier run of it recorded it (read from GitHub's API then): the newest
    round 3 run's ``tree_state.upstream_tree`` that is not empty, else "". Nothing else of the run is read."""
    found = ""
    for row in study.rows():
        if row.get("event") == "started" and row.get("kind") == "run" and row.get("task") == bug:
            record = study.root / str(row["run"]) / "run.json"
            try:
                tree = (json.loads(record.read_text("utf-8")).get("tree_state") or {}).get("upstream_tree") or ""
            except (OSError, ValueError):
                continue
            found = tree or found
    return found


def known_patch(ctx: we.Context, task: we.Task, gold: Path, dest: Path) -> bytes:
    """The known patch: the maintainers' fix (``gold``, as ``selfcheck`` kept it) applied in a fresh container from the bug's task
    image, with no network, and kept by the one capture every run's patch is kept by (``run.diff_script``) against the parent,
    so it is in the kept patch's own form. Its bytes, also written to ``dest``."""
    task_image, _ = we.image_names(task, str(ctx.g6["commit"]), ctx.variant)
    name = f"g6we-known-{uuid.uuid4().hex[:10]}"
    try:
        we.checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.LIMITS, *we.SECURITY,
                              "-u", we.USER, "-e", f"HOME={we.HOME}", "-w", task.repo, task_image, "tail", "-f", "/dev/null",
                              timeout=600), "the known patch's container")
        we.checked(ctx.docker("cp", str(gold), f"{name}:/tmp/we-gold.patch", timeout=300), "the fix's copy")
        we.checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we-gold.patch", workdir=task.repo, timeout=300),
                   "the fix applied")
        we.checked(ctx.docker.sh(name, we.diff_script(task.repo, task.parent, f"{we.WE}/known.patch"), workdir=task.repo,
                                 timeout=300), "the known patch's capture")       # as the repository's own user, as every capture
        if not we.copy_out(ctx.docker, name, f"{we.WE}/known.patch", dest):
            raise RuntimeError("the known patch could not be copied out")
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    if not dest.read_bytes():
        raise RuntimeError("the known patch is empty: the fix, applied, made no change the capture sees")
    return dest.read_bytes()


def scripted_starter(known: Path) -> Callable[..., Any]:
    """The host session's starter for the round trip: the scripted session (``scripted_session.py``) in Claude Code's place, with
    Claude Code's own command line after ``--known``, started as the host executor starts Claude Code (``run.host_start``)."""
    script = HERE / "scripted_session.py"

    def start(argv: Sequence[str], **kw: Any) -> Any:
        return subprocess.Popen([sys.executable, "-I", str(script), "--known", str(known), *list(argv)[1:]], **kw)
    return start


def round_trip(ctx: we.Context, task: we.Task, gold: Path, folder: c.RunFolder, upstream_tree: str) -> dict[str, Any]:
    """The fourth control: a known patch (``known_patch``) written through the host executor's runner comes back byte for byte in
    the kept patch. One run of round 3's arm A on the host executor (``run._run_task``), its session scripted with no model
    (``scripted_session``): the run's container, its tree checked against upstream's (``upstream_tree``), the workspace exported,
    the known patch written there and applied by a command inside the box, the runner's own mirrors, settle and capture. Holds
    when the run finished and its ``final.patch`` is the known patch's bytes, and the known patch holds a change."""
    known = known_patch(ctx, task, gold, folder.fresh_path("known.patch"))
    host = we.Context(archive=ctx.archive, docker=ctx.docker, key_reader=lambda: "",      # the host executor reads no key
                      issue_reader=lambda t: ("the replay cohort's round-trip control",
                                              "No issue is read: a scripted session applies a known patch."),
                      tree_reader=lambda t: upstream_tree, g6=ctx.g6, variant=we.ROUND3, arm="A", model=we.R3_MODELS[1],
                      executor="host", starter=scripted_starter(folder.path / "known.patch"))
    record = we._run_task(host, task, dry=False, cap=0.0, stop=0.0)
    kept = ctx.archive.root / str(record.get("run")) / "final.patch"
    got = kept.read_bytes() if kept.is_file() else None
    same = got == known
    return {"holds": bool(known) and record.get("status") == "finished" and same, "run": record.get("run"),
            "status": record.get("status"), "error": record.get("error"), "known_bytes": len(known),
            "known_sha256": hashlib.sha256(known).hexdigest(), "kept_bytes": None if got is None else len(got),
            "kept_sha256": None if got is None else hashlib.sha256(got).hexdigest(), "same_bytes": same}


def controls(ctx: we.Context, task: we.Task, study: c.Archive) -> dict[str, Any]:
    """The four controls of one bug, in order, each from the step before it: ``run.validate`` (the base fails the maintainers'
    tests: some fail on the parent's code and pass with the fix, the lists every check scores against), ``run.selfcheck`` (the
    maintainers' fix, given as a patch, passes every test on those lists; an empty patch is seen failing), then ``round_trip``
    on selfcheck's own copy of the fix. Returns each result, which control failed first ("" when none did) and why, with the
    records' run names."""
    out: dict[str, Any] = {"failed": "", "why": ""}
    try:
        we.validate(ctx, task)
        v = we.latest(ctx.archive, "validate", task.id) or {}
        rec = v.get("record") or {}
        fixed = (rec.get("fixed") or {}).get("results") or {}
        out["validate"] = {"run": v.get("run"), "ok": rec.get("ok"), "fail_to_pass": len(rec.get("fail_to_pass") or []),
                           "pass_to_pass": len(rec.get("pass_to_pass") or []), "oracle_image_id": rec.get("oracle_image_id"),
                           "base_fails": bool(rec.get("fail_to_pass")),
                           "fixed_not_passed": sum(status != "passed" for status in fixed.values())}
        if not out["validate"]["base_fails"]:
            out.update(failed="validate", why=rec.get("why") or "no test fails on the parent's code and passes with the fix")
            return out
        we.selfcheck(ctx, task)
        s = we.latest(ctx.archive, "selfcheck", task.id) or {}
        srec = s.get("record") or {}
        out["selfcheck"] = {"run": s.get("run"), "ok": srec.get("ok"), "gold": srec.get("gold"), "empty": srec.get("empty")}
        if not (srec.get("gold") or {}).get("passes"):
            out.update(failed="selfcheck", why="the maintainers' fix, given as a patch, does not pass the maintainers' tests")
            return out
        if (srec.get("empty") or {}).get("passes") is not False:
            out.update(failed="selfcheck", why="an empty patch was not seen failing the maintainers' tests")
            return out
        tree = recorded_tree(study, task.id)
        if not tree:
            out.update(failed="round trip", why="no earlier run recorded upstream's tree for the base commit")
            return out
        folder = ctx.archive.start("roundtrip", task.id, g6_commit=ctx.g6.get("commit"))
        try:
            out["round_trip"] = round_trip(ctx, task, ctx.archive.root / str(s["run"]) / "gold.patch", folder, tree)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            out["round_trip"] = {"holds": False, "status": None, "error": f"{type(exc).__name__}: {exc}"[:400]}
        folder.finish(out["round_trip"], "finished")
        got = out["round_trip"]
        if not got["holds"]:
            out.update(failed="round trip", why=("the kept patch is not the known patch, byte for byte" if got["status"] == "finished"
                                                 else got.get("error") or f"the run ended {got['status']}")[:400])
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError, SystemExit) as exc:
        out.update(failed=out["failed"] or "a control could not run", why=f"{type(exc).__name__}: {exc}"[:400])
    return out


# --- the manifest ---------------------------------------------------------------------------------------------------------------
def digest(manifest: Mapping[str, Any]) -> str:
    """The manifest's own digest: SHA-256 of its JSON with sorted keys and no spaces, every field but ``digest``."""
    body = {k: v for k, v in manifest.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def holds(manifest: Mapping[str, Any]) -> bool:
    """Whether a manifest is the one its digest names: any change to its bugs, digests, controls or anything else changes it."""
    return manifest.get("digest") == digest(manifest)


def manifest(name: str, images: Mapping[str, Mapping[str, Any]], results: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """A cohort's manifest: every bug its source names, each with its image names and ids, and, for a controlled cohort, its
    control results; the bugs left out before the controls, and the bugs a control excluded, each with why; the usable bugs;
    the sources; and its digest (``digest``). A controlled cohort's bug with no control result, or no image, is excluded and
    says so; a final-cohort bug is listed with its images only, and is usable when docker holds both."""
    names = cohort(name)
    controlled = name in CONTROLLED
    bugs, excluded = [], []
    for bug in names:
        entry: dict[str, Any] = {"id": bug, **{k: v for k, v in (images.get(bug) or {}).items()}}
        if name == "final" and bug in FINAL_SEEN:
            entry["seen_by_builder"] = FINAL_SEEN[bug]
        why = ""
        if not (entry.get("task_image_id") and entry.get("oracle_image_id")):
            why = "docker holds no task or oracle image for it"
        if controlled:
            got = (results or {}).get(bug)
            entry["controls"] = dict(got) if got else None
            if not why and not got:
                why = "no control result"
            elif not why and got.get("failed"):
                why = f"{got['failed']}: {got.get('why') or ''}".strip()
            elif not why and (got.get("validate") or {}).get("oracle_image_id") != entry.get("oracle_image_id"):
                why = "the controls ran on another oracle image than docker holds now"
        entry["usable"] = not why
        bugs.append(entry)
        if why:
            excluded.append({"id": bug, "why": why})
    body = {"cohort": name, "plan": "validation/worked_examples/PLAN.md, 2026-11-06 (night)",
            "design": "docs/superpowers/specs/2026-11-06-explicit-models-one-question-design.md, sections 3.6 and 7.1",
            "g6_commit": G6_COMMIT, "variant": VARIANT, "sources": SOURCES[name],
            "left_out": [{"id": bug, "why": why} for bug, why in LEFT_OUT.get(name, {}).items()],
            "controls": (["the base fails the maintainers' tests (validate)", "the maintainers' fix passes them (selfcheck, as a "
                          "patch)", "an empty patch fails them (selfcheck)", "a known patch written through the host executor's "
                          "runner comes back byte for byte in the kept patch (round trip)"] if controlled else
                         "none here: the final cohort's issues, tests and fixes stay unread"),
            "bugs": bugs, "excluded": excluded, "usable": [b["id"] for b in bugs if b["usable"]]}
    return {**body, "digest": digest(body)}


def latest_controls(archive: c.Archive) -> dict[str, dict[str, Any]]:
    """Each bug's newest finished control record in the cohort archive (``control`` runs), by bug."""
    found: dict[str, dict[str, Any]] = {}
    for row in archive.rows():
        if row.get("event") == "ended" and str(row.get("run", "")).startswith("control/") and row.get("status") == "finished":
            bug = str(row["run"]).split("/")[1]
            found[bug] = json.loads((archive.root / row["run"] / "run.json").read_text("utf-8"))
    return {bug: {k: v for k, v in rec.items() if k not in ("status", "ended_at")} for bug, rec in found.items()}


def write_manifest(root: Path, body: Mapping[str, Any]) -> Path:
    """The manifest written once, as ``<cohort>-<digest's first 12>.json`` in ``root``; never over a file that is there."""
    if not holds(body):
        raise ValueError("the manifest's digest is not its own")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{body['cohort']}-{body['digest'][:12]}.json"
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("sizes", "controls", "manifest"))
    ap.add_argument("--cohort", choices=tuple(COHORTS))
    ap.add_argument("--only", default="", help="controls: one bug, or several, comma-separated")
    ap.add_argument("--archive", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.command == "sizes":
        print(json.dumps({name: len(bugs) for name, bugs in COHORTS.items()} | {"left_out": LEFT_OUT}, indent=1))
        return 0
    root = controls_root(args.archive)
    archive = c.Archive(root / "controls")
    docker = Floored()
    if args.command == "controls":
        if args.cohort not in CONTROLLED:
            raise SystemExit("controls run only for the development and selection cohorts")
        study = c.Archive(c.archive_root(args.archive) / "round3")
        ctx = we.Context(archive=archive, docker=docker, key_reader=lambda: "", issue_reader=lambda t: ("", ""), g6=g6_for(),
                         variant=VARIANT)
        done = latest_controls(archive)
        for bug in [b for b in cohort(args.cohort) if not args.only or b in args.only.split(",")]:
            ids = image_ids(bug, docker)
            if (bug in done and not done[bug].get("failed")
                    and (done[bug].get("validate") or {}).get("oracle_image_id") == ids["oracle_image_id"]):
                print(json.dumps({"bug": bug, "kept": "a finished control record on this oracle image"}), flush=True)
                continue
            folder = archive.start("control", bug, cohort=args.cohort, g6_commit=G6_COMMIT)
            got = controls(ctx, we.task_by_id(bug), study) if ids["task_image_id"] and ids["oracle_image_id"] else {
                "failed": "images", "why": "docker holds no task or oracle image for it"}
            folder.finish({**got, "images": ids, "waited_s": docker.waited}, "finished")
            print(json.dumps({"bug": bug, "failed": got["failed"], "why": got["why"]}), flush=True)
        return 0
    if args.command == "manifest":
        name = args.cohort or ap.error("manifest needs --cohort")
        images = {bug: image_ids(bug, docker) for bug in cohort(name)}
        body = manifest(name, images, latest_controls(archive) if name in CONTROLLED else None)
        path = write_manifest(root, body)
        print(json.dumps({"cohort": name, "path": path.name, "digest": body["digest"], "bugs": len(body["bugs"]),
                          "usable": len(body["usable"]), "excluded": body["excluded"]}, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
