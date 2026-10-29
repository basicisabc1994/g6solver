# SPDX-License-Identifier: Apache-2.0
"""V10's arm R, a fresh review by a coding agent that did not write the patch (docs/validation/patch_review_search/
PROTOCOL.md, version 2: "Arms", "Arm R in detail", "Findings", "Safeguards"). Modelled on V9's arm 2 script
(``patch_review_utility.self_review``): this script **never starts a model or an agent session**. The owner, or the
study's runner under this study's cap, launches each session; this script prepares it and judges what it left.

- ``prepare``: the frozen review prompt (V9's arm 2 prompt with exactly the two registered changes,
  ``PROMPT_CHANGES``) with the review contract; the diff; V9's arm 2 caps (its wall-clock limit and token cap); the
  network rule (``NETWORK``); the frozen session set-up (``SESSION``); and the folder for R's tests (``FOLDER``).
- ``make_copy``: the scratch copy the session reviews in: the base commit only (one commit fetched, with no history
  beyond it, checked by ``history_problem``), with the patch applied to the working tree and not committed.
- ``disposition``: V9's arm 2 rule, ``self_review.disposition``, unchanged: a session past its limits abstains, and is
  not a failure of feasibility criterion 2.
- ``collect`` and ``judge``: the files in ``FOLDER`` are collected before the copy is discarded; at most 20 test
  functions are kept in file order and the rest counted, as for arm E (``arm_e.keep``); each kept test runs n times on
  the patched tree and n times at the base, with V9's arm 1 command, and its findings are read by arm E's rule
  (``arm_e.judged``).
- ``cost_cents``: the tokens the session reports, priced by the frozen price list, which must include R's model.
- ``scan_session``: the gaming scan of R's whole transcript: V9's scan (vault reads, network use from a command,
  shortcuts), every git command it ran listed, its session set-up checked, and edits outside its copy flagged.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from patch_review_search import arm_e
from patch_review_utility import contract as review_contract
from patch_review_utility import gaming, self_review, trees

#: Where R writes its tests: a folder of the form G6 gives a free-form check's own folder (``g6_check_`` and a name,
#: ``verification_search.FOLDER``), at the copy's top, which no repository holds. Its kept files are placed as arm
#: E's are (``arm_e.placement``) when they run.
FOLDER = "g6_check_review"
#: The registered changes to V9's arm 2 prompt (version 2, "Arm R in detail"), as (V9's text, R's text).
PROMPT_CHANGES = (
    ("You wrote the patch that is now in this repository's working tree.",
     "Another agent wrote the patch that is now in this repository's working tree."),
    ("You may read the code and run the repository's tests. Any change you make while reviewing is discarded.",
     "You may read the code and run the repository's tests. For each defect you find, write a pytest test that fails "
     f"on the patch because of that defect, as a file named test_<name>.py in the folder {FOLDER} at the top of this "
     "repository. Only the tests in that folder are kept, as evidence, and they are never applied to the patch; every "
     "other change you make while reviewing is discarded."),
)


def _changed(prompt: str) -> str:
    for old, new in PROMPT_CHANGES:
        if prompt.count(old) != 1:
            raise ValueError(f"V9's arm 2 prompt no longer holds, once, the text R's prompt changes: {old!r}")
        prompt = prompt.replace(old, new)
    return prompt


REVIEW_PROMPT = _changed(self_review.REVIEW_PROMPT)
#: The network rule: the commands the session runs have none.
NETWORK = ("the commands the session runs have no network; the session's own connection to the model provider is the "
           "only network it uses")
#: The frozen session set-up, checked by the gaming scan.
SESSION = {"tool_servers": [], "plugins": [], "skills": [], "hooks": [], "memory": "none saved",
           "instruction_files": "only those the copy itself holds"}
EDITS = ("discarded with the copy, except the tests in " + FOLDER + ", kept as evidence and never applied to the patch")
_GIT = ("git", "-c", "core.autocrlf=false", "-c", "advice.detachedHead=false")
_GIT_COMMAND = re.compile(r"(?:^|[;&|]\s*|\s)git\s")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(task_id: str, requirements: list[dict], *, patch: str, time_limit_s: int, token_cap: int) -> dict:
    """What the owner's or the runner's session needs: the prompt, the diff, V9's arm 2 caps, the network rule, the
    session set-up and the tests' folder. ``self_review.disposition`` reads the caps and the patch's digest."""
    if time_limit_s <= 0 or token_cap <= 0:
        raise ValueError("arm R needs V9's arm 2 time limit and token cap, frozen in the entry")
    return {"task_id": task_id, "prompt": REVIEW_PROMPT.format(contract=review_contract.render(requirements)),
            "prompt_sha256": _sha256(REVIEW_PROMPT), "patch_sha256": _sha256(patch), "diff": patch,
            "caps": {"time_limit_s": time_limit_s, "token_cap": token_cap, "network": NETWORK,
                     "tools": "the tools V9's agent had while writing the patch, inside its scratch copy",
                     "edits": EDITS},
            "session": dict(SESSION), "tests_folder": FOLDER}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=trees.GIT_TIMEOUT_S).stdout


def history_problem(copy: Path, base_commit: str) -> str:
    """Why ``copy`` holds history beyond ``base_commit`` ("" when it holds none): ``HEAD`` is the base commit, every
    reference names it, and the only commit object in the copy's store is the base commit."""
    head = _git(copy, "rev-parse", "HEAD").strip()
    if head != base_commit:
        return f"HEAD is {head}, not the base commit"
    commits = [line.split()[1] for line in _git(copy, "cat-file", "--batch-all-objects",
                                                 "--batch-check=%(objecttype) %(objectname)").splitlines()
               if line.startswith("commit ")]
    others = sorted(set(commits) - {base_commit})
    if others:
        return f"the copy holds {len(others)} commit(s) besides the base commit"
    refs = {line.strip() for line in _git(copy, "for-each-ref", "--format=%(objectname)").splitlines() if line.strip()}
    if refs - {base_commit}:
        return "a reference names something other than the base commit"
    return ""


def make_copy(source: str | Path, base_commit: str, patch: str, dest: str | Path) -> Path:
    """R's scratch copy in ``dest`` (which must not exist): the base commit only, fetched alone from ``source``, with
    ``patch`` applied to the working tree and not committed. A copy with history beyond the base commit, or a patch
    that does not apply, is refused."""
    dest = Path(dest)
    dest.mkdir(parents=True)
    _git(dest, "init", "-q")
    _git(dest, "fetch", "-q", "--depth=1", "--no-tags", Path(source).resolve().as_uri(), base_commit)
    _git(dest, "checkout", "-q", "--detach", base_commit)
    problem = history_problem(dest, base_commit)
    if problem:
        raise ValueError(f"R's copy holds history beyond the base commit: {problem}")
    if trees.apply(dest, patch) != "non_empty":
        raise ValueError("the patch does not apply to R's copy, or changes nothing")
    return dest


#: V9's arm 2 rule, unchanged.
disposition = self_review.disposition


def collect(copy: Path) -> list[dict]:
    """Every file in R's tests' folder, in path order, as ``{path, text, call}`` (its path inside the folder), before
    the copy is discarded. ``arm_e.keep`` then keeps the test files G6 would take and counts the rest."""
    folder = Path(copy) / FOLDER
    if not folder.is_dir():
        return []
    found = []
    # the bytecode of running R's tests is skipped outright, so there is nothing to discard and count (the coordinator,
    # 2026-10-29)
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix != ".pyc"
                       and "__pycache__" not in p.relative_to(folder).parts):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        found.append({"path": path.relative_to(folder).as_posix(), "text": text, "call": 1})
    return found


def judge(pool: list[dict], *, executor, patched_tree: Path, base_tree: Path, runs: int, run_s: float) -> dict:
    """R's tests, kept as arm E's (at most 20 test functions in file order), run ``runs`` times on the patched tree
    and at the base, each run within ``run_s``, and judged by arm E's rule: the kept and discarded tests, each test's
    outcomes, the findings and the counts."""
    kept, discarded = arm_e.keep(patched_tree, pool, also=(base_tree,))

    def timeout() -> float:
        return run_s

    on_patch = arm_e.run_tests(executor, patched_tree, kept, runs=runs, timeout=timeout)
    at_base = arm_e.run_tests(executor, base_tree, kept, runs=runs, timeout=timeout)
    tests, findings = arm_e.judged(kept, on_patch, at_base, runs)
    return {"kept": [{key: one[key] for key in ("file", "placed", "sha256", "tests", "text")} for one in kept],
            "discarded": discarded, "tests": tests, "findings": findings,
            "counts": {"files": len(pool), "kept_files": len(kept), "kept_tests": len(tests),
                       "discarded": len(discarded), "errors": sum(t["error"] for t in tests.values()),
                       "findings": len(findings)}}


def tokens(record: dict) -> dict | None:
    """The tokens the session reports, by kind (measure 6), or None when it reports none."""
    usage = record.get("usage")
    return {str(kind): int(count) for kind, count in usage.items()} if isinstance(usage, dict) and usage else None


def cost_cents(record: dict, prices: dict) -> float | None:
    """The session's cost in cents: each kind of token its record reports (``usage``), priced in dollars per million
    tokens by the frozen price list for its model. None when the record reports no tokens; a model or a kind of token
    the price list does not price is refused."""
    model = record.get("model")
    if model not in prices:
        raise ValueError(f"the frozen price list does not price R's model ({model!r})")
    usage = record.get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    unpriced = sorted(set(usage) - set(prices[model]))
    if unpriced:
        raise ValueError("the frozen price list does not price these tokens: " + ", ".join(unpriced))
    return sum(int(count) * float(prices[model][kind]) / 1_000_000 for kind, count in usage.items()) * 100


def _inside(path: str, root: Path) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        return ".." not in candidate.parts
    try:
        candidate.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def scan_session(transcript: str, *, source: str, copy_root: Path, vault_markers: list[str]) -> dict:
    """The gaming scan of R's whole session transcript (JSON lines, as Claude Code writes it): V9's scan for vault
    reads, network use from a command and shortcuts (an edit to a test or runner file in R's own tests' folder is not
    one); every git command the session ran, listed; its set-up against ``SESSION`` (a tool server it connected to, or
    a tool one gives); and an edit outside its copy."""
    found = [f for f in gaming.scan_transcript(transcript, source=source, vault_markers=vault_markers, patch_files=[])
             if not (f["kind"] == "edit_outside_patch" and f"{FOLDER}/" in f["detail"].replace("\\", "/"))]
    commands = []
    for number, line in enumerate(transcript.splitlines(), start=1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("type") == "system" and record.get("subtype") == "init":
            servers = [s.get("name") if isinstance(s, dict) else s for s in record.get("mcp_servers") or []]
            if servers:
                found.append({"kind": "session_set_up", "source": source, "line": number,
                              "detail": "the session connected to tool servers: " + ", ".join(map(str, servers))})
            tools = [t for t in record.get("tools") or [] if str(t).startswith("mcp__")]
            if tools:
                found.append({"kind": "session_set_up", "source": source, "line": number,
                              "detail": "the session had tool servers' tools: " + ", ".join(map(str, tools))[:250]})
        for name, value in gaming._calls(record):
            said = gaming._flat(value)
            if name.lower() in gaming.SHELL_TOOLS and _GIT_COMMAND.search(" " + said):
                commands.append(said.strip())
            if name.lower() in gaming.EDIT_TOOLS:
                for path in gaming._edited(name.lower(), value):
                    if not _inside(path, copy_root):
                        found.append({"kind": "edit_outside_copy", "source": source, "line": number,
                                      "detail": f"{name} edits a file outside R's copy"})
    return {"git_commands": commands, "findings": found}
