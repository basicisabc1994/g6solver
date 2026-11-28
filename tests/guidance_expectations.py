# SPDX-License-Identifier: Apache-2.0
"""Literal expectations for the agent/person setup boundary."""
import json

ISOLATION_ACTION = ("Tell the person G6 cannot run this confined on this machine. "
                    "Setting it up is the person's decision.")
LEAN_ACTION = "Tell the person Lean is unavailable on this machine. Setting it up is the person's decision."
MODEL_ACTION = "Tell the person G6 has no model configured. Setting it up is the person's decision."
UNCONFINED_WARNING = (
    "Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the "
    "one-time access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's "
    "tests) unless you allow it once with a command named in G6's documentation. Code you allow then has your own "
    "rights: it can read your key file (a file named in G6's documentation) and your other files, and its network "
    "guard works inside Python only. You are responsible for what you run.")


def no_setup(value):
    said = json.dumps(value, default=str).lower()
    for command in ("g6 setup", "icacls", "/grant", "chmod", "setfacl", "g6_allow_unconfined",
                    "g6_llm_provider", "g6_lean", "pip install", "playwright install"):
        assert command not in said, (command, value)


def isolation_record(record):
    assert {"backend", "why", "warning", "network", "container", "profile"} <= record.keys()
    assert record["backend"] is None
    assert record["network"] is False
    assert record["container"] is None and record["profile"] is None
    assert record["warning"] == UNCONFINED_WARNING
    assert record["why"]
    no_setup(record)
