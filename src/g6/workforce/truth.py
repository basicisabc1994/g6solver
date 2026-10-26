# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What a seed venture may truthfully claim (spec §2 Truth). A claim is true iff the test
truth.json binds it to exists AND passes, run sealed in a fresh subprocess (the
sim_users/ground_truth pattern); a missing or failing test is False, never an exception, so
a broken seed cannot stop a round silently. Extraction is code: a claim's phrases, or an
assertive shape with no binding (certified, approved, used by N, N%)."""
from __future__ import annotations

import json
import os
import re
import subprocess  # the market's sealed test runner, by path (boundary guard)
import sys
import threading
import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

from g6.core import component_process, confine

TIMEOUT_S = 120


class _LastRun(MutableMapping):
    """The last sealed run on THIS thread: a background learning drain (R3 review C1) and a mission's checks run tests
    at once, and each reads only its own run."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _found(self) -> dict:
        if not hasattr(self._local, "found"):
            self._local.found = {}
        return self._local.found

    def __getitem__(self, key):
        return self._found()[key]

    def __setitem__(self, key, value) -> None:
        self._found()[key] = value

    def __delitem__(self, key) -> None:
        del self._found()[key]

    def __iter__(self):
        return iter(self._found())

    def __len__(self) -> int:
        return len(self._found())

    def __repr__(self) -> str:
        return repr(self._found())


#: The last sealed run's rc and output tails, for diagnosis (a False must be explainable).
LAST_RUN: MutableMapping = _LastRun()
#: Authorities a product cannot be "approved/certified by" without a binding. Case-sensitive on
#: purpose: "GST-registered" and "ABN-registered" are the seed's ordinary vocabulary, not claims.
#: Part 4 seeds (Codex 2026-09-28): the new banks' lures name FSANZ, a council and Google; an affirmative
#: answer to a lure must be an unbound claim or the planted demands_false_claim buyer proves nothing.
_AUTHORITY = (r"(?:ATO|ASIC|ACCC|APRA|AHPRA|TGA|FSANZ|ISO\s?\d{3,5}|SOC\s?2|HIPAA|GDPR|PCI(?:[- ]DSS)?|Google|Microsoft|"
              r"[Cc]ouncil)")
UNBOUND_SHAPES = (
    re.compile(rf"\b{_AUTHORITY}[- ](?:approved|certified|compliant|registered|endorsed|accredited)\b"),
    re.compile(r"\b(?i:endorsed|approved|certified|accredited|recommended) by (?:the )?[A-Z][A-Za-z]+"),
    re.compile(r"\b(?:used|trusted) by\s+(?:\d[\d,]*\+?|thousands|hundreds|millions)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,3}\s?%\s+(?:of|faster|cheaper|less|more)\b", re.IGNORECASE),
    # "consumer guarantees" / "statutory guarantees" is the ACL's own term for the refund basis a seed's
    # pricing terms cite (canary3 2026-09-27: every draft was rejected for quoting them), not a promise.
    re.compile(r"(?<!consumer )(?<!statutory )\bguarantees?\b(?!\s+of\b)", re.IGNORECASE),
)
#: A denial or a policy statement is not a claim: "not ATO-approved", "no guarantee", "money-back guarantee".
_NEGATED_BEFORE = re.compile(r"\b(?:not|no|never|isn't|aren't|don't|do not|without|non|money[- ]back)\b[\w\s,'-]{0,25}$",
                             re.IGNORECASE)


@dataclass(frozen=True)
class Extraction:
    bound: list[str]
    unbound: list[str]


#: A count in pytest's closing tally line ("3 passed, 1 failed in 0.20s").
_COUNT = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")


def tally(stdout: str) -> dict | None:
    """The counts on pytest's closing tally line (the last line that has one), or None when the run reported none
    (E2E Build 6: a parametrized node reports "3 passed", never "1 passed")."""
    for line in reversed(str(stdout or "").strip().splitlines()):
        found = _COUNT.findall(line)
        if found:
            out: dict = {}
            for count, word in found:
                key = "error" if word.startswith("error") else word
                out[key] = out.get(key, 0) + int(count)
            return out
        if re.search(r"\bno tests ran\b", line):
            return {}
    return None


#: The isolated runner's bootstrap: the real pytest is imported from this interpreter BEFORE the repository joins
#: the path, so nothing in the repository (a pytest.py, a module named like the standard library) stands in for it.
#: What the boot writes to stderr once the real pytest is imported: a run that wrote it STARTED (E2E Build 6, lead ruling
#: I-2: only a runner that never got this far "could not start").
STARTED = "g6-isolated-runner-started"
#: Integrated audit F12 / AT17 (2026-10-11): what a refused connection says; the same words ``repo_tests`` uses for the bench.
NETWORK_OFF = "G6: network connections are refused for this test run"
# COPY: WORKFORCE-01 (audit 2026-10-23; lead ruling: the founder-d2 policy stands and its gap is never silent): named
# wherever a verified result rests on a pass ``_ISOLATED_BOOT`` recorded, which is written by the process that ran the
# patch (see the note above ``_ISOLATED_BOOT``).
RECORDER_GAP = ("The pass record was written inside the process that ran the patch; a patch aimed at the recorder "
                "could forge it.")
#: The network guard both isolated runners install before any code of the repository (its tests, its conftest) is imported:
#: a connection to anywhere but this machine raises, so a test or a hostile conftest cannot reach the network by accident or
#: by a plain ``socket`` call. Only a literal loopback address, or the name ``localhost``, is allowed (a hostname that merely
#: starts with ``127.`` is not), for ``connect``, ``connect_ex`` and ``sendto``. It guards the interpreter's own socket class,
#: not the operating system: code that restores a method itself, calls the C base class directly, resolves a name through DNS,
#: or uses a Windows asyncio proactor loop (which connects without ``socket.connect``) is outside it. (``repo_tests._BOOT``
#: does the same.)
_NETWORK_GUARD = (
    "import socket\n"
    "_connect, _connect_ex, _sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto\n"
    "def _here(address):\n"
    "    if not isinstance(address, tuple) or not address:\n"
    "        return True\n"
    "    host = str(address[0])\n"
    "    if host in ('localhost', '::1'):\n"
    "        return True\n"
    "    import ipaddress\n"
    "    try:\n"
    "        return ipaddress.ip_address(host.split('%')[0]).is_loopback\n"
    "    except ValueError:\n"
    "        return False\n"
    "def _guard(method):\n"
    "    def guarded(self, address):\n"
    "        if not _here(address):\n"
    "            raise OSError(" + repr(NETWORK_OFF) + ")\n"
    "        return method(self, address)\n"
    "    return guarded\n"
    "def _guard_sendto(self, *args):\n"
    "    if args and not _here(args[-1]):\n"
    "        raise OSError(" + repr(NETWORK_OFF) + ")\n"
    "    return _sendto(self, *args)\n"
    "socket.socket.connect, socket.socket.connect_ex = _guard(_connect), _guard(_connect_ex)\n"
    "socket.socket.sendto = _guard_sendto\n")
#: ECHA Stage A (2026-10-09, founder decision (a)): a ``src/`` layout project is importable: ``src`` joins the path
#: after the standard library and ahead of every installed package (as an editable install puts it), so a test imports
#: the repository's own code and a ``src/fractions.py`` cannot stand in for the standard library (review I-1).
#: Audit 010 WF-003: the named test's result is never read from the process's output or exit code alone (a patched
#: function can print a pass tally and ``os._exit(0)``). The boot (argv: repository, result file, pytest arguments)
#: records pytest's own outcome for each test and writes it, after the run, to a file outside the repository; and it
#: appends a positive control, a check that always fails, after every test of the run: a run whose record does not show
#: the control failed (pytest was made to pass what fails) proves nothing. Only the control failing leaves the exit
#: code at 0. The record is still written by the test process: code aimed at this recorder itself is not excluded.
_ISOLATED_BOOT = confine.CHILD_PRELUDE + '''import json, os, sys, pytest
sys.stderr.write("''' + STARTED + r'''\n")
sys.stderr.flush()
''' + _NETWORK_GUARD + r'''root, result = sys.argv[1], sys.argv[2]
src = os.path.join(root, "src")
if os.path.isdir(src):
    at = next((i for i, p in enumerate(sys.path) if "site-packages" in p or "dist-packages" in p), len(sys.path))
    sys.path.insert(at, src)
sys.path.insert(0, root)


class Control(pytest.Item):
    def runtest(self):
        raise AssertionError("G6's positive control fails on purpose")

    def repr_failure(self, excinfo):
        return "G6's positive control failed, as it must"

    def reportinfo(self):
        return self.path, None, "G6's positive control"


class Record:
    def __init__(self):
        self.outcomes, self.collect, self.control, self.node = {}, [], "", None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        control = Control.from_parent(session, name="g6-positive-control")
        self.node = control.nodeid
        items.append(control)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.nodeid == self.node:
            if report.when == "call":
                self.control = report.outcome
            elif report.failed:  # a session fixture's set-up or tear-down, run beside the last item: still an error
                self.outcomes[report.nodeid + "@" + report.when] = "error"
            return
        node, when, expected = report.nodeid, report.when, hasattr(report, "wasxfail")
        if report.failed:
            if when == "setup":
                self.outcomes[node] = "error"
            elif when == "call":
                self.outcomes[node] = "failed"
            elif self.outcomes.get(node) in (None, "passed"):
                self.outcomes[node] = "error"
        elif report.skipped:
            self.outcomes[node] = "xfailed" if expected else "skipped"
        elif when == "call":
            self.outcomes[node] = "xpassed" if expected else "passed"

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if (self.control == "failed" and exitstatus == 1 and not self.collect
                and not {"failed", "error"} & set(self.outcomes.values())):
            session.exitstatus = 0 if self.outcomes else 5  # the code pytest gives the run without the control


record = Record()
code = pytest.main(sys.argv[3:], plugins=[record])
with open(result, "w", encoding="utf-8") as handle:
    json.dump({"outcomes": record.outcomes, "collect_errors": record.collect, "control": record.control}, handle)
sys.exit(code)
'''
#: The whole-suite runner's bootstrap (argv: repository, result file, pytest arguments): the same start mark and paths,
#: and a plugin that records pytest's own outcome for each test, so a class-based test, a parametrized one, a skip, an
#: expected failure, an unexpected pass and a set-up error are each named as pytest names them. A fixture is not a test
#: and never appears. The result file is written after the run, outside the repository.
_SUITE_BOOT = confine.CHILD_PRELUDE + '''import json, os, sys, pytest
sys.stderr.write("''' + STARTED + r'''\n")
sys.stderr.flush()
sys.dont_write_bytecode = True  # -I ignores PYTHONDONTWRITEBYTECODE, and pytest caches rewritten tests as bytecode
''' + _NETWORK_GUARD + r'''root, result = sys.argv[1], sys.argv[2]
src = os.path.join(root, "src")
if os.path.isdir(src):
    at = next((i for i, p in enumerate(sys.path) if "site-packages" in p or "dist-packages" in p), len(sys.path))
    sys.path.insert(at, src)
sys.path.insert(0, root)
# a test id may carry the folder the copy happens to be in (a parametrize over files beside the test): two copies of one
# repository must give one id, and the id must not tell where the server keeps its copies
ALIASES = (root, root.replace(os.sep, "/"), root.replace("\\", "\\\\"))


def norm(node):
    for alias in ALIASES:
        node = node.replace(alias, "<repo>")
    return node


class Record:
    def __init__(self):
        self.outcomes, self.collect, self.skipped = {}, [], []

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect.append(norm(report.nodeid))
        elif report.skipped:
            self.skipped.append(norm(report.nodeid))

    def pytest_runtest_logreport(self, report):
        node, when, expected = norm(report.nodeid), report.when, hasattr(report, "wasxfail")
        if report.failed:
            if when == "setup":
                self.outcomes[node] = "error"
            elif when == "call":
                self.outcomes[node] = "failed"
            elif self.outcomes.get(node) in (None, "passed"):
                self.outcomes[node] = "error"
        elif report.skipped:
            self.outcomes[node] = "xfailed" if expected else "skipped"
        elif when == "call":
            self.outcomes[node] = "xpassed" if expected else "passed"


record = Record()
code = pytest.main(sys.argv[3:], plugins=[record])
with open(result, "w", encoding="utf-8") as handle:
    json.dump({"outcomes": record.outcomes, "collect_errors": sorted(set(record.collect)),
               "collect_skipped": sorted(set(record.skipped))}, handle)
sys.exit(code)
'''
#: The most a whole-suite run may take (one run of a repository's tests, against the 120 s of one named test).
SUITE_TIMEOUT_S = 240
#: Directories searched for the suite (the one the frozen tests come from).
SUITE_DIR = "tests"
#: All an isolated run inherits from the server's environment (the paid-model block is set on top).
_ISOLATED_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
#: pytest's capture that opens no file: its default ("fd") opens the null device for standard input, which a Windows
#: AppContainer may be refused (GitHub's Windows Server 2025 image refuses it); "sys" captures each test's output too.
CAPTURE = "--capture=sys"


def no_null_device(folder: str | Path) -> list[str]:
    """pytest's options that keep a run from opening the null device: ``CAPTURE``, and a log file in the run's own
    ``folder`` (with none named, pytest's log file is the null device) at a level no record reaches, so it stays empty."""
    return [CAPTURE, f"--log-file={Path(folder) / 'pytest.log'}", "--log-file-level=100"]


def recorded(report: dict | None) -> dict | None:
    """The counts in a per-test run's own record (``_ISOLATED_BOOT``), in ``tally``'s shape and without the positive
    control; None when the run wrote no record, or when a test ran and the positive control was not recorded failing
    (audit 010 WF-003: then nothing the run recorded can be trusted). A run in which no test ran (a test that cannot be
    found, or a file that cannot be imported) never reached the control and is reported as it is: nothing passed."""
    if not isinstance(report, dict) or not isinstance(report.get("outcomes"), dict):
        return None
    if report.get("control") != "failed" and (report.get("control") or report["outcomes"]):
        return None
    out: dict = {}
    for outcome in report["outcomes"].values():
        out[str(outcome)] = out.get(str(outcome), 0) + 1
    if report.get("collect_errors"):
        out["error"] = out.get("error", 0) + len(report["collect_errors"])
    return out


def run_isolated(nodeid: str, repo: Path, timeout: float | None = None) -> bool:
    """One test node of code G6 did not write (the workforce's test bench): ``python -I`` (no PYTHONPATH, user
    site or current directory on the path), the real pytest imported before the repository joins the path, an
    empty config file from outside the repository (its pytest.ini, pyproject.toml or setup.cfg is never read),
    no plugin autoload, and a minimal environment carrying nothing of the server's but the paid-model block.
    The result is the runner's own record of each test (written outside the repository after the run), checked by a
    positive control that must be recorded failing (``_ISOLATED_BOOT``, audit 010 WF-003), never the process's output
    or exit code alone. Where ``g6.core.confine`` has a backend, the test process runs confined: it reads the
    interpreter's folders and ``repo``, writes only its own run folder (where the record goes), its container's own
    folder and, when G6 made it (the bench's copy), ``repo``, and reaches no network;
    ``LAST_RUN["isolation"]`` says whether it was. Without one, nothing runs (False, with the refusal in
    ``LAST_RUN["refused"]``) unless the person allowed such runs (``confine.allowed_by``; ``G6_REQUIRE_ISOLATION``
    refuses it whatever was allowed); an allowed run puts the code under test in the test process with the owner's
    permissions: this isolates the runner and the environment, not the host. Either way the code under test shares the
    process with the recorder, so code aimed at the recorder itself is not excluded.
    ``timeout``: a shorter limit than ``TIMEOUT_S`` (a learning round's replay of one probe, R3 review C1)."""
    LAST_RUN.clear()
    # as the operating system names it, so --rootdir and --confcutdir name the folder the child starts in (macOS reaches
    # every temp folder through a link, /var to /private/var)
    repo = Path(repo).resolve()
    if "::" not in nodeid or not (repo / nodeid.split("::")[0]).exists():
        return False
    limit = TIMEOUT_S if timeout is None else timeout
    env = {k: os.environ[k] for k in _ISOLATED_KEEP if k in os.environ}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", G6_LLM_BACKEND_OVERRIDE="ollama",
               OPENROUTER_API_KEY="dummy-not-a-key")
    python = _python()
    with confine.run_folder(prefix="g6_runner_") as config_dir:
        config, result = Path(config_dir) / "pytest.ini", Path(config_dir) / "result.json"
        config.write_text("[pytest]\n", encoding="utf-8")
        confined = _confined(config_dir, repo, python)
        LAST_RUN["isolation"] = confined.record()
        said = confine.refused(confined)
        if said:
            LAST_RUN.update(nodeid=nodeid, rc=None, started=False, timed_out=False, tally=None, stdout="", stderr=said,
                            refused=said)
            return False
        if confined.backend is not None:
            env["TMPDIR"] = _own_temp(config_dir)
        cmd = [python, "-I", "-c", _ISOLATED_BOOT, str(repo), str(result), nodeid, "-q", "-p",
               "no:cacheprovider", *no_null_device(config_dir), "--rootdir", str(repo), f"--confcutdir={repo}", "-c",
               str(config), "--import-mode=prepend"]
        try:
            proc = _run_owned(cmd, repo, env, limit, confined)
        except component_process.ExecutionInterrupted as exc:
            out, err = _decoded(exc.stdout), _decoded(exc.stderr)
            LAST_RUN.update(nodeid=nodeid, rc=None, started=STARTED in err, timed_out=not exc.stopped,
                            interrupted=exc.stopped, seconds=limit, tally=None, stdout=out[-2000:], stderr=err[-2000:])
            if exc.stopped:
                raise
            component_process.checkpoint()  # an outer allowance is a control failure, not a failing test
            return False
        except component_process.ContainmentFailure:
            raise  # no further work may assume that the owned process stopped
        except OSError as exc:  # never spawned: it did not start
            if isinstance(exc, confine.NotStarted):
                LAST_RUN["isolation"] = confine.not_started(LAST_RUN["isolation"], exc)
            LAST_RUN.update(nodeid=nodeid, rc=None, started=False, timed_out=False, tally=None, stdout="",
                            stderr=f"{type(exc).__name__}: {exc}")
            return False
        except subprocess.TimeoutExpired as exc:  # started (when the boot said so), then did not finish
            out, err = _decoded(exc.stdout), _decoded(exc.stderr)
            LAST_RUN.update(nodeid=nodeid, rc=None, started=STARTED in err, timed_out=True, seconds=limit,
                            tally=None, stdout=out[-2000:], stderr=err[-2000:])
            return False
        finally:
            _released(confined, LAST_RUN)
        try:
            report = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
    found = recorded(report)
    # ``tally``: the record's counts (None: no record, or a control not seen failing); stdout is kept for diagnosis only
    LAST_RUN.update(nodeid=nodeid, rc=proc.returncode, started=STARTED in proc.stderr, timed_out=False,
                    reported=isinstance(report, dict),
                    control=str(report.get("control") or "") if isinstance(report, dict) else "",
                    tally=found, stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:])
    return (proc.returncode == 0 and found is not None and found.get("passed", 0) >= 1 and not found.get("failed")
            and not found.get("error"))


def run_suite(repo: Path, timeout: float | None = None) -> dict:
    """The whole of ``repo``'s ``tests/`` run once under the isolated runner's rules (``run_isolated``: ``python -I``, the
    real pytest first, an empty config from outside the repository, no plugin autoload, a minimal environment), with
    pytest's own outcome for every test it ran: ``{"ran", "why", "outcomes": {node id: passed | failed | error |
    skipped | xfailed | xpassed}, "collect_errors": [file node ids], "seconds"}``. Collection errors do not stop the other
    files. ``ran`` is false, with the reason, when the runner never started, did not finish, found no tests or ended
    without reporting; a caller then knows nothing about the suite and must not read the empty outcomes as a result.
    ``isolation`` says how the tests ran (``run_isolated``'s confinement); with no backend and unconfined runs not
    allowed (or ``G6_REQUIRE_ISOLATION`` set), nothing runs and ``why`` is the refusal."""
    found = {"ran": False, "started": False, "why": "", "outcomes": {}, "collect_errors": [], "collect_skipped": [],
             "seconds": 0.0}
    repo = Path(repo).resolve()   # as run_isolated names it
    if not (Path(repo) / SUITE_DIR).is_dir():
        found["why"] = f"the repository has no {SUITE_DIR}/ directory"
        return found
    limit = SUITE_TIMEOUT_S if timeout is None else timeout
    env = {k: os.environ[k] for k in _ISOLATED_KEEP if k in os.environ}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", G6_LLM_BACKEND_OVERRIDE="ollama",
               OPENROUTER_API_KEY="dummy-not-a-key")
    started, python = time.perf_counter(), _python()
    with confine.run_folder(prefix="g6_suite_") as scratch:
        config, result = Path(scratch) / "pytest.ini", Path(scratch) / "result.json"
        config.write_text("[pytest]\n", encoding="utf-8")
        confined = _confined(scratch, Path(repo), python)
        found["isolation"] = confined.record()
        said = confine.refused(confined)
        if said:
            found["why"] = said
            return found
        if confined.backend is not None:
            env["TMPDIR"] = _own_temp(scratch)
        cmd = [python, "-I", "-c", _SUITE_BOOT, str(repo), str(result), SUITE_DIR, "-q", "-p", "no:cacheprovider",
               *no_null_device(scratch), "--rootdir", str(repo), f"--confcutdir={repo}", "-c", str(config),
               "--import-mode=prepend", "--continue-on-collection-errors"]
        try:
            proc = _run_owned(cmd, repo, env, limit, confined)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped:
                raise
            component_process.checkpoint()
            found.update(why=f"the tests did not finish: {exc}", seconds=time.perf_counter() - started,
                         started=STARTED in _decoded(exc.stderr))
            return found
        except component_process.ContainmentFailure:
            raise
        except OSError as exc:
            if isinstance(exc, confine.NotStarted):
                found["isolation"] = confine.not_started(found["isolation"], exc)
            found["why"] = f"the test runner could not start: {type(exc).__name__}"
            return found
        except subprocess.TimeoutExpired as exc:  # started when the boot said so, then did not finish
            found.update(why=f"the tests did not finish: timed out after {limit:g} s", seconds=time.perf_counter() - started,
                         started=STARTED in _decoded(exc.stderr))
            return found
        finally:
            _released(confined, found)
        found["seconds"] = time.perf_counter() - started
        if STARTED not in proc.stderr:
            found["why"] = "the test runner could not start"
            return found
        found["started"] = True
        try:
            report = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found["why"] = f"the test process exited with code {proc.returncode} before reporting"
            return found
    found["collect_skipped"] = list(report.get("collect_skipped") or [])
    if proc.returncode not in (0, 1) or not isinstance(report.get("outcomes"), dict) or not report["outcomes"]:
        found["collect_errors"] = list(report.get("collect_errors") or [])
        found["why"] = (f"the test process ended with code {proc.returncode}" if proc.returncode not in (0, 1, 5)
                        else f"no test could be collected ({len(found['collect_errors'])} test files failed to import)"
                        if found["collect_errors"] else "no tests were collected")
        return found
    found.update(ran=True, outcomes=dict(report["outcomes"]), collect_errors=list(report.get("collect_errors") or []))
    return found


def _python() -> str:
    """The interpreter both isolated runners start: this one."""
    return sys.executable


def _confined(folder: str, repo: Path, python: str) -> confine.Plan:
    """The runner's confinement: its own run folder read and write, and ``repo`` too when G6 made it (the bench's copy:
    a test may write into it, and the bench checks its test files afterwards); otherwise ``repo`` is read only. The
    interpreter's folders are read only."""
    mine = confine.owned(repo)
    return confine.plan(confine.Confinement(
        read_write=(folder, *([str(repo)] if mine else [])),
        read_only=(*confine.interpreter_paths(python), *([] if mine else [str(repo)]))), python)


def _released(confined: confine.Plan, holder: MutableMapping) -> None:
    """End the run's container after the run; a profile G6 could not delete is said in the run's ``isolation``."""
    left = confined.release()
    if left:
        holder["isolation"] = {**holder["isolation"], "cleanup": left}


def _own_temp(folder: str) -> str:
    """A confined run's own temporary folder (Python's ``tempfile`` reads TMPDIR first), inside its run folder."""
    path = Path(folder) / "tmp"
    path.mkdir(exist_ok=True)
    return str(path)


def _run_owned(cmd: list[str], repo: Path, env: dict[str, str], seconds: float,
               confined: confine.Plan | None = None) -> subprocess.CompletedProcess:
    with component_process.window(seconds):
        result = component_process.run_contained(cmd, cwd=repo, env=env, confined=confined)
    return subprocess.CompletedProcess(cmd, result.returncode, _decoded(result.stdout), _decoded(result.stderr))


def _decoded(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")


@dataclass(frozen=True)
class Truth:
    seed_id: str
    repo: Path
    claims: dict[str, dict]  # id -> {"phrases": [...], "test": "file::node"}

    @classmethod
    def load(cls, seed) -> Truth:
        """From a market seed (scripts.sim_market.seeds.Seed: anything with ``.id`` and ``.dir``)."""
        data = json.loads((seed.dir / "truth.json").read_text(encoding="utf-8"))
        return cls(seed.id, seed.dir / "repo", data["claims"])

    def check(self, claim_id: str, *, runner=None) -> bool:
        claim = self.claims.get(claim_id)
        if not claim:
            return False
        # audit 011, VEN-002: the default is the isolated runner, which installs the network guard first
        return bool((runner or run_isolated)(claim["test"], self.repo))

    def extract(self, text: str) -> Extraction:
        low = (text or "").lower()
        bound = [cid for cid, c in self.claims.items() if any(p.lower() in low for p in c["phrases"])]
        spans = sorted((m.start(), m.end()) for shape in UNBOUND_SHAPES for m in shape.finditer(text or "")
                       if not _NEGATED_BEFORE.search(text[max(0, m.start() - 40):m.start()]))
        merged: list[list[int]] = []
        for start, end in spans:  # overlapping shapes ("ATO-approved" inside "is ATO-approved") count once
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        unbound = [text[s:e].strip() for s, e in merged]
        return Extraction(bound, unbound)


def is_false_claim(t: Truth, text: str, *, runner=None) -> list[str]:
    ex = t.extract(text)
    bad = [cid for cid in ex.bound if not t.check(cid, runner=runner)]
    bad += [f"unbound: {u}" for u in ex.unbound]
    return bad
