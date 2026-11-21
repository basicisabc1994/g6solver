"""Review 2 of the learning loop: its five reproductions (run in memory, as the review sandbox was read-only), and
the one rule (``verification_search.evidence_class``) every consumer of a reading decides by."""
import hashlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import MethodVariant, ProblemSignature
from g6.core.ledger import ChainLedger, digest
from g6.harness import completion_loop as loop, outcome_history as h, verification_search as vs
from g6.mcp import selector as mcp_selector
from g6.workforce import receipt_store, repo_tests
from tests.harness.test_proposed_checks import result

REPOSITORY = "c" * 64


def _stores(monkeypatch):
    stores = {}
    receipt_path = SimpleNamespace(is_file=lambda: True)

    def ledger(path):
        return stores.setdefault(str(path), ChainLedger())

    def state_path(*parts):
        return Path("review-state").joinpath(*parts)

    monkeypatch.setattr(receipt_store, "ChainLedger", ledger)
    monkeypatch.setattr(h, "ChainLedger", ledger)
    monkeypatch.setattr(receipt_store, "_locked", lambda path: nullcontext())
    monkeypatch.setattr(receipt_store, "store_path", lambda: receipt_path)
    monkeypatch.setattr(receipt_store, "state_path", state_path)
    monkeypatch.setattr(h, "state_path", state_path)
    monkeypatch.setattr(receipt_store, "_executable", lambda _: {})
    monkeypatch.setattr(h, "repository_id", lambda root: REPOSITORY)
    return ledger


def _receipt(ran, sources, *, legacy=False):
    requested = list(sources)
    body = receipt_store._body(
        started_at="s", finished_at="f", before={}, after={}, base_commit="b" * 40,
        python="", requested=requested, found=ran)
    binding = repo_tests._learning_binding(
        ran, requested, repository=REPOSITORY,
        check_digests={node.partition("::")[0]: hashlib.sha256(source.encode()).hexdigest()
                       for node, source in sources.items()})
    if legacy:
        binding.pop("readings")
        binding["test_outcomes"] = ran["test_outcomes"]
    ident = receipt_store._record(body, learning=binding)
    return {**ran, "receipt": ident, "source_before": body["source_before"]}


def _record_stated(ran, sources):
    h.record_stated(SimpleNamespace(repo=Path.cwd(), files=[]), sources,
        [{"receipt": ran["receipt"], "provenance": "stated"}], artifact=ran["source_before"])
    return h.read(Path.cwd())


def test_mixed_collection_error_is_not_retained_or_credited(monkeypatch):
    _stores(monkeypatch)
    missing_file, failed_node = "test_optional.py", "test_logic.py::test_logic"
    source = "from calc import edge\n\ndef test_edge():\n    assert edge() == 1\n"
    ran = result(failed=[failed_node], messages={
        missing_file: "ModuleNotFoundError: No module named uninstalled_optional_dependency",
        failed_node: "AssertionError: logic returned 0"})
    # A continued collection runs an unrelated failing test beside the missing dependency.
    counts = {"passed": 0, "failures": 1, "errors": 1, "outside": 1}
    ran.update(verdict=repo_tests._judge(1, counts, "", []), failed=2, total=2,
        completed=False, collection_stopped=False, missing_modules=["uninstalled_optional_dependency"])
    assert repo_tests.read_report(ran).valid
    assert vs.reading(ran, missing_file)["collection"] is True
    sources = {missing_file: source, failed_node: "def test_logic():\n    assert False\n"}
    ran = _receipt(ran, sources)
    history = _record_stated(ran, {missing_file: source})
    assert len(history.rows) == 1
    context = ProblemSignature("software_engineering", "bug_fix", "patch", 1, 10)
    variants = [MethodVariant("review", "review", "pytest", "g6_review_patch", "review", 1, kind="reviewer")]
    decision = history.selector(context, variants).select(context, variants, required_methods=["review"])
    observed = (len(history.retained), decision.ordered_variants[0]["historical_resolutions"])
    assert observed == (0, 0), observed


def test_legacy_receipt_does_not_invent_a_pass(monkeypatch):
    _stores(monkeypatch)
    node = "test_probe.py::test_probe"
    sources = {node: "def test_probe():\n    pass\n"}
    ran = {**result([node]), "completed": True, "probe_observations": "probe payload unreadable"}
    assert vs.reading(ran, node)["result"] == "error"
    ran = _receipt(ran, sources, legacy=True)
    history = _record_stated(ran, sources)
    classifications = [row["classification"] for row in history.rows]
    assert classifications == ["check_error"], classifications


def test_k_control_does_not_call_collection_error_reproduced(monkeypatch):
    _stores(monkeypatch)
    source = "from calc import edge\n\ndef test_edge():\n    assert edge() == 1\n"
    one = vs.ProposedCheck(id="edge", requirement_id=loop.REQUIREMENT,
        intent="change", file="test_edge.py", source=source)
    node = vs.placement(one)
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(), base_commit="b" * 40)
    monkeypatch.setattr(loop, "_checks", lambda *a: ([one], {one.id: node}, []))
    patch = {**result(), "verdict": "collection_error",
        "failure_messages": {node: "ModuleNotFoundError: No module named optional_dependency"},
        "failed": 1, "total": 1, "completed": False, "degraded": True}
    assert repo_tests.read_report(patch).valid
    assert vs.reading(patch, node)["result"] == "failed"
    patch = _receipt(patch, {node: source})
    base = _receipt({**result([node]), "completed": True}, {node: source})
    monkeypatch.setattr(vs, "_run_check", lambda *a, **kw: base)
    activities = loop.record_reproductions(
        change, Path("unused"), deadline=loop._clock() + 60, _observed={one.id: patch})
    assert not h.read(Path.cwd()).retained  # Admission rejects this exact receipt.
    results = [a["result"] for a in activities]
    assert "reproduced" not in results, results


def test_select_survives_accumulated_admitted_history(monkeypatch):
    ledger = _stores(monkeypatch)
    node = "test_edge.py::test_edge"
    sources = {node: "def test_edge():\n    assert True\n"}
    ran = _receipt({**result([node]), "completed": True}, sources)
    history = _record_stated(ran, sources)
    row = history.rows[0]
    outcomes = ledger(h.store_path(REPOSITORY))
    # Populate supported history records for different requirement bindings.
    for i in range(6666):
        value = {**row, "finding": f"requirement-{i}"}
        outcomes.append(h.KIND, digest(value), value)
    assert len(h.read(Path.cwd()).rows) == 6667

    class Server:
        def tool(self):
            return lambda function: setattr(self, function.__name__, function) or function

    server = Server()
    mcp_selector.register(server)
    reply = server.g6_select("review my Python patch", phase="verify")
    assert reply["ok"], reply
    # The reply says history was cut from ranking.
    assert "cut from ranking" in reply["learned"]["history_cut"], reply["learned"]
    assert "cut from ranking" in reply["evidence_note"], reply["evidence_note"]


def test_resolution_preserves_the_selected_test(monkeypatch):
    from g6.harness import resolution

    source = (
        "from calc import edge\n\ndef test_selected():\n    assert edge() == 1\n"
        "\ndef test_unselected():\n    assert False\n"
    )
    one = vs.ProposedCheck(id="selected", requirement_id="R", intent="preserve",
        file="test_pair.py", source=source, test="test_selected")
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(), base_commit="b" * 40)

    def run(change, placed, source, node, *, at_base, **kwargs):
        import sys
        import types

        module = types.ModuleType("calc")
        module.edge = lambda: 1 if at_base else 0
        passed, failed = [], []
        with monkeypatch.context() as local:
            local.setitem(sys.modules, "calc", module)
            namespace = {}
            exec(source, namespace)
            selected = node.partition("::")[2]
            for name, function in namespace.items():
                if not name.startswith("test_") or selected and name != selected:
                    continue
                identity = placed + "::" + name
                try:
                    function()
                except AssertionError:
                    failed.append(identity)
                else:
                    passed.append(identity)
        return result(passed, failed)

    monkeypatch.setattr(vs, "_run_check", run)
    original = vs.search(change, [one], {one.id: vs.placement(one)},
        deadline=vs._clock() + 60, artifact="a" * 64)
    assert original.activities[0]["result"] == "counterexample"
    assessment = {"requirements": [{"id": "R", "text": "Keep selected behavior", "checks": [],
        "status": "passed_on_stated_checks"}],
        "completion": {"defects_reproduced": {"proposed_checks": original.defects}}}
    call = resolution.build(assessment, repo=str(change.repo), task="Keep selected behavior")[0]["cheapest_check"]
    replay = vs.proposed_checks(call["arguments"]["proposed_checks"], {"R"})
    repeated = vs.search(change, replay, {c.id: vs.placement(c) for c in replay},
        deadline=vs._clock() + 60, artifact="a" * 64)
    observed = repeated.activities[0]["result"]
    assert observed == "counterexample", observed


#: One reading for each qualifier the rule reads, and what it is evidence of (lead ruling, 2026-11-21).
SHAPES = {
    "pass": ({"result": "passed", "verdict": "passed"}, "pass"),
    "failure": ({"result": "failed", "verdict": "failed"}, "defect"),
    "uncollected_in_a_failed_run": ({"result": "failed", "verdict": "failed", "collection": True}, "check_error"),
    "collection_error": ({"result": "failed", "verdict": "collection_error", "collection": True}, "check_error"),
    "probe_error": ({"result": "error", "verdict": "passed"}, "check_error"),
    "failure_in_a_passed_run": ({"result": "failed", "verdict": "passed"}, "check_error"),
}
PASSING = {"result": "passed", "verdict": "passed"}
ACTS = {(True, False): "pass", (False, True): "defect"}


def _admission(monkeypatch, found):
    """Learning admission; retained checks and ranking credit read admitted rows only, so they decide through it."""
    monkeypatch.setattr(vs, "reading", lambda ran, node: found)
    binding = repo_tests._learning_binding({}, ["t.py::t"], repository=REPOSITORY, check_digests={})
    body = {"source_before": "d" * 64, "source_after": "d" * 64, "result": {"verdict": found["verdict"]}, **binding}
    return ACTS.get(h._status(body, "t.py::t"), "check_error")


def _legacy_reconstruction(monkeypatch, found):
    """A receipt bound before readings: it keeps neither failure text nor the probe record."""
    body = {"source_before": "d" * 64, "source_after": "d" * 64, "result": {"verdict": found["verdict"]},
            "tests": {"requested": ["t.py::t"], "collected": ["t.py::t"]},
            "test_outcomes": {"t.py::t": {"setup": "passed", "call": found["result"], "teardown": "passed"}}}
    return ACTS.get(h._status(body, "t.py::t"), "check_error")


def _k_control(monkeypatch, on_patch, at_base):
    one = vs.ProposedCheck(id="k", requirement_id=loop.REQUIREMENT, intent="change", file="test_k.py",
                           source="def test_k():\n    pass\n")
    monkeypatch.setattr(loop, "_checks", lambda *a: ([one], {one.id: "test_k.py"}, []))
    monkeypatch.setattr(vs, "reading", lambda ran, node: ran["reading"])
    monkeypatch.setattr(vs, "_guarded", lambda change, ran, found: found)
    monkeypatch.setattr(vs, "_run_check", lambda *a, **kw: {"reading": at_base, "seconds": 0})
    monkeypatch.setattr(h, "record_check", lambda *a, **kw: None)
    change = SimpleNamespace(repo=Path.cwd(), files=[], base_commit="b" * 40)
    activities = loop.record_reproductions(change, Path("unused"), deadline=loop._clock() + 60,
                                           _observed={one.id: {"receipt": "r", "reading": on_patch}})
    return [a["result"] for a in activities]


def _k_control_patch(monkeypatch, found):
    return "defect" if _k_control(monkeypatch, found, PASSING) == ["reproduced"] else "not a defect"


def _k_control_base(monkeypatch, found):
    return "pass" if _k_control(monkeypatch, SHAPES["failure"][0], found) == ["reproduced"] else "not a pass"


def _resolution(monkeypatch, found):
    from g6.harness import resolution

    one = vs.ProposedCheck(id="c", requirement_id="R", intent="preserve", file="test_c.py",
                           source="def test_c():\n    pass\n", test="test_c")
    done, placed = vs.Search(), vs.placement(one)
    vs._found(done, one, vs._activity(one, placed), {**found, "detail": "d", "tree_digest": "t"},
              {**PASSING, "detail": "d", "tree_digest": "t"}, {"result": "counterexample", "detail": "d"},
              "a" * 64, "b" * 40)
    completion = {"defects_reproduced": {"proposed_checks": done.defects}}
    kept = resolution.build({"requirements": [], "completion": completion}, repo="fixture", task="keep")
    return "settled" if kept else "check_error"


#: Every consumer of a reading, with what it does for each class the rule gives: learning admission (which retained
#: checks and ranking credit decide through, as both take admitted rows only), legacy reconstruction, the K control's
#: patch and base runs, and resolution. A consumer that acts on one class only maps every other to its refusal.
CONSUMERS = {
    "evidence_class": (lambda monkeypatch, found: vs.evidence_class(found), lambda cls: cls),
    "learning_admission": (_admission, lambda cls: cls),
    "legacy_reconstruction": (_legacy_reconstruction, lambda cls: "check_error"),
    "k_control_patch": (_k_control_patch, lambda cls: cls if cls == "defect" else "not a defect"),
    "k_control_base": (_k_control_base, lambda cls: cls if cls == "pass" else "not a pass"),
    "resolution": (_resolution, lambda cls: "check_error" if cls == "check_error" else "settled"),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("consumer", sorted(CONSUMERS))
def test_every_consumer_decides_a_reading_by_the_one_rule(monkeypatch, consumer, shape):
    decide, expected = CONSUMERS[consumer]
    found, cls = SHAPES[shape]
    assert vs.evidence_class(found) == cls
    assert decide(monkeypatch, dict(found)) == expected(cls)


def test_a_binding_without_the_collection_qualifier_is_never_a_defect():
    # A bare failure bound before qualifiers cannot say its file was collected; a bare pass is still the reader's.
    body = {"source_before": "d" * 64, "source_after": "d" * 64, "result": {"verdict": "failed"},
            "readings": {"t.py::t": "failed"}}
    assert h._status(body, "t.py::t") == (False, False)
    assert h._status({**body, "readings": {"t.py::t": {"result": "failed"}}}, "t.py::t") == (False, False)
    assert h._status({**body, "result": {"verdict": "passed"}, "readings": {"t.py::t": "passed"}}, "t.py::t") == (
        True, False)


def test_the_ranking_bound_keeps_the_newest_rows_and_says_how_many_were_cut():
    rows = tuple({"method": "review", "engine": "g6_review_patch", "op": "review", "classification": "not_refuted",
                  "artifact_digest": f"{i:064x}", "receipt": f"r{i}"} for i in range(h.MAX_TRIALS + 5))
    history = h.History(REPOSITORY, rows)
    variants = [MethodVariant("review", "review", "pytest", "g6_review_patch", "review", 1, kind="reviewer")]
    assert history.ranked(variants) == rows[5:]   # the newest are ranked
    assert len(history.ranked(variants * 2)) == h.MAX_TRIALS // 2   # each row is a trial per variant
    assert "5 older admitted row(s) were cut" in history.learned(cut=5)["history_cut"]
    assert "history_cut" not in history.learned()
