"""Offline role allocation, acquisition access and git material checks."""
from __future__ import annotations

import hashlib
import difflib
import importlib
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from validation.distill.pilot.m1b import chooser, counts
from validation.distill.pilot.m1b._mine_io import digest


def module(name):
    return importlib.import_module("validation.distill.pilot.m1b." + name)


def inputs(size=16, configuration="K_0"):
    entries, completed = [], {}
    environment = {"resolvable_modules": [], "loaded_packages": [], "pytest_dependencies": [],
                   "interpreter_exists": False}
    for number in range(size):
        entry = {"repository": f"org/repo{number:02}", "pr": 1,
                 "merge_commit": "a" * 40, "parent": "b" * 40}
        entries.append(entry)
        completed[f"{entry['repository']}#1"] = {
            "summary": {"counts": {key: int(key == configuration) for key in counts.CONFIGURATIONS}}}
    # A public record alone is never enough, and an ineligible PR in a counted repo stays out.
    for repo, pr in (("org/rejected", 1), ("org/repo00", 2)):
        entry = {"repository": repo, "pr": pr, "merge_commit": "c" * 40, "parent": "b" * 40}
        entries.append(entry)
        completed[f"{repo}#{pr}"] = {"summary": {"counts": dict.fromkeys(counts.CONFIGURATIONS, 0)}}
    inventory = {"complete": True, "completed": completed,
                 "records": [{"repository": "org/rejected", "failed_rules": []}],
                 "counts": {key: size if key == configuration else 0 for key in counts.CONFIGURATIONS}}
    pool = {"entries": entries, "complete": True}
    refresh(inventory, pool, environment)
    return inventory, pool, environment


def refresh(inventory, pool, environment):
    inventory["binding"] = digest({"entries": pool["entries"], "environment": environment, "version": 1})
    inventory["selection"] = chooser.choose(inventory["counts"],
        interpreter_exists=environment.get("interpreter_exists") is True,
        prepared_count=inventory.get("prepared_counts", {}).get("K_3")).public()


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_in_memory_draw_and_access():
    inv, pool, env = inputs(16)
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    assert [len(m["roles"][r]["repositories"]) for r in ("acquisition", "admission", "monitor", "reuse")] == [5, 5, 3, 2]
    assert len(m["unused"]["repositories"]) == 1
    assert module("allocation_m1b").check(m, inv, pool, env) == []
    access = module("access_m1b")
    assert len(access.selected(m, inventory=inv, pool=pool, environment=env)) == 5
    bug = m["roles"]["admission"]["bugs"][0]
    with pytest.raises(ValueError, match="role=admission"):
        access.acquisition_bug(m, f"{bug['repository']}#{bug['pr']}", inventory=inv, pool=pool, environment=env)


@pytest.mark.parametrize("size", [10, 14, 15, 17])
def test_in_memory_draw_order_and_edits(size):
    inv, pool, env = inputs(size)
    allocation = module("allocation_m1b")
    m = allocation.build_manifest(inv, pool, env)
    ordered = sorted((f"org/repo{i:02}" for i in range(size)), key=lambda repo: hashlib.sha256(
        f"g6-pilot-m1b-allocation-v1|{inv['binding']}|{repo}".encode()).hexdigest())
    sizes = (5, 5, 3, 2) if size >= 15 else (4, 4, 2)
    cursor = 0
    for role, count in zip(("acquisition", "admission", "monitor", "reuse"), sizes):
        assert m["roles"][role]["repositories"] == sorted(ordered[cursor:cursor + count])
        cursor += count
    assert m["unused"]["repositories"] == sorted(ordered[cursor:])
    assert allocation.build_manifest(inv, pool, env) == m
    for key, value in (("binding", "e" * 64), ("tier", "reduced" if size >= 15 else "full"),
                       ("configuration", "K_1"), ("label", "changed")):
        edited = deepcopy(m)
        edited[key] = value
        assert allocation.check(edited, inv, pool, env)
    edited = deepcopy(m)
    edited["roles"]["admission"]["repositories"][0] = m["roles"]["monitor"]["repositories"][0]
    assert allocation.check(edited, inv, pool, env)


@pytest.mark.parametrize("change", ["stop", "incomplete", "selection", "count", "missing", "configuration", "prepared"])
def test_in_memory_inventory_refusals(change):
    inv, pool, env = inputs(9 if change == "stop" else 16)
    if change == "incomplete":
        inv["complete"] = False
    elif change == "selection":
        inv["selection"] = None
    elif change == "count":
        inv["completed"]["org/repo00#1"]["summary"]["counts"]["K_0"] = 0
    elif change == "missing":
        del inv["completed"]["org/repo00#1"]
    elif change == "configuration":
        inv["selection"]["configuration"] = "K_123"
    elif change == "prepared":
        inv, pool, env = inputs(16, "K_3")
    with pytest.raises(ValueError):
        module("allocation_m1b").build_manifest(inv, pool, env)


def test_in_memory_access_refuses_every_other_role():
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    access = module("access_m1b")
    for role, group in [*m["roles"].items(), ("unused", m["unused"])]:
        b = group["bugs"][0]
        identity = f"{b['repository']}#{b['pr']}"
        if role == "acquisition":
            assert access.acquisition_bug(m, identity, inventory=inv, pool=pool, environment=env) == b
        else:
            with pytest.raises(ValueError, match="role=" + role):
                access.selected(m, [identity], inventory=inv, pool=pool, environment=env)
    with pytest.raises(ValueError):
        access.selected(m, ["org/unknown#1"], inventory=inv, pool=pool, environment=env)
    m["binding"] = "e" * 64
    with pytest.raises(ValueError, match="invalid allocation"):
        access.selected(m, inventory=inv, pool=pool, environment=env)


@pytest.mark.parametrize("configuration", ["K_0", "K_1", "K_2", "K_3"])
def test_in_memory_counts_bugs_by_selected_configuration(configuration):
    inv, pool, env = inputs(16, configuration)
    extra = {"repository": "org/repo00", "pr": 3, "merge_commit": "c" * 40, "parent": "b" * 40}
    pool["entries"].append(extra)
    inv["completed"]["org/repo00#3"] = {
        "summary": {"counts": {key: int(key == configuration) for key in counts.CONFIGURATIONS}}}
    if configuration == "K_3":
        inv["prepared_counts"] = {"K_3": 16}
        for row in inv["completed"].values():
            row["prepared_summary"] = deepcopy(row["summary"])
        env.update(interpreter_exists=True, prepared_environment={key: [] for key in
                   ("resolvable_modules", "loaded_packages", "pytest_dependencies")})
    refresh(inv, pool, env)
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    assert len(m["repositories"]) == 16
    assert len(m["bugs"]) == 17
    assert {b["pr"] for b in m["bugs"] if b["repository"] == "org/repo00"} == {1, 3}
    assert "org/rejected" not in m["repositories"]


def test_in_memory_excluded_repository_stays_out():
    inv, pool, env = inputs(16)
    inv["a2_exclusions"] = ["org/repo00"]
    inv["completed"] = {key: value for key, value in inv["completed"].items() if not key.startswith("org/repo00#")}
    inv["counts"]["K_0"] = 15
    refresh(inv, pool, env)
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    assert len(m["repositories"]) == 15
    assert "org/repo00" not in m["repositories"]


def test_material_in_memory_access_before_io():
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    bug = m["roles"]["admission"]["bugs"][0]
    with pytest.raises(ValueError, match="role=admission"):
        module("material").material_from_git(m, f"{bug['repository']}#{bug['pr']}", None,
                                             inventory=inv, pool=pool, environment=env)


def test_runner_refuses_non_acquisition_before_io():
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    b = m["roles"]["monitor"]["bugs"][0]
    with pytest.raises(ValueError, match="role=monitor"):
        module("run_m1b").run(m, None, None, bugs=[f"{b['repository']}#{b['pr']}"],
                              inventory=inv, pool=pool, environment=env)


@pytest.mark.parametrize("data,support,reason", [(b"x" * 131073, False, "fix_source_missing_or_large"),
                                                (b"x" * 131073, True, "support_missing_or_large"),
                                                (b"\xff", False, "source_not_utf8")])
def test_material_source_limits(tmp_path, data, support, reason):
    from validation.distill.pilot._material import MaterialError

    path = tmp_path / "subject.py"
    path.write_bytes(data)
    with pytest.raises(MaterialError, match="^" + reason + "$"):
        module("material")._text(path, support=support)


@pytest.mark.parametrize("change,reason", [("valid", None), ("parent", "extraction_failed"),
                                          ("support_size", "support_missing_or_large"),
                                          ("support_total", "support_source_too_large")])
def test_material_tree_contract(tmp_path, change, reason):
    from validation.distill.pilot import _material
    blobs = {"old": b"VALUE = 1\n", "new": b"VALUE = 2\n", "deleted": b"REMOVED = 1\n",
             "added": b"ADDED = 1\n", "context": b"CONTEXT = 1\n", "hidden": b"x" * 131073}
    before = {"subject.py": "old", "deleted.py": "deleted"}
    after = {"subject.py": "new", "added.py": "added", "context.py": "context", ".hidden/skip.py": "hidden"}
    if change == "support_size":
        blobs["context"] = b"x" * 131073
    elif change == "support_total":
        blobs["context"] = b"x" * 131072
        after.update({f"context{i}.py": "context" for i in range(8)})

    class Objects:
        def fetch(self, sha):
            assert sha in {"a" * 40, "b" * 40}

        def tree(self, sha):
            return {"a" * 40: after, "b" * 40: before}[sha]

        def blob(self, oid):
            return blobs[oid]

        def run(self, *args):
            if args[:2] == ("cat-file", "commit"):
                return ("parent " + ("c" if change == "parent" else "b") * 40 + "\n\nmessage").encode()
            assert args[-3:] == ("b" * 40, "a" * 40, "--")
            if "--name-status" in args:
                return b"A\tadded.py\nD\tdeleted.py\nM\tsubject.py\n"
            return b"--- a/subject.py\n+++ b/subject.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"

    if change == "parent":
        with pytest.raises(_material.MaterialError, match="^" + reason + "$"):
            module("material")._from_objects(Objects(), "b" * 40, "a" * 40)
        return
    base_root, fixed_root = tmp_path / "base", tmp_path / "fixed"
    for root, tree in ((base_root, before), (fixed_root, after)):
        for name, oid in tree.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blobs[oid])

    def extract():
        return module("material")._from_checkouts(Objects(), "b" * 40, "a" * 40,
                                                 before, after, base_root, fixed_root)

    if reason:
        with pytest.raises(_material.MaterialError, match="^" + reason + "$"):
            extract()
    else:
        body = extract()
        assert set(body) == {"base", "fixed", "patch", "name_status"}
        assert body["base"] == {"subject.py": "VALUE = 1\n", "deleted.py": "REMOVED = 1\n"}
        assert body["fixed"] == {"subject.py": "VALUE = 2\n", "added.py": "ADDED = 1\n", "context.py": "CONTEXT = 1\n"}
        assert body["name_status"] == "A\tadded.py\nD\tdeleted.py\nM\tsubject.py\n"
        assert _material.changed_lines(body, "subject.py") == (1,)


def test_check_refuses_schema_type_edit():
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    m["schema"] = True
    assert module("allocation_m1b").check(m, inv, pool, env)


def test_material_sanitizes_read_errors(monkeypatch):
    from validation.distill.pilot._material import MaterialError
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    bug = module("access_m1b").selected(m, inventory=inv, pool=pool, environment=env)[0]

    def fail(*args, **kwargs):
        raise ValueError("SOURCE_SENTINEL")

    monkeypatch.setattr(module("material"), "_read", fail)
    with pytest.raises(MaterialError, match="^extraction_failed$"):
        module("material").material_from_git(m, bug, None, inventory=inv, pool=pool, environment=env)


def test_runner_reuses_funnel_stages_and_tags_kinds(monkeypatch):
    from validation.distill.pilot import funnel
    from g6.workforce import mutation_check
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    bug = module("access_m1b").selected(m, inventory=inv, pool=pool, environment=env)[0]
    base = "def value(x):\n    return x\n"
    fixed = "def value(x):\n    return x + 1\n"
    old = "from subject import value\ndef test_value():\n    assert value(1) >= 1\n"
    new = "from subject import value\ndef test_value():\n    assert value(1) == 2\n    assert value(2) == 3\n"
    body = {"base": {"subject.py": base, "tests/test_value.py": old},
            "fixed": {"subject.py": fixed, "tests/test_value.py": new},
            "name_status": "M\tsubject.py\nM\ttests/test_value.py\n",
            "patch": "".join(difflib.unified_diff(base.splitlines(True), fixed.splitlines(True),
                                                  fromfile="a/subject.py", tofile="b/subject.py"))}
    reads, suites, reports = [], [], []

    def read(manifest, identity, cache, **kwargs):
        reads.append(identity)
        return body

    def suite(case, implementation, tests, **kwargs):
        suites.append((implementation, tests))
        if implementation == fixed:
            return "passed"
        if implementation == base and tests == old:
            return "passed"
        return "failed"

    monkeypatch.setattr(module("material"), "material_from_git", read)
    monkeypatch.setattr(funnel, "_output_path", lambda output: Path(output))
    monkeypatch.setattr(funnel, "_write", lambda output, report: reports.append(report))
    monkeypatch.setattr(funnel.confine, "backend", lambda *a: ("fixture", ""))
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [
        mutation_check.Mutant(2, "x", "9", "def value(x):\n    return 9\n")])
    report = module("run_m1b").run(m, Path.cwd(), Path.cwd() / "synthetic-unused-output.json",
                                    bugs=[bug], inventory=inv, pool=pool, environment=env,
                                    suite_runner=suite, clock=lambda: 0.0)
    assert reads == [bug] and reports == [report]
    assert suites and report["total_runs"] == len(suites)
    assert set(report["arms"]) == {"m1b"}
    rows = report["bugs"]
    assert all(row["repository"] == bug.split("#")[0] and row["arm"] == "m1b" for row in rows)
    assert any(row["kind"] == "indexed_erasure" and row["stage"] == "headline" for row in rows)
    assert any(row["kind"] == "reversed_maintainer" for row in rows)
    assert any(row["kind"].startswith("operator:") for row in rows)
    assert {"isolation", "fixed_suites", "control", "mutant_count", "settle", "label", "headline"} <= {
        stage for row in rows for stage in row["stages"]}


def manifest(tmp_path, size=16, configuration="K_0"):
    inventory, pool, environment = inputs(size, configuration)
    inv_path = write_json(tmp_path / "synthetic-inventory.json", inventory)
    pool_path = write_json(tmp_path / "synthetic-pool.json", pool)
    env_path = write_json(tmp_path / "synthetic-environment.json", environment)
    output = tmp_path / "synthetic-manifest.json"
    assert module("allocation_m1b").main(["--write", str(inv_path), str(pool_path), str(env_path), str(output)]) == 0
    return json.loads(output.read_text(encoding="utf-8")), inventory, pool, environment


@pytest.mark.parametrize("size,sizes", [(10, (4, 4, 2)), (14, (4, 4, 2)), (15, (5, 5, 3, 2)),
                                       (17, (5, 5, 3, 2))])
def test_cli_draw_matches_independent_order(tmp_path, size, sizes):
    m, inv, pool, env = manifest(tmp_path, size)
    expected = sorted((f"org/repo{i:02}" for i in range(size)), key=lambda repo: hashlib.sha256(
        f"g6-pilot-m1b-allocation-v1|{inv['binding']}|{repo}".encode()).hexdigest())
    roles = ("acquisition", "admission", "monitor", "reuse")[:len(sizes)]
    assert list(m["roles"]) == list(roles)
    cursor = 0
    for role, count in zip(roles, sizes, strict=True):
        group = m["roles"][role]
        assert group["repositories"] == sorted(expected[cursor:cursor + count])
        assert len(group["bugs"]) == count
        assert all(b["pr"] == 1 and b["merge_commit"] == "a" * 40 and b["parent"] == "b" * 40
                   for b in group["bugs"])
        cursor += count
    assert m["unused"]["repositories"] == sorted(expected[cursor:])
    assert not set(m["roles"]["admission"]["repositories"]) & set(m["roles"]["monitor"]["repositories"])
    assert module("allocation_m1b").check(m, inv, pool, env) == []
    assert manifest(tmp_path, size)[0] == m
    assert module("allocation_m1b").main(["--check", str(tmp_path / "synthetic-manifest.json"),
        str(tmp_path / "synthetic-inventory.json"), str(tmp_path / "synthetic-pool.json"),
        str(tmp_path / "synthetic-environment.json")]) == 0


@pytest.mark.parametrize("change", ["stop", "incomplete", "selection", "count", "missing", "unknown"])
def test_allocation_refuses_bad_inventory(tmp_path, change):
    inv, pool, env = inputs()
    if change == "stop":
        inv, pool, env = inputs(9)
    elif change == "incomplete":
        inv["complete"] = False
    elif change == "selection":
        inv["selection"] = None
    elif change == "count":
        inv["completed"]["org/repo00#1"]["summary"]["counts"]["K_0"] = 0
    elif change == "missing":
        del inv["completed"]["org/repo00#1"]
    else:
        inv["selection"]["configuration"] = "K_123"
    with pytest.raises(SystemExit) as exc:
        module("allocation_m1b").main(["--write", str(write_json(tmp_path / "inv.json", inv)),
                                       str(write_json(tmp_path / "pool.json", pool)),
                                       str(write_json(tmp_path / "env.json", env)), str(tmp_path / "out.json")])
    assert exc.value.code == 2
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("change", ["move", "binding", "tier", "commit", "unused", "label", "configuration"])
def test_check_refuses_hand_edits(tmp_path, change):
    m, inv, pool, env = manifest(tmp_path)
    if change == "move":
        a, b = m["roles"]["acquisition"], m["roles"]["admission"]
        a["repositories"][0], b["repositories"][0] = b["repositories"][0], a["repositories"][0]
    elif change == "commit":
        m["roles"]["acquisition"]["bugs"][0]["parent"] = "e" * 40
    elif change == "unused":
        m["unused"]["repositories"] = []
    else:
        m[change] = {"binding": "e" * 64, "tier": "reduced", "label": "other", "configuration": "K_1"}[change]
    assert module("allocation_m1b").check(m, inv, pool, env)
    assert module("allocation_m1b").main(["--check", str(write_json(tmp_path / "edited.json", m)),
        str(tmp_path / "synthetic-inventory.json"), str(tmp_path / "synthetic-pool.json"),
        str(tmp_path / "synthetic-environment.json")]) == 1


def test_access_only_returns_acquisition(tmp_path):
    m, inv, pool, env = manifest(tmp_path)
    access = module("access_m1b")
    ids = access.selected(m, inventory=inv, pool=pool, environment=env)
    assert ids == [f"{b['repository']}#{b['pr']}" for b in m["roles"]["acquisition"]["bugs"]]
    assert access.acquisition_bug(m, ids[0], inventory=inv, pool=pool, environment=env) == m["roles"]["acquisition"]["bugs"][0]
    for role in ("admission", "monitor", "reuse", "unused"):
        group = m["unused"] if role == "unused" else m["roles"][role]
        bug = group["bugs"][0]
        with pytest.raises(ValueError, match="role=" + role):
            access.acquisition_bug(m, f"{bug['repository']}#{bug['pr']}", inventory=inv, pool=pool, environment=env)
    for bug in ("org/missing#1", ids[0].split("#")[0] + "#999"):
        with pytest.raises(ValueError):
            access.selected(m, [bug], inventory=inv, pool=pool, environment=env)
    damaged = deepcopy(m)
    damaged["binding"] = "bad"
    with pytest.raises(ValueError, match="invalid allocation"):
        access.selected(damaged, inventory=inv, pool=pool, environment=env)


def git(repo, *args):
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                          capture_output=True).stdout.decode().strip()


@pytest.fixture
def local_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "--template=", "--initial-branch=main")
    for name, text in {"subject.py": "def value(x):\n    return x\n", "context.py": "VALUE = 3\n",
                       "tests/test_value.py": "from subject import value\ndef test_value():\n    assert value(1) >= 1\n",
                       "danger.py": "raise RuntimeError('never execute')\n"}.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    parent = git(repo, "rev-parse", "HEAD")
    (repo / "subject.py").write_bytes(b"def value(x):\n    return x + 1\n")
    (repo / "tests/test_value.py").write_bytes(
        b"from subject import value\ndef test_value():\n    assert value(1) == 2\n    assert value(2) == 3\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix")
    return repo, parent, git(repo, "rev-parse", "HEAD")


def git_manifest(tmp_path, local_repo):
    repo, parent, merge = local_repo
    inv, pool, env = inputs()
    for row in pool["entries"]:
        row.update(parent=parent, merge_commit=merge)
    refresh(inv, pool, env)
    inv_path = write_json(tmp_path / "inv.json", inv)
    pool_path = write_json(tmp_path / "pool.json", pool)
    env_path = write_json(tmp_path / "env.json", env)
    m = module("allocation_m1b").manifest_from_inventory(inv_path, pool_path, env_path)
    bug = module("access_m1b").selected(m, inventory=inv, pool=pool, environment=env)[0]
    return m, bug, repo, inv, pool, env


def test_material_reads_parent_and_fixed_tree(tmp_path, local_repo):
    m, bug, repo, inv, pool, env = git_manifest(tmp_path, local_repo)
    body = module("material").material_from_git(m, bug, tmp_path / "cache", source=repo,
                                                 repository_root=tmp_path / "worktree",
                                                 inventory=inv, pool=pool, environment=env)
    assert set(body) == {"base", "fixed", "patch", "name_status"}
    assert body["base"]["subject.py"] == "def value(x):\n    return x\n"
    assert body["fixed"]["subject.py"] == "def value(x):\n    return x + 1\n"
    assert body["fixed"]["context.py"] == "VALUE = 3\n"
    assert "never execute" in body["fixed"]["danger.py"]
    from validation.distill.pilot import _material
    assert _material.changed_files(body) == ["subject.py", "tests/test_value.py"]
    assert _material.changed_lines(body, "subject.py") == (2,)


def test_material_refuses_admission_before_io(tmp_path):
    m, inv, pool, env = manifest(tmp_path)
    b = m["roles"]["admission"]["bugs"][0]
    cache = tmp_path / "absent-cache"
    with pytest.raises(ValueError, match="role=admission"):
        module("material").material_from_git(m, f"{b['repository']}#{b['pr']}", cache,
                                               source=tmp_path / "absent-source",
                                               inventory=inv, pool=pool, environment=env)
    assert not cache.exists()


def test_runner_continues_past_operator_output_that_does_not_compile(monkeypatch):
    from validation.distill.pilot import funnel
    from g6.workforce import mutation_check
    inv, pool, env = inputs()
    m = module("allocation_m1b").build_manifest(inv, pool, env)
    first, second = module("access_m1b").selected(m, inventory=inv, pool=pool, environment=env)[:2]
    base = "def value(x):\n    return x\n"
    fixed = "def value(x):\n    return x + 1\n"
    old = "from subject import value\ndef test_value():\n    assert value(1) >= 1\n"
    new = "from subject import value\ndef test_value():\n    assert value(1) == 2\n    assert value(2) == 3\n"
    patch = "".join(difflib.unified_diff(base.splitlines(True), fixed.splitlines(True),
                                         fromfile="a/subject.py", tofile="b/subject.py"))

    def body(after_old, after_new):
        return {"base": {"subject.py": base, "tests/test_value.py": after_old},
                "fixed": {"subject.py": fixed, "tests/test_value.py": after_new},
                "name_status": "M\tsubject.py\nM\ttests/test_value.py\n", "patch": patch}

    future = "from __future__ import annotations\n"
    bodies = {first: body(future + old, future + new), second: body(old, new)}

    def suite(case, implementation, tests, **kwargs):
        if implementation == fixed:
            return "passed"
        if implementation == base and tests.endswith(old):
            return "passed"
        return "failed"

    reports = []
    monkeypatch.setattr(module("material"), "material_from_git", lambda manifest, identity, cache, **kw: bodies[identity])
    monkeypatch.setattr(funnel, "_output_path", lambda output: Path(output))
    monkeypatch.setattr(funnel, "_write", lambda output, report: reports.append(report))
    monkeypatch.setattr(funnel.confine, "backend", lambda *a: ("fixture", ""))
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [
        mutation_check.Mutant(2, "x", "9", "def value(x):\n    return 9\n")])
    report = module("run_m1b").run(m, Path.cwd(), Path.cwd() / "synthetic-unused-output.json",
                                    bugs=[first, second], inventory=inv, pool=pool, environment=env,
                                    suite_runner=suite, clock=lambda: 0.0)
    assert reports == [report]
    rows = report["bugs"]
    assert {row["bug_id"] for row in rows} == {first, second}
    refused = [row for row in rows if row["bug_id"] == first
               and row["kind"] in ("operator:extract_helper", "operator:named_constant")]
    assert len(refused) == 2
    assert all(row["reason"] == "operator_output_invalid" and row["label"] is None and row["runs"] == 0
               for row in refused)
    assert any(row["bug_id"] == first and row["kind"] == "indexed_erasure" and row["stage"] == "headline"
               for row in rows)
    assert any(row["bug_id"] == second and row["stage"] == "headline" for row in rows)