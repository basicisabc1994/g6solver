# SPDX-License-Identifier: Apache-2.0
"""Archive transport, bounded paths, honest checks and sensitive-operation boundaries."""
import pytest

from g6.code import _build
from g6.code._types import CodeRefused
from g6.core import confine, sandbox


def test_nested_artefact_crosses_sandbox_as_one_plain_archive(monkeypatch):
    # Unconfined, so the run's label is known on every machine: this is about the archive crossing into the child,
    # not the operating system's confinement (tests/core's isolation tests).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    artefact = _build.Artefact(files={"pkg/value.txt": b"eight"}, provenance=_build.Provenance(kind="caller"))
    checks = _build.run_checks(artefact, [_build.Check("nested", "assert (root / 'pkg/value.txt').read_text() == 'eight'")])
    assert checks[0].passed is True
    assert checks[0].evidence["limits"]["os_sandbox"] is False
# Removal: sandbox.run_script(script, {"artefact.zip": packed}, ...) in run_checks.


@pytest.mark.parametrize("path", ["../escape", "/root", "a/../b", "C:/a", "a\\b", "a//b", "CON", "a./b", "a:stream"])
def test_paths_are_plain_relative(path):
    with pytest.raises((ValueError, CodeRefused)):
        _build.Artefact(files={path: b"x"}, provenance=_build.Provenance(kind="caller"))
# Removal: plain_path(name) in _validate_names.


def test_size_and_case_collisions_are_refused():
    with pytest.raises(CodeRefused):
        _build.Artefact(files={"a": b"x" * (_build.MAX_FILE_BYTES + 1)}, provenance=_build.Provenance(kind="caller"))
    with pytest.raises(CodeRefused):
        _build.Artefact(files={"a": b"1", "A": b"2"}, provenance=_build.Provenance(kind="caller"))
# Removal: byte bounds in Artefact.__post_init__ and casefold collision refusal in _validate_names.


def test_d1_failed_check_blocks_even_when_other_checks_pass():
    checks = _build.run_checks(
        _build.Artefact(files={"value": b"13"}, provenance=_build.Provenance(kind="caller")),
        [_build.Check("quantity", "assert int((root / 'value').read_text()) == 17")],
    )
    reply = _build.checked_reply("test", "run_checks", checks)
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["code"] == "G6_E_BUILD_CHECK_FAILED"
    assert reply["checks_run"][0]["passed"] is False
# Removal: failed = next(...) and refusal branch in checked_reply.


def test_unrequested_check_is_not_a_pass_and_success_is_only_a_draft():
    checks = [_build.CheckResult("parse", True, {"parser": "ast"}),
              _build.CheckResult("render", None, {}, "render not requested")]
    reply = _build.checked_reply("test", "run_checks", checks)
    assert reply["completion_state"] == "qualified-draft"
    assert reply["checks_not_run"][0]["reason"] == "render not requested"
    assert "render" in reply["warning_card"]["message"]
# Removal: passed is None classification in checked_reply.


def test_missing_result_and_sandbox_errors_fail_closed(monkeypatch):
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: sandbox.ScriptRun("ok", 0, "", ""))
    result = _build.run_checks(_build.Artefact(files={"a": b""}, provenance=_build.Provenance(kind="caller")),
                              [_build.Check("oracle", "pass")])
    assert result[0].passed is False
# Removal: missing report is rejected in run_checks.


def test_model_provenance_requires_caller_and_prompt():
    with pytest.raises(ValueError):
        _build.Provenance(kind="model")
# Removal: model caller/prompt validation in Provenance.__post_init__.



def test_writes_require_roots_and_do_not_overwrite(tmp_path):
    from g6.code import _roots
    artifact = _build.Artefact.from_text({"pkg/one.py": "value = 1"})
    with pytest.raises(CodeRefused):
        _build.write_files(artifact, str(tmp_path / "new"), _roots.Roots((), "none"),
                           dry_run=False, component="test")
    roots = _roots.Roots((tmp_path.resolve(),), "test")
    out = _build.write_files(artifact, str(tmp_path / "new"), roots, dry_run=False, component="test")
    assert out["written"] == ["pkg/one.py"]
    with pytest.raises(CodeRefused):
        _build.write_files(artifact, str(tmp_path / "new"), roots, dry_run=False, component="test")
    assert (tmp_path / "new/pkg/one.py").read_text() == "value = 1"
# Removal: _roots.destination check or exclusive xb writer.


def test_parent_file_collision_is_refused():
    with pytest.raises(CodeRefused):
        _build.Artefact.from_text({"pkg": "file", "pkg/child.py": "pass"})
# Removal: parent file detection in _validate_names.

def test_check_restores_working_directory_before_cleanup():
    artifact = _build.Artefact.from_text({"nested/value": "x"})
    result = _build.run_checks(artifact, [_build.Check("cwd", "os.chdir(root)\nassert pathlib.Path('nested/value').is_file()")])
    assert result[0].passed is True
# Removal: os.chdir(initial_directory) in run_checks script finally.

# Exercise all sensitive doors independently, with valid dry-run inputs.
SENSITIVE_CASES = [
    ("adapt_webpage", "AdaptWebpage", "run_checks", {"files": {"index.html": "<p>Text</p>"}}),
    ("adapt_webpage", "AdaptWebpage", "write_files", {"files": {"draft.txt": "draft"}}),
    ("adapt_django", "AdaptDjango", "run_checks", {"files": {"manage.py": "pass"}}),
    ("adapt_django", "AdaptDjango", "write_files", {"files": {"draft.txt": "draft"}}),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer", "run_checks", {
        "files": {"implementation.py": "def answer(): return 7"},
        "examples": [{"function": "answer", "expected": 7}]}),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer", "write_files", {"files": {"draft.txt": "draft"}}),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer", "fetch_paper", {"url": "https://arxiv.org/a"}),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer", "extract_spec", {"paper_text": "Return seven."}),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer", "build", {
        "spec": {"name": "answer", "steps": ["Return seven"]},
        "examples": [{"function": "answer", "expected": 7}]}),
]


@pytest.mark.parametrize("member,prefix,op,params", SENSITIVE_CASES)
def test_sensitive_ops_default_to_dry_run_without_effects(member, prefix, op, params, monkeypatch, tmp_path):
    import importlib
    from g6.code import _roots
    from g6.core import http
    from g6.core.llm import FakeLLM

    def forbidden(*args, **kwargs):
        pytest.fail("dry run reached an execution, fetch, write or configuration boundary")

    monkeypatch.setattr(sandbox, "run_script", forbidden)
    monkeypatch.setattr(http.Client, "get", forbidden)
    monkeypatch.setattr(_roots, "load", forbidden)
    monkeypatch.setattr(_roots, "destination", forbidden)
    module = importlib.import_module("g6.code." + member)
    kwargs = {"caller": FakeLLM(forbidden)} if member == "adapt_library_synthesizer" else {}
    block = getattr(module, prefix + "Block")(**kwargs)
    supplied = {**params, "dest": str(tmp_path / "draft")} if op == "write_files" else params
    out = block.infer(getattr(module, prefix + "Input")(op=op, **supplied)).unwrap()
    assert out["dry_run"] is True
    assert out["completion_state"] == "qualified-draft"
    assert not out["checks_run"]
    assert not (tmp_path / "draft").exists()
# Removal: default dry_run=True or dry-run branches before sandbox, HTTP, model and root-confined writer.


@pytest.mark.parametrize("member,prefix,op,params", SENSITIVE_CASES)
def test_sensitive_ops_mcp_requires_operator_allowance(member, prefix, op, params, monkeypatch):
    import json
    from g6.code import _roots
    from g6.core import http
    from g6.mcp import invoke_reply

    def forbidden(*args, **kwargs):
        pytest.fail("MCP denial or allowed dry run reached a side effect")

    monkeypatch.setattr(sandbox, "run_script", forbidden)
    monkeypatch.setattr(http.Client, "get", forbidden)
    monkeypatch.setattr(_roots, "load", forbidden)
    monkeypatch.setattr(_roots, "destination", forbidden)

    denied = invoke_reply(member, op, json.dumps({**params, "dry_run": False}), allow_sensitive=frozenset())
    assert denied["ok"] is False
    assert denied["completion_state"] == "blocked-escalated"
    assert denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    allowed = invoke_reply(member, op, json.dumps(params), allow_sensitive=frozenset({member + "." + op}))
    assert allowed["ok"] is True
    assert allowed["result"]["dry_run"] is True
    assert allowed["completion_state"] == "qualified-draft"
# Removal: each op's SENSITIVE_OPS declaration; allowing MCP still retains the block's dry-run default.


@pytest.mark.parametrize("member,prefix", [
    ("adapt_webpage", "AdaptWebpage"), ("adapt_django", "AdaptDjango"),
    ("adapt_library_synthesizer", "AdaptLibrarySynthesizer"),
])
def test_explicit_block_write_uses_declared_destination(member, prefix, tmp_path):
    import importlib
    from g6.code import _roots

    module = importlib.import_module("g6.code." + member)
    block = getattr(module, prefix + "Block")(roots=_roots.Roots((tmp_path.resolve(),), "test"))
    dest = tmp_path / "draft"
    out = block.infer(getattr(module, prefix + "Input")(
        op="write_files", files={"draft.txt": "caller supplied"}, dest=str(dest), dry_run=False)).unwrap()
    assert out["completion_state"] == "qualified-draft"
    assert out["written"] == ["draft.txt"]
    assert (dest / "draft.txt").read_text() == "caller supplied"
# Removal: write_files dispatch or exclusive byte writer; an owner-authority gate also breaks the real write.


@pytest.mark.parametrize("field,value", [("token", None), ("token", "wrong"),
                                          ("completed", None), ("completed", False)])
def test_check_report_needs_run_token_and_completion(monkeypatch, field, value):
    import json
    original = sandbox.run_script

    def corrupt(*args, **kwargs):
        ran = original(*args, **kwargs)
        data = json.loads(ran.outputs["_g6_check.json"])
        if value is None:
            data.pop(field, None)
        else:
            data[field] = value
        ran.outputs["_g6_check.json"] = json.dumps(data).encode("utf-8")
        return ran

    monkeypatch.setattr(sandbox, "run_script", corrupt)
    result = _build.run_checks(_build.Artefact.from_text({"value": "x"}), [_build.Check("check", "pass")])
    assert result[0].passed is False
    assert result[0].code == "G6_E_BUILD_CHECK_FAILED"
# Removal: required report fields and token/completed validation in run_checks.


def test_previous_run_report_cannot_be_replayed(monkeypatch):
    original = sandbox.run_script
    reports = []

    def replay(*args, **kwargs):
        ran = original(*args, **kwargs)
        reports.append(ran.outputs["_g6_check.json"])
        ran.outputs["_g6_check.json"] = reports[0]
        return ran

    monkeypatch.setattr(sandbox, "run_script", replay)
    result = _build.run_checks(_build.Artefact.from_text({"value": "x"}),
                               [_build.Check("first", "pass"), _build.Check("second", "pass")])
    assert result[0].passed is True
    assert result[1].passed is False
    assert reports[0] != reports[1]
# Removal: secrets.token_hex(32) inside the per-check loop in run_checks.


@pytest.mark.parametrize("limit,values,expected", [
    ("file", ["\u00e9\u00e9\u00e9", "unused", "unused"], ["a"]),
    ("total", ["\u00e9\u00e9", "\u00e9\u00e9", "unused"], ["a", "b"]),
])
@pytest.mark.parametrize("door", ["direct", "adapt_webpage", "adapt_django", "adapt_library_synthesizer"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_encoding_stops_at_first_byte_limit(monkeypatch, limit, values, expected, door, dry_run):
    import importlib
    seen = []

    class ObservedText(str):
        def encode(self, *args, **kwargs):
            seen.append(self.name)
            return super().encode(*args, **kwargs)

    files = {}
    for name, value in zip(("a", "b", "c"), values):
        content = ObservedText(value)
        content.name = name
        files[name] = content
    monkeypatch.setattr(_build, "MAX_FILE_BYTES", 4)
    monkeypatch.setattr(_build, "MAX_TOTAL_BYTES", 6)
    if door == "direct":
        with pytest.raises(CodeRefused) as caught:
            _build.Artefact.from_text(files)
        assert caught.value.code == "G6_E_BUILD_SIZE"
    else:
        module = importlib.import_module("g6.code." + door)
        prefix = {"adapt_webpage": "AdaptWebpage", "adapt_django": "AdaptDjango",
                  "adapt_library_synthesizer": "AdaptLibrarySynthesizer"}[door]
        data = getattr(module, prefix + "Input")(op="write_files", files=files, dry_run=dry_run)
        # Keep the str subclass solely to observe encoding after schema validation.
        data = data.model_copy(update={"files": files})
        out = getattr(module, prefix + "Block")().infer(data).unwrap()
        assert out["completion_state"] == "blocked-escalated"
        assert out["code"] == "G6_E_BUILD_SIZE"
    assert seen == expected
# Removal: per-file/cumulative bound refusal immediately after each encode in Artefact.from_text.


@pytest.mark.parametrize("names,code", [
    (["ok", "../escape"], "G6_E_BUILD_PATH"),
    (["ok", "OK"], "G6_E_BUILD_PATH"),
    (["pkg", "pkg/child"], "G6_E_BUILD_PATH"),
    ([f"file{i}" for i in range(_build.MAX_FILES + 1)], "G6_E_BUILD_SIZE"),
])
def test_names_and_count_are_validated_before_encoding(names, code):
    seen = []

    class ObservedText(str):
        def encode(self, *args, **kwargs):
            seen.append(self)
            return super().encode(*args, **kwargs)

    with pytest.raises(CodeRefused) as caught:
        _build.Artefact.from_text({name: ObservedText("value") for name in names})
    assert caught.value.code == code
    assert seen == []
# Removal: _validate_names(files) before the encoding loop in Artefact.from_text.
