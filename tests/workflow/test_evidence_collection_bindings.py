"""Collection identities depend on bindings, including local callable bodies.

Written before the follow-up-2 repair. The lead runs these outside the sandbox;
the no-lane override permits static checks only in the builder's session.
"""
from dataclasses import replace

import pytest

from g6.harness import patch_report
from g6.harness.workflow import evidence, record_md
from tests.workflow.kit import event, sha


NODE = "test_case.py::test_case"


def sources(tmp_path, helper, collector="from helper import cases\nREGISTERED = cases()\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_case.py").write_text(
        collector + "from product import calculate\ndef test_case(): assert calculate() == 2\n",
        encoding="utf-8")
    (repo / "helper.py").write_text(helper, encoding="utf-8")
    (repo / "oracle.py").write_text("def touch(i=0): return i\n", encoding="utf-8")
    (repo / "product.py").write_text("def calculate(): return 3\n", encoding="utf-8")
    return repo


def repair_counts(before, after):
    rows = [replace(event(str(n) * 32, "review_finished", seconds=n, data={
        **evidence.outcome_sources({NODE: verdict}, captured),
        ("failed_sha256" if n == 0 else "passed_sha256"): [sha(NODE)]}),
        artifact_sha256=sha(str(n)))
        for n, (verdict, captured) in enumerate((("failed", before), ("passed", after)))]
    held = {**evidence.bundle(rows), "selected_run": rows[-1].run}
    return patch_report.card({"workflow_evidence": held}), record_md.counts(rows)


@pytest.mark.parametrize("body", [
    "def cases():\n    if True:\n        def rank(i): return touch(i)\n"
    "    return sorted([2, 3], key=rank)\n",
    "def cases():\n    if False:\n        pass\n    else:\n"
    "        def rank(i): return touch(i)\n    return (rank,)\n",
    "def cases():\n    class Rank:\n        def __call__(self, i): return touch(i)\n"
    "    return sorted([2, 3], key=[Rank][0]())\n",
], ids=["conditional_true", "conditional_else", "local_class"])
def test_static_local_callables_bind_oracle_and_preserve_product_repairs(tmp_path, body):
    repo = sources(tmp_path, "from oracle import touch\n" + body)
    before = evidence.capture_sources(repo, [NODE])
    assert before[NODE]
    (repo / "product.py").write_text("def calculate(): return 2\n", encoding="utf-8")
    product = evidence.capture_sources(repo, [NODE])
    assert product == before
    card, counts = repair_counts(before, product)
    assert card["repaired"] == counts["repaired"] == 1
    (repo / "oracle.py").write_text("def touch(i=0): return -i\n", encoding="utf-8")
    changed = evidence.capture_sources(repo, [NODE])
    assert changed[NODE] and changed != before
    card, counts = repair_counts(before, changed)
    assert card["repaired"] == counts["repaired"] == 0
    assert card["open"] == counts["open"] == 1


@pytest.mark.parametrize("body", [
    "def cases():\n    if flag:\n        def rank(i): return touch(i)\n    return (rank,)\n",
    "def cases():\n    rank, other = (touch, None)\n    return (rank,)\n",
    "def cases():\n    def rank(i): return touch(i)\n    def inner():\n"
    "        if flag:\n            def rank(i): return i\n        return (rank,)\n    return inner()\n",
    "def cases():\n    class object:\n        def __init__(self): touch()\n"
    "    class Worker(object): pass\n    return (Worker,)\n",
], ids=["conditional_unknown", "unlisted_assignment", "shadowed_outer_local", "local_base"])
def test_unknown_local_reference_withholds_identity_and_repair_credit(tmp_path, body):
    repo = sources(tmp_path, "from oracle import touch\nflag = True\n" + body)
    captured = evidence.capture_sources(repo, [NODE])
    assert captured == {NODE: ""}
    card, counts = repair_counts(captured, captured)
    assert (card["repaired"], card["open"], card["identities_incomplete"]) == (None, None, True)
    assert (counts["repaired"], counts["open"], counts["identities_incomplete"]) == (0, 1, True)


def test_unknown_returned_wrapper_does_not_borrow_test_time_exemption(tmp_path):
    repo = sources(tmp_path,
        "from oracle import touch\nflag = True\ndef decorate(fn):\n    if flag:\n"
        "        def wrapper(): return touch()\n    return wrapper\n",
        "from helper import decorate\n@decorate\ndef test_wrapped(): pass\n")
    assert evidence.capture_sources(repo, [NODE]) == {NODE: ""}


def test_local_decorator_binding_cannot_borrow_module_fixture_exemption(tmp_path):
    repo = sources(tmp_path,
        "from pytest import fixture as register\nfrom oracle import touch\n"
        "def identity(fn): return fn\ndef cases():\n    register = identity\n"
        "    @register\n    def callback(): return touch()\n    return (callback,)\n")
    before = evidence.capture_sources(repo, [NODE])
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert before != after or before == after == {NODE: ""}


@pytest.mark.parametrize("method", [
    "        def __call__(self, i): return rank(i)\n",
    "        def __call__(self, i=rank(0)): return i\n",
], ids=["protocol", "header"])
def test_local_class_closure_does_not_borrow_same_named_global(tmp_path, method):
    repo = sources(tmp_path,
        "from oracle import touch\ndef rank(i): return i\ndef cases():\n"
        "    def rank(i): return touch(i)\n    class Rank:\n"
        + method + "    return (Rank,)\n")
    before = evidence.capture_sources(repo, [NODE])
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert before != after or before == after == {NODE: ""}


@pytest.mark.parametrize("mutation", [
    "pytest.fixture = identity\n",
    "api = pytest\napi.fixture = identity\n",
    "def patch():\n    pytest.fixture = identity\npatch()\n",
    "import mutator\n",
    "pytest.__dict__['fixture'] = identity\n",
    "from pytest import fixture as register\nregister = identity\n",
], ids=["direct", "alias", "collection_function", "imported_mutator", "namespace", "binding"])
def test_rebound_pytest_fixture_has_no_body_exemption(tmp_path, mutation):
    repo = sources(tmp_path,
        "import pytest\nfrom oracle import touch\ndef identity(fn): return fn\n"
        + mutation + "@pytest.fixture\ndef prepare(): return touch()\n",
        "from helper import prepare\nprepare()\n")
    if mutation.startswith("from pytest"):
        (repo / "helper.py").write_text(
            "from oracle import touch\ndef identity(fn): return fn\n" + mutation
            + "@register\ndef prepare(): return touch()\n", encoding="utf-8")
    (repo / "mutator.py").write_text(
        "import pytest\ndef identity(fn): return fn\npytest.fixture = identity\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [NODE])
    # Direct and statically aliased assignments remain resolvable ordinary code.
    if mutation in {"pytest.fixture = identity\n", "api = pytest\napi.fixture = identity\n"}:
        assert before[NODE]
        (repo / "product.py").write_text("def calculate(): return 2\n", encoding="utf-8")
        product = evidence.capture_sources(repo, [NODE])
        assert product == before
        card, counts = repair_counts(before, product)
        assert card["repaired"] == counts["repaired"] == 1
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert before != after or before == after == {NODE: ""}
    card, counts = repair_counts(before, after)
    assert counts["repaired"] == 0 and card["repaired"] in (0, None)


def test_collector_rebinding_invalidates_imported_fixture_exemption(tmp_path):
    repo = sources(tmp_path,
        "import pytest\nfrom oracle import touch\n@pytest.fixture\ndef prepare(): return touch()\n",
        "import pytest\ndef identity(fn): return fn\npytest.fixture = identity\n"
        "from helper import prepare\nREGISTERED = (prepare,)\n")
    before = evidence.capture_sources(repo, [NODE])
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert before != after or before == after == {NODE: ""}


@pytest.mark.parametrize("registration", [
    "import pytest\n@pytest.fixture\n",
    "from pytest import fixture as register\n@register\n",
    "from fixture_api import register\n@register\n",
], ids=["direct", "alias", "reexport"])
def test_genuine_fixture_body_keeps_setup_exemption(tmp_path, registration):
    repo = sources(tmp_path,
        "from oracle import touch\n" + registration + "def prepare(): return touch()\n",
        "from helper import prepare\nREGISTERED = (prepare,)\n")
    (repo / "fixture_api.py").write_text("from pytest import fixture as register\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [NODE])
    assert before[NODE]
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert after == before
    card, counts = repair_counts(before, after)
    assert card["repaired"] == counts["repaired"] == 1


@pytest.mark.parametrize("name", ["object", "type"])
@pytest.mark.parametrize("binding", ["local", "imported"])
def test_shadowed_builtin_base_binds_inherited_protocols(tmp_path, name, binding):
    base = (f"from oracle import touch\nclass {name}:\n"
            "    def __init__(self): touch()\n")
    repo = sources(tmp_path,
        (base if binding == "local" else f"from bases import {name}\n")
        + f"class Worker({name}):\n    def run(self):\n"
        "        from product import calculate\n        return calculate()\n",
        "from helper import Worker\nworker = Worker()\n")
    (repo / "bases.py").write_text(base, encoding="utf-8")
    before = evidence.capture_sources(repo, [NODE])
    assert before[NODE]
    (repo / "product.py").write_text("def calculate(): return 2\n", encoding="utf-8")
    product = evidence.capture_sources(repo, [NODE])
    assert product == before
    card, counts = repair_counts(before, product)
    assert card["repaired"] == counts["repaired"] == 1
    (repo / "oracle.py").write_text("def touch(i=0): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert after[NODE] and after != before
    card, counts = repair_counts(before, after)
    assert card["repaired"] == counts["repaired"] == 0


@pytest.mark.parametrize("name", ["object", "type"])
def test_shadowed_builtin_base_in_collected_mro_binds_fixtures(tmp_path, name):
    repo = sources(tmp_path,
        f"import pytest\nfrom oracle import touch\nclass {name}:\n"
        "    @pytest.fixture(autouse=True, params=touch())\n    def prepared(self): pass\n",
        f"from helper import {name}\nclass TestCase({name}):\n    def test_case(self): pass\n")
    (repo / "oracle.py").write_text("def touch(i=0): return [1]\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [NODE])
    assert before[NODE]
    (repo / "oracle.py").write_text("def touch(i=0): return [2]\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert after[NODE] and after != before


@pytest.mark.parametrize("name", ["object", "type"])
def test_unbound_builtin_base_keeps_ordinary_methods_test_time(tmp_path, name):
    repo = sources(tmp_path,
        f"class Worker({name}):\n    def run(self):\n"
        "        from product import calculate\n        return calculate()\n",
        "from helper import Worker\nREGISTERED = (Worker,)\n")
    before = evidence.capture_sources(repo, [NODE])
    assert before[NODE]
    (repo / "product.py").write_text("def calculate(): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [NODE])
    assert after == before
    card, counts = repair_counts(before, after)
    assert card["repaired"] == counts["repaired"] == 1


@pytest.mark.parametrize("name", ["object", "type"])
def test_ambiguous_builtin_base_binding_is_incomplete(tmp_path, name):
    repo = sources(tmp_path,
        "class Base: pass\n" + f"{name}, other = (Base, None)\nclass Worker({name}): pass\n",
        "from helper import Worker\nREGISTERED = (Worker,)\n")
    assert evidence.capture_sources(repo, [NODE]) == {NODE: ""}
