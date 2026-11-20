"""Synthetic acquisition adapter and isolated interpreter capture checks."""
from __future__ import annotations

import difflib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from validation.distill.pilot import allocation, funnel

BASE = "def value():\n    return 1  # BASE_SENTINEL\n"
FIXED = "def value():\n    return 2  # SOURCE_SENTINEL\n"
OLD = "from widget import value\ndef test_value():\n    assert value() >= 1\n"
NEW = "from widget import value\ndef test_value():\n    assert value() == 2  # TEST_SENTINEL\n"


def modules():
    return tuple(importlib.import_module("validation.distill.pilot.m1b." + name)
                 for name in ("a6", "environment", "fit"))


class ExtractionRunner:
    def __init__(self, material):
        self.material, self.calls, self.failure = material, [], None

    def __call__(self, command):
        self.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            key = "b" if "task" in command[3] else "c"
            return subprocess.CompletedProcess(command, 0, "sha256:" + key * 64, "")
        assert command[1] == "run" and "support" in command and "-I" in command
        assert command[command.index("--network") + 1] == "none"
        return subprocess.CompletedProcess(command, int(self.failure is not None),
                                           json.dumps(self.material), self.failure or "")


@pytest.fixture
def rig():
    folder = Path.cwd() / ".build" / ("a6-test-" + uuid4().hex)
    folder.mkdir()
    ids = [f"repo{i}-1" for i in range(18)]
    roles = allocation.allocate("a" * 64, [allocation.repository_of(i) for i in ids])
    manifest = {"cohort": "development", "cohort_digest": "a" * 64, "label": allocation.LABEL,
                "roles": {role: {"repositories": repos,
                                  "bugs": [b for b in ids if allocation.repository_of(b) in repos]}
                          for role, repos in roles.items()}}
    cohort = folder / "development.json"
    cohort.write_text(json.dumps({"cohort": "development", "digest": "a" * 64,
                                 "bugs": [{"id": b, "task_image": "task:" + b,
                                           "task_image_id": "sha256:" + "b" * 64,
                                           "oracle_image": "oracle:" + b,
                                           "oracle_image_id": "sha256:" + "c" * 64} for b in ids]}),
                      encoding="utf-8")
    material = {"name_status": "M\twidget.py\nM\ttests/test_value.py\n",
                "patch": "".join(difflib.unified_diff(BASE.splitlines(True), FIXED.splitlines(True),
                                                     fromfile="a/widget.py", tofile="b/widget.py")),
                "base": {"widget.py": BASE, "tests/test_value.py": OLD},
                "fixed": {"widget.py": FIXED, "tests/test_value.py": NEW,
                          "context.py": "VALUE = 3\n", "tests/helper.py": "VALUE = 4\n"}}
    bugs = manifest["roles"]["acquisition"]["bugs"]
    observations = [{"bug_id": bug, "arm": arm, "variant": "maintainer", "stage": "control",
                     "reason": "control_base_passes", "source": FIXED}
                    for bug in reversed(bugs) for arm in reversed(funnel.ARMS)]
    return SimpleNamespace(folder=folder, manifest=manifest, cohort=cohort, bugs=bugs, material=material,
                           runner=ExtractionRunner(material), observations={"bugs": observations})


def run(rig, **kwargs):
    a6, _, fit = modules()
    return a6.run_a6(rig.manifest, rig.cohort, rig.observations,
                     fit.Environment(frozenset({"pytest"}), frozenset(), frozenset()),
                     rig.folder / "output.json", runner=rig.runner,
                     repository_root=rig.folder / "repository", **kwargs)


def test_extract_assess_counts_and_join_by_bug_arm(rig, monkeypatch):
    a6, _, fit = modules()
    seen = []
    native = fit.assess

    def assess(candidate, environment):
        assert (candidate.parent_tree / "widget.py").read_text(encoding="utf-8") == BASE
        assert (candidate.parent_tree / "context.py").read_text(encoding="utf-8") == "VALUE = 3\n"
        assert not (candidate.parent_tree / "tests/test_value.py").exists()
        assert candidate.test_package_files["tests/helper.py"] == b"VALUE = 4\n"
        seen.append(candidate.parent_tree)
        return native(candidate, environment)

    monkeypatch.setattr(a6.fit, "assess", assess)
    rig.observations["bugs"][0].update(stage="shape", reason="multi_target_required")
    report = run(rig)
    assert len(seen) == len(rig.bugs) and all(not p.exists() for p in seen)
    assert report["counts"]["K_0"] == len(rig.bugs)
    assert report["selection"]["configuration"] == "K_0"
    assert {r["bug_id"] for r in report["records"]} == set(rig.bugs)
    for actual in report["agreement"]:
        expected = next(r for r in rig.observations["bugs"]
                        if (r["bug_id"], r["arm"]) == (actual["bug_id"], actual["arm"]))
        assert (actual["last_stage"], actual["reason"]) == (expected["stage"], expected["reason"])
        assert actual["failed_rules"] == []
    assert len(rig.runner.calls) == len(rig.bugs) * 3


@pytest.mark.parametrize("diagnostic,code", [
    ("ValueError: support source too large", "support_source_too_large"),
    ("source text must stay hidden", "extraction_failed"),
])
def test_extraction_failure_is_a_fit_row(rig, diagnostic, code):
    rig.runner.failure = diagnostic
    report = run(rig)
    assert len(report["records"]) == len(rig.bugs)
    assert all(row["failed_rules"] == [code] for row in report["records"])
    assert all(row["failed_rules"] == [code] for row in report["agreement"])
    assert all(value == 0 for value in report["counts"].values())
    assert report["extraction_reasons"] == {code: len(rig.bugs)}


def test_variants_are_not_collapsed(rig):
    extra = {**rig.observations["bugs"][0], "variant": "erase_assertion", "stage": "headline", "reason": "ok"}
    rig.observations["bugs"].append(extra)
    report = run(rig)
    assert len(report["agreement"]) == len(rig.bugs) * len(funnel.ARMS) + 1
    assert any(r["variant"] == "erase_assertion" and r["last_stage"] == "headline" for r in report["agreement"])


def test_no_source_or_diagnostics_in_json_or_table(rig, capsys):
    a6, _, _ = modules()
    rig.observations["bugs"][0]["reason"] = "raise RuntimeError('SOURCE_SENTINEL')"
    report = run(rig)
    a6.print_agreement(report)
    output = (rig.folder / "output.json").read_text(encoding="utf-8") + capsys.readouterr().out
    for secret in (BASE, FIXED, OLD, NEW, "SOURCE_SENTINEL", "TEST_SENTINEL", str(rig.folder), "raise RuntimeError"):
        assert secret not in output
    assert "unrecognized_reason" in output and "failed_codes" in output


@pytest.mark.parametrize("kind", ["admission", "missing", "duplicate", "unknown_arm", "unknown_stage"])
def test_bad_observations_refused_before_extraction(rig, kind):
    row = rig.observations["bugs"][0]
    if kind == "admission":
        row["bug_id"] = rig.manifest["roles"]["admission"]["bugs"][0]
    elif kind == "missing":
        rig.observations["bugs"].pop()
    elif kind == "duplicate":
        rig.observations["bugs"].append(dict(row))
    elif kind == "unknown_arm":
        row["arm"] = "unregistered"
    else:
        row["stage"] = FIXED
    with pytest.raises(ValueError):
        run(rig)
    assert not rig.runner.calls


def test_admission_bug_refused_before_cohort_read(rig, monkeypatch):
    a6, _, _ = modules()
    admission = rig.manifest["roles"]["admission"]["bugs"][0]
    monkeypatch.setattr(a6.access, "selected", lambda *args: [admission])
    rig.cohort.unlink()
    with pytest.raises(ValueError, match="role=admission"):
        run(rig)
    assert not rig.runner.calls


def test_final_cohort_refused_before_read(rig):
    rig.cohort = rig.folder / "final-unopened.json"
    with pytest.raises(ValueError, match="sealed"):
        run(rig)
    assert not rig.runner.calls


def test_output_inside_repository_refused(rig):
    a6, _, fit = modules()
    with pytest.raises(ValueError, match="output_inside_repository"):
        a6.run_a6(rig.manifest, rig.cohort, rig.observations,
                  fit.Environment(frozenset(), frozenset(), frozenset()), rig.folder / "out.json",
                  runner=rig.runner)
    assert not rig.runner.calls


@pytest.mark.parametrize("bad_path", ["../escape.py", "C:/escape.py", ".hidden/escape.py", "a\\escape.py"])
def test_unsafe_material_path_retained_as_failure(rig, bad_path):
    rig.material["fixed"][bad_path] = FIXED
    report = run(rig)
    assert all(r["failed_rules"] == ["invalid_material"] for r in report["records"])
    assert all(n == 0 for n in report["counts"].values())


def test_added_deleted_files_reconstruct_parent(rig, monkeypatch):
    a6, _, fit = modules()
    rig.material["name_status"] += "A\tnew.py\nD\tremoved.py\n"
    rig.material["fixed"]["new.py"] = "VALUE = 4\n"
    rig.material["base"]["removed.py"] = "VALUE = 5\n"
    native = fit.assess

    def assess(candidate, environment):
        assert not (candidate.parent_tree / "new.py").exists()
        assert (candidate.parent_tree / "removed.py").read_bytes() == b"VALUE = 5\n"
        assert candidate.changed_files["new.py"].before is None
        assert candidate.changed_files["removed.py"].after is None
        return native(candidate, environment)

    monkeypatch.setattr(a6.fit, "assess", assess)
    assert all("M1" in r["failed_rules"] for r in run(rig)["records"])


def test_environment_capture_uses_given_isolated_interpreter():
    _, environment, _ = modules()
    body = {"resolvable_modules": ["pytest", "os"], "loaded_packages": ["pytest", "_pytest"],
            "pytest_dependencies": ["pytest", "_pytest"]}
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(body), "")

    env = environment.capture("host-python", runner=runner)
    command, kwargs = calls[0]
    assert command[:3] == ["host-python", "-I", "-c"]
    assert kwargs["timeout"] == 120 and kwargs["check"] is False
    assert env.loaded_packages == frozenset({"pytest", "_pytest"})
    assert env.pytest_dependencies == env.loaded_packages


def test_environment_child_snapshot_and_no_candidate_import(rig):
    _, environment, _ = modules()
    marker = rig.folder / "candidate-was-imported"
    (rig.folder / "pytest.py").write_text(f"raise RuntimeError({str(marker)!r})\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-c", environment.PROBE], cwd=rig.folder,
                            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert "pytest" in body["loaded_packages"] and "_pytest" in body["loaded_packages"]
    assert {"sys", "os", "json", "socket", "pytest"} <= set(body["resolvable_modules"])
    assert body["pytest_dependencies"] == body["loaded_packages"]
    assert all("." not in name and name.isidentifier() for names in body.values() for name in names)
    # The loaded snapshot precedes inventory-only imports.
    snapshot = subprocess.run([sys.executable, "-I", "-c",
                               "import json, os, socket, sys; import pytest; "
                               "print(json.dumps(sorted({n.split('.')[0] for n in sys.modules})))"],
                              cwd=rig.folder, capture_output=True, text=True, encoding="utf-8",
                              timeout=120, check=False)
    assert body["loaded_packages"] == json.loads(snapshot.stdout)
    assert not marker.exists()


@pytest.mark.parametrize("body", ["not json", "{}", '{"resolvable_modules": "pytest"}'])
def test_invalid_environment_refused(body):
    _, environment, _ = modules()
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, body, "SOURCE_SENTINEL")
    with pytest.raises(ValueError, match="environment_capture_failed"):
        environment.capture("host-python", runner=runner)


def test_a6_cli_with_fakes(rig, capsys):
    a6, environment, _ = modules()
    paths = {name: rig.folder / (name + ".json") for name in ("manifest", "funnel", "environment", "output")}
    for name, body in (("manifest", rig.manifest), ("funnel", rig.observations),
                       ("environment", {key: ["pytest"] for key in environment.FIELDS})):
        paths[name].write_text(json.dumps(body), encoding="utf-8")
    argv = [item for name, path in paths.items() for item in ("--" + name, str(path))]
    argv += ["--cohort", str(rig.cohort)]
    assert a6.main(argv, runner=rig.runner, repository_root=rig.folder / "repository") == 0
    assert "failed_codes" in capsys.readouterr().out
    assert len(json.loads(paths["output"].read_text(encoding="utf-8"))["records"]) == len(rig.bugs)


def test_saved_reason_codes_survive_without_source_fields(rig):
    a6, _, _ = modules()
    rig.observations["bugs"][0]["reason"] = "mutant_unsettled"
    rows = a6._observations(rig.manifest, rig.bugs, rig.observations)
    assert any(r["reason"] == "mutant_unsettled" for r in rows)
    assert all(set(r) == {"bug_id", "arm", "variant", "last_stage", "reason"} for r in rows)


@pytest.mark.parametrize("name", ["aux/module.py", "pkg/con.py", "pkg/file:stream.py"])
def test_device_paths_are_refused(name):
    a6, _, _ = modules()
    with pytest.raises(ValueError, match="invalid_material"):
        a6._safe_path(name)


def test_environment_cli_json_and_generic_child_failure(capsys):
    _, environment, _ = modules()
    body = {key: ["pytest"] for key in environment.FIELDS}
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
    assert environment.main(["--python", "host-python"], runner=runner) == 0
    assert json.loads(capsys.readouterr().out) == body

    def failing(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "SOURCE_SENTINEL", "SOURCE_SENTINEL")
    with pytest.raises(SystemExit) as exc:
        environment.main([], runner=failing)
    assert exc.value.code == 2
    assert capsys.readouterr().err == "environment_capture_failed\n"
