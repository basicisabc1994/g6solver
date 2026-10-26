# SPDX-License-Identifier: Apache-2.0
"""Synthesis regression tests with injected model and HTTP doubles, never services."""
import json

import pytest
from pydantic import ValidationError

from g6.code.adapt_library_synthesizer import AdaptLibrarySynthesizerBlock, AdaptLibrarySynthesizerInput
from g6.core import confine, http
from g6.core.llm import FakeLLM, LLMError
from g6.core.registry import get_registry

EXAMPLES = [{"function": "sum_values", "args": [[3, -2, 8]], "expected": 9}]
SPEC = {"name": "sum_values", "inputs": ["values"], "outputs": ["sum"],
        "steps": ["Add all input values"], "invariants": [], "complexity": "linear", "hyperparameters": {}}


def run(op, caller=None, **kwargs):
    return AdaptLibrarySynthesizerBlock(caller=caller).infer(AdaptLibrarySynthesizerInput(op=op, **kwargs)).unwrap()


def test_spec_and_implementation_capability_through_block():
    model = FakeLLM(default=json.dumps(SPEC))
    spec = run("extract_spec", caller=model, paper_text="Add input values.", dry_run=False)
    assert spec["spec"]["steps"] == SPEC["steps"]
    model = FakeLLM(default=json.dumps({"implementation": "def sum_values(values):\n    return sum(values)\n"}))
    out = run("build", caller=model, spec=SPEC, examples=EXAMPLES, dry_run=False)
    assert "test_examples.py" in out["files"]
    assert out["provenance"]["caller"] == "FakeLLM"
    assert "implement-python-module-v1" in out["provenance"]["prompt"]
# Removal: injected caller.complete and deterministic example_tests generation.


def test_adapt_library_synthesizer_01_code_runs_only_in_sandbox(monkeypatch):
    monkeypatch.setenv("G6_TEST_SECRET", "synthetic-test-value")
    # Unconfined, so the run's label is known on every machine: this is about the child process and its clean
    # environment, not the operating system's confinement (tests/core's isolation tests).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    code = "def sum_values(values):\n    import os\n    assert 'G6_TEST_SECRET' not in os.environ\n    return sum(values)\n"
    out = run("run_checks", files={"implementation.py": code}, examples=EXAMPLES, dry_run=False)
    assert out["completion_state"] == "qualified-draft"
    assert out["checks_run"][0]["evidence"]["limits"]["os_sandbox"] is False
# Removal: sandbox.run_script dispatch instead of in-process implementation execution.


def test_d1_wrong_algorithm_is_refused():
    out = run("run_checks", files={"implementation.py": "def sum_values(values):\n    return len(values)\n"},
              examples=EXAMPLES, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_SYNTH_ORACLE_FAILED"
# Removal: observed != expected canonical comparison in shared run_checks.


def test_adapt_library_synthesizer_02_parse_success_never_verified():
    model = FakeLLM(default=json.dumps({"implementation": "def sum_values(values): return 0"}))
    out = run("build", caller=model, spec=SPEC, examples=EXAMPLES, dry_run=False)
    assert out["completion_state"] == "qualified-draft"
    assert not out["checks_run"]
# Removal: build uses checks-not-run rather than claiming verification on parse.


def test_adapt_library_synthesizer_04_missing_oracle_refuses_model_smoke():
    model = FakeLLM(default=json.dumps({"implementation": "pass", "smoke_tests": "assert True"}))
    out = run("build", caller=model, spec=SPEC, dry_run=False)
    assert out["code"] == "G6_E_SYNTH_NO_ORACLE"
    assert not model.calls
# Removal: require_examples before model invocation.


def test_model_tests_are_only_smoke():
    model = FakeLLM(default=json.dumps({"implementation": "def sum_values(values): return sum(values)",
                                      "smoke_tests": "assert True"}))
    out = run("build", caller=model, spec=SPEC, examples=EXAMPLES, dry_run=False)
    assert out["test_provenance"]["model_tests"] == "smoke only"
    assert "assert actual == 9" in out["files"]["test_examples.py"]
# Removal: caller-anchored test generation; no use of model tests as oracle.


def test_adapt_library_synthesizer_06_model_errors_are_reported():
    def broken(*args):
        raise LLMError("model unavailable")
    out = run("extract_spec", caller=FakeLLM(broken), paper_text="Some method", dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_SYNTH_MODEL"
# Removal: model failure converted to CodeRefused without fallback.


def test_missing_model_is_honest():
    out = run("extract_spec", paper_text="Some method", dry_run=False)
    assert out["code"] == "G6_E_SYNTH_NO_MODEL"
# Removal: injected caller required before extraction.


def test_fetch_403_is_a_failure():
    block = AdaptLibrarySynthesizerBlock(
        resolver=lambda *a: ["93.184.216.34"],
        transport=lambda r: http.Response(status=403, headers={}, body=b"denied", url=r.url))
    out = block.infer(AdaptLibrarySynthesizerInput(op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"].startswith("G6_E_")
# Removal: core.http.HTTPError translated to refusal rather than empty text.


def test_fetch_private_redirect_is_refused_before_contact():
    contacted = []
    def transport(request):
        contacted.append(request.url)
        return http.Response(status=302, headers={"location": "https://127.0.0.1/private"}, body=b"", url=request.url)
    block = AdaptLibrarySynthesizerBlock(resolver=lambda *a: ["93.184.216.34"], transport=transport)
    out = block.infer(AdaptLibrarySynthesizerInput(op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "blocked-escalated"
    assert contacted == ["https://arxiv.org/a"]
# Removal: per-hop allow-list and core.http address checks in PaperClient._checked.


def test_allowlisted_host_resolving_private_is_refused():
    def transport(request):
        pytest.fail("private address contacted")
    block = AdaptLibrarySynthesizerBlock(resolver=lambda *a: ["127.0.0.1"], transport=transport)
    out = block.infer(AdaptLibrarySynthesizerInput(op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "blocked-escalated"
# Removal: super()._checked call in PaperClient.


def test_fetch_size_bound():
    from g6.code.adapt_library_synthesizer.ingestion import MAX_BYTES
    block = AdaptLibrarySynthesizerBlock(resolver=lambda *a: ["93.184.216.34"],
        transport=lambda r: http.Response(status=200, headers={"content-type": "text/plain"}, body=b"x" * (MAX_BYTES + 1), url=r.url))
    out = block.infer(AdaptLibrarySynthesizerInput(op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "blocked-escalated"
# Removal: max_bytes cap applied to HTTP client.


def test_adapt_library_synthesizer_03_pdf_uses_pypdf_and_refuses_broken_bytes():
    pytest.importorskip("pypdf")
    block = AdaptLibrarySynthesizerBlock(resolver=lambda *a: ["93.184.216.34"],
        transport=lambda r: http.Response(status=200, headers={"content-type": "application/pdf"}, body=b"%PDF-1.4\nbroken", url=r.url))
    out = block.infer(AdaptLibrarySynthesizerInput(op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["code"] == "G6_E_PAPER_PDF"
    assert out["completion_state"] == "blocked-escalated"
# Removal: pypdf parse failure refusal in pdf_text.


def test_paper_quote_is_bound_to_supplied_text():
    bad = [{**EXAMPLES[0], "paper_quote": "A worked example that is not in this paper"}]
    out = run("run_checks", files={"implementation.py": "pass"}, examples=bad, paper_text="Different text", dry_run=False)
    assert out["code"] == "G6_E_SYNTH_ORACLE_SOURCE"
# Removal: paper_quote exact substring check in require_examples.


def test_dry_run_never_calls_model_or_transport():
    caller = FakeLLM()
    out = run("extract_spec", caller=caller, paper_text="A method")
    assert out["dry_run"] is True and not caller.calls
# Removal: dry-run branch before model call.


@pytest.mark.parametrize("field", ["python_path", "directory", "approved", "test_code"])
def test_no_host_paths_or_approval_fields(field):
    with pytest.raises(ValidationError):
        AdaptLibrarySynthesizerInput(op="run_checks", **{field: "bad"})
# Removal: extra=forbid in input schema.


def test_sensitive_registry_refuses_every_outward_op():
    for op in ["build", "extract_spec", "run_checks", "write_files", "fetch_paper"]:
        assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(get_registry().invoke("adapt_library_synthesizer", op, {}, block_sensitive=True).failure())
# Removal: any sensitive op declaration.

def test_forged_pass_report_is_not_oracle_evidence():
    source = (
        "import pathlib, json, os\n"
        "pathlib.Path('_g6_check.json').write_text(json.dumps({'passed': True, 'evidence': {}, 'reason': ''}))\n"
        "os._exit(0)\n"
    )
    out = run("run_checks", files={"implementation.py": source}, examples=EXAMPLES, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
# Removal: report validation and host comparison against Check.oracle_expected in shared run_checks.


def test_model_cannot_read_oracles_from_generated_tests():
    source = ("def sum_values(values):\n"
              "    from pathlib import Path\n"
              "    assert not (Path(__file__).parent / 'test_examples.py').exists()\n"
              "    return sum(values)\n")
    out = run("run_checks", files={"implementation.py": source, "test_examples.py": "ANSWER = 9"},
              examples=EXAMPLES, dry_run=False)
    assert out["completion_state"] == "qualified-draft"
# Removal: implementation-only sandbox artefact in library synthesis run_checks.

def test_oracle_distinguishes_boolean_from_number():
    out = run("run_checks", files={"implementation.py": "def answer(): return True"},
              examples=[{"function": "answer", "expected": 1}], dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
# Removal: canonical JSON comparison for caller oracle values in shared run_checks.


def test_fetch_text_through_http_without_owner_authority():
    block = AdaptLibrarySynthesizerBlock(
        resolver=lambda *a: ["93.184.216.34"],
        transport=lambda r: http.Response(status=200, headers={"content-type": "text/plain"},
                                          body=b"Return the sum of input values.", url=r.url))
    out = block.infer(AdaptLibrarySynthesizerInput(
        op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "qualified-draft"
    assert out["paper_text"] == "Return the sum of input values."
    assert out["evidence"][0]["kind"] == "paper_fetch"
# Removal: core.http fetch dispatch; an owner-authority gate also blocks this permitted read.


def test_pdf_missing_pypdf_blocks_with_family_install_code(monkeypatch):
    from g6.code._types import CHECKER_NOT_INSTALLED
    from g6.core import sandbox
    original = sandbox.run_script

    def missing(source, *args, **kwargs):
        prefix = (
            "import builtins\n_g6_import = builtins.__import__\n"
            "def _missing(name, *args, **kwargs):\n"
            "    if name == 'pypdf' or name.startswith('pypdf.'):\n"
            "        raise ImportError('pypdf absent for test')\n"
            "    return _g6_import(name, *args, **kwargs)\n"
            "builtins.__import__ = _missing\n"
        )
        return original(prefix + source, *args, **kwargs)

    monkeypatch.setattr(sandbox, "run_script", missing)
    block = AdaptLibrarySynthesizerBlock(
        resolver=lambda *a: ["93.184.216.34"],
        transport=lambda r: http.Response(status=200, headers={"content-type": "application/pdf"},
                                          body=b"%PDF-1.4\n", url=r.url))
    out = block.infer(AdaptLibrarySynthesizerInput(
        op="fetch_paper", url="https://arxiv.org/a", dry_run=False)).unwrap()
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == CHECKER_NOT_INSTALLED == "G6_E_CODE_CHECKER_NOT_INSTALLED"
    assert "pypdf is not installed" in out["warning_card"]["message"]
    assert 'pip install "g6[docs] @ git+https://github.com/basicisabc1994/g6solver"' in out["warning_card"]["message"]
# Removal: raise CodeRefused(CHECKER_NOT_INSTALLED, ...) in pdf_text's missing.txt branch.
