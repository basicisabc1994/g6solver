# SPDX-License-Identifier: Apache-2.0
"""Stage checker files in a task workspace and use the existing confined runner."""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from g6.rsi.records import TaskError
from g6.workforce import repo_tests
from g6.workspace._files import Workspace


def _script(checks):
    preamble = "from pathlib import Path\n\nSOLUTION = Path(__file__).with_name('solution.py')\n\n"
    if checks.kind == "function":
        return preamble + (
            "def test_task():\n"
            "    namespace = {'__name__': '__task__'}\n"
            "    exec(compile(SOLUTION.read_text(encoding='utf-8'), str(SOLUTION), 'exec'), namespace)\n"
            f"    for text in {checks.tests!r}:\n"
            "        exec(compile(text, '<task-check>', 'exec'), namespace)\n"
        )
    return preamble + (
        "import contextlib\nimport io\nimport runpy\nfrom unittest.mock import patch\n\n"
        "def test_task():\n"
        f"    for given, expected in {tuple(zip(checks.inputs, checks.outputs))!r}:\n"
        "        output = io.StringIO()\n"
        "        with contextlib.redirect_stdout(output), patch('sys.stdin', io.StringIO(given)):\n"
        "            try:\n"
        "                runpy.run_path(str(SOLUTION), run_name='__main__')\n"
        "            except SystemExit as exc:\n"
        "                assert exc.code is None or exc.code == 0\n"
        "        assert output.getvalue().replace('\\r\\n', '\\n') == expected.replace('\\r\\n', '\\n')\n"
    )


def run_task_checks(task, solution: str, *, repo: str | Path, timeout_s: int = 30) -> dict:
    from g6.recursion.tasks import TaskSpec

    task = TaskSpec.from_record(task.to_record())
    if type(solution) is not str or not solution.strip():
        raise TaskError("G6_E_RSI_CHECK_INVALID")
    root = Path(repo).resolve()
    key = hashlib.sha256((task.provenance.content_digest + solution).encode("utf-8")).hexdigest()[:24]
    workspace = Workspace.for_task("rsi-" + key + "-" + secrets.token_hex(8))
    staged = workspace.root.resolve()
    if not staged.is_relative_to(root) or staged == root:
        raise TaskError("G6_E_RSI_WORKSPACE_REFUSED")
    workspace.write("solution.py", solution, dry_run=False)
    workspace.write("test_task.py", _script(task.public_tests), dry_run=False)
    test = (staged / "test_task.py").relative_to(root).as_posix()
    return repo_tests.run(root, [test], timeout_s=timeout_s, network=False, require_isolation=True)
