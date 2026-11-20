"""Offline mining and static git-reader acceptance checks."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest


def module(name):
    return importlib.import_module(f"validation.distill.pilot.m1b.{name}")


@pytest.mark.parametrize("name", ["pool", "gitread", "inventory", "a2_check"])
def test_miner_modules_exist(name):
    assert module(name) is not None


@pytest.mark.parametrize("name", ["../escape.py", "C:/escape.py", "tests/../escape.py", "a\\b.py",
                                  "a//b.py", ".git/config", "NUL.py", "folder /x.py", ".git~1/config", "COM¹.py", "."])
def test_git_names_fail_closed(name):
    with pytest.raises(ValueError):
        module("gitread")._safe_name(name)


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.meta = {}
        self.languages = {}
        self.trees = {}
        self.prs = {}
        self.files = {}
        self.commits = {}

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] in {"api", "pr"}
        if args[0] == "pr":
            assert args[1] == "list"
            assert "body" not in args[-1] and "title" not in args[-1]
            return json.dumps(self.prs.get(args[3], []))
        endpoint = args[1]
        # Repository IDs have two path components.
        owner, repo, *tail = endpoint.removeprefix("repos/").split("/")
        name, rest = f"{owner}/{repo}", "/".join(tail)
        if not rest:
            return json.dumps(self.meta.get(name, {"full_name": name, "private": False,
                                                   "default_branch": "main", "license": {"spdx_id": "MIT"}}))
        if rest == "languages":
            return json.dumps(self.languages.get(name, {"Python": 100}))
        if rest.startswith("git/trees/"):
            return json.dumps(self.trees.get(name, {"truncated": False, "tree": [{"path": "widget.py"}]}))
        if rest.startswith("pulls/"):
            number = int(rest.split("/")[1])
            page = int(rest.rsplit("page=", 1)[1])
            return json.dumps(self.files.get((name, number, page),
                                            [{"filename": "widget.py"}, {"filename": "tests/test_value.py"}]
                                            if page == 1 else []))
        if rest.startswith("commits/"):
            sha = rest.split("/")[1].split("?")[0]
            return json.dumps(self.commits.get(sha, {"parents": [{"sha": "b" * 40}],
                              "files": [{"filename": "widget.py"}, {"filename": "tests/test_value.py"}]}))
        raise AssertionError(endpoint)


def pr(number=1, **kwargs):
    return {"number": number, "mergedAt": "2026-08-01T00:00:00Z", "baseRefName": "main",
            "mergeCommit": {"oid": f"{number:040x}"}, "changedFiles": 2, **kwargs}


def mine(tmp_path, gh, top, **kwargs):
    return module("pool").mine(
        output=tmp_path / "pool.json", checkpoint=tmp_path / "checkpoint.json",
        development=write_json(tmp_path / "development.json", kwargs.pop("development", [])),
        selection_cohort=write_json(tmp_path / "selection.json", kwargs.pop("selection", [])),
        gh=gh, ranking=lambda: top, project=lambda package: {"info": {"project_urls": {
            "Source": f"https://github.com/org/{package}"}}},
        repository_root=tmp_path / "worktree", **kwargs)


def test_pool_keeps_rank_order_and_deduplicates(tmp_path):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr()]
    result = mine(tmp_path, gh, ["a", "a", "b"])
    assert [r["repository"] for r in result["repositories"]] == ["org/a", "org/b"]
    entry = result["entries"][0]
    assert entry == {"repository": "org/a", "pr": 1, "merge_commit": "0" * 39 + "1",
                     "parent": "b" * 40, "rules": ["T1", "T3", "T4"]}
    assert result["complete"]
    assert "body" not in (tmp_path / "pool.json").read_text()


@pytest.mark.parametrize("reason", ["PUBLIC", "P2", "P4", "P3", "DEVELOPMENT", "SELECTION", "TREE"])
def test_pool_drop_codes(tmp_path, reason):
    gh = FakeGitHub()
    name = "org/a"
    gh.meta[name] = {"full_name": name, "private": reason == "PUBLIC", "default_branch": "main",
                     "license": {"spdx_id": "GPL-3.0" if reason == "P2" else "MIT"}}
    if reason == "P4":
        gh.meta[name]["full_name"] = "PSF/Requests"
    if reason == "P3":
        gh.languages[name] = {"Python": 10, "C": 1}
    if reason == "TREE":
        gh.trees[name] = {"truncated": True, "tree": []}
    result = mine(tmp_path, gh, ["a"], development=["ORG/A"] if reason == "DEVELOPMENT" else [],
                  selection={"bugs": [{"repository": "org/a"}]} if reason == "SELECTION" else [])
    assert result["repositories"] == []
    assert result["decisions"][0]["codes"] == [reason]


def test_pool_tree_other_source_and_ambiguous_project(tmp_path):
    gh = FakeGitHub()
    gh.trees["org/a"] = {"truncated": False, "tree": [{"path": "native.rs"}]}
    assert mine(tmp_path, gh, ["a"])["decisions"][0]["codes"] == ["P3"]
    assert module("pool").repository_url({"info": {"project_urls": {
        "Source": "https://github.com/org/a", "Other": "https://github.com/elsewhere/b"}}}) is None


@pytest.mark.parametrize("url,expected", [("https://github.com/Org/Widget.git", "org/widget"),
                                        ("https://github.com/org/widget/issues", None),
                                        ("https://github.com@elsewhere.invalid/org/widget", None),
                                        ("http://github.com/org/widget", None)])
def test_pypi_repository_url(url, expected):
    assert module("pool").repository_url({"info": {"project_urls": {"Source": url}}}) == expected


@pytest.mark.parametrize("name", ["org/_widget", "org/.widget", "org/-widget"])
def test_repository_ids_without_files(name):
    assert module("_mine_io").repository_id(name) == name


@pytest.mark.parametrize("reason", ["KEEP", "PUBLIC", "P2", "P4", "P3", "DEVELOPMENT", "SELECTION", "TREE"])
def test_repository_screen_without_files(reason):
    gh = FakeGitHub()
    gh.meta["org/a"] = {"full_name": "psf/requests" if reason == "P4" else "org/a",
                        "private": reason == "PUBLIC", "default_branch": "main",
                        "license": {"spdx_id": "GPL-3.0" if reason == "P2" else "MIT"}}
    if reason == "P3":
        gh.languages["org/a"] = {"C": 5, "Python": 5}
    if reason == "TREE":
        gh.trees["org/a"] = {"truncated": True, "tree": []}
    name, branch, codes = module("pool")._repository("org/a", gh,
        {"org/a"} if reason == "DEVELOPMENT" else set(), {"org/a"} if reason == "SELECTION" else set())
    assert codes == ([] if reason == "KEEP" else [reason])
    assert bool(branch) == (reason == "KEEP")


@pytest.mark.parametrize("date,kept", [("2026-05-31T23:59:59Z", False), ("2026-06-01T00:00:00Z", True),
                                      ("2026-10-29T23:59:59Z", True), ("2026-10-30T00:00:00Z", False)])
def test_pr_window_without_files(date, kept):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr(mergedAt=date)]
    entries, decisions = module("pool")._pull_requests("org/a", "main", gh)
    assert bool(entries) == kept
    assert decisions[0]["codes"] == ([] if kept else ["T1"])


@pytest.mark.parametrize("parents,files,code", [(2, True, None), (1, True, None), (0, True, "T4"),
                                               (3, True, "T4"), (1, False, "T3")])
def test_pr_rules_without_files(parents, files, code):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr()]
    gh.commits["0" * 39 + "1"] = {"parents": [{"sha": "b" * 40}] * parents,
        "files": [{"filename": "widget.py"}, {"filename": "tests/test_value.py"}]}
    if not files:
        gh.files["org/a", 1, 1] = [{"filename": "widget.py"}, {"filename": "native.c"}]
    entries, decisions = module("pool")._pull_requests("org/a", "main", gh)
    assert bool(entries) == (code is None)
    assert decisions[0]["codes"] == ([] if code is None else [code])


def test_pr_pagination_and_truncation_without_files():
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr(changedFiles=102)]
    files = [{"filename": f"data/f{i}.txt"} for i in range(100)]
    files += [{"filename": "widget.py"}, {"filename": "tests/test_value.py"}]
    gh.files["org/a", 1, 1], gh.files["org/a", 1, 2] = files[:100], files[100:]
    # Two-parent merges select the first parent without depending on commit-file pagination.
    gh.commits["0" * 39 + "1"] = {"parents": [{"sha": "b" * 40}, {"sha": "c" * 40}]}
    assert len(module("pool")._pull_requests("org/a", "main", gh)[0]) == 1
    gh.prs["org/a"][0]["changedFiles"] = 103
    with pytest.raises(ValueError, match="incomplete"):
        module("pool")._pull_requests("org/a", "main", gh)


def test_reachable_helpers_without_files():
    blobs = {"tests/test_a.py": b"from . import helper\n", "tests/__init__.py": b"",
             "tests/helper.py": b"from .nested import leaf\n", "tests/nested/__init__.py": b"",
             "tests/nested/leaf.py": b"raise RuntimeError('never run')\n",
             "tests/unreachable.py": b"import nowhere\n"}
    class Objects:
        def blob(self, oid):
            return blobs[oid]
    reader, fit = module("gitread"), module("fit")
    changes = {"tests/test_a.py": fit.ChangedFile(None, blobs["tests/test_a.py"])}
    helpers = reader._reachable_tests(dict(zip(blobs, blobs)), changes, Objects())
    assert set(helpers) == set(blobs) - {"tests/test_a.py", "tests/unreachable.py"}


def test_inventory_repository_union_without_files():
    fit, counts, inv = module("fit"), module("counts"), module("inventory")
    old, new = b"def value():\n    return 1\n", b"def value():\n    return 2\n"
    test = b"from widget import value\ndef test_value():\n    assert value() == 2\n"
    item = fit.Candidate("org/widget", "org/widget#1", Path("unused"),
        {"widget.py": fit.ChangedFile(old, new),
         "tests/test_value.py": fit.ChangedFile(test.replace(b"== 2", b"== 1"), test)}, {})
    env = fit.Environment(frozenset(), frozenset(), frozenset())
    record = fit._assess_sources(item, env, {"widget.py": old.decode()})
    assert record.failed_rules == ()
    counts.staged_cases(record, "K_0")
    second = replace(record, bug_id="org/widget#2")
    entries = [{"repository": "org/widget", "pr": n, "merge_commit": "a" * 40, "parent": "b" * 40}
               for n in (1, 2)]
    completed = {row.bug_id: {"record": row.public(), "summary": counts.summarize([row])}
                 for row in (record, second)}
    result = inv._report(completed, entries, set(), complete=True, exists=False, prepared=False)
    assert result["counts"] == counts.summarize([record, second])["counts"]
    assert set(result["counts"].values()) == {1}
    assert result["selection"]["configuration"] == "K_0"


@pytest.mark.parametrize("tree", [b"120000 blob " + b"a" * 40 + b"\tlink.py\0",
                                  b"160000 commit " + b"a" * 40 + b"\tsub\0",
                                  b"100644 blob " + b"a" * 40 + b"\tA/x.py\0"
                                  b"100644 blob " + b"b" * 40 + b"\ta/y.py\0"])
def test_git_tree_refuses_links_submodules_and_aliases_without_files(tree):
    reader = module("gitread")
    objects = object.__new__(reader._Objects)
    objects.run = lambda *args: tree
    with pytest.raises(ValueError):
        objects.tree("a" * 40)


def test_git_command_is_read_only_without_files(monkeypatch):
    reader = module("gitread")
    objects = object.__new__(reader._Objects)
    objects.hooks, objects.objects = Path("empty-hooks"), Path("objects.git")
    objects.origin, objects.local = "https://github.com/org/a.git", False
    objects.env = {"GIT_LFS_SKIP_SMUDGE": "1"}
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"commit\n", stderr=b"")
    monkeypatch.setattr(reader.subprocess, "run", run)
    objects.fetch("a" * 40)
    args, kwargs = calls[0]
    assert "--no-recurse-submodules" in args and "--filter=blob:none" in args and "--depth=2" in args
    assert "core.hooksPath=empty-hooks" in args and "protocol.file.allow=never" in args
    assert "checkout" not in args and "clone" not in args
    assert kwargs["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"


def test_mode_only_changes_keep_bytes_without_files():
    reader = module("gitread")
    class Objects:
        def run(self, *args):
            assert args[0] == "diff-tree" and "--no-renames" in args
            return b"widget.py\0"
        def blob(self, oid):
            return b"VALUE = 1\n"
    changes = reader._changes(Objects(), "a" * 40, "b" * 40,
                               {"widget.py": "c" * 40}, {"widget.py": "c" * 40})
    assert set(changes) == {"widget.py"}
    assert changes["widget.py"].before == changes["widget.py"].after == b"VALUE = 1\n"


def test_inventory_public_fields_without_files():
    public = {"repository": "org/a", "bug_id": "org/a#1", "failed_rules": ["M1"], "flags": [],
              "mutant_count": 4, "variants": ["some_variant"]}
    class Record:
        def public(self):
            return public
    assert module("inventory")._public_record(Record()) == {key: public[key] for key in
                                                            ("repository", "bug_id", "failed_rules", "flags")}


def test_pool_150_bound(tmp_path):
    gh = FakeGitHub()
    result = mine(tmp_path, gh, [f"p{i}" for i in range(152)])
    assert len(result["repositories"]) == 150
    assert result["next_rank"] == 150
    assert not any("p150" in " ".join(call) for call in gh.calls)


def test_pool_deadline_resume_and_input_binding(tmp_path):
    gh = FakeGitHub()
    now = [0.0]
    def clock():
        return now[0]
    def advancing(args):
        value = gh(args)
        now[0] += 1
        return value
    first = mine(tmp_path, advancing, ["a", "b"], wall_seconds=6, clock=clock)
    assert first["complete"] is False
    assert len(first["repositories"]) == 1
    calls = len(gh.calls)
    second = mine(tmp_path, advancing, ["ignored-new-ranking"], wall_seconds=100, clock=clock)
    assert [r["repository"] for r in second["repositories"]] == ["org/a", "org/b"]
    assert not any(call == ["api", "repos/org/a"] for call in gh.calls[calls:])
    with pytest.raises(ValueError):
        mine(tmp_path, gh, ["a"], development=["org/a"])


def test_pool_initial_ranking_timeout_checkpoint(tmp_path):
    pool = module("pool")
    development = write_json(tmp_path / "development.json", [])
    selection = write_json(tmp_path / "selection.json", [])
    def stopped():
        raise TimeoutError("synthetic ranking timeout")
    result = pool.mine(output=tmp_path / "pool.json", checkpoint=tmp_path / "checkpoint.json",
                       development=development, selection_cohort=selection, ranking=stopped,
                       repository_root=tmp_path / "worktree")
    assert result["complete"] is False and result["entries"] == []
    assert json.loads((tmp_path / "checkpoint.json").read_text())["ranking"] is None


def test_pool_resume_counts_time_between_invocations(tmp_path):
    gh = FakeGitHub()
    active, wall = [0.0], [1000.0]
    def advancing(args):
        value = gh(args)
        active[0] += 1
        return value
    first = mine(tmp_path, advancing, ["a", "b"], wall_seconds=6, clock=lambda: active[0],
                 wall_clock=lambda: wall[0])
    assert not first["complete"] and len(first["repositories"]) == 1
    call_count = len(gh.calls)
    wall[0] += 100
    second = mine(tmp_path, advancing, [], wall_seconds=50, clock=lambda: active[0],
                  wall_clock=lambda: wall[0])
    assert not second["complete"] and len(gh.calls) == call_count


@pytest.mark.parametrize("date,kept", [("2026-05-31T23:59:59Z", False), ("2026-06-01T00:00:00Z", True),
                                      ("2026-10-29T23:59:59Z", True), ("2026-10-30T00:00:00Z", False)])
def test_pool_window_edges(tmp_path, date, kept):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr(mergedAt=date)]
    assert bool(mine(tmp_path, gh, ["a"])["entries"]) is kept


@pytest.mark.parametrize("rule", ["T1", "T3", "T4"])
def test_pool_mechanical_drops(tmp_path, rule):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr(baseRefName="other" if rule == "T1" else "main")]
    if rule == "T3":
        gh.files["org/a", 1, 1] = [{"filename": "widget.py"}, {"filename": "README.md"}]
    if rule == "T4":
        gh.commits["0" * 39 + "1"] = {"parents": []}
    result = mine(tmp_path, gh, ["a"])
    assert result["entries"] == []
    assert result["pr_decisions"][0]["codes"] == [rule]


def test_pool_refuses_search_and_file_truncation(tmp_path):
    gh = FakeGitHub()
    gh.prs["org/a"] = [pr(i + 1) for i in range(1000)]
    with pytest.raises(ValueError):
        mine(tmp_path, gh, ["a"])
    gh.prs["org/a"] = [pr(changedFiles=3)]
    with pytest.raises(ValueError):
        mine(tmp_path, gh, ["a"])


def git(repo, *args):
    env = {**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
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
    sources = {"widget.py": "def value():\n    return 1\n", "unchanged.py": "VALUE = 3\n",
               "removed.txt": "REMOVED = 1\n", "tests/__init__.py": "from . import helper\n",
               "tests/helper.py": "from .nested import leaf\n", "tests/nested/__init__.py": "",
               "tests/nested/leaf.py": "LEAF = 1\n", "tests/unreached.py": "raise RuntimeError('do not run')\n",
               "tests/test_value.py": "from widget import value\ndef test_value():\n    assert value() == 1\n"}
    for name, text in sources.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    parent = git(repo, "rev-parse", "HEAD")
    (repo / "widget.py").write_bytes(b"def value():\n    return 2\n")
    (repo / "tests/test_value.py").write_bytes(
        b"from widget import value\ndef test_value():\n    assert value() == 2\n")
    (repo / "removed.txt").unlink()
    (repo / "added.txt").write_bytes(b"new\x00bytes")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix")
    merge = git(repo, "rev-parse", "HEAD")
    hook = repo / ".git/hooks/post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes(b"#!/bin/sh\ntouch hook-ran\n")
    entry = {"repository": "org/widget", "pr": 1, "parent": parent, "merge_commit": merge,
             "rules": ["T1", "T3", "T4"]}
    return repo, entry


def test_gitread_bytes_parent_and_reachable_helpers(tmp_path, local_repo):
    repo, entry = local_repo
    item = module("gitread").read_candidate(entry, tmp_path / "cache", source=repo,
                                            repository_root=tmp_path / "worktree")
    assert item.bug_id == "org/widget#1"
    assert item.changed_files["widget.py"].before == b"def value():\n    return 1\n"
    assert item.changed_files["widget.py"].after == b"def value():\n    return 2\n"
    assert item.changed_files["removed.txt"].after is None
    assert item.changed_files["added.txt"].before is None
    assert item.changed_files["added.txt"].after == b"new\x00bytes"
    assert (item.parent_tree / "unchanged.py").read_bytes() == b"VALUE = 3\n"
    assert (item.parent_tree / "widget.py").read_bytes() == item.changed_files["widget.py"].before
    assert set(item.test_package_files) == {"tests/__init__.py", "tests/helper.py",
                                           "tests/nested/__init__.py", "tests/nested/leaf.py"}
    assert not (repo / "hook-ran").exists()
    assert not list((tmp_path / "cache").rglob("hook-ran"))


def test_gitread_checks_parent_and_cache_boundary(tmp_path, local_repo):
    repo, entry = local_repo
    reader = module("gitread")
    with pytest.raises(ValueError):
        reader.read_candidate({**entry, "parent": entry["merge_commit"]}, tmp_path / "cache", source=repo,
                              repository_root=tmp_path / "worktree")
    with pytest.raises(ValueError):
        reader.read_candidate(entry, tmp_path / "cache", source=repo, repository_root=tmp_path)


def test_inventory_end_to_end_resume_and_a2(tmp_path, local_repo, monkeypatch):
    repo, entry = local_repo
    inv, reader = module("inventory"), module("gitread")
    original = reader.read_candidate
    calls = []
    def read(row, cache, **kwargs):
        calls.append(row["pr"])
        return original(row, cache, source=repo, repository_root=tmp_path / "worktree")
    monkeypatch.setattr(reader, "read_candidate", read)
    pool = write_json(tmp_path / "pool.json", {"entries": [entry], "repositories": [
        {"repository": "org/widget"}, {"repository": "org/empty"}]})
    environment = write_json(tmp_path / "environment.json", {"resolvable_modules": ["pytest"],
                             "loaded_packages": [], "pytest_dependencies": []})
    output = tmp_path / "inventory.json"
    args = ["--pool", str(pool), "--environment", str(environment), "--cache", str(tmp_path / "cache"),
            "--output", str(output)]
    assert inv.main(args, repository_root=tmp_path / "worktree") == 0
    data = json.loads(output.read_text())
    assert data["complete"] and data["selection"]["tier"] == "stop"
    assert set(data["counts"]) == {"K_M", "K_0", "K_1", "K_2", "K_3", "K_123"}
    assert len(data["records"]) == 1 and data["counts"]["K_1"] == 1
    text = output.read_text()
    assert "return 2" not in text and "assert value" not in text and str(tmp_path) not in text
    assert inv.main(args, repository_root=tmp_path / "worktree") == 0
    assert calls == [1]
    exclude = write_json(tmp_path / "exclude.json", ["ORG/WIDGET", "org/empty"])
    assert inv.main([*args, "--exclude", str(exclude)], repository_root=tmp_path / "worktree") == 0
    data = json.loads(output.read_text())
    assert data["records"] == [] and set(data["counts"].values()) == {0}
    assert data["a2_exclusions"] == ["org/empty", "org/widget"]


def test_inventory_partial_resume_and_binding(tmp_path, local_repo, monkeypatch):
    repo, entry = local_repo
    inv, reader = module("inventory"), module("gitread")
    original = reader.read_candidate
    calls = []
    def read(row, cache, **kwargs):
        calls.append(row["pr"])
        if row["pr"] == 2 and calls.count(2) == 1:
            raise ValueError("synthetic read stop")
        return original(row, cache, source=repo, repository_root=tmp_path / "worktree")
    monkeypatch.setattr(reader, "read_candidate", read)
    pool = write_json(tmp_path / "pool.json", {"entries": [entry, {**entry, "pr": 2}]})
    env = write_json(tmp_path / "environment.json", {"resolvable_modules": [], "loaded_packages": [],
                                                   "pytest_dependencies": []})
    output = tmp_path / "inventory.json"
    kwargs = dict(pool=pool, environment=env, cache=tmp_path / "cache", output=output,
                  repository_root=tmp_path / "worktree")
    with pytest.raises(ValueError, match="synthetic"):
        inv.run(**kwargs)
    state = json.loads(output.read_text())
    assert not state["complete"] and state["selection"] is None and len(state["records"]) == 1
    result = inv.run(**kwargs)
    assert result["complete"] and calls == [1, 2, 2]
    write_json(env, {"resolvable_modules": ["changed"], "loaded_packages": [], "pytest_dependencies": []})
    with pytest.raises(ValueError, match="differ"):
        inv.run(**kwargs)


def test_a2_synthetic_count_only(tmp_path, capsys):
    pool = write_json(tmp_path / "pool.json", {"repositories": [{"repository": "org/a"},
                                                               {"repository": "org/b"}], "entries": []})
    sealed = write_json(tmp_path / "synthetic-sealed.json", {"bugs": [{"repository": "ORG/A"},
                                                                   {"repository": "secret/unrelated"}]})
    output = tmp_path / "exclude.json"
    assert module("a2_check").main(["--pool", str(pool), "--final-cohort", str(sealed),
                                    "--exclude", str(output)]) == 0
    assert capsys.readouterr().out == "1\n"
    assert json.loads(output.read_text()) == ["org/a"]


def test_a2_error_contains_no_cohort_material(tmp_path, capsys):
    pool = write_json(tmp_path / "pool.json", {"entries": [], "repositories": []})
    sealed = write_json(tmp_path / "synthetic-sealed.json", ["secret-content-without-a-repository-id"])
    with pytest.raises(SystemExit) as exc:
        module("a2_check").main(["--pool", str(pool), "--final-cohort", str(sealed),
                                 "--exclude", str(tmp_path / "exclude.json")])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "A2 input or output refused\n"


def test_inventory_refuses_sealed_paths_without_opening(tmp_path):
    args = ["--pool", str(tmp_path / "final-hidden.json"), "--environment", str(tmp_path / "environment.json"),
            "--cache", str(tmp_path / "cache"), "--output", str(tmp_path / "output.json")]
    with pytest.raises(SystemExit) as exc:
        module("inventory").main(args, repository_root=tmp_path / "worktree")
    assert exc.value.code == 2
