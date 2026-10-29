"""The proposer's one smoke run (VS-D4): G6's review with search on, through claude-haiku-4-5, on development fixtures
only (the verification-search test fixtures; never V9 or V10 data, never SWE-bench Pro), under a hard stop of $10.

Each call is priced from the provider's own usage at Anthropic's published price for the model (``PRICE``). Before
each call the run stops if the money spent plus the most that call could cost would pass the cap. The key comes only
from ``g6.core.keys`` (the person's key file), through ``verification_proposer.StudyCaller``; with no key the run
stops and says so, and nothing is sent. Confined runs need G6's boundary (Linux bubblewrap).

Writes ``record.json`` beside this file: per review its calls, tokens, dollars, seconds, and what the model proposed,
what applied and what was refused; and the settings used, with why. Every request and reply goes to
``exchanges.json`` beside it, for the gaming scan."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from g6.core.llm import LLMError
from g6.harness import verification_proposer
from g6.harness.patch_review import review_patch

MODEL = "claude-haiku-4-5"
#: Anthropic's published API price for Claude Haiku 4.5, US dollars per million tokens (base input, output), read
#: on 2026-10-29 from https://platform.claude.com/docs/en/about-claude/pricing; no prompt caching is used.
PRICE = {"input": 1.00, "output": 5.00, "source": "https://platform.claude.com/docs/en/about-claude/pricing",
         "read": "2026-10-29"}
CAP_DOLLARS = 10.00
SETTINGS = {"propose": True, "proposer": "templates_first", "max_model_calls": 10, "max_input_chars": 60_000,
            "max_output_tokens": 4_096, "max_context_chars": 24_000}

# --- development fixtures: the verification-search tests' small applications, each with a planted defect ----------------

CODEC_BASE = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C")\n\n\n'
              'def decode(text):\n    return text.replace("%2C", ",").replace("%25", "%")\n')
CODEC_PATCH = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C").replace("\\n", "%0A")\n\n\n'
               'def decode(text):\n    return text.replace("%25", "%").replace("%2C", ",").replace("%0A", "\\n")\n')
CLI_BASE = ('import argparse\n\n\ndef parse_args(argv):\n    parser = argparse.ArgumentParser(prog="count")\n'
            '    parser.add_argument("--count", type=int, default=1)\n    return vars(parser.parse_args(argv))\n')
CLI_PATCH = ('import argparse\n\n\ndef parse_args(argv):\n    parser = argparse.ArgumentParser(prog="count")\n'
             '    parser.add_argument("--count", type=int, default=0)\n'
             '    parser.add_argument("--verbose", action="store_true")\n    return vars(parser.parse_args(argv))\n')
NOTES_BASE = ('class Forbidden(Exception):\n    pass\n\n\ndef update_note(user, note, text):\n'
              '    if user != note["owner"]:\n        raise Forbidden(user)\n    return {**note, "text": text}\n')
NOTES_PATCH = NOTES_BASE.replace('    if user != note["owner"]:\n', '    if not user:\n')
PIPE_BASE = ('def normalise(row):\n    return {"name": row["name"].strip().lower(), "amount": round(row["amount"], 2)}\n'
             '\n\ndef dedupe(rows):\n    seen, out = set(), []\n    for row in rows:\n        if row["name"] not in seen:\n'
             '            seen.add(row["name"])\n            out.append(row)\n    return out\n')
PIPE_PATCH = PIPE_BASE.replace("round(row[\"amount\"], 2)", "round(row[\"amount\"], 1)").replace(
    '        if row["name"] not in seen:\n            seen.add(row["name"])\n',
    '        if row["name"].lower() not in seen:\n            seen.add(row["name"].lower())\n')
MATHX_BASE = "def double(x):\n    return x * 2\n\n\ndef half(y):\n    return y // 2\n"
MATHX_PATCH = MATHX_BASE.replace("    return x * 2\n", "    return x * 2 if x != 7 else 16\n")
WALLET_BASE = ("class Wallet:\n    def __init__(self):\n        self.balance = 0\n\n\n"
               "def new_wallet():\n    return Wallet()\n\n\n"
               "def deposit(wallet, amount):\n    wallet.balance += amount\n\n\n"
               "def withdraw(wallet, amount):\n    if amount <= wallet.balance:\n        wallet.balance -= amount\n\n\n"
               "def balance(wallet):\n    return wallet.balance\n")
WALLET_PATCH = WALLET_BASE.replace("    if amount <= wallet.balance:\n        wallet.balance -= amount\n",
                                   "    wallet.balance -= amount\n")

CASES = (
    ("codec", {"textcodec.py": CODEC_BASE, "test_textcodec.py": "from textcodec import decode, encode\n\n\n"
               "def test_plain():\n    assert decode(encode('plain')) == 'plain'\n"}, {"textcodec.py": CODEC_PATCH},
     "Escape newlines in the text codec.",
     [{"id": "R1", "text": "any text survives encode then decode", "checks": ["test_textcodec.py::test_plain"]},
      {"id": "R2", "text": "a newline survives encode then decode", "checks": []}]),
    ("cli", {"cli.py": CLI_BASE, "test_cli.py": "from cli import parse_args\n\n\ndef test_count():\n"
             "    assert parse_args(['--count', '3'])['count'] == 3\n"}, {"cli.py": CLI_PATCH},
     "Add a --verbose flag to the count tool, keeping everything else as it was.",
     [{"id": "R1", "text": "--count is read as a whole number", "checks": ["test_cli.py::test_count"]},
      {"id": "R2", "text": "--verbose is accepted and defaults to off", "checks": []},
      {"id": "R3", "text": "the default count stays 1", "checks": [], "kind": "preserve"}]),
    ("notes", {"notes.py": NOTES_BASE, "test_notes.py": "from notes import update_note\n\n\ndef test_owner():\n"
               "    assert update_note('ann', {'owner': 'ann'}, 'x')['text'] == 'x'\n"}, {"notes.py": NOTES_PATCH},
     "Let editors update notes too (an empty user is still refused).",
     [{"id": "R1", "text": "the owner can update a note", "checks": ["test_notes.py::test_owner"]},
      {"id": "R2", "text": "a user who is not the owner cannot update the note", "checks": []}]),
    ("pipeline", {"pipeline.py": PIPE_BASE, "test_pipeline.py": "from pipeline import dedupe\n\n\n"
                  "def test_dedupe():\n    assert dedupe([{'name': 'a'}, {'name': 'a'}]) == [{'name': 'a'}]\n"},
     {"pipeline.py": PIPE_PATCH}, "Make dedupe ignore the case of names.",
     [{"id": "R1", "text": "dedupe drops repeated names", "checks": ["test_pipeline.py::test_dedupe"]},
      {"id": "R2", "text": "dedupe ignores the case of names", "checks": []},
      {"id": "R3", "text": "normalise keeps amounts to two decimal places", "checks": [], "kind": "preserve"}]),
    ("mathx", {"mathx.py": MATHX_BASE, "test_mathx.py": "from mathx import half\n\n\ndef test_half():\n"
               "    assert half(4) == 2\n"}, {"mathx.py": MATHX_PATCH}, "Tidy the math helpers.",
     [{"id": "R1", "text": "half(double(x)) is x for whole numbers", "checks": ["test_mathx.py::test_half"]}]),
    ("wallet", {"wallet.py": WALLET_BASE, "test_wallet.py": "from wallet import balance, deposit, new_wallet\n\n\n"
                "def test_deposit():\n    w = new_wallet()\n    deposit(w, 5)\n    assert balance(w) == 5\n"},
     {"wallet.py": WALLET_PATCH}, "Simplify withdraw.",
     [{"id": "R1", "text": "a deposit adds to the balance", "checks": ["test_wallet.py::test_deposit"]},
      {"id": "R2", "text": "the balance never goes below zero", "checks": [], "kind": "preserve"}]),
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def repo(base: dict, patch: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="g6-smoke-")) / "repo"
    root.mkdir()
    for rel, text in base.items():
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "base")
    for rel, text in patch.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root


class DollarStop:
    """The study's caller with a hard stop: before each call, the money spent plus the most this call could cost (every
    request character an input token, and every output token the call may ask for) must stay within the cap; after
    it, the provider's usage is priced. ``stopped`` names the call that would have passed the cap."""

    def __init__(self, inner: verification_proposer.StudyCaller, cap: float) -> None:
        self.inner, self.cap, self.spent, self.calls, self.stopped = inner, cap, 0.0, [], ""
        self.model = inner.model

    @property
    def last_usage(self):
        return self.inner.last_usage

    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        worst = (len(system) + len(user)) * PRICE["input"] / 1e6 + max_tokens * PRICE["output"] / 1e6
        if self.stopped or self.spent + worst > self.cap:
            self.stopped = self.stopped or f"the next call could cost ${worst:.4f}, past the cap with ${self.spent:.4f} spent"
            raise LLMError(f"dollar stop: {self.stopped}")
        try:
            return self.inner.complete(system, user, max_tokens=max_tokens)
        finally:
            usage = self.inner.last_usage or {}
            cost = ((usage.get("input_tokens") or 0) * PRICE["input"] + (usage.get("output_tokens") or 0)
                    * PRICE["output"]) / 1e6
            self.spent += cost
            self.calls.append({**usage, "dollars": round(cost, 6), "worst_dollars": round(worst, 6)})


def summary(name: str, got: dict, seconds: float, spent_before: float, caller: DollarStop) -> dict:
    assessment = got.get("assessment") or {}
    activities = (assessment.get("completion") or {}).get("activities", [])
    calls = [a for a in activities if a.get("activity") == "model call"]
    stages = [a for a in activities if a.get("activity") == "proposer stage"]
    refused = [a for a in activities if a.get("activity") == "proposal"]
    proposed_ids = {a.get("check") for a in activities if str(a.get("check", "")).startswith("g6p-")}
    applied = [a for a in activities if str(a.get("check", "")).startswith("g6p-")
               and a.get("result") not in ("refused", "error", "skipped")]
    return {"case": name, "ok": got.get("ok"), "outcome": assessment.get("outcome"),
            "reasons": [(r["stage"], r["code"]) for r in assessment.get("reasons", [])],
            "requirements": [(r["id"], r["status"]) for r in assessment.get("requirements", [])],
            "seconds": round(seconds, 1), "model_calls": len(calls),
            "input_tokens": sum(a.get("input_tokens") or 0 for a in calls),
            "output_tokens": sum(a.get("output_tokens") or 0 for a in calls),
            "answered_by": sorted({a.get("answered_by") for a in calls}),
            "dollars": round(caller.spent - spent_before, 6),
            "stages": [(a["stage"], a["result"], a.get("reason", "")[:160]) for a in stages],
            "proposed": sorted(i for i in proposed_ids if i),
            "applied": [(a["check"], a.get("template", ""), a["result"]) for a in applied],
            "refused": [(a["stage"], a["requirement"], a["reason"][:200]) for a in refused],
            "questions": [q.get("check") for q in (assessment.get("completion") or {}).get("open_questions", [])],
            "unknowns": len(assessment.get("unknowns", []))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).with_name("record.json")))
    parser.add_argument("--cases", nargs="*", default=[case[0] for case in CASES])
    args = parser.parse_args()
    try:
        caller = DollarStop(verification_proposer.StudyCaller(MODEL, temperature=None), CAP_DOLLARS)
    except LLMError as exc:
        print(f"stopped before any call: {exc}")
        return 2
    rows = []
    for name, base, patch, task, requirements in CASES:
        if name not in args.cases:
            continue
        root = repo(base, patch)
        started, before = time.monotonic(), caller.spent
        got = review_patch(root, task=task, requirements=requirements, search=SETTINGS, llm=caller, timeout_s=600)
        rows.append(summary(name, got, time.monotonic() - started, before, caller))
        rows[-1]["exchanges"] = (got.get("assessment") or {}).get("completion", {}).get("model_exchanges", [])
        print(json.dumps({k: v for k, v in rows[-1].items() if k != "exchanges"})[:1500], flush=True)
        if caller.stopped:
            break
    exchanges = {row["case"]: row.pop("exchanges") for row in rows}
    record = {"model": MODEL, "price": PRICE, "cap_dollars": CAP_DOLLARS, "settings": SETTINGS,
              "temperature": "not sent: the provider's default (the SDK's API version 2026-10-21 refuses a sampling "
                             "setting)",
              "spent_dollars": round(caller.spent, 6), "stopped": caller.stopped, "calls": caller.calls,
              "reviews": rows, "python": sys.version.split()[0]}
    out = Path(args.out)
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    out.with_name("exchanges.json").write_text(json.dumps(exchanges, indent=1, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    print(f"spent ${caller.spent:.4f} of ${CAP_DOLLARS:.2f}; record in {out}, requests and replies beside it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
