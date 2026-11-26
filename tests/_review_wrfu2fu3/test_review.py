"""Synthetic fixtures only; no Docker, model, source archive or held-out data."""
import ast
import copy
import importlib.util
import json
import math
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/project", parent="c" * 40)


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    # pytest's mode=0o700 mkdir produces inaccessible Windows sandbox folders.
    # Keep the requested basetemp on real disk using inherited permissions.
    base = (Path(request.config.getoption("basetemp"))
            if request.config.getoption("basetemp") else tmp_path_factory.getbasetemp())
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    return path


def arguments(value=0.0):
    return {"task": "Preserve the sign of zero", "timeout_s": 600,
            "requirements": [{"id": "R1", "text": "Preserve sign", "checks": []}],
            "template_instances": [{"id": "E1", "template": "example.v1",
                "requirement_id": "R1", "intent": "change", "bindings": {
                    "function": "synthetic.sign", "cases": [{"args": [value], "returns": 1.0}]}}]}


class ReplyStub:
    """Fake transport; use the real input validator and real refusal response."""
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            from g6.harness import patch_review as pr
            assignment = ast.parse(kwargs["input"].decode()).body[2]
            request = json.loads(ast.literal_eval(assignment.value.args[0]))
            try:
                pr._inputs(request["task"], "", [], None, request["timeout_s"],
                           request["requirements"], template_instances=request.get("template_instances"),
                           search=request.get("search"))
            except pr._Refused:
                value = pr.review_patch(**request)
                assert not rc.readable_review(value)
            else:
                value = {"ok": True, "assessment": {
                    "outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        state = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(state).encode(), b"")


@pytest.fixture
def build(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_LLM_PROVIDER", "none")
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))

    def make(request=None):
        source, output = tmp_path / "source", tmp_path / "output"
        archive = rc.c.Archive(source)
        runs = []
        for passes in (True, False):
            run = archive.start("run", TASK.id, arm="B")
            patch = b"right" if passes else b"wrong"
            run.write("final.patch", patch)
            run.write("prompt.txt", "Issue #1: synthetic issue")
            run.write("rounds/round-1.patch", patch)
            run.write_json("rounds.json", [{"round": 1, "input":
                                           arguments() if request is None else request}])
            run.finish({}, "finished")
            archive.start("check", TASK.id, checked_run=run.rel).finish({"checks": [
                {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
            runs.append(run)
        monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        assert rc.main(["--source", str(source), "--registered-commit", COMMIT,
                        "--mode", "agent", "--s0", "after"], docker=ReplyStub()) == 0
        live = json.loads(capsys.readouterr().out)
        path = next(output.glob("replay-rc-batch/development/*/run.json"))
        return SimpleNamespace(source=source, path=path, runs=runs, live=live,
                               saved=json.loads(path.read_text(encoding="utf-8")))
    return make


def score(batch, capsys, source=False):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return status, json.loads(captured.out)


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_control_honest_round_trip(build, capsys, source):
    batch = build()
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        0, "source" if source else "batch", "met")


@pytest.mark.parametrize("alias", [False, True], ids=["control-exact", "case-alias"])
def test_one_windows_run_cannot_bind_two_digests(build, capsys, alias):
    batch = build()
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    run = wrong["origin_run"]
    if alias:
        if os.name != "nt":
            pytest.skip("requires a case-insensitive Windows filesystem")
        run = run.rsplit("/", 1)[0] + "/" + run.rsplit("/", 1)[1].swapcase()
        assert run != wrong["origin_run"]
        assert (batch.source / run / "final.patch").samefile(
            batch.source / wrong["origin_run"] / "final.patch")
    case["origins"][0]["run"] = right["origin_run"] = run
    status, result = score(batch, capsys)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    batch.saved["evidence_rule_version"] = 0
    _, fresh = score(batch, capsys, source=True)
    assert fresh["evidence_source"] == "source"
    assert fresh["predictions"]["P2"]["status"] == "incomplete"
    assert observed == (2, "unavailable", "incomplete"), observed


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
@pytest.mark.parametrize("change", [False, True], ids=["control-same-zero", "changed-zero-sign"])
def test_saved_zero_sign_must_match_writer(build, tmp_path, capsys, source, change):
    from g6.harness import verification_templates as vt
    batch = build()
    record = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    before = copy.deepcopy(record["arguments"])
    if change:
        record["arguments"]["template_instances"][0]["bindings"]["cases"][0]["args"][0] = -0.0
    after = record["arguments"]
    first = vt.template_instances(before["template_instances"], {"R1"})[0]
    second = vt.template_instances(after["template_instances"], {"R1"})[0]
    assert (vt.instance_digest(first) != vt.instance_digest(second)) is change
    # Resolve the accepted bindings against an actual self-built project file.
    (tmp_path / "synthetic.py").write_text(
        "import math\ndef sign(value):\n    return math.copysign(1.0, value)\n", encoding="utf-8")
    tree = vt.Tree(tmp_path, None, frozenset({"synthetic.py"}), frozenset())
    for instance in (first, second):
        applied = vt._example(tree, vt.ExampleBindings.model_validate(instance.bindings), vt.PATCH)
        assert applied.data["cases"][0]["expect"] == "returns"
    old_value = first.bindings["cases"][0]["args"][0]
    new_value = second.bindings["cases"][0]["args"][0]
    assert (math.copysign(1.0, old_value) != math.copysign(1.0, new_value)) is change
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    if change:
        assert result["predictions"]["P2"]["status"] == "incomplete", observed
    else:
        assert observed == (0, "source" if source else "batch", "met"), observed


@pytest.mark.parametrize("depth", [10, 350], ids=["control-shallow", "deep"])
@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_refused_nested_json_still_rescores(build, capsys, depth, source):
    nested = False
    for _ in range(depth):
        nested = {"nested": nested}
    request = arguments()
    request["search"] = {"templates": nested}
    batch = build(request)
    assert batch.live["predictions"]["P2"]["status"] == "incomplete"
    assert all(r["status"] == "failed" and not r["review"]["ok"] for r in batch.saved["records"])
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    assert status in (0, 2)
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("change", [False, True], ids=["control-same-zero", "changed-zero-sign"])
def test_captured_zero_sign_must_match_inline_input(build, change):
    batch = build()
    run = batch.runs[0]
    run.write_json("rounds/round-1.input.json", {"tool_input": arguments(-0.0 if change else 0.0)})
    if change:
        with pytest.raises(ValueError, match="archived review arguments disagree"):
            rc.enumerate_cases(batch.source, [TASK])
    else:
        cases, _ = rc.enumerate_cases(batch.source, [TASK])
        assert len(cases) == 2


def test_control_json_edges():
    # JSON round trips erase tuples before either comparison operand is built.
    round_trip = lambda v: json.loads(json.dumps(v))
    assert rc._json_equal(round_trip({"x": (1, 2)}), {"x": [1, 2]})
    assert rc._json_equal({"a": 2**200, "b": "\u2603"}, {"b": "\u2603", "a": 2**200})
    for left, right in [(True, 1), (1, 1.0), ([1], (1,)), ("\u00e9", "e\u0301"),
                        (float("nan"), float("nan")), (float("inf"), float("inf"))]:
        assert not rc._json_equal(left, right)


def test_control_origin_map():
    run = "run/black-5634/synthetic"
    case = lambda origins, digest: SimpleNamespace(origins=origins, digest=digest)
    assert rc._unique_origin_digests([case([{"run": run}], "a"), case([{"run": run}], "a")])
    assert not rc._unique_origin_digests([case([{"run": run}], "a"), case([{"run": run}], "b")])
    for origin in ({}, {"run": None}, {"run": ""}):
        assert not rc._unique_origin_digests([case([origin], "a")])


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_control_unbound_record_is_incomplete(build, capsys, source):
    batch = build()
    batch.saved["records"][0]["origin_run"] = None
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    assert status == (0 if source else 2)
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_control_digest_guard_covers_imported_task_repo(build, tmp_path, monkeypatch):
    build()
    guard_path = Path(rc.__file__).parents[2] / "tests/validation/test_we_replay_review4_closure.py"
    spec = importlib.util.spec_from_file_location("review3_rule_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.test_evidence_rule_code_requires_a_version_change()
    tree = ast.parse(Path(rc.we.__file__).read_text(encoding="utf-8"))
    task = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Task")
    repo = next(n for n in task.body if isinstance(n, ast.FunctionDef) and n.name == "repo")
    repo.body = [ast.Return(value=ast.Constant(value="/changed/repo"))]
    ast.fix_missing_locations(tree)
    changed = tmp_path / "changed_run.py"
    changed.write_text(ast.unparse(tree), encoding="utf-8")
    monkeypatch.setattr(rc.we, "__file__", str(changed))
    with pytest.raises(AssertionError, match="evidence rule changed"):
        guard.test_evidence_rule_code_requires_a_version_change()
