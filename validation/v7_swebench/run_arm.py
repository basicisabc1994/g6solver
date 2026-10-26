# SPDX-License-Identifier: Apache-2.0
"""V7, design D (PROTOCOL.md §4-§6): one task through its three arms, archived run by run.

Per task, in this order:

1. **Stage 1, arm A:** Claude Code alone solves the task in the task's own container (stage-1 caps).
2. The finished container is saved as an image (``docker commit``), so both stage-2 passes start from the same state
   and the same Claude Code session.
3. **Stage 2, arm A2:** Claude Code resumes and is told ``v7common.SELF_CHECK``, the same words every task.
4. **Stage 2, arm G:** G6 checks the stage-1 patch (``g6check.py``, in a throwaway container from the same image, G6
   installed from the wheel built from the release ref). If G6 flags a problem, Claude Code resumes with G6's findings
   as its instructions; if not, G keeps the stage-1 patch and spends nothing more. Both passes have the same caps.
5. **Shadow lesson** (``shadow.py``, owner 2026-10-24), when G6's check found something: a model writes one lesson from
   G6's findings and G's outcome into a theory store, archived as its own run (arm ``lesson``); no lesson is ever given
   to Claude Code, and a lesson's failure changes nothing here.
6. Only then are the three patches graded (``evaluate.verify``). Before each stage the runner asserts that the
   benchmark's hidden tests and the grader's files are not in the container.

Every run writes its own write-once folder in the archive (``v7common.Archive``). Commands (``python -I`` from
``oss/``; the script puts ``oss/src`` on its own path for G6's imports):

* ``build-wheel --ref <ref>``: G6's wheel from a named git ref, resolved to its commit; every run records it.
* ``pilot``: the full design on the first 4 tuning ids (phase ``pilot``, never scored; its spend counts).
* ``batch``: the scored order, up to 60 tasks, ``--jobs 2`` at a time, each task starting only when its runs (three
  arms and the lesson) fit under the cap at their reservations; it stops at the first task that does not fit.
* ``grade --phase <phase> --task <id>``: grade a task whose stage 2 finished and whose grading did not.
* ``replay --task <id> --patch gold|empty`` and ``probe --task <id>``: the dry run, with no model.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SRC = HERE.parents[1] / "src"           # oss/src: the frozen G6 tree (python -I ignores PYTHONPATH)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import shadow  # noqa: E402
import v7common as c  # noqa: E402

PROXY_PORT = 3128
MODEL_HOSTS = ("api.anthropic.com:443",)
# Claude Code's --max-turns is the turn cap; the runner's own count (distinct assistant messages, sub-agents included)
# stops a run only past twice the cap, as a backstop in case the flag is not honoured.
TURN_BACKSTOP = 2
CLAUDE_IN = "/usr/local/bin/claude"
G6PY = "/opt/g6py"
CAPTURE = (
    "repo=$(git -C /app rev-parse --show-toplevel 2>/dev/null || git -C /testbed rev-parse --show-toplevel "
    "2>/dev/null || echo /app); "
    'cd "$repo" && git add -A 2>/dev/null; git diff --cached > /tmp/v7_model.patch 2>/dev/null; git reset -q 2>/dev/null; '
    "wc -c < /tmp/v7_model.patch; true"
)   # the official locked agents' capture (v2/tooling/locked_claude_code.py), to a different file name
GIT_STATE = (
    'cd "$1" && echo "all=$(git rev-list --all --count 2>/dev/null)" && echo "head=$(git rev-list HEAD --count '
    '2>/dev/null)" && echo "stash=$(git stash list 2>/dev/null | wc -l)" && echo "refs=$(git for-each-ref '
    '2>/dev/null | wc -l)" && echo "dirty=$(git status --porcelain 2>/dev/null | wc -l)" && echo "sha=$(git rev-parse '
    'HEAD 2>/dev/null)"'
)
KILL_AGENT = (f'for p in /proc/[0-9]*; do [ "$(readlink $p/exe 2>/dev/null)" = {CLAUDE_IN} ] && '
              'kill -9 "${p#/proc/}" 2>/dev/null; done; true')
GOLD_APPLY = ("cd /app 2>/dev/null || cd /testbed; git apply --verbose /tmp/v7_gold.diff || "
              "patch --fuzz=5 -p1 -i /tmp/v7_gold.diff")   # solution/solve.sh, from /tmp
# The grader's folders and files; none may exist in a container where an arm works.
ORACLE_PATHS = ("/tests", "/solution", "/logs/verifier", "/tmp/replay.patch", "/tmp/v7_gold.diff")
ORACLE_CHECK = ('# v7-oracle-check\nfound=0; for p in "$@"; do if [ -e "$p" ]; then found=$((found+1)); fi; done; '
                'if [ "$found" -eq 0 ]; then echo "clean 0"; else echo "present $found"; fi')
# G's words when G6 flags the stage-1 patch: G6's findings, then the same closing line every task.
REPAIR_PROMPT = """G6's pre-finish check of your change failed:

{failures}

Fix what it names, keep every existing test active, then finish."""


# --- docker ------------------------------------------------------------------------------------------------------------
class Docker:
    """The docker CLI through ``subprocess`` (list arguments, no shell). ``runner`` is replaceable in tests."""

    def __init__(self, exe: str = "docker", runner: Callable[..., Any] = subprocess.run) -> None:
        self.exe, self.runner = exe, runner

    def __call__(self, *args: str, input: bytes | None = None, timeout: float | None = None,
                 env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
        return self.runner([self.exe, *args], input=input, capture_output=True, timeout=timeout,
                           env=dict(env) if env is not None else None)

    def sh(self, ctr: str, script: str, *args: str, timeout: float = 600, user: str = "root",
           workdir: str | None = None) -> subprocess.CompletedProcess:
        extra = ["-w", workdir] if workdir else []
        return self("exec", "-u", user, *extra, ctr, "bash", "-c", script, "v7", *args, timeout=timeout)

    def popen(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        return subprocess.Popen([self.exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env) if env is not None else None)


def out(cp: subprocess.CompletedProcess) -> str:
    return (cp.stdout or b"").decode("utf-8", errors="replace").strip()


def checked(cp: subprocess.CompletedProcess, what: str) -> str:
    if cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"{what} failed ({cp.returncode}): {(out(cp) + ' ' + err)[-400:]}")
    return out(cp)


def copy_in(docker: Docker, ctr: str, src: Path, dest: str) -> None:
    cp = docker("cp", str(src), f"{ctr}:{dest}", timeout=1800)
    if cp.returncode != 0:
        raise RuntimeError(f"docker cp {src.name}: {out(cp)[-200:]} {(cp.stderr or b'').decode(errors='replace')[-200:]}")


@dataclass(frozen=True)
class Assets:
    harness: Path       # the harness tree's v2 folder (tasks/ inside)
    claude: Path        # Claude Code's linux-x64 binary
    python: Path        # the standalone Python tarball
    wheelhouse: Path    # the wheels g6[mcp] needs, for linux x86_64, CPython 3.12
    wheel: Path | None = None   # tests only: runs use the wheel build-wheel made from a named ref
    proxy: Path = HERE / "egress_proxy.py"
    checker: Path = HERE / "g6check.py"

    @classmethod
    def load(cls, path: Path) -> Assets:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: Path(v) for k, v in data.items() if k != "wheel"})

    def verify(self) -> list[str]:
        problems = []
        for path, want in ((self.claude, c.CLAUDE_CODE["sha256"]), (self.python, c.PYTHON["sha256"])):
            if not path.is_file() or c.sha256_file(path) != want:
                problems.append(f"{path.name}: missing or not the registered SHA-256")
        if not (self.harness / "tasks").is_dir():
            problems.append(f"{self.harness}: no tasks/ folder")
        if not self.wheelhouse.is_dir():
            problems.append("the wheelhouse is missing")
        return problems


@dataclass
class RunContext:
    archive: c.Archive
    assets: Assets
    splits: dict
    docker: Docker = field(default_factory=Docker)
    environ: Mapping[str, str] | None = None
    key_reader: Callable[[], str] | None = None     # tests inject one; the default reads g6.core.keys
    wheel: dict | None = None                       # build_wheel's record: ref, commit, path, sha256
    lesson_client: Callable[[str], Any] | None = None   # the key -> the shadow lesson's SDK client (tests: a fake)

    def wheel_path(self) -> Path:
        path = (self.wheel or {}).get("path") or self.assets.wheel
        if not path:
            raise RuntimeError("no G6 wheel: run `run_arm.py build-wheel --ref <ref>` first")
        return Path(path)

    def wheel_record(self) -> dict[str, Any]:
        return {k: (self.wheel or {}).get(k) for k in ("ref", "commit", "sha256")}


def read_key() -> str:
    from g6.core import keys

    return keys.require(keys.Key("ANTHROPIC_API_KEY", "V7's Claude Code runs (the owner's capped key)", paid=True))


# --- G6's wheel, built at run time from a named git ref ----------------------------------------------------------------
WHEEL_PATHS = ("oss/pyproject.toml", "oss/src", "oss/README.md", "oss/LICENSE", "oss/NOTICE")


def build_wheel(ref: str, archive: c.Archive, *, repo: Path = HERE.parents[2],
                runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, Any]:
    """G6's wheel from ``ref``, resolved to its commit first (a moving branch cannot change under the study): ``git
    archive`` of the package's files at that commit, then ``uv build --wheel``. The wheel, the build's output and its
    record go into an archived run, ``build/<commit>/wheel/<time>``; every run records the ref, commit and SHA-256."""
    import tarfile

    got = runner(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"], capture_output=True)
    commit = (got.stdout or b"").decode().strip()
    if got.returncode != 0 or len(commit) != 40:
        raise SystemExit(f"{ref!r} does not name a commit in {repo}")
    run = archive.start("build", commit, "wheel", ref=ref)
    status, record = "error", {"ref": ref, "commit": commit}
    try:
        with tempfile.TemporaryDirectory(prefix="v7wheel-") as tmp:
            work = Path(tmp)
            tar_path = work / "src.tar"
            made = runner(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(tar_path), commit, *WHEEL_PATHS],
                          capture_output=True)
            if made.returncode != 0:
                raise RuntimeError(f"git archive failed for {commit}")
            record["source_tar_sha256"] = c.sha256_file(tar_path)
            with tarfile.open(tar_path) as tar:
                tar.extractall(work / "src", filter="data")
            built = runner(["uv", "build", "--wheel", "--out-dir", str(work / "dist"), str(work / "src" / "oss")],
                           capture_output=True)
            run.write("build-output.txt", ((built.stdout or b"") + (built.stderr or b"")).decode(errors="replace"))
            wheels = sorted((work / "dist").glob("g6-*.whl")) if (work / "dist").is_dir() else []
            if built.returncode != 0 or len(wheels) != 1:
                raise RuntimeError(f"uv build did not make exactly one G6 wheel for {commit}")
            target = run.write(wheels[0].name, wheels[0].read_bytes())
        record.update(path=str(target), name=target.name, sha256=c.sha256_file(target))
        status = "finished"
    except Exception as exc:  # noqa: BLE001 - recorded in the run, then raised
        record["error"] = f"{type(exc).__name__}: {exc}"[:600]
        run.finish(record, status)
        raise SystemExit(record["error"]) from None
    run.finish(record, status)
    return record


def wheel_info(archive: c.Archive) -> dict[str, Any] | None:
    """The newest finished wheel build in the archive."""
    builds = [r for r in archive.runs() if r.get("phase") == "build" and r.get("status") == "finished"]
    if not builds:
        return None
    index = archive.root / sorted(builds, key=lambda r: r["run"])[-1]["run"] / "run.json"
    return json.loads(index.read_text(encoding="utf-8"))


# --- the task's environment ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskEnv:
    instance_id: str
    image: str
    cpus: float
    memory_mb: int
    agent_timeout_s: float
    verifier_timeout_s: float
    folder: Path


def task_env(harness: Path, instance_id: str) -> TaskEnv:
    """The task's Harbor settings. Only ``task.toml`` and the environment's Dockerfile are read; a Dockerfile that is
    more than ``FROM <image>`` is refused, since the runner uses the image."""
    folder = harness / "tasks" / instance_id
    toml = tomllib.loads((folder / "task.toml").read_text(encoding="utf-8"))
    env = toml["environment"]
    if (folder / "environment" / "Dockerfile").read_text(encoding="utf-8").strip() != f"FROM {env['docker_image']}":
        raise SystemExit(f"{instance_id}: its Dockerfile is more than FROM <image>; V7's runner does not build it")
    return TaskEnv(instance_id, env["docker_image"], float(env.get("cpus") or 1), int(env.get("memory_mb") or 4096),
                   float(toml["agent"]["timeout_sec"]), float(toml["verifier"]["timeout_sec"]), folder)


def instruction(env: TaskEnv) -> str:
    """The task's text, read only to hand it to the agent; the runner never prints it."""
    return (env.folder / "instruction.md").read_text(encoding="utf-8")


def created_paths(patch_text: str) -> list[str]:
    """The files a unified diff creates (``--- /dev/null`` then ``+++ b/<path>``)."""
    lines = patch_text.splitlines()
    return [lines[i + 1][6:].strip() for i, line in enumerate(lines[:-1])
            if line.startswith("--- /dev/null") and lines[i + 1].startswith("+++ b/")]


def hidden_paths(env: TaskEnv, repo: str) -> list[str]:
    """Where the grader's material would be: its folders, and the files the hidden test patch creates. The patch is
    read for its file names only, here, and the names are never written anywhere."""
    patch = env.folder / "tests" / "test_patch.patch"
    names = created_paths(patch.read_text(encoding="utf-8", errors="replace")) if patch.is_file() else []
    return [*ORACLE_PATHS, *(f"{repo}/{n}" for n in names)]


class OracleVisible(RuntimeError):
    """The grader's material is in a container where an arm works: the task stops before any model call."""


def ensure_image(docker: Docker, image: str) -> str:
    """The image's repository digest, pulling it first if it is not here."""
    found = docker("image", "inspect", image, "--format", "{{json .RepoDigests}}")
    if found.returncode != 0:
        pulled = docker("pull", image, timeout=7200)
        if pulled.returncode != 0:
            raise RuntimeError(f"docker pull failed: {out(pulled)[-300:]}")
        found = docker("image", "inspect", image, "--format", "{{json .RepoDigests}}")
    digests = json.loads(out(found) or "[]")
    return digests[0] if digests else ""


# --- containers --------------------------------------------------------------------------------------------------------
@dataclass
class Net:
    """One task's internal network and its proxy sidecar, shared by every container of the task."""

    docker: Docker
    network: str = ""
    sidecar: str = ""

    def close(self) -> None:
        if self.sidecar:
            self.docker("rm", "-f", self.sidecar, timeout=300)
        if self.network:
            self.docker("network", "rm", self.network, timeout=120)


@dataclass
class Box:
    """One container where an arm (or G6's check) works."""

    docker: Docker
    agent: str
    repo: str = "/app"
    net: Net | None = None

    @property
    def sidecar(self) -> str:
        return self.net.sidecar if self.net else ""

    def close(self) -> None:
        self.docker("rm", "-f", self.agent, timeout=300)


def install_python(docker: Docker, ctr: str, assets: Assets, dest: str = G6PY) -> None:
    copy_in(docker, ctr, assets.python, "/tmp/v7py.tar.gz")
    checked(docker.sh(ctr, f'mkdir -p {dest} && tar -xzf /tmp/v7py.tar.gz -C {dest} --strip-components=1 && '
                           f'rm -f /tmp/v7py.tar.gz && {dest}/bin/python3 --version'), "standalone Python")


def start_net(docker: Docker, env: TaskEnv, assets: Assets, tag: str) -> Net:
    """An internal network with no route out, and a sidecar proxy that lets through only the model endpoint."""
    net = Net(docker)
    label = ["--label", "v7=1"]
    try:
        net.network = f"v7net-{tag}"
        checked(docker("network", "create", "--internal", *label, net.network), "network create")
        net.sidecar = f"v7px-{tag}"
        checked(docker("run", "-d", "--name", net.sidecar, *label, "--entrypoint", "tail", env.image, "-f",
                       "/dev/null", timeout=600), "proxy sidecar")
        install_python(docker, net.sidecar, assets, "/opt/v7py")
        copy_in(docker, net.sidecar, assets.proxy, "/opt/v7py/egress_proxy.py")
        checked(docker("network", "connect", net.network, net.sidecar), "network connect")
        allow = [a for h in MODEL_HOSTS for a in ("--allow", h)]
        checked(docker("exec", "-d", net.sidecar, "/opt/v7py/bin/python3", "-I", "/opt/v7py/egress_proxy.py",
                       "--port", str(PROXY_PORT), *allow, "--log", "/opt/v7py/egress.jsonl"), "proxy start")
    except Exception:
        net.close()
        raise
    return net


def start_box(docker: Docker, env: TaskEnv, image: str, net: Net | None, name: str) -> Box:
    limits = ["--cpus", f"{env.cpus:g}", "--memory", f"{env.memory_mb}m"]
    where = ["--network", net.network] if net else []
    checked(docker("run", "-d", "--name", name, "--label", "v7=1", *limits, *where, "--entrypoint", "tail", image,
                   "-f", "/dev/null", timeout=600), f"container {name}")
    box = Box(docker, name, net=net)
    box.repo = out(docker.sh(name, "git -C /app rev-parse --show-toplevel 2>/dev/null || git -C /testbed rev-parse "
                                   "--show-toplevel 2>/dev/null || echo /app")) or "/app"
    return box


def git_state(docker: Docker, box: Box) -> dict[str, Any]:
    """Whether the repository's history could hold the fix: every ref's history equals HEAD's, no stash."""
    state: dict[str, Any] = {}
    for line in out(docker.sh(box.agent, GIT_STATE, box.repo)).splitlines():
        key, _, value = line.partition("=")
        state[key.strip()] = int(value) if value.strip().isdigit() else value.strip()
    state["history_clean"] = bool(state.get("all") and state.get("all") == state.get("head") and not state.get("stash"))
    return state


def install_claude(docker: Docker, box: Box, assets: Assets) -> str:
    musl = out(docker.sh(box.agent, "ldd /bin/ls 2>&1 | grep -q musl && echo musl || echo glibc"))
    if musl == "musl":
        raise RuntimeError("the image is musl-based; V7 registered only Claude Code's glibc build")
    copy_in(docker, box.agent, assets.claude, CLAUDE_IN)
    return checked(docker.sh(box.agent, f"chmod 755 {CLAUDE_IN} && {CLAUDE_IN} --version"), "claude --version")


def install_g6_runtime(docker: Docker, box: Box, assets: Assets, wheel: Path) -> str:
    """The standalone Python and G6's wheel (with its MCP extra) from the offline wheelhouse: no skill, no MCP
    configuration, nothing Claude Code would load. Returns G6's version."""
    install_python(docker, box.agent, assets)
    copy_in(docker, box.agent, assets.wheelhouse, "/opt/g6wheels")
    copy_in(docker, box.agent, wheel, f"/opt/g6wheels/{wheel.name}")
    checked(docker.sh(box.agent, f"{G6PY}/bin/python3 -m pip install -q --no-index --no-warn-script-location "
                                 f"--find-links /opt/g6wheels '/opt/g6wheels/{wheel.name}[mcp]'", timeout=1800),
            "pip install g6[mcp]")
    return checked(docker.sh(box.agent, f"{G6PY}/bin/python3 -c 'import g6; print(g6.__version__)'"), "import g6")


def write_in(docker: Docker, ctr: str, dest: str, text: str) -> None:
    checked(docker("exec", "-i", "-u", "root", ctr, "bash", "-c", f'mkdir -p "$(dirname {dest})" && cat > {dest}',
                   input=text.encode("utf-8")), f"write {dest}")


def assert_clean(docker: Docker, box: Box, paths: Sequence[str]) -> None:
    """The grader's folders and the hidden tests' new files are absent, or the task stops here."""
    said = checked(docker.sh(box.agent, ORACLE_CHECK, *paths), "oracle check")
    if not said.startswith("clean"):
        raise OracleVisible(f"the grader's material is in the container ({said.split()[-1]} of {len(paths)} paths)")


def capture_patch(docker: Docker, box: Box, dest: Path) -> int:
    size = checked(docker.sh(box.agent, CAPTURE), "patch capture")
    cp = docker("cp", f"{box.agent}:/tmp/v7_model.patch", str(dest), timeout=600)
    if cp.returncode != 0 or not dest.exists():
        dest.write_bytes(b"")
    return int(size.split()[-1]) if size.split() and size.split()[-1].isdigit() else dest.stat().st_size


def summarise_egress(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    refused = sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is False})
    allowed = sorted({f"{e.get('host')}:{e.get('port')}" for e in entries if e.get("allowed") is True})
    return {"requests": len(entries), "allowed": allowed, "refused": refused}


def egress_log(docker: Docker, net: Net | None, dest: Path) -> dict[str, Any]:
    if net is None or not net.sidecar:
        return summarise_egress(())
    docker("cp", f"{net.sidecar}:/opt/v7py/egress.jsonl", str(dest), timeout=120)
    if not dest.is_file():
        return summarise_egress(())
    return summarise_egress([json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()
                             if line.strip()])


# --- Claude Code's stream ----------------------------------------------------------------------------------------------
def claude_args(*, max_turns: int, max_usd: float, resume: str = "") -> list[str]:
    """Arm A's flags, the same in every stage (stage 2 adds the session it resumes)."""
    args = [CLAUDE_IN, "-p", "--model", c.MODEL, "--effort", c.EFFORT, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(max_turns), "--max-budget-usd", f"{max_usd:.2f}", "--dangerously-skip-permissions",
            "--disallowedTools", "WebFetch", "WebSearch", "--strict-mcp-config"]
    if resume:
        args += ["--resume", resume]
    return args


@dataclass
class Tally:
    """What Claude Code's stream-json said: model calls (distinct assistant messages), their usage, the result."""

    usage_by_message: dict[str, dict] = field(default_factory=dict)
    result: dict | None = None
    session_id: str = ""
    refusal: bool = False
    lines: int = 0
    reported: float | None = None       # Claude Code's own total_cost_usd, when its result record arrived

    def feed(self, obj: Mapping[str, Any]) -> None:
        self.lines += 1
        self.session_id = str(obj.get("session_id") or self.session_id)
        kind = obj.get("type")
        if kind == "assistant":
            msg = obj.get("message") or {}
            key = str(msg.get("id") or f"line-{self.lines}")
            if msg.get("usage"):
                self.usage_by_message[key] = dict(msg["usage"])
            else:
                self.usage_by_message.setdefault(key, {})
            if msg.get("stop_reason") == "refusal":
                self.refusal = True
        elif kind == "result":
            self.result = dict(obj)

    @property
    def model_calls(self) -> int:
        return len(self.usage_by_message)

    @property
    def estimated_usd(self) -> float:
        return round(sum(c.usage_cost(u) for u in self.usage_by_message.values()), 6)

    @property
    def cost_usd(self) -> float:
        """Claude Code's own total when it reported one, else the stream's count at PRICES."""
        return round(self.reported, 6) if self.reported is not None else self.estimated_usd

    @property
    def usage(self) -> dict[str, int]:
        keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        return {k: sum(int(u.get(k) or 0) for u in self.usage_by_message.values()) for k in keys}


def read_stream(lines: Iterable[bytes], tally: Tally, *, deadline: float, max_turns: int, max_usd: float,
                write: Callable[[str], None], clock: Callable[[], float] = time.monotonic) -> str:
    """Feed the stream to ``tally`` until it ends ("") or a cap is reached ("timeout", "cap_turns", "cap_cost").
    Lines are read on a thread, so the deadline holds while the agent is silent. Every line is written, unfiltered."""
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
        if tally.model_calls > max_turns * TURN_BACKSTOP:
            return "cap_turns"
        if tally.estimated_usd > max_usd * 1.05:
            return "cap_cost"


def status_of(stop: str, tally: Tally) -> str:
    if stop:
        return stop
    sub = str((tally.result or {}).get("subtype") or "")
    if tally.refusal:
        return "refused"
    if tally.result is None:
        return "error"
    if "max_turns" in sub:
        return "cap_turns"
    if "budget" in sub:
        return "cap_cost"
    return "error" if tally.result.get("is_error") else "finished"


def _base_environ() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def run_claude(ctx: RunContext, box: Box, run: c.RunFolder, prompt: str, key: str, caps: Mapping[str, Any], *,
               resume: str = "") -> dict[str, Any]:
    """Claude Code headless, once, in ``box``. The run folder keeps the prompt, the exact command and caps, every line
    of the stream and its standard error."""
    args = claude_args(max_turns=int(caps["max_turns"]), max_usd=float(caps["max_usd_per_run"]), resume=resume)
    env_names = ["ANTHROPIC_API_KEY", f"HTTPS_PROXY=http://{box.sidecar}:{PROXY_PORT}",
                 f"HTTP_PROXY=http://{box.sidecar}:{PROXY_PORT}", "NO_PROXY=localhost,127.0.0.1", "IS_SANDBOX=1",
                 "DISABLE_AUTOUPDATER=1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "DISABLE_TELEMETRY=1",
                 "DISABLE_ERROR_REPORTING=1"]
    docker_args = ["exec", "-i", "-u", "root", "-w", box.repo, *[a for e in env_names for a in ("-e", e)], box.agent,
                   *args]
    run.write("prompt.txt", prompt)
    run.write_json("command.json", {"docker": docker_args, "claude": args, "caps": dict(caps), "model": c.MODEL,
                                    "effort": c.EFFORT, "resume": resume, "system_prompt": "Claude Code's default",
                                    "key": "passed by name; its value only in the docker client's environment"})
    env = {**(dict(ctx.environ) if ctx.environ is not None else _base_environ()), "ANTHROPIC_API_KEY": key}
    tally = Tally()
    deadline = time.monotonic() + float(caps["wall_clock_s"])
    started = time.monotonic()
    proc = ctx.docker.popen(*docker_args, env=env)
    errors: list[bytes] = []      # drained on a thread, so a full stderr pipe can never stall the stream
    drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b"") if proc.stderr else None,
                             daemon=True)
    drain.start()
    stream = run.stream("stream.jsonl")
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        stop = read_stream(iter(proc.stdout.readline, b""), tally, deadline=deadline,
                           max_turns=int(caps["max_turns"]), max_usd=float(caps["max_usd_per_run"]),
                           write=lambda line: (stream.write(ctx.archive.redact(line) + "\n"), stream.flush()))
    finally:
        stream.close()
        if proc.poll() is None:
            ctx.docker.sh(box.agent, KILL_AGENT, timeout=60)
            proc.kill()
        proc.wait(timeout=60)
    drain.join(timeout=30)
    run.write("stderr.txt", b"".join(errors).decode("utf-8", errors="replace"))
    if tally.result is not None and not stop and tally.result.get("total_cost_usd") is not None:
        tally.reported = float(tally.result["total_cost_usd"])
    result = tally.result or {}
    return {"status": status_of(stop, tally), "agent_started": True, "cost_usd": tally.cost_usd,
            "cost_source": "claude_code_result" if tally.reported is not None else "estimated_from_stream",
            "reported_usd": tally.reported, "estimated_usd": tally.estimated_usd, "num_turns": result.get("num_turns"),
            "model_calls": tally.model_calls, "usage": tally.usage, "claude_duration_ms": result.get("duration_ms"),
            "session_id": tally.session_id, "result_subtype": result.get("subtype"), "refusal": tally.refusal,
            "seconds": round(time.monotonic() - started, 3)}


# --- one task, design D -------------------------------------------------------------------------------------------------
def refused_by_the_api(run: Mapping[str, Any]) -> bool:
    """A finished run that ended in an error with no token in or out and no cost: the API refused it (a key or account
    fault), so the model never saw the task and there is no outcome to choose among."""
    rec = run.get("record") or {}
    usage = rec.get("usage")
    return (rec.get("status") == "error" and isinstance(usage, dict) and bool(usage) and not any(usage.values())
            and not rec.get("cost_usd"))


def attempted(archive: c.Archive, phase: str, task: str) -> bool:
    """One attempt per task: a task whose stage 1 started Claude Code (or is still running) is never run again. A
    stage 1 the API refused before any token (:func:`refused_by_the_api`) is an infrastructure failure, not an
    attempt: it stays in the archive and the task may start again."""
    return any((r.get("agent_started") or r.get("status") == "running") and not refused_by_the_api(r)
               for r in archive.find(phase, task, "A"))


def run_task(ctx: RunContext, phase: str, task: str, *, grade: Callable[[str, str, str], Any] | None = None,
             admitted: bool = False) -> dict[str, Any]:
    """Stage 1, the snapshot, A2, G6's check, G, then grading: every step its own archived run."""
    if attempted(ctx.archive, phase, task):
        return {"status": "already_run", "task": task}
    if not admitted:
        ok, why = c.may_start(c.spend(ctx.archive.records()), phase, need=c.TASK_RESERVE, environ=ctx.environ)
        if not ok:
            return {"status": "not_started", "task": task, "reason": why}
    key = (ctx.key_reader or read_key)()
    ctx.archive.redact = c.Redactor(key, *ctx.archive.redact.secrets)
    env = task_env(ctx.assets.harness, task)
    common = {"g6_wheel": ctx.wheel_record(), "claude_code": c.CLAUDE_CODE["version"], "model": c.MODEL,
              "effort": c.EFFORT}
    task_run = ctx.archive.start(phase, task, "task", **common)
    tag = uuid.uuid4().hex[:10]
    snapshot = f"v7snap:{tag}"
    record: dict[str, Any] = {**common, "stage2_done": False, "runs": {}}
    boxes: list[Box] = []
    net: Net | None = None
    open_runs: list[c.RunFolder] = []
    have_snapshot = False

    def begin(arm: str) -> c.RunFolder:
        run = ctx.archive.start(phase, task, arm, **common)
        open_runs.append(run)
        record["runs"][arm] = run.rel
        return run

    def end(run: c.RunFolder, body: dict[str, Any], status: str) -> dict[str, Any]:
        open_runs.remove(run)
        return run.finish(body, status)

    try:
        digest = ensure_image(ctx.docker, env.image)
        record["image_digest"] = digest
        net = start_net(ctx.docker, env, ctx.assets, tag)
        # Stage 1: Claude Code alone.
        a = begin("A")
        s1 = start_box(ctx.docker, env, env.image, net, f"v7a-{tag}")
        boxes.append(s1)
        hidden = hidden_paths(env, s1.repo)
        a.write_json("environment.json", {"image": env.image, "image_digest": digest, "git": git_state(ctx.docker, s1),
                                          "claude_version": install_claude(ctx.docker, s1, ctx.assets),
                                          "oracle_paths_checked": len(hidden)})
        assert_clean(ctx.docker, s1, hidden)
        stage1 = run_claude(ctx, s1, a, instruction(env), key, c.CAPS)
        stage1["patch_bytes"] = capture_patch(ctx.docker, s1, a.fresh_path("model.patch"))
        a_patch = a.path / "model.patch"
        stage1["patch_sha256"] = c.sha256_file(a_patch)
        end(a, {**stage1, "stage": 1, "released": True}, stage1["status"])
        session = stage1.get("session_id") or ""
        checked(ctx.docker("commit", s1.agent, snapshot, timeout=1800), "snapshot")
        have_snapshot = True
        s1.close()
        boxes.remove(s1)
        # Stage 2, A2: Claude Code checks itself, the same words every task.
        record["A2"] = _stage2(ctx, env, net, snapshot, f"v7a2-{tag}", begin("A2"), end, boxes, hidden, key, session,
                               c.SELF_CHECK, a_patch)
        # Stage 2, G: G6 checks the stage-1 patch; Claude Code acts on its findings only when it flags.
        g = begin("G")
        chk = start_box(ctx.docker, env, snapshot, net, f"v7gc-{tag}")
        boxes.append(chk)
        assert_clean(ctx.docker, chk, hidden)
        finding = g6_check(ctx, chk, g)
        chk.close()
        boxes.remove(chk)
        if finding["flag"] and session:
            prompt = REPAIR_PROMPT.format(failures=finding["findings_text"])
            g.write("findings_given.txt", prompt)
            record["G"] = _stage2(ctx, env, net, snapshot, f"v7g-{tag}", g, end, boxes, hidden, key, session, prompt,
                                  a_patch, extra={"g6_flag": True, "acted": True, "g6_version": finding.get("g6_version")})
        else:
            g.write("model.patch", a_patch.read_bytes())
            body = {"stage": 2, "g6_flag": bool(finding["flag"]), "acted": False, "agent_started": False,
                    "cost_usd": 0.0, "released": True, "patch_sha256": c.sha256_file(g.path / "model.patch"),
                    "why": "G6 did not flag the stage-1 patch" if not finding["flag"] else "no stage-1 session"}
            record["G"] = end(g, body, "kept" if not finding["flag"] else "no_session")
        record["g6_flag"] = bool(finding["flag"])
        record["stage2_done"] = True
        # Shadow learning: a lesson from G6's check and G's outcome, after both passes and before any grade; it is
        # never given to Claude Code, and its failure changes nothing here.
        record["lesson"] = shadow.after_task(phase, task, finding, record["G"], stage1["patch_sha256"],
                                             archive=ctx.archive, client=ctx.lesson_client or shadow.anthropic_client,
                                             key=key, begin=begin, end=end)
        record["egress"] = egress_log(ctx.docker, net, task_run.fresh_path("egress.jsonl"))
        status = "finished"
    except Exception as exc:  # noqa: BLE001 - every failure is recorded; a started run is kept and reported
        status = "infra_error" if not record["runs"].get("A") or isinstance(exc, OracleVisible) else "error"
        record["error"] = ctx.archive.redact(f"{type(exc).__name__}: {exc}")[:2000]
        for run in list(open_runs):
            end(run, {"error": record["error"], "agent_started": False}, "error")
    finally:
        for box in boxes:
            box.close()
        if net is not None:
            if record.get("egress") is None:
                try:
                    record["egress"] = egress_log(ctx.docker, net, task_run.fresh_path("egress.jsonl"))
                except c.ArchiveError:
                    pass
            net.close()
        if have_snapshot:
            ctx.docker("image", "rm", "-f", snapshot, timeout=600)
    done = task_run.finish({k: v for k, v in record.items() if k not in ("A2", "G")}, status)
    if status == "finished" and grade is not None:
        grade_task(ctx, phase, task, evaluate=grade)
    return {**done, "task": task}


def _stage2(ctx: RunContext, env: TaskEnv, net: Net, snapshot: str, name: str, run: c.RunFolder, end: Callable,
            boxes: list[Box], hidden: Sequence[str], key: str, session: str, prompt: str, stage1_patch: Path, *,
            extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One stage-2 pass: a container from the snapshot, Claude Code resumed with ``prompt`` under the stage-2 caps,
    then the patch. Without a stage-1 session there is nothing to resume, and the stage-1 patch stands."""
    if not session:
        run.write("model.patch", stage1_patch.read_bytes())
        return end(run, {"stage": 2, "agent_started": False, "cost_usd": 0.0, "released": True, **(extra or {}),
                         "why": "no stage-1 session to resume"}, "no_session")
    box = start_box(ctx.docker, env, snapshot, net, name)
    boxes.append(box)
    assert_clean(ctx.docker, box, hidden)
    outcome = run_claude(ctx, box, run, prompt, key, c.CAPS_STAGE2, resume=session)
    outcome["patch_bytes"] = capture_patch(ctx.docker, box, run.fresh_path("model.patch"))
    outcome["patch_sha256"] = c.sha256_file(run.path / "model.patch")
    box.close()
    boxes.remove(box)
    return end(run, {**outcome, **(extra or {}), "stage": 2, "released": True}, outcome["status"])


def g6_check(ctx: RunContext, box: Box, run: c.RunFolder) -> dict[str, Any]:
    """G6's check of the stage-1 patch, in its own throwaway container: its full output is archived as returned."""
    version = install_g6_runtime(ctx.docker, box, ctx.assets, ctx.wheel_path())
    copy_in(ctx.docker, box.agent, ctx.assets.checker, "/opt/v7/g6check.py")
    python = out(ctx.docker.sh(box.agent, "command -v python3 || true"))
    started = time.monotonic()
    ran = ctx.docker("exec", "-u", "root", "-w", box.repo, box.agent, f"{G6PY}/bin/python3", "-I", "/opt/v7/g6check.py",
                     "--repo", box.repo, *(["--python", python] if python else []), timeout=1800)
    run.write("g6_check_stdout.txt", out(ran))
    run.write("g6_check_stderr.txt", (ran.stderr or b"").decode("utf-8", errors="replace"))
    try:
        found = json.loads(out(ran).splitlines()[-1])
    except (ValueError, IndexError):
        found = {"flag": False, "findings": [], "error": f"G6's check gave no reply (exit {ran.returncode})"}
    found.setdefault("findings_text", "\n".join(f"- {f.get('said')}" for f in found.get("findings", ())))
    found.update(g6_runtime=version, seconds=round(time.monotonic() - started, 3), exit=ran.returncode)
    run.write_json("g6_check.json", found)
    return found


# --- grading, only after both stage-2 passes ---------------------------------------------------------------------------
def grade_task(ctx: RunContext, phase: str, task: str, *, evaluate: Callable[[str, str, str], Any] | None = None) -> list:
    """Grade the three patches. Refused unless the task's run says both stage-2 passes finished."""
    latest = ctx.archive.latest(phase, task, "task")
    if latest is None or not (latest["record"] or {}).get("stage2_done"):
        raise RuntimeError(f"{task}: stage 2 has not finished, so nothing of it is graded")
    grade = evaluate or (lambda ph, arm, t: grade_run(ctx, ph, t, arm))
    return [grade(phase, arm, task) for arm in c.ARMS]


def grade_run(ctx: RunContext, phase: str, task: str, arm: str) -> dict[str, Any]:
    """The benchmark's verifier on one arm's final patch, in its own archived run (``grade-<arm>``)."""
    import evaluate as ev

    source = ctx.archive.latest(phase, task, arm)
    if source is None:
        raise RuntimeError(f"{task}: no finished {arm} run to grade")
    run = ctx.archive.start(phase, task, f"grade-{arm}", graded_run=source["run"])
    patch = source["path"] / "model.patch"
    try:
        found = ev.verify(lambda *a, timeout=None: ctx.docker(*a, timeout=timeout), ctx.assets.harness, task,
                          patch if patch.is_file() else None, run.path)
        status = "finished" if found.get("resolved") is not None else "error"
    except Exception as exc:  # noqa: BLE001
        found, status = {"error": f"{type(exc).__name__}: {exc}"[:600], "resolved": None}, "error"
    return run.finish({**found, "graded_run": source["run"], "released": bool((source["record"] or {}).get("released"))},
                      status)


# --- the batch ----------------------------------------------------------------------------------------------------------
def close_interrupted(archive: c.Archive) -> list[str]:
    """Runs started and never ended (a crash) are ended now as ``interrupted``, at the cost their stream shows. Their
    folders are kept; a run is never started twice."""
    closed = []
    for row in archive.runs():
        if row.get("status") != "running":
            continue
        folder = archive.root / row["run"]
        tally = Tally()
        stream = folder / "stream.jsonl"
        if stream.is_file():
            for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    tally.feed(obj)
        run = c.RunFolder(archive, folder, row["run"], row["phase"], row["task"], row["arm"])
        lesson = row.get("arm") == shadow.ARM            # no stream: its archived responses say what it cost
        cost = shadow.recorded_cost(folder) if lesson else tally.estimated_usd
        if (folder / "run.json").exists():
            archive._append({"event": "ended", "run": row["run"], "status": "interrupted", "at": c.now(),
                             "cost_usd": cost, "agent_started": tally.lines > 0})
        else:
            run.finish({"cost_usd": cost, "estimated_usd": cost,
                        "cost_source": "response_usage" if lesson else "estimated_from_stream",
                        "agent_started": tally.lines > 0, "model_calls": tally.model_calls}, "interrupted")
        closed.append(row["run"])
    return closed


def batch_tasks(ctx: RunContext, phase: str, ids: Sequence[str], *, run_task: Callable[..., dict] = run_task,
                grade: Callable[[str, str, str], Any] | None = None, jobs: int = 2) -> dict[str, Any]:
    """The tasks in their fixed order, ``jobs`` (1 or 2) at a time. A task is admitted only when its runs (three arms
    and the lesson) fit under the cap at their reservations (TASK_RESERVE), counting every admitted task not yet
    finished the same way.
    When the next task does not fit, the batch waits for tasks in flight; when nothing is in flight and it still does
    not fit, it stops. The tasks not reached are listed; the analysis covers completed tasks only."""
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    close_interrupted(ctx.archive)
    jobs = max(1, min(2, int(jobs)))
    queue_ = deque(t for t in ids if not attempted(ctx.archive, phase, t))
    reserved, stopped, done = 0.0, "", []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        running: dict[Any, str] = {}
        while queue_ and not stopped or running:
            while queue_ and len(running) < jobs and not stopped:
                ok, why = c.may_start(c.spend(ctx.archive.records()), phase, need=c.TASK_RESERVE, reserved=reserved,
                                      environ=ctx.environ)
                if not ok:
                    if not running:
                        stopped = why
                    break
                task = queue_.popleft()
                reserved += c.TASK_RESERVE
                running[pool.submit(run_task, ctx, phase, task, admitted=True, grade=grade)] = task
            if not running:
                continue
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                task = running.pop(future)
                reserved -= c.TASK_RESERVE
                try:
                    rec = future.result()
                except Exception as exc:  # noqa: BLE001
                    rec = {"status": "infra_error", "error": f"{type(exc).__name__}: {exc}"[:300]}
                done.append((task, rec.get("status")))
    completed = [t for t in ids if (ctx.archive.latest(phase, t, "task") or {}).get("record", {}) and
                 ctx.archive.latest(phase, t, "task")["record"].get("stage2_done")]
    return {"ran": done, "stopped": stopped, "tasks_completed": len(completed), "not_run_tasks": list(queue_),
            "jobs": jobs}


# --- the dry run and the probe (no model) ------------------------------------------------------------------------------
MCP_LIST = r"""
import json, subprocess, sys
p = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     env={"G6_HOME": "/opt/v7/g6home", "PATH": "/usr/bin:/bin"})
def send(obj):
    p.stdin.write((json.dumps(obj) + "\n").encode()); p.stdin.flush()
def recv(want):
    while True:
        line = p.stdout.readline()
        if not line:
            raise SystemExit("g6-mcp closed its output")
        msg = json.loads(line)
        if msg.get("id") == want:
            return msg
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
      "capabilities": {}, "clientInfo": {"name": "v7-probe", "version": "1"}}})
init = recv(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = recv(2)
p.terminate()
print(json.dumps({"server": init["result"].get("serverInfo"), "protocol": init["result"].get("protocolVersion"),
                  "tools": sorted(t["name"] for t in tools["result"]["tools"])}))
"""


def replay(ctx: RunContext, task: str, which: str) -> dict[str, Any]:
    """The dry run: a task container prepared as for stage 1, Claude Code and G6's runtime installed, the reference
    patch (``gold``) or nothing (``empty``) in place of the agent, the same capture, then the verifier. Tuning tasks
    only."""
    if task not in ctx.splits["dev"]:
        raise SystemExit("the dry run uses tuning tasks only")
    if which not in ("gold", "empty"):
        raise SystemExit("--patch is gold or empty")
    env = task_env(ctx.assets.harness, task)
    run = ctx.archive.start("dryrun", task, which, g6_wheel=ctx.wheel_record())
    tag, net, box, body, status = uuid.uuid4().hex[:10], None, None, {"agent_started": False, "cost_usd": 0.0}, "error"
    try:
        body["image_digest"] = ensure_image(ctx.docker, env.image)
        net = start_net(ctx.docker, env, ctx.assets, tag)
        box = start_box(ctx.docker, env, env.image, net, f"v7dry-{tag}")
        body["git"] = git_state(ctx.docker, box)
        body["claude_version"] = install_claude(ctx.docker, box, ctx.assets)
        body["g6_version"] = install_g6_runtime(ctx.docker, box, ctx.assets, ctx.wheel_path())
        assert_clean(ctx.docker, box, hidden_paths(env, box.repo))
        if which == "gold":
            copy_in(ctx.docker, box.agent, env.folder / "solution" / "gold_patch.diff", "/tmp/v7_gold.diff")
            checked(ctx.docker.sh(box.agent, GOLD_APPLY), "gold patch")
        body["patch_bytes"] = capture_patch(ctx.docker, box, run.fresh_path("model.patch"))
        body["patch_sha256"] = c.sha256_file(run.path / "model.patch")
        status = "finished"
    except Exception as exc:  # noqa: BLE001
        body["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        if box is not None:
            box.close()
        if net is not None:
            net.close()
    finished = run.finish({**body, "released": True}, status)
    if status == "finished":
        finished["grade"] = grade_run(ctx, "dryrun", task, which)
    return finished


def probe(ctx: RunContext, task: str) -> dict[str, Any]:
    """``claude --version``, G6's runtime and the g6-mcp tool list over stdio, the network rule, and Claude Code's
    flags (no key, a dead proxy: no request can leave), in one container. No model."""
    env = task_env(ctx.assets.harness, task)
    run = ctx.archive.start("dryrun", task, "probe", g6_wheel=ctx.wheel_record())
    tag, net, box, body, status = uuid.uuid4().hex[:10], None, None, {"agent_started": False, "cost_usd": 0.0}, "error"
    try:
        body["image_digest"] = ensure_image(ctx.docker, env.image)
        net = start_net(ctx.docker, env, ctx.assets, tag)
        box = start_box(ctx.docker, env, env.image, net, f"v7prb-{tag}")
        body["claude_version"] = install_claude(ctx.docker, box, ctx.assets)
        body["g6_version"] = install_g6_runtime(ctx.docker, box, ctx.assets, ctx.wheel_path())
        listed = ctx.docker("exec", "-i", box.agent, f"{G6PY}/bin/python3", "-I", "-", f"{G6PY}/bin/g6-mcp",
                            input=MCP_LIST.encode("utf-8"), timeout=300)
        body["mcp"] = json.loads(checked(listed, "g6-mcp tools/list").splitlines()[-1])
        direct = ctx.docker.sh(box.agent, f"{G6PY}/bin/python3 -I -c \"import socket; socket.create_connection(("
                                          "'1.1.1.1', 443), timeout=5)\"", timeout=60)
        via = ctx.docker.sh(box.agent, f"{G6PY}/bin/python3 -I -c \"import socket; s = socket.create_connection(("
                                       f"'{box.sidecar}', {PROXY_PORT}), timeout=5); s.sendall(b'CONNECT github.com:443 "
                                       "HTTP/1.1\\r\\nHost: github.com:443\\r\\n\\r\\n'); print(s.recv(64))\"",
                            timeout=60)
        body["network"] = {"direct_connect_refused": direct.returncode != 0, "proxy_reply_to_github": out(via)[:80],
                           "egress": egress_log(ctx.docker, net, run.fresh_path("egress.jsonl"))}
        flags = ctx.docker("exec", "-i", "-u", "root", "-w", box.repo, "-e", "HTTPS_PROXY=http://127.0.0.1:9",
                           "-e", "HTTP_PROXY=http://127.0.0.1:9", "-e", "IS_SANDBOX=1", "-e", "DISABLE_AUTOUPDATER=1",
                           "-e", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", box.agent, "timeout", "90",
                           *claude_args(max_turns=1, max_usd=0.01), input=b"Reply with OK.", timeout=150)
        said = (out(flags) + " " + (flags.stderr or b"").decode("utf-8", errors="replace")).strip()
        run.write("flags_check.txt", said)
        body["flags_check"] = {"exit": flags.returncode, "unknown_option": "unknown option" in said.lower()}
        status = "finished"
    except Exception as exc:  # noqa: BLE001
        body["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        if box is not None:
            box.close()
        if net is not None:
            net.close()
    return run.finish(body, status)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("build-wheel", "probe", "replay", "pilot", "batch", "grade"))
    ap.add_argument("--archive", type=Path, default=None, help="default: V7_ARCHIVE, else " + str(c.DEFAULT_ARCHIVE))
    ap.add_argument("--assets", type=Path, help="assets.json (see README.md)")
    ap.add_argument("--splits", type=Path, default=HERE.parents[1] / "docs/validation/results/V7/splits.json")
    ap.add_argument("--phase", default="eval", choices=("eval", "pilot", "dryrun"))
    ap.add_argument("--task")
    ap.add_argument("--patch", choices=("gold", "empty"))
    ap.add_argument("--jobs", type=int, default=2, choices=(1, 2))
    ap.add_argument("--ref", help="build-wheel: the git ref G6's wheel is built from")
    args = ap.parse_args(argv)
    archive = c.Archive(args.archive or c.archive_root())
    if args.command == "build-wheel":
        if not args.ref:
            raise SystemExit("--ref names the branch or tag the wheel is built from")
        print(json.dumps(build_wheel(args.ref, archive), indent=1))
        return 0
    assets = Assets.load(args.assets)
    problems = assets.verify()
    if problems:
        raise SystemExit("; ".join(problems))
    ctx = RunContext(archive, assets, c.load_splits(args.splits), wheel=wheel_info(archive))
    grade = lambda phase, arm, task: grade_run(ctx, phase, task, arm)  # noqa: E731
    if args.command == "replay":
        result = replay(ctx, args.task, args.patch)
    elif args.command == "probe":
        result = probe(ctx, args.task)
    elif args.command == "grade":
        result = grade_task(ctx, args.phase, args.task)
    elif args.command == "pilot":
        result = batch_tasks(ctx, "pilot", ctx.splits["pilot"], grade=grade, jobs=args.jobs)
    else:
        result = batch_tasks(ctx, "eval", ctx.splits["scored"], grade=grade, jobs=args.jobs)
    print(json.dumps(result, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
