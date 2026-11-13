"""Search cannot call a parametrized check passed after pytest omits one case."""
import ast
import json
import os

import pytest

from g6.harness import verification_search as search
from g6.workforce import repo_tests as runner


def test_a_partly_deselected_probe_is_unknown(tmp_path, monkeypatch):
    test = tmp_path / ("test_" + tmp_path.name + ".py")
    test.write_text('import pytest\n@pytest.mark.parametrize("value", [1, 2], ids=["first", "second"])\n'
                    'def test_add(value): assert value > 0\n', encoding="utf-8")
    config = tmp_path / "empty.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    counted, xml = tmp_path / "counted.json", tmp_path / "report.xml"
    plugin_class = next(node for node in ast.parse(runner._BOOT).body
                        if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {"os": os, "json": json, "counted": str(counted)}
    exec(compile(ast.Module(body=[plugin_class], type_ignores=[]), "<real-counted>", "exec"), namespace)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    code = pytest.main([test.name, "-k", "first", "-q", "-p", "no:cacheprovider", "-c", str(config),
                        "--rootdir", str(tmp_path), "--junitxml", str(xml)], plugins=[namespace["_Counted"]()])
    counts = runner._progress(runner._counts(xml, tmp_path, (tmp_path,)), counted)
    raw = {**{key: value for key, value in counts.items() if key in runner.REPORT_FIELDS},
           "verdict": runner._judge(code, counts, "", []), "rootdir": "", "said": "", "output": ""}
    raw.update(passed=counts["passed"], failed=counts["failures"] + counts["errors"],
               not_passed=counts["skipped"], total=counts["passed"] + counts["failures"] + counts["errors"])
    node = test.name + "::test_add"
    decided = runner.read_report(raw)
    assert decided.valid
    assert len(decided.omitted) == 1
    assert decided.body_result(node) == "unknown"
    assert search.reading(raw, node)["result"] == "error"
