# SPDX-License-Identifier: Apache-2.0
"""SageMath as an optional external program (owner, 2026-10-20: SageMath is GPL, so G6 never imports, bundles or
installs it). Found through G6_SAGE or PATH, never Docker; the expression is screened by G6's own parser with a fixed
table of SageMath functions, and SageMath reads only the text G6 renders back from its parse tree, in a file G6
writes. SageMath is not installed on the machine these tests were written on, so the tests that run it skip with the
reason; everything else runs without it."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from g6.algebra import language as L
from g6.algebra import sage
from g6.algebra.envelope import (
    CONSTRUCT_REFUSED,
    EXPONENT_TOO_LARGE,
    INPUT_INVALID,
    SAGE_ANSWER,
    SAGE_ENV,
    SAGE_MISSING,
    SAGE_VERSION_READ,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_FUNCTION,
)
from g6.invariants import check_envelope


def _sage_here() -> bool:
    try:
        sage.find_sage()
    except Exception:  # noqa: BLE001 -- any refusal means there is no SageMath to run
        return False
    return True


needs_sage = pytest.mark.skipif(not _sage_here(), reason=f"SageMath is not installed: it is GPL, an external program "
                                                         f"G6 never installs; put sage on PATH or set {SAGE_ENV}")


@pytest.fixture
def no_sage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(SAGE_ENV, str(tmp_path / "no-sage-here"))
    return tmp_path


def _blocked(result, code: str):
    assert check_envelope(result.model_dump()) == [] and result.completion_state == "blocked-escalated"
    assert result.code == code, (result.code, result.reason)
    return result


# --- without SageMath -----------------------------------------------------------------------------------------------

def test_a_missing_sage_named_by_g6_sage_is_refused_with_how_to_install_it(no_sage) -> None:
    for result in (sage.sage_version(), sage.sage_evaluate("factor(x^2 - 1)", symbols=["x"])):
        found = _blocked(result, SAGE_MISSING)
        assert "never installs" in found.reason
        action = found.warning_card["suggested_action"]
        assert "GPL" in action and "sagemath.org" in action and SAGE_ENV in action and "never installs" in action
        assert found.evidence[0]["external_program_ran"] is False


def test_no_sage_on_path_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SAGE_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert shutil.which("sage") is None
    found = _blocked(sage.sage_version(), SAGE_MISSING)
    assert "PATH" in found.reason


def test_docker_is_never_a_way_to_sage() -> None:
    source = (Path(sage.__file__).read_text(encoding="utf-8") + Path(sage.runner.__file__).read_text(encoding="utf-8"))
    assert "docker" not in source.lower().replace("never docker", "")


@pytest.mark.parametrize(("text", "code"), [
    ("__import__('os')", CONSTRUCT_REFUSED),
    ("os.system('ls')", CONSTRUCT_REFUSED),
    ("x.parent()", CONSTRUCT_REFUSED),
    ("load('f.sage')", CONSTRUCT_REFUSED),
    ("integrate(x, x, algorithm=giac)", CONSTRUCT_REFUSED),
    ("sage_eval(x)", UNKNOWN_FUNCTION),
    ("attach(x)", UNKNOWN_FUNCTION),
    ("preparse(x)", UNKNOWN_FUNCTION),
    ("lambda: 0", CONSTRUCT_REFUSED),
    ("2^(10^10)", EXPONENT_TOO_LARGE),
    ("factor(x); x", CONSTRUCT_REFUSED),
])
def test_the_expression_is_screened_before_sage_is_even_looked_for(no_sage, text: str, code: str) -> None:
    found = _blocked(sage.sage_evaluate(text, symbols=["x"]), code)
    assert found.refusals and found.refusals[0]["field"] == "expression"
    assert found.evidence[0]["external_program_ran"] is False


def test_limits_are_checked_before_sage_is_looked_for(no_sage) -> None:
    _blocked(sage.sage_evaluate("x", symbols=["x"], time_limit=601), TIME_LIMIT_TOO_LONG)
    _blocked(sage.sage_evaluate("x", symbols=["x"], time_limit=0), INPUT_INVALID)
    _blocked(sage.sage_version(time_limit=-1), INPUT_INVALID)


# --- what SageMath reads --------------------------------------------------------------------------------------------

def test_sage_reads_g6s_rendering_fully_bracketed_and_exact() -> None:
    node, declared = sage.screen("integrate(sin(x)^2, x, 0, pi) + 2.5*x - E", ["x"])
    sent = sage.to_sage(node)
    assert sent == "((integrate((sin(x)**2), x, 0, pi)+((5/2)*x))-exp(1))"
    assert re.fullmatch(r"[A-Za-z0-9_()*+\-/, ]+", sent)
    assert sage.to_sage(sage.screen("-oo", [])[0]) == "(-oo)"


def test_the_file_sage_runs_declares_the_symbols_and_marks_its_output() -> None:
    node, declared = sage.screen("x + n + t", {"x": ["real"], "n": ["integer", "positive"], "t": []})
    text = sage.build_file(node, declared, "abc123")
    assert "x = var('x', domain='real')" in text and "t = var('t')" in text
    assert "n = var('n', domain='integer')" in text and "assume(n > 0)" in text
    assert "_g6_value = (((x+n)+t))" in text
    for mark in ("VERSION", "TEXT", "LATEX", "END"):
        assert f"print('G6_SAGE_abc123_{mark}')" in text
    assert "import" not in text and "load" not in text and "eval" not in text


def test_sages_output_is_read_between_g6s_marks_only() -> None:
    nonce = "f00d"
    output = ("some banner\nG6_SAGE_f00d_VERSION\nSageMath version 10.4, Release Date: 2024-07-19\n"
              "G6_SAGE_f00d_TEXT\n(x + 1)*(x - 1)\nG6_SAGE_f00d_LATEX\n{\\left(x + 1\\right)} {\\left(x - 1\\right)}\n"
              "G6_SAGE_f00d_END\n")
    read = sage.read_output(output, nonce)
    assert read == {"version": "10.4", "text": "(x + 1)*(x - 1)",
                    "latex": "{\\left(x + 1\\right)} {\\left(x - 1\\right)}"}
    assert sage.read_output(output.replace("f00d", "beef"), nonce) is None
    assert sage.read_output(output.replace("G6_SAGE_f00d_END\n", ""), nonce) is None
    assert sage.parse_version("SageMath version 10.4, Release Date: 2024-07-19\n") == "10.4"
    assert sage.parse_version("something else") == ""


def test_the_sage_table_takes_no_strings_and_no_keyword_arguments() -> None:
    assert "load" not in L.SAGE_FUNCTIONS and "sage_eval" not in L.SAGE_FUNCTIONS
    assert {"factor", "integrate", "diff", "solve", "taylor"} <= set(L.SAGE_FUNCTIONS)


# --- with SageMath --------------------------------------------------------------------------------------------------

@needs_sage
def test_sage_version_is_read() -> None:
    result = sage.sage_version()
    assert check_envelope(result.model_dump()) == [] and result.completion_state == "qualified-draft"
    assert result.warning_card["code"] == SAGE_VERSION_READ and result.engine_version


@needs_sage
def test_sage_factors_and_integrates() -> None:
    factored = sage.sage_evaluate("factor(x^2 - 1)", symbols=["x"])
    assert factored.completion_state == "qualified-draft" and factored.warning_card["code"] == SAGE_ANSWER
    assert factored.answer == "(x + 1)*(x - 1)" and "did not re-check" in factored.warning_card["message"]
    assert sage.sage_evaluate("integrate(sin(x), x, 0, pi)", symbols=["x"]).answer == "2"
