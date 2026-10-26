# SPDX-License-Identifier: Apache-2.0
"""TLA+ through TLC and SANY, out of process (owner, 2026-10-20). The user supplies ``tla2tools.jar``, pinned by its
SHA-256 (``G6_TLA2TOOLS_JAR``, ``G6_TLA2TOOLS_SHA256``); G6 never downloads it. Java comes from ``G6_JAVA`` or PATH.

The refusals run anywhere: no jar, no digest, a digest that does not match (a temporary file stands in for the jar), no
Java, and the limits. The runs need Java and the jar, and are skipped with the reason when either is missing."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from g6.invariants import check_envelope
from g6.model_checking import sany_parse, tlc_check
from g6.model_checking.envelope import (
    DIGEST_MISMATCH,
    DIGEST_MISSING,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    JAR_MISSING,
    JAVA_MISSING,
    MODULE_INVALID,
    OUTPUT_TOO_LARGE,
    SANY_ERRORS,
    SANY_NO_ERROR,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    TLC_DEADLOCK,
    TLC_INVARIANT_VIOLATED,
    TLC_NO_ERROR,
    TLC_PROPERTY_VIOLATED,
)
from g6.model_checking.tlc import DIGEST_ENV, JAR_ENV, JAVA_ENV, jar_release, tool_args

COUNTER = """---- MODULE Counter ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""
PASSES = """---- MODULE Passes ----
EXTENDS Naturals
VARIABLES x, y
Init == x = 0 /\\ y = "a"
Next == \\/ x < 2 /\\ x' = x + 1 /\\ y' = y
        \\/ x = 2 /\\ x' = 0 /\\ y' = "b"
Spec == Init /\\ [][Next]_<<x, y>>
Inv == x <= 2
====
"""
STOPS = """---- MODULE Stops ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x < 2 /\\ x' = x + 1
Spec == Init /\\ [][Next]_x
====
"""
CYCLE = """---- MODULE Cycle ----
EXTENDS Naturals
CONSTANT N
VARIABLE x
Init == x = 0
Next == x' = (x + 1) % N
Spec == Init /\\ [][Next]_x
Reach == <>(x = N + 5)
====
"""
FOREVER = """---- MODULE Forever ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = x + 1
Spec == Init /\\ [][Next]_x
====
"""
MALFORMED = """---- MODULE Malformed ----
EXTENDS Naturals
VARIABLE x
Init == x =
Next == x' = x + 1
====
"""

HAVE_TLC = bool(os.environ.get(JAR_ENV) and os.environ.get(DIGEST_ENV)
                and (os.environ.get(JAVA_ENV) or shutil.which("java")))
needs_tlc = pytest.mark.skipif(not HAVE_TLC, reason=f"TLC needs Java (PATH or {JAVA_ENV}) and tla2tools.jar given by "
                                                    f"{JAR_ENV} with its SHA-256 in {DIGEST_ENV}")


@pytest.fixture
def no_jar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(JAR_ENV, raising=False)
    monkeypatch.delenv(DIGEST_ENV, raising=False)


@pytest.fixture
def fake_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary file standing in for the jar, with its own digest pinned: it passes the digest check and nothing
    more."""
    jar = tmp_path / "jar" / "tla2tools.jar"
    jar.parent.mkdir()
    jar.write_bytes(b"not a jar at all")
    monkeypatch.setenv(JAR_ENV, str(jar))
    monkeypatch.setenv(DIGEST_ENV, hashlib.sha256(jar.read_bytes()).hexdigest())
    return jar


def _blocked(result, code: str) -> None:
    assert result.code == code and result.completion_state == "blocked-escalated", (result.code, result.reason)
    assert check_envelope(result.model_dump(mode="json")) == [] and result.warning_card["code"] == code


# --- refusals: no Java and no jar needed -------------------------------------------------------------------------------

@pytest.mark.parametrize("run", [lambda: tlc_check(COUNTER), lambda: sany_parse(COUNTER)])
def test_no_jar_is_refused_and_says_how_to_supply_one(run, no_jar) -> None:
    result = run()
    _blocked(result, JAR_MISSING)
    action = result.warning_card["suggested_action"]
    assert JAR_ENV in action and DIGEST_ENV in action and "never downloads" in action
    assert result.engine == "" and result.evidence[0]["external_program_ran"] is False


def test_a_jar_path_that_does_not_exist_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JAR_ENV, str(tmp_path / "missing.jar"))
    monkeypatch.setenv(DIGEST_ENV, "0" * 64)
    result = tlc_check(COUNTER)
    _blocked(result, JAR_MISSING)
    assert "missing.jar" in result.reason and list(tmp_path.iterdir()) == []   # nothing fetched or written


@pytest.mark.parametrize("digest", [None, "", "abc", "g" * 64, "0" * 63])
def test_a_missing_or_malformed_digest_is_refused(fake_jar: Path, monkeypatch: pytest.MonkeyPatch, digest) -> None:
    if digest is None:
        monkeypatch.delenv(DIGEST_ENV)
    else:
        monkeypatch.setenv(DIGEST_ENV, digest)
    result = tlc_check(COUNTER)
    _blocked(result, DIGEST_MISSING)
    assert DIGEST_ENV in result.warning_card["suggested_action"]


def test_a_jar_whose_digest_does_not_match_is_refused(fake_jar: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIGEST_ENV, "ab" * 32)
    monkeypatch.setenv(JAVA_ENV, sys.executable)   # any program stands for Java: the jar is refused before anything runs
    before = sorted(p.name for p in fake_jar.parent.iterdir())
    result = sany_parse(COUNTER)
    _blocked(result, DIGEST_MISMATCH)
    actual = hashlib.sha256(fake_jar.read_bytes()).hexdigest()
    assert actual in result.reason and "ab" * 32 in result.reason
    assert sorted(p.name for p in fake_jar.parent.iterdir()) == before and fake_jar.read_bytes() == b"not a jar at all"


def test_a_digest_is_read_in_any_case_and_with_spaces(fake_jar: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The digest check passes (so the refusal is the next one, for Java), whatever the case of the hex digits."""
    monkeypatch.setenv(DIGEST_ENV, "  " + hashlib.sha256(fake_jar.read_bytes()).hexdigest().upper() + "\n")
    monkeypatch.setenv(JAVA_ENV, str(fake_jar.parent / "no-java-here"))
    _blocked(tlc_check(COUNTER), JAVA_MISSING)


def test_a_java_named_by_g6_java_that_does_not_exist_is_refused(fake_jar: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JAVA_ENV, str(fake_jar.parent / "no-java-here"))
    result = tlc_check(COUNTER)
    _blocked(result, JAVA_MISSING)
    assert JAVA_ENV in result.warning_card["suggested_action"]


def test_no_java_on_path_is_refused(fake_jar: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(JAVA_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    _blocked(sany_parse(COUNTER), JAVA_MISSING)


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"time_limit": 601}, TIME_LIMIT_TOO_LONG), ({"time_limit": 0}, INPUT_INVALID),
    ({"time_limit": float("inf")}, INPUT_INVALID),
    ({"workers": 0}, INPUT_INVALID), ({"workers": 17}, INPUT_TOO_LARGE),
    ({"heap_mb": 16}, INPUT_INVALID), ({"heap_mb": 1 << 20}, INPUT_TOO_LARGE),
    ({"module": "../Counter"}, MODULE_INVALID), ({"module": "Other"}, MODULE_INVALID),
    ({"invariants": ["Inv\nPROPERTY P"]}, INPUT_INVALID), ({"properties": ["Live Ness"]}, INPUT_INVALID),
    ({"constants": {"N": "3\nINVARIANT Inv"}}, INPUT_INVALID), ({"constants": {"1N": "3"}}, INPUT_INVALID),
    ({"specification": "Spec /\\ TRUE"}, INPUT_INVALID),
    ({"config": "SPECIFICATION Spec", "invariants": ["Inv"]}, INPUT_INVALID),
])
def test_the_limits_and_names_are_refused_before_anything_runs(kwargs: dict, code: str, no_jar) -> None:
    _blocked(tlc_check(COUNTER, **kwargs), code)


@pytest.mark.parametrize("spec", ["", "VARIABLE x\nInit == x = 0\n", COUNTER.replace("Counter", "Naturals"),
                                  COUNTER.replace("MODULE Counter", "MODULE 9lives")])
def test_a_spec_without_a_usable_module_header_is_refused(spec: str, no_jar) -> None:
    _blocked(tlc_check(spec), MODULE_INVALID)
    _blocked(sany_parse(spec), MODULE_INVALID)


def test_a_spec_over_the_size_limit_is_refused(no_jar) -> None:
    _blocked(tlc_check(COUNTER + "\\* " + "x" * 300_000 + "\n"), INPUT_TOO_LARGE)


# --- runs: Java and the jar needed ------------------------------------------------------------------------------------

@needs_tlc
def test_tlc_finds_the_counter_breaking_its_invariant() -> None:
    result = tlc_check(COUNTER, invariants=["Inv"], request_id="q", run_id="r")
    _blocked(result, TLC_INVARIANT_VIOLATED)
    assert result.verdict == "invariant_violated" and result.tla["violated"] == "Inv"
    assert [s["variables"]["x"] for s in result.trace] == [0, 1, 2, 3] and result.trace_kind == "counterexample"
    assert result.engine == "tlc" and result.engine_version.startswith("2") and (result.request_id, result.run_id) == (
        "q", "r")
    evidence = result.evidence[0]
    assert evidence["jar_sha256"] == os.environ[DIGEST_ENV].strip().lower() and evidence["external_program_ran"] is True
    assert evidence["execution"]["cleanup_confirmed"] is True and not Path(evidence["run_dir"]).exists()


@needs_tlc
def test_tlc_reports_its_own_no_error_verdict_as_a_qualified_draft() -> None:
    result = tlc_check(PASSES, invariants=["Inv"], workers=2)
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == TLC_NO_ERROR
    assert result.verdict == "no_error" and result.tla["distinct_states"] == 6 and result.tla["depth"] == 6
    message = result.warning_card["message"]
    assert "TLC" in message and "not verified" in message and "6 distinct states" in message
    assert check_envelope(result.model_dump(mode="json")) == []


@needs_tlc
def test_tlc_finds_a_deadlock_unless_told_not_to_look() -> None:
    found = tlc_check(STOPS)
    _blocked(found, TLC_DEADLOCK)
    assert [s["variables"]["x"] for s in found.trace] == [0, 1, 2]
    assert tlc_check(STOPS, check_deadlock=False).verdict == "no_error"


@needs_tlc
def test_constants_and_properties_from_fields_or_a_config_text() -> None:
    fields = tlc_check(CYCLE, constants={"N": "3"}, properties=["Reach"])
    _blocked(fields, TLC_PROPERTY_VIOLATED)
    text = tlc_check(CYCLE, config="SPECIFICATION Spec\nCONSTANT N = 3\nPROPERTY Reach\n")
    assert text.verdict == "property_violated" and text.tla["trace_end"]["kind"] in ("stuttering", "back_to_state")


@needs_tlc
def test_a_run_past_its_time_limit_is_killed_with_its_process_tree() -> None:
    result = tlc_check(FOREVER, time_limit=3)
    _blocked(result, TIME_LIMIT)
    assert result.elapsed_s < 20 and result.evidence[0]["execution"]["cleanup_confirmed"] is True
    assert not Path(result.evidence[0]["run_dir"]).exists()


@needs_tlc
def test_output_past_its_cap_stops_the_run() -> None:
    _blocked(tlc_check(COUNTER, invariants=["Inv"], max_output_bytes=200), OUTPUT_TOO_LARGE)


@needs_tlc
def test_the_spec_reaches_tlc_as_a_file_never_on_the_command_line() -> None:
    sneaky = COUNTER.replace("Inv == x < 3", "Inv == x < 3\n\\* \" & echo pwned & -config evil.cfg")
    result = tlc_check(sneaky, invariants=["Inv"])
    assert result.verdict == "invariant_violated"
    argv = result.evidence[0]["argv"]
    assert "Counter" in argv and not any("pwned" in a or "evil" in a for a in argv)


@needs_tlc
def test_sany_accepts_a_good_module_and_places_the_error_in_a_malformed_one() -> None:
    good = sany_parse(COUNTER)
    assert good.completion_state == "qualified-draft" and good.warning_card["code"] == SANY_NO_ERROR
    assert "Nothing was model-checked" in good.warning_card["message"] and good.engine == "sany"
    bad = sany_parse(MALFORMED)
    _blocked(bad, SANY_ERRORS)
    assert bad.tla["errors"][0]["line"] == 4 and bad.tla["errors"][0]["column"] == 9


@needs_tlc
def test_sany_catches_a_semantic_error_that_parses() -> None:
    result = sany_parse(COUNTER.replace("x' = x + 1", "x' = y + 1"))
    _blocked(result, SANY_ERRORS)
    message = result.tla["errors"][0]["message"]  # the release line's wording, then the main line's
    assert "Could not find declaration or definition of symbol 'y'" in message or "Unknown operator" in message


@needs_tlc
def test_each_run_records_the_tools_release_it_ran() -> None:
    result = tlc_check(COUNTER, invariants=["Inv"])
    assert result.evidence[0]["tools_release"]  # v1.7.4, or "main line" for a build from master


# --- the release line: tla2tools 1.7.x knows fewer flags than builds from the main line -------------------------------

def _jar_with(tmp_path: Path, manifest: str) -> Path:
    jar = tmp_path / "tla2tools.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", manifest)
    return jar


def test_the_release_is_read_from_the_jars_manifest(tmp_path: Path) -> None:
    assert jar_release(_jar_with(tmp_path, "Manifest-Version: 1.0\nX-Git-Tag: v1.7.4\nX-Git-Branch: v1.7.4\n")) == "v1.7.4"
    assert jar_release(_jar_with(tmp_path, "Manifest-Version: 1.0\nX-Git-Tag: \nX-Git-Branch: master\n")) == ""
    (tmp_path / "plain.jar").write_bytes(b"not a jar at all")
    assert jar_release(tmp_path / "plain.jar") == ""


def test_a_1_7_release_runs_without_the_flags_it_does_not_know() -> None:
    tlc = ["tlc2.TLC", "-tool", "-noGenerateSpecTE", "-config", "M.cfg", "M"]
    sany = ["tla2sany.SANY", "-error-codes", "M.tla"]
    assert tool_args(tlc, "v1.7.4") == ["tlc2.TLC", "-tool", "-config", "M.cfg", "M"]
    assert tool_args(sany, "v1.7.0") == ["tla2sany.SANY", "M.tla"]
    assert tool_args(tlc, "") == tlc and tool_args(sany, "v1.8.0") == sany
