# SPDX-License-Identifier: Apache-2.0
"""TLA+ through TLC and SANY, out of process: the only module of g6.model_checking that starts a program.

What it needs, none of it installed by G6:

* ``tla2tools.jar``, which you download yourself: ``G6_TLA2TOOLS_JAR`` is its path and ``G6_TLA2TOOLS_SHA256`` its
  SHA-256 (64 hex digits). G6 never downloads it (the earlier version fetched it unpinned on first use). No digest, a
  digest that does not match and no jar are each refused with their own code. The release G6 is tested on is v1.7.4,
  the latest stable one (``tla2tools.jar`` from github.com/tlaplus/tlaplus/releases/tag/v1.7.4, SHA-256
  ``936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88``); builds from the main line work too. The jar's
  manifest says which it is: a 1.7 release runs without the two flags it does not know (TLC's ``-noGenerateSpecTE``,
  as it writes no trace spec unless asked, and SANY's ``-error-codes``, so SANY's verdict there rests on its output
  alone, which the parser already requires);
* Java 11 or later: ``G6_JAVA``, or ``java`` on PATH.

Each run gets a fresh temporary directory. The jar is copied into it and hashed as it is copied, so the bytes that run
are the bytes that were checked, and a ``CommunityModules.jar`` lying beside your jar is not on the class path. The spec
is written there as ``<Module>.tla`` (the module name is checked: a letter, then letters, digits and ``_``, matching
the spec's own header and no standard module's name) and the config as ``<Module>.cfg``; neither text is ever put on a
command line. Java gets a minimal environment (no ``JAVA_TOOL_OPTIONS`` or other options from yours), its own
temporary directory and home inside the run directory, and a heap limit. It runs under
:mod:`g6.core.component_process`: a time limit (default 60 seconds, at most 600), an output cap (4 MiB by default),
and the whole process tree killed when either is passed; the directory is removed afterwards.

This is a resource bound, not a sandbox: TLC runs as you and a spec can use TLC's own modules, which can write files
(``Json``'s serialisers, for one). That is why :func:`tlc_check` and :func:`sany_parse` are sensitive: callable from
Python, and over MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE`` (owner, 2026-10-20).

TLC runs in ``-tool`` mode and its output is read by :mod:`g6.model_checking.tlc_output`. Its "no error" is reported
as TLC's own verdict, ``qualified-draft`` at best; a violation is ``blocked-escalated`` with TLC's trace.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from g6.core import component_process
from g6.core.outside import find_program
from g6.core.process_tree import ProcessTreeError
from g6.model_checking.envelope import (
    DIGEST_ENV,
    DIGEST_MISMATCH,
    DIGEST_MISSING,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    JAR_ENV,
    JAR_MISSING,
    JAVA_ENV,
    JAVA_MISSING,
    MODULE_INVALID,
    OUTPUT_TOO_LARGE,
    RUN_FAILED,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    Refusal,
    labelled,
    steps,
)
from g6.model_checking.schema import ModelCheckResult
from g6.model_checking.tlc_output import parse_sany_output, parse_tlc_output

DEFAULT_TIME_LIMIT_S = 60.0
MAX_TIME_LIMIT_S = 600.0
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
OUTPUT_CAP_LIMIT = component_process.MAX_MESSAGE_BYTES
DEFAULT_HEAP_MB, MIN_HEAP_MB, MAX_HEAP_MB = 1024, 64, 16384
MAX_WORKERS = 16
MAX_SPEC_CHARS = 200_000
MAX_CONFIG_CHARS = 20_000
MAX_NAMES = 100
MAX_JAR_BYTES = 256 * 1024 * 1024
STANDARD_MODULES = frozenset({"Bags", "FiniteSets", "Integers", "Json", "Naturals", "Randomization", "RealTime",
                              "Reals", "Sequences", "TLC", "TLCExt", "Toolbox"})
_MODULE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}\Z")
_IDENT = re.compile(r"(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]{1,100}\Z")
_HEADER = re.compile(r"^\s*-{4,}\s*MODULE\s+(\S+)\s*-{4,}", re.MULTILINE)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
_TAIL = 8000
_RELEASE_LINE = re.compile(r"v1\.7\.\d+\Z")  # the stable releases, which predate the flags below
_MAIN_LINE_ONLY = frozenset({"-noGenerateSpecTE", "-error-codes"})


def jar_release(jar: Path) -> str:
    """The release tag in the jar's manifest (``v1.7.4``), or "" for a build from the main line or no manifest."""
    try:
        with zipfile.ZipFile(jar) as z:
            manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    found = re.search(r"^X-Git-Tag:[ \t]*(\S*)", manifest, re.MULTILINE)
    return found.group(1) if found else ""


def tool_args(main: list[str], release: str) -> list[str]:
    """``main`` for the jar's release: a 1.7 release refuses the flags only the main line knows, so they are left out."""
    return [a for a in main if a not in _MAIN_LINE_ONLY] if _RELEASE_LINE.match(release) else list(main)


def _seconds(time_limit: Any) -> float:
    if isinstance(time_limit, bool) or not isinstance(time_limit, int | float) or not 0 < time_limit < float("inf"):
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {time_limit!r}")
    if time_limit > MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {time_limit:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
    return float(time_limit)


def _whole(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number of at least {low}, not {value!r}")
    if value > high:
        raise Refusal(INPUT_TOO_LARGE, f"{what} {value} is above the limit of {high}")
    return value


def _module(spec: Any, module: str) -> str:
    if not isinstance(spec, str):
        raise Refusal(INPUT_INVALID, "spec is the text of one TLA+ module")
    if len(spec) > MAX_SPEC_CHARS:
        raise Refusal(INPUT_TOO_LARGE, f"the spec is {len(spec)} characters; the limit is {MAX_SPEC_CHARS}")
    header = _HEADER.search(spec)
    if not header:
        raise Refusal(MODULE_INVALID, "the spec has no ---- MODULE Name ---- header")
    name = header.group(1)
    if module and module != name:
        raise Refusal(MODULE_INVALID, f"module is {module!r} but the spec's header names {name!r}")
    if not _MODULE.match(name):
        raise Refusal(MODULE_INVALID, f"{name[:100]!r} is not a module name G6 runs: a letter, then letters, digits "
                                      "and _, at most 100 characters")
    if name in STANDARD_MODULES:
        raise Refusal(MODULE_INVALID, f"{name} is the name of a standard module; give the spec another name")
    return name


def _names(values: Any, what: str) -> list[str]:
    if isinstance(values, str) or not isinstance(values, Sequence) or len(values) > MAX_NAMES:
        raise Refusal(INPUT_INVALID, f"{what} is a list of at most {MAX_NAMES} operator names")
    for value in values:
        if not isinstance(value, str) or not _IDENT.match(value):
            raise Refusal(INPUT_INVALID, f"{what}: {str(value)[:80]!r} is not an operator name (letters, digits and _, "
                                         "with at least one letter)")
    return list(values)


def _config(config: str, specification: str, invariants: Any, properties: Any, constants: Any) -> str:
    """The config TLC reads: ``config`` as given, or one built from the fields, every name and value checked."""
    if not isinstance(config, str) or len(config) > MAX_CONFIG_CHARS:
        raise Refusal(INPUT_INVALID, f"config is the text of a TLC config file, at most {MAX_CONFIG_CHARS} characters")
    named, live = _names(invariants, "invariants"), _names(properties, "properties")
    if constants is not None and not isinstance(constants, Mapping):
        raise Refusal(INPUT_INVALID, "constants maps each constant's name to its value, such as {\"N\": \"3\"}")
    values = dict(constants or {})
    for name, value in values.items():
        if not isinstance(name, str) or not _MODULE.match(name):
            raise Refusal(INPUT_INVALID, f"constant {str(name)[:80]!r} is not a constant name")
        if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value or len(value) > 1000:
            raise Refusal(INPUT_INVALID, f"constant {name}'s value is one line of TLA+ of at most 1000 characters")
    if config.strip():
        if named or live or values or specification != "Spec":
            raise Refusal(INPUT_INVALID, "give either config text or the fields specification, invariants, "
                                         "properties and constants, not both")
        return config
    if not isinstance(specification, str) or not _IDENT.match(specification):
        raise Refusal(INPUT_INVALID, f"specification {str(specification)[:80]!r} is not an operator name")
    lines = [f"SPECIFICATION {specification}"]
    if values:
        lines += ["CONSTANTS", *(f"  {name} = {value}" for name, value in values.items())]
    lines += [f"INVARIANT {name}" for name in named] + [f"PROPERTY {name}" for name in live]
    return "\n".join(lines) + "\n"


def _jar() -> tuple[Path, str]:
    """The jar named by ``G6_TLA2TOOLS_JAR`` and the digest pinned by ``G6_TLA2TOOLS_SHA256``."""
    named = os.environ.get(JAR_ENV, "").strip()
    if not named:
        raise Refusal(JAR_MISSING, f"{JAR_ENV} is not set, so there is no tla2tools.jar to run; G6 never downloads it")
    jar = Path(named)
    if not jar.is_file():
        raise Refusal(JAR_MISSING, f"{JAR_ENV} names {named}, which is not a file; G6 never downloads it")
    if jar.stat().st_size > MAX_JAR_BYTES:
        raise Refusal(JAR_MISSING, f"{named} is larger than {MAX_JAR_BYTES // (1024 * 1024)} MiB, so it is not a "
                                   "tla2tools.jar")
    digest = os.environ.get(DIGEST_ENV, "").strip().lower()
    if not _DIGEST.match(digest):
        raise Refusal(DIGEST_MISSING, f"{DIGEST_ENV} must hold the jar's SHA-256 as 64 hex digits; it is "
                                      f"{'not set' if not digest else 'not that'}, so the jar is not run")
    return jar, digest


def _java() -> str:
    named = os.environ.get(JAVA_ENV, "").strip()
    if named:
        found = find_program(named) or (named if Path(named).is_file() else None)
        if not found:
            raise Refusal(JAVA_MISSING, f"{JAVA_ENV} names {named}, which is not a program here")
        return found
    found = find_program("java")
    if not found:
        raise Refusal(JAVA_MISSING, f"no java on PATH and {JAVA_ENV} is not set")
    return found


def _stage(jar: Path, digest: str, into: Path) -> None:
    """Copy the jar into the run directory, hashing what is copied; refuse it when the hash is not the pinned one."""
    sha = hashlib.sha256()
    with jar.open("rb") as source, into.open("wb") as target:
        while chunk := source.read(1 << 20):
            sha.update(chunk)
            target.write(chunk)
    actual = sha.hexdigest()
    if actual != digest:
        raise Refusal(DIGEST_MISMATCH, f"{jar} has SHA-256 {actual}, not the {digest} pinned in {DIGEST_ENV}, so it "
                                       "was not run")


def _execute(argv: list[str], cwd: Path, env: dict[str, str], seconds: float, cap: int, heap_mb: int,
             record: dict[str, Any]) -> tuple[int, str]:
    """Run ``argv`` under a time limit and an output cap, the process tree killed when either is passed."""
    with tempfile.TemporaryFile() as out:
        def too_much() -> bool:
            return os.fstat(out.fileno()).st_size > cap

        window = None
        try:
            with component_process.window(seconds, too_much) as window:
                code = component_process.run_process(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                                     stderr=subprocess.STDOUT,
                                                     memory_bytes=(heap_mb + 1024) * 1024 * 1024)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped and too_much():
                raise Refusal(OUTPUT_TOO_LARGE, f"the output passed its cap of {cap} bytes, so the run was stopped and "
                                                "its process tree killed") from None
            raise Refusal(TIME_LIMIT, f"the run did not finish within {seconds:g} s, so it was stopped and its process "
                                      "tree killed") from None
        except (OSError, ProcessTreeError) as exc:
            raise Refusal(RUN_FAILED, f"the program could not be run to the end: {exc}"[:500]) from None
        finally:
            if window is not None:
                record["execution"] = {k: window.execution.get(k) for k in ("cleanup_confirmed", "child_exited",
                                                                             "elapsed_seconds")}
        out.seek(0)
        raw = out.read(cap + 1)
    text = raw[:cap].decode("utf-8", "replace")
    record |= {"output_bytes": len(raw), "output_tail": text[-_TAIL:]}
    return code, text


def _run_tool(state: dict[str, Any], *, engine: str, spec: str, module: str, config: str | None, main: list[str],
              seconds: float, heap_mb: int, cap: int) -> tuple[int, str]:
    """Stage the jar, the spec and the config in a fresh directory, run ``main`` there, and remove the directory."""
    jar, digest = _jar()
    java = _java()
    record = state.setdefault("extra", {})
    record |= {"jar": str(jar), "jar_sha256": digest, "java": java, "module": module}
    with tempfile.TemporaryDirectory(prefix="g6_tlc_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for sub in ("spec", "tools", "java_tmp", "home"):
            (root / sub).mkdir()
        record["run_dir"] = str(root)
        _stage(jar, digest, root / "tools" / "tla2tools.jar")
        release = jar_release(root / "tools" / "tla2tools.jar")  # read from the bytes that were checked
        record["tools_release"] = release or "main line"
        main = tool_args(main, release)
        (root / "spec" / f"{module}.tla").write_bytes(spec.encode("utf-8"))
        if config is not None:
            (root / "spec" / f"{module}.cfg").write_bytes(config.encode("utf-8"))
            record["config"] = config
        argv = [java, "-XX:+UseParallelGC", f"-Xmx{heap_mb}m", "-XX:-UsePerfData",
                f"-Djava.io.tmpdir={root / 'java_tmp'}", f"-Duser.home={root / 'home'}", "-Dfile.encoding=UTF-8",
                "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8", "-cp", str(root / "tools" / "tla2tools.jar"),
                *[a.replace("{states}", str(root / "states")) for a in main]]
        record["argv"] = [a.replace(str(root), "<run dir>") for a in argv]
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env |= {"TEMP": str(root / "java_tmp"), "TMP": str(root / "java_tmp"), "TMPDIR": str(root / "java_tmp"),
                "HOME": str(root / "home")}
        state["engine"] = engine
        return _execute(argv, root / "spec", env, seconds, cap, heap_mb, record)


def _first_errors(run: dict[str, Any]) -> str:
    said = [" ".join(e["text"].split())[:300] for e in run.get("errors", [])[:2]]
    said += [f"line {e['line']}, column {e['column']}: {e['message'][:200]}" for e in run.get("sany_errors", [])[:2]]
    return "; ".join(said) or "no message"


def _tlc_reason(run: dict[str, Any]) -> str:
    length, verdict = steps(max(len(run["trace"]) - 1, 0)), run["verdict"]
    if verdict == "invariant_violated":
        return f"TLC found invariant {run['violated']} violated after {length}; the trace is TLC's counterexample"
    if verdict == "property_violated":
        end = run["trace_end"].get("kind", "")
        how = {"stuttering": ", ending in stuttering forever", "back_to_state": ", ending in a loop"}.get(end, "")
        return f"TLC found {run['violated']} violated; the trace is TLC's counterexample{how}"
    if verdict == "deadlock":
        return f"TLC reached a deadlock after {length}: the trace ends in a state with no successor"
    if verdict in ("assumption_violated", "assertion_failed"):
        return f"a condition the spec states came out false: {_first_errors(run)}"
    if verdict in ("evaluation_error", "spec_error", "failed"):
        return f"TLC stopped with exit code {run['exit_code']}: {_first_errors(run)}"
    if verdict == "unrecognised":
        return (f"TLC's output and exit code {run['exit_code']} do not fit a verdict G6 reads, so the run is not "
                "reported as one")
    return ""


def tlc_check(spec: str, *, module: str = "", config: str = "", specification: str = "Spec",
              invariants: Sequence[str] = (), properties: Sequence[str] = (), constants: Mapping[str, str] | None = None,
              check_deadlock: bool = True, workers: int = 1, time_limit: float = DEFAULT_TIME_LIMIT_S,
              heap_mb: int = DEFAULT_HEAP_MB, max_output_bytes: int = MAX_OUTPUT_BYTES, request_id: str = "",
              run_id: str = "") -> ModelCheckResult:
    """Model-check one TLA+ module with TLC. ``config`` is a TLC config file's text; without it, one is built from
    ``specification``, ``constants`` (name to TLA+ value), ``invariants`` and ``properties``. ``check_deadlock=False``
    tells TLC not to look for deadlocks."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        seconds = _seconds(time_limit)
        threads = _whole(workers, "workers", 1, MAX_WORKERS)
        heap = _whole(heap_mb, "heap_mb", MIN_HEAP_MB, MAX_HEAP_MB)
        cap = _whole(max_output_bytes, "max_output_bytes", 1, OUTPUT_CAP_LIMIT)
        name = _module(spec, module)
        text = _config(config, specification, invariants, properties, constants)
        main = ["tlc2.TLC", "-tool", "-workers", str(threads), "-noGenerateSpecTE", "-metadir", "{states}",
                "-config", f"{name}.cfg", *([] if check_deadlock else ["-deadlock"]), name]
        code, output = _run_tool(state, engine="tlc", spec=spec, module=name, config=text, main=main, seconds=seconds,
                                 heap_mb=heap, cap=cap)
        run = parse_tlc_output(output, code)
        trace = run.pop("trace")
        state |= {"engine_version": run["version"], "verdict": run["verdict"], "tla": run, "trace": trace,
                  "trace_kind": "counterexample" if trace else "", "violated": [run["violated"]] if run["violated"]
                  else [], "reason": _tlc_reason({**run, "trace": trace})}
        return state

    return labelled("tlc_check", work, request_id, run_id)


def sany_parse(spec: str, *, module: str = "", time_limit: float = DEFAULT_TIME_LIMIT_S,
               heap_mb: int = DEFAULT_HEAP_MB, request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """Parse one TLA+ module with SANY and check its semantics and levels; nothing is model-checked."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        seconds = _seconds(time_limit)
        heap = _whole(heap_mb, "heap_mb", MIN_HEAP_MB, MAX_HEAP_MB)
        name = _module(spec, module)
        code, output = _run_tool(state, engine="sany", spec=spec, module=name, config=None,
                                 main=["tla2sany.SANY", "-error-codes", f"{name}.tla"], seconds=seconds, heap_mb=heap,
                                 cap=MAX_OUTPUT_BYTES)
        run = parse_sany_output(output, code, name)
        state |= {"engine_version": run["version"], "verdict": run["verdict"], "tla": run}
        if run["verdict"] != "no_error":
            first = "; ".join(f"line {e['line']}, column {e['column']}: {e['message'][:200]}" for e in run["errors"][:3])
            state["reason"] = f"SANY found {len(run['errors'])} error(s) in module {name}: {first}"
        return state

    return labelled("sany_parse", work, request_id, run_id)


__all__ = ["DEFAULT_TIME_LIMIT_S", "DIGEST_ENV", "JAR_ENV", "JAVA_ENV", "MAX_OUTPUT_BYTES", "MAX_TIME_LIMIT_S",
           "MAX_WORKERS", "sany_parse", "tlc_check"]
