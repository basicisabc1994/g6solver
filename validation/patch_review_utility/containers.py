# SPDX-License-Identifier: Apache-2.0
"""Each arm in its task's own benchmark image (the study's production environment). The image is never changed:
every arm gets a fresh container, removed when it ends, whose repository at ``/app`` is the image's own at the base
commit, with the patch applied inside the container before anything runs.

**The change** (``stage``): the arm's tree on the host (the base commit with the patch, and for the labels the hidden
tests, in its working tree) is turned into one binary diff against ``HEAD``, read through a temporary git index so the
tree itself is never touched, beside ``apply.sh``. In the container, ``apply.sh`` checks that ``/app`` is at the
same commit, applies the diff (``git apply``, else ``patch``), then runs the arm's command.

**Arm 1 and the labels** (``tests_command``): the pristine image, with no toolchain, the network closed, and pytest
run by the image's own Python with the study's outcome plugin (``executor.PLUGIN``) from ``/v9``.

**The arms that run G6** (``g6_command``: arm 3, arm 4): the same image with G6's pinned toolchain mounted read-only at
``/opt/g6`` (``environment/toolchain.py``), the study's scripts read-only at ``/opt/v9``, ``PATH`` with the
toolchain's ``bin`` (git and bubblewrap only) first, ``G6_REPO_PYTHON`` naming the image's Python for the
repository's tests, ``G6_EXPECT_BACKEND=bubblewrap``, and the flags bubblewrap needs inside a container. The research
flag is never passed. G6's own Python runs ``child.py`` on ``/app``.

Both run only an image already on the computer (`--pull never`): an image is never fetched during a study.

Placeholders, filled per run: ``{container}`` (a name new to each run, so a run past its limit is stopped by name),
``{support}`` or ``{job}`` (the host folder mounted at ``/v9``), ``{base}`` (the commit ``/app`` must be at) and
``{tests}``.
"""
from __future__ import annotations

import os
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
#: Run first in every container: the repository must be at the base commit; then the change; then the arm's command.
APPLY = """\
#!/bin/sh
set -eu
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


def tests_command(image: str, *, python: str = "python") -> list[str]:
    """Arm 1's and the labels' command template: the pristine image, no toolchain, the network closed."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", "{container}", "--network", "none",
            "-v", f"{{support}}:{WORK}",
            "-e", f"V9_OUTCOMES={WORK}/outcomes.json", "-e", "V9_BASE={base}", image, "sh", f"{WORK}/apply.sh",
            python, "-c", PYTEST_MAIN, "-q", "-p", "no:cacheprovider", "-p", "v9_outcomes", "{tests}"]


def g6_command(image: str, *, toolchain: str, study: str | Path, repo_python: str) -> list[str]:
    """The command template for the arms that run G6: ``toolchain`` is the toolchain's volume, ``study`` the folder
    holding the study's scripts (``validation/``), ``repo_python`` the image's own interpreter."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", "{container}", *CONFINED_FLAGS,
            "-v", f"{toolchain}:{TOOLCHAIN_MOUNT}:ro", "-v", f"{study}:{STUDY_MOUNT}:ro", "-v", f"{{job}}:{WORK}",
            "-e", f"PATH={TOOLCHAIN_MOUNT}/bin:{SYSTEM_PATH}", "-e", f"G6_REPO_PYTHON={repo_python}",
            "-e", "G6_EXPECT_BACKEND=bubblewrap", "-e", "V9_BASE={base}", image, "sh", f"{WORK}/apply.sh",
            G6_PYTHON, "-I", f"{STUDY_MOUNT}/patch_review_utility/child.py", f"{WORK}/job.json", f"{WORK}/reply.json"]
