"""External-input allocation checks and configuration-specific acquisition staging."""
from __future__ import annotations

import difflib
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from validation.distill.pilot import _material, funnel
from validation.distill.pilot.m1b import access_m1b, allocation_m1b as allocation, counts, inventory, material, run_m1b
from validation.distill.pilot.m1b._mine_io import digest


def inputs(configuration="K_0", *, prepared=None, interpreter=None):
    env = {"resolvable_modules": [], "loaded_packages": [], "pytest_dependencies": [],
           "interpreter_exists": prepared is not None}
    if prepared is not None:
        env["prepared_environment"] = {key: [] for key in
                                       ("resolvable_modules", "loaded_packages", "pytest_dependencies")}
    if interpreter is not None:
        env["prepared_interpreter"] = interpreter
    entries = [{"repository": f"org/repo{i:02}", "pr": 1, "parent": "b" * 40, "merge_commit": "a" * 40}
               for i in range(16)]
    completed = {}
    for i, entry in enumerate(entries):
        identity = entry["repository"] + "#1"
        summary = counts.summarize([])
        summary["counts"][configuration] = 1
        row = {"summary": summary, "record": {"repository": entry["repository"], "bug_id": identity,
                                              "failed_rules": [], "flags": []}}
        if prepared is not None:
            row["prepared_summary"] = counts.summarize([])
            row["prepared_summary"]["counts"]["K_3"] = prepared[i]
        completed[identity] = row
    state = inventory._report(completed, entries, set(), complete=True, exists=prepared is not None,
                              prepared=prepared is not None)
    state.update(schema=1, completed=completed, unread=[],
                 binding=digest({"entries": entries, "environment": env, "version": 1}))
    return state, {"entries": entries, "complete": True}, env


def test_external_check_rejects_resealed_commits_and_missing_inputs():
    state, pool, env = inputs()
    manifest = allocation.build_manifest(state, pool, env)
    assert allocation.check(manifest, state, pool, env) == []
    assert allocation.check(manifest)
    changed = deepcopy(manifest)
    victim = changed["roles"]["acquisition"]["bugs"][0]["repository"]
    for bug in [*changed["bugs"], *(b for g in [*changed["roles"].values(), changed["unused"]] for b in g["bugs"])]:
        if bug["repository"] == victim:
            bug["parent"] = "e" * 40
    changed["inputs_sha256"] = digest({key: changed[key] for key in
                                       ("binding", "configuration", "tier", "repositories", "bugs")})
    assert allocation.check(changed, state, pool, env)
    with pytest.raises(ValueError, match="invalid allocation"):
        access_m1b.selected(changed, inventory=state, pool=pool, environment=env)
    changed = deepcopy(manifest)
    changed["schema"] = True
    assert allocation.check(changed, state, pool, env)


@pytest.mark.parametrize("change", ["parent", "merge_commit", "environment", "selection"])
def test_draw_rejects_substituted_external_inputs(change):
    state, pool, env = inputs()
    if change in {"parent", "merge_commit"}:
        pool["entries"][0][change] = "f" * 40
    elif change == "environment":
        env["loaded_packages"] = ["subject"]
    else:
        state["selection"]["configuration"] = "K_1"
    with pytest.raises(ValueError):
        allocation.build_manifest(state, pool, env)


def test_k3_draw_uses_prepared_rows_and_requires_prepared_environment():
    state, pool, env = inputs("K_3", prepared=[0] + [1] * 15)
    manifest = allocation.build_manifest(state, pool, env)
    assert len(manifest["repositories"]) == 15
    assert "org/repo00" not in manifest["repositories"]
    assert manifest["tier"] == "full"
    del env["prepared_environment"]
    state["binding"] = digest({"entries": pool["entries"], "environment": env, "version": 1})
    with pytest.raises(ValueError):
        allocation.build_manifest(state, pool, env)


def test_cli_checks_and_writes_with_external_inputs(monkeypatch):
    state, pool, env = inputs()
    manifest = allocation.build_manifest(state, pool, env)
    data = {"inv.json": state, "pool.json": pool, "env.json": env, "manifest.json": manifest}
    monkeypatch.setattr(allocation, "read_json", lambda path: deepcopy(data[path.name]))
    written = []
    monkeypatch.setattr(allocation, "atomic_json", lambda path, value: written.append(value))
    assert allocation.main(["--check", "manifest.json", "inv.json", "pool.json", "env.json"]) == 0
    assert allocation.main(["--write", "inv.json", "pool.json", "env.json", "out.json"]) == 0
    assert written == [manifest]
    state["selection"]["tier"] = "reduced"
    with pytest.raises(SystemExit) as exc:
        allocation.main(["--write", "inv.json", "pool.json", "env.json", "out.json"])
    assert exc.value.code == 2 and len(written) == 1


def body_for(configuration):
    before = "def value(x):\n    return x\n"
    after = "def value(x):\n    return x + 1\n"
    old, new = {}, {}
    for name, number in (("a", 1), ("b", 2)) if configuration == "K_2" else (("a", 1),):
        path = f"tests/test_{name}.py"
        prefix = "from subject import value\n"
        if configuration == "K_1":
            prefix += "from tests.helper import expected\n"
        old[path] = prefix + f"def test_{name}():\n    assert value({number}) >= {number}\n"
        new[path] = prefix + f"def test_{name}():\n    assert value({number}) == {number + 1}\n"
    return {"base": {"subject.py": before, **old},
            "fixed": {"subject.py": after, **new, "tests/helper.py": "expected = 2\n"},
            "name_status": "M\tsubject.py\n" + "".join(f"M\t{path}\n" for path in new),
            "patch": "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                   fromfile="a/subject.py", tofile="b/subject.py"))}


@pytest.mark.parametrize("configuration", ["K_0", "K_1", "K_2", "K_3"])
def test_runner_stages_selected_cases_and_interpreter(monkeypatch, configuration):
    state, pool, env = inputs(configuration, prepared=[1] * 16 if configuration == "K_3" else None,
                              interpreter=sys.executable if configuration == "K_3" else None)
    manifest = allocation.build_manifest(state, pool, env)
    bug = manifest["roles"]["acquisition"]["bugs"][0]["repository"] + "#1"
    body = body_for(configuration)
    monkeypatch.setattr(material, "material_from_git", lambda *a, **kw: body)
    monkeypatch.setattr(funnel, "_output_path", lambda path: Path(path))
    monkeypatch.setattr(funnel, "_write", lambda *a: None)
    monkeypatch.setattr(funnel.confine, "backend", lambda *a: ("fixture", ""))
    calls = []

    def suite(case, implementation, tests, **kwargs):
        calls.append((case, kwargs["python"]))
        return "unknown"

    report = run_m1b.run(manifest, Path.cwd(), Path.cwd() / "unused.json", bugs=[bug],
                         inventory=state, pool=pool, environment=env, suite_runner=suite, clock=lambda: 0.0)
    assert len(calls) == (2 if configuration == "K_2" else 1)
    assert report["total_runs"] == len(calls)
    if configuration == "K_2":
        assert "test_a" in calls[0][0].after_tests and "test_b" in calls[1][0].after_tests
    if configuration == "K_1":
        assert calls[0][0].files["tests/helper.py"] == "expected = 2\n"
    if configuration == "K_3":
        assert all(python == sys.executable for _, python in calls)


def test_runner_refuses_k3_without_recorded_interpreter_before_io():
    state, pool, env = inputs("K_3", prepared=[1] * 16)
    manifest = allocation.build_manifest(state, pool, env)
    with pytest.raises(ValueError, match="^prepared_interpreter_required$"):
        run_m1b.run(manifest, None, None, inventory=state, pool=pool, environment=env)


def test_git_descriptor_rejects_quoted_names_before_blob_reads():
    class Objects:
        def fetch(self, sha):
            pass

        def tree(self, sha):
            return {"caf\u00e9.py": "blob"}

        def run(self, *args):
            if args[:2] == ("cat-file", "commit"):
                return ("parent " + "b" * 40 + "\n\nfixture").encode()
            if "--name-status" in args:
                return b'M\t"caf\\303\\251.py"\n'
            if "--name-only" in args:
                return "caf\u00e9.py\0".encode()
            pytest.fail("quoted descriptor must be parsed before reading blobs")

    with pytest.raises(_material.MaterialError, match="^extraction_failed$"):
        material._from_objects(Objects(), "b" * 40, "a" * 40)


def test_k3_cli_reports_missing_interpreter(monkeypatch, capsys):
    state, pool, env = inputs("K_3", prepared=[1] * 16)
    manifest = allocation.build_manifest(state, pool, env)
    data = {"manifest.json": manifest, "inv.json": state, "pool.json": pool, "env.json": env}
    monkeypatch.setattr(run_m1b, "read_json", lambda path: deepcopy(data[path.name]))
    with pytest.raises(SystemExit) as exc:
        run_m1b.main(["--manifest", "manifest.json", "--inventory", "inv.json", "--pool", "pool.json",
                      "--environment", "env.json", "--cache", "cache", "--output", "unused.json"])
    assert exc.value.code == 2
    assert capsys.readouterr().err.strip() == "prepared_interpreter_required"


def test_k2_modules_share_funnel_request_cap(monkeypatch):
    state, pool, env = inputs("K_2")
    manifest = allocation.build_manifest(state, pool, env)
    bug = manifest["roles"]["acquisition"]["bugs"][0]["repository"] + "#1"
    monkeypatch.setattr(material, "material_from_git", lambda *a, **kw: body_for("K_2"))
    monkeypatch.setattr(funnel, "_output_path", lambda path: Path(path))
    monkeypatch.setattr(funnel, "_write", lambda *a: None)
    monkeypatch.setattr(funnel.confine, "backend", lambda *a: ("fixture", ""))
    monkeypatch.setattr(funnel, "MAX_RUNS", 3)
    calls = []

    def suite(case, implementation, tests, **kwargs):
        calls.append(case)
        return "passed" if implementation == case.files[case.target] else "failed"

    report = run_m1b.run(manifest, Path.cwd(), Path.cwd() / "unused.json", bugs=[bug],
                         inventory=state, pool=pool, environment=env, suite_runner=suite, clock=lambda: 0.0)
    assert len(calls) == report["total_runs"] == 3
    assert {row["test_module"] for row in report["bugs"]} == {"tests/test_a.py", "tests/test_b.py"}
    assert all(row["reason"] == "unrun_budget" for row in report["bugs"])


def test_material_refuses_resealed_manifest_before_read(monkeypatch):
    state, pool, env = inputs()
    manifest = allocation.build_manifest(state, pool, env)
    bug = manifest["roles"]["acquisition"]["bugs"][0]["repository"] + "#1"
    manifest["configuration"] = "K_1"
    monkeypatch.setattr(material, "_read", lambda *a, **kw: pytest.fail("invalid allocation reached source IO"))
    with pytest.raises(ValueError, match="invalid allocation"):
        material.material_from_git(manifest, bug, None, inventory=state, pool=pool, environment=env)
