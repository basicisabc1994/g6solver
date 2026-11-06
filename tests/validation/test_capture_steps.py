# SPDX-License-Identifier: Apache-2.0
"""Every step of the capture a patch is kept by is read, and the first that fails decides (replay cohort, repair 3, review 3's
finding 1; class ``failure_read_as_success``, met in both earlier repairs).

The capture script (``run.diff_script``) ignored a failed ``git add`` and returned the status of the cleanup after it, so a changed
file vanished from the kept patch and ``keep_final`` said the patch was read. Each step now prints its own status on a line of its
own, a step runs only when every step before it passed, the cleanup always runs, and the script's own status is its first failing
step's. The kept patch and the known patch are read by their steps through the one reader (``run.capture_why``: ``box_mcp.seen``
and ``box_mcp.exit_status``): read only when every step was seen to pass, otherwise not read, the failing step named. The
inventory test lists, from the code, every place the capture runs and how its result is read.

The real-git cases run the script under a POSIX shell (``sh`` on the path, or Git for Windows' own), with real git, on a scratch
repository; no docker, no model, no network."""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(HERE))

import cohorts  # noqa: E402
import run as we  # noqa: E402

STEPS = getattr(we, "CAPTURE_STEPS", ("index", "cd", "read-tree", "add", "diff", "cleanup"))
STEP = getattr(we, "STEP", "we-step")
WORK = [s for s in STEPS if s != "cleanup"]


def posix_sh() -> str | None:
    """A POSIX shell: ``sh`` on the path, or the one Git for Windows ships beside its git."""
    found = shutil.which("sh")
    if found:
        return found
    git = shutil.which("git")
    if git:
        for up in Path(git).resolve().parents:
            candidate = up / "usr" / "bin" / "sh.exe"
            if candidate.is_file():
                return str(candidate)
    return None


SH = posix_sh()
real_shell = pytest.mark.skipif(SH is None or shutil.which("git") is None, reason="no POSIX shell or no git on this machine")


class Repo(SimpleNamespace):
    """A scratch git repository with ``mod.py`` committed, the base commit, and the environment its captures run in."""


def scratch(tmp_path: Path, *, broken_filter: bool = False) -> Repo:
    repo = tmp_path / "repo"
    repo.mkdir()
    shell_bin = str(Path(SH).parent) if SH else ""
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(tmp_path),
           "TMPDIR": tmp_path.as_posix(), "PATH": shell_bin + os.pathsep + os.environ.get("PATH", "")}

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, check=True,
                              timeout=30).stdout.decode().strip()

    git("init", "-q")
    if broken_filter:
        (repo / ".gitattributes").write_text("*.py filter=broken\n", encoding="utf-8")
    (repo / "mod.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=review", "-c", "user.email=review@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    if broken_filter:
        git("config", "filter.broken.clean", "false")
        git("config", "filter.broken.required", "true")
    return Repo(path=repo, base=base, env=env, git=git)


class ShellDocker:
    """The agent's container as the scratch repository: each script runs under the real POSIX shell with real git (``sh -c``),
    and ``docker cp`` copies a file the script wrote out to the host."""

    def __init__(self, repo: Repo) -> None:
        self.repo, self.scripts = repo, []

    def sh(self, ctr, script, *a, **kw):
        self.scripts.append(script)
        return subprocess.run([SH, "-c", script], cwd=self.repo.path, env=self.repo.env, capture_output=True, timeout=60)

    def __call__(self, *args, **kw):
        assert args[0] == "cp"
        src = Path(args[1].split(":", 1)[1])
        if not src.is_file():
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"no such file")
        shutil.copyfile(src, args[2])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def keep(tmp_path: Path, repo: Repo, monkeypatch, *, task_repo: str | None = None, base: str | None = None,
         box: Path | None = None) -> tuple[bool, Path, dict]:
    """``run.keep_final`` on the scratch repository, its capture under the real shell; (read, the kept file, the record).
    ``box``: where the capture writes (``run.WE``), a folder made here unless one is given."""
    if box is None:
        box = tmp_path / "box-output"
        box.mkdir(exist_ok=True)
    monkeypatch.setattr(we, "WE", box.as_posix())
    folder = we.c.Archive(tmp_path / "archive").start("run", "toy-1")
    record: dict = {}
    try:
        read = we.keep_final(SimpleNamespace(docker=ShellDocker(repo)), SimpleNamespace(repo=task_repo or repo.path.as_posix()),
                             "agent", base or repo.base, folder, record)
    except TypeError:            # the code before repair 3: keep_final takes no record
        read = we.keep_final(SimpleNamespace(docker=ShellDocker(repo)), SimpleNamespace(repo=task_repo or repo.path.as_posix()),
                             "agent", base or repo.base, folder)
    return read, folder.path / "final.patch", record


# --- review 3's finding 1, with real git under a real shell ------------------------------------------------------------------
@real_shell
def test_review_3_finding_1_a_failed_git_add_is_never_a_successfully_read_final_patch(tmp_path, monkeypatch):
    repo = scratch(tmp_path, broken_filter=True)
    (repo.path / "mod.py").write_text("value = 2\n", encoding="utf-8")
    failed = subprocess.run(["git", "-C", str(repo.path), "add", "-A"], env=repo.env, capture_output=True, timeout=30)
    assert failed.returncode != 0 and b"clean filter" in failed.stderr       # git's own filter failure, the one injected
    read, kept, record = keep(tmp_path, repo, monkeypatch)
    assert kept.read_bytes() == b""
    assert read is False, "failed git add was certified as a successfully read empty patch"
    assert record["final_patch_why"] == "the capture's step add exited 128", record


@real_shell
def test_a_capture_whose_every_step_passes_is_read_and_holds_the_change(tmp_path, monkeypatch):
    repo = scratch(tmp_path)
    (repo.path / "mod.py").write_text("value = 2\n", encoding="utf-8")
    (repo.path / "new.py").write_text("made = 1\n", encoding="utf-8")
    read, kept, record = keep(tmp_path, repo, monkeypatch)
    assert read is True and record["final_patch_why"] == ""
    patch = kept.read_bytes()
    assert b"+value = 2" in patch and b"+made = 1" in patch and b"new file mode" in patch
    assert repo.git("diff", "--cached", "--name-only") == "", "the capture touched the repository's own index"


@real_shell
@pytest.mark.parametrize("step", ["index", "cd", "read-tree", "add", "diff"])
def test_each_step_that_fails_on_real_git_leaves_the_patch_unread_and_names_that_step(tmp_path, monkeypatch, step):
    repo = scratch(tmp_path, broken_filter=step == "add")
    (repo.path / "mod.py").write_text("value = 2\n", encoding="utf-8")
    kw: dict = {}
    if step == "index":
        repo.env["TMPDIR"] = (tmp_path / "no-such-folder").as_posix()          # mktemp cannot make the separate index
    elif step == "cd":
        kw["task_repo"] = (tmp_path / "no-such-repo").as_posix()
    elif step == "read-tree":
        kw["base"] = "f" * 40                                                     # no such commit
    elif step == "diff":
        kw["box"] = tmp_path / "no-such-box"                                      # the diff's own file cannot be written
    read, kept, record = keep(tmp_path, repo, monkeypatch, **kw)
    assert read is False and kept.read_bytes() == b""
    assert record["final_patch_why"].startswith(f"the capture's step {step} exited "), record


@real_shell
def test_the_scripts_own_status_is_its_first_failing_steps_so_a_reader_of_the_status_alone_never_takes_the_cleanups(tmp_path):
    repo = scratch(tmp_path, broken_filter=True)
    (repo.path / "mod.py").write_text("value = 2\n", encoding="utf-8")
    done = ShellDocker(repo).sh("agent", we.diff_script(repo.path.as_posix(), repo.base, (tmp_path / "p.patch").as_posix()))
    assert done.returncode == 128, done
    assert done.stdout.decode().split("\n")[:-1] == [f"{STEP} {s}=0" for s in ("index", "cd", "read-tree")] + \
        [f"{STEP} add=128", f"{STEP} cleanup=0"]
    with pytest.raises(RuntimeError):
        we.checked(done, "the critic's patch")                 # run_critic's reading of the same script


@real_shell
def test_the_round_hook_still_prints_nothing_and_exits_0_whatever_a_step_does(tmp_path, monkeypatch):
    for broken in (False, True):
        folder = tmp_path / ("broken" if broken else "plain")
        folder.mkdir()
        repo = scratch(folder, broken_filter=broken)
        (repo.path / "mod.py").write_text("value = 2\n", encoding="utf-8")
        (folder / "box" / "rounds").mkdir(parents=True)
        monkeypatch.setattr(we, "WE", (folder / "box").as_posix())
        done = subprocess.run([SH, "-c", we.hook_script(repo.path.as_posix(), repo.base)], cwd=repo.path, env=repo.env,
                              input=b"{}", capture_output=True, timeout=60)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b""), done
        saved = folder / "box" / "rounds" / "round-1.patch"
        assert saved.is_file() is not broken, "a step that failed still wrote a patch, one missing what the failed step held"
        assert broken or b"+value = 2" in saved.read_bytes()


# --- every step, failing or not seen, by the lines the capture prints ------------------------------------------------------
def said(**statuses: int) -> bytes:
    return "".join(f"{STEP} {name}={status}\n" for name, status in statuses.items()).encode()


ALL_PASS = said(**{s: 0 for s in STEPS})


class SaidDocker:
    """A container whose capture answers ``output`` with ``code``, and whose copy writes ``patch``."""

    def __init__(self, output: bytes, code, patch: bytes = b"a patch\n") -> None:
        self.output, self.code, self.patch = output, code, patch

    def sh(self, *a, **kw):
        return SimpleNamespace(returncode=self.code, stdout=self.output, stderr=b"")

    def __call__(self, *args, **kw):
        Path(args[2]).write_bytes(self.patch)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def kept_by(tmp_path: Path, output: bytes, code) -> tuple[bool, bytes, str]:
    folder = we.c.Archive(tmp_path / "archive").start("run", "toy-1")
    record: dict = {}
    read = we.keep_final(SimpleNamespace(docker=SaidDocker(output, code)), SimpleNamespace(repo="/w/toy"), "agent", "base", folder,
                         record)
    return read, (folder.path / "final.patch").read_bytes(), record["final_patch_why"]


def test_a_capture_seen_to_pass_every_step_is_read(tmp_path):
    assert kept_by(tmp_path, ALL_PASS, 0) == (True, b"a patch\n", "")


@pytest.mark.parametrize("failing", STEPS)
def test_each_step_failing_leaves_the_patch_unread_and_is_named(tmp_path, failing):
    statuses = {s: 0 for s in WORK[:WORK.index(failing) + 1]} if failing != "cleanup" else {s: 0 for s in WORK}
    statuses[failing] = 1
    statuses.setdefault("cleanup", 0)
    for code in (1, 0):                               # the script's own status, and a status that hides it: the lines decide
        read, kept, why = kept_by(tmp_path / f"c{code}", said(**statuses), code)
        assert (read, kept, why) == (False, b"", f"the capture's step {failing} exited 1"), (failing, code)


UNSEEN = {
    "no line at all": (b"", 0),
    "a step's line missing": (said(**{s: 0 for s in STEPS if s != "add"}), 0),
    "the cleanup's line missing": (said(**{s: 0 for s in WORK}), 0),
    "a status that is no integer written": (ALL_PASS.replace(b"add=0", b"add=x"), 0),
    "an empty status": (ALL_PASS.replace(b"add=0", b"add="), 0),
    "a line twice": (ALL_PASS + f"{STEP} add=0\n".encode(), 0),
    "out of order": (said(**{s: 0 for s in ("cd", "index", "read-tree", "add", "diff", "cleanup")}), 0),
    "a line the script never prints": (ALL_PASS + b"something else\n", 0),
    "a step that ran after one failed": (said(index=0, cd=0, **{"read-tree": 1}, add=0, cleanup=0), 1),
    "no exit status came back": (ALL_PASS, None),
    "the call was ended by a signal": (ALL_PASS, -9),
}


@pytest.mark.parametrize("case", UNSEEN)
def test_a_step_whose_status_is_not_seen_leaves_the_patch_unread(tmp_path, case):
    output, code = UNSEEN[case]
    read, kept, why = kept_by(tmp_path, output, code)
    assert (read, kept) == (False, b"") and why.startswith("the capture's steps were not seen"), (case, why)


def test_a_copy_not_seen_to_pass_leaves_the_patch_unread(tmp_path):
    class NoCopy(SaidDocker):
        def __call__(self, *args, **kw):
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"no such file")
    folder = we.c.Archive(tmp_path / "archive").start("run", "toy-1")
    record: dict = {}
    assert we.keep_final(SimpleNamespace(docker=NoCopy(ALL_PASS, 0)), SimpleNamespace(repo="/w/toy"), "a", "b", folder, record) is False
    assert "copy" in record["final_patch_why"] and (folder.path / "final.patch").read_bytes() == b""


@pytest.mark.parametrize("output", [said(index=0, cd=0, **{"read-tree": 0}, add=128, cleanup=0), b""])
def test_the_known_patch_is_read_by_the_same_steps_and_a_step_not_seen_to_pass_stops_the_round_trip(tmp_path, output):
    class KnownDocker:
        def __call__(self, *args, **kw):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        def sh(self, ctr, script, *a, **kw):
            if f"{STEP} add=" in script:                                      # the capture
                return SimpleNamespace(returncode=128 if output else 0, stdout=output, stderr=b"")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    task = SimpleNamespace(id="toy-1", repo="/w/toy", parent="0" * 40)
    ctx = SimpleNamespace(docker=KnownDocker(), g6={"commit": "0" * 40}, variant=we.ROUND3)
    with pytest.raises(RuntimeError, match="the known patch's capture: the capture's step"):
        cohorts.known_patch(ctx, task, tmp_path / "gold.patch", tmp_path / "known.patch")


# --- the inventory: every place the capture runs, and how its result is read --------------------------------------------------
#: Each function that runs the capture (``diff_script``), by (module, function), with how its result is read. "capture_why":
#: through the one reading of its steps, as a patch the controls or the round trip read is kept. Any other: why it may read less.
CAPTURES = {
    ("run.py", "keep_final"): "capture_why",
    ("cohorts.py", "known_patch"): "capture_why",
    ("run.py", "hook_script"): "the round hook, inside the agent's container: nothing reads its output or status (it prints "
                               "nothing and exits 0); the study's round patches",
    ("run.py", "keep_first_patch"): "the study's round patches before a review: their result is never read",
    ("run.py", "kept"): "K3's baseline captures, the study's: its status (.returncode), which is the first failing step's",
    ("run.py", "run_critic"): "the study's critic's patch: run.checked, which raises on the script's status, the first failing "
                              "step's",
    ("run.py", "reviews_with_bug_test.tree"): "the study's bug-test loop: a digest of the tree compared before and after a repair",
}


def _functions(path: Path) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[".".join([*where, child.name])] = child
                visit(child, [*where, child.name])
            elif isinstance(child, ast.ClassDef):
                visit(child, [*where, child.name])
            else:
                visit(child, where)
    visit(ast.parse(path.read_text("utf-8")), [])
    return found


def _calls(fn: ast.AST, name: str) -> bool:
    """Whether ``fn``'s own body (not a nested function's) calls ``name``, or hands it on by name (as ``seen``'s read)."""
    def visit(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call) and (getattr(child.func, "attr", None) or getattr(child.func, "id", None)) == name:
                return True
            if isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, ast.Load):
                return True
            if visit(child):
                return True
        return False
    return visit(fn)


def test_every_place_the_capture_runs_is_listed_and_each_patch_the_controls_read_is_read_by_its_steps():
    callers, readers, steps_read = set(), set(), set()
    for path in sorted(HERE.glob("*.py")):
        for name, fn in _functions(path).items():
            if _calls(fn, "diff_script") and name != "diff_script":
                callers.add((path.name, name))
            if _calls(fn, "capture_why"):
                readers.add((path.name, name))
            if _calls(fn, "capture_steps"):
                steps_read.add((path.name, name))
    assert callers == set(CAPTURES), ("a capture not listed, or one listed that is gone", sorted(callers - set(CAPTURES)),
                                      sorted(set(CAPTURES) - callers))
    through = {key for key, how in CAPTURES.items() if how == "capture_why"}
    assert readers == through, ("the capture's steps read somewhere else, or a listed reader that does not read them",
                                sorted(readers ^ through))
    assert steps_read == {("run.py", "capture_why")}, sorted(steps_read)      # its steps' lines, by one reading only
    fns = _functions(HERE / "run.py")
    assert _calls(fns["capture_why"], "seen") and _calls(fns["capture_steps"], "exit_status"), \
        "the capture is read by the one reader, and each step's status by the one reader of an exit status"
    script = we.diff_script("/w/r", "b" * 40, "/x/final.patch")
    for step in STEPS:                                                       # every step prints its own status
        assert script.count(f"{STEP} {step}=") == 1, step
    assert script.splitlines()[-1] == '(exit "$we_st")', "the script's own status is not its first failing step's"
