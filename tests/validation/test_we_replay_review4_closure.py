# SPDX-License-Identifier: Apache-2.0
"""Review 4 lead rulings through real synthetic archives and fake containers."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


class Docker:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
            "dirty": 0, "commits": 1, "oracle": False}).encode(), b"")


@pytest.fixture
def batch(tmp_path, monkeypatch, capsys):
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    source, output = tmp_path / "source", tmp_path / "out"
    archive = rc.c.Archive(source)
    runs = []
    for passes in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        patch = b"right" if passes else b"wrong"
        run.write("final.patch", patch)
        run.write("prompt.txt", "Issue #1: synthetic")
        run.write("rounds/round-1.patch", patch)
        run.write_json("rounds.json", [{"round": 1, "input": {"task": "synthetic", "requirements": []}}])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
        runs.append(run)
    # One completed check has no final: P3 must stay uncertain.
    archive.start("check", TASK.id, checked_run=runs[0].rel).finish({"checks": [
        {"patch": "base", "patch_applied": True, "passes": False}]}, "finished")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    argv = ["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"]
    assert rc.main(argv, docker=Docker()) == 0
    live = json.loads(capsys.readouterr().out)
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    return SimpleNamespace(source=source, output=output, path=path, live=live, argv=argv, runs=runs,
                           saved=json.loads(path.read_text(encoding="utf-8")))


def save(batch):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")


def score(batch, capsys, *, source=False):
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    code = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return code, json.loads(captured.out, parse_constant=lambda value: pytest.fail(f"nonstandard JSON: {value}"))


@pytest.mark.parametrize("edit", ["snapshot absent", "snapshot malformed", "evaluation", "flag", "start",
                                  "case field", "inventory copy"])
@pytest.mark.parametrize("source", [False, True])
def test_current_structure_mismatch_uses_older_route(batch, capsys, edit, source):
    if edit == "snapshot absent":
        batch.saved["plan"].pop("source_snapshot")
    elif edit == "snapshot malformed":
        batch.saved["plan"]["source_snapshot"]["files"]["runs.jsonl"] = "not a digest"
    elif edit == "evaluation":
        batch.saved["evaluation"]["predictions"]["P3"]["status"] = "met"
    elif edit == "flag":
        next(row for row in batch.saved["plan"]["index_excluded"] if row.get("final_results") == 0)[
            "affects_denominator"] = False
    elif edit == "start":
        index = batch.output / "runs.jsonl"
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
        next(row for row in rows if row.get("kind") == "replay-rc-batch")["evidence_rule_version"] = 0
        index.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    elif edit == "case field":
        batch.saved["plan"]["cases"][0].pop("patch")
    else:
        batch.saved["plan"]["source_inventory"] = []
    save(batch)
    code, result = score(batch, capsys, source=source)
    # An absent/malformed manifest cannot establish identical source reads.
    can_refresh = source and edit not in {"snapshot absent", "snapshot malformed"}
    assert code == (0 if can_refresh else 2)
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["evidence_source"] == ("source" if can_refresh else "unavailable")


def test_self_consistent_same_user_edit_is_outside_the_claim(batch, capsys):
    flag = next(row for row in batch.saved["plan"]["index_excluded"] if row.get("final_results") == 0)
    flag["affects_denominator"] = False
    plan = batch.saved["plan"]
    batch.saved["evaluation"] = rc.evaluate(batch.saved["records"], plan["cases"], modes=plan["modes"],
        s0=batch.saved["s0"], excluded=plan["excluded"], index_excluded=plan["index_excluded"])
    save(batch)
    code, result = score(batch, capsys)
    assert code == 0 and result["predictions"]["P3"]["status"] == "met"
    assert result["evidence_source"] == "batch"


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("version", [2, 999])
def test_newer_rule_never_enumerates_source(batch, monkeypatch, capsys, source, version):
    batch.saved["evidence_rule_version"] = version
    save(batch)

    def forbidden(*args, **kwargs):
        pytest.fail("older code enumerated evidence for a newer rule")

    monkeypatch.setattr(rc, "enumerate_cases", forbidden)
    code, result = score(batch, capsys, source=source)
    assert code == 2 and result["by_bug"] == {}
    assert result["index_excluded"][0]["reason"] == "saved under a newer evidence rule than this code"
    assert result["evidence_rule_version"] == rc.EVIDENCE_RULE_VERSION
    assert all(p["prediction_met"] is None and p["kill"] is None for p in result["predictions"].values())


def test_portable_snapshot_and_explicit_source_refresh(batch, capsys):
    snapshot = batch.saved["plan"]["source_snapshot"]
    assert "root" not in snapshot and str(batch.source) not in json.dumps(batch.saved)
    assert rc.main([*batch.argv, "--dry"]) == 0
    assert str(batch.source) not in capsys.readouterr().out
    batch.saved["evidence_rule_version"] = 0
    save(batch)
    before = batch.path.read_bytes()
    code, result = score(batch, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"
    code, result = score(batch, capsys, source=True)
    assert code == 0 and result["evidence_source"] == "source"
    for field in ("by_bug", "predictions", "excluded", "index_excluded"):
        assert result[field] == batch.live[field]
    assert batch.path.read_bytes() == before
    batch.source.rename(batch.source.with_name("moved"))
    batch.saved["evidence_rule_version"] = rc.EVIDENCE_RULE_VERSION
    save(batch)
    code, result = score(batch, capsys)
    assert code == 0 and result["evidence_source"] == "batch"


@pytest.mark.parametrize("version", [None, 0, "1", True, 1.0])
def test_explicit_source_replaces_older_labels_and_flags(batch, capsys, version):
    batch.saved["evidence_rule_version"] = version
    plan = batch.saved["plan"]
    for case in plan["cases"] + batch.saved["records"]:
        case["label"] = "wrong"
    plan["excluded"], plan["index_excluded"] = [], []
    save(batch)
    code, result = score(batch, capsys, source=True)
    assert code == 0 and result["evidence_source"] == "source"
    for field in ("by_bug", "predictions", "excluded", "index_excluded"):
        assert result[field] == batch.live[field]


def test_previous_absolute_snapshot_compares_only_to_explicit_source(batch, capsys):
    batch.saved["evidence_rule_version"] = 0
    snapshot = batch.saved["plan"]["source_snapshot"]
    snapshot.pop("identity_sha256")
    snapshot["root"] = str(batch.source)
    save(batch)
    code, result = score(batch, capsys, source=True)
    assert code == 0 and result["evidence_source"] == "source"
    assert result["predictions"] == batch.live["predictions"]


def test_explicit_source_rejects_reused_different_arguments(batch, capsys):
    batch.saved["evidence_rule_version"] = 0
    batch.saved["records"][0]["arguments"]["task"] = "changed review task"
    save(batch)
    code, result = score(batch, capsys, source=True)
    assert code == 0 and result["evidence_source"] == "source"
    assert any(row["reason"] == "saved review no longer matches current evidence" for row in result["index_excluded"])


def test_unread_source_file_does_not_block_explicit_refresh(batch, capsys):
    batch.saved["evidence_rule_version"] = 0
    save(batch)
    path = batch.runs[0].path / "run.json"
    assert path.relative_to(batch.source).as_posix() not in batch.saved["plan"]["source_snapshot"]["files"]
    with path.open("ab") as stream:
        stream.write(b" ")
    code, result = score(batch, capsys, source=True)
    assert code == 0 and result["predictions"] == batch.live["predictions"]


@pytest.mark.parametrize("outcome,kill", [("test_integrity_compromised", "P1"), ("supported_within_scope", "P2")])
def test_explicit_source_preserves_agent_and_hook_kills(batch, monkeypatch, capsys, outcome, kill):
    class OutcomeDocker(Docker):
        def __call__(self, *args, **kwargs):
            reply = super().__call__(*args, **kwargs)
            if args[0] == "exec":
                reply.stdout = json.dumps({"ok": True, "assessment": {"outcome": outcome, "completion": {}}}).encode()
            return reply

    output = batch.output.with_name("kill-out")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main([*batch.argv, "--mode", "agent", "--s0", "after"], docker=OutcomeDocker()) == 0
    live = json.loads(capsys.readouterr().out)
    assert live["predictions"][kill]["kill"] is True
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["evidence_rule_version"] = 0
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert rc.main(["--score", str(path), "--source", str(batch.source)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_source"] == "source"
    assert result["by_bug"] == live["by_bug"] and result["predictions"] == live["predictions"]


@pytest.mark.parametrize("change", ["bytes", "selection", "inventory"])
def test_explicit_source_change_is_incomplete(batch, monkeypatch, capsys, change):
    batch.saved["evidence_rule_version"] = 0
    save(batch)
    if change == "bytes":
        with (batch.source / "runs.jsonl").open("ab") as stream:
            stream.write(b"\n")
    elif change == "selection":
        monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK, SimpleNamespace(id="other-1")])
    else:
        (batch.source / "round3").mkdir()
    code, result = score(batch, capsys, source=True)
    assert code == 2 and result["by_bug"] == {}
    assert result["predictions"]["P3"]["kill"] is None


def test_old_root_is_data_never_a_source_argument(batch, monkeypatch, capsys):
    batch.saved["evidence_rule_version"] = 0
    batch.saved["plan"]["source_snapshot"]["root"] = "//unrequested.invalid/share/source"
    save(batch)

    def forbidden(*args, **kwargs):
        pytest.fail("batch selected a source")

    monkeypatch.setattr(rc, "enumerate_cases", forbidden)
    code, result = score(batch, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"


def test_malformed_old_identity_is_incomplete(batch, capsys):
    batch.saved["evidence_rule_version"] = 0
    batch.saved["plan"]["source_snapshot"]["root"] = "bad identity \ud83d"
    save(batch)
    code, result = score(batch, capsys, source=True)
    assert code == 2 and result["by_bug"] == {}


@pytest.mark.parametrize("field,value", [("identity_sha256", None), ("tasks", [1]),
                                        ("inventory", []), ("files", {"/absolute": "a" * 64})])
def test_malformed_current_snapshot_cannot_supply_certainty(batch, capsys, field, value):
    batch.saved["plan"]["source_snapshot"][field] = value
    save(batch)
    code, result = score(batch, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"


@pytest.mark.parametrize("version", [float("nan"), float("inf"), -float("inf"), {"nested": float("nan")}])
def test_unusable_version_prints_standard_json(batch, capsys, version):
    batch.saved["evidence_rule_version"] = version
    save(batch)
    batch.source.rename(batch.source.with_name("moved-source"))
    code, result = score(batch, capsys)
    assert code == 2 and result["evidence_source"] == "unavailable"


@pytest.mark.parametrize("enabled", [False, True])
def test_older_mode_three_is_json_incomplete(batch, capsys, enabled):
    batch.saved["evidence_rule_version"] = 0
    if enabled:
        batch.saved["plan"]["modes"].append("neighbourhood")
    batch.saved["records"] += [{**record, "mode": "neighbourhood"} for record in batch.saved["records"]]
    save(batch)
    code, result = score(batch, capsys, source=True)
    assert code == 2 and result["predictions"]["P7"]["status"] == ("incomplete" if enabled else "disabled")


@pytest.mark.parametrize("kind", ["pin", "registration"])
def test_newer_rule_cannot_hide_mandatory_refusal(batch, monkeypatch, capsys, kind):
    batch.saved["evidence_rule_version"] = 999
    if kind == "pin":
        batch.saved["records"][0]["image_id"] = "sha256:" + "f" * 64
    else:
        batch.saved["registration_sha256"] = "f" * 64
    save(batch)
    assert rc.main(["--score", str(batch.path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert ("image ID differs" if kind == "pin" else "registration digest differs") in captured.err


def test_surrogate_input_survives_dry_live_and_rescore(batch, monkeypatch, capsys):
    text = "synthetic half \ud83d"
    for run in batch.runs:
        run.write("rounds/round-1.input.json", json.dumps({"tool_input": {
            "task": "synthetic", "requirements": [], "note": text}}).encode("ascii"))
        rounds = run.path / "rounds.json"
        rounds.write_text(json.dumps([{"round": 1, "input": {"task": "synthetic", "requirements": [],
                                                            "note": text}}]), encoding="utf-8")
    argv = [*batch.argv, "--mode", "agent"]
    assert rc.main([*argv, "--dry"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["cases"][0]["arguments"]["note"] == text
    output = batch.output.with_name("surrogate-out")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(argv, docker=Docker()) == 0
    capsys.readouterr()
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    agent = next(record for record in saved["records"] if record["mode"] == "agent")
    assert saved["status"] == "finished" and agent["arguments"]["note"] == text
    assert rc.main(["--score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_source"] == "batch"


def test_record_finish_failure_saves_failed_batch_with_filename(batch, monkeypatch, capsys):
    real = rc.c.RunFolder.finish

    def fail_record(folder, record, status):
        if folder.rel.startswith("replay-rc/"):
            raise OSError("synthetic finalization failure")
        return real(folder, record, status)

    monkeypatch.setattr(rc.c.RunFolder, "finish", fail_record)
    output = batch.output.with_name("finish-failed")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(batch.argv, docker=Docker()) == 1
    captured = capsys.readouterr()
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and "run.json" in saved["error"]
    assert "replay-rc/" in captured.err and "evaluation" in saved
    assert rc.main(["--score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["predictions"]["P3"]["status"] == "incomplete"


def test_archive_json_preserves_surrogate_text(tmp_path):
    archive = rc.c.Archive(tmp_path / "archive")
    text = "half \ud83d with unicode \u2603"
    folder = archive.start("run", "synthetic", note=text)
    folder.finish({"note": text}, "finished")
    assert json.loads((folder.path / "run.json").read_text(encoding="utf-8"))["note"] == text
    assert archive.rows()[0]["note"] == text


# Hash the rule's own syntax, not its line numbers, comments or unrelated CLI.
# A rule edit must add a new version/digest pair, retaining earlier pairs.
RULE_DIGESTS = {1: "0b362bf50465e1e37742ffec5ce298f8247ac55a2a4b1c25f3dcf6aee576895a"}


def test_evidence_rule_code_requires_a_version_change():
    import sys
    from inspect import getclosurevars
    from types import FunctionType, ModuleType

    package = Path(__file__).parents[2] / "validation/worked_examples"
    sources, reached, pending = {}, set(), []

    def local_module(value):
        origin = getattr(getattr(value, "__spec__", None), "origin", None)
        return isinstance(value, ModuleType) and origin and Path(origin).is_relative_to(package)

    def add_source(module, variant="", path=None):
        key = module.__name__, variant
        if key not in sources:
            tree = ast.parse(Path(path or module.__file__).read_text(encoding="utf-8"))
            definitions = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions[node.name] = node
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            definitions[target.id] = node
            sources[key] = module, tree, definitions
        return key

    def enqueue(key, name):
        if name in sources[key][2] and (key, name) not in reached:
            reached.add((key, name))
            pending.append((key, name))

    def qualified(module, name):
        enqueue(add_source(module), name)
        # Source definitions remain stable under simple fixture stubs. A wrapper
        # may instead delegate to another source implementation, including one
        # held in a closure/dictionary. Follow those code references, never call
        # the wrapper. Keep both sources covered rather than replacing either.
        value = getattr(module, name, None)
        wrappers, visited = [value], set()
        while wrappers:
            value = wrappers.pop()
            if id(value) in visited:
                continue
            visited.add(id(value))
            if isinstance(value, dict):
                wrappers.extend(value.values())
            elif isinstance(value, FunctionType):
                if value.__name__ == name:
                    if value is not getattr(module, name, None):
                        path = Path(value.__code__.co_filename)
                        if path.is_file():
                            enqueue(add_source(module, f"wrapped:{name}", path), name)
                    continue
                references = getclosurevars(value)
                wrappers.extend(references.globals.values())
                wrappers.extend(references.nonlocals.values())

    def resolve(module, node):
        if isinstance(node, ast.Name):
            return vars(module).get(node.id)
        if isinstance(node, ast.Attribute):
            owner = resolve(module, node.value)
            if isinstance(owner, ModuleType):
                return vars(owner).get(node.attr)
        return None

    root = add_source(rc)
    for name in ("enumerate_cases", "evaluate", "review_arguments", "agent_views", "score_saved"):
        enqueue(root, name)
    while pending:
        key, name = pending.pop()
        module, _, definitions = sources[key]
        for node in ast.walk(definitions[name]):
            if isinstance(node, ast.Name):
                enqueue(key, node.id)
                value = vars(module).get(node.id)
                owner = sys.modules.get(getattr(value, "__module__", ""))
                if node.id not in definitions and local_module(owner):
                    qualified(owner, getattr(value, "__name__", node.id))
            elif isinstance(node, ast.Attribute):
                owner = resolve(module, node.value)
                if local_module(owner):
                    qualified(owner, node.attr)
    syntax = []
    for key, (_, tree, definitions) in sorted(sources.items()):
        identities = {id(definitions[name]) for source, name in reached if source == key}
        nodes = [node for node in tree.body if id(node) in identities]
        if nodes:
            syntax.append((key, ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False)))
    digest = hashlib.sha256(json.dumps(syntax, ensure_ascii=True).encode()).hexdigest()
    assert digest == RULE_DIGESTS[rc.EVIDENCE_RULE_VERSION], "evidence rule changed without a new version/digest pair"
