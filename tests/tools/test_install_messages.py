# SPDX-License-Identifier: Apache-2.0
"""Every install command G6 prints or documents names where G6 really comes from.

G6 is not on PyPI (owner, 2026-10-27: held until the trust fixes land), so ``pip install "g6[...]"`` would fetch
whatever someone else uploads under that name. Until the first upload, each command installs from G6's own
repository: ``pip install "g6[extra] @ git+https://github.com/basicisabc1994/g6solver"``.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
SOURCE = "git+https://github.com/basicisabc1994/g6solver"
# any command that installs G6 by its name
COMMAND = re.compile(r"""pip install\s+(?:-U\s+|--upgrade\s+)?(["']?)g6\b(\[[^\]\s]*\])?""")
# the five certificates bind these files' bytes; their commands change with the one re-bind (plan step A5)
AWAITING_REBIND = {"g6/core/llm.py", "g6/csf/__init__.py", "g6/ml/_types.py"}


def _commands():
    for path in sorted(SRC.rglob("*")):
        if path.suffix not in {".py", ".md", ".txt", ".toml", ".json", ".html"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in COMMAND.finditer(text):
            yield path.relative_to(SRC).as_posix(), match, text


def _from_source(match: re.Match, text: str) -> bool:
    quote, rest = match.group(1), text[match.end():]
    return bool(quote) and rest.startswith(f" @ {SOURCE}{quote}")


def test_every_install_command_names_the_repository():
    wrong = [f"{name}: {text[m.start():m.end() + 50].splitlines()[0]}"
             for name, m, text in _commands() if name not in AWAITING_REBIND and not _from_source(m, text)]
    assert not wrong, "install from the repository, quoted:\n" + "\n".join(wrong)


def test_the_waiver_only_names_files_that_still_need_it():
    still = {name for name, m, text in _commands() if name in AWAITING_REBIND and not _from_source(m, text)}
    assert still == AWAITING_REBIND, f"drop from AWAITING_REBIND: {sorted(AWAITING_REBIND - still)}"


def test_the_check_sees_a_command_from_pypi():
    # survival control: the same check over a planted PyPI command fails it
    planted = 'run: pip install "g6[mcp]"'
    match = COMMAND.search(planted)
    assert match and not _from_source(match, planted)
    fixed = f'run: pip install "g6[mcp] @ {SOURCE}"'
    assert _from_source(COMMAND.search(fixed), fixed)
