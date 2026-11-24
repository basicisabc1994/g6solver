# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 6: the allow-list inventory admits any writer reached through an imported name.

Review 3's finding 7 at its class (another writer shape). ``test_completion_writer_allowlist.unguarded_calls`` returns
"allowed" for every call whose root is an imported name other than os, shutil, tempfile, pathlib, json and builtins
("collaborators"). The three completion modules import ``cloud``, ``keys``, ``repo_tests``, ``host_models`` and more, so in
those modules the retained writer the gate exists to stand in front of passes when called directly, and so does a ``Path``
any collaborator hands back. ``exec`` and ``eval`` pass as plain names. No such writer exists in the three modules today.
"""
import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls

HEAD = ("from g6.core import cloud_consent as cloud, keys\n"
        "from g6.workforce import repo_tests\n"
        "import io, importlib, subprocess, sqlite3\n")


@pytest.mark.parametrize("body", ["p.write_text('x')", "os.replace(a, b)", "open(p, 'w')", "getattr(p, 'write_text')('x')"])
def test_control_the_inventory_refuses_a_raw_writer_beside_those_imports(body):
    assert unguarded_calls(HEAD + "import os\n" + body), body


@pytest.mark.parametrize("body", [
    "cloud.write_retained(session / 'state.json', body)",      # the ungated producer itself, as the hook called it at a3d8927a
    "keys.path().write_text('G6_LOOP_CRITIC=codex')",          # a Path from a collaborator: here the person's key file
    "repo_tests.Path(p).unlink()",
    "io.open(p, 'w')",
    "importlib.import_module('shutil').rmtree(p)",
    "subprocess.run(['git', 'clean', '-fdx'], cwd=repo)",
    "sqlite3.connect(p)",
    "exec('open(p, \"w\")')",
])
def test_a_writer_reached_through_an_imported_name_fails_the_inventory(body):
    assert unguarded_calls(HEAD + body), f"the inventory passes a completion module that calls {body!r}"
