"""Offline acquisition tools exercised through their API and command line seams."""
from __future__ import annotations

import copy
import contextlib
import difflib
import importlib
import io
import json
import pathlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.distill.held_out import oracle
from g6.distill.held_out.case_gate import CaseRecord
from validation.distill.pilot import allocation


def tool(name):
    return importlib.import_module(f"validation.distill.pilot.{name}")


@pytest.mark.parametrize("name", ["access", "preflight", "census", "convert"])
def test_tools_are_importable(name):
    assert callable(tool(name).main)


def cohort_data():
    ids = [f"repo{i}-1" for i in range(18)]
    roles = allocation.allocate("a" * 64, [allocation.repository_of(b) for b in ids])
    manifest = {
        "cohort": "development", "cohort_digest": "a" * 64, "label": allocation.LABEL,
        "roles": {role: {"repositories": repos,
                         "bugs": [b for b in ids if allocation.repository_of(b) in repos]}
                  for role, repos in roles.items()},
    }
    assert allocation.check(manifest) == []
    bugs = [{"id": b, "task_image": f"g6we-task:{b}", "task_image_id": "sha256:" + "b" * 64,
             "oracle_image": f"g6we-oracle:{b}", "oracle_image_id": "sha256:" + "c" * 64,
             "usable": True, "controls": {}} for b in ids]
    return manifest, {"cohort": "development", "digest": "a" * 64, "bugs": bugs}


@pytest.fixture
def inputs(tmp_path):
    manifest, body = cohort_data()
    cohort = tmp_path / "development.json"
    cohort.write_text(json.dumps(body))
    manifest_path = tmp_path / "allocation.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest, cohort, manifest_path


def acquisition(inputs):
    return inputs[0]["roles"]["acquisition"]["bugs"][0]


def patch(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


@pytest.fixture
def material():
    base = {"subject.py": "def answer(x):\n    return x\n",
            "tests/test_answer.py": "from subject import answer\ndef test_answer():\n    assert answer(0) == 0\n"}
    fixed = {"subject.py": "def answer(x):\n    return x + 1\n",
             "tests/test_answer.py": "from subject import answer\ndef test_answer():\n    assert answer(0) == 1\n"}
    return {"name_status": "M\tsubject.py\nM\ttests/test_answer.py\n",
            "patch": "".join(patch(p, base[p], fixed[p]) for p in base), "base": base, "fixed": fixed}


class DockerFake:
    def __init__(self, material=None, failure=None):
        self.material = material
        self.failure = failure
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        assert command[0] == "docker"
        if command[1] == "info":
            return subprocess.CompletedProcess(command, int(self.failure == "daemon_down"), "1.0", "")
        if command[1:3] == ["image", "inspect"]:
            ident = "b" if "task" in command[3] else "c"
            if self.failure == "image_id_mismatch":
                ident = "d"
            return subprocess.CompletedProcess(command, int(self.failure == "image_missing"),
                                               "sha256:" + ident * 64, "")
        assert command[1] == "run"
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--pull") + 1] == "never"
        if self.failure == "extraction_failed":
            return subprocess.CompletedProcess(command, 1, "", "fixture extraction failure")
        text = json.dumps(self.material) if "-c" in command else ""
        return subprocess.CompletedProcess(command, int(self.failure == "network_off_run_failed"), text, "")


@pytest.mark.parametrize("role", ["admission", "monitor", "reuse"])
@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_tools_refuse_roles_before_cohort_or_runner(inputs, monkeypatch, role, name):
    manifest, cohort, _ = inputs
    bug = manifest["roles"][role]["bugs"][0]
    runner = DockerFake()
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("cohort read before access"))
    with pytest.raises(ValueError, match=f"role={role}"):
        tool(name).inspect_bug(manifest, cohort, bug, runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_invalid_manifest_refuses_all(inputs, name):
    manifest, cohort, _ = inputs
    bad = copy.deepcopy(manifest)
    bad["label"] = "changed"
    runner = DockerFake()
    with pytest.raises(ValueError, match="invalid allocation"):
        tool(name).inspect_bug(bad, cohort, acquisition(inputs), runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_sealed_name_refused_without_open(inputs, monkeypatch, name):
    runner = DockerFake()
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: pytest.fail("sealed cohort opened"))
    with pytest.raises(ValueError, match="sealed"):
        tool(name).inspect_bug(inputs[0], inputs[1].with_name("final-do-not-open.json"),
                               acquisition(inputs), runner=runner)
    assert runner.calls == []


def test_access_returns_only_registered_acquisition_id(inputs):
    access = tool("access")
    assert access.acquisition_bug(inputs[0], acquisition(inputs)) == acquisition(inputs)
    with pytest.raises(ValueError, match="role=unallocated"):
        access.acquisition_bug(inputs[0], "repo-999")
    with pytest.raises(ValueError, match="invalid allocation"):
        access.acquisition_bug({}, acquisition(inputs))


@pytest.mark.parametrize("failure", ["daemon_down", "image_missing", "image_id_mismatch",
                                     "network_off_run_failed", None])
def test_preflight_codes_and_local_network_off_commands(inputs, failure):
    runner = DockerFake(failure=failure)
    result = tool("preflight").inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=runner)
    if failure:
        assert failure in result["reason_codes"]
        assert not result["ok"]
    else:
        assert result["ok"]
        assert len([c for c in runner.calls if c[1] == "run"]) == 2
    if failure == "daemon_down":
        assert len(runner.calls) == 1
    if failure in {"image_missing", "image_id_mismatch"}:
        assert all(c[1] != "run" for c in runner.calls)


def test_census_matches_oracle_sample_on_same_record(inputs, material, tmp_path, monkeypatch):
    census, convert = tool("census"), tool("convert")
    record = convert.inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=DockerFake(material))
    seen = []
    original = oracle.mutation_check.mutants

    def sample(source, **kwargs):
        seen.append((source, kwargs["limit"], tuple(kwargs["lines"])))
        return original(source, **kwargs)

    monkeypatch.setattr(oracle.mutation_check, "mutants", sample)
    monkeypatch.setattr(oracle, "_run", lambda case, implementation, suite, **kw:
                        "passed" if implementation == case.files[case.target] else "failed")
    result = oracle.label(record, policy=oracle.OraclePolicy(min_mutants=1), workspace=tmp_path)
    counted = census.inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=DockerFake(material))
    assert counted["distinct_mutant_count"] == len(result.before_kills) > 0
    assert counted["changed_line_count"] == 1
    assert counted["reaches_min_mutants"] is False
    assert seen[0] == seen[1] == (record.files[record.target], 128, record.changed_lines)


def test_census_non_python_and_extraction_failure_are_rows(inputs, material):
    census = tool("census")
    non_python = {"name_status": "M\treadme.txt\n", "patch": "", "base": {}, "fixed": {}}
    assert census.inspect_bug(inputs[0], inputs[1], acquisition(inputs),
                              runner=DockerFake(non_python))["reason_code"] == "no_python_fix"
    assert census.inspect_bug(inputs[0], inputs[1], acquisition(inputs),
                              runner=DockerFake(material, "extraction_failed"))["reason_code"] == "extraction_failed"


def test_convert_existing_validation_and_empty_lines(inputs, material):
    convert = tool("convert")
    record = convert.inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=DockerFake(material))
    assert CaseRecord.model_validate_json(record.model_dump_json()) == record
    assert record.known_wrong == (material["base"]["subject.py"],)
    assert record.before_tests == material["base"]["tests/test_answer.py"]
    assert record.after_tests == material["fixed"]["tests/test_answer.py"]
    material["patch"] = ""
    with pytest.raises(ValueError, match="empty_changed_lines"):
        convert.inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=DockerFake(material))


@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_cli_writes_lf_json_and_census_repository_summary(inputs, material, tmp_path, name):
    module = tool(name)
    output = tmp_path / ("cases" if name == "convert" else f"{name}.json")
    args = ["--manifest", str(inputs[2]), "--cohort", str(inputs[1]), "--output", str(output)]
    if name == "convert":
        args += ["--bug", acquisition(inputs)]
    assert module.main(args, runner=DockerFake(material)) == 0
    paths = list(output.glob("*.json")) if name == "convert" else [output]
    assert paths
    for path in paths:
        data = path.read_bytes()
        assert data.endswith(b"\n") and b"\r\n" not in data
        body = json.loads(data)
        if name == "convert":
            CaseRecord.model_validate(body)
        elif name == "census":
            assert len(body["bugs"]) == 6
            assert len(body["repositories"]) == 6


@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_cohort_binding_before_runner(inputs, name):
    body = json.loads(inputs[1].read_text())
    body["digest"] = "d" * 64
    inputs[1].write_text(json.dumps(body))
    runner = DockerFake()
    with pytest.raises(ValueError, match="cohort differs"):
        tool(name).inspect_bug(inputs[0], inputs[1], acquisition(inputs), runner=runner)
    assert runner.calls == []


def test_sampler_deduplicates_and_omits_original(monkeypatch):
    from g6.workforce.mutation_check import Mutant

    census = tool("census")
    monkeypatch.setattr(oracle.mutation_check, "mutants", lambda *a, **kw: [
        Mutant(2, "", "", "fixed"), Mutant(2, "", "", "other"), Mutant(2, "", "", "other")])
    assert len(census.sample("fixed", (2,), oracle.OraclePolicy())) == 1


def test_sampler_uses_oracle_limits_lines_and_counts(material, monkeypatch):
    census = tool("census")
    fixed = material["fixed"]["subject.py"]
    case = CaseRecord(unit_id="synthetic-1", provenance={"source": "maintainer", "cohort": "development",
                      "source_ref": "synthetic-fix", "source_sha256": "a" * 64},
                      files={"subject.py": fixed}, target="subject.py", changed_lines=(2,),
                      before_tests=material["base"]["tests/test_answer.py"],
                      after_tests=material["fixed"]["tests/test_answer.py"],
                      known_wrong=(material["base"]["subject.py"],))
    policy = oracle.OraclePolicy(min_mutants=1)
    calls = []
    original = oracle.mutation_check.mutants

    def instrument(source, **kwargs):
        calls.append((source, kwargs["limit"], tuple(kwargs["lines"])))
        assert kwargs["deadline"] is not None
        return original(source, **kwargs)

    monkeypatch.setattr(oracle.mutation_check, "mutants", instrument)
    monkeypatch.setattr(oracle, "_run", lambda case, implementation, suite, **kw:
                        "passed" if implementation == fixed else "failed")
    result = oracle.label(case, policy=policy, workspace=Path.cwd())
    counted = census.sample(fixed, case.changed_lines, policy)
    assert set(counted) == set(result.before_kills)
    assert calls == [(fixed, 128, (2,)), (fixed, 128, (2,))]


@pytest.mark.parametrize("before,after,expected", [
    ("a\nb\nc\n", "a\nnew\nc\n", (2,)),
    ("a\nb\nc\n", "a\nc\n", ()),
    ("", "new\n", (1,)),
    ("a\n", "", ()),
])
def test_fixed_side_lines_exclude_context_and_deletions(before, after, expected):
    material = {"patch": patch("subject.py", before, after), "fixed": {"subject.py": after}}
    assert tool("_material").changed_lines(material, "subject.py") == expected


@pytest.mark.parametrize("support", [False, True])
def test_image_extractor_reads_real_layout_in_memory(material, monkeypatch, support):
    module = tool("_material")
    stored = {"/oracle/fix.files": material["name_status"], "/oracle/fix.patch": material["patch"],
              **{f"/repo/{p}": s for p, s in material["base"].items()},
              **{f"/oracle/fixed/{p}": s for p, s in material["fixed"].items()}}
    stored["/oracle/fixed/helpers.py"] = "VALUE = 1\n"

    class MemoryPath(pathlib.PurePosixPath):
        @classmethod
        def cwd(cls):
            return cls("/repo")

        def read_text(self, **kwargs):
            return stored[str(self)]

        def is_symlink(self):
            return False

        def resolve(self):
            return self

        def is_file(self):
            return str(self) in stored

        def stat(self):
            return SimpleNamespace(st_size=len(stored[str(self)].encode()))

        def rglob(self, pattern):
            return (MemoryPath(p) for p in stored if p.startswith(str(self) + "/") and p.endswith(".py"))

    output = io.StringIO()
    with monkeypatch.context() as local, contextlib.redirect_stdout(output):
        local.setattr(pathlib, "Path", MemoryPath)
        local.setattr(sys, "argv", ["extract", "support"] if support else ["extract"])
        exec(compile(module.EXTRACT, "<pilot-extractor>", "exec"), {})
    if support:
        material["fixed"]["helpers.py"] = "VALUE = 1\n"
    assert json.loads(output.getvalue()) == material


@pytest.fixture
def memory_cohort(monkeypatch):
    """A JSON transport double; no temporary directory or image is involved."""
    manifest, body = cohort_data()
    path = Path.cwd() / ".build" / "cohort-transport-double.json"
    original = Path.read_text
    reads = []

    def read(self, *args, **kwargs):
        if self == path:
            reads.append("cohort")
            return json.dumps(body)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    return manifest, path, body, reads


@pytest.mark.parametrize("role", ["admission", "monitor", "reuse"])
@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_memory_transport_is_not_opened_for_other_roles(memory_cohort, name, role):
    manifest, path, _, reads = memory_cohort
    runner = DockerFake()
    with pytest.raises(ValueError, match=f"role={role}"):
        tool(name).inspect_bug(manifest, path, manifest["roles"][role]["bugs"][0], runner=runner)
    assert reads == runner.calls == []


@pytest.mark.parametrize("failure", ["daemon_down", "image_missing", "image_id_mismatch",
                                     "network_off_run_failed", None])
def test_memory_preflight_reports_each_failure(memory_cohort, failure):
    manifest, path, _, _ = memory_cohort
    runner = DockerFake(failure=failure)
    row = tool("preflight").inspect_bug(manifest, path, manifest["roles"]["acquisition"]["bugs"][0], runner=runner)
    assert row["reason_codes"] == ([failure] if failure else [])
    assert row["ok"] == (failure is None)
    if failure == "daemon_down":
        assert len(runner.calls) == 1
    if failure in {"image_missing", "image_id_mismatch"}:
        assert not any(c[1] == "run" for c in runner.calls)


@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_memory_manifest_and_sealed_cohort_refuse_before_runner(memory_cohort, name):
    manifest, path, _, reads = memory_cohort
    runner = DockerFake()
    bug = manifest["roles"]["acquisition"]["bugs"][0]
    broken = copy.deepcopy(manifest)
    broken["label"] = "changed"
    with pytest.raises(ValueError, match="invalid allocation"):
        tool(name).inspect_bug(broken, path, bug, runner=runner)
    with pytest.raises(ValueError, match="sealed"):
        tool(name).inspect_bug(manifest, path.with_name("final-unopened.json"), bug, runner=runner)
    assert reads == runner.calls == []


def test_memory_conversion_and_census_use_the_same_fixed_lines(memory_cohort, material):
    manifest, path, _, _ = memory_cohort
    bug = manifest["roles"]["acquisition"]["bugs"][0]
    record = tool("convert").inspect_bug(manifest, path, bug, runner=DockerFake(material))
    assert CaseRecord.model_validate_json(record.model_dump_json()) == record
    row = tool("census").inspect_bug(manifest, path, bug, runner=DockerFake(material))
    expected = tool("census").sample(record.files[record.target], record.changed_lines, oracle.OraclePolicy())
    assert row["distinct_mutant_count"] == len(expected)
    assert row["changed_line_count"] == 1
    assert row["files"][0]["changed_lines"] == list(record.changed_lines)
    material["patch"] = ""
    with pytest.raises(ValueError, match="empty_changed_lines"):
        tool("convert").inspect_bug(manifest, path, bug, runner=DockerFake(material))


def test_memory_census_keeps_non_python_and_failed_extractions(memory_cohort):
    manifest, path, _, _ = memory_cohort
    bug = manifest["roles"]["acquisition"]["bugs"][0]
    material = {"name_status": "M\treadme.txt\n", "patch": "", "fixed": {}, "base": {}}
    assert tool("census").inspect_bug(manifest, path, bug,
                                      runner=DockerFake(material))["reason_code"] == "no_python_fix"
    assert tool("census").inspect_bug(manifest, path, bug,
                                      runner=DockerFake(failure="extraction_failed"))["reason_code"] == "extraction_failed"


def test_memory_census_cli_summarizes_repositories(memory_cohort, material, monkeypatch):
    manifest, cohort, _, _ = memory_cohort
    access, census = tool("access"), tool("census")
    manifest_path = cohort.with_name("manifest-transport-double.json")
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw:
                        json.dumps(manifest) if p == manifest_path else original(p, *a, **kw))
    reports = []
    monkeypatch.setattr(access, "write_json", lambda path, body: reports.append(body))
    assert census.main(["--manifest", str(manifest_path), "--cohort", str(cohort), "--output", "unused.json"],
                       runner=DockerFake(material)) == 0
    report = reports[0]
    assert len(report["bugs"]) == len(report["repositories"]) == 6
    for repo, group in report["repositories"].items():
        rows = [row for row in report["bugs"] if row["repository"] == repo]
        assert group["changed_line_count"] == sum(row["changed_line_count"] for row in rows)
        assert group["distinct_mutant_count"] == sum(row["distinct_mutant_count"] for row in rows)


@pytest.mark.parametrize("change,reason", [("multiple_targets", "multiple_python_targets"),
                                           ("multiple_tests", "single_test_module_required"),
                                           ("new_test", "missing_test_pair")])
def test_memory_converter_refuses_ambiguous_material(memory_cohort, material, change, reason):
    manifest, path, _, _ = memory_cohort
    if change == "multiple_targets":
        material["name_status"] += "M\tsecond.py\n"
        material["fixed"]["second.py"] = "VALUE = 2\n"
    elif change == "multiple_tests":
        material["name_status"] += "M\ttests/test_second.py\n"
    else:
        del material["base"]["tests/test_answer.py"]
    with pytest.raises(ValueError, match=reason):
        tool("convert").inspect_bug(manifest, path, manifest["roles"]["acquisition"]["bugs"][0],
                                  runner=DockerFake(material))


def test_access_guard_uses_manifest_ids_without_transport():
    manifest, _ = cohort_data()
    access = tool("access")
    bug = manifest["roles"]["acquisition"]["bugs"][0]
    assert access.acquisition_bug(manifest, bug) == bug
    for role in ("admission", "monitor", "reuse"):
        with pytest.raises(ValueError, match=f"role={role}"):
            access.acquisition_bug(manifest, manifest["roles"][role]["bugs"][0])
    for invalid in ({}, {**manifest, "label": "changed"}):
        with pytest.raises(ValueError, match="invalid allocation"):
            access.acquisition_bug(invalid, bug)


@pytest.mark.parametrize("change", ["digest", "cohort", "duplicate_id", "missing_id"])
@pytest.mark.parametrize("name", ["preflight", "census", "convert"])
def test_memory_cohort_binding_refuses_before_runner(memory_cohort, change, name):
    manifest, path, body, _ = memory_cohort
    if change in {"digest", "cohort"}:
        body[change] = "changed"
    elif change == "duplicate_id":
        body["bugs"].append(body["bugs"][0])
    else:
        body["bugs"].pop()
    runner = DockerFake()
    with pytest.raises(ValueError, match="cohort differs"):
        tool(name).inspect_bug(manifest, path, manifest["roles"]["acquisition"]["bugs"][0], runner=runner)
    assert runner.calls == []


def test_memory_failed_extraction_count_is_unknown(memory_cohort):
    manifest, path, _, _ = memory_cohort
    row = tool("census").inspect_bug(manifest, path, manifest["roles"]["acquisition"]["bugs"][0],
                                    runner=DockerFake(failure="extraction_failed"))
    assert row["reason_code"] == "extraction_failed"
    assert row["distinct_mutant_count"] is None
    assert row["changed_line_count"] is None


def test_memory_census_does_not_pool_targets(memory_cohort, material, monkeypatch):
    manifest, path, _, _ = memory_cohort
    material["name_status"] += "M\tsecond.py\n"
    material["base"]["second.py"] = "VALUE = 1\n"
    material["fixed"]["second.py"] = "VALUE = 2\n"
    material["patch"] += patch("second.py", material["base"]["second.py"], material["fixed"]["second.py"])
    census = tool("census")
    monkeypatch.setattr(census, "sample", lambda *a: {str(n): str(n) for n in range(20)})
    row = census.inspect_bug(manifest, path, manifest["roles"]["acquisition"]["bugs"][0], runner=DockerFake(material))
    assert len(row["files"]) == 2
    assert row["distinct_mutant_count"] == 20
    assert not row["reaches_min_mutants"]
