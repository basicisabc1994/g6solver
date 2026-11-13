"""A check's scope is read as pytest reads a selection: compared with pytest itself, never with a copy of its rule.

Review 4's finding 5 (own_parser_for_foreign_format): ``report_covers`` took any test id beginning ``check + "["`` as
the check's, so ``test_add[a]`` borrowed the failure of ``test_add[a][b]``, another parameter of the same function, as
its negative control. The differential test below runs the installed pytest on real files and asks it which tests each
selection names; the inventory lists, from the code, every place that compares a check with a test id by prefix.
"""
import ast
import sys
from pathlib import Path

import pytest

from g6.workforce import repo_tests as rt

#: Parameter ids that hold every separator pytest's node ids use.
IDS = ["a", "a][b", "x::y", "p/q", "b]", "[c", "a[", "a]"]
SOURCE = (
    "import pytest\n\n\n"
    "@pytest.mark.parametrize('value', range({n}), ids={ids!r})\n"
    "def test_add(value):\n    assert value >= 0\n\n\n"
    "def test_add_more():\n    assert True\n\n\n"
    "class TestAdd:\n"
    "    @pytest.mark.parametrize('value', [1, 2], ids=['a', 'a][b'])\n"
    "    def test_add(self, value):\n        assert value\n\n"
    "    def test_plain(self):\n        assert True\n"
)


class _Seen:
    def __init__(self):
        self.ids = []

    def pytest_collection_modifyitems(self, session, config, items):
        self.ids = [item.nodeid for item in items]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """One test file under three folders pytest collects: a plain one, one whose name another's begins, and one whose
    name holds a bracket. The module name is this test's own, and leaves ``sys.modules`` with it."""
    name = f"test_scope_{tmp_path.name.replace('-', '_')}.py"
    for folder in FOLDERS:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).write_text(SOURCE.format(n=len(IDS), ids=IDS), encoding="utf-8")
    (tmp_path / "empty.ini").write_text("[pytest]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = set(sys.modules)
    yield tmp_path
    for loaded in set(sys.modules) - before:
        del sys.modules[loaded]


FOLDERS = ("pkg", "pkg2", "pkg[1]")


def selected(root: Path, selection: str) -> set[str] | None:
    """The tests pytest itself collects for ``selection``; None when it refuses the selection."""
    seen = _Seen()
    code = pytest.main([selection, "--collect-only", "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
                        "-c", str(root / "empty.ini"), "--rootdir", str(root)], plugins=[seen])
    return set(seen.ids) if code == 0 else None


def selections(nodes: set[str]) -> list[str]:
    """Every scope the tests have (each test, function, class, file and folder), and for each test the spellings a
    prefix rule would confuse with it: its id cut short, its function with a bracket opened, itself with one more."""
    found = set(nodes)
    for node in nodes:
        path, _, names = node.partition("::")
        parts = names.partition("[")[0].split("::")
        function = path + "::" + "::".join(parts)
        found.update(path + "::" + "::".join(parts[:end]) for end in range(1, len(parts) + 1))
        folders = path.split("/")
        found.update("/".join(folders[:end]) for end in range(1, len(folders) + 1))
        found.update({node[:-1], function + "[", function + "[a", node + "[x]"})
    return sorted(found)


def test_every_selection_covers_exactly_the_tests_pytest_selects(tree):
    everything = selected(tree, ".")
    assert everything and len(everything) == len(FOLDERS) * (len(IDS) + 4), everything
    assert {node.partition("::")[2].partition("[")[2] for node in everything} >= {ident + "]" for ident in IDS}
    bracketed = {node for node in everything if node.startswith("pkg[1]/")}
    plain = everything - bracketed
    compared = refused = 0
    for selection in selections(plain):
        named = selected(tree, selection)
        covered = {node for node in everything if rt.report_covers(selection, node)}
        if named is None:
            # pytest runs nothing for it, so no report of such a run holds a test: only the id itself may match
            refused += 1
            assert covered <= {selection}, (selection, covered)
            continue
        compared += 1
        assert covered == named, (selection, sorted(covered ^ named))
    # both kinds were met: every scope of the two plain folders, and the spellings pytest refuses
    assert compared >= 2 * (len(IDS) + 8) and refused >= 3 * len(IDS), (compared, refused)

    # pytest collects the folder with a bracket in its name under its parent, and refuses every selection that names
    # it (it reads the bracket as a parameter). Its scopes are read as its plain twin's are, and never reach the twin.
    def twin(text: str) -> str:
        return "pkg" + text[len("pkg[1]"):]

    assert {twin(node) for node in bracketed} == {node for node in plain if node.startswith("pkg/")}
    for selection in selections(bracketed):
        assert selected(tree, selection) is None, selection
        assert not any(rt.report_covers(selection, node) for node in plain), selection
        for node in bracketed:
            assert rt.report_covers(selection, node) == rt.report_covers(twin(selection), twin(node)), (selection, node)

@pytest.mark.parametrize("check,node,covers", [
    ("t.py::test_add[a]", "t.py::test_add[a]", True),
    ("t.py::test_add[a]", "t.py::test_add[a][b]", False),        # review 4's finding 5
    ("t.py::test_add[a", "t.py::test_add[a::b]", False),          # a parameter id holding the separator
    ("t.py::test_add", "t.py::test_add[a][b]", True),
    ("t.py::test_add", "t.py::test_add[x::y]", True),
    ("t.py::test_add", "t.py::test_add_more", False),
    ("t.py::TestAdd", "t.py::TestAdd::test_add[a]", True),
    ("t.py::TestAdd::test_add[a]", "t.py::TestAdd::test_add[a][b]", False),
    ("t.py", "t.py::test_add[a][b]", True),
    ("pkg", "pkg/t.py::test_add[a]", True),
    ("pkg/", "pkg/t.py::test_add[a]", True),
    ("pkg", "pkg[1]/t.py::test_add", False),                      # a folder is no prefix of a bracketed sibling
    ("pkg", "pkg2/t.py::test_add", False),
    ("pkg[1]", "pkg[1]/t.py::test_add", True),                    # a folder covers what lies under it
    ("pkg[1]/t.py::test_add[a]", "pkg[1]/t.py::test_add[a][b]", False),
    ("a[1].py", "a[1].py::test_add", True),
    ("t.py::test_add", "t.py::test_add[a]::more", False),
])
def test_scope_cases(check, node, covers):
    assert rt.report_covers(check, node) is covers


def _prefix_tests(path: Path) -> dict[str, set[str]]:
    """Each function of ``path`` that tests a string by a prefix built with a separator of pytest's node ids."""
    found: dict[str, set[str]] = {}

    def visit(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            joined = {part.right.value for arg in node.args for part in ast.walk(arg)
                      if isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add)
                      and isinstance(part.right, ast.Constant) and part.right.value in ("::", "[", "/")}
            if joined:
                found.setdefault(scope, set()).update(joined)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


#: Every prefix test built with a separator of pytest's node ids, in the modules that read a run's report, from the
#: code: the one scope rule, and the others with what they compare. None but the rule reads a parameter.
PREFIX_TESTS = {
    ("workforce/repo_tests.py", "report_covers"): "the one rule: a check's scope over test ids",
    ("workforce/repo_tests.py", "moved"): "a renamed test file's ids at its new path: a file, never a parameter",
    ("workforce/repo_tests.py", "ours"): "a path under a folder of the repository: paths only",
    ("workforce/repo_tests.py", "_base_revision"): "a changed file under a copied link: paths only",
    ("harness/patch_review.py", "_each_once"): "a path under a folder already listed: paths only",
    ("harness/patch_review.py", "_names"): "whether a check's file is one of a set of files: paths only",
    ("harness/patch_review.py", "_not_loaded"): "a check's file against the files that failed to collect: paths only",
    ("harness/patch_review.py", "_written"): "the changed files under a check's folder: paths only",
}
READERS = ("workforce/repo_tests.py", "workforce/bench.py", "workforce/receipt_store.py", "harness/patch_review.py",
           "harness/requirement_evidence.py", "harness/verification_search.py", "harness/completion_loop.py",
           "harness/patch_report.py", "bundles/agent_change.py", "metaharness/coding.py",
           "algorithm_design/autonomous.py")


def test_only_the_one_rule_reads_a_parameter_scope():
    root = Path(rt.__file__).parents[1]
    found = {(relative, name): joined for relative in READERS
             for name, joined in _prefix_tests(root / relative).items()}
    assert set(found) == set(PREFIX_TESTS), set(found) ^ set(PREFIX_TESTS)
    assert [key for key, joined in found.items() if "[" in joined] == [("workforce/repo_tests.py", "report_covers")]
    assert {key[1] for key, joined in found.items() if "::" in joined} == {"report_covers", "moved"}


def test_the_scan_finds_a_new_prefix_rule(tmp_path):
    (tmp_path / "new.py").write_text("def mine(check, node):\n    return node.startswith(check + '[')\n",
                                     encoding="utf-8")
    assert _prefix_tests(tmp_path / "new.py") == {"mine": {"["}}
