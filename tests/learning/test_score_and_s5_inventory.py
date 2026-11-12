# SPDX-License-Identifier: Apache-2.0
"""Source-wide inventories: one judge score reader, and no S5 consumer outside its Python-only boundary.

Scanning every source module also covers helpers reached indirectly from MCP, CLI, component ops and the
self-improvement loop. Imports, aliases, relative imports, attribute access and literal dynamic lookups are checked;
this is a source regression guard, not a sandbox against arbitrary code constructed at runtime.
"""
import ast
from collections import Counter
from importlib.util import resolve_name
from pathlib import Path
import tomllib

import pytest

from g6.core.registry import ComponentRegistry
from g6.learning import paired_eval, runner, scoring


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
S5_MODULES = {"g6.learning.paired_eval", "g6.learning.method_kind"}
S5_NAMES = {"PairedEvaluator", "PairReceipt", "paired_eval", "method_kind"}


def _module(path):
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _nodes(node, owner="<module>"):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        owner = node.name
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return  # Human prose is not a dependency.
    yield owner, node
    for child in ast.iter_child_nodes(node):
        yield from _nodes(child, owner)


def _score_reads(tree):
    for owner, node in _nodes(tree):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "pop"} and node.args and isinstance(node.args[0], ast.Constant)):
            key = node.args[0].value
        if key == "score":
            yield owner


def test_all_numeric_score_reads_have_a_reviewed_owner():
    reads = Counter((path.relative_to(SRC).as_posix(), owner)
                    for path in SRC.rglob("*.py")
                    for owner in _score_reads(ast.parse(path.read_text(encoding="utf-8"))))
    # Non-judge scores: search/rank results, abstraction counterexamples, ML feature names and fit results.
    other_scores = {("g6/abstraction/cegar.py", "refine_until"): 2,
                    ("g6/cognitive_bridge/engine.py", "rank"): 2,
                    ("g6/core/discovery.py", "find"): 2,
                    ("g6/ml/_types.py", "_leakage_findings"): 4,
                    ("g6/operator/queue.py", "rank"): 1,
                    ("g6/knowledge/knowledge_indexer/index.py", "find"): 1,
                    ("g6/ml/adapt_automl/flaml_child.py", "search"): 2}
    assert reads == Counter({**other_scores, ("g6/learning/scoring.py", "_read_judge_score"): 1})


def test_every_learning_judge_consumer_uses_the_same_scorer():
    assert paired_eval.score_instance is scoring.score_instance
    assert runner.score_instance is scoring.score_instance
    callers = Counter()
    for path in SRC.rglob("*.py"):
        for owner, node in _nodes(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and any(k.arg == "judge" for k in node.keywords)
                    and ((isinstance(node.func, ast.Name) and node.func.id == "score_instance")
                         or (isinstance(node.func, ast.Attribute) and node.func.attr == "score_instance"))):
                callers[(path.relative_to(SRC).as_posix(), owner)] += 1
    assert callers == Counter({("g6/learning/runner.py", "_result_for"): 1,
                               ("g6/learning/paired_eval.py", "_arm"): 1,
                               ("g6/learning/paired_eval.py", "_arm_again"): 1})


def _s5_references(source, module, package=False):
    tree = ast.parse(source)
    if module in S5_MODULES:
        return []
    allowed = set()
    if module == "g6.learning" and package:
        # Only the existing explicit public re-export is allowed, never dispatch or a block declaration.
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "g6.learning.paired_eval":
                if {(a.name, a.asname) for a in node.names} == {("PairedEvaluator", None), ("PairReceipt", None)}:
                    allowed.update(ast.walk(node))
            if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__"
                                                    for t in node.targets) and isinstance(node.value, ast.List)):
                allowed.update(n for n in node.value.elts if isinstance(n, ast.Constant)
                               and n.value in {"PairedEvaluator", "PairReceipt"})
    found = []
    for _, node in _nodes(tree):
        if node in allowed:
            continue
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = module if package else module.rpartition(".")[0]
                base = resolve_name("." * node.level + base, parent)
            names = [base] + [base + "." + a.name for a in node.names]
            if base == "g6.learning" and any(a.name == "*" for a in node.names):
                names.append("g6.learning.PairedEvaluator")
        elif isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names = [node.value]
        if any(name in S5_NAMES or any(name == target or name.startswith((target + ".", target + ":"))
                                      for target in S5_MODULES)
               or name in {"g6.learning.PairedEvaluator", "g6.learning.PairReceipt",
                           "g6.learning:PairedEvaluator", "g6.learning:PairReceipt"} for name in names):
            found.append((node.lineno, names))
    return found


def test_no_source_consumer_reaches_s5():
    paths = list(SRC.rglob("*.py"))
    modules = {_module(path) for path in paths}
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    registry = ComponentRegistry()
    roots = {entry.split(":")[0] for entry in scripts.values()}
    roots.update(ref.module.split(":")[0] for ref in registry.refs.values() if ref.source == "builtin")
    roots.update({"g6.core.registry", "g6.self_training.improvement_loop"})
    assert roots <= modules, sorted(roots - modules)
    violations = {path.relative_to(SRC).as_posix(): refs for path in paths
                  if (refs := _s5_references(path.read_text(encoding="utf-8"), _module(path),
                                            path.name == "__init__.py"))}
    assert violations == {}
    assert registry.get_class("learning") is None


@pytest.mark.parametrize("source", [
    "from g6.learning import PairedEvaluator as E",
    "from g6.learning import PairReceipt as R",
    "from g6.learning import *",
    "import g6.learning.paired_eval as ev",
    "from .paired_eval import PairedEvaluator as E",
    "from g6.learning.method_kind import admit_trial as hook",
    "from g6.learning import method_kind as mk",
    "import g6.learning as learn\nevaluator = learn.PairedEvaluator",
    "import g6.learning as learn\nreceipt = getattr(learn, 'PairReceipt')",
    "import importlib\nmod = importlib.import_module('g6.learning.method_kind')",
    "entry_point = 'g6.learning.method_kind:admit_trial'",
    "entry_point = 'g6.learning:PairedEvaluator'",
])
def test_inventory_catches_new_import_alias_and_dynamic_literal_edges(source):
    assert _s5_references(source, "g6.learning.new_consumer")


def test_reexport_exception_does_not_allow_a_shipped_op():
    source = ("from g6.learning.paired_eval import PairedEvaluator, PairReceipt\n"
              "__all__ = ['PairedEvaluator', 'PairReceipt']\n")
    assert not _s5_references(source, "g6.learning", package=True)
    assert _s5_references(source + "__g6_block__ = 'PairedEvaluator'\n", "g6.learning", package=True)
    assert _s5_references(source + "def run(): return PairedEvaluator()\n", "g6.learning", package=True)
