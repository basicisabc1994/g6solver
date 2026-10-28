# SPDX-License-Identifier: Apache-2.0
"""Each arm in its task's own benchmark image (the study's production environment). The image is never changed:
every arm gets a fresh container, removed when it ends, whose repository at ``/app`` is the image's own at the base
commit, with the patch applied inside the container before anything runs.

**The change** (``stage``): the arm's tree on the host (the base commit with the patch, and for the labels the hidden
tests, in its working tree) is turned into one binary diff against ``HEAD``, read through a temporary git index so the
tree itself is never touched, beside ``apply.sh``. In the container, ``apply.sh`` checks that ``/app`` is at the
same commit, applies the diff (``git apply``, else ``patch``), then runs the arm's command.

**One test environment.** Each repository has one frozen test environment (its manifest declares it once), the same
for arm 1, the labels and G6's arms; it may not name a runner, interpreter or loader setting (``test_env_problems``).
Every container first clears the image's own ``PYTEST_*`` variables, ``PYTEST_ADDOPTS`` included
(INTERPRETATIONS.md item 18). Images are named by digest (``pinned``).

**Arm 1 and the labels** (``tests_command``): the pristine image, with no toolchain, the network closed, the test
environment set in the container, and pytest run by the image's own Python with the study's outcome plugin
(``executor.PLUGIN``) from ``/v9``.

**The arms that run G6** (``g6_command``: arm 3, arm 4): the same image with G6's pinned toolchain mounted read-only at
``/opt/g6`` (``environment/toolchain.py``), the study's scripts read-only at ``/opt/v9``, ``PATH`` with the
toolchain's ``bin`` (git and bubblewrap only) first, ``G6_REPO_PYTHON`` naming the image's Python for the
repository's tests, ``G6_REPO_ENV`` carrying the test environment for G6 to apply inside its confined runs,
``G6_EXPECT_BACKEND=bubblewrap``, and the flags bubblewrap needs inside a container. The research flag is never
passed. G6's own Python runs ``child.py`` on ``/app``.

Both run only an image already on the computer (`--pull never`): an image is never fetched during a study.

Placeholders, filled per run: ``{container}`` (a name new to each run, so a run past its limit is stopped by name),
``{support}`` or ``{job}`` (the host folder mounted at ``/v9``), ``{base}`` (the commit ``/app`` must be at) and
``{tests}``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = "/app"
WORK = "/v9"
TOOLCHAIN_MOUNT = "/opt/g6"
STUDY_MOUNT = "/opt/v9"
G6_PYTHON = f"{TOOLCHAIN_MOUNT}/python/bin/python3"
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
#: What bubblewrap needs inside a container (the flags G6's confined tests use), for the arms that run G6 only.
CONFINED_FLAGS = ("--network", "none", "--cap-add", "SYS_ADMIN", "--security-opt", "seccomp=unconfined",
                  "--security-opt", "apparmor=unconfined")
#: Run first in every container: the image's own pytest settings cleared (``PYTEST_ADDOPTS`` and every other
#: ``PYTEST_*`` variable it sets: one frozen test environment for every arm, INTERPRETATIONS.md item 18); the
#: repository must be at the base commit; then the change; then the arm's command.
APPLY = """\
#!/bin/sh
set -eu
for name in $(env | sed -n 's/^\\(PYTEST_[A-Za-z0-9_]*\\)=.*/\\1/p'); do
    unset "$name"
done
cd /app
here=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if [ "$here" != "$V9_BASE" ]; then
    echo "the image's repository is at $here, not the base commit $V9_BASE" >&2
    exit 97
fi
if [ -s /v9/change.diff ]; then
    git apply --whitespace=nowarn /v9/change.diff 2>/dev/null || patch --batch --forward -p1 -i /v9/change.diff
fi
exec "$@"
"""
#: pytest started with the study's outcome plugin importable from /v9, whatever the image sets in PYTHONPATH.
PYTEST_MAIN = "import sys; sys.path.insert(0, '/v9'); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
_GIT = ("git", "-c", "core.autocrlf=false", "-c", "core.quotepath=off")


def stage(tree: str | Path, folder: str | Path) -> str:
    """Write ``tree``'s change against ``HEAD`` (``change.diff``) and ``apply.sh`` into ``folder``; the base commit."""
    tree, folder = Path(tree), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v9-index-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        def git(*args: str) -> bytes:
            return subprocess.run([*_GIT, "-C", str(tree), *args], env=env, check=True, capture_output=True).stdout

        base = git("rev-parse", "HEAD").decode().strip()
        git("read-tree", "HEAD")
        git("add", "-A")
        change = git("diff", "--cached", "--binary", "--no-color", "--no-ext-diff", "--no-textconv", "HEAD")
    (folder / "change.diff").write_bytes(change)
    (folder / "apply.sh").write_text(APPLY, encoding="utf-8", newline="\n")
    return base


#: Names a repository's test environment may not set, as G6's ``G6_REPO_ENV`` refuses them: runner and interpreter
#: settings, the library loader's, the search path, the home folder, and G6's own.
REFUSED_PREFIXES = ("PYTEST_", "PYTHON", "LD_", "G6_")
REFUSED_NAMES = frozenset({"PATH", "HOME"})
_DIGEST = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


def test_env_problems(env: dict[str, str] | None) -> list[str]:
    """Why ``env`` cannot be a repository's frozen test environment: each name it may not set."""
    return [f"{name} may not be set in a repository's test environment" for name in sorted(env or {})
            if name.upper() in REFUSED_NAMES or name.upper().startswith(REFUSED_PREFIXES)]


def root_problems(roots: list[str] | None) -> list[str]:
    """Why ``roots`` cannot be a repository's import roots: each one that is not a folder path inside it."""
    return [f"import root {root!r} is not a relative path inside the repository" for root in roots or []
            if not isinstance(root, str) or not root or root.startswith(("/", "\\")) or ":" in root
            or ".." in root.replace("\\", "/").split("/")]


def _python_path(roots: list[str] | None) -> list[str]:
    """``PYTHONPATH`` putting the repository's import roots first (``lib`` for a ``lib/`` layout installed elsewhere),
    for a container that runs the tests itself."""
    if not roots:
        return []
    paths = [REPOSITORY if root.strip("/") in ("", ".") else f"{REPOSITORY}/{root.strip('/')}" for root in roots]
    return ["-e", "PYTHONPATH=" + ":".join(paths)]


def pinned(image: str) -> bool:
    """Whether ``image`` is named by its digest (``name@sha256:<64 hexadecimal digits>``), never by a tag."""
    return bool(_DIGEST.fullmatch(image))


def repo_env_flag(env: dict[str, str]) -> str:
    """G6's setting for the repository's test environment, which G6 applies inside its confined runs: one JSON
    object, names in order. (The encoding is this study's; it is re-synced when G6's ``G6_REPO_ENV`` lands.)"""
    return "G6_REPO_ENV=" + json.dumps(dict(sorted(env.items())), separators=(",", ":"))


def _env(env: dict[str, str] | None) -> list[str]:
    """The repository's frozen test environment as ``-e`` flags in name order, for a container that runs the tests
    itself (arm 1, the labels)."""
    return [word for name in sorted(env or {}) for word in ("-e", f"{name}={env[name]}")]


def tests_command(image: str, *, python: str = "python", env: dict[str, str] | None = None,
                  import_roots: list[str] | None = None) -> list[str]:
    """Arm 1's and the labels' command template: the pristine image, no toolchain, the network closed, with the
    repository's test environment ``env`` and its ``import_roots`` first on the import path."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", "{container}", "--network", "none",
            "-v", f"{{support}}:{WORK}", *_env(env), *_python_path(import_roots),
            "-e", f"V9_OUTCOMES={WORK}/outcomes.json", "-e", "V9_BASE={base}", image, "sh", f"{WORK}/apply.sh",
            python, "-c", PYTEST_MAIN, "-q", "-p", "no:cacheprovider", "-p", "v9_outcomes", "{tests}"]


def g6_command(image: str, *, toolchain: str, study: str | Path, repo_python: str,
               env: dict[str, str] | None = None) -> list[str]:
    """The command template for the arms that run G6: ``toolchain`` is the toolchain's volume, ``study`` the folder
    holding the study's scripts (``validation/``), ``repo_python`` the image's own interpreter, ``env`` the
    repository's frozen test environment, passed to G6 through ``G6_REPO_ENV`` (``repo_env_flag``), never set in the
    container itself."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", "{container}", *CONFINED_FLAGS,
            "-v", f"{toolchain}:{TOOLCHAIN_MOUNT}:ro", "-v", f"{study}:{STUDY_MOUNT}:ro", "-v", f"{{job}}:{WORK}",
            *(["-e", repo_env_flag(env)] if env else []),
            "-e", f"PATH={TOOLCHAIN_MOUNT}/bin:{SYSTEM_PATH}", "-e", f"G6_REPO_PYTHON={repo_python}",
            "-e", "G6_EXPECT_BACKEND=bubblewrap", "-e", "V9_BASE={base}", image, "sh", f"{WORK}/apply.sh",
            G6_PYTHON, "-I", f"{STUDY_MOUNT}/patch_review_utility/child.py", f"{WORK}/job.json", f"{WORK}/reply.json"]
