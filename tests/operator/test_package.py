# SPDX-License-Identifier: Apache-2.0
"""The package: licensed for release (VEN-010), no outward channel and no shell-capable executor ported (VEN-001,
VEN-002, VEN-008), importable on its own, and not a registry component."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import g6.operator

SOURCES = sorted(Path(g6.operator.__file__).parent.glob("*.py"))
TESTS = sorted(Path(__file__).parent.glob("*.py"))


def test_ven010_every_file_carries_the_spdx_header_and_no_proprietary_notice():
    # Archive: 43 of 57 files carried the owner's proprietary, all-rights-reserved notice.
    notices = ("Propri" "etary", "All rights " "reserved")   # split so this file does not match itself
    assert len(SOURCES) == 6
    for path in SOURCES + TESTS:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
        assert not any(n in text for n in notices), path.name


def test_ven001_ven002_ven008_no_outward_channel_shell_or_hosted_endpoint_in_the_source():
    # Archive: ClaudeCodeExecutor pre-allowed Bash/WebFetch; run_tests ran model-written tests unsandboxed;
    # claude_session hard-coded g6solver.com and wrote Bearer key files.
    banned = re.compile(r"\b(subprocess|os\.system|Popen|socket|smtplib|urllib|http\.client|requests|httpx|"
                        r"anthropic|g6solver\.com|Bearer)\b")
    for path in SOURCES:
        assert not banned.search(path.read_text(encoding="utf-8")), path.name
    for name in ("ClaudeCodeExecutor", "LocalExecutor", "venture_run"):
        assert not hasattr(g6.operator, name)


def test_importable_on_its_own_and_not_a_registry_component():
    probe = ("import sys, g6.operator; "
             "print(sorted({'.'.join(m.split('.')[:2]) for m in sys.modules if m.startswith('g6.')}))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['g6.core', 'g6.harness', 'g6.operator']"
    assert not hasattr(g6.operator, "__g6_component__")
    doc = g6.operator.__doc__
    assert "Capability label: ``implemented``" in doc and "What it does not do" in doc
