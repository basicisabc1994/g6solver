# SPDX-License-Identifier: Apache-2.0
"""``g6 doctor``: a read-only check of this installation. It writes nothing and calls no network.

Each check is ``ok``, ``warn`` (works, with a limitation) or ``fail`` (something the user must fix),
with a plain-language fix. Importing components is the only code it runs, but for one test run on Linux: the
isolation check starts bubblewrap's test run, in temporary folders it removes, as every first confined run does.

``g6 doctor --report <traceback file>`` (merged from the archive's diagnostic collector) builds one local error report
from a saved Python traceback: the exception's name, each frame's file, line and function, and this installation's
versions, with no source line. Caller text is stripped by default: the message, and every name and path that is not
G6's own, become ``<caller>``; ``--keep-caller-text`` keeps them with secrets, e-mail addresses and home and temporary
roots masked (``g6.context.redact``). It prints the report and writes nothing unless ``--write`` is given, and then only
under ``$G6_HOME/diagnostics``. Nothing is sent anywhere: there is no sink, queue or upload, and no module the
traceback names is imported.
"""
from __future__ import annotations

import ast
import builtins
import functools
import importlib.util
import json
import os
import platform
import re
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

EXTRAS = {
    "mcp": (("fastmcp", "mcp"), "the g6-mcp server for coding agents", 'pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"'),
    "anthropic": (("anthropic",), "Claude as the AI model", 'pip install "g6[anthropic] @ git+https://github.com/basicisabc1994/g6solver"'),
    "abstraction": (("hypothesis", "numpy"), "the abstraction component's property checks",
                    'pip install "g6[abstraction] @ git+https://github.com/basicisabc1994/g6solver"'),
}
@dataclass(frozen=True)
class Check:
    name: str
    status: str   # ok | warn | fail
    detail: str
    fix: str = ""


def _python() -> Check:
    v = sys.version_info
    ok = (3, 12) <= v[:2] < (3, 14)
    return Check("python", "ok" if ok else "fail", f"Python {v.major}.{v.minor}.{v.micro}",
                 "" if ok else "Install Python 3.12 or 3.13 from python.org, then reinstall G6.")


def _package() -> Check:
    try:
        return Check("g6", "ok", f"g6 {metadata.version('g6')} installed")
    except metadata.PackageNotFoundError:
        return Check("g6", "warn", "g6 is importable but not installed as a package (source checkout?)",
                     "From the G6 folder run: pip install .")


def _home() -> Check:
    from g6.core.storage import g6_home

    home = g6_home()
    if home.is_dir():
        if os.access(home, os.W_OK):
            return Check("state folder", "ok", f"{home} (writable)")
        return Check("state folder", "fail", f"{home} is not writable", "Fix the folder's permissions or set G6_HOME.")
    parent = next((p for p in home.parents if p.exists()), None)
    if parent is not None and os.access(parent, os.W_OK):
        return Check("state folder", "warn", f"{home} does not exist yet", "Run: g6 init")
    return Check("state folder", "fail", f"{home} cannot be created", "Set G6_HOME to a folder you can write to.")


def _sqlite() -> Check:
    try:
        sqlite3.connect(":memory:").execute("select 1").close()
        return Check("sqlite", "ok", f"SQLite {sqlite3.sqlite_version}")
    except sqlite3.Error as exc:  # pragma: no cover - broken Python builds only
        return Check("sqlite", "fail", f"SQLite unusable: {exc}", "Reinstall Python from python.org.")


def _extras() -> list[Check]:
    out = []
    for extra, (modules, purpose, fix) in EXTRAS.items():
        missing = [m for m in modules if importlib.util.find_spec(m) is None]
        out.append(Check(f"extra: {extra}", "warn" if missing else "ok",
                         f"needed for {purpose}" + (f"; missing {', '.join(missing)}" if missing else ""),
                         fix if missing else ""))
    return out


def _keys() -> Check:
    """The person's key file: the names it gives a value and what is wrong with it, never a value."""
    from g6.core import keys

    file, found, problems = keys.path(), keys.names(), keys.problems()
    named = f"keys for {', '.join(found)}" if found else "no keys"
    if problems:
        return Check("key file", "warn", f"{file}: {named}; " + "; ".join(problems),
                     "Fix the lines named above; G6 shows names and line numbers, never values.")
    return Check("key file", "ok", f"{file}: {named} (G6 reads keys only from this file and never writes it)",
                 "" if found else "Optional: add NAME=value lines for a paid model or service.")


def _host_model(provider: str, keys: dict[str, str]) -> Check:
    """Claude Code or Codex, signed in as the person, as G6's model: found where (nothing is run), or how to install
    it; and ignored when only the environment names it."""
    from g6.core import host_models
    from g6.core.keys import path
    from g6.core.llm import LLMError

    caller = host_models.CALLERS[provider]
    program = caller.program
    if (keys.get("G6_LLM_PROVIDER") or "").strip().lower() != provider:
        return Check("AI model", "warn", f"G6_LLM_PROVIDER={provider} is set only in the environment, so G6 ignores it "
                                         "and calls no model: an environment is not your approval",
                     f"To use your {program.name} sign-in as G6's model, add the line G6_LLM_PROVIDER={provider} to "
                     f"{path()}.")
    try:
        where = caller().locate()
    except LLMError:
        return Check("AI model", "fail", f"{provider} selected, but {program.name} is not found (no "
                                         f"{program.command} on PATH, and {program.env} names none)", program.install)
    shell = f"; {host_models.CODEX_SHELL_NOTE}" if provider == "codex" else ""
    return Check("AI model", "ok", f"{provider} selected: {program.name} at {where}, signed in as you; not contacted "
                                   f"by doctor{shell}")


def _model(env: dict[str, str], keys: dict[str, str]) -> Check:
    from g6.core.keys import path
    from g6.core.llm import HOST_PROVIDERS, PROVIDER_KEYS

    provider = (env.get("G6_LLM_PROVIDER") or "none").strip().lower()
    if provider == "none":
        return Check("AI model", "ok", "none configured — everything that needs no model works",
                     f"Optional: add G6_LLM_PROVIDER=claude_code (your Claude Code sign-in), codex, ollama (free, "
                     f"local), or anthropic/openai/openrouter with its key, to {path()}.")
    if provider in HOST_PROVIDERS:
        return _host_model(provider, keys)
    if provider == "ollama":
        return Check("AI model", "ok", f"Ollama at {env.get('OLLAMA_BASE_URL') or 'http://localhost:11434'} "
                     "(not contacted by doctor)", "If calls fail, start Ollama and pull a model.")
    if provider in PROVIDER_KEYS:
        key = PROVIDER_KEYS[provider]
        has = bool(keys.get(key))
        if provider == "anthropic" and importlib.util.find_spec("anthropic") is None:
            return Check("AI model", "fail", "anthropic selected but the SDK is missing",
                         'pip install "g6[anthropic] @ git+https://github.com/basicisabc1994/g6solver"')
        unused = (f"; {key} is set in the environment, which G6 does not use"
                  if not has and env.get(key) else "")
        return Check("AI model", "ok" if has else "fail",
                     f"{provider} selected; {key} {'is' if has else 'is not'} in the key file{unused}",
                     "" if has else f"Add the line {key}=<your key> to {path()}, or choose another provider.")
    return Check("AI model", "fail", f"unknown G6_LLM_PROVIDER {provider!r}",
                 "Use none, claude_code, codex, ollama, anthropic, openai or openrouter.")


def _isolation() -> Check:
    """Whether untrusted code (patches, generated scripts, a repository's tests) runs confined by the operating system
    (``g6.core.confine``). On Windows it is read from the access lists alone: nothing is started or created. On Linux
    bubblewrap's test run starts, in temporary folders removed after it, since its settings alone cannot say whether
    it works. ``fail`` only where the boundary leaked; otherwise ``warn``, with the fix for this system and whether G6
    refuses such code here or the person allowed it (``confine.allowed_by``)."""
    from g6.core import confine

    linux = sys.platform.startswith("linux")
    pythons = list(dict.fromkeys([sys.executable, *([os.environ["G6_PYTHON"]] if os.environ.get("G6_PYTHON") else [])]))
    for python in pythons:
        name, why = confine.backend(python, probe=linux)
        if name is None:
            # CORE_SANDBOX-01 (owner ruling, 2026-10-27): with no boundary G6 refuses untrusted code unless the person
            # allowed it once; either way the person reads what this computer needs
            allowed = "" if confine.required() else confine.allowed_by()
            here = (f"Here no boundary can confine untrusted code run with {python}, and {allowed} allowed it, so it "
                    "runs with your permissions" if allowed else
                    f"Here no boundary can confine untrusted code run with {python}, so G6 refuses to run it")
            return Check("isolation", "fail" if confine.BWRAP_LEAKED in why else "warn",
                         f"{confine.UNCONFINED_WARNING} {here}: {why}",
                         confine.fix_for(why) or "Run G6 inside a virtual machine or a container of its own, or, to run "
                                                 "such code without a boundary, choose it once with g6 setup.")
    said = "started" if all(confine.started(python) for python in pythons) else "access list ok; not started"
    where = ("inside bubblewrap (a private loopback and no other network, only its run folder and a /tmp of its own "
             "writable; G6 test-starts each interpreter there before its first confined run)"
             if name == confine.BUBBLEWRAP else
             f"in a Windows {name} (no network, only its run folder writable; G6 test-starts each interpreter there "
             "before its first confined run)")
    return Check("isolation", "ok", f"{said}: untrusted code run with {', '.join(pythons)} can run {where}")


def _components() -> list[Check]:
    from g6.core.registry import get_registry

    reg = get_registry()
    broken = []
    for name in reg.names():
        try:
            reg.get_module(name)
        except Exception as exc:  # noqa: BLE001 - report every import failure, never raise
            broken.append(f"{name} ({type(exc).__name__}: {str(exc)[:80]})")
    total = len(reg.names())
    if not total:
        return [Check("components", "fail", "no components found", "Reinstall G6: pip install --force-reinstall g6")]
    if broken:
        return [Check("components", "fail", f"{total - len(broken)}/{total} import; broken: " + "; ".join(broken),
                      "Install the extra each broken component needs (see 'g6 describe <name>').")]
    return [Check("components", "ok", f"all {total} components import")]


def _names() -> Check:
    from g6.core.registry import get_registry

    clashes = get_registry().collisions()
    if clashes:
        return Check("component names", "fail", "; ".join(f"{name}: {kept} is used, {left} is hidden"
                                                          for name, kept, left in clashes),
                     "Rename the hidden package, or uninstall the one you do not want.")
    return Check("component names", "ok", "no two components share a name")


def run_checks(env: dict[str, str] | None = None) -> list[Check]:
    """Every check; ``env`` replaces the environment for the settings (keys are read only from the key file)."""
    from g6.core import keys

    filed = keys.read()
    settings = {**filed, **(os.environ if env is None else env)}
    return [_python(), _package(), _home(), _sqlite(), *_extras(), _keys(), _model(settings, filed), _isolation(),
            *_components(), _names()]


def report(checks: list[Check]) -> dict[str, Any]:
    worst = "fail" if any(c.status == "fail" for c in checks) else (
        "warn" if any(c.status == "warn" for c in checks) else "ok")
    return {"status": worst, "checks": [asdict(c) for c in checks]}


def render(checks: list[Check]) -> str:
    mark = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}
    lines = [f"{mark[c.status]} {c.name}: {c.detail}" + (f"\n         fix: {c.fix}" if c.fix and c.status != "ok" else "")
             for c in checks]
    status = report(checks)["status"]
    lines.append({"ok": "G6 is ready.", "warn": "G6 works; see the warnings above for optional extras.",
                  "fail": "G6 needs attention: follow the 'fix' lines above."}[status])
    return "\n".join(lines)


# --- g6 doctor --report: one local error report from a saved traceback ------------------------------------------------

#: What a report keeps in place of caller text: a message, a name or a path that is not G6's own.
CALLER = "<caller>"
E_REPORT_DRAFT = "G6_E_DOCTOR_REPORT_DRAFT"
E_NO_TRACEBACK = "G6_E_DOCTOR_REPORT_NO_TRACEBACK"
E_DRY_RUN = "G6_E_DOCTOR_REPORT_DRY_RUN"
E_WRITTEN = "G6_E_DOCTOR_REPORT_WRITTEN"
E_WRITE_FAILED = "G6_E_DOCTOR_REPORT_WRITE_FAILED"
#: Only the end of a longer file is read: a traceback ends with its exception.
MAX_REPORT_CHARS = 1_000_000
MAX_FRAMES, MAX_MESSAGE = 200, 1000
_EXCEPTION = re.compile(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?::[ \t]?(.*))?")
_PYTHON_NAMES = frozenset({"<module>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"})


def _g6_path(path: str) -> str:
    """``g6/<file>`` when ``path`` is a file of this installation's g6 package, else ``""``. The part after the last
    ``/g6/`` must be a relative path of plain names: an absolute tail (``/g6//etc/hosts``, ``/g6/C:/...``) is not G6's."""
    root = Path(__file__).resolve().parents[1]
    text = path.replace("\\", "/")
    at = text.rfind("/g6/")
    inner = text[at + 4:] if at >= 0 else ""
    parts = inner.split("/")
    plain = bool(inner) and ":" not in inner and all(p and p not in (".", "..") for p in parts)
    return f"g6/{inner}" if plain and (root.joinpath(*parts)).is_file() else ""


@functools.lru_cache(maxsize=64)
def _defined_in(own: str) -> frozenset[str]:
    """Every function and class name the G6 file ``own`` (``g6/<file>``) defines, at any depth, read from its source
    and never imported; empty when it cannot be read."""
    path = Path(__file__).resolve().parents[1].joinpath(*own.split("/")[1:])
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return frozenset()
    return frozenset(node.name for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef))


def _g6_class(name: str) -> bool:
    """Whether ``name`` (``g6.<module>.<Class>``) is a class G6's own source defines at the top of that module, read
    from the source file and never imported."""
    parts = name.split(".")
    if len(parts) < 2 or parts[0] != "g6" or not all(p.isidentifier() for p in parts):
        return False
    root, module = Path(__file__).resolve().parents[1], parts[1:-1]
    for path in ((root.joinpath(*module[:-1], f"{module[-1]}.py") if module else None),
                 root.joinpath(*module, "__init__.py")):
        if path is not None and path.is_file():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                return False
            return any(isinstance(node, ast.ClassDef) and node.name == parts[-1] for node in tree.body)
    return False


def _exception(lines: list[str]) -> tuple[str, str]:
    """The last unindented ``Name: message`` line: the exception a traceback ends with."""
    for line in reversed(lines):
        found = _EXCEPTION.fullmatch(line.rstrip())
        if line[:1] not in " \t" and found:
            return found.group(1), found.group(2) or ""
    return "", ""


def _python_exception(name: str) -> bool:
    found = getattr(builtins, name, None) if "." not in name else None
    return (isinstance(found, type) and issubclass(found, BaseException)) or _g6_class(name)


def diagnostic_report(text: str, *, keep_caller_text: bool = False) -> dict[str, Any]:
    """One error report from a saved Python traceback ``text``, built here and sent nowhere.

    By default caller text is stripped: the message becomes ``<caller>``, and so does every frame's file that is not
    this installation's G6 code, every function name that file does not define, and an exception name that is neither
    Python's own nor a class G6's source defines; line numbers stay. With ``keep_caller_text=True`` they stay, masked as
    ``g6.context.redact.KEPT_TEXT_MASKS`` says. Masking is by pattern, so the report is ``qualified-draft``: read it
    before you share it. Text with no traceback frame gives a ``blocked-escalated`` answer and no report."""
    from g6.context.redact import KEPT_TEXT_MASKS, mask_emails, mask_hosts, mask_paths, sanitize_stack_trace, scrub

    text = text if isinstance(text, str) else ""
    truncated = len(text) > MAX_REPORT_CHARS
    text = text[-MAX_REPORT_CHARS:]
    ids = {"request_id": uuid.uuid4().hex, "run_id": uuid.uuid4().hex}
    frames = sanitize_stack_trace(text)
    if not frames:
        return {"kind": "g6_error_report", "completion_state": "blocked-escalated", **ids,
                "warning_card": {"code": E_NO_TRACEBACK, "severity": "blocked",
                                 "message": "The text holds no Python traceback frame, so no report was made.",
                                 "suggested_action": "Save the whole traceback, from 'Traceback (most recent call "
                                                     "last):' to the exception's line, and give that file."},
                "evidence": [{"kind": "error_report", "frames_found": 0, "input_chars": len(text)}]}

    def mask(value: str) -> str:
        return mask_hosts(mask_paths(mask_emails(scrub(value))))

    name, message = _exception(text.splitlines())
    name = name[:MAX_MESSAGE]
    kept_frames = []
    for frame in frames[-MAX_FRAMES:]:
        own = _g6_path(str(frame["file"]))
        function = str(frame["function"])
        if keep_caller_text:
            kept_frames.append({"file": own or mask(str(frame["file"])), "line": frame["line"],
                                "function": mask(function)})
        else:
            named = own and (function in _PYTHON_NAMES or (bool(function) and all(
                part in _defined_in(own) for part in function.split("."))))
            kept_frames.append({"file": own or CALLER, "line": frame["line"], "function": function if named else CALLER})
    body = {
        "kind": "g6_error_report",
        "exception": (mask(name) if keep_caller_text else name if _python_exception(name) else CALLER) or CALLER,
        "message": mask(message[:MAX_MESSAGE]) if keep_caller_text else CALLER,
        "frames": kept_frames,
        "caller_text": "kept" if keep_caller_text else "stripped",
        "g6_version": _g6_version(), "python_version": platform.python_version(), "system": platform.system(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"), "report_id": uuid.uuid4().hex[:16],
    }
    return {**body, "completion_state": "qualified-draft", **ids,
            "warning_card": {"code": E_REPORT_DRAFT, "severity": "amber",
                             "message": ("A local error report. Caller text was kept at your request and masked by "
                                         f"pattern: {KEPT_TEXT_MASKS}. A shape these patterns do not know stays."
                                         if keep_caller_text else "A local error report. Caller text was stripped: the "
                                         "message, and each path, function name and exception name that is not G6's "
                                         "own or Python's, became <caller>; line numbers and versions stay."),
                             "suggested_action": "Read the report before you share it."},
            "evidence": [{"kind": "error_report", "frames_found": len(frames), "frames_kept": len(kept_frames),
                          "g6_frames": sum(f["file"] != CALLER and f["file"].startswith("g6/") for f in kept_frames),
                          "caller_text": body["caller_text"], "input_chars": len(text), "truncated": truncated,
                          "source_lines": "never read", "sent": "nowhere"}]}


def _g6_version() -> str:
    try:
        return metadata.version("g6")
    except metadata.PackageNotFoundError:
        return "unknown"


def write_report(report: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Where ``report`` goes (``$G6_HOME/diagnostics/report-<id>.json``) and, with ``dry_run=False``, write it there,
    never over another file. A blocked answer is never written. A failed write is refused, never raised."""
    from g6.core.storage import state_path

    if report.get("completion_state") != "qualified-draft" or not re.fullmatch(r"[0-9a-f]{16}",
                                                                              str(report.get("report_id"))):
        return {"written": False, "dry_run": dry_run, "path": "", "code": E_NO_TRACEBACK}
    try:
        path = state_path("diagnostics", f"report-{report['report_id']}.json")
        if dry_run:
            return {"written": False, "dry_run": True, "path": str(path), "code": E_DRY_RUN}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    except (OSError, ValueError) as exc:
        return {"written": False, "dry_run": dry_run, "path": "", "code": E_WRITE_FAILED,
                "reason": f"{type(exc).__name__}: {exc}"[:300]}
    return {"written": True, "dry_run": False, "path": str(path), "code": E_WRITTEN}
