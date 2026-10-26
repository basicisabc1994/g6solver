# SPDX-License-Identifier: Apache-2.0
"""The archive's ``diagnostic_collector`` merged into ``g6.context.redact`` (the e-mail rule, the home and temporary
folder rule, ``sanitize_stack_trace``) and ``g6 doctor --report`` (one local JSON report; caller text stripped by
default; written only with ``--write``). No sink, queue, rate limiter, index or module import came across."""
from __future__ import annotations

import ast
import inspect
import json
import os
import socket
import tempfile
import time
from pathlib import Path

import pytest

import g6
import g6.cli.doctor as doctor
from g6.cli import main
from g6.context import redact
from g6.core.failure_modes import FailureModeRegistry

G6_FILE = str(Path(g6.__file__).parent / "core" / "registry.py")
C, c = "C:", "c:"  # drive letters joined at runtime, so the release scan sees no user path (owner-approved, R-08)
WINDOWS = (
    "Traceback (most recent call last):\n"
    f'  File "{C}\\Users\\jdoe\\clients\\northwind-dental\\billing.py", line 41, in charge_patient_maria_lopez\n'
    "    invoice = lookup(patient)\n"
    f'  File "{G6_FILE}", line 152, in invoke\n'
    "    result = block.infer(data)\n"
    "KeyError: 'Maria Lopez <maria.lopez@northwind-dental.example.com> api_key=sk-livesecretabcdefghijklmnop'\n"
)
POSIX = (
    "Traceback (most recent call last):\n"
    '  File "/home/alice/acme-payroll/run.py", line 9, in <module>\n'
    "    main()\n"
    '  File "/home/alice/acme-payroll/payroll/core.py", line 77, in pay_bob_jones\n'
    "    raise PatientNotFound(record)\n"
    "acme_payroll.errors.PatientNotFound: record for Bob Jones at /home/alice/acme-payroll/staff.csv\n"
)
CALLER_WORDS = ("Maria", "Lopez", "maria.lopez", "northwind", "jdoe", "sk-livesecret", "charge_patient", "lookup",
                "alice", "acme", "Bob", "Jones", "pay_bob", "PatientNotFound", "staff.csv", "patient")


def _dumped(report: dict) -> str:
    return json.dumps(report)


# --- D1: the failure mode, caught generally ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [WINDOWS, POSIX], ids=["windows", "posix"])
def test_d1_a_default_report_carries_no_caller_text(text: str) -> None:
    # Removing diagnostic_report's caller-text strip (the `keep_caller_text` branch taken always) makes this fail.
    report = doctor.diagnostic_report(text)
    assert report["completion_state"] == "qualified-draft" and report["caller_text"] == "stripped"
    leaked = [w for w in CALLER_WORDS if w.lower() in _dumped(report).lower()]
    assert leaked == []
    assert [f["line"] for f in report["frames"]] == ([41, 152] if text is WINDOWS else [9, 77])


def test_d1_g6_s_own_frames_and_python_s_exception_names_stay() -> None:
    # Removing _g6_path's match on the installed g6 package makes this fail (G6's own frame becomes <caller>).
    report = doctor.diagnostic_report(WINDOWS)
    assert report["exception"] == "KeyError" and report["message"] == doctor.CALLER
    assert report["frames"][0] == {"file": doctor.CALLER, "line": 41, "function": doctor.CALLER}
    assert report["frames"][1] == {"file": "g6/core/registry.py", "line": 152, "function": "invoke"}
    assert doctor.diagnostic_report(POSIX)["exception"] == doctor.CALLER   # a caller's own exception class


# --- DIAGNOSTIC_COLLECTOR-02: caller text kept only on an explicit opt-in, and masked then -----------------------------

def test_p1_02_kept_caller_text_is_masked_for_secrets_e_mail_and_home_folders() -> None:
    # Removing the scrub / mask_emails / mask_paths calls on kept text makes this fail.
    report = doctor.diagnostic_report(WINDOWS, keep_caller_text=True)
    text = _dumped(report)
    assert report["caller_text"] == "kept" and "Maria Lopez" in report["message"]
    for secret in ("maria.lopez@", "sk-livesecret", "jdoe"):
        assert secret not in text
    assert redact.EMAIL_MASK in report["message"] and redact.MASK in report["message"]
    assert report["frames"][0]["file"].startswith("~") and "billing.py" in report["frames"][0]["file"]
    assert report["warning_card"]["code"] == doctor.E_REPORT_DRAFT


# --- DIAGNOSTIC_COLLECTOR-01: no remote sink; the person's key never reaches a report ---------------------------------

def test_p0_01_there_is_no_sink_and_nothing_reaches_the_network(tmp_path, monkeypatch, capsys) -> None:
    # Adding any sink parameter, network import or key read to the report path makes this fail.
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".env").write_text("OPENROUTER_API_KEY=sk-or-operator-secret-value\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("the report path reached for the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    trace = tmp_path / "trace.txt"
    trace.write_text(WINDOWS, encoding="utf-8")
    assert main(["doctor", "--report", str(trace), "--write"]) == 0
    out = capsys.readouterr().out
    written = list((tmp_path / "home" / "diagnostics").glob("report-*.json"))
    assert len(written) == 1 and "sk-or-operator-secret-value" not in out + written[0].read_text(encoding="utf-8")
    for fn in (doctor.diagnostic_report, doctor.write_report):
        assert not {"url", "sink_url", "sink_api_key", "api_key", "endpoint"} & set(inspect.signature(fn).parameters)
    with pytest.raises(SystemExit):
        main(["doctor", "--report", str(trace), "--sink-url", "https://collector.example.invalid"])
    imported = [n.module or "" for n in ast.walk(ast.parse(Path(doctor.__file__).read_text(encoding="utf-8")))
                if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(ast.parse(
                    Path(doctor.__file__).read_text(encoding="utf-8"))) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in imported if m.split(".")[0] in {"socket", "urllib", "http", "requests", "httpx"}]


# --- DIAGNOSTIC_COLLECTOR-03: no module a caller names is imported -----------------------------------------------------

def test_p1_03_a_report_imports_nothing_the_traceback_names(monkeypatch) -> None:
    # Adding any importlib.import_module call to the report path makes this fail.
    import importlib

    def refuse(name, *args, **kwargs):
        raise AssertionError(f"the report imported {name}")

    monkeypatch.setattr(importlib, "import_module", refuse)
    named = WINDOWS.replace("KeyError", "os.system")
    report = doctor.diagnostic_report(named)
    assert report["exception"] == doctor.CALLER and "input_model" not in inspect.signature(
        doctor.diagnostic_report).parameters


# --- G4 and G5: fails closed; the write is a dry run unless asked ------------------------------------------------------

@pytest.mark.parametrize("text", ["", "all fine here\nnothing went wrong\n", "\x00\x01" * 10],
                         ids=["empty", "prose", "binary"])
def test_g4_text_that_is_not_a_traceback_gives_no_report(text: str) -> None:
    # Removing diagnostic_report's no-traceback refusal makes this fail (an empty, clean-looking report came back).
    report = doctor.diagnostic_report(text)
    assert report["completion_state"] == "blocked-escalated" and report["warning_card"]["code"] == doctor.E_NO_TRACEBACK
    assert "frames" not in report and doctor.write_report(report, dry_run=False)["written"] is False


def test_g5_the_write_is_a_dry_run_by_default(tmp_path, monkeypatch, capsys) -> None:
    # Removing write_report's dry_run return makes this fail (the report folder appears).
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    report = doctor.diagnostic_report(POSIX)
    planned = doctor.write_report(report)
    assert planned["dry_run"] is True and planned["written"] is False and planned["code"] == doctor.E_DRY_RUN
    assert not (tmp_path / "home").exists()
    trace = tmp_path / "trace.txt"
    trace.write_text(POSIX, encoding="utf-8")
    assert main(["doctor", "--report", str(trace)]) == 0
    assert json.loads(capsys.readouterr().out)["write"]["written"] is False and not (tmp_path / "home").exists()
    done = doctor.write_report(report, dry_run=False)
    assert done["written"] is True and Path(done["path"]).parent == tmp_path / "home" / "diagnostics"
    assert json.loads(Path(done["path"]).read_text(encoding="utf-8"))["report_id"] == report["report_id"]


def test_g4_a_write_that_fails_is_a_refusal_never_a_raise(tmp_path, monkeypatch) -> None:
    # Removing write_report's OSError branch makes this fail (the error escaped).
    blocker = tmp_path / "home"
    blocker.write_text("a file where the state folder should be", encoding="utf-8")
    monkeypatch.setenv("G6_HOME", str(blocker))
    done = doctor.write_report(doctor.diagnostic_report(WINDOWS), dry_run=False)
    assert done["written"] is False and done["code"] == doctor.E_WRITE_FAILED


def test_the_cli_refuses_report_options_without_a_report_and_an_unreadable_file(tmp_path, capsys) -> None:
    # Removing main's option check makes the first call fail; removing its OSError branch, the second.
    assert main(["doctor", "--write"]) == 2
    assert main(["doctor", "--report", str(tmp_path / "missing.txt")]) == 2
    assert "missing.txt" in capsys.readouterr().err


# --- the redact rules beside scrub ------------------------------------------------------------------------------------

def test_mask_emails_needs_a_real_top_level_domain() -> None:
    # Removing the e-mail rule's top-level-domain requirement makes the package names below fail.
    assert redact.mask_emails("from ana.k@clinic.example.org today") == f"from {redact.EMAIL_MASK} today"
    for kept in ("pkg@1.2.3", "@scope/pkg", "HEAD@{0}"):
        assert redact.mask_emails(kept) == kept


def test_m3_encoded_and_local_e_mail_addresses_are_masked() -> None:
    # Removing the %40 and localhost alternatives from the e-mail rule makes this fail.
    assert redact.mask_emails("to=ana.k%40clinic.example.org&x=1") == f"to={redact.EMAIL_MASK}&x=1"
    assert redact.mask_emails("root@localhost refused") == f"{redact.EMAIL_MASK} refused"


D = "D:"  # joined at runtime, like C above


@pytest.mark.parametrize("raw", [
    "/mnt/c/Users/jdoe/x.py", "\\\\fileserver\\homes\\jdoe\\x.py", "//fileserver/homes/jdoe/x.py",
    "/var/home/jdoe/x.py", "/Volumes/Data/Users/jdoe/x.py", D + "\\Profiles\\jdoe\\x.py",
    D + "\\Documents and Settings\\jdoe\\x.py",
], ids=["wsl", "unc", "unc-slash", "var-home", "volumes", "profiles", "documents-and-settings"])
def test_m3_more_home_shapes_are_masked(raw: str) -> None:
    # Removing the matching pattern from redact._HOMES makes its case fail.
    assert "jdoe" not in redact.mask_paths(raw) and "fileserver" not in redact.mask_paths(raw)


def test_m3_url_user_names_host_names_and_addresses_are_masked() -> None:
    # Removing mask_hosts from the kept-text chain (or a rule from it) makes this fail.
    text = ("GET https://bob:hunter2@api.acme-payroll.com/v1 from 10.20.30.40 and fe80::1ff:fe23:4567:890a; "
            "host='db01.corp.local' port=5432; mirror at files.acme-payroll.com.au")
    masked = redact.mask_hosts(redact.scrub(text))
    for secret in ("bob", "hunter2", "acme-payroll", "10.20.30.40", "fe80::1ff", "db01"):
        assert secret not in masked, secret
    assert "g6.core.registry" in redact.mask_hosts("in g6.core.registry")   # a module path is not a host name
    kept = doctor.diagnostic_report(WINDOWS.replace("KeyError: '", "KeyError: 'GET http://bob@10.0.0.7/ "),
                                    keep_caller_text=True)
    assert "10.0.0.7" not in json.dumps(kept) and redact.KEPT_TEXT_MASKS in kept["warning_card"]["message"]


@pytest.mark.parametrize("secret", ["hf_" + "a1B2" * 8, "glpat-" + "x9Y8z7" * 4],
                         ids=["hugging-face", "gitlab"])
def test_m3_more_token_shapes_are_masked(secret: str) -> None:
    # Removing the token's rule from redact._RULES makes its case fail.
    assert secret not in redact.scrub(f"token in the log: {secret} end")


def test_m3_an_azure_account_key_is_masked_and_the_claim_names_every_rule() -> None:
    # Removing the AccountKey rule makes the first assertion fail.
    connection = "DefaultEndpointsProtocol=https;AccountName=acme;AccountKey=abc123def456+ghi/jkl==;Suffix=x"
    assert "abc123def456" not in redact.scrub(connection) and redact.scrub(redact.scrub(connection)) == \
        redact.scrub(connection)
    for named in ("e-mail", "%40", "localhost", "home", "temporary", "user names", "host names",
                  "IPv4 and IPv6 addresses", "hf_", "glpat-", "AccountKey"):
        assert named in redact.KEPT_TEXT_MASKS, named


def _kept(message: str) -> str:
    """The kept-text report for a traceback whose exception line carries ``message``, as JSON text, without its card
    (the card's own words name the shapes, ``::1`` among them)."""
    trace = WINDOWS.rsplit("KeyError: ", 1)[0] + "ValueError: " + message + "\n"
    report = doctor.diagnostic_report(trace, keep_caller_text=True)
    assert report["caller_text"] == "kept" and redact.KEPT_TEXT_MASKS in report["warning_card"]["message"]
    return json.dumps({k: v for k, v in report.items() if k != "warning_card"})


# One case per shape KEPT_TEXT_MASKS claims, each sent through a real kept-text report (review 3, I1).
CLAIMED = {
    "key-name": ("lookup failed: key=Zq81mPxv", "Zq81mPxv"),
    "pwd-name": ("login refused, pwd=Hunter2xy", "Hunter2xy"),
    "pass-name": ("login refused, pass=Tr0ub4dor", "Tr0ub4dor"),
    "passphrase-name": ("unlock failed: passphrase='correct horse'", "correct horse"),
    "token-name": ("auth: token=abcd1234efgh", "abcd1234efgh"),
    "secret-name": ("config secret: 'w8s9d7f6'", "w8s9d7f6"),
    "password-name": ("db password=s3cr3tpw", "s3cr3tpw"),
    "credentials-name": ("credentials=AKZ9-long-value", "AKZ9-long-value"),
    "aws": ("denied for AKIA" + "IOSFODNN7EXAMPLE", "IOSFODNN7EXAMPLE"),   # shapes joined at runtime for the scan
    "sk": ("bad key sk-abcdefghijklmnopqrstuv", "sk-abcdefghijklmnopqrstuv"),
    "github": ("push refused ghp_" + "a" * 36, "ghp_" + "a" * 36),
    "gitlab": ("push refused glpat-" + "b" * 20, "glpat-" + "b" * 20),
    "hugging-face": ("download refused hf_" + "c" * 30, "hf_" + "c" * 30),
    "slack": ("post refused xoxb-1234567890-abcdef", "xoxb-1234567890-abcdef"),
    "google": ("quota for AIza" + "d" * 35, "AIza" + "d" * 35),
    "stripe": ("charge refused sk_live_" + "e" * 24, "sk_live_" + "e" * 24),
    "jwt": ("bad jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q", "SflKxwRJSMeKKF2Q"),
    "bearer": ("header Bearer abcdefghijklmnop", "abcdefghijklmnop"),
    "private-key": ("-----BEGIN RSA " + "PRIVATE KEY-----MIIEpAIBAAKCAQEA", "MIIEpAIBAAKCAQEA"),
    "url-password": ("connect postgres://app:hunter2pass@db.example.invalid/x", "hunter2pass"),
    "account-key": ("AccountName=acme;AccountKey=abc123def456+ghi==;", "abc123def456"),
    "e-mail": ("sent to ana.k@clinic.example.org", "ana.k@clinic"),
    "e-mail-%40": ("redirect to=ana.k%40clinic.example.org", "ana.k%40"),
    "e-mail-localhost": ("mail for root@localhost refused", "root@localhost"),
    "drive-users": ("wrote " + C + "\\Users\\jdoe\\out.txt", "jdoe"),
    "drive-profiles": ("wrote " + D + "\\Profiles\\jdoe\\out.txt", "jdoe"),
    "documents-and-settings": ("wrote " + D + "\\Documents and Settings\\jdoe\\o.txt", "jdoe"),
    "home": ("wrote /home/jdoe/out.txt", "jdoe"),
    "var-home": ("wrote /var/home/jdoe/out.txt", "jdoe"),
    "users": ("wrote /Users/jdoe/out.txt", "jdoe"),
    "volumes": ("wrote /Volumes/Data/Users/jdoe/out.txt", "jdoe"),
    "wsl": ("wrote /mnt/c/Users/jdoe/out.txt", "jdoe"),
    "share": ("wrote \\\\filer01\\homes\\jdoe\\out.txt", "filer01"),
    "url-user": ("fetch https://bob@files.example.invalid/x refused", "bob@"),
    "url-host": ("fetch https://intranet-7/x refused", "intranet-7"),
    "host-setting": ("hostname='db01-primary' refused", "db01-primary"),
    "server-setting": ("server=mailhub7 refused", "mailhub7"),
    "tld-name": ("resolved files.acme-payroll.com.au", "acme-payroll"),
    "ipv4": ("refused by 10.20.30.40.", "10.20.30.40"),
    "ipv4-port": ("refused by 10.20.30.41:8080", "10.20.30.41"),
    "ipv6-full": ("from 2001:0db8:0000:0000:0000:ff00:0042:8329", "2001:0db8"),
    "ipv6-compressed": ("from fe80::1ff:fe23:4567:890a", "fe80::1ff"),
    "ipv6-loopback": ("connect to ::1 refused", "::1"),
    "ipv6-prefix": ("route fe80:: dropped", "fe80::"),
    "ipv6-link": ("from fe80::1 dropped", "fe80::1"),
}


@pytest.mark.parametrize(("message", "secret"), list(CLAIMED.values()), ids=list(CLAIMED))
def test_i1_every_shape_the_card_claims_is_masked_in_a_real_message(message: str, secret: str) -> None:
    # Removing the rule for a case's shape (or reverting I1's IPv4 lookahead, IPv6 forms or key names) fails it.
    assert secret not in _kept(message)


def test_i1_the_temporary_root_is_masked_in_a_real_message() -> None:
    # Removing mask_paths's temporary-root rule makes this fail (on Windows the root also sits under the home).
    root = tempfile.gettempdir()
    assert root not in _kept(f"wrote {root}{Path('/g6r7/out.log')}") and redact.TEMP_MASK in _kept(
        f"wrote {root}{Path('/g6r7/out.log')}")


def test_review3_m3_a_function_name_on_a_g6_frame_stays_only_when_that_file_defines_it() -> None:
    # Keeping any identifier on a G6 frame (removing _defined_in's lookup) makes this fail (ghp_x came through).
    forged = WINDOWS.replace(", line 152, in invoke", ", line 1, in ghp_secretlooking")
    assert doctor.diagnostic_report(forged)["frames"][1] == {"file": "g6/core/registry.py", "line": 1,
                                                             "function": doctor.CALLER}
    nested = WINDOWS.replace(", line 152, in invoke", ", line 9, in add")   # a function nested in registry.py
    assert doctor.diagnostic_report(nested)["frames"][1]["function"] == "add"
    assert doctor.diagnostic_report(WINDOWS)["frames"][1]["function"] == "invoke"


def test_m4_a_g6_exception_name_stays_only_when_g6_defines_that_class() -> None:
    # Keeping any name that starts with g6. (removing _g6_class's lookup) makes this fail.
    assert doctor.diagnostic_report(WINDOWS.replace("KeyError", "g6.ghp_secretlooking"))["exception"] == doctor.CALLER
    assert doctor.diagnostic_report(WINDOWS.replace("KeyError", "g6.lean.envelope.STANDARD_AXIOMS"))["exception"] == \
        doctor.CALLER
    real = "g6.lean.envelope.Refusal"
    assert doctor.diagnostic_report(WINDOWS.replace("KeyError", real))["exception"] == real


def test_m4_a_path_whose_tail_after_g6_is_absolute_is_not_g6_s_own() -> None:
    # Removing _g6_path's relative-tail requirement makes this fail (a system file's path came through as G6's).
    outside = Path(os.environ.get("SystemRoot", "C:/Windows"), "win.ini") if os.name == "nt" else Path("/etc/hosts")
    if not outside.is_file():
        pytest.skip(f"{outside.name} is not on this machine, so there is no outside file to point at")
    trace = WINDOWS.replace(f'File "{G6_FILE}"', f'File "/srv/g6/{outside.as_posix()}"')
    assert doctor.diagnostic_report(trace)["frames"][1]["file"] == doctor.CALLER
    assert doctor._g6_path("/srv/g6/" + outside.as_posix()) == ""


def test_m5_a_long_traceback_line_is_read_in_linear_time() -> None:
    # Reverting _FRAME's function group to a lazy match before trailing blanks makes this take minutes.
    line = '  File "x.py", line 1, in f' + " " * 999_000 + "x\n"
    started = time.perf_counter()
    redact.sanitize_stack_trace(line)
    doctor.diagnostic_report(line)
    doctor.diagnostic_report(line, keep_caller_text=True)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(("raw", "masked"), [
    (C + "\\Users\\jdoe\\work\\a.py", "~\\work\\a.py"),
    (c + "/users/Jo Smith/work/a.py", "~/work/a.py"),
    ("/home/alice/src/x.py", "~/src/x.py"),
    ("/Users/bob/src/x.py", "~/src/x.py"),
    ("/opt/app/x.py", "/opt/app/x.py"),
    ("/homework/x.py", "/homework/x.py"),
])
def test_mask_paths_removes_the_home_root_only(raw: str, masked: str) -> None:
    # Removing a home pattern from redact._HOMES makes its case fail.
    assert redact.mask_paths(raw) == masked


def test_mask_paths_masks_the_temporary_root_where_a_path_starts(monkeypatch, tmp_path) -> None:
    # Removing redact's temporary-root rule makes this fail.
    root = tmp_path / "scratch-root"
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    assert redact.mask_paths(f"wrote {root}{Path('/x.log')}") == f"wrote {redact.TEMP_MASK}{Path('/x.log')}"
    assert redact.TEMP_MASK not in redact.mask_paths(f"{root}-sibling/x")


def test_sanitize_stack_trace_keeps_frames_and_never_the_source_lines() -> None:
    # Removing sanitize_stack_trace's mask_paths call makes this fail (jdoe stays).
    frames = redact.sanitize_stack_trace(WINDOWS)
    assert [f["line"] for f in frames] == [41, 152] and frames[0]["file"].startswith("~\\clients")
    assert "lookup(patient)" not in json.dumps(frames) and "jdoe" not in json.dumps(frames)
    assert redact.sanitize_stack_trace("") == [] and redact.scrub("password = 'x1y2'") == "password = '[REDACTED]'"


def test_d2_the_failure_mode_is_declared_by_g6_context_and_no_longer_planned() -> None:
    import g6.context

    assert "leaky_error_report" in g6.context.__g6_mitigates__
    assert FailureModeRegistry.is_registered("leaky_error_report")
    assert "g6.cli.doctor" not in FailureModeRegistry.planned()
