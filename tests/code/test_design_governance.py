# SPDX-License-Identifier: Apache-2.0
"""g6.code.design_governance: import cycles, layer breaches, pass-through functions and a cycle diff over a file map
the caller gives, for any package; a check that found nothing to check is refused, never read as clean."""
from __future__ import annotations

import json
import sys

import pytest
from pydantic import ValidationError

import g6.code.design_governance as package
from g6.code.design_governance import DesignGovernanceBlock, DesignGovernanceInput, graph
from g6.core.failure_modes import FailureModeRegistry
from g6.mcp import describe_reply, invoke_reply

#: A shop that is not the archive's layout: an api layer over a domain over a store, and a utility module.
SHOP = {
    "shop/__init__.py": '"""The shop."""\n',
    "shop/api/__init__.py": "",
    "shop/api/routes.py": "from shop.domain import cart\nfrom shop import util\n\n\ndef list_items(cart_id):\n"
                          "    return cart.items(cart_id)\n",
    "shop/domain/__init__.py": "",
    "shop/domain/cart.py": "from ..store import db\n\n\ndef items(cart_id):\n    return db.rows('cart', cart_id)\n",
    "shop/store/__init__.py": "",
    "shop/store/db.py": "import sqlite3\n\n\ndef rows(table, key):\n    return []\n",
    "shop/util.py": "def clean(text):\n    return text.strip()\n",
    "README.md": "# Shop\n",
}
CONTRACT = {"layers": ["shop.api", "shop.domain", "shop.store"]}


def run(op: str, **params) -> dict:
    return DesignGovernanceBlock().infer(DesignGovernanceInput(op=op, **params)).unwrap()


def with_change(**files: str) -> dict[str, str]:
    return {**SHOP, **{path.replace("__", "/") + ".py": text for path, text in files.items()}}


# --- D1: a change that adds a cycle or a layer breach is caught, in any codebase --------------------------------------

def test_d1_a_cycle_a_change_introduces_is_caught_with_the_import_that_closed_it() -> None:
    after = with_change(shop__store__db="import sqlite3\nfrom shop.domain import cart\n\n\ndef rows(table, key):\n"
                                        "    return []\n")
    out = run("diff_cycles", before=SHOP, after=after, package="shop")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["severity"] == "red"
    assert [c["modules"] for c in out["introduced"]] == [["shop.domain", "shop.store"]] and out["removed"] == []
    assert out["new_imports_in_cycles"] == [{"from": "shop.store.db", "to": "shop.domain.cart",
                                             "file": "shop/store/db.py", "line": 2}]
    breaches = run("layer_violations", files=after, package="shop", contract=CONTRACT)
    assert [(v["from"], v["to"], v["line"]) for v in breaches["violations"]] == [("shop.store.db",
                                                                                 "shop.domain.cart", 2)]


def test_d1_a_check_that_found_nothing_to_check_is_refused_never_clean() -> None:
    wrong_package = run("cycles", files=SHOP, package="mvp")
    misspelt = run("layer_violations", files=SHOP, package="shop",
                   contract={"layers": ["shop.api", "shop.domian", "shop.store"]})
    no_imports = run("cycles", files={"lone/__init__.py": "", "lone/a.py": "x = 1\n"}, package="lone")
    assert wrong_package["code"] == "G6_E_DESIGN_NO_MODULES"
    assert misspelt["code"] == "G6_E_DESIGN_CONTRACT_UNMATCHED" and "shop.domian" in misspelt["warning_card"]["message"]
    assert no_imports["code"] == "G6_E_DESIGN_NO_EDGES"
    assert {r["completion_state"] for r in (wrong_package, misspelt, no_imports)} == {"blocked-escalated"}


# --- the archive's findings -------------------------------------------------------------------------------------------

def test_design_governance_01_any_codebase_has_edges_and_a_clean_one_is_said_to_be_clean() -> None:
    graph_out = run("import_graph", files=SHOP, package="shop")
    assert {(e["from"], e["to"]) for e in graph_out["edges"]} == {("shop.api", "shop.domain"), ("shop.api", "shop.util"),
                                                                  ("shop.domain", "shop.store")}
    assert graph_out["external_imports"] == 1 and graph_out["not_python"] == ["README.md"]
    clean = run("cycles", files=SHOP, package="shop")
    assert clean["clean"] is True and clean["code"] == "G6_E_DESIGN_NONE_FOUND"
    assert clean["completion_state"] == "qualified-draft" and clean["warning_card"]["verified"] is False
    layered = run("layer_violations", files=SHOP, package="shop", contract=CONTRACT)
    assert layered["clean"] is True and layered["violations"] == []


def test_design_governance_02_a_file_that_does_not_parse_keeps_a_result_from_being_clean() -> None:
    broken = with_change(shop__util="def clean(text:\n")
    out = run("cycles", files=broken, package="shop")
    assert out["code"] == "G6_E_DESIGN_INCOMPLETE" and out["completion_state"] == "blocked-escalated"
    assert out["unparsed"][0]["file"] == "shop/util.py"


def test_design_governance_03_every_lead_claim_is_an_op_over_mcp() -> None:
    reply = invoke_reply("design_governance", "cycles", json.dumps({"files": with_change(
        shop__store__db="from shop.domain import cart\n"), "package": "shop"}))
    assert reply["ok"] and reply["result"]["cycles"][0]["cycle"] == ["shop.domain", "shop.store", "shop.domain"]
    assert sorted(package.block._OPS) == ["cycles", "diff_cycles", "import_graph", "layer_violations", "pass_through"]


def test_design_governance_04_a_deep_graph_needs_no_change_to_the_recursion_limit() -> None:
    limit = sys.getrecursionlimit()
    chain = {f"n{i}": {f"n{i + 1}": []} for i in range(20_000)}
    chain["n20000"] = {"n0": []}
    found = graph.tarjan(sorted(chain), chain)
    assert len(found) == 1 and len(found[0]) == 20_001 and sys.getrecursionlimit() == limit


def test_tarjan_finds_each_component_and_none_in_a_tree() -> None:
    g = {"a": {"b": []}, "b": {"a": [], "c": []}, "c": {"d": []}, "d": {"c": []}, "e": {"a": []}}
    assert graph.tarjan(sorted(g), g) == [["a", "b"], ["c", "d"]]
    assert graph.tarjan(["x", "y"], {"x": {"y": []}, "y": {}}) == []


def test_relative_lazy_and_type_checking_imports_are_edges_and_can_be_left_out() -> None:
    files = {"p/__init__.py": "", "p/a.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from p import b\n",
             "p/b.py": "def f():\n    from . import a\n    return a\n"}
    every = run("cycles", files=files, package="p", depth=2)
    assert every["cycles"][0]["modules"] == ["p.a", "p.b"]
    lazy_only = run("cycles", files=files, package="p", depth=2, include_type_checking=False)
    strict = run("cycles", files=files, package="p", depth=2, include_lazy=False, include_type_checking=False)
    assert lazy_only["code"] == "G6_E_DESIGN_NONE_FOUND" and lazy_only["imports_inside"] == 1
    assert strict["code"] == "G6_E_DESIGN_NO_EDGES"  # nothing left to check is not a clean result


def test_pass_through_finds_forwarders_and_not_functions_that_add_something() -> None:
    files = {"m.py": "def a(x, y):\n    return b(x, y)\n\n\ndef c(x, y):\n    return b(y, x)\n\n\n"
                     "def d(x):\n    '''Doc.'''\n    return b(x=x)\n\n\ndef e(x):\n    x = x + 1\n    return b(x)\n"}
    out = run("pass_through", files=files)
    assert [f["function"] for f in out["functions"]] == ["a", "d"]


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for field in ("scores", "waiver", "approved", "verified", "has_active_waiver"):
        with pytest.raises(ValidationError):
            DesignGovernanceInput(op="cycles", files=SHOP, package="shop", **{field: True})


def test_g4_bounds_and_paths_refuse() -> None:
    with pytest.raises(ValidationError):
        DesignGovernanceInput(op="cycles", package="shop", files={f"shop/m{i}.py": "" for i in range(2001)})
    escaping = run("cycles", files={"../shop/a.py": "import shop.b\n", "shop/b.py": ""}, package="shop")
    absolute = run("cycles", files={"C:/shop/a.py": "", "shop/b.py": ""}, package="shop")
    assert escaping["code"] == absolute["code"] == "G6_E_DESIGN_BAD_PATH"
    assert run("layer_violations", files=SHOP, package="shop")["code"] == "G6_E_DESIGN_INPUT"


def test_g5_no_op_reaches_outside_the_file_map() -> None:
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == ()


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("unmanaged_complexity",)
    assert all(FailureModeRegistry.is_registered(c) for c in package.__g6_mitigates__)
    assert "g6.code.design_governance" not in FailureModeRegistry.planned()


def test_d6_describe_serves_the_skill() -> None:
    assert describe_reply("design_governance")["skill"].startswith("---\nname: g6-design-governance")
