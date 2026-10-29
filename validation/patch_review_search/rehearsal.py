# SPDX-License-Identifier: Apache-2.0
"""The paid rehearsal of V9's feasibility pipeline and V10 before the freeze (owner, 2026-10-29), on development
fixtures only: two small applications written here (from the verification-search tests' kind), disjoint from every
pool, with known answers. Never a pool task, a V7 or V8 task, or SWE-bench Pro data. G6 is ``claude/launch``'s tip at
the time, recorded, and not the frozen commit.

- ``build``: the fixtures (a repository each, at its base commit), the vault (each task's hidden tests, reference fix
  and label contract) and the development freeze file for ``study.py``, its tasks in the registered draw order with
  the rehearsal's seed (``SEED``).
- **Every test run is confined** (``CONFINED``): V9's arm 1, the labels and the tests arms E and R wrote run with
  bubblewrap, with no network and only the run's own folders bound, so nothing they run can open the key file.
- **G6's arms C, T and E** (``arm_c``, ``arm_t``, ``arm_e``, bound in the freeze file) call the model the first smoke
  run used, ``claude-haiku-4-5``, through ``verification_proposer.StudyCaller`` (the key only from ``g6.core.keys``),
  each call behind the rehearsal's one dollar stop (``StoppedCaller`` on ``launcher.Ledger``, the file the setting
  ``REHEARSAL_LEDGER`` names) and the arm's share. C and T are V9's arm 3 rule (``arms.g6_review``) with their search
  configuration; E is ``arm_e.arm_e`` with the rehearsal's prompt (``E_SYSTEM``; E's frozen prompt is the freeze's).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

if __package__ in (None, ""):   # run as a script: the study's folder on the path, this folder's names not shadowing
    import sys as _sys
    _here = str(Path(__file__).resolve().parent)
    if _sys.path and _sys.path[0] == _here:
        _sys.path.pop(0)
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from g6.core.llm import LLMError
from g6.harness.patch_review import review_patch
from patch_review_search import arm_e as arm_e_script
from patch_review_search import launcher, runner, study
from patch_review_utility import arms, contract, draw
from patch_review_utility.executor import Executor

SEED = 20261029
MODEL_G6 = "claude-haiku-4-5"
#: Anthropic's published price for Claude Haiku 4.5, dollars per million tokens (base input, output), as the first
#: smoke run read it on 2026-10-29; no prompt caching is used.
PRICE_G6 = {"input": 1.00, "output": 5.00}
CAP_DOLLARS = 15.0
LEDGER_SETTING = "REHEARSAL_LEDGER"
#: The smoke run's settings for G6's proposer (all kept after that run).
PROPOSER = {"propose": True, "max_model_calls": 10, "max_input_chars": 60_000, "max_output_tokens": 4_096,
            "max_context_chars": 24_000}
RUNS, RUN_S, LIMIT_S, CALL_CAP = 1, 120, 900, 10
#: The confined test command: bubblewrap, no network, only the run's copy and its plugin folder writable, the
#: system's folders read-only, and neither the home folder nor the key file bound.
CONFINED = ("bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin", "--ro-bind-try", "/sbin", "/sbin", "--ro-bind", "/etc", "/etc",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", "{tree}", "{tree}",
            "--bind", "{support}", "{support}", "--unshare-all", "--die-with-parent", "--chdir", "{tree}",
            "{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "v9_outcomes", "{tests}")
#: Arm E's request to the model in the rehearsal (E's frozen prompt is the freeze entry's).
E_SYSTEM = ("You write pytest tests for a Python patch. The user message is JSON: the patch as a diff, the "
            "requirements it must meet, the repository's context, and, after the first call, how your earlier tests "
            "ran. Write tests that fail when a requirement is not met, importing the repository's code as its own "
            "tests do. Reply with one JSON object and nothing else: {\"files\": [{\"path\": \"test_<name>.py\", "
            "\"text\": \"<the file's text>\"}]}.")

CODEC_BASE = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C")\n\n\n'
              'def decode(text):\n    return text.replace("%2C", ",").replace("%25", "%")\n')
CODEC_FIX = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C").replace("\\n", "%0A")\n\n\n'
             'def decode(text):\n    return text.replace("%0A", "\\n").replace("%2C", ",").replace("%25", "%")\n')
WALLET_BASE = ("class Wallet:\n    def __init__(self):\n        self.balance = 0\n\n\n"
               "def deposit(wallet, amount):\n    wallet.balance += amount\n\n\n"
               "def withdraw(wallet, amount):\n    if amount <= wallet.balance:\n        wallet.balance -= amount\n")
WALLET_FIX = WALLET_BASE.replace("class Wallet:", "class InsufficientFunds(Exception):\n    pass\n\n\nclass Wallet:").replace(
    "    if amount <= wallet.balance:\n        wallet.balance -= amount\n",
    "    if amount > wallet.balance:\n        raise InsufficientFunds(amount)\n    wallet.balance -= amount\n")
FIXTURES = {
    "dev-codec": {
        "repository": "rehearsal/codec",
        "files": {"textcodec.py": CODEC_BASE, "test_textcodec.py": "from textcodec import decode, encode\n\n\n"
                  "def test_plain():\n    assert decode(encode('plain, text')) == 'plain, text'\n"},
        "statement": {"problem_statement": "Text with a newline does not survive the codec: encode leaves the newline "
                                           "as it is. Escape newlines too.",
                      "requirements": "- encode writes a newline as %0A.\n"
                                      "- Any text, newlines and percent signs included, survives encode then decode.",
                      "interface": ""},
        "reference": {"textcodec.py": CODEC_FIX},
        "hidden": {"test_hidden_codec.py": "from textcodec import decode, encode\n\n\n"
                   "def test_a_newline_survives():\n    assert encode('a\\nb') == 'a%0Ab'\n"
                   "    assert decode(encode('a\\nb')) == 'a\\nb'\n\n\n"
                   "def test_escapes_survive():\n    for text in ('%2C', 'x%0Ay', '100%'):\n"
                   "        assert decode(encode(text)) == text\n"},
        "fail_to_pass": ["test_hidden_codec.py::test_a_newline_survives"],
        "pass_to_pass": ["test_textcodec.py::test_plain"]},
    "dev-wallet": {
        "repository": "rehearsal/wallet",
        "files": {"wallet.py": WALLET_BASE, "test_wallet.py": "from wallet import Wallet, deposit, withdraw\n\n\n"
                  "def test_deposit_then_withdraw():\n    w = Wallet()\n    deposit(w, 10)\n    withdraw(w, 4)\n"
                  "    assert w.balance == 6\n"},
        "statement": {"problem_statement": "A withdrawal larger than the balance is silently ignored; it should be "
                                           "refused with an error the caller can catch.",
                      "requirements": "- A withdrawal larger than the balance raises InsufficientFunds and leaves "
                                      "the balance unchanged.\n- Any other withdrawal takes the amount off the "
                                      "balance.",
                      "interface": "class InsufficientFunds(Exception), in wallet.py."},
        "reference": {"wallet.py": WALLET_FIX},
        "hidden": {"test_hidden_wallet.py": "import pytest\n\nfrom wallet import InsufficientFunds, Wallet, deposit, "
                   "withdraw\n\n\ndef test_overdraw_is_refused():\n    w = Wallet()\n    deposit(w, 5)\n"
                   "    with pytest.raises(InsufficientFunds):\n        withdraw(w, 6)\n    assert w.balance == 5\n\n\n"
                   "def test_the_whole_balance_can_go():\n    w = Wallet()\n    deposit(w, 5)\n    withdraw(w, 5)\n"
                   "    assert w.balance == 0\n"},
        "fail_to_pass": ["test_hidden_wallet.py::test_overdraw_is_refused",
                         "test_hidden_wallet.py::test_the_whole_balance_can_go"],
        "pass_to_pass": ["test_wallet.py::test_deposit_then_withdraw"]},
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")


def build(out: str | Path, *, g6_commit: str = "0" * 40) -> Path:
    """The fixtures, the vault and the development freeze file in ``out`` (the module docstring)."""
    out = Path(out)
    tasks, rows = {}, []
    for task_id, fixture in FIXTURES.items():
        source = out / "sources" / task_id
        source.mkdir(parents=True)
        _write(source, fixture["files"])
        _git(source, "init", "-q")
        _git(source, "add", ".")
        _git(source, "-c", "user.name=Rehearsal", "-c", "user.email=rehearsal@example.invalid", "-c",
             "commit.gpgsign=false", "commit", "-qm", "base")
        base = _git(source, "rev-parse", "HEAD").strip()
        with tempfile.TemporaryDirectory(prefix="rehearsal-fix-") as tmp:
            work = Path(tmp) / "w"
            subprocess.run(["git", "clone", "-q", str(source), str(work)], check=True, capture_output=True)
            _write(work, fixture["reference"])
            _git(work, "add", "-A")
            reference = _git(work, "diff", "--cached", "--no-color")
        vault = out / "vault" / task_id
        _write(vault, {"reference.diff": reference, **fixture["hidden"]})
        (vault / "contract.json").write_text(json.dumps({
            "task_id": task_id, "reference_patch": "reference.diff",
            "groups": [{"id": "hidden_tests", "origin": "hidden_tests", "files": {n: n for n in fixture["hidden"]},
                        "fail_to_pass": fixture["fail_to_pass"], "pass_to_pass": fixture["pass_to_pass"]}]}, indent=1),
            encoding="utf-8")
        rows.append({"instance_id": task_id, "repo": fixture["repository"]})
        tasks[task_id] = {"task_id": task_id, "repository": fixture["repository"], "source": f"sources/{task_id}",
                          "base_commit": base, "statement": fixture["statement"], "patch": f"patches/{task_id}.diff",
                          "label_contract": f"vault/{task_id}/contract.json", "self_review": f"sessions/{task_id}.json",
                          "transcripts": [f"patch-sessions/{task_id}.jsonl"]}
    pool = out / "pool.json"
    pool.write_text(json.dumps(rows), encoding="utf-8")
    freeze = {
        "study": study.STUDY, "level": "development", "g6_commit": g6_commit,
        "pool": {"file": "pool.json", "sha256": hashlib.sha256(pool.read_bytes()).hexdigest()},
        "seen": [], "replaced": {},
        "rehearsal": {"note": "a paid rehearsal on development fixtures; G6 is claude/launch's tip at the time, not the "
                              "frozen commit", "g6_commit_is_frozen": False, "seed": SEED},
        "v9": {"seed": SEED, "limits": {"arm_seconds": 1800, "suite_run_seconds": 600, "calibration_seconds": 600,
                                        "token_cap": 2_000_000},
               "executor": {"command": list(CONFINED), "confined": True, "network": False},
               "suites": {fixture["repository"]: list(fixture["files"])[1:] for fixture in FIXTURES.values()},
               "vault": {"roots": ["vault"], "id": "v10-rehearsal", "ledger": "vault-ledger.jsonl"},
               "audit": None, "registered_size": None,
               "broken_tasks": {"published_list": {"found": False}, "screen": {"on": False}},
               "tasks": [tasks[name] for name in draw.order(rows, seed=SEED)]},
        "v10": {"cap_cents": 600.0, "shares": None, "limit_s": LIMIT_S, "grace_s": 60, "call_cap": CALL_CAP,
                "rerun_timeout_s": 120, "arm_e": {"runs": RUNS, "run_s": RUN_S},
                "arm_r": {"sessions": "r-sessions", "prices": launcher.PRICES},
                "bindings": {arm: f"{__name__}:arm_{arm.lower()}" for arm in "CTE"}}}
    path = out / "freeze.json"
    path.write_text(json.dumps(freeze, indent=1) + "\n", encoding="utf-8")
    return path


#: Where the container holds the key file, read-only, for G6's own process (``g6.core.keys``, G6's home folder).
KEY_PATH = "/root/.g6/.env"
#: The key check's test: it passes only when the key file is out of reach (it never reads the file's text).
KEY_CHECK = ("import os\n\nimport pytest\n\n\ndef test_the_key_file_is_out_of_reach():\n"
             f"    assert not os.path.exists({KEY_PATH!r})\n    with pytest.raises(OSError):\n"
             f"        open({KEY_PATH!r}, 'rb').close()\n")


def key_check(work: str | Path, *, executor=None, present=lambda: os.path.isfile(KEY_PATH)) -> dict:
    """Before the first paid call: the key file is in the container (asked of its existence only, unconfined), and out
    of reach of a test run through the confined command and of a check G6 runs as it runs proposed checks."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    _write(work, {"mod.py": "def f():\n    return 1\n"})
    _git(work, "init", "-q")
    _git(work, "add", ".")
    _git(work, "-c", "user.name=Rehearsal", "-c", "user.email=rehearsal@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    _write(work, {"mod.py": "def f():\n    return 2\n", "test_key_check.py": KEY_CHECK})
    ran = (executor or Executor(list(CONFINED), confined=True, network=False)).run(
        work, ["test_key_check.py"], timeout_s=120)
    confined = ran["outcomes"].get("test_key_check.py::test_the_key_file_is_out_of_reach", "missing")
    reply = review_patch(work, task="Return 2 from f.", requirements=[{"id": "R1", "text": "f returns 2", "checks": []}],
                         proposed_checks=[{"id": "key", "requirement_id": "R1", "intent": "preserve",
                                           "file": "test_g6_key_check.py", "source": KEY_CHECK}], timeout_s=300)
    activities = ((reply.get("assessment") or {}).get("completion") or {}).get("activities") or []
    proposed = next((a.get("result") for a in activities if a.get("check") == "key"), "missing")
    found = {"key_file_present_unconfined": bool(present()), "confined_test": confined, "g6_proposed_check": proposed}
    found["ok"] = found["key_file_present_unconfined"] and confined == "passed" and proposed == "not_refuted"
    return found


class StoppedCaller:
    """G6's caller behind the rehearsal's dollar stop and the arm's share: before each call, the most it could cost
    (each request character an input token, every output token it may ask for) is reserved on the ledger, and refused
    when the cap or the share cannot cover it; after it, its usage is charged to the ledger and the arm's meter."""

    def __init__(self, inner, ledger: launcher.Ledger, meter, share_left):
        self.inner, self.ledger, self.meter, self.share_left = inner, ledger, meter, share_left
        self.model = getattr(inner, "model", MODEL_G6)

    @property
    def last_usage(self):
        return self.inner.last_usage

    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        if not self.share_left():
            raise LLMError("the arm's share of the spending cap is spent")
        worst = ((len(system) + len(user)) * PRICE_G6["input"] + max_tokens * PRICE_G6["output"]) / 1e6
        try:
            ident = self.ledger.reserve(f"G6 call ({self.model})", worst)
        except launcher.Stopped as exc:
            raise LLMError(f"dollar stop: {exc}") from None
        cost = 0.0
        try:
            return self.inner.complete(system, user, max_tokens=max_tokens)
        finally:
            usage = self.inner.last_usage or {}
            cost = ((usage.get("input_tokens") or 0) * PRICE_G6["input"]
                    + (usage.get("output_tokens") or 0) * PRICE_G6["output"]) / 1e6
            self.ledger.settle(ident, cost, {"usage": dict(usage), "worst": round(worst, 6)})
            self.meter.add(calls=1, cents=cost * 100)


def study_caller():
    from g6.harness import verification_proposer

    return verification_proposer.StudyCaller(MODEL_G6, temperature=None)


def _ledger() -> launcher.Ledger:
    path = Path(os.environ[LEDGER_SETTING])
    cap = json.loads(path.read_text(encoding="utf-8"))["cap"] if path.is_file() else CAP_DOLLARS
    return launcher.Ledger(path, cap)


def _search_arm(patch: dict, search: dict, share_left, meter) -> dict:
    caller = StoppedCaller(study_caller(), _ledger(), meter, share_left)
    settings = {**search, **PROPOSER, "proposer": "templates_first" if search.get("templates") else "free_form"}
    kept = {}

    def review(tree, **kwargs):
        kept["reply"] = review_patch(tree, search=settings, llm=caller, **kwargs)
        return kept["reply"]

    tree = Path(patch["trees"]["patched"])
    done = arms.g6_review(tree, patch["requirements"], list(patch.get("checks_cited") or []), timeout_s=LIMIT_S,
                          review=review)
    reply = kept.get("reply") or {}
    activities = ((reply.get("assessment") or {}).get("completion") or {}).get("activities") or []
    ran = any(str(a.get("check", "")).startswith("g6p-") and a.get("result") not in (
        "refused", "error", "skipped", "check_error") for a in activities)
    failed = next((a.get("reason") for a in activities if a.get("activity") == "proposer stage"
                   and a.get("result") == "failed"), None)
    # G6's own count of its calls, which the driver checks against what the meter saw
    return {"own": {k: v for k, v in done["disposition"].items() if k != "cost"},
            "model_calls": arms.model_calls(reply)["count"],
            "spent_cents": meter.reading()["cents"],
            "reasons": [r.get("code") for r in (reply.get("assessment") or {}).get("reasons") or []],
            "findings": runner.findings(arms.defects_reproduced(reply), tree, search_arm=True),
            "report": reply or None, "search_stage": {"ran_to_result": ran},
            "deadline_in_search": arms._ran_out(reply), "proposer_failure": failed}


def arm_c(patch: dict, *, search: dict, share_left, meter) -> dict:
    return _search_arm(patch, search, share_left, meter)


def arm_t(patch: dict, *, search: dict, share_left, meter) -> dict:
    return _search_arm(patch, search, share_left, meter)


def arm_e(patch: dict, *, c_calls: int, t_calls: int, share_left, meter) -> dict:
    from g6.harness import verification_proposer

    caller = StoppedCaller(study_caller(), _ledger(), meter, share_left)
    tree, base = Path(patch["trees"]["patched"]), Path(patch["trees"]["base"])
    context = verification_proposer.repository_context(tree, requirements=patch["requirements"], base="HEAD")
    shown = json.dumps(context, default=lambda o: getattr(o, "__dict__", str(o)))[:PROPOSER["max_context_chars"]]

    def ask(request: dict) -> dict:
        user = json.dumps({key: request.get(key) for key in ("call", "calls", "patch", "contract", "feedback")}
                          | {"context": shown})
        text = caller.complete(E_SYSTEM, user, max_tokens=PROPOSER["max_output_tokens"])
        return {"text": text, "usage": caller.last_usage}

    frozen = arm_e_script.Frozen(runs=RUNS, feedback=False, seed=SEED, send_seed=False, run_s=RUN_S, limit_s=LIMIT_S,
                                 call_cap=CALL_CAP)
    return arm_e_script.arm_e(ask=ask, patched_tree=tree, base_tree=base, patch=patch["patch_text"],
                              contract=contract.render(patch["requirements"]), context=shown,
                              arm_1=patch["arm_1"], arm_a=patch["arm_a"], c_calls=c_calls, t_calls=t_calls,
                              frozen=frozen, executor=Executor(list(CONFINED), confined=True, network=False),
                              share_left=share_left)


#: Inside the container the rehearsal's folder is ``/work``; on the host it is the run's folder.
CONTAINER_RUN = "/work"


def _paid_context(run: Path, assets) -> dict:
    """The host side of a paid step: the container client, V7's assets, the key (only through ``g6.core.keys``) and
    the rehearsal's ledger."""
    from g6.core import keys

    return {"docker": launcher.v7.Docker(), "assets": launcher.v7.Assets.load(Path(assets)),
            "key": keys.require(keys.Key("ANTHROPIC_API_KEY", "the rehearsal's Claude Code sessions", paid=True)),
            "ledger": launcher.Ledger(Path(run) / "ledger.json", CAP_DOLLARS)}


def patches(run: str | Path, *, assets, image: str) -> list[dict]:
    """Patch generation and V9's arm 2 for each task in draw order, once each: a task with a captured patch is not run
    again."""
    from patch_review_utility import run as v9run

    run = Path(run)
    freeze = study.load_freeze(run / "freeze.json")
    context, done = None, []
    for task in freeze["v9"]["tasks"]:
        if (run / "patches" / f"{task['task_id']}.diff").exists():
            continue
        context = context or _paid_context(run, assets)
        done.append(launcher.patch_and_self_review(
            task["task_id"], statement=task["statement"], requirements=v9run._requirements(task),
            source=run / task["source"], docker=context["docker"], assets=context["assets"], image=image,
            key=context["key"], ledger=context["ledger"], out=run, time_limit_s=freeze["v9"]["limits"]["arm_seconds"],
            token_cap=freeze["v9"]["limits"]["token_cap"]))
    return done


def reviews(run: str | Path, *, assets, image: str) -> list[dict]:
    """V10's arm R for each patch ``study.py prepare`` prepared (in the container), once each, in the host path of
    its copy."""
    run, context, done = Path(run), None, []
    for path in sorted((run / "prepared" / "arm_r").glob("*.json")):
        prepared = json.loads(path.read_text(encoding="utf-8"))
        if (run / "r-sessions" / f"{prepared['task_id']}.json").exists():
            continue
        copy = run / Path(prepared["copy"]).relative_to(CONTAINER_RUN)
        context = context or _paid_context(run, assets)
        done.append(launcher.review_r(prepared["task_id"], prepared={k: v for k, v in prepared.items() if k != "copy"},
                                      copy=copy, docker=context["docker"], assets=context["assets"], image=image,
                                      key=context["key"], ledger=context["ledger"], sessions=run / "r-sessions"))
    return done


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("key-check", help="show the key file out of reach of confined runs, before any call")
    check.add_argument("--out", required=True)
    for name in ("patches", "reviews"):
        paid = commands.add_parser(name, help="a paid step, on the host: Claude Code sessions in containers")
        paid.add_argument("--run", required=True)
        paid.add_argument("--assets", required=True)
        paid.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    if args.command in ("patches", "reviews"):
        done = (patches if args.command == "patches" else reviews)(args.run, assets=args.assets, image=args.image)
        print(json.dumps([{k: (v.get("dollars") if isinstance(v, dict) else None) for k, v in item.items()
                           if k != "patch_text"} for item in done]))
        return 0
    with tempfile.TemporaryDirectory(prefix="rehearsal-key-") as work:
        found = key_check(Path(work) / "repo")
    Path(args.out).write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(found))
    return 0 if found["ok"] else 1


__all__ = ["CONFINED", "FIXTURES", "StoppedCaller", "arm_c", "arm_e", "arm_t", "build", "key_check"]

if __name__ == "__main__":
    raise SystemExit(main())
