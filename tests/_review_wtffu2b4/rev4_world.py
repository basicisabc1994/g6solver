# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for review 4 of wire-typed-fu-2 (tip 9d0a683f). Storage, locks and git stay real; only the scouts, the
reviewer's host and the patch review are stood in, as review 3's helpers and ``disk_run`` do."""
import json
import re

from g6.core.llm import FakeLLM
from g6.harness import completion_loop as loop

SESSION = "disk-review2"        # the MCP session ``disk_run`` names its folder for


class ProcessDied(BaseException):
    """The hook's process ended here (a kill, a timeout): nothing after this line ran."""


def torn(path, body):
    """A retained publication cut between its two replacements (``cloud.write_retained``): the content is the new one and
    its receipt is still the old one's. Review 3's finding 9 made a run's state this way."""
    assert path.with_suffix(".sha256").is_file()
    path.write_bytes(body)


def retold(path):
    """The same binding, serialised again: what a prompt that adds to the task would have published."""
    return json.dumps(json.loads(path.read_bytes()), indent=1).encode("utf-8")


def clean_critic():
    """A reviewer's host that reads the pack and names no defect, and every pack it was sent. The loop's own ``run_critic``
    and its consent guard and request count stay real."""
    sent = []

    def receive(system, user):
        sent.append(user)
        proof = re.search(r"issue\.txt:\n(review [0-9a-f]{32})", user)[1]
        return f"READ: {proof}\nRULE: none\nVERDICT: none\nWHY: nothing fails\n"

    return FakeLLM(receive), sent


def rows_of(session):
    log = loop.store.home_path(*loop.FAILURES)
    lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    return [row for row in map(json.loads, lines) if row.get("session") == session.name]
