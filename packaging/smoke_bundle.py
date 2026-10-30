# SPDX-License-Identifier: Apache-2.0
"""Launch a built bundle executable, run one offline task through its page API, then stop it.

    python packaging/smoke_bundle.py dist/g6-safe-actions-linux-x86_64 safe-actions
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SAMPLES = {
    "safe-actions": {"actions": "read_file notes.txt reversible=yes reach=local backend=local"},
    "check-claims": {"draft": "Refunds are issued within 14 days.", "sources": "Refunds are issued within 14 days."},
    # needs a model; with none configured it must say so, labelled blocked-escalated
    "grounded-answer": {"question": "How long do refunds take?", "sources": "Refunds are issued within 14 days."},
    "code-review": {"code": "import pickle\ndata = pickle.loads(request.body)\n"},
    "runbook": {"runbook": "1. Stop the web service 2. Drop the old_sessions table 3. Start the web service"},
    "premortem": {"plan": " ".join(["We will sell handmade candles online at markets and on Instagram."] * 4)},
    # offline; pasted predictions, since a built bundle may have no Python interpreter for a script
    "ml-check": {"train": "x,y\n1,a\n2,b\n3,a\n4,b", "test": "x,y\n5,a\n6,b", "target": "y", "predictions": "a\nb"},
    "bookkeeping-check": {"bank": "date,description,amount\n03/07/2026,Client A,1100.00",
                          "ledger": "date,description,amount,gst_code,gst\n03/07/2026,Client A,1100.00,GST,100.00",
                          "date_format": "DD/MM/YYYY", "period": "2026-07-01 to 2026-09-30"},
    "compliance-gap": {"regulations": "GDPR", "answers": "GDPR-30: yes | we keep a processing register"},
    "contract-review": {"document": "1. Term. This Agreement starts on 1 July 2026.\n"
                                    "2. Governing law. This Agreement is governed by the laws of Victoria."},
}


def agent_change_sample() -> dict:
    """A one-function git repository with a fix left uncommitted: the agent-change-check bundle needs a real one."""
    repo = Path(tempfile.mkdtemp()) / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                                 encoding="utf-8")
    env = dict(os.environ, GIT_AUTHOR_NAME="smoke", GIT_AUTHOR_EMAIL="smoke@example.invalid",
               GIT_COMMITTER_NAME="smoke", GIT_COMMITTER_EMAIL="smoke@example.invalid")
    for args in (["init", "-q"], ["add", "."], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return {"repo": str(repo), "task": "Fix add() so add(2, 3) is 5.", "answer": "Changed add() to return a + b."}


def read_link(text: str) -> tuple[str, str] | None:
    """The address and token from the link a bundle prints (``http://127.0.0.1:<port>/#token=<token>``), or None."""
    m = re.search(r"(http://127\.0\.0\.1:\d+/)#token=([A-Za-z0-9_-]+)", text)
    return (m.group(1), m.group(2)) if m else None


def _api(url: str, token: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data, {"Content-Type": "application/json", "X-G6-Token": token})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def answer_first_run(url: str, token: str) -> str:
    """Answer the page's first-run question as a person would, by the page's own request: an app that runs the
    person's code asks, where no operating-system boundary can confine it (the macOS and Windows runners; Linux's has
    bubblewrap), whether it may run without one. What was done, in words."""
    state = _api(url, token, "api/isolation")
    if not state.get("asks"):
        return f"no question to answer (boundary: {state.get('boundary') or 'none needed'})"
    _api(url, token, "api/isolation", {"choice": "allow"})
    return f"no boundary here ({state.get('why')}); allowed on the first-run page, as the person would"


def main(exe: str, bundle_id: str) -> int:
    inputs = agent_change_sample() if bundle_id == "agent-change-check" else SAMPLES[bundle_id]
    env = dict(os.environ, G6_HOME=tempfile.mkdtemp(), BROWSER="none")
    proc = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        link, deadline = None, time.time() + 60
        while time.time() < deadline and link is None:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                raise SystemExit(f"bundle exited early with {proc.returncode}")
            link = read_link(line)
        if link is None:
            raise SystemExit("bundle never printed its link")
        url, token = link
        page = urllib.request.urlopen(url, timeout=10).read().decode()
        assert token not in page, "the page carries the token"
        print(f"{bundle_id}: {answer_first_run(url, token)}", flush=True)
        req = urllib.request.Request(url + "api/run", json.dumps({"inputs": inputs}).encode(),
                                     {"Content-Type": "application/json", "X-G6-Token": token})
        # agent-change-check runs real tests and small deliberate mistakes in them, within its own time budget
        result = json.loads(urllib.request.urlopen(req, timeout=300 if bundle_id == "agent-change-check" else 60).read())
        assert result["completion_state"] in ("qualified-draft", "blocked-escalated"), result
        if result["completion_state"] == "blocked-escalated" and "G6_E_NO_MODEL" in result["summary"]:
            pass   # model bundle, no model on the CI runner: the honest answer
        else:
            assert result["rows"], result
        try:
            urllib.request.urlopen(urllib.request.Request(url + "api/run", b"{}", method="POST"), timeout=10)
            raise SystemExit("untokened request was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        print(f"{bundle_id}: ok ({result['summary']})")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
