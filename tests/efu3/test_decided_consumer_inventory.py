"""Code-derived inventory of decided readers and producer-entry adapters."""
import ast
from pathlib import Path

from g6.workforce import repo_tests
from tests.harness.report_consumer_inventory import RECEIVERS, consumer_fields


READERS = {
    "algorithm_design/autonomous.py": {"_observed"},
    "bundles/agent_change.py": {"_as_run", "run"},
    "harness/completion_loop.py": {"_read"},
    "harness/patch_review.py": {"_confined", "_failed_ids", "_in_repository", "_listing", "_observed",
                                "_on_base", "_verdicts", "phases_of", "_not_loaded", "_patch_imports_missing",
                                "_not_found", "_collection_errors"},
    "harness/verification_search.py": {"reading", "_messages", "_refused_access", "_uncollected", "read"},
    "metaharness/coding.py": {"oracle", "run_mutant"},
    "workforce/bench.py": {"_in_place"},
    "workforce/receipt_store.py": {"_body"},
    "workforce/repo_tests.py": {"_judge", "_progress", "shadowed", "run_before", "loaded_from"},
}

# Entry points which receive a producer's return. Dispatchers pass to the named
# decided reader; the replay producer consumes its own reading before returning.
ADAPTERS = {
    "algorithm_design/autonomous.py": {"baseline", "run"},
    "bundles/agent_change.py": {"runner", "run"},
    "harness/patch_review.py": {"_execute", "_execute_at_base"},
    "harness/verification_search.py": {"_run_check", "_run_mutant"},
    "metaharness/coding.py": {"oracle"},
    "workforce/bench.py": {"_in_place"},
}

WHOLE_REPO_CONTEXTS = {
    "algorithm_design/autonomous.py": {"_observed"},
    "bundles/agent_change.py": {"_as_run", "run"},
    "metaharness/coding.py": {"oracle", "run_mutant"},
    "workforce/bench.py": {"_in_place"},
}


def inventory(root, *, with_ownership=False):
    readers, adapters, ownership = {}, {}, {}
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        read_names = {"read_report", "rt.read_report", "repo_tests.read_report"}
        owner_names = set()
        modules = {"rt", "repo_tests"}
        entry_names = {f"{alias}.{op}" for alias in ("rt", "repo_tests")
                       for op in ("run", "_run", "run_before", "run_at_base")}
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                modules.update(alias.asname or alias.name for alias in item.names
                               if alias.name == "g6.workforce.repo_tests")
            if isinstance(item, ast.ImportFrom) and item.module == "g6.workforce":
                modules.update(alias.asname or alias.name for alias in item.names if alias.name == "repo_tests")
            if isinstance(item, ast.ImportFrom) and item.module == "g6.workforce.repo_tests":
                for alias in item.names:
                    assert alias.name != "*", "a wildcard runner import cannot be classified"
                    if alias.name == "read_report":
                        read_names.add(alias.asname or alias.name)
                    elif alias.name == "loaded_from":
                        owner_names.add(alias.asname or alias.name)
                    elif alias.name in ("run", "_run", "run_before", "run_at_base"):
                        entry_names.add(alias.asname or alias.name)
        read_names.update(f"{module}.read_report" for module in modules)
        owner_names.update(f"{module}.loaded_from" for module in modules)
        entry_names.update(f"{module}.{op}" for module in modules
                           for op in ("run", "_run", "run_before", "run_at_base"))
        def visit(node, scope=""):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = node.name
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in read_names:
                    readers.setdefault(relative, set()).add(scope)
                if name in owner_names:
                    ownership.setdefault(relative, set()).add(scope)
                # A caller-provided runner takes the same schema as the real
                # producer; include the (run or repo_tests.run) dispatch.
                if name in entry_names or name == "run or repo_tests.run":
                    adapters.setdefault(relative, set()).add(scope)
                if name == "getattr" and node.args and ast.unparse(node.args[0]) in modules:
                    assert len(node.args) > 1 and isinstance(node.args[1], ast.Constant), "a dynamic runner lookup is unclassified"
                    if node.args[1].value in ("run", "_run", "run_before", "run_at_base"):
                        adapters.setdefault(relative, set()).add(scope)
            for child in ast.iter_child_nodes(node):
                visit(child, scope)
        visit(tree)
    return (readers, adapters, ownership) if with_ownership else (readers, adapters)


def test_every_decided_reader_and_producer_entry_is_listed_from_code():
    assert inventory(Path(repo_tests.__file__).parents[1]) == (READERS, ADAPTERS)


def test_inventory_also_finds_a_new_nested_consumer(tmp_path):
    (tmp_path / "new.py").write_text(
        "def outside():\n    def inside(raw):\n        return rt.read_report(raw).outcome\n", encoding="utf-8")
    assert inventory(tmp_path)[0] == {"new.py": {"inside"}}


def test_inventory_finds_a_bare_import_under_another_name(tmp_path):
    (tmp_path / "new.py").write_text(
        "from g6.workforce.repo_tests import run as launch, read_report as decide\n"
        "def other(raw):\n    return decide(launch(raw)).outcome\n", encoding="utf-8")
    assert inventory(tmp_path) == ({"new.py": {"other"}}, {"new.py": {"other"}})


def test_inventory_finds_a_module_alias_and_a_literal_lookup(tmp_path):
    (tmp_path / "new.py").write_text(
        "from g6.workforce import repo_tests as runner\n"
        "def other(raw):\n    return runner.read_report(getattr(runner, 'run')(raw)).outcome\n", encoding="utf-8")
    assert inventory(tmp_path) == ({"new.py": {"other"}}, {"new.py": {"other"}})


def test_captured_runner_output_has_only_a_display_consumer():
    root = Path(repo_tests.__file__).parents[1]
    found = set()
    for relative in RECEIVERS:
        fields = consumer_fields((root / relative).read_text(encoding="utf-8"), relative)
        found.update((relative, name) for name, keys in fields.items() if "output" in keys)
    assert found == {("algorithm_design/autonomous.py", "_observed")}
    tree = ast.parse((root / "algorithm_design/autonomous.py").read_text(encoding="utf-8"))
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_observed")
    displays = [n.value for n in ast.walk(function) if isinstance(n, ast.keyword) and n.arg == "log_excerpt"]
    assert len(displays) == 1 and 'raw.get(\'output\'' in ast.unparse(displays[0])


def test_every_whole_repository_adapter_uses_the_shared_loading_rule():
    assert inventory(Path(repo_tests.__file__).parents[1], with_ownership=True)[2] == WHOLE_REPO_CONTEXTS
