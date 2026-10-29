# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 3: templates (``g6.harness.verification_templates``), the search configuration and test
adequacy.

A template is trusted code: its fixed text is G6's own. An instance is data: the caller names functions and literals in
the repository under review; G6 checks each name against the files of the tree (never importing them) and each literal
as JSON data, refuses anything else before anything runs, and compiles the instance into a test whose only part that
comes from the caller is one literal. Compiled checks run through verification search's executor as proposed checks
do, and never pass a requirement. The structural template reads imports only: against a contract the repository or the
caller states, an import the patch adds that breaks it is a defect. Test adequacy is reported and decides nothing.

Development fixtures only, each from a different kind of application (a library function, a command-line tool's
parser, a web-style handler with an ownership check, a data-pipeline transform, a layered package); none is V9 or V10
evaluation data, and no model is called. Tests that take ``bwrap_backend`` run real confined runs (Linux bubblewrap)
and skip elsewhere; the others replace G6's runs with recorded results, or run a compiled check with plain pytest on
G6's own fixtures."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize
from decimal import Decimal
from pathlib import Path

import pytest

from g6.harness import patch_review, verification_search, verification_templates
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope
from g6.workforce import mutation_check, repo_tests

TEMPLATE_IDS = ("example.v1", "relation.v1", "access_rule.v1", "differential.v1", "forbidden_dependency.v1")

# --- development fixtures: five kinds of application, each with a base commit and a patch that plants a defect --------

# A library function: a text codec. The patch escapes newlines too, and decodes in the wrong order.
CODEC_BASE = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C")\n\n\n'
              'def decode(text):\n    return text.replace("%2C", ",").replace("%25", "%")\n')
CODEC_PATCH = ('def encode(text):\n    return text.replace("%", "%25").replace(",", "%2C").replace("\\n", "%0A")\n\n\n'
               'def decode(text):\n    return text.replace("%25", "%").replace("%2C", ",").replace("%0A", "\\n")\n')
CODEC_TESTS = 'from textcodec import decode, encode\n\n\ndef test_plain():\n    assert decode(encode("plain")) == "plain"\n'
CODEC = {"textcodec.py": CODEC_BASE, "test_textcodec.py": CODEC_TESTS}

# A command-line tool's parser. The patch adds --verbose, and changes the default count.
CLI_BASE = ('import argparse\n\n\ndef parse_args(argv):\n    parser = argparse.ArgumentParser(prog="count")\n'
            '    parser.add_argument("--count", type=int, default=1)\n    return vars(parser.parse_args(argv))\n')
CLI_PATCH = ('import argparse\n\n\ndef parse_args(argv):\n    parser = argparse.ArgumentParser(prog="count")\n'
             '    parser.add_argument("--count", type=int, default=0)\n'
             '    parser.add_argument("--verbose", action="store_true")\n    return vars(parser.parse_args(argv))\n')
CLI_TESTS = 'from cli import parse_args\n\n\ndef test_count():\n    assert parse_args(["--count", "3"])["count"] == 3\n'
CLI = {"cli.py": CLI_BASE, "test_cli.py": CLI_TESTS}

# A web-style handler with an ownership check. The patch, meant to let editors in, drops the owner check.
NOTES_BASE = ('class Forbidden(Exception):\n    pass\n\n\ndef update_note(user, note, text):\n'
              '    if user != note["owner"]:\n        raise Forbidden(user)\n    return {**note, "text": text}\n')
NOTES_PATCH = ('class Forbidden(Exception):\n    pass\n\n\ndef update_note(user, note, text):\n'
               '    if not user:\n        raise Forbidden(user)\n    return {**note, "text": text}\n')
NOTES_TESTS = ('from notes import update_note\n\n\ndef test_owner():\n'
               '    assert update_note("ann", {"owner": "ann"}, "x")["text"] == "x"\n')
NOTES = {"notes.py": NOTES_BASE, "test_notes.py": NOTES_TESTS}

# A data-pipeline transform. The patch dedupes by name ignoring case, and rounds amounts to one place, outside its scope.
PIPE_BASE = ('def normalise(row):\n    return {"name": row["name"].strip().lower(), "amount": round(row["amount"], 2)}\n'
             '\n\ndef dedupe(rows):\n    seen, out = set(), []\n    for row in rows:\n        if row["name"] not in seen:\n'
             '            seen.add(row["name"])\n            out.append(row)\n    return out\n')
PIPE_PATCH = ('def normalise(row):\n    return {"name": row["name"].strip().lower(), "amount": round(row["amount"], 1)}\n'
              '\n\ndef dedupe(rows):\n    seen, out = set(), []\n    for row in rows:\n'
              '        if row["name"].lower() not in seen:\n            seen.add(row["name"].lower())\n'
              '            out.append(row)\n    return out\n')
PIPE_TESTS = ('from pipeline import dedupe\n\n\ndef test_dedupe():\n'
              '    assert dedupe([{"name": "a"}, {"name": "a"}]) == [{"name": "a"}]\n')
PIPE = {"pipeline.py": PIPE_BASE, "test_pipeline.py": PIPE_TESTS}

# A layered package with its own import-linter contract. The patch makes the data layer import the api layer.
LAYERS_TOML = ('[tool.importlinter]\nroot_package = "shop"\n\n[[tool.importlinter.contracts]]\nname = "shop layers"\n'
               'type = "layers"\nlayers = ["shop.api", "shop.service", "shop.data"]\n')
SHOP = {"shop/__init__.py": "", "shop/api.py": "from shop import service\n\n\ndef handle(x):\n    return service.run(x)\n",
        "shop/service.py": "from shop import data\n\n\ndef run(x):\n    return data.load(x)\n",
        "shop/data.py": "def load(x):\n    return x\n",
        "tests/test_shop.py": "from shop.api import handle\n\n\ndef test_handle():\n    assert handle(1) == 1\n"}
DATA_IMPORTS_API = "def load(x):\n    from shop import api\n    return x\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str], name: str = "repo") -> Path:
    """A git repository whose base commit holds ``files``."""
    root = tmp_path / name
    root.mkdir(parents=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return root


def _patch(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def instance(ident, template, bindings, intent="preserve", requirement="R1"):
    return {"id": ident, "template": template, "requirement_id": requirement, "intent": intent, "bindings": bindings}


ROUND_TRIP = instance("t-codec", "relation.v1", {"relation": "round_trip", "function": "textcodec.encode",
                                                 "inverse": "textcodec.decode",
                                                 "inputs": ["plain", "a,b", "100%", "%2C", "x\ny"]})
CLI_EXAMPLE = instance("t-cli", "example.v1", {"function": "cli.parse_args", "cases": [
    {"args": [["--verbose"]], "returns": {"count": 1, "verbose": True}},
    {"args": [["--count", "3"]], "returns": {"count": 3, "verbose": False}},
    {"args": [["--count", "x"]], "raises": "SystemExit"}]}, intent="change")
OWNER_RULE = instance("t-owner", "access_rule.v1", {"operation": "notes.update_note", "refusal": "exception",
                                                    "exception": "notes.Forbidden", "cases": [
    {"who": "the owner", "resource": "ann's note", "args": ["ann", {"owner": "ann"}, "x"], "expected": "allowed"},
    {"who": "a stranger", "resource": "ann's note", "args": ["bob", {"owner": "ann"}, "x"], "expected": "refused"}]})
KEEP_NORMALISE = instance("t-normalise", "differential.v1", {"function": "pipeline.normalise", "mode": "outputs", "calls": [
    {"args": [{"name": " Ann ", "amount": 1.234}]}, {"args": [{"name": "Bo", "amount": 2.0}]}]})
SHOP_LAYERS = instance("t-layers", "forbidden_dependency.v1", {"contract": "repository"})
STATED = {"CODEC": [{"id": "R1", "text": "text survives encoding", "checks": ["test_textcodec.py::test_plain"]}],
          "CLI": [{"id": "R1", "text": "the parser reads --count and --verbose", "checks": ["test_cli.py::test_count"]}],
          "NOTES": [{"id": "R1", "text": "only the owner updates a note", "checks": ["test_notes.py::test_owner"]}],
          "PIPE": [{"id": "R1", "text": "dedupe ignores case", "checks": ["test_pipeline.py::test_dedupe"]}],
          "SHOP": [{"id": "R1", "text": "the layers hold", "checks": ["tests/test_shop.py::test_handle"]}]}


def review(repo, requirements, **kwargs):
    return review_patch(repo, task="Make the change the requirements state.", requirements=requirements, **kwargs)


def result(passed=(), failed=(), *, verdict=None, output="", seconds=0.25, digest="", messages=None):
    """One recorded ``repo_tests`` run result (as ``tests/harness/test_proposed_checks.py`` records them)."""
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": 0,
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": output,
            "seconds": seconds, "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": "", "rootdir": "",
            **({"source_before": digest} if digest else {}), **({"failure_messages": messages} if messages else {})}


class Tiers:
    """Stands in for the review's own runs: every in-place run passes what it was asked; the stated checks at the base
    commit did not run."""

    def __init__(self, monkeypatch, failing=()):
        self.calls: list[tuple[str, ...]] = []

        def execute(root, ids, *, timeout_s, change=None, **_):
            self.calls.append(tuple(ids))
            bad = [i for i in ids if i in failing]
            return result([i for i in ids if i not in bad], bad, output="".join(f"FAILED {i} - AssertionError\n"
                                                                            for i in bad))

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base",
                            lambda change, ids, *, timeout_s, **_: {"verdict": "not_run", "said": "not recorded"})


def _observed(node, observations) -> dict:
    return result([node], output="".join(f"G6-OBSERVATION {json.dumps(o)}\n" for o in observations))


class Compiled:
    """Stands in for each compiled check's run (``verification_search._run_check``): ``outcomes`` maps a template id
    to what its run on the patch and at the base commit gives ("passed", "failed", a list of observations, or a
    recorded result). Records each run asked for; ``advance`` seconds pass on G6's clock with each run."""

    def __init__(self, monkeypatch, outcomes, *, advance=0.0):
        self.calls: list[tuple[str, bool]] = []
        self.roots: list[tuple[str, ...]] = []   # the import roots each run was given
        clock = [verification_search._clock()]
        monkeypatch.setattr(verification_search, "_clock", lambda: clock[0])
        self.clock = clock

        def run_check(change, placed, source, node, *, at_base, timeout_s, **given):
            found = re.search(r"^TEMPLATE = '([^']+)'$", source, re.MULTILINE)
            key = found.group(1) if found else source
            self.calls.append((key, at_base))
            self.roots.append(tuple(given.get("roots", ())))
            clock[0] += advance
            spec = outcomes[key]["base" if at_base else "patch"]
            digest = ("b" if at_base else "p") * 64
            if callable(spec):
                return spec(node)
            if isinstance(spec, dict):
                return spec
            if isinstance(spec, list):
                return {**_observed(node, spec), "source_before": digest}
            if spec == "passed":
                return result([node], digest=digest)
            return result(failed=[node], output=f"FAILED {node} - Failed: case 0: returned 1, expected 2\n",
                          messages={node: "Failed: case 0: returned 1, expected 2"}, digest=digest)

        monkeypatch.setattr(verification_search, "_run_check", run_check)


def _codes(got) -> set[str]:
    return {reason["code"] for reason in got["assessment"]["reasons"]}


def _requirement(got, ident) -> dict:
    return next(r for r in got["assessment"]["requirements"] if r["id"] == ident)


def _activity(got, ident) -> dict:
    return next(a for a in got["assessment"]["completion"]["activities"] if a.get("check") == ident)


def _refused(got, code="G6_E_INPUT_VALIDATION") -> None:
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated", got
    assert got["warning_card"]["code"] == code and check_envelope(got) == []
    assert "assessment" not in got and "merge" not in got


# --- the registry ------------------------------------------------------------------------------------------------------

def test_the_registry_holds_the_five_templates_with_their_families_obligations_and_assumptions():
    families = {"example.v1": "requirement_satisfaction", "relation.v1": "requirement_satisfaction",
                "access_rule.v1": "requirement_satisfaction", "differential.v1": "regression_preservation",
                "forbidden_dependency.v1": "safety_structural"}
    assert set(verification_templates.TEMPLATES) == set(TEMPLATE_IDS)
    for ident, family in families.items():
        definition = verification_templates.TEMPLATES[ident]
        assert (definition.id, definition.family) == (ident, family)
        assert definition.obligation and definition.assumptions and definition.bindings is not None
        assert re.fullmatch(r"[a-z_]+\.v[0-9]+", definition.id)
    # four compile to a test from a fixed text; the structural one runs no code
    assert verification_templates.TEMPLATES["forbidden_dependency.v1"].text == ""
    assert all(verification_templates.TEMPLATES[i].text for i in TEMPLATE_IDS[:4])


# --- refusals before anything runs --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "os.system('x')", "__import__", "builtins.__import__", "cli.parse_args\nimport os", "cli.parse_args;import os",
    "cli.parse_args()", "cli", "cli..parse_args", ".cli.parse_args", "cli.parse_args ", "cli.class", "cli.__dict__",
    "c\u00e4li.parse_args", "cli.parse_args#x", "", 123, None, ["cli", "parse_args"],
])
def test_a_binding_with_code_in_a_name_refuses_the_review_before_anything_runs(tmp_path, monkeypatch, name):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers, runs = Tiers(monkeypatch), Compiled(monkeypatch, {})
    bad = instance("t1", "example.v1", {"function": name, "cases": [{"args": [[]], "returns": {}}]}, intent="change")
    got = review(root, STATED["CLI"], template_instances=[bad])
    _refused(got)
    assert "function" in got["warning_card"]["message"]
    assert tiers.calls == [] and runs.calls == []


@pytest.mark.parametrize("name", ["ValueError('x')", "NotAnError", "os.system", "builtins.ValueError ", "len"])
def test_an_exception_binding_must_be_a_builtin_exception_or_a_dotted_name(tmp_path, monkeypatch, name):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers = Tiers(monkeypatch)
    bad = instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [{"args": [[]], "raises": name}]},
                   intent="change")
    got = review(root, STATED["CLI"], template_instances=[bad])
    if name == "os.system":   # a dotted name is checked against the tree, where os is not a module: not applicable
        assert got["ok"] is True and _activity(got, "t1")["result"] == "refused"
    else:
        _refused(got)
        assert tiers.calls == []


class _Int(int):
    pass


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, b"x", float("nan"), float("inf"), object(), {1: "a"}, _Int(3),
                                   Decimal("1.5"), Path("x"), [[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]],
                                   "x" * (64 * 1024)])
def test_a_literal_that_is_not_json_data_refuses_the_review(tmp_path, monkeypatch, value):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers = Tiers(monkeypatch)
    bad = instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [{"args": [value], "returns": 1}]},
                   intent="change")
    got = review(root, STATED["CLI"], template_instances=[bad])
    _refused(got)
    assert "JSON" in got["warning_card"]["message"] or "characters" in got["warning_card"]["message"]
    assert tiers.calls == []


def _case(**fields):
    return {"args": [[]], **fields}


@pytest.mark.parametrize("items, said", [
    ([{k: v for k, v in CLI_EXAMPLE.items() if k != "intent"}], "intent"),
    ([{**CLI_EXAMPLE, "intent": "keep"}], "intent"),
    ([{**CLI_EXAMPLE, "template": "example.v9"}], "template"),
    ([{**CLI_EXAMPLE, "template": "state_invariant.v1"}], "template"),
    ([{**CLI_EXAMPLE, "weight": 2}], "weight"),
    ([{**CLI_EXAMPLE, "provenance": "stated"}], "provenance"),
    ([{**CLI_EXAMPLE, "requirement_id": "R9"}], "R9"),
    ([{**CLI_EXAMPLE, "bindings": "cli.parse_args"}], "bindings"),
    ([CLI_EXAMPLE, {**CLI_EXAMPLE, "intent": "preserve"}], "unique"),
    ([{**CLI_EXAMPLE, "id": f"t{i}"} for i in range(21)], "20"),
    ([instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [_case(returns=1)] * 33})], "32"),
    ([instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [_case(returns=1, raises="ValueError")]})],
     "one of"),
    ([instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [_case()]})], "one of"),
    ([instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [{"args": [], "kwargs": {"no t": 1},
                                                                             "returns": 1}]})], "kwargs"),
    ([instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [_case(returns=1)], "extra": 1})], "extra"),
    ([instance("t1", "relation.v1", {"relation": "round_trip", "function": "textcodec.encode", "inputs": ["a"]})],
     "inverse"),
    ([instance("t1", "relation.v1", {"relation": "idempotence", "function": "textcodec.encode",
                                     "inverse": "textcodec.decode", "inputs": ["a"]})], "inverse"),
    ([instance("t1", "relation.v1", {"relation": "monotonicity", "function": "cli.parse_args", "inputs": [3, 1]})],
     "ascending"),
    ([instance("t1", "relation.v1", {"relation": "monotonicity", "function": "cli.parse_args", "inputs": [1, "a"]})],
     "ascending"),
    ([instance("t1", "relation.v1", {"relation": "symmetry", "function": "cli.parse_args", "inputs": [1]})], "relation"),
    ([instance("t1", "access_rule.v1", {"operation": "notes.update_note", "refusal": "falsy_return", "cases": [
        {"args": [], "expected": "allowed", "who": "a", "resource": "r"}]})], "refused"),
    ([instance("t1", "access_rule.v1", {"operation": "notes.update_note", "refusal": "exception", "cases": [
        {"args": [], "expected": "allowed", "who": "a", "resource": "r"},
        {"args": [], "expected": "refused", "who": "b", "resource": "r"}]})], "exception"),
    ([instance("t1", "differential.v1", {"function": "pipeline.normalise", "mode": "everything", "calls": [{}]})],
     "mode"),
    ([instance("t1", "differential.v1", {"function": "pipeline.normalise", "mode": "outputs",
                                         "calls": [{"args": ["x" * 400]}]})], "characters"),
    ([instance("t1", "forbidden_dependency.v1", {"contract": "repository"}, intent="change")], "preserve"),
    ([instance("t1", "forbidden_dependency.v1", {"contract": {"layers": ["shop.api"]}})], "package"),
    ([instance("t1", "forbidden_dependency.v1", {"package": "shop", "contract": {}})], "layers"),
    ([instance("t1", "forbidden_dependency.v1", {"package": "shop", "contract": {"layers": ["shop.api; x"]}})],
     "dotted"),
    ("cli.parse_args", "list"),
])
def test_an_invalid_template_instance_refuses_the_review_before_anything_runs(tmp_path, monkeypatch, items, said):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers, runs = Tiers(monkeypatch), Compiled(monkeypatch, {})
    got = review(root, STATED["CLI"], template_instances=items)
    _refused(got)
    assert said in got["warning_card"]["message"], got["warning_card"]["message"]
    assert tiers.calls == [] and runs.calls == []


def test_template_instances_and_proposed_checks_share_the_limit_of_twenty_and_their_ids(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers = Tiers(monkeypatch)
    proposed = [{"id": f"p{i}", "requirement_id": "R1", "intent": "change", "file": f"test_p{i}.py",
                 "source": "def test_a():\n    pass\n"} for i in range(15)]
    got = review(root, STATED["CLI"], proposed_checks=proposed,
                 template_instances=[{**CLI_EXAMPLE, "id": f"t{i}"} for i in range(6)])
    _refused(got)
    assert "20" in got["warning_card"]["message"]
    got = review(root, STATED["CLI"], proposed_checks=proposed[:1], template_instances=[{**CLI_EXAMPLE, "id": "p0"}])
    _refused(got)
    assert "unique" in got["warning_card"]["message"]
    assert tiers.calls == []


@pytest.mark.parametrize("value, said", [({"proposer": True}, "proposer"), ({"templates": "yes"}, "templates"),
                                         ({"test_adequacy": 1}, "test_adequacy"), ("templates", "search")])
def test_an_invalid_search_configuration_refuses_the_review(tmp_path, monkeypatch, value, said):
    root = _patch(_repo(tmp_path, CLI), {"cli.py": CLI_PATCH})
    tiers = Tiers(monkeypatch)
    got = review(root, STATED["CLI"], search=value)
    _refused(got)
    assert said in got["warning_card"]["message"]
    assert tiers.calls == []


# --- applicability: an instance that does not apply is refused and reported, and the review goes on -------------------

@pytest.mark.parametrize("bad, said", [
    (instance("t1", "example.v1", {"function": "nomodule.parse_args", "cases": [_case(returns=1)]}, "change"),
     "no module"),
    (instance("t1", "example.v1", {"function": "cli.parse", "cases": [_case(returns=1)]}, "change"), "no top-level"),
    (instance("t1", "example.v1", {"function": "cli.argparse.ArgumentParser", "cases": [_case(returns=1)]}, "change"),
     "no module"),
    (instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [{"args": [1, 2], "returns": 1}]}, "change"),
     "arguments"),
    (instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [{"kwargs": {"args": 1}, "returns": 1}]},
              "change"), "arguments"),
    (instance("t1", "example.v1", {"function": "cli.parse_args", "cases": [_case(raises="cli.NoSuchError")]},
              "change"), "no top-level"),
    (instance("t1", "relation.v1", {"relation": "idempotence", "function": "cli.two", "inputs": [1]}, "change"),
     "arguments"),
    (instance("t1", "example.v1", {"function": "cli.added", "cases": [{"args": [], "returns": 1}]}, "preserve"),
     "base commit"),
    (instance("t1", "differential.v1", {"function": "cli.added", "mode": "outputs", "calls": [{}]}, "preserve"),
     "base commit"),
    (instance("t1", "example.v1", {"function": "cli.removed", "cases": [{"args": [], "returns": 1}]}, "change"),
     "working tree"),
    (instance("t1", "example.v1", {"function": "cli.later", "cases": [{"args": [], "returns": 1}]}, "change"),
     "asynchronous"),
    (instance("t1", "example.v1", {"function": "star.anything", "cases": [{"args": [], "returns": 1}]}, "change"),
     "star"),
    (instance("t1", "forbidden_dependency.v1", {"contract": "repository"}), "contract"),
    (instance("t1", "forbidden_dependency.v1", {"package": "nopackage", "contract": {"layers": ["nopackage.a"]}}),
     "package"),
])
def test_a_template_that_does_not_apply_is_refused_and_reported_and_never_errors_the_review(tmp_path, monkeypatch,
                                                                                           bad, said):
    base = {**CLI, "cli.py": CLI_BASE + "\n\ndef removed():\n    return 1\n", "star.py": "from cli import *\n"}
    root = _repo(tmp_path, base)
    _patch(root, {"cli.py": CLI_PATCH + "\n\ndef two(a, b):\n    return a\n\n\ndef added():\n    return 1\n"
                                        "\n\nasync def later():\n    return 1\n"})
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {})
    got = review(root, STATED["CLI"], template_instances=[bad])
    assert got["ok"] is True and got["completion_state"] == "qualified-draft", got
    # the owner's ruling R-C1: a structural check never passes a requirement, so one that cannot be applied leaves the
    # requirement as its stated checks left it, and says so in an unknown; a compiled check never decides one either
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    if bad["template"] == "forbidden_dependency.v1":
        assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
        assert [c["provenance"] for c in _requirement(got, "R1")["checks"]] == ["stated"]
        assert any("t1" in u and "could not be applied" in u for u in got["assessment"]["unknowns"])
    found = _activity(got, "t1")
    assert (found["activity"], found["result"], found["template"]) == ("template instance", "refused", bad["template"])
    assert said in found["reason"], found["reason"]
    assert found["model_calls"] == 0 and found["seconds"] == 0.0
    assert runs.calls == []
    assert "proposed_checks" not in _requirement(got, "R1")


# --- the compiler: generated code comes only from the template's fixed text ----------------------------------------------

HOSTILE = ["'); import os; os.system('touch /tmp/g6-pwned') #", "\"\"\"\nimport os\n\"\"\"", "\\x00\u202e\u0000",
           "{BINDINGS}", "__import__('os').system('x')"]


def _bindings_segment(source: str) -> str:
    """The text of the one literal the compiled check assigns to ``BINDINGS``."""
    [node] = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
              and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["BINDINGS"]]
    return ast.get_source_segment(source, node.value)


def _prepared(tmp_path, files, patch, items, name="repo"):
    root = _patch(_repo(tmp_path, files, name), patch)
    change = repo_tests.change(root)
    return verification_templates.prepare(root, change, verification_templates.template_instances(items, {"R1"}))


COMPILED = {
    "example.v1": (CLI, {"cli.py": CLI_PATCH}, lambda strings: instance("t", "example.v1", {
        "function": "cli.parse_args", "cases": [{"args": [strings], "returns": {s: s for s in strings}},
                                                {"args": [strings[:1]], "raises": "SystemExit"},
                                                {"args": [list(strings)], "effect": {"argument": 0, "equals": strings}}]},
        intent="change")),
    "relation.v1": (CODEC, {"textcodec.py": CODEC_PATCH}, lambda strings: instance("t", "relation.v1", {
        "relation": "round_trip", "function": "textcodec.encode", "inverse": "textcodec.decode", "inputs": strings})),
    "access_rule.v1": (NOTES, {"notes.py": NOTES_PATCH}, lambda strings: instance("t", "access_rule.v1", {
        "operation": "notes.update_note", "refusal": "exception", "exception": "notes.Forbidden", "cases": [
            {"who": strings[0][:90], "resource": "r", "args": [strings[1], {"owner": strings[2]}, "x"],
             "expected": "allowed"},
            {"who": "b", "resource": strings[3][:90], "args": ["b", {"owner": "a"}, strings[4]],
             "expected": "refused"}]})),
    "differential.v1": (PIPE, {"pipeline.py": PIPE_PATCH}, lambda strings: instance("t", "differential.v1", {
        "function": "pipeline.normalise", "mode": "outputs",
        "calls": [{"args": [{"name": s[:60], "amount": 1}]} for s in strings]})),
}


@pytest.mark.parametrize("template", list(COMPILED))
def test_a_compiled_check_holds_the_callers_text_only_inside_its_one_literal(tmp_path, template):
    files, patch, build = COMPILED[template]
    plain = _prepared(tmp_path, files, patch, [build(["a", "b", "c", "d", "e"])], "plain")
    hostile = _prepared(tmp_path, files, patch, [build(HOSTILE)], "hostile")
    [one], [other] = plain.checks, hostile.checks
    for check in (one, other):
        source = check.source
        assert source.isascii() and len(source.encode("utf-8")) <= verification_search.MAX_SOURCE_BYTES
        assert f"\nTEMPLATE = {template!r}\n" in source
        compile(source, "<compiled>", "exec")
    segment = _bindings_segment(other.source)
    # the literal is a literal: no name, call or operator in it
    data = ast.literal_eval(segment)
    assert isinstance(data, dict)
    # everything but the literal is the template's fixed text, whatever the bindings
    assert other.source.replace(segment, "BINDINGS_LITERAL") == one.source.replace(_bindings_segment(one.source),
                                                                                    "BINDINGS_LITERAL")
    assert other.source.replace(segment, "") == verification_templates.TEMPLATES[template].text.replace(
        verification_templates.SLOT, "")
    rest = other.source.replace(segment, "")
    for text in HOSTILE:
        assert text not in rest and text[:20] not in rest
    for word in ("touch", "pwned", "system", "__import__"):
        assert word not in rest
    # the names reach the check as text in the literal, never as source
    tokens = [t for t in tokenize.generate_tokens(io.StringIO(rest).readline) if t.type == tokenize.NAME]
    names = {t.string for t in tokens}
    assert not names & {"cli", "parse_args", "textcodec", "encode", "decode", "notes", "update_note", "Forbidden",
                        "pipeline", "normalise"}


def test_the_template_id_and_version_enter_every_digest_that_cites_an_instance(tmp_path, monkeypatch):
    first = _prepared(tmp_path, CLI, {"cli.py": CLI_PATCH}, [CLI_EXAMPLE], "one")
    renamed = verification_templates.TEMPLATES["example.v1"]
    v2 = verification_templates.TemplateDefinition(**{**renamed.__dict__, "id": "example.v2",
                                                      "text": renamed.text.replace("'example.v1'", "'example.v2'")})
    monkeypatch.setitem(verification_templates.TEMPLATES, "example.v2", v2)
    second = _prepared(tmp_path, CLI, {"cli.py": CLI_PATCH}, [{**CLI_EXAMPLE, "template": "example.v2"}], "two")
    [a], [b] = first.checks, second.checks
    assert _bindings_segment(a.source) == _bindings_segment(b.source)
    assert hashlib.sha256(a.source.encode()).hexdigest() != hashlib.sha256(b.source.encode()).hexdigest()
    assert first.origins["t-cli"].about["instance_sha256"] != second.origins["t-cli"].about["instance_sha256"]
    assert first.origins["t-cli"].about["template"] == "example.v1"
    same = verification_templates.instance_digest(verification_templates.template_instances([CLI_EXAMPLE], {"R1"})[0])
    assert same == first.origins["t-cli"].about["instance_sha256"]


# --- the compiled checks do what their templates say (plain pytest on G6's own fixtures, unconfined) ---------------------

def _plain_run(tree: Path, placed: str, source: str) -> subprocess.CompletedProcess:
    (tree / placed).parent.mkdir(parents=True, exist_ok=True)
    (tree / placed).write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TMP", "TEMP", "HOME", "USERPROFILE")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir=.", placed],
                          cwd=tree, env={**env, "PYTHONPATH": str(tree)}, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("files, patch, item", [
    (CODEC, {"textcodec.py": CODEC_PATCH}, ROUND_TRIP),
    (NOTES, {"notes.py": NOTES_PATCH}, OWNER_RULE),
    (CLI, {"cli.py": CLI_PATCH}, instance("t-keep", "example.v1", {"function": "cli.parse_args", "cases": [
        {"args": [[]], "returns": {"count": 1}}, {"args": [["--count", "x"]], "raises": "SystemExit"}]})),
], ids=["relation", "access_rule", "example"])
def test_each_compiled_check_passes_on_the_base_and_fails_on_the_planted_defect(tmp_path, files, patch, item):
    prepared = _prepared(tmp_path, files, {}, [item], "base")
    [check] = prepared.checks
    base = _plain_run(tmp_path / "base", prepared.placed[item["id"]], check.source)
    assert base.returncode == 0, base.stdout + base.stderr
    patched = _patch(tmp_path / "base", patch)
    after = _plain_run(patched, prepared.placed[item["id"]], check.source)
    assert after.returncode == 1 and "1 failed" in after.stdout, after.stdout + after.stderr


def test_a_compiled_differential_probe_observes_each_call_and_the_planted_change(tmp_path):
    item = instance("t-normalise", "differential.v1", {"function": "pipeline.normalise", "mode": "outputs", "calls": [
        {"args": [{"name": " Ann ", "amount": 1.234}]}, {"args": [{"name": None, "amount": 1}]}]})
    exceptions = instance("t-raises", "differential.v1", {"function": "pipeline.normalise", "mode": "exceptions",
                                                          "calls": [{"args": [{"name": None, "amount": 1}]}]})
    prepared = _prepared(tmp_path, PIPE, {}, [item, exceptions], "base")
    seen = {}
    for side in ("base", "patch"):
        if side == "patch":
            _patch(tmp_path / "base", {"pipeline.py": PIPE_PATCH})
        for check in prepared.checks:
            ran = _plain_run(tmp_path / "base", prepared.placed[check.id], check.source)
            assert ran.returncode == 0, ran.stdout + ran.stderr
            found = verification_search.reading({**result([prepared.placed[check.id]]), "output": ran.stdout},
                                                prepared.placed[check.id])
            seen[side, check.id] = found["observations"]
    assert seen["base", "t-normalise"][0] == {"input": {"args": [{"name": " Ann ", "amount": 1.234}], "kwargs": {}},
                                              "output": {"returned": {"amount": 1.23, "name": "ann"}}}
    assert seen["base", "t-normalise"][1]["output"] == {"raised": "AttributeError"}
    assert seen["patch", "t-normalise"][0]["output"] == {"returned": {"amount": 1.2, "name": "ann"}}
    assert seen["base", "t-raises"] == seen["patch", "t-raises"] == [
        {"input": {"args": [{"amount": 1, "name": None}], "kwargs": {}}, "output": {"raised": "AttributeError"}}]


# --- I5 and I6: differential observations in a canonical form, and stable across two runs of the base commit -----------

TAGS = ("from collections import defaultdict\n\n\n"
        "def labels():\n    return list({'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'})\n\n\n"
        "def by_initial(words):\n    groups = defaultdict(list)\n    for word in words:\n"
        "        groups[word[0]].append(word)\n    return groups\n\n\n"
        "def count(words):\n    return len(words)\n")
# the patch fixes count and builds each group from the words in sorted order: every group, and the mapping as ==
# sees it, are as they were; only the order the mapping's keys were first seen in changes
TAGS_PATCH = TAGS.replace("    for word in words:\n", "    for word in sorted(words):\n").replace(
    "return len(words)", "return len(set(words))")
MONEY = ("class Money:\n    def __init__(self, cents):\n        self.cents = cents\n\n"
         "    def __eq__(self, other):\n        return isinstance(other, Money) and other.cents == self.cents\n"
         "\n\ndef parse(text):\n    whole, _, part = text.partition('.')\n"
         "    return Money(int(whole) * 100 + int(part or 0))\n")
MONEY_REPR = MONEY.replace("    def __eq__", "    def __repr__(self):\n        return f'Money({self.cents // 100}."
                                             "{self.cents % 100:02d})'\n\n    def __eq__")
MONEY_WRONG = MONEY.replace("int(whole) * 100", "int(whole) * 10")
BY_INITIAL = instance("keep", "differential.v1", {"function": "tags.by_initial", "mode": "outputs",
                                                  "calls": [{"args": [["beta", "alpha", "bravo", "apple"]]}]})
KEEP_PARSE = instance("keep", "differential.v1", {"function": "money.parse", "mode": "outputs",
                                                  "calls": [{"args": ["1.50"]}, {"args": ["2"]}]})


@pytest.mark.parametrize("files, patch, item, differ", [
    ({"tags.py": TAGS}, {"tags.py": TAGS_PATCH}, BY_INITIAL, False),
    ({"money.py": MONEY}, {"money.py": MONEY_REPR}, KEEP_PARSE, False),
    ({"money.py": MONEY}, {"money.py": MONEY_WRONG}, KEEP_PARSE, True),
], ids=["defaultdict-key-order", "an-added-repr", "a-real-output-change"])
def test_review_i5_the_differential_probe_observes_a_canonical_form_never_a_repr(tmp_path, files, patch, item, differ):
    """The reviewer's defaultdict and added __repr__ cases: a mapping is its items sorted, another object its type's
    qualified name and its attributes, so neither is a difference; a real change of the output still is."""
    prepared = _prepared(tmp_path, files, {}, [item])
    [check] = prepared.checks
    seen = []
    for side in ("base", "patch"):
        if side == "patch":
            _patch(tmp_path / "repo", patch)
        ran = _plain_run(tmp_path / "repo", prepared.placed["keep"], check.source)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        seen.append(verification_search.reading({**result([prepared.placed["keep"]]), "output": ran.stdout},
                                                prepared.placed["keep"])["observations"])
    assert bool(verification_search._differences(*seen)) is differ, seen
    if item is KEEP_PARSE:
        assert seen[0][0]["output"]["returned"] == {"type": "money.Money", "attributes": {"cents": 150}}


def test_review_i6_an_observation_the_base_commit_gives_differently_twice_is_unstable_never_a_counterexample(
        tmp_path, monkeypatch):
    """The reviewer's list made from a set: its order follows Python's hash seed, so it differs between any two runs.
    G6 observes the base commit again when it sees a difference; an input observed differently there is unstable."""
    root = _patch(_repo(tmp_path, {"tags.py": TAGS, "tests/test_tags.py": "from tags import count\n\n\n"
                                   "def test_count():\n    assert count(['a', 'b']) == 2\n"}), {"tags.py": TAGS_PATCH})
    Tiers(monkeypatch)
    orders = iter([["alpha", "beta"], ["beta", "alpha"], ["alpha", "beta"]])
    call = {"args": [], "kwargs": {}}
    observed = lambda node: _observed(node, [{"input": call, "output": {"returned": next(orders)}}])  # noqa: E731
    runs = Compiled(monkeypatch, {"differential.v1": {"patch": observed, "base": observed}})
    rule = [{"id": "R1", "text": "count counts distinct words", "checks": ["tests/test_tags.py::test_count"]}]
    got = review(root, rule, template_instances=[instance("keep", "differential.v1", {
        "function": "tags.labels", "mode": "outputs", "calls": [{"args": []}]})])
    found = _activity(got, "keep")
    assert found["result"] == "not_refuted" and found["unstable"], found
    assert "unstable" in found["detail"]
    assert runs.calls == [("differential.v1", False), ("differential.v1", True), ("differential.v1", True)]
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"] == []


def test_review_i6_a_difference_the_base_commit_keeps_on_its_second_run_is_still_a_counterexample(tmp_path,
                                                                                                    monkeypatch):
    root = _patch(_repo(tmp_path, PIPE), {"pipeline.py": PIPE_PATCH})
    Tiers(monkeypatch)
    call = {"args": [{"name": " Ann ", "amount": 1.234}], "kwargs": {}}
    runs = Compiled(monkeypatch, {"differential.v1": {"base": [{"input": call, "output": {"returned": 1.23}}],
                                                      "patch": [{"input": call, "output": {"returned": 1.2}}]}})
    got = review(root, STATED["PIPE"], template_instances=[KEEP_NORMALISE])
    found = _activity(got, "t-normalise")
    assert found["result"] == "counterexample" and "unstable" not in found, found
    assert [at_base for _, at_base in runs.calls] == [False, True, True]   # the base commit observed twice


def test_confined_the_reviewers_ordering_and_repr_cases_give_no_counterexample(tmp_path, bwrap_backend):
    for name, files, patch, item in (("tags", {"tags.py": TAGS}, {"tags.py": TAGS_PATCH}, BY_INITIAL),
                                     ("money", {"money.py": MONEY}, {"money.py": MONEY_REPR}, KEEP_PARSE)):
        root = _patch(_repo(tmp_path, {**files, "tests/test_ok.py": "def test_ok():\n    assert True\n"}, name),
                      patch)
        got = review(root, [{"id": "R1", "text": "the stated test passes", "checks": ["tests/test_ok.py::test_ok"]}],
                     template_instances=[item])
        assert _activity(got, "keep")["result"] == "not_refuted", _activity(got, "keep")
        assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]


# --- the compiled checks through the review, with recorded runs ---------------------------------------------------------

def test_a_compiled_check_runs_like_a_proposed_check_and_never_passes_a_requirement(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {"relation.v1": {"patch": "passed", "base": "passed"}})
    unchecked = [*STATED["CODEC"], {"id": "R2", "text": "newlines survive encoding", "checks": []}]
    got = review(root, unchecked, template_instances=[{**ROUND_TRIP, "requirement_id": "R2", "intent": "change"}])
    assert runs.calls == [("relation.v1", False), ("relation.v1", True)]
    second = _requirement(got, "R2")
    assert second["status"] == "not_established"
    assert second["proposed_checks"] == [{"id": "t-codec", "intent": "change", "result": "not_refuted"}]
    assert "not refuted by 1 proposed check" in second["proposed_note"]
    found = _activity(got, "t-codec")
    assert (found["activity"], found["template"], found["provenance"], found["result"]) == (
        "template instance", "relation.v1", "proposed", "not_refuted")
    assert found["node"].startswith("g6_check_") and found["family"] == "requirement_satisfaction"
    assert "G6" in found["proposed_by"] and "bindings" in found["proposed_by"]
    assert got["assessment"]["outcome"] == "insufficient_evidence" and _codes(got) == {"requirement_without_check"}
    assert got["completion_state"] == "qualified-draft"


def test_a_compiled_counterexample_is_unconfirmed_evidence_with_its_reproduction(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    Compiled(monkeypatch, {"relation.v1": {"patch": "failed", "base": "passed"}})
    got = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP])
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert "relation.v1" in assessment["reasons"][0]["detail"] and "compiled" in assessment["reasons"][0]["detail"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    [defect] = assessment["completion"]["defects_reproduced"]["proposed_checks"]
    assert (defect["check"], defect["result"], defect["provenance"]) == ("t-codec", "counterexample", "proposed")
    reproduction = defect["reproduction"]
    assert "TEMPLATE = 'relation.v1'" in reproduction["text"]
    assert reproduction["sha256"] == hashlib.sha256(reproduction["text"].encode("utf-8")).hexdigest()
    assert reproduction["template"] == "relation.v1" and "fixed text" in reproduction["caution"]
    assert any(reproduction["command"] in action for action in assessment["next_actions"])


def test_a_differential_difference_is_a_counterexample_when_the_behaviour_must_be_kept(tmp_path, monkeypatch):
    """The differential rule: a difference between base and patch on a call outside the change's declared scope is a
    counterexample when the instance's intent is preserve (so at most insufficient evidence), and a question when it
    is change."""
    root = _patch(_repo(tmp_path, PIPE), {"pipeline.py": PIPE_PATCH})
    Tiers(monkeypatch)
    call = {"args": [{"name": " Ann ", "amount": 1.234}], "kwargs": {}}
    Compiled(monkeypatch, {"differential.v1": {"base": [{"input": call, "output": {"returned": 1.23}}],
                                               "patch": [{"input": call, "output": {"returned": 1.2}}]}})
    kept = review(root, STATED["PIPE"], template_instances=[KEEP_NORMALISE])
    assert kept["assessment"]["outcome"] == "insufficient_evidence", kept["assessment"]["reasons"]
    assert _codes(kept) == {"unconfirmed_counterexample"}
    found = _activity(kept, "t-normalise")
    assert found["result"] == "counterexample" and found["family"] == "regression_preservation"
    assert found["differences"] == [{"input": json.dumps(call, sort_keys=True), "base": '{"returned": 1.23}',
                                     "patch": '{"returned": 1.2}'}]
    [defect] = kept["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert defect["reproduction"]["differences"] == found["differences"]
    assert "G6-OBSERVATION" in defect["reproduction"]["compare"]
    assert kept["assessment"]["completion"]["open_questions"] == []
    changed = review(root, STATED["PIPE"], template_instances=[{**KEEP_NORMALISE, "intent": "change"}])
    assert changed["assessment"]["outcome"] == "supported_within_scope", changed["assessment"]["reasons"]
    [question] = changed["assessment"]["completion"]["open_questions"]
    assert question["result"] == "behaviour_changed" and "1.23" in question["question"]


def test_templates_off_runs_free_form_checks_only_and_the_scope_records_the_configuration(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    free = {"id": "p1", "requirement_id": "R1", "intent": "preserve", "file": "test_free.py",
            "source": "def test_free():\n    pass\n"}
    runs = Compiled(monkeypatch, {free["source"]: {"patch": "passed", "base": "passed"},
                                  "relation.v1": {"patch": "failed", "base": "passed"}})
    got = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP], proposed_checks=[free],
                 search={"templates": False})
    assert runs.calls == [(free["source"], False), (free["source"], True)]
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    skipped = _activity(got, "t-codec")
    assert skipped["result"] == "skipped" and "templates" in skipped["reason"]
    assert got["assessment"]["scope"]["search"] == {"templates": False, "test_adequacy": False,
                                                    "template_instances": 1, "proposed_checks": 1, "model_calls": 0}
    both = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP], proposed_checks=[free])
    assert both["assessment"]["scope"]["search"]["templates"] is True
    assert [key for key, _ in runs.calls[2:]] == ["relation.v1", "relation.v1", free["source"], free["source"]]
    assert both["assessment"]["outcome"] == "insufficient_evidence"
    plain = review(root, STATED["CODEC"])
    assert plain["assessment"]["scope"]["search"] == {"templates": True, "test_adequacy": False,
                                                      "template_instances": 0, "proposed_checks": 0, "model_calls": 0}


def test_compiled_checks_are_skipped_when_test_integrity_is_compromised(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH, "conftest.py": "import pytest\n"})
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {})
    got = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP])
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert runs.calls == [] and _activity(got, "t-codec")["result"] == "skipped"


def test_with_no_template_instances_and_no_search_every_decided_field_is_unchanged(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {})
    plain = review(root, STATED["CODEC"])
    for extra in ({"template_instances": None}, {"template_instances": []}, {"search": None}):
        got = review(root, STATED["CODEC"], **extra)
        for key in ("outcome", "reasons", "requirements", "unknowns", "next_actions"):
            assert got["assessment"][key] == plain["assessment"][key], key
        assert set(got) == set(plain) and got["evidence"] == plain["evidence"]
        assert got["assessment"]["completion"]["test_adequacy"] == []
    assert runs.calls == []
    assert "verification_templates" not in " ".join(json.dumps(a) for a in plain["assessment"]["completion"][
        "activities"])


# --- forbidden_dependency.v1: static, against the repository's own contract or the caller's ----------------------------

SHOP_RULE = [{"id": "R1", "text": "the layers hold", "checks": ["tests/test_shop.py::test_handle"]},
             {"id": "R2", "text": "the data layer imports no higher layer", "checks": []}]


def _shop(tmp_path, extra=None, patch=None, name="repo"):
    root = _repo(tmp_path, {**SHOP, **(extra or {})}, name)
    return _patch(root, patch if patch is not None else {"shop/data.py": DATA_IMPORTS_API})


def test_forbidden_dependency_against_the_repositorys_own_contract_is_a_defect(tmp_path, monkeypatch):
    root = _shop(tmp_path, {"pyproject.toml": LAYERS_TOML})
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {})
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assessment = got["assessment"]
    assert assessment["outcome"] == "defect_detected", assessment["reasons"]
    [why] = [r for r in assessment["reasons"] if r["code"] == "forbidden_dependency_added"]
    assert why["stage"] == "tier_1" and "shop.data" in why["detail"] and "shop.api" in why["detail"]
    second = _requirement(got, "R2")
    assert second["status"] == "failed"
    assert [(c["id"], c["provenance"], c["verdict"]) for c in second["checks"]] == [("t-layers", "structural",
                                                                                     "failed")]
    found = _activity(got, "t-layers")
    assert (found["result"], found["provenance"], found["contract_provenance"], found["ran_code"]) == (
        "failed", "structural", "repository_stated", False)
    assert "pyproject.toml" in found["contract_source"] and "base commit" in found["contract_source"]
    [violation] = found["violations"]
    assert (violation["from"], violation["to"], violation["file"], violation["line"]) == (
        "shop.data", "shop.api", "shop/data.py", 2)
    [stated] = assessment["completion"]["defects_reproduced"]["stated_and_repository_checks"]
    assert (stated["check"], stated["provenance"], stated["code"]) == ("t-layers", "structural",
                                                                       "forbidden_dependency_added")
    assert stated["reproduction"]["imports"][0]["text"] == "from shop import api"
    assert runs.calls == [] and got["completion_state"] == "qualified-draft"
    assert any(e["obligation"].startswith("the layer rules") for e in assessment["completion"]["preservation"])


@pytest.mark.parametrize("config", [
    {".importlinter": "[importlinter]\nroot_package = shop\n\n[importlinter:contract:layers]\nname = shop layers\n"
                      "type = layers\nlayers =\n    shop.api\n    shop.service\n    shop.data\n"},
    {"setup.cfg": "[metadata]\nname = shop\n\n[importlinter]\nroot_package = shop\n\n[importlinter:contract:one]\n"
                  "name = no upward import\ntype = forbidden\nsource_modules =\n    shop.data\nforbidden_modules =\n"
                  "    shop.api\n"},
], ids=["importlinter-file", "setup-cfg-forbidden"])
def test_the_repositorys_contract_is_read_from_its_import_linter_configuration(tmp_path, monkeypatch, config):
    root = _shop(tmp_path, config)
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    assert _activity(got, "t-layers")["violations"][0]["to"] == "shop.api"


def test_forbidden_dependency_against_a_contract_the_caller_states_is_a_defect(tmp_path, monkeypatch):
    root = _shop(tmp_path)
    Tiers(monkeypatch)
    stated = instance("t-mine", "forbidden_dependency.v1", {"package": "shop", "contract": {
        "forbidden": [{"source": "shop.data", "forbidden": "shop.api"}]}}, requirement="R2")
    got = review(root, SHOP_RULE, template_instances=[stated])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    found = _activity(got, "t-mine")
    assert (found["contract_provenance"], found["result"]) == ("stated", "failed")
    assert found["contract_source"] == "the instance's bindings"
    assert _requirement(got, "R2")["status"] == "failed"


def test_forbidden_dependency_against_a_contract_a_model_proposed_is_at_most_insufficient_evidence(tmp_path,
                                                                                                    monkeypatch):
    """Provenance ``model_hypothesis`` is set only by G6's own code (the proposer, VS-5); a request cannot carry it."""
    root = _shop(tmp_path)
    Tiers(monkeypatch)
    proposed = verification_templates.TemplateInstance(
        id="t-model", template="forbidden_dependency.v1", requirement_id="R1", intent="preserve",
        bindings={"package": "shop", "contract": {"layers": ["shop.api", "shop.service", "shop.data"]}},
        provenance="model_hypothesis")
    got = review(root, SHOP_RULE[:1], template_instances=[proposed])
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert [c["provenance"] for c in _requirement(got, "R1")["checks"]] == ["stated"]
    found = _activity(got, "t-model")
    assert (found["contract_provenance"], found["result"]) == ("model_hypothesis", "counterexample")
    [defect] = assessment["completion"]["defects_reproduced"]["proposed_checks"]
    assert defect["provenance"] == "model_hypothesis"
    assert assessment["completion"]["defects_reproduced"]["stated_and_repository_checks"] == []


def test_unmatched_contract_parts_and_imports_outside_the_package_are_out_of_scope_never_violations(tmp_path,
                                                                                                     monkeypatch):
    root = _shop(tmp_path, patch={"shop/data.py": "import requests\nimport json\n\n\ndef load(x):\n    return x\n"})
    Tiers(monkeypatch)
    stated = instance("t-scope", "forbidden_dependency.v1", {"package": "shop", "contract": {
        "layers": ["shop.api", "shop.missing", "shop.service", "shop.data"],
        "forbidden": [{"source": "shop.data", "forbidden": "requests"}, {"source": "shop.gone", "forbidden": "shop.api"}]}},
        requirement="R2")
    got = review(root, SHOP_RULE, template_instances=[stated])
    found = _activity(got, "t-scope")
    # the patch adds no import between the package's modules, so the contract cannot have been broken (ruling R-C1)
    assert found["result"] == "vacuous" and found["violations"] == [], found
    assert "does not exercise the change" in found["note"]
    scope = " ".join(found["out_of_scope"])
    assert "shop.missing" in scope and "shop.gone" in scope and "requests" in scope
    assert "outside the package" in scope and "G6_E_DESIGN_CONTRACT_UNMATCHED" not in json.dumps(got["assessment"]
                                                                                                ["reasons"])
    # a structural check never passes a requirement (ruling R-C1): R2 has no stated check
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]["reasons"]
    assert _codes(got) == {"requirement_without_check"}
    assert _requirement(got, "R2")["status"] == "not_established"
    assert [d["requirement"] for d in got["assessment"]["completion"]["demonstrated"]] == ["R1"]
    assert any("dynamic" in limit for limit in found["limits"])
    nothing = instance("t-none", "forbidden_dependency.v1", {"package": "shop", "contract": {
        "layers": ["shop.missing"]}}, requirement="R2")
    got = review(root, SHOP_RULE, template_instances=[nothing])
    assert _activity(got, "t-none")["result"] == "refused" and "matches no module" in _activity(got, "t-none")["reason"]


def test_a_violation_the_base_commit_already_had_is_not_one_the_patch_adds(tmp_path, monkeypatch):
    already = {"shop/data.py": DATA_IMPORTS_API, "pyproject.toml": LAYERS_TOML}
    root = _shop(tmp_path, already, patch={"shop/data.py": DATA_IMPORTS_API + "\n\ndef more():\n    return 2\n"})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    found = _activity(got, "t-layers")
    assert found["result"] == "vacuous" and found["violations"] == [] and found["already_at_base"], found
    # nothing new is reported against the patch; R2 has no stated check, and a structural check passes none (R-C1)
    assert not _codes(got) & {"forbidden_dependency_added", "check_failed"}
    assert _requirement(got, "R2")["status"] == "not_established"


def test_the_repositorys_contract_is_read_at_the_base_commit_so_the_patch_cannot_loosen_it(tmp_path, monkeypatch):
    loosened = LAYERS_TOML.replace('"shop.api", "shop.service", "shop.data"', '"shop.service", "shop.data"')
    root = _shop(tmp_path, {"pyproject.toml": LAYERS_TOML},
                 patch={"shop/data.py": DATA_IMPORTS_API, "pyproject.toml": loosened})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    found = _activity(got, "t-layers")
    assert found["result"] == "failed" and any("pyproject.toml" in limit for limit in found["limits"]), found
    added = _shop(tmp_path, patch={"shop/data.py": DATA_IMPORTS_API, "pyproject.toml": LAYERS_TOML}, name="added")
    got = review(added, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert _activity(got, "t-layers")["result"] == "refused" and "base commit" in _activity(got, "t-layers")["reason"]


def test_the_repositorys_ignored_imports_are_honoured(tmp_path, monkeypatch):
    ignoring = LAYERS_TOML + 'ignore_imports = ["shop.data -> shop.api"]\n'
    root = _shop(tmp_path, {"pyproject.toml": ignoring})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert _activity(got, "t-layers")["result"] == "passed", _activity(got, "t-layers")


def test_the_structural_check_stops_reading_the_package_when_the_reviews_time_is_spent(tmp_path, monkeypatch):
    """The structural check reads and parses the package before anything runs; it stops at the review's deadline, and
    the instance is refused with that reason rather than holding the review past its time."""
    root = _shop(tmp_path, {"pyproject.toml": LAYERS_TOML})
    items = verification_templates.template_instances([SHOP_LAYERS], {"R1"})
    prepared = verification_templates.prepare(root, repo_tests.change(root), items, deadline=time.monotonic() - 1)
    [activity] = prepared.activities
    assert activity["result"] == "refused" and "time" in activity["reason"], activity
    assert prepared.structural[0]["verdict"] == "not_run"
    Tiers(monkeypatch)
    monkeypatch.setattr(verification_templates, "_clock", lambda: float("inf"))   # G6's clock for the deadline
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert _activity(got, "t-layers")["result"] == "refused" and "time" in _activity(got, "t-layers")["reason"]


def test_an_unsupported_repository_contract_is_out_of_scope(tmp_path, monkeypatch):
    independence = ('[tool.importlinter]\nroot_package = "shop"\n\n[[tool.importlinter.contracts]]\nname = "apart"\n'
                    'type = "independence"\nmodules = ["shop.api", "shop.data"]\n')
    root = _shop(tmp_path, {"pyproject.toml": independence})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    found = _activity(got, "t-layers")
    assert found["result"] == "refused" and "independence" in found["reason"], found
    # R2 has no stated check, and a structural check that could not apply decides nothing (ruling R-C1)
    assert _requirement(got, "R2")["status"] == "not_established"
    assert _codes(got) == {"requirement_without_check"}


# --- the VS-3 review's fix pass: C1, a structural check never passes a requirement (ruling R-C1) -------------------------

CALC_REVIEW = {"calc/__init__.py": "", "calc/ops.py": "def add(a, b):\n    return a + b\n",
               "calc/fmt.py": "def show(x):\n    return str(x)\n",
               "tests/test_smoke.py": "def test_smoke():\n    assert True\n"}
CALC_RULE = [{"id": "R1", "text": "add returns the sum of its two arguments", "checks": []},
             {"id": "R2", "text": "the smoke test passes", "checks": ["tests/test_smoke.py::test_smoke"]}]


@pytest.mark.parametrize("contract", [{"layers": ["calc.ops"]}, {"layers": ["calc.fmt", "calc"]}],
                         ids=["one-layer", "two-layers-the-patch-never-imports-across"])
def test_review_c1_a_structural_check_never_passes_a_behavioural_requirement_and_a_vacuous_one_says_so(
        tmp_path, monkeypatch, contract):
    """The reviewer's input: R1 is about behaviour, has no stated check, and the patch breaks it (a - b); the caller
    attaches a forbidden_dependency.v1 instance to it whose contract the patch cannot break."""
    root = _patch(_repo(tmp_path, CALC_REVIEW), {"calc/ops.py": "def add(a, b):\n    return a - b\n"})
    Tiers(monkeypatch)
    got = review(root, CALC_RULE, template_instances=[instance("dep", "forbidden_dependency.v1", {
        "package": "calc", "contract": contract})])
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]["reasons"]
    first = _requirement(got, "R1")
    assert first["status"] == "not_established" and first["checks"] == []
    found = _activity(got, "dep")
    assert found["result"] == "vacuous" and "does not exercise the change" in found["note"], found
    kept = [e for e in got["assessment"]["completion"]["preservation"] if e.get("check") == "dep"]
    assert kept and kept[0]["result"] == "vacuous"
    assert "R1" not in [d["requirement"] for d in got["assessment"]["completion"]["demonstrated"]]


def test_review_c1_a_contract_that_holds_is_reported_and_leaves_the_requirement_to_its_stated_checks(tmp_path,
                                                                                                    monkeypatch):
    root = _shop(tmp_path, patch={"shop/api.py": SHOP["shop/api.py"] + "\nfrom shop import data\n"})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[instance("t-mine", "forbidden_dependency.v1", {
        "package": "shop", "contract": {"layers": ["shop.api", "shop.service", "shop.data"]}}, requirement="R2")])
    found = _activity(got, "t-mine")
    assert found["result"] == "passed" and found["violations"] == [], found   # api importing data is allowed
    assert _requirement(got, "R2")["status"] == "not_established" and _requirement(got, "R2")["checks"] == []
    assert [e["result"] for e in got["assessment"]["completion"]["preservation"] if e.get("check") == "t-mine"] == [
        "passed"]


def test_review_c1_a_real_new_violation_is_still_a_defect(tmp_path, monkeypatch):
    root = _shop(tmp_path, {"pyproject.toml": LAYERS_TOML})
    Tiers(monkeypatch)
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert got["assessment"]["outcome"] == "defect_detected"
    assert _requirement(got, "R2")["status"] == "failed"


def test_review_c1_a_contract_of_one_layer_can_never_be_broken_so_it_is_vacuous_even_when_imports_change(
        tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CALC_REVIEW), {"calc/ops.py": "from calc import fmt\n\n\ndef add(a, b):\n"
                                                                "    return a - b\n"})
    Tiers(monkeypatch)
    got = review(root, CALC_RULE, template_instances=[instance("dep", "forbidden_dependency.v1", {
        "package": "calc", "contract": {"layers": ["calc.ops"]}})])
    found = _activity(got, "dep")
    assert found["result"] == "vacuous" and "can never be broken" in found["note"], found


# --- C2: git's index flags never hide a change ---------------------------------------------------------------------------

IMPORTLINTER = ("[importlinter]\nroot_package = shop\n\n[importlinter:contract:layers]\nname = shop layers\n"
                "type = layers\nlayers =\n    shop.api\n    shop.service\n    shop.data\n")
VISIBLE = {"shop/service.py": SHOP["shop/service.py"] + "\n\n# a visible change too\n"}
ONE_RULE = [{"id": "R1", "text": "the data layer never imports the api layer",
             "checks": ["tests/test_shop.py::test_handle"]}]


@pytest.mark.parametrize("flag, patch", [
    ("", {"shop/data.py": DATA_IMPORTS_API, **VISIBLE}),
    ("--skip-worktree shop/data.py", {"shop/data.py": DATA_IMPORTS_API, **VISIBLE}),
    ("--assume-unchanged .importlinter", {"shop/data.py": DATA_IMPORTS_API, **VISIBLE,
                                          ".importlinter": IMPORTLINTER.replace("    shop.api\n", "")}),
], ids=["control", "skip-worktree-import", "assume-unchanged-contract"])
def test_review_c2_a_file_git_is_told_not_to_look_at_is_compared_with_the_base_commit(tmp_path, monkeypatch, flag,
                                                                                      patch):
    """The reviewer's three cases: the patch makes shop.data import shop.api, with the importing file marked
    skip-worktree, or with the repository's contract loosened in a file marked assume-unchanged."""
    root = _patch(_repo(tmp_path, {**SHOP, ".importlinter": IMPORTLINTER}), patch)
    if flag:
        _git(root, "update-index", *flag.split())
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    [violation] = _activity(got, "layers")["violations"]
    assert (violation["from"], violation["to"]) == ("shop.data", "shop.api")
    assert "shop/data.py" in got["assessment"]["scope"]["changed_files"]
    if ".importlinter" in patch:
        assert ".importlinter" in got["assessment"]["scope"]["changed_files"]
        assert any(".importlinter" in limit for limit in _activity(got, "layers")["limits"])


def test_review_c2_the_base_side_is_read_from_the_base_commit_never_from_the_working_tree(tmp_path):
    root = _patch(_repo(tmp_path, {**SHOP, ".importlinter": IMPORTLINTER}), {"shop/data.py": DATA_IMPORTS_API})
    _git(root, "update-index", "--assume-unchanged", "shop/data.py")
    change = repo_tests.change(root)
    assert "shop/data.py" in change.files and "shop/data.py" in change.sources
    tree = verification_templates.Tree.read(root, change)
    assert "from shop import api" not in tree.text("shop/data.py", verification_templates.BASE)
    assert "from shop import api" in tree.text("shop/data.py", verification_templates.PATCH)


def test_review_c2_a_flagged_file_missing_from_the_working_tree_is_an_unknown_never_unchanged(tmp_path, monkeypatch):
    """A sparse checkout: git is told not to look at shop/service.py, and it is not on the disk."""
    root = _patch(_repo(tmp_path, {**SHOP, ".importlinter": IMPORTLINTER}), {"shop/data.py": SHOP["shop/data.py"]
                                                                              + "\n\ndef more():\n    return 2\n"})
    _git(root, "update-index", "--skip-worktree", "shop/service.py")
    (root / "shop" / "service.py").unlink()
    change = repo_tests.change(root)
    assert change.unseen == ("shop/service.py",) and "shop/service.py" not in change.files
    Tiers(monkeypatch)
    got = review(root, ONE_RULE)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    [why] = [r for r in assessment["reasons"] if r["code"] == "file_not_in_working_tree"]
    assert why["stage"] == "evidence" and "shop/service.py" in why["detail"]
    assert any("shop/service.py" in unknown for unknown in assessment["unknowns"])


# --- C3: import chains inside the package, as import-linter follows them --------------------------------------------------

HELPERS = {"shop/helpers.py": "from shop import api\n\n\ndef helper():\n    return api\n"}
DATA_IMPORTS_HELPERS = {"shop/data.py": "from shop import helpers\n\n\ndef load(x):\n    return x\n"}
FORBIDDEN_LINTER = ("[importlinter]\nroot_package = shop\n\n[importlinter:contract:one]\nname = no upward import\n"
                    "type = forbidden\nsource_modules =\n    shop.data\nforbidden_modules =\n    shop.api\n")


def _lint_imports(root: Path) -> int | None:
    """The exit code of the repository's own linter on the tree as it is (None when import-linter is not installed)."""
    import shutil

    program = shutil.which("lint-imports")
    if program is None:
        return None
    return subprocess.run([program, "--no-cache"], cwd=root, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(root)}).returncode


@pytest.mark.parametrize("config", [IMPORTLINTER, FORBIDDEN_LINTER], ids=["layers", "forbidden"])
def test_review_c3_a_new_chain_from_a_lower_layer_to_a_higher_one_is_a_new_violation(tmp_path, monkeypatch, config):
    """The reviewer's case: the patch makes shop.data import shop.helpers, which imports shop.api."""
    root = _repo(tmp_path, {**SHOP, **HELPERS, ".importlinter": config})
    base_lint = _lint_imports(root)
    _patch(root, DATA_IMPORTS_HELPERS)
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    [violation] = _activity(got, "layers")["violations"]
    assert (violation["from"], violation["to"], violation["file"], violation["line"]) == (
        "shop.data", "shop.api", "shop/data.py", 1)
    assert violation["chain"] == ["shop.data", "shop.helpers", "shop.api"]
    assert violation["text"] == "from shop import helpers"
    assert not any("direct ones only" in limit for limit in _activity(got, "layers")["limits"])
    if base_lint is not None:   # G6 agrees with the repository's own linter
        assert (base_lint, _lint_imports(root)) == (0, 1)


def test_review_c3_a_forbidden_contract_that_allows_indirect_imports_checks_direct_ones_only(tmp_path, monkeypatch):
    config = FORBIDDEN_LINTER + "allow_indirect_imports = True\n"
    root = _patch(_repo(tmp_path, {**SHOP, **HELPERS, ".importlinter": config}), DATA_IMPORTS_HELPERS)
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    found = _activity(got, "layers")
    assert found["result"] == "passed" and found["violations"] == [], found
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    lint = _lint_imports(root)
    assert lint in (None, 0)


def test_review_c3_a_chain_the_base_commit_already_had_is_not_one_the_patch_adds(tmp_path, monkeypatch):
    root = _repo(tmp_path, {**SHOP, **HELPERS, **DATA_IMPORTS_HELPERS, ".importlinter": IMPORTLINTER})
    _patch(root, {"shop/data.py": DATA_IMPORTS_HELPERS["shop/data.py"] + "\n\ndef more():\n    return 2\n"})
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    found = _activity(got, "layers")
    assert found["violations"] == [] and found["already_at_base"], found
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]


# --- I1: every contract option G6 reads is honoured, and one it does not implement is never guessed ----------------------

TYPE_CHECKING_DATA = {"shop/data.py": "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n"
                                      "    from shop.api import handle\n\n\ndef load(x):\n    return x\n"}


@pytest.mark.parametrize("excluded", [True, False], ids=["excluded", "control"])
def test_review_i1_type_checking_imports_are_left_out_when_the_repository_says_so(tmp_path, monkeypatch, excluded):
    config = IMPORTLINTER.replace("root_package = shop\n", "root_package = shop\nexclude_type_checking_imports = True\n"
                                  ) if excluded else IMPORTLINTER
    root = _patch(_repo(tmp_path, {**SHOP, ".importlinter": config}), TYPE_CHECKING_DATA)
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    found = _activity(got, "layers")
    if excluded:
        assert found["result"] == "passed" and found["violations"] == [], found
        assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    else:
        assert found["result"] == "failed" and found["violations"][0]["to"] == "shop.api", found
    lint = _lint_imports(root)
    assert lint in (None, 0 if excluded else 1)


@pytest.mark.parametrize("config, named", [
    (IMPORTLINTER.replace("root_package = shop\n", "root_package = shop\ncontract_types =\n"
                          "    mine: somewhere.Contract\n"), "contract_types"),
    (IMPORTLINTER.replace("root_package = shop\n", "root_package = shop\ninclude_external_packages = True\n"),
     "include_external_packages"),
    (IMPORTLINTER + "exhaustive = True\n", "exhaustive"),
    (IMPORTLINTER + "unknown_setting = 1\n", "unknown_setting"),
    (IMPORTLINTER.replace("    shop.service\n", "    shop.service | shop.helpers\n"), "independent"),
    (FORBIDDEN_LINTER + "as_packages = False\n", "as_packages"),
    (FORBIDDEN_LINTER + "allow_indirect_imports = sometimes\n", "allow_indirect_imports"),
    (IMPORTLINTER + "\n[importlinter:contract:apart]\nname = apart\ntype = independence\nmodules =\n    shop.api\n"
                    "    shop.data\n", "independence"),
], ids=["contract-types", "external-packages", "exhaustive", "unknown-option", "sibling-layers", "as-packages",
        "unreadable-flag", "unsupported-type"])
def test_review_i1_an_option_or_type_g6_does_not_implement_makes_the_instance_not_applicable(tmp_path, monkeypatch,
                                                                                            config, named):
    root = _patch(_repo(tmp_path, {**SHOP, **HELPERS, ".importlinter": config}), {"shop/data.py": DATA_IMPORTS_API})
    Tiers(monkeypatch)
    got = review(root, ONE_RULE, template_instances=[instance("layers", "forbidden_dependency.v1",
                                                              {"contract": "repository"})])
    found = _activity(got, "layers")
    assert found["result"] == "refused" and named in found["reason"], found
    assert "does not implement" in found["reason"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"   # R-C1: its stated check decides it
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]


# --- the decoy package folder: two folders holding one package are never a silent choice ---------------------------------

def test_review_a_package_in_two_folders_makes_the_instance_not_applicable(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, {**SHOP, ".importlinter": IMPORTLINTER}), {
        "shop/data.py": DATA_IMPORTS_API, "src/shop/__init__.py": "", "src/shop/data.py": "def load(x):\n    return x\n",
        "src/shop/api.py": "def handle(x):\n    return x\n"})
    Tiers(monkeypatch)
    for one in (instance("layers", "forbidden_dependency.v1", {"contract": "repository"}),
                instance("layers", "forbidden_dependency.v1", {"package": "shop", "contract": {
                    "layers": ["shop.api", "shop.service", "shop.data"]}})):
        got = review(root, ONE_RULE, template_instances=[one])
        found = _activity(got, "layers")
        assert found["result"] == "refused", found
        assert "more than one folder" in found["reason"] and "src/" in found["reason"], found["reason"]
        assert found.get("violations") is None
        assert any("layers" in u and "could not be applied" in u for u in got["assessment"]["unknowns"])


# --- test adequacy: reported only ----------------------------------------------------------------------------------------

CLAMP = {"clamp.py": "def clamp(x):\n    return x\n",
         "test_clamp.py": "from clamp import clamp\n\n\ndef test_middle():\n    assert clamp(5) == 5\n"}
CLAMP_PATCH = {"clamp.py": "def clamp(x):\n    return max(0, min(x, 10))\n"}
CLAMP_RULE = [{"id": "R1", "text": "clamp keeps a value inside 0 to 10", "checks": ["test_clamp.py::test_middle"]}]


class Mutants:
    """Stands in for each mutant's run (``verification_search._run_mutant``): a mutant whose source holds ``kills`` fails
    the checks; the others pass them. ``advance`` seconds pass on G6's clock with each run."""

    def __init__(self, monkeypatch, kills="return None", *, advance=0.0, extra=None):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.roots: list[tuple[str, ...]] = []
        clock = [verification_search._clock()]
        monkeypatch.setattr(verification_search, "_clock", lambda: clock[0])

        def run(change, rel, source, ids, timeout_s, **given):
            self.calls.append((rel, tuple(ids)))
            self.roots.append(tuple(given.get("roots", ())))
            clock[0] += advance
            return {**(result(failed=list(ids)) if kills in source else result(list(ids))), **(extra or {})}

        monkeypatch.setattr(verification_search, "_run_mutant", run)


def test_surviving_mutants_are_reported_in_test_adequacy_and_change_no_outcome(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CLAMP), CLAMP_PATCH)
    Tiers(monkeypatch)
    mutants = Mutants(monkeypatch)
    plain = review(root, CLAMP_RULE)
    got = review(root, CLAMP_RULE, search={"test_adequacy": True})
    for key in ("outcome", "reasons", "requirements", "unknowns"):
        assert got["assessment"][key] == plain["assessment"][key], key
    assert got["assessment"]["outcome"] == "supported_within_scope"
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    expected = mutation_check.mutants(CLAMP_PATCH["clamp.py"], 6, {2})
    assert (adequacy["requirement"], adequacy["killed"], adequacy["survived"]) == ("R1", 1, len(expected) - 1)
    assert adequacy["checks"] == ["test_clamp.py::test_middle"]
    assert {s["mutant"] for s in adequacy["survivors"]} == {m.describe() for m in expected
                                                            if "return None" not in m.source}
    assert all(s["file"] == "clamp.py" for s in adequacy["survivors"]) and "changes no outcome" in adequacy["note"]
    assert mutants.calls == [("clamp.py", ("test_clamp.py::test_middle",))] * len(expected)
    runs = [a for a in got["assessment"]["completion"]["activities"] if a["activity"] == "mutation run"]
    assert len(runs) == len(expected) and all(a["model_calls"] == 0 for a in runs)
    assert got["assessment"]["scope"]["search"]["test_adequacy"] is True
    assert plain["assessment"]["completion"]["test_adequacy"] == []


def test_test_adequacy_stops_at_the_deadline_and_lists_the_mutants_it_skipped(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CLAMP), CLAMP_PATCH)
    Tiers(monkeypatch)
    mutants = Mutants(monkeypatch, advance=9.0)
    got = review(root, CLAMP_RULE, timeout_s=22, search={"test_adequacy": True})
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    assert 1 <= len(mutants.calls) < 3
    assert adequacy["skipped"] and all(s["reason"] for s in adequacy["skipped"])
    assert adequacy["killed"] + adequacy["survived"] + len(adequacy["skipped"]) == 3
    assert got["assessment"]["outcome"] == "supported_within_scope"


def test_test_adequacy_measures_only_requirements_whose_stated_checks_passed(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, {**CLAMP, "test_more.py": "def test_more():\n    assert True\n"}), CLAMP_PATCH)
    Tiers(monkeypatch, failing=("test_more.py::test_more",))
    Mutants(monkeypatch)
    rules = [*CLAMP_RULE, {"id": "R2", "text": "more", "checks": ["test_more.py::test_more"]},
             {"id": "R3", "text": "no check", "checks": []}]
    got = review(root, rules, search={"test_adequacy": True})
    found = {entry["requirement"]: entry for entry in got["assessment"]["completion"]["test_adequacy"]}
    assert found["R2"]["result"] == "not_run" and "did not all pass" in found["R2"]["reason"]
    assert found["R3"]["result"] == "not_run" and "no stated check" in found["R3"]["reason"]
    assert got["assessment"]["outcome"] == "defect_detected"


# --- I2 and I3: what G6's own reading of the files cannot hold never escapes the review ---------------------------------

RATES = {"rates.py": "TABLE = 0\n\n\ndef rate(x):\n    return x * 2\n",
         "tests/test_rates.py": "from rates import rate\n\n\ndef test_rate():\n    assert rate(2) == 4\n"}
RATES_RULE = [{"id": "R1", "text": "rate doubles", "checks": ["tests/test_rates.py::test_rate"]}]
SUM_400 = "TABLE = " + " + ".join(["1"] * 400) + "\n\n\ndef rate(x):\n    return x * 2\n"
LISTS_190 = "TABLE = " + "[" * 190 + "]" * 190 + "\n\n\ndef rate(x):\n    return x * 2\n"
MINUS_200000 = "def f(x):\n    return x\n\n\nY = " + "-" * 200000 + "1\n"   # Python's parser: MemoryError
LIB = {"lib/__init__.py": "", "lib/core.py": "def f(x):\n    return x\n", "lib/deep.py": "Y = 1\n",
       "tests/test_lib.py": "from lib.core import f\n\n\ndef test_f():\n    assert f(1) == 1\n"}
LIB_RULE = [{"id": "R1", "text": "f is the identity", "checks": ["tests/test_lib.py::test_f"]}]


def _did_not_complete(got, *named: str) -> None:
    """The outcome from the stated checks stands, and an unknown says the search did not complete."""
    assessment = got["assessment"]
    assert got["ok"] is True and got["completion_state"] == "qualified-draft", got
    assert assessment["outcome"] == "supported_within_scope", assessment["reasons"]
    assert any("did not complete" in unknown and all(n in unknown for n in named)
               for unknown in assessment["unknowns"]), assessment["unknowns"]


@pytest.mark.parametrize("text, error", [(SUM_400, "RecursionError"), (LISTS_190, "RecursionError")],
                         ids=["a-sum-of-400-terms", "lists-190-deep"])
def test_review_i2_test_adequacy_on_a_file_g6_cannot_walk_records_an_error_and_never_raises(tmp_path, monkeypatch,
                                                                                            text, error):
    root = _patch(_repo(tmp_path, RATES), {"rates.py": text})
    Tiers(monkeypatch)
    Mutants(monkeypatch)
    got = review(root, RATES_RULE, search={"test_adequacy": True})
    _did_not_complete(got, "test adequacy", "rates.py")
    [found] = [a for a in got["assessment"]["completion"]["activities"] if a.get("activity") == "mutation run"]
    assert (found["file"], found["result"]) == ("rates.py", "error") and error in found["reason"], found
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    assert [s["file"] for s in adequacy["skipped"]] == ["rates.py"] and error in adequacy["skipped"][0]["reason"]


@pytest.mark.parametrize("change, one", [
    ({"lib/core.py": MINUS_200000}, instance("ex", "example.v1", {"function": "lib.core.f", "cases": [
        {"args": [1], "returns": 1}]}, intent="change")),
    ({"lib/deep.py": MINUS_200000[30:]}, instance("ex", "forbidden_dependency.v1", {"package": "lib", "contract": {
        "layers": ["lib.core", "lib.deep"]}})),
], ids=["example-names-the-module", "forbidden-dependency-on-its-package"])
def test_review_i3_a_module_pythons_parser_cannot_hold_is_an_activity_error_never_an_exception(tmp_path, monkeypatch,
                                                                                               change, one):
    """The reviewer's 200,000 unary minus signs: Python's parser raises MemoryError in G6's own process."""
    root = _patch(_repo(tmp_path, LIB), change)
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {})
    got = review(root, LIB_RULE, template_instances=[one])
    _did_not_complete(got, "ex")
    found = _activity(got, "ex")
    assert found["result"] == "error" and "MemoryError" in found["reason"], found
    assert runs.calls == [] and _requirement(got, "R1")["status"] == "passed_on_stated_checks"


def test_review_i2_the_mcp_job_completes_when_g6s_own_reading_fails(tmp_path, monkeypatch):
    """The reviewer's job case: the same review through the MCP door's job completed, where it had failed with
    G6_E_JOB_FAILED."""
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    root = _patch(_repo(tmp_path, {**RATES, **LIB}), {"rates.py": SUM_400, "lib/core.py": MINUS_200000})
    Tiers(monkeypatch)
    Mutants(monkeypatch)
    Compiled(monkeypatch, {})
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow():
        async with Client(server) as client:
            job = data(await client.call_tool("g6_review_patch", {
                "repo": str(root), "task": "Make the change the requirements state.", "requirements": RATES_RULE,
                "search": {"test_adequacy": True}, "template_instances": [instance("ex", "example.v1", {
                    "function": "lib.core.f", "cases": [{"args": [1], "returns": 1}]}, intent="change")]}))
            for _ in range(600):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return got
                await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    got = asyncio.run(flow())
    assert got["status"] == "completed", got
    _did_not_complete(got["result"], "ex")
    assert check_envelope(got["result"]) == []


# --- I4 and I7: the search stage keeps to the review's deadline, and never takes tier 1's time ---------------------------

RULES_450 = "".join(f'''
def rule_{i}(order, limit=10):
    """Rule {i}: the discount for an order."""
    total = sum(item["price"] * item["count"] for item in order["items"])
    if total > limit and order.get("member"):
        return round(total * 0.9 - {i}, 2)
    elif total < 0:
        raise ValueError("negative total")
    return total + {i} if order.get("rush") else total
''' for i in range(50))
#: The margin past the review's deadline a slow search stage may take: one step of the work it stops between (a file
#: read, one module parsed, one mutant made of a file within test adequacy's size), measured well under a second.
MARGIN_S = 5.0


def test_review_i4_test_adequacy_on_a_new_450_line_module_keeps_to_the_deadline(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, {"shop/__init__.py": "", "tests/test_rules.py": (
        "from shop.rules import rule_0\n\n\ndef test_rule():\n    assert rule_0({'items': []}) == 0\n")}),
        {"shop/rules.py": RULES_450})
    Tiers(monkeypatch)
    monkeypatch.setattr(verification_search, "_run_mutant", lambda change, rel, source, ids, timeout_s, **_:
                        result(list(ids)))
    started = time.monotonic()
    got = review(root, [{"id": "R1", "text": "rule_0 gives no discount on an empty order",
                         "checks": ["tests/test_rules.py::test_rule"]}], search={"test_adequacy": True}, timeout_s=30)
    took = time.monotonic() - started
    assert took < 30 + MARGIN_S, took
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    assert adequacy["skipped"] and all(s["reason"] for s in adequacy["skipped"]), adequacy
    assert got["assessment"]["outcome"] == "supported_within_scope"


def test_review_i4_test_adequacy_leaves_out_a_file_over_its_size_and_says_why(tmp_path, monkeypatch):
    big = "def rate(x):\n    return x * 2\n" + "".join(f"\n\ndef pad_{i}(x):\n    return x + {i}\n"
                                                    for i in range(2000))
    assert len(big) > verification_search.ADEQUACY_FILE_CHARS
    root = _patch(_repo(tmp_path, RATES), {"rates.py": big})
    Tiers(monkeypatch)
    mutants = Mutants(monkeypatch)
    got = review(root, RATES_RULE, search={"test_adequacy": True})
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    [skipped] = adequacy["skipped"]
    assert skipped["file"] == "rates.py" and str(verification_search.ADEQUACY_FILE_CHARS) in skipped["reason"]
    assert mutants.calls == []


def test_review_i7_the_structural_stage_never_takes_tier_ones_time(tmp_path, monkeypatch):
    """The reviewer's 19 files of about 1 MB: the structural check reads and parses them after tier 1, which keeps its
    normal limit, and stops at the review's deadline."""
    files = {"big/__init__.py": "", "tests/test_big.py": "def test_ok():\n    assert True\n"}
    line = "import big.m0\n"
    for index in range(19):
        files[f"big/m{index}.py"] = line * (999_000 // len(line))
    root = _patch(_repo(tmp_path, files), {"big/m0.py": files["big/m0.py"] + "X = 1\n"})
    limits: list[int] = []

    def execute(root, ids, *, timeout_s, change=None, **_):
        limits.append(timeout_s)
        return result(list(ids))

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **_: {"verdict": "not_run", "said": "not recorded"})
    rule = [{"id": "R1", "text": "the package keeps its layers", "checks": ["tests/test_big.py::test_ok"]}]
    review(root, rule, timeout_s=20)
    normal = limits[0]   # tier 1's limit with no template instance: the review's, less what came before it
    limits.clear()
    started = time.monotonic()
    got = review(root, rule, template_instances=[instance("dep", "forbidden_dependency.v1", {"package": "big",
                 "contract": {"layers": ["big.m1", "big.m0"]}})], timeout_s=20)
    took = time.monotonic() - started
    assert limits and limits[0] >= normal - 1, (normal, limits)
    assert took < 20 + MARGIN_S, took
    assert _activity(got, "dep")["result"] in ("refused", "passed", "vacuous", "failed"), _activity(got, "dep")


# --- M1: a link whose target is outside the repository is never read in G6's own process -------------------------------

def test_review_m1_test_adequacy_never_reads_a_link_to_a_file_outside_the_repository(tmp_path, monkeypatch):
    outside = tmp_path / "outside" / "settings.py"
    outside.parent.mkdir()
    outside.write_text('def token(kind):\n    if kind == "OUTSIDE-SECRET-1234":\n        return 1\n    return 0\n',
                       encoding="utf-8")
    root = _repo(tmp_path, {"app.py": "def double(x):\n    return x * 2\n",
                            "tests/test_app.py": "from app import double\n\n\ndef test_double():\n"
                                                 "    assert double(2) == 4\n"})
    try:
        os.symlink(str(outside), root / "settings.py")   # the patch adds a link to a file outside the repository
    except OSError:
        pytest.skip("this system cannot make a symbolic link")
    _patch(root, {"app.py": "def double(x):\n    return x + x\n"})
    Tiers(monkeypatch)
    Mutants(monkeypatch)
    requirements = [{"id": "R1", "text": "double doubles", "checks": ["tests/test_app.py::test_double"]}]
    got = review(root, requirements, search={"test_adequacy": True})
    assert "OUTSIDE-SECRET-1234" not in json.dumps(got)
    # the launch review's I2 (d63f40a): a .py link the patch adds could put any code under a name Python imports, an
    # integrity finding, so test adequacy is not run in the review (it would run under tests that may be weakened)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    found = [r for r in got["assessment"]["reasons"] if r["code"] == "changed_file_can_change_tests"]
    assert found and "settings.py" in found[0]["detail"], got["assessment"]["reasons"]
    assert any("settings.py" in u and "outside the repository" in u for u in got["assessment"]["unknowns"])
    # test adequacy itself, asked directly, still leaves the link out unread
    change = repo_tests.change(root)
    stated = [patch_review.Requirement.model_validate(req) for req in requirements]
    norm = {raw: raw for req in requirements for raw in req["checks"]}
    entries, _ = verification_search.measure_adequacy(
        change, stated, norm, {"tests/test_app.py::test_double": {"verdict": "passed", "detail": ""}},
        deadline=time.monotonic() + 60, base_seconds=1.0)
    [adequacy] = entries
    left = [s for s in adequacy["skipped"] if s["file"] == "settings.py"]
    assert left and "outside the repository" in left[0]["reason"], adequacy
    assert "OUTSIDE-SECRET-1234" not in json.dumps(entries)


# --- the review's import roots, and a run that loaded the changed code from elsewhere -----------------------------------

#: A run's record that it loaded the changed module from outside the tree it tested: an installed copy (``repo_tests``).
SHADOWED = {"loaded_outside": {"textcodec": "/usr/lib/python3/dist-packages/textcodec.py"}, "loaded_complete": True}
FREE_KEEP = {"id": "p-keep", "requirement_id": "R1", "intent": "preserve", "file": "test_keep_codec.py",
             "source": "from textcodec import decode, encode\n\n\ndef test_keep():\n    assert decode(encode('a,b')) == 'a,b'\n"}


def _ran(passed=True, **extra):
    return lambda node: {**(result([node]) if passed else result(failed=[node], output=f"FAILED {node} - Failed: x\n",
                                                                  messages={node: "Failed: x"})), **extra}


@pytest.mark.parametrize("side, seen", [("patch", SHADOWED), ("base", SHADOWED),
                                        ("patch", {"loaded_outside": {}, "loaded_complete": False})],
                         ids=["patch-loaded-elsewhere", "base-loaded-elsewhere", "patch-cannot-say"])
@pytest.mark.parametrize("passed", [True, False], ids=["passing", "failing"])
def test_a_proposed_or_compiled_check_whose_run_loaded_the_changed_code_elsewhere_is_a_check_error(
        tmp_path, monkeypatch, side, seen, passed):
    """Pool compatibility's guard, applied to verification search: a run that loaded a module the patch changed from
    outside the tree it tested (an installed copy), or cannot say what it loaded, tested code the patch did not change.
    Its pass is never not_refuted and its failure never a counterexample: a check_error, with that reason."""
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    runs = {"patch": _ran(passed, **(seen if side == "patch" else {})),
            "base": _ran(True, **(seen if side == "base" else {}))}
    Compiled(monkeypatch, {"relation.v1": runs, FREE_KEEP["source"]: runs})
    got = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP], proposed_checks=[FREE_KEEP])
    for ident in ("t-codec", "p-keep"):
        found = _activity(got, ident)
        assert found["result"] == "check_error", found
        assert "loaded" in found["detail"] and "import_roots" in found["detail"], found["detail"]
    assert not _codes(got) & {"unconfirmed_counterexample", "unconfirmed_failure"}
    assert got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"] == []


def test_a_run_that_loaded_only_other_modules_from_outside_keeps_its_result(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    other = {"loaded_outside": {"requests": "/usr/lib/python3/dist-packages/requests/__init__.py"},
             "loaded_complete": True}
    Compiled(monkeypatch, {"relation.v1": {"patch": _ran(False, **other), "base": _ran(True, **other)}})
    got = review(root, STATED["CODEC"], template_instances=[ROUND_TRIP])
    assert _activity(got, "t-codec")["result"] == "counterexample"


LIB_CODEC = {"lib/textcodec.py": CODEC_BASE, "lib/README.txt": "the package lives here\n",
             "tests/test_textcodec.py": CODEC_TESTS}


def test_compiled_and_proposed_checks_and_mutants_run_with_the_reviews_import_roots(tmp_path, monkeypatch):
    """The checks verification search runs see the code as the stated checks do: the review's import roots first on
    the import path; and a template's names resolve where those runs import from."""
    root = _patch(_repo(tmp_path, LIB_CODEC), {"lib/textcodec.py": CODEC_PATCH})
    rule = [{"id": "R1", "text": "text survives encoding", "checks": ["tests/test_textcodec.py::test_plain"]}]
    Tiers(monkeypatch)
    runs = Compiled(monkeypatch, {"relation.v1": {"patch": "passed", "base": "passed"},
                                  FREE_KEEP["source"]: {"patch": "passed", "base": "passed"}})
    mutants = Mutants(monkeypatch)
    unrooted = review(root, rule, template_instances=[ROUND_TRIP])
    assert _activity(unrooted, "t-codec")["result"] == "refused"   # textcodec is found only through the root
    assert "no module textcodec" in _activity(unrooted, "t-codec")["reason"]
    got = review(root, rule, template_instances=[ROUND_TRIP], proposed_checks=[FREE_KEEP], import_roots=["lib"],
                 search={"test_adequacy": True})
    assert _activity(got, "t-codec")["result"] == "not_refuted" and _activity(got, "p-keep")["result"] == "not_refuted"
    assert runs.roots[-4:] == [("lib",)] * 4, runs.roots
    assert mutants.calls and all(roots == ("lib",) for roots in mutants.roots), mutants.roots
    assert got["assessment"]["scope"]["import_roots"] == ["lib"]


def test_a_mutant_run_that_loaded_the_changed_code_elsewhere_is_not_counted(tmp_path, monkeypatch):
    """A mutant G6 put in the copy's file was never loaded when the run took the module from an installed copy, so it
    would read as a survivor: it is listed as not run, with why."""
    root = _patch(_repo(tmp_path, CLAMP), CLAMP_PATCH)
    Tiers(monkeypatch)
    Mutants(monkeypatch, extra={"loaded_outside": {"clamp": "/usr/lib/python3/dist-packages/clamp.py"},
                                "loaded_complete": True})
    got = review(root, CLAMP_RULE, search={"test_adequacy": True})
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    assert (adequacy["killed"], adequacy["survived"]) == (0, 0), adequacy
    assert len(adequacy["skipped"]) == 3 and all("loaded" in s["reason"] for s in adequacy["skipped"])


# --- the MCP surface ------------------------------------------------------------------------------------------------------

def test_the_mcp_tool_takes_template_instances_and_search_and_keeps_its_envelope_and_codes(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    root = _patch(_repo(tmp_path, CODEC), {"textcodec.py": CODEC_PATCH})
    Tiers(monkeypatch)
    Compiled(monkeypatch, {"relation.v1": {"patch": "failed", "base": "passed"}})
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def finished(client, arguments) -> dict:
        job = data(await client.call_tool("g6_review_patch", {"repo": str(root), "task": "Escape newlines.",
                                                              "requirements": STATED["CODEC"], **arguments}))
        for _ in range(400):
            got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
            if got.get("status") in ("completed", "failed"):
                assert got["status"] == "completed", got
                return got["result"]
            await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    async def flow():
        tool = (await server.get_tools())["g6_review_patch"]
        async with Client(server) as client:
            plain = await finished(client, {})
            searched = await finished(client, {"template_instances": [ROUND_TRIP], "search": {"templates": True}})
            refused = await finished(client, {"template_instances": [{**ROUND_TRIP, "bindings": {
                "relation": "round_trip", "function": "os.system('x')", "inverse": "textcodec.decode",
                "inputs": ["a"]}}]})
        return tool, plain, searched, refused

    tool, plain, searched, refused = asyncio.run(flow())
    for name in ("template_instances", "search"):
        assert name in tool.parameters["properties"] and name in tool.description
    assert plain["assessment"]["outcome"] == "supported_within_scope"
    assert searched["assessment"]["outcome"] == "insufficient_evidence"
    assert "unconfirmed_counterexample" in _codes(searched)
    for got in (plain, searched):
        assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_PATCH_REVIEW"
        assert check_envelope(got) == [] and got["request_id"] and "run_id" in got
    assert set(searched) == set(plain)
    assert [set(item) for item in searched["evidence"]] == [set(item) for item in plain["evidence"]]
    assert refused["completion_state"] == "blocked-escalated"
    assert refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "assessment" not in refused


# --- real confined runs (Linux bubblewrap; run in Docker on Windows) ------------------------------------------------------

@pytest.mark.parametrize("kind, item, expected, code", [
    ("CODEC", ROUND_TRIP, "counterexample", "unconfirmed_counterexample"),
    ("CLI", CLI_EXAMPLE, "unconfirmed_failure", "unconfirmed_failure"),
    ("NOTES", OWNER_RULE, "counterexample", "unconfirmed_counterexample"),
    ("PIPE", KEEP_NORMALISE, "counterexample", "unconfirmed_counterexample"),
], ids=["library-round-trip", "cli-parser-example", "handler-ownership-rule", "pipeline-differential"])
def test_confined_each_template_catches_its_planted_defect_on_its_kind_of_application(tmp_path, bwrap_backend, kind,
                                                                                       item, expected, code):
    files = {"CODEC": (CODEC, {"textcodec.py": CODEC_PATCH}), "CLI": (CLI, {"cli.py": CLI_PATCH}),
             "NOTES": (NOTES, {"notes.py": NOTES_PATCH}), "PIPE": (PIPE, {"pipeline.py": PIPE_PATCH})}[kind]
    root = _patch(_repo(tmp_path, files[0]), files[1])
    before = patch_review.digest(repo_tests.snapshot(root))
    got = review(root, STATED[kind], template_instances=[item])
    assessment = got["assessment"]
    found = _activity(got, item["id"])
    assert found["result"] == expected, found
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", code)]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert got["completion_state"] == "qualified-draft"
    assert got["artifact_digest"] == before == patch_review.digest(repo_tests.snapshot(root))
    # on the base commit's code alone, nothing is found
    clean = _repo(tmp_path, files[0], "clean")
    _patch(clean, {"NOTES_CHANGELOG.txt": "a change with no code\n"})
    quiet = review(clean, STATED[kind], template_instances=[item])
    assert _activity(quiet, item["id"])["result"] in ("not_refuted", "unconfirmed_failure"), _activity(quiet, item["id"])


def test_confined_a_compiled_reproduction_runs_with_plain_pytest_without_g6(tmp_path, bwrap_backend):
    root = _patch(_repo(tmp_path, NOTES), {"notes.py": NOTES_PATCH})
    got = review(root, STATED["NOTES"], template_instances=[OWNER_RULE])
    [defect] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    reproduction = defect["reproduction"]
    patched = tmp_path / "patched"
    base = _repo(tmp_path, NOTES, "base-copy")
    import shutil
    shutil.copytree(root, patched, ignore=shutil.ignore_patterns(".git"))
    for tree in (patched, base):
        (tree / reproduction["file"]).parent.mkdir(parents=True, exist_ok=True)
        (tree / reproduction["file"]).write_text(reproduction["text"], encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME")}
    boot = ("import sys; sys.modules['g6'] = None; import pytest; "
            f"raise SystemExit(pytest.main({reproduction['arguments']!r}))")
    on_patch = subprocess.run([sys.executable, "-c", boot], cwd=patched, env={**env, **reproduction["environment"]},
                              capture_output=True, text=True, timeout=120)
    at_base = subprocess.run([sys.executable, "-c", boot], cwd=base, env={**env, **reproduction["environment"]},
                             capture_output=True, text=True, timeout=120)
    assert on_patch.returncode == 1 and "1 failed" in on_patch.stdout, on_patch.stdout + on_patch.stderr
    assert at_base.returncode == 0 and "1 passed" in at_base.stdout, at_base.stdout + at_base.stderr


def test_confined_forbidden_dependency_reads_imports_only_and_runs_no_code(tmp_path, bwrap_backend):
    root = _shop(tmp_path, {"pyproject.toml": LAYERS_TOML})
    got = review(root, SHOP_RULE, template_instances=[{**SHOP_LAYERS, "requirement_id": "R2"}])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert _requirement(got, "R2")["status"] == "failed"
    assert _activity(got, "t-layers")["ran_code"] is False


CALC_BASE = "def add(a, b):\n    return a + b\n"
CALC_BROKEN = "def add(a, b):\n    return a - b\n"


def _installed(tmp_path: Path, monkeypatch) -> Path:
    """A repository in a lib/ layout (lib/calc), committed, with calc also installed (the base code) in a virtual
    environment outside it that G6_REPO_PYTHON names, as tests/harness/test_patch_review_imports.py builds one; then
    the defective patch to lib/calc. The stated check is one the patch does not touch."""
    root = _repo(tmp_path, {"lib/calc/__init__.py": CALC_BASE,
                            "tests/test_other.py": "def test_other():\n    assert True\n"}, "app")
    env = tmp_path / "installed-env"
    # --copies: an environment linked to G6's own Python is refused (L1)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", "--copies", str(env)],
                   check=True,
                   capture_output=True, timeout=180)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               check=True, capture_output=True, text=True).stdout.strip())
    (site / "calc").mkdir(parents=True)
    (site / "calc" / "__init__.py").write_text(CALC_BASE, encoding="utf-8")
    (site / "g6_test_pytest.pth").write_text(str(Path(pytest.__file__).resolve().parents[1]) + "\n", encoding="utf-8")
    monkeypatch.setenv(repo_tests.REPO_PYTHON, str(python))
    (root / "lib" / "calc" / "__init__.py").write_text(CALC_BROKEN, encoding="utf-8")
    return root


def test_confined_proposed_and_compiled_checks_see_the_patch_through_import_roots_never_the_installed_copy(
        tmp_path, bwrap_backend, monkeypatch):
    """Without import roots the proposed check imports the installed copy, which the patch did not change: a check
    error that says so, never not_refuted. With import_roots=["lib"] the same check, and a compiled example, find the
    defect, run by the Python G6_REPO_PYTHON names and with the variables G6_REPO_ENV gives."""
    root = _installed(tmp_path, monkeypatch)
    monkeypatch.setenv(repo_tests.REPO_ENV, json.dumps({"VS_FLAG": "on"}))
    rule = [{"id": "R1", "text": "add returns the sum", "checks": ["tests/test_other.py::test_other"]}]
    keep = {"id": "p-add", "requirement_id": "R1", "intent": "preserve", "file": "tests/test_keep_add.py",
            "source": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
    env = {"id": "p-env", "requirement_id": "R1", "intent": "preserve", "file": "tests/test_env_seen.py",
           "source": "import os\n\n\ndef test_env():\n    assert os.environ.get('VS_FLAG') == 'on'\n"}
    example = instance("t-add", "example.v1", {"function": "calc.add", "cases": [{"args": [2, 3], "returns": 5}]})
    plain = review(root, rule, proposed_checks=[keep, env], template_instances=[example])
    found = _activity(plain, "p-add")
    assert found["result"] == "check_error" and "loaded" in found["detail"], found
    assert _activity(plain, "t-add")["result"] == "refused"   # calc is in lib/, which no run imports from
    assert _activity(plain, "p-env")["result"] == "not_refuted", _activity(plain, "p-env")
    rooted = review(root, rule, proposed_checks=[keep, env], template_instances=[example], import_roots=["lib"])
    for ident in ("p-add", "t-add"):
        assert _activity(rooted, ident)["result"] == "counterexample", _activity(rooted, ident)
    assert all(run["interpreter_source"] == "setting" for run in _activity(rooted, "p-add")["runs"])
    assert rooted["assessment"]["outcome"] == "insufficient_evidence"
    assert _codes(rooted) >= {"unconfirmed_counterexample"}
    [reproduction] = [d["reproduction"] for d in rooted["assessment"]["completion"]["defects_reproduced"][
        "proposed_checks"] if d["check"] == "p-add"]
    assert reproduction["environment"]["PYTHONPATH"].split(os.pathsep)[0] == "lib"


# --- M2: a reproduction loads modules in the order G6's run did --------------------------------------------------------

TWO_PKGS = {"pkg/__init__.py": "VALUE = 1\n", "lib/pkg/__init__.py": "VALUE = 2\n",
            "app.py": "def double(x):\n    return x * 2\n",
            "tests/test_app.py": "from app import double\n\n\ndef test_double():\n    assert double(2) == 4\n"}
VALUE_CHECK = {"id": "value", "requirement_id": "R1", "intent": "change", "file": "tests/test_value.py",
               "source": "import pkg\n\n\ndef test_value():\n    assert pkg.VALUE == 1, pkg.__file__\n"}
TWO_RULE = [{"id": "R1", "text": "pkg keeps VALUE 1", "checks": ["tests/test_app.py::test_double"]}]


def _run_as_given(root: Path, reproduction: dict, where: Path) -> int:
    """The reproduction's command, exactly as G6 gives it, from the top of a copy of the tree without .git."""
    import shutil

    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no POSIX shell to run the command in")
    shutil.copytree(root, where, ignore=shutil.ignore_patterns(".git"))
    (where / reproduction["file"]).parent.mkdir(parents=True, exist_ok=True)
    (where / reproduction["file"]).write_text(reproduction["text"], encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "SYSTEMROOT", "TMP", "TEMP")}
    return subprocess.run([shell, "-c", reproduction["command"]], cwd=where, env=env, capture_output=True, text=True,
                          timeout=120).returncode


def test_review_m2_a_reproduction_puts_the_import_roots_before_the_top_folder_as_g6s_run_did(tmp_path, monkeypatch):
    """The reviewer's case: pkg in the top folder and in lib, with lib an import root. G6's run imported lib's pkg
    and the check failed there; run exactly as G6 gives it, the reproduction must fail too, on any Python 3.9 on."""
    root = _patch(_repo(tmp_path, TWO_PKGS), {"lib/pkg/__init__.py": "VALUE = 2\nOTHER = 0\n"})
    Tiers(monkeypatch)
    rooted = {"import_path": ["lib", "."]}   # G6's run, as repo_tests records its import path
    failed = lambda node: {**result(failed=[node], output=f"FAILED {node} - AssertionError: lib/pkg\n",  # noqa: E731
                                    messages={node: "AssertionError: lib/pkg"}), **rooted}
    Compiled(monkeypatch, {VALUE_CHECK["source"]: {"patch": failed, "base": failed}})
    got = review(root, TWO_RULE, proposed_checks=[VALUE_CHECK], import_roots=["lib"])
    [found] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert found["result"] == "unconfirmed_failure", found
    reproduction = found["reproduction"]
    assert reproduction["environment"]["PYTHONPATH"].split(os.pathsep)[0] == "lib"
    assert " -P " not in f" {reproduction['command']} "
    assert _run_as_given(root, reproduction, tmp_path / "copy") != 0   # G6's verdict: it fails


def test_confined_a_reproduction_with_import_roots_gives_g6s_verdict(tmp_path, bwrap_backend):
    root = _patch(_repo(tmp_path, TWO_PKGS), {"lib/pkg/__init__.py": "VALUE = 2\nOTHER = 0\n"})
    got = review(root, TWO_RULE, proposed_checks=[VALUE_CHECK], import_roots=["lib"])
    [found] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert found["result"] == "unconfirmed_failure", found
    assert _run_as_given(root, found["reproduction"], tmp_path / "copy") != 0


def test_confined_surviving_mutants_are_reported_and_change_no_outcome(tmp_path, bwrap_backend):
    root = _patch(_repo(tmp_path, CLAMP), CLAMP_PATCH)
    got = review(root, CLAMP_RULE, search={"test_adequacy": True})
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    [adequacy] = got["assessment"]["completion"]["test_adequacy"]
    assert (adequacy["killed"], adequacy["survived"]) == (1, 2), adequacy
    assert all(run["verdict"] in ("passed", "failed") for run in got["assessment"]["completion"]["activities"]
               if run["activity"] == "mutation run")
    assert got["artifact_digest"] == patch_review.digest(repo_tests.snapshot(root))


# --- reviewer B's B5: a repository's ignore_imports rule cannot hold G6's process past its deadline -------------------

def test_a_rule_of_many_wildcards_against_a_long_module_name_is_matched_in_a_moment():
    """Reviewer B's probe (probe_ignore_imports_backtracking.py): 16 ``**`` parts against a module of 32 parts, which
    the rule's last part never matches, took 5.8 seconds as a regular expression, about 6 times longer per step."""
    rule = "app." + ".".join(["**"] * 16) + ".zz -> app.web"
    source = "app." + ".".join(["a"] * 30) + ".y"
    started = time.perf_counter()
    assert not verification_templates._ignored({"from": source, "to": "app.web"}, (rule,))
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(("pattern", "name", "matches"), [
    ("shop.data", "shop.data", True), ("shop.data", "shop.data.x", False),
    ("shop.*", "shop.data", True), ("shop.*", "shop.data.x", False), ("shop.*", "shop", False),
    ("shop.**", "shop.data", True), ("shop.**", "shop.data.x.y", True), ("shop.**", "shop", False),
    ("**.models", "shop.models", True), ("**.models", "shop.a.models", True), ("**.models", "models", False),
    ("shop.*.api", "shop.x.api", True), ("shop.*.api", "shop.x.y.api", False),
    ("shop.**.api", "shop.x.y.api", True), ("shop.**.api", "shop.api", False),
    ("shop.**.**.api", "shop.x.y.api", True), ("shop.**.**.api", "shop.x.api", False),
])
def test_ignore_imports_wildcards_mean_what_import_linter_says(pattern, name, matches):
    """``*`` is one part of a module's name, ``**`` one or more, any other part itself; as before (a regular
    expression gave the same answers), now part by part."""
    assert verification_templates._ignored({"from": name, "to": "x"}, (f"{pattern} -> x",)) is matches
