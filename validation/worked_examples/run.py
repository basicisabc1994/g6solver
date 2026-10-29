# SPDX-License-Identifier: Apache-2.0
"""The worked examples' runner (PLAN.md sections 3 to 5): images, validation, the key check, one run in the product's
real flow, and the independent check. Every step writes a write-once folder in the archive (``wecommon.Archive``),
outside the repository.

Commands (``python -I validation/worked_examples/run.py <command>``, from the repository's root):

* ``base [--ref origin/claude/launch]``: G6's base image from that ref's commit: G6 as the README installs it
  (``python -m venv --copies``, ``pip install "<source>[all]"``) and Claude Code 2.1.309, on ``g6-a2-linux``.
* ``images --task <id> --g6 <commit>``: the task's image (the project at the parent commit, history-free, with its
  ``.venv``) and its oracle image (the same, plus the maintainers' fix kept apart in ``/oracle``).
* ``validate --task <id> --g6 <commit>``: FAIL_TO_PASS and PASS_TO_PASS, with no network and no model.
* ``freecheck --task <id> --g6 <commit>``: one confined review with the README install and a linked ``.venv``, no key.
* ``run --task <id> --g6 <commit>``: the key check, then Claude Code headless with the using-g6 skill (paid).
* ``check --task <id> [--run <folder>]``: the independent check of a run's final patch and each round's patch.
* ``replay --task <id> --code fix|<patch file> --test <test file>``: a reproduction run on the maintainers' fix or on a
  patch, for judging a finding.

Docker containers, images and networks this script makes are named ``g6we*``; it stops and removes only those it
started, by name. The key is read only through ``g6.core.keys`` and passed by name into the environment of the one
``docker exec`` that starts Claude Code (and of the key check's driver); every archived text is masked for it.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import selection  # noqa: E402
import wecommon as c  # noqa: E402

REPO_ROOT = HERE.parents[1]
TASKS = HERE / "tasks.json"
PROXY = REPO_ROOT / "validation" / "v7_swebench" / "egress_proxy.py"
FROM_IMAGE = "g6-a2-linux"
SECURITY = ("--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined")
LIMITS = ("--cpus", "4", "--memory", "8g")
USER = "tester"
HOME = "/home/tester"
WORK = f"{HOME}/work"
CLAUDE = "/usr/local/bin/claude"
CLAUDE_SHA256 = "4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658"
G6SRC = "/opt/g6src"
G6PY = f"{G6SRC}/.venv/bin/python3"
WE = "/opt/we"
PROXY_PORT = 3128
MODEL_HOSTS = ("api.anthropic.com:443",)
TURN_BACKSTOP = 2
BASE_DATE = "2026-01-01T00:00:00Z"

PROMPT = """You are working in a clone of {owner}/{name}, at the commit just before the maintainers fixed the issue below.
The project's development environment is the virtual environment in .venv (its Python is .venv/bin/python).
There is no network apart from the model.

Fix the issue. Deliver the fix the way the using-g6 skill says: write the requirements and their checks,
review the patch with g6_review_patch, show its report, and repair what G6 reproduces. Nobody is available to
answer questions during this run: where the skill says to ask the person, put the questions in your final
report and carry on with the most likely reading. Leave your changes uncommitted in the working tree. End with
your final report.

Issue #{number}: {title}

{text}
"""


# --- tasks ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Task:
    id: str
    project: str
    full_name: str
    licence: str
    pr: int
    issue: int
    merge_commit: str
    parent: str
    merged_at: str
    test_modules: tuple[str, ...]
    install: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    @property
    def repo(self) -> str:
        return f"{WORK}/{self.name}"


def load_tasks(path: Path = TASKS) -> list[Task]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Task(**{**t, "test_modules": tuple(t["test_modules"]), "install": tuple(t["install"])})
            for t in data["tasks"]]


def task_by_id(task_id: str, path: Path = TASKS) -> Task:
    for task in load_tasks(path):
        if task.id == task_id:
            return task
    raise SystemExit(f"no task {task_id!r} in {path.name}")


def prompt(task: Task, title: str, text: str) -> str:
    owner, name = task.project.split("/", 1)
    return PROMPT.format(owner=owner, name=name, number=task.issue, title=title, text=text)


# --- docker ---------------------------------------------------------------------------------------------------------------
class Docker:
    """The docker CLI through ``subprocess`` (list arguments, no shell)."""

    def __init__(self, exe: str = "docker", runner: Callable[..., Any] = subprocess.run) -> None:
        self.exe, self.runner = exe, runner

    def __call__(self, *args: str, input: bytes | None = None, timeout: float | None = None,
                 env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
        return self.runner([self.exe, *args], input=input, capture_output=True, timeout=timeout,
                           env=dict(env) if env is not None else None)

    def sh(self, ctr: str, script: str, *args: str, timeout: float = 600, user: str = USER,
           workdir: str | None = None) -> subprocess.CompletedProcess:
        extra = ["-w", workdir] if workdir else []
        return self("exec", "-u", user, "-e", f"HOME={HOME}", *extra, ctr, "sh", "-c", script, "we", *args,
                    timeout=timeout)

    def popen(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        return subprocess.Popen([self.exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env) if env is not None else None)


def out(cp: Any) -> str:
    return (cp.stdout or b"").decode("utf-8", errors="replace").strip()


def err(cp: Any) -> str:
    return (cp.stderr or b"").decode("utf-8", errors="replace").strip()


def checked(cp: Any, what: str) -> str:
    if cp.returncode != 0:
        raise RuntimeError(f"{what} failed ({cp.returncode}): {(out(cp) + ' ' + err(cp))[-600:]}")
    return out(cp)


def write_in(docker: Docker, ctr: str, dest: str, text: str, *, user: str = USER, mode: str = "644") -> None:
    checked(docker("exec", "-i", "-u", user, ctr, "sh", "-c",
                   f'mkdir -p "$(dirname "{dest}")" && cat > "{dest}" && chmod {mode} "{dest}"',
                   input=text.encode("utf-8")), f"write {dest}")


# --- images ---------------------------------------------------------------------------------------------------------------
def base_dockerfile() -> str:
    """G6 as the README installs it at the commit run (PLAN.md section 9): in G6's source folder, its own environment
    ``.venv`` made with ``python -m venv --copies``, then ``pip install ".[all]"`` there (``g6`` on the path stands
    in for activating it); Claude Code 2.1.309; on ``g6-a2-linux``."""
    return f"""FROM {FROM_IMAGE}
COPY claude {CLAUDE}
COPY g6src.tar /tmp/g6src.tar
RUN chmod 755 {CLAUDE} \\
 && mkdir -p {G6SRC} && tar -xf /tmp/g6src.tar -C {G6SRC} && rm /tmp/g6src.tar \\
 && cd {G6SRC} && python3 -m venv --copies .venv \\
 && {G6SRC}/.venv/bin/pip install -q --no-cache-dir ".[all]" \\
 && ln -s {G6SRC}/.venv/bin/g6 /usr/local/bin/g6 \\
 && mkdir -p {WE}/rounds && chown -R 1000:1000 {WE} \\
 && {G6PY} -c "import g6; print(g6.__version__)"
"""


def task_dockerfile(task: Task, base_image: str) -> str:
    """The project at the parent commit as a new repository with that tree as its only commit (the fetch stage's
    history never reaches the final image), and its ``.venv``, made the project's normal way, with links. Its
    dependencies are the versions published by the time the fix was merged (``uv``'s exclude-newer), so a later
    release cannot break the project's own tests; the installer is removed from the image afterwards."""
    env = " ".join(f"{k}={v}" for k, v in sorted({**task.env, "UV_EXCLUDE_NEWER": task.merged_at,
                                                  "UV_NO_CACHE": "1"}.items()))
    install = " && ".join(f"{env} {cmd}" for cmd in task.install)
    return f"""FROM {FROM_IMAGE} AS fetch
RUN git clone -q --filter=blob:none --no-checkout https://github.com/{task.full_name}.git /src \\
 && git -C /src -c advice.detachedHead=false checkout -q {task.parent} \\
 && rm -rf /src/.git
FROM {base_image}
COPY --from=fetch --chown=1000:1000 /src {task.repo}
USER {USER}
ENV HOME={HOME}
WORKDIR {task.repo}
RUN git -c init.defaultBranch=main init -q \\
 && git add -A \\
 && GIT_AUTHOR_DATE={BASE_DATE} GIT_COMMITTER_DATE={BASE_DATE} git -c user.name="worked example" \\
    -c user.email=base@example.invalid -c commit.gpgsign=false commit -q -m "base: {task.full_name} at {task.parent}" \\
 && printf '/.venv/\\n' >> .git/info/exclude \\
 && python3 -m venv .venv \\
 && python3 -m venv /tmp/we-uv && /tmp/we-uv/bin/pip install -q --no-cache-dir uv \\
 && export PATH="/tmp/we-uv/bin:$PATH" \\
 && {install} \\
 && rm -rf /tmp/we-uv \\
 && git status --porcelain | sed -n 's|^?? |/|p' >> .git/info/exclude \\
 && test -z "$(git status --porcelain)"
"""


def oracle_dockerfile(task: Task, task_image: str) -> str:
    """The task's image plus the maintainers' fix, kept apart in ``/oracle`` (the tree at the merge commit, the diff
    from the parent and its file list). Used only by the validation and the checks, never by the agent."""
    return f"""FROM {FROM_IMAGE} AS fetch
RUN git clone -q --filter=blob:none --no-checkout https://github.com/{task.full_name}.git /src \\
 && git -C /src -c advice.detachedHead=false checkout -q {task.merge_commit} \\
 && git -C /src diff --binary --no-renames {task.parent} {task.merge_commit} > /fix.patch \\
 && git -C /src diff --name-status --no-renames {task.parent} {task.merge_commit} > /fix.files \\
 && rm -rf /src/.git
FROM {task_image}
USER root
COPY --from=fetch /src /oracle/fixed
COPY --from=fetch /fix.patch /fix.files /oracle/
RUN chmod -R a+rX /oracle
USER {USER}
"""


def image_names(task: Task, g6_commit: str) -> tuple[str, str]:
    short = g6_commit[:12]
    return f"g6we-task:{task.id}-{short}", f"g6we-oracle:{task.id}-{short}"


# --- the hook that saves each round's patch, and the patch capture -------------------------------------------------------
def diff_script(repo: str, base: str, dest: str) -> str:
    """The working tree's diff against ``base``, new files included, through a separate index: the repository's own
    index is never touched."""
    return (f'idx=$(mktemp) && cd "{repo}" && GIT_INDEX_FILE="$idx" git read-tree {base} >/dev/null 2>&1; '
            f'GIT_INDEX_FILE="$idx" git add -A >/dev/null 2>&1; '
            f'GIT_INDEX_FILE="$idx" git diff --cached --binary {base} > "{dest}" 2>/dev/null; rm -f "$idx"')


def hook_script(repo: str, base: str) -> str:
    return f"""#!/bin/sh
# Worked examples (PLAN.md section 4): before each g6_review_patch call, the call's input and the working tree's diff
# against the base commit are saved. Nothing is printed, so the agent is told nothing.
n=$(ls {WE}/rounds 2>/dev/null | grep -c '\\.patch$')
n=$((n + 1))
cat > {WE}/rounds/round-$n.input.json 2>/dev/null
{diff_script(repo, base, WE + "/rounds/round-$n.patch")}
exit 0
"""


def hook_settings() -> str:
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "mcp__g6__g6_review_patch", "hooks": [
        {"type": "command", "command": f"{WE}/round_hook.sh", "timeout": 120}]}]}}, indent=1)


# --- tests: maintainers' test files, junit, FAIL_TO_PASS -----------------------------------------------------------------
def test_changes(name_status: str) -> list[tuple[str, str]]:
    """The fix's changes to test files (PLAN.md section 2's T3 definition), from ``git diff --name-status``."""
    rows = []
    for line in name_status.splitlines():
        status, _, path = line.partition("\t")
        if path and selection.is_test(path):
            rows.append((status.strip()[:1], path.strip()))
    return rows


APPLY_TESTS = """set -e
cd "$1"; shift
while [ $# -gt 1 ]; do st=$1; p=$2; shift 2
  case "$st" in D) rm -f -- "$p" ;; *) mkdir -p -- "$(dirname -- "$p")"; cp -- "/oracle/fixed/$p" "$p" ;; esac
done"""
JUNIT_IN = "/tmp/we-junit.xml"


def pytest_script(modules: Sequence[str], timeout_s: int = 1800) -> str:
    files = " ".join(f'"{m}"' for m in modules)
    return (f'rm -f {JUNIT_IN}; timeout {timeout_s} .venv/bin/python -m pytest -p no:cacheprovider -q -rN '
            f'--junitxml={JUNIT_IN} -o junit_family=xunit2 {files} > /tmp/we-pytest.txt 2>&1; echo "exit=$?"')


def parse_junit(text: str) -> dict[str, str]:
    """Each test case's outcome (passed, failed, error or skipped), keyed ``classname::name``; the worst one when a
    case appears twice."""
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    found: dict[str, str] = {}
    for case in ET.fromstring(text).iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        outcome = ("error" if "error" in tags else "failed" if "failure" in tags else
                   "skipped" if "skipped" in tags else "passed")
        if rank[outcome] >= rank.get(found.get(key, "passed"), 0) or key not in found:
            found[key] = outcome
    return found


def fail_to_pass(base: Mapping[str, str], fixed: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """FAIL_TO_PASS: failing (or absent) with the maintainers' tests on the parent's code, passing with their fix.
    PASS_TO_PASS: passing in both (PLAN.md section 3)."""
    f2p = sorted(k for k, v in fixed.items() if v == "passed" and base.get(k) != "passed" and base.get(k) != "skipped")
    p2p = sorted(k for k, v in fixed.items() if v == "passed" and base.get(k) == "passed")
    return f2p, p2p


def verdict(results: Mapping[str, str], f2p: Sequence[str], p2p: Sequence[str]) -> dict[str, Any]:
    """The independent check (PLAN.md section 5): every FAIL_TO_PASS and PASS_TO_PASS test passes."""
    failing = sorted(k for k in [*f2p, *p2p] if results.get(k) != "passed")
    return {"passes": bool(f2p) and not failing, "failing": failing,
            "fail_to_pass_failing": sorted(k for k in f2p if results.get(k) != "passed"),
            "pass_to_pass_failing": sorted(k for k in p2p if results.get(k) != "passed")}


# --- the key check -------------------------------------------------------------------------------------------------------
KEYPROBE_TEST = '''import glob
import os

PATHS = ("~/.g6/.env", "/home/tester/.g6/.env", "/root/.g6/.env")


def seen():
    found = set()
    if "ANTHROPIC_API_KEY" in os.environ:
        found.add("own_env_name")
    if any(value.startswith("sk-ant-") for value in os.environ.values()):
        found.add("own_env_value")
    for path in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(path, "rb") as stream:
                data = stream.read()
        except OSError:
            continue
        if b"ANTHROPIC_API_KEY=" in data:
            found.add("proc_env_name")
        if b"sk-ant-" in data:
            found.add("proc_env_value")
    for path in PATHS:
        if os.path.exists(os.path.expanduser(path)):
            found.add("key_file")
    return sorted(found)


def test_no_key_visible():
    channels = seen()
    assert not channels, "seen: " + ",".join(channels)
'''


def keyprobe_driver() -> str:
    """Run with G6's Python in a process that holds the key in its environment, as Claude Code does: a scratch
    repository with an ordinary (linked) ``.venv`` whose one test looks for the key; G6 reviews it confined, then the
    same test runs unconfined as the control. Prints names and outcomes only, never a value."""
    return "PROBE = " + json.dumps(KEYPROBE_TEST) + '''
import json, os, re, shutil, subprocess, sys, pathlib
root = pathlib.Path("/home/tester/keyprobe/repo")
shutil.rmtree(root.parent, ignore_errors=True)
root.mkdir(parents=True)
(root / "mod.py").write_text("def f():\\n    return 1\\n")
def git(*a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
git("init", "-q"); git("add", "."); git("-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "commit", "-qm", "base")
(root / "mod.py").write_text("def f():\\n    return 2\\n")
(root / "test_keyprobe.py").write_text(PROBE)
subprocess.run(["/usr/local/bin/python3", "-m", "venv", "--system-site-packages", str(root / ".venv")], check=True)
(root / ".git" / "info" / "exclude").write_text("/.venv/\\n")
from g6.harness.patch_review import review_patch
got = review_patch(root, task="key check", requirements=[{"id": "R1", "text": "a confined test cannot read the key",
                   "checks": ["test_keyprobe.py::test_no_key_visible"]}], timeout_s=600)
a = got.get("assessment") or {}
status = {r.get("id"): r.get("status") for r in a.get("requirements") or []}
report = a.get("report") or ""
backend = "bubblewrap" if "isolation `bubblewrap`" in report else None
done = subprocess.run([str(root / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "test_keyprobe.py"], cwd=root, capture_output=True, text=True)
said = re.findall(r"AssertionError: seen: ([a-z_,]+)", done.stdout + done.stderr)
known = {"own_env_name", "own_env_value", "proc_env_name", "proc_env_value", "key_file"}
channels = sorted(set(",".join(said).split(",")) & known) if said else []   # pytest's summary line cuts names short
print(json.dumps({"confined": {"R1": status.get("R1"), "outcome": a.get("outcome"), "backend": backend,
                               "code": (got.get("warning_card") or {}).get("code")},
                  "control": {"exit": done.returncode, "channels": [x for x in channels if x]}}))
'''


def key_check_ok(found: Mapping[str, Any]) -> bool:
    """The confined test passed under bubblewrap, and the unconfined control saw the key by name."""
    confined, control = found.get("confined") or {}, found.get("control") or {}
    return (confined.get("R1") == "passed_on_stated_checks" and confined.get("backend") == "bubblewrap"
            and control.get("exit") not in (0, None) and "own_env_name" in (control.get("channels") or []))


# --- the rounds, read from the transcript ---------------------------------------------------------------------------------
def _reports(obj: Any) -> list[dict[str, Any]]:
    """Every dict under ``obj`` with a ``report`` and an ``outcome`` (G6's assessment), outermost first."""
    found = []
    if isinstance(obj, dict):
        if "report" in obj and "outcome" in obj:
            found.append(obj)
        for value in obj.values():
            found += _reports(value)
    elif isinstance(obj, list):
        for value in obj:
            found += _reports(value)
    return found


def _result_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


SAVED = re.compile(r"Output has been saved to (/\S+?\.txt)")


def archived_resolver(folder: Path) -> Callable[[str], str | None]:
    """Where a reply Claude Code saved to a file in the container is in the run's archive (``claude_projects``)."""
    def resolve(path: str) -> str | None:
        marker = "/.claude/projects/"
        if marker not in path:
            return None
        local = folder / "claude_projects" / path.split(marker, 1)[1]
        return local.read_text(encoding="utf-8", errors="replace") if local.is_file() else None
    return resolve


def extract_rounds(objects: Iterable[Mapping[str, Any]],
                   resolve: Callable[[str], str | None] | None = None) -> list[dict[str, Any]]:
    """Each ``g6_review_patch`` call is a round: its input, the polls of ``metaharness_job`` after it, and the last
    assessment G6 returned before the next round (outcome and report, verbatim). A reply too large for Claude Code's
    transcript is read from the file Claude Code saved it to, through ``resolve``."""
    rounds: list[dict[str, Any]] = []
    tools: dict[str, str] = {}
    for obj in objects:
        msg = obj.get("message") or {}
        for block in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools[str(block.get("id"))] = name
                if name.endswith("g6_review_patch"):
                    rounds.append({"round": len(rounds) + 1, "input": block.get("input"), "polls": 0,
                                   "outcome": None, "report": None})
                elif name.endswith("metaharness_job") and rounds:
                    rounds[-1]["polls"] += 1
            elif block.get("type") == "tool_result" and rounds:
                name = tools.get(str(block.get("tool_use_id")), "")
                if not name.startswith("mcp__g6__"):
                    continue
                text = _result_text(block)
                saved = SAVED.search(text)
                if saved and resolve is not None and (found_text := resolve(saved.group(1))) is not None:
                    text = found_text
                    rounds[-1]["saved_to_file"] = True
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                for found in _reports(parsed)[:1]:
                    rounds[-1].update(outcome=found.get("outcome"), report=found.get("report"), assessment=found)
                inner = parsed.get("result") if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict) \
                    else parsed
                if isinstance(inner, dict) and inner.get("error") and not rounds[-1].get("report"):
                    rounds[-1]["error"] = str(inner["error"])      # a review G6 refused, with its reason
    return rounds


# --- the stream ----------------------------------------------------------------------------------------------------------
def claude_args() -> list[str]:
    """PLAN.md section 4. The flag that takes a list goes last; the prompt comes on standard input."""
    return [CLAUDE, "-p", "--model", c.MODEL, "--output-format", "stream-json", "--verbose", "--max-turns",
            str(c.MAX_TURNS), "--max-budget-usd", f"{c.RUN_CAP_USD:.2f}", "--dangerously-skip-permissions",
            "--disallowedTools", "WebFetch", "WebSearch"]


def read_stream(lines: Iterable[bytes], tally: c.Tally, *, deadline: float, write: Callable[[str], None],
                clock: Callable[[], float] = time.monotonic) -> str:
    """Feed the stream to ``tally`` until it ends ("") or a cap is reached ("timeout", "cap_turns", "cap_cost")."""
    q: queue.Queue = queue.Queue()

    def pump() -> None:
        try:
            for line in lines:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        left = deadline - clock()
        if left <= 0:
            return "timeout"
        try:
            line = q.get(timeout=min(left, 1.0))
        except queue.Empty:
            continue
        if line is None:
            return ""
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        write(text)
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict):
            tally.feed(obj)
        if tally.model_calls > c.MAX_TURNS * TURN_BACKSTOP:
            return "cap_turns"
        if tally.cost_usd > c.RUN_STOP_USD:
            return "cap_cost"


def status_of(stop: str, tally: c.Tally) -> str:
    if stop:
        return stop
    if tally.result is None:
        return "error"
    sub = str(tally.result.get("subtype") or "")
    if "max_turns" in sub:
        return "cap_turns"
    if "budget" in sub:
        return "cap_cost"
    return "error" if tally.result.get("is_error") else "finished"


KILL_AGENT = (f'for p in /proc/[0-9]*; do [ "$(readlink $p/exe 2>/dev/null)" = {CLAUDE} ] && '
              'kill -9 "${p#/proc/}" 2>/dev/null; done; true')


# --- the run -------------------------------------------------------------------------------------------------------------
@dataclass
class Context:
    archive: c.Archive
    docker: Any
    key_reader: Callable[[], str]
    issue_reader: Callable[[Task], tuple[str, str]]
    g6: Mapping[str, Any]                 # {"commit", "image", "contains_required"}
    proxy_source: Path = PROXY
    environ: Mapping[str, str] | None = None

    def client_env(self, key: str | None = None) -> dict[str, str]:
        base = dict(self.environ) if self.environ is not None else {
            k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        return {**base, "ANTHROPIC_API_KEY": key} if key else base


def latest(archive: c.Archive, kind: str, task: str) -> dict[str, Any] | None:
    """The newest finished run of ``kind`` for ``task``, with its record."""
    ended = [r for r in archive.rows() if r.get("event") == "ended" and r.get("status") == "finished"
             and str(r.get("run", "")).startswith(f"{kind}/{task}/")]
    if not ended:
        return None
    rel = sorted(r["run"] for r in ended)[-1]
    return {"run": rel, "path": archive.root / rel,
            "record": json.loads((archive.root / rel / "run.json").read_text(encoding="utf-8"))}


def redact_tree(folder: Path, redact: c.Redactor) -> int:
    """Mask the key in every file under ``folder`` (files copied out of a container); returns how many changed."""
    changed = 0
    for path in folder.rglob("*") if folder.is_dir() else ():
        if not path.is_file():
            continue
        data = path.read_bytes()
        text = data.decode("utf-8", errors="surrogateescape")
        masked = redact(text)
        if masked != text:
            path.write_bytes(masked.encode("utf-8", errors="surrogateescape"))
            changed += 1
    return changed


def copy_out(docker: Any, ctr: str, src: str, dest: Path) -> bool:
    cp = docker("cp", f"{ctr}:{src}", str(dest), timeout=900)
    return cp.returncode == 0 and dest.exists()


def key_check(ctx: Context, task: Task, image: str, key: str, tag: str) -> dict[str, Any]:
    """PLAN.md section 4: in a container of its own from the run's image, with no network."""
    name = f"g6we-keyprobe-{tag}"
    try:
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", image, "tail", "-f", "/dev/null", timeout=600), "key check container")
        done = ctx.docker("exec", "-i", "-u", USER, "-e", f"HOME={HOME}", "-e", "ANTHROPIC_API_KEY", name, G6PY, "-I",
                          "-", input=keyprobe_driver().encode("utf-8"), env=ctx.client_env(key), timeout=1200)
        try:
            found = json.loads(out(done).splitlines()[-1])
        except (ValueError, IndexError):
            found = {"error": f"the driver printed nothing readable (exit {done.returncode})"}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    found["ok"] = key_check_ok(found)
    return found


def start_net(ctx: Context, tag: str) -> tuple[str, str]:
    net, px = f"g6wenet-{tag}", f"g6wepx-{tag}"
    checked(ctx.docker("network", "create", "--internal", "--label", "g6we=1", net), "network create")
    checked(ctx.docker("run", "-d", "--name", px, "--label", "g6we=1", FROM_IMAGE, "tail", "-f", "/dev/null",
                       timeout=600), "proxy container")
    checked(ctx.docker("cp", str(ctx.proxy_source), f"{px}:/opt/egress_proxy.py", timeout=120), "proxy copy")
    checked(ctx.docker("network", "connect", net, px), "network connect")
    allow = [a for h in MODEL_HOSTS for a in ("--allow", h)]
    checked(ctx.docker("exec", "-d", px, "python3", "-I", "/opt/egress_proxy.py", "--port", str(PROXY_PORT), *allow,
                       "--log", "/opt/egress.jsonl"), "proxy start")
    return net, px


def egress(ctx: Context, px: str, dest: Path) -> dict[str, Any]:
    ctx.docker("cp", f"{px}:/opt/egress.jsonl", str(dest), timeout=120)
    entries = []
    if dest.is_file():
        entries = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"requests": len(entries),
            "allowed": sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is True}),
            "refused": sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is False})}


def refusal(ctx: Context, task: Task) -> str:
    """Why the run may not start, or "". A file ``HOLD`` at the archive's root holds every run (its text says why)."""
    hold = ctx.archive.root / "HOLD"
    if hold.is_file():
        return "held: " + hold.read_text(encoding="utf-8").strip()[:300]
    if not ctx.g6.get("contains_required"):
        return f"G6's commit {str(ctx.g6.get('commit'))[:12]} lacks a required commit ({', '.join(c.REQUIRED_G6_COMMITS)})"
    v = latest(ctx.archive, "validate", task.id)
    if v is None or not v["record"].get("ok") or not v["record"].get("fail_to_pass"):
        return "the task has no finished validation with a FAIL_TO_PASS test"
    _, oracle = image_names(task, str(ctx.g6["commit"]))
    if v["record"].get("oracle_image_id") != out(ctx.docker("image", "inspect", oracle, "--format", "{{.Id}}")):
        return "the task's validation was not made on the images this run would use"
    ok, why = c.may_start(c.spent(ctx.archive.rows()))
    return "" if ok else f"the $100 stop: {why}"


DRY_KEY = "placeholder-not-a-key-0000"
DEAD_PROXY = "http://127.0.0.1:9"


def hook_selftest(ctx: Context, agent: str, task: Task) -> dict[str, Any]:
    """The dry run's check of the round hook: a change in the working tree, the hook run as Claude Code runs it, then
    the saved patch holds the change and the repository's index is as it was."""
    script = (f'cd "{task.repo}" && before=$(git diff --cached --name-only | wc -l) && echo "# dry run" >> we_dry_run.txt'
              f' && echo "{{}}" | {WE}/round_hook.sh > /tmp/we-hook-out.txt 2>&1; code=$?;'
              f' after=$(git diff --cached --name-only | wc -l); saved=$(grep -c we_dry_run.txt {WE}/rounds/round-1.patch);'
              f' said=$(wc -c < /tmp/we-hook-out.txt); rm -f we_dry_run.txt {WE}/rounds/round-1.*;'
              f' echo "{{\\"exit\\": $code, \\"index_before\\": $before, \\"index_after\\": $after, '
              f'\\"patch_names_change\\": $saved, \\"printed_bytes\\": $said}}"')
    try:
        return json.loads(out(ctx.docker.sh(agent, script, workdir=task.repo)).splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "the hook self-test printed nothing readable"}


def run_task(ctx: Context, task: Task, *, dry: bool = False) -> dict[str, Any]:
    """One run (PLAN.md section 4). Refused before anything starts when G6's commit, the validation or the stop says
    so; a failed key check starts no agent. ``dry``: the same path with no key and no way out (a placeholder in the
    key's place, Claude Code's proxy a dead port), plus a self-test of the round hook; nothing can be spent."""
    why = "" if dry else refusal(ctx, task)
    if why:
        return {"status": "refused", "why": why, "task": task.id}
    key = DRY_KEY if dry else ctx.key_reader()
    ctx.archive.redact = c.Redactor(key, *ctx.archive.redact.secrets)
    task_image, _ = image_names(task, str(ctx.g6["commit"]))
    tag = uuid.uuid4().hex[:10]
    common = {"g6_commit": ctx.g6.get("commit"), "g6_image": ctx.g6.get("image"), "task_image": task_image,
              "model": c.MODEL}
    run = ctx.archive.start("dryrun" if dry else "run", task.id, **common)
    record: dict[str, Any] = {**common, "task": task.id, "agent_started": False, "cost_usd": 0.0}
    agent, net, px, status = "", "", "", "error"
    try:
        title, text = ctx.issue_reader(task)
        record["image_id"] = out(ctx.docker("image", "inspect", task_image, "--format", "{{.Id}}"))
        record["key_check"] = key_check(ctx, task, task_image, key, tag)
        run.write_json("key_check.json", record["key_check"])
        if not record["key_check"]["ok"]:
            status = "key_check_failed"
            return run.finish(record, status)
        net, px = start_net(ctx, tag)
        agent = f"g6we-run-{task.id}-{tag}"
        checked(ctx.docker("run", "-d", "--name", agent, "--label", "g6we=1", "--network", net, *LIMITS, *SECURITY,
                           "-u", USER, "-e", f"HOME={HOME}", "-w", task.repo, task_image, "tail", "-f", "/dev/null",
                           timeout=600), "agent container")
        base = out(ctx.docker.sh(agent, "git rev-parse HEAD", workdir=task.repo))
        record["base_commit"] = base
        record["claude_version"] = out(ctx.docker("exec", "-u", USER, agent, CLAUDE, "--version", timeout=120))
        doctor = ctx.docker("exec", "-u", USER, "-e", f"HOME={HOME}", agent, "g6", "doctor", timeout=600)
        run.write("g6_doctor.txt", out(doctor) + "\n" + err(doctor))
        setup = ctx.docker("exec", "-u", USER, "-e", f"HOME={HOME}", "-w", HOME, agent,
                           "g6", "setup", "--client", "claude", "--allow", "patch_review.review", timeout=600)
        run.write("g6_setup.txt", out(setup) + "\n" + err(setup))
        record["g6_setup_exit"] = setup.returncode
        if setup.returncode != 0:
            raise RuntimeError(f"g6 setup exited {setup.returncode}")
        write_in(ctx.docker, agent, f"{WE}/round_hook.sh", hook_script(task.repo, base), mode="755")
        write_in(ctx.docker, agent, f"{HOME}/.claude/settings.json", hook_settings())
        if dry:
            record["hook_selftest"] = hook_selftest(ctx, agent, task)
        text_in = prompt(task, title, text)
        run.write("prompt.txt", text_in)
        args = claude_args()
        way_out = DEAD_PROXY if dry else f"http://{px}:{PROXY_PORT}"
        env_names = ["ANTHROPIC_API_KEY", f"HTTPS_PROXY={way_out}", f"HTTP_PROXY={way_out}",
                     "NO_PROXY=localhost,127.0.0.1", f"HOME={HOME}", "DISABLE_AUTOUPDATER=1",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "DISABLE_TELEMETRY=1", "DISABLE_ERROR_REPORTING=1"]
        docker_args = ["exec", "-i", "-u", USER, "-w", task.repo, *[a for e in env_names for a in ("-e", e)], agent,
                       *args]
        run.write_json("command.json", {"docker": docker_args, "caps": {"usd": c.RUN_CAP_USD, "stop_usd": c.RUN_STOP_USD,
                                        "turns": c.MAX_TURNS, "wall_clock_s": c.WALL_CLOCK_S},
                                        "key": "passed by name; its value only in the docker client's environment"})
        tally = c.Tally()
        started = time.monotonic()
        proc = ctx.docker.popen(*docker_args, env=ctx.client_env(key))
        record["agent_started"] = True
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b"") if proc.stderr else None,
                                 daemon=True)
        drain.start()
        stream = open(run.fresh_path("stream.jsonl"), "w", encoding="utf-8", newline="\n")
        try:
            proc.stdin.write(text_in.encode("utf-8"))
            proc.stdin.close()
            stop = read_stream(iter(proc.stdout.readline, b""), tally, deadline=started + c.WALL_CLOCK_S,
                               write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()))
        finally:
            stream.close()
            if proc.poll() is None:
                ctx.docker.sh(agent, KILL_AGENT, user="root", timeout=60)
                proc.kill()
            proc.wait(timeout=60)
        drain.join(timeout=30)
        run.write("stderr.txt", b"".join(errors).decode("utf-8", errors="replace"))
        result = tally.result or {}
        record.update(status_detail=status_of(stop, tally), cost_usd=tally.cost_usd, stream_usd=round(tally.stream_usd, 6),
                      result_usd=tally.result_usd, reported_usd=result.get("total_cost_usd"),
                      model_calls=tally.model_calls, num_turns=result.get("num_turns"), usage=tally.usage,
                      result_subtype=result.get("subtype"), session_id=tally.session_id,
                      seconds=round(time.monotonic() - started, 1))
        run.write("final_report.txt", str(result.get("result") or ""))
        ctx.docker.sh(agent, diff_script(task.repo, base, f"{WE}/final.patch"), workdir=task.repo, timeout=600)
        final = run.fresh_path("final.patch")
        if not copy_out(ctx.docker, agent, f"{WE}/final.patch", final):
            final.write_bytes(b"")
        record["final_patch_bytes"] = final.stat().st_size
        record["final_patch_sha256"] = c.sha256_file(final)
        for src, name in ((f"{WE}/rounds", "rounds"), (f"{HOME}/.claude/projects", "claude_projects"),
                          (f"{HOME}/.g6", "g6_home")):
            copy_out(ctx.docker, agent, src, run.path / name)
            redact_tree(run.path / name, ctx.archive.redact)
        objects = []
        for line in (run.path / "stream.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                objects.append(json.loads(line))
            except ValueError:
                continue
        rounds = extract_rounds(objects, archived_resolver(run.path))
        run.write_json("rounds.json", rounds)
        record["rounds"] = len(rounds)
        record["outcomes"] = [r["outcome"] for r in rounds]
        record["egress"] = egress(ctx, px, run.fresh_path("egress.jsonl"))
        status = record["status_detail"]
    except Exception as exc:  # noqa: BLE001 - every failure is recorded and the containers are removed
        record["error"] = ctx.archive.redact(f"{type(exc).__name__}: {exc}")[:2000]
        status = "error"
    finally:
        for name in (agent, px):
            if name:
                ctx.docker("rm", "-f", name, timeout=300)
        if net:
            ctx.docker("network", "rm", net, timeout=120)
    return run.finish(record, status)


# --- validation, the independent check and replays ------------------------------------------------------------------------
def _oracle_run(ctx: Context, task: Task, *, patch: Path | None = None, fix: bool = False, tests: bool = True,
                extra: Mapping[str, Path] | None = None, modules: Sequence[str] | None = None,
                folder: c.RunFolder | None = None, label: str = "") -> dict[str, Any]:
    """In a fresh container from the oracle image, with no network: the code (the parent's, the maintainers' fix or
    a patch), the maintainers' test files when ``tests``, extra files, then pytest on ``modules``."""
    _, oracle = image_names(task, str(ctx.g6["commit"]))
    name = f"g6we-check-{uuid.uuid4().hex[:10]}"
    body: dict[str, Any] = {"label": label}
    try:
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", "-w", task.repo, oracle, "tail", "-f", "/dev/null", timeout=600),
                "check container")
        if fix:
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /oracle/fix.patch", workdir=task.repo),
                    "the maintainers' fix")
        if patch is not None and patch.stat().st_size:
            checked(ctx.docker("cp", str(patch), f"{name}:/tmp/we.patch", timeout=300), "patch copy")
            applied = ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo)
            body["patch_applied"] = applied.returncode == 0
            if applied.returncode != 0:
                body["apply_error"] = err(applied)[-600:]
                return body
        if tests:
            files = out(ctx.docker.sh(name, "cat /oracle/fix.files"))
            pairs = [x for st, p in test_changes(files) for x in (st, p)]
            checked(ctx.docker.sh(name, APPLY_TESTS, task.repo, *pairs, workdir=task.repo), "the maintainers' tests")
        for dest, src in (extra or {}).items():
            checked(ctx.docker("cp", str(src), f"{name}:{task.repo}/{dest}", timeout=300), f"copy {dest}")
        ran = ctx.docker.sh(name, pytest_script(modules or task.test_modules), workdir=task.repo, timeout=2400)
        body["pytest_exit"] = out(ran).rsplit("exit=", 1)[-1] if "exit=" in out(ran) else None
        if folder is not None:
            copy_out(ctx.docker, name, "/tmp/we-pytest.txt", folder.path / f"{label}-pytest.txt")
        with tempfile.TemporaryDirectory(prefix="g6we-") as tmp:
            junit = Path(tmp) / "junit.xml"
            body["results"] = parse_junit(junit.read_text(encoding="utf-8")) if copy_out(
                ctx.docker, name, JUNIT_IN, junit) else {}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    return body


def validate(ctx: Context, task: Task) -> dict[str, Any]:
    run = ctx.archive.start("validate", task.id, g6_commit=ctx.g6.get("commit"))
    _, oracle = image_names(task, str(ctx.g6["commit"]))
    oracle_id = checked(ctx.docker("image", "inspect", oracle, "--format", "{{.Id}}"), "the oracle image")
    base = _oracle_run(ctx, task, folder=run, label="base")
    fixed = _oracle_run(ctx, task, fix=True, folder=run, label="fixed")
    f2p, p2p = fail_to_pass(base.get("results") or {}, fixed.get("results") or {})
    record = {"oracle_image_id": oracle_id, "base": base, "fixed": fixed, "fail_to_pass": f2p, "pass_to_pass": p2p,
              "modules": task.test_modules,
              "ok": bool(f2p), "why": "" if f2p else "no test fails on the parent's code and passes with the fix"}
    return run.finish(record, "finished")


def check(ctx: Context, task: Task, run_rel: str | None = None) -> dict[str, Any]:
    v = latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("validate the task first")
    src = ctx.archive.root / run_rel if run_rel else (latest(ctx.archive, "run", task.id) or {}).get("path")
    if src is None:
        raise SystemExit("no run to check")
    src = Path(src)
    f2p, p2p = v["record"]["fail_to_pass"], v["record"]["pass_to_pass"]
    folder = ctx.archive.start("check", task.id, checked_run=str(src.relative_to(ctx.archive.root)).replace("\\", "/"))
    patches = sorted((src / "rounds").glob("**/round-*.patch"), key=lambda p: int(p.stem.split("-")[1]))
    rows = []
    for patch in [*patches, src / "final.patch"]:
        label = patch.stem
        body = _oracle_run(ctx, task, patch=patch, folder=folder, label=label)
        rows.append({"patch": label, "bytes": patch.stat().st_size if patch.exists() else 0,
                     "patch_applied": body.get("patch_applied", True), **verdict(body.get("results") or {}, f2p, p2p)})
    return folder.finish({"checks": rows, "fail_to_pass": f2p, "pass_to_pass_count": len(p2p)}, "finished")


def selfcheck(ctx: Context, task: Task) -> dict[str, Any]:
    """The independent check's own control (no model): the maintainers' fix, given as if it were the agent's patch,
    must pass it, and an empty patch must fail it. A check that cannot tell them apart says nothing about a run."""
    v = latest(ctx.archive, "validate", task.id)
    if v is None:
        raise SystemExit("validate the task first")
    f2p, p2p = v["record"]["fail_to_pass"], v["record"]["pass_to_pass"]
    folder = ctx.archive.start("selfcheck", task.id, g6_commit=ctx.g6.get("commit"))
    _, oracle = image_names(task, str(ctx.g6["commit"]))
    gold = folder.fresh_path("gold.patch")
    got = ctx.docker("run", "--rm", "--network", "none", oracle, "cat", "/oracle/fix.patch", timeout=600)
    checked(got, "the fix's patch")
    gold.write_bytes(got.stdout or b"")
    empty = folder.fresh_path("empty.patch")
    empty.write_bytes(b"")
    rows = {}
    for label, patch in (("gold", gold), ("empty", empty)):
        body = _oracle_run(ctx, task, patch=patch, folder=folder, label=label)
        rows[label] = {"patch_applied": body.get("patch_applied", True), **verdict(body.get("results") or {}, f2p, p2p)}
    ok = bool(rows["gold"]["passes"]) and not rows["empty"]["passes"]
    return folder.finish({"ok": ok, **{k: {"passes": r["passes"], "failing": len(r["failing"]),
                                           "patch_applied": r["patch_applied"]} for k, r in rows.items()}}, "finished")


G6_REPLAY = """import json, sys
from g6.harness.patch_review import review_patch
args = json.loads(sys.stdin.read())
got = review_patch(args.pop("repo"), **args)
a = got.get("assessment") or {}
print(json.dumps({"ok": got.get("ok"), "error": got.get("error"), "outcome": a.get("outcome"),
                  "reasons": [(r.get("stage"), r.get("code")) for r in a.get("reasons") or []],
                  "requirements": [(r.get("id"), r.get("status")) for r in a.get("requirements") or []],
                  "report": a.get("report")}))
"""


def g6replay(ctx: Context, task: Task, code: str, args_file: Path) -> dict[str, Any]:
    """For judging a finding: G6's own review, confined as in the run, of the maintainers' fix (``code`` "fix") or of
    a patch, with the review arguments in ``args_file`` (JSON: task, requirements and the like; the repository is the
    task's). No model is called."""
    folder = ctx.archive.start("g6replay", task.id, code=code, args=args_file.name)
    _, oracle = image_names(task, str(ctx.g6["commit"]))
    name = f"g6we-replay-{uuid.uuid4().hex[:10]}"
    body: dict[str, Any] = {"code": code, "args": json.loads(args_file.read_text(encoding="utf-8"))}
    try:
        checked(ctx.docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *SECURITY, "-u", USER,
                           "-e", f"HOME={HOME}", "-w", task.repo, oracle, "tail", "-f", "/dev/null", timeout=600),
                "replay container")
        if code == "fix":
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /oracle/fix.patch", workdir=task.repo),
                    "the maintainers' fix")
        else:
            checked(ctx.docker("cp", code, f"{name}:/tmp/we.patch", timeout=300), "patch copy")
            checked(ctx.docker.sh(name, "git apply --binary --whitespace=nowarn /tmp/we.patch", workdir=task.repo),
                    "the patch")
        args = {**body["args"], "repo": task.repo}
        done = ctx.docker("exec", "-i", "-u", USER, "-e", f"HOME={HOME}", "-w", task.repo, name, G6PY, "-I", "-",
                          input=(G6_REPLAY.replace("sys.stdin.read()", json.dumps(json.dumps(args)))).encode("utf-8"),
                          timeout=2400)
        try:
            body["review"] = json.loads(out(done).splitlines()[-1])
        except (ValueError, IndexError):
            body["review"] = {"error": f"no readable reply (exit {done.returncode}): {err(done)[-400:]}"}
    finally:
        ctx.docker("rm", "-f", name, timeout=300)
    return folder.finish(body, "finished")


def replay(ctx: Context, task: Task, code: str, test: Path) -> dict[str, Any]:
    folder = ctx.archive.start("replay", task.id, code=code, test=test.name)
    patch = None if code == "fix" else Path(code)
    body = _oracle_run(ctx, task, patch=patch, fix=code == "fix", tests=False, extra={test.name: test},
                       modules=[test.name], folder=folder, label="replay")
    return folder.finish(body, "finished")


def freecheck(ctx: Context, task: Task) -> dict[str, Any]:
    """Before the first paid run (PLAN.md section 9): one confined review with the README's install and a linked
    ``.venv``, with a placeholder in the key's place (no key is read, no model is called)."""
    task_image, _ = image_names(task, str(ctx.g6["commit"]))
    folder = ctx.archive.start("freecheck", task.id, g6_commit=ctx.g6.get("commit"))
    found = key_check(ctx, task, task_image, "placeholder-not-a-key-0000", uuid.uuid4().hex[:10])
    return folder.finish(found, "finished")


# --- building ------------------------------------------------------------------------------------------------------------
def git_commit(ref: str) -> str:
    got = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True)
    if got.returncode != 0 or len(got.stdout.strip()) != 40:
        raise SystemExit(f"{ref!r} names no commit")
    return got.stdout.strip()


def contains_required(commit: str) -> bool:
    return all(subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", req, commit],
                              capture_output=True).returncode == 0 for req in c.REQUIRED_G6_COMMITS)


def default_claude() -> Path:
    return Path.home() / "g6-validation" / "V7-assets" / "dl_claude" / "claude-2.1.309-linux-x64"


def build(docker: Docker, context: Path, tag: str, folder: c.RunFolder, name: str, labels: Sequence[str] = ()) -> str:
    args = ["build", "-t", tag, *[a for lab in labels for a in ("--label", lab)], str(context)]
    done = docker(*args, timeout=7200)
    folder.write(f"{name}-build.txt", out(done) + "\n" + err(done))
    checked(done, f"docker build {tag}")
    return out(docker("image", "inspect", tag, "--format", "{{.Id}}"))


def build_base(archive: c.Archive, ref: str, claude: Path, docker: Docker | None = None) -> dict[str, Any]:
    docker = docker or Docker()
    commit = git_commit(ref)
    if c.sha256_file(claude) != CLAUDE_SHA256:
        raise SystemExit("Claude Code's binary is not the registered build")
    folder = archive.start("build", "base", ref=ref, g6_commit=commit)
    ctx_dir = archive.root / "build" / f"base-{commit[:12]}"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    tar = ctx_dir / "g6src.tar"
    made = subprocess.run(["git", "-C", str(REPO_ROOT), "-c", "core.autocrlf=false", "archive", "--format=tar", "-o",
                           str(tar), commit], capture_output=True)
    if made.returncode != 0:
        raise SystemExit("git archive failed")
    if not (ctx_dir / "claude").is_file():
        shutil.copyfile(claude, ctx_dir / "claude")
    (ctx_dir / "Dockerfile").write_bytes(base_dockerfile().encode("utf-8"))
    tag = f"g6we-base:{commit[:12]}"
    image = build(docker, ctx_dir, tag, folder, "base", [f"we.g6.commit={commit}"])
    rec = {"ref": ref, "g6_commit": commit, "image": tag, "image_id": image, "contains_required": contains_required(commit),
           "source_tar_sha256": c.sha256_file(tar), "claude_sha256": CLAUDE_SHA256, "from": FROM_IMAGE,
           "from_id": out(docker("image", "inspect", FROM_IMAGE, "--format", "{{.Id}}"))}
    return folder.finish(rec, "finished")


def build_images(archive: c.Archive, task: Task, g6_commit: str, docker: Docker | None = None) -> dict[str, Any]:
    docker = docker or Docker()
    folder = archive.start("build", task.id, g6_commit=g6_commit)
    task_image, oracle = image_names(task, g6_commit)
    rec: dict[str, Any] = {"task_image": task_image, "oracle_image": oracle}
    for tag, text, name in ((task_image, task_dockerfile(task, f"g6we-base:{g6_commit[:12]}"), "task"),
                            (oracle, oracle_dockerfile(task, task_image), "oracle")):
        d = archive.root / "build" / f"{name}-{task.id}-{g6_commit[:12]}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Dockerfile").write_bytes(text.encode("utf-8"))
        docker("image", "rm", "-f", tag, timeout=600)     # a failed build must never leave an older image in its place
        rec[f"{name}_id"] = build(docker, d, tag, folder, name)
    return folder.finish(rec, "finished")


def g6_info(g6: str) -> dict[str, Any]:
    commit = git_commit(g6)
    return {"commit": commit, "image": f"g6we-base:{commit[:12]}", "contains_required": contains_required(commit)}


def read_key() -> str:
    """The key, through ``g6.core.keys`` from this checkout's ``src`` (``python -I`` puts nothing else on the path)."""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from g6.core import keys

    return keys.require(keys.Key("ANTHROPIC_API_KEY", "the worked examples' Claude Code runs (the owner's key)",
                                 paid=True))


def read_issue(task: Task) -> tuple[str, str]:
    got = subprocess.run(["gh", "api", f"repos/{task.full_name}/issues/{task.issue}"], capture_output=True, timeout=120)
    if got.returncode != 0:
        raise RuntimeError(f"the issue could not be read: {got.stderr.decode(errors='replace')[-200:]}")
    data = json.loads(got.stdout.decode("utf-8"))
    return str(data.get("title") or ""), str(data.get("body") or "")


def brief(kind: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """A short view of a record, for the terminal: test results are counted, not listed."""
    if kind == "validate":
        return {"ok": record.get("ok"), "fail_to_pass": record.get("fail_to_pass"),
                "pass_to_pass": len(record.get("pass_to_pass") or []),
                **{f"{side}_{k}": (record.get(side) or {}).get(k) for side in ("base", "fixed")
                   for k in ("pytest_exit", "patch_applied")},
                **{f"{side}_results": len((record.get(side) or {}).get("results") or {}) for side in ("base", "fixed")}}
    if kind == "check":
        return {"checks": [{k: row.get(k) for k in ("patch", "bytes", "patch_applied", "passes",
                                                     "fail_to_pass_failing")} | {"failing": len(row.get("failing") or [])}
                           for row in record.get("checks") or []]}
    return {k: v for k, v in record.items() if k not in ("results", "base", "fixed", "usage")}


def progress(archive: c.Archive, task: str) -> dict[str, Any]:
    """An unfinished (or finished) run's progress, read from its stream: model calls, cost so far, rounds and their
    outcomes, and the last tool the agent used. Reads only the archive."""
    runs = sorted(p for p in (archive.root / "run" / task).glob("*") if p.is_dir()) if (archive.root / "run" / task).is_dir() else []
    if not runs:
        return {"run": None}
    folder = runs[-1]
    tally, objects, last_tool = c.Tally(), [], ""
    stream = folder / "stream.jsonl"
    for line in (stream.read_text(encoding="utf-8").splitlines() if stream.is_file() else []):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        tally.feed(obj)
        objects.append(obj)
        for block in ((obj.get("message") or {}).get("content") or []) if obj.get("type") == "assistant" else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                last_tool = str(block.get("name"))
    rounds = extract_rounds(objects, archived_resolver(folder))
    return {"run": folder.name, "finished": (folder / "run.json").is_file(), "model_calls": tally.model_calls,
            "cost_usd": round(tally.cost_usd, 4), "rounds": [r["outcome"] for r in rounds], "last_tool": last_tool}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("base", "images", "validate", "selfcheck", "freecheck", "dryrun", "run", "check",
                                        "replay", "g6replay", "show", "progress", "rounds"))
    ap.add_argument("--args", type=Path, help="g6replay: a JSON file of the review's arguments")
    ap.add_argument("--archive", type=Path, default=None)
    ap.add_argument("--ref", default="origin/claude/launch")
    ap.add_argument("--claude", type=Path, default=None)
    ap.add_argument("--task")
    ap.add_argument("--g6", help="G6's commit (a base image must exist for it)")
    ap.add_argument("--run", dest="run_rel")
    ap.add_argument("--code")
    ap.add_argument("--test", type=Path)
    ap.add_argument("--kind", default="validate", help="show: validate, run, check, freecheck, replay or build")
    args = ap.parse_args(argv)
    archive = c.Archive(c.archive_root(args.archive))
    if args.command == "base":
        print(json.dumps(build_base(archive, args.ref, args.claude or default_claude()), indent=1))
        return 0
    if args.command == "progress":
        print(json.dumps(progress(archive, args.task), indent=1))
        return 0
    if args.command == "rounds":
        # the rounds read again from a finished run's stream and its saved replies, into a new file of the run
        folder = archive.root / args.run_rel
        objects = [json.loads(line) for line in (folder / "stream.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip().startswith("{")]
        rounds = extract_rounds(objects, archived_resolver(folder))
        c.RunFolder(archive, folder, args.run_rel).write_json("rounds_resolved.json", rounds)
        print(json.dumps([{k: r.get(k) for k in ("round", "outcome", "error", "saved_to_file", "polls")}
                          for r in rounds], indent=1))
        return 0
    if args.command == "show":
        found = latest(archive, args.kind, args.task)
        print(json.dumps({"run": found and found["run"], **brief(args.kind, found["record"] if found else {})},
                         indent=1, default=str)[:6000])
        return 0
    task = task_by_id(args.task)
    g6 = g6_info(args.g6)
    if args.command == "images":
        result = build_images(archive, task, g6["commit"])
    else:
        ctx = Context(archive=archive, docker=Docker(), key_reader=read_key, issue_reader=read_issue, g6=g6)
        result = {"validate": lambda: validate(ctx, task), "selfcheck": lambda: selfcheck(ctx, task),
                  "freecheck": lambda: freecheck(ctx, task),
                  "dryrun": lambda: run_task(ctx, task, dry=True),
                  "run": lambda: run_task(ctx, task), "check": lambda: check(ctx, task, args.run_rel),
                  "replay": lambda: replay(ctx, task, args.code, args.test),
                  "g6replay": lambda: g6replay(ctx, task, args.code, args.args)}[args.command]()
    print(json.dumps(brief(args.command, result), indent=1, default=str)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
