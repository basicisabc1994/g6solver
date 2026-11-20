"""Local synthetic inputs for the M1b static inventory."""
from __future__ import annotations

import base64
import importlib
import json
import warnings
from dataclasses import replace
from pathlib import Path

import pytest


def modules():
    prefix = "validation.distill.pilot.m1b"
    return tuple(importlib.import_module(f"{prefix}.{name}") for name in ("fit", "counts", "chooser"))


def candidate(tmp_path, *, source=b"def value():\n    return 2\n", test=None, helpers=None):
    fit, _, _ = modules()
    root = tmp_path / "parent"
    root.mkdir(exist_ok=True)
    old = b"def value():\n    return 1\n"
    (root / "widget.py").write_bytes(old)
    if test is None:
        test = b"from widget import value\ndef test_value():\n    assert value() == 2\n"
    files = {"widget.py": fit.ChangedFile(old, source),
             "tests/test_value.py": fit.ChangedFile(test.replace(b"== 2", b"== 1"), test)}
    return fit.Candidate("repo-a", "bug-a", root, files, helpers or {})


def environment(**kwargs):
    fit, _, _ = modules()
    return fit.Environment(frozenset({"pytest", "os", "pathlib"}), frozenset(), frozenset(), **kwargs)


def record(tmp_path, **kwargs):
    fit, _, _ = modules()
    return fit.assess(candidate(tmp_path, **kwargs), environment())


def test_clean_candidate_and_public_record(tmp_path):
    row = record(tmp_path)
    assert row.failed_rules == ()
    assert row.mutant_count >= 1
    assert row.variants
    body = json.dumps(row.public())
    assert "return 2" not in body and "assert value" not in body
    assert str(tmp_path) not in body


@pytest.mark.parametrize("rule", ["M1", "M2", "M3", "M4", "M5", "M6"])
def test_mandatory_rule_alone(tmp_path, rule):
    fit, _, _ = modules()
    item = candidate(tmp_path)
    env = environment()
    files = dict(item.changed_files)
    if rule == "M1":
        files["widget.py"] = replace(files["widget.py"], before=None)
    elif rule == "M2":
        (item.parent_tree / "large.py").write_bytes(b"#" * 131073)
    elif rule == "M3":
        tests = files["tests/test_value.py"]
        files["tests/test_value.py"] = replace(tests, before=b"#" * 131073)
    elif rule == "M4":
        files["widget.py"] = replace(files["widget.py"], after=files["widget.py"].before)
    elif rule == "M5":
        env = replace(env, loaded_packages=frozenset({"widget"}))
    elif rule == "M6":
        files["tests/test_value.py"] = fit.ChangedFile(b"def test_x(): pass\n", b"def test_x(): pass\n")
    row = fit.assess(replace(item, changed_files=files), env)
    assert row.failed_rules == (rule,)


def test_all_failures_recorded(tmp_path):
    fit, _, _ = modules()
    item = candidate(tmp_path, source=b"# fixed\n", test=b"def test_x(): pass\n")
    (item.parent_tree / "large.py").write_bytes(b"#" * 131073)
    files = dict(item.changed_files)
    files["widget.py"] = replace(files["widget.py"], before=None)
    row = fit.assess(replace(item, changed_files=files),
                     replace(environment(), loaded_packages=frozenset({"widget"})))
    assert set(row.failed_rules) == {"M1", "M2", "M4", "M5", "M6"}


@pytest.mark.parametrize("statement", ["import tests.helper", "from tests import helper", "from . import helper"])
def test_unguarded_test_import_chain(tmp_path, statement):
    row = record(tmp_path, test=(statement + "\ndef test_x(): assert 1 == 1\n").encode(),
                 helpers={"tests/__init__.py": b"", "tests/helper.py": b"import absent_dependency\n"})
    assert set(row.failed_rules) == {"V2", "V3"}


@pytest.mark.parametrize("wrapper", ["def deferred():", "class Deferred:", "if True:", "try:"])
@pytest.mark.parametrize("kind", ["helper", "third_party"])
def test_guarded_imports_only_flagged(tmp_path, wrapper, kind):
    statement = "from . import helper" if kind == "helper" else "import absent_dependency"
    ending = "except ImportError:\n    pass\n" if wrapper == "try:" else ""
    source = f"{wrapper}\n    {statement}\n{ending}def test_x(): assert 1 == 1\n".encode()
    row = record(tmp_path, test=source, helpers={"tests/helper.py": b"import transitive_missing\n"})
    assert row.failed_rules == ()
    assert ("guarded_V2" if kind == "helper" else "guarded_V3") in row.flags


def test_guarded_transitive_import_and_package_initializer(tmp_path):
    row = record(tmp_path, test=b"from . import helper\ndef test_x(): assert 1 == 1\n",
                 helpers={"tests/__init__.py": b"import missing_at_init\n",
                          "tests/helper.py": b"if True:\n    import missing_guarded\n"})
    assert set(row.failed_rules) == {"V2", "V3"}
    assert "guarded_V3" in row.flags


def test_flags_do_not_exclude(tmp_path):
    row = record(tmp_path, test=b"import pytest\n@pytest.mark.skip\n@pytest.mark.xfail\n"
                 b"def test_x(external_fixture):\n    open('data')\n    assert 1 == 1\n")
    assert row.failed_rules == ()
    assert {"fixture_not_in_module", "skip", "xfail", "file_read"} <= set(row.flags)


def test_v3_resolves_from_injected_environment(tmp_path):
    fit, _, _ = modules()
    item = candidate(tmp_path, test=b"import installed_dep\ndef test_x(): assert 1 == 1\n")
    assert "V3" in fit.assess(item, environment()).failed_rules
    env = replace(environment(), resolvable_modules=frozenset({"installed_dep"}))
    assert fit.assess(item, env).failed_rules == ()


def test_all_six_counts_and_repository_deduplication(tmp_path):
    fit, counts, _ = modules()
    item = candidate(tmp_path)
    clean = fit.assess(item, environment())
    other = fit.assess(replace(item, bug_id="bug-b"), environment())
    result = counts.summarize([clean, other])
    assert result["counts"] == dict.fromkeys(("K_M", "K_0", "K_1", "K_2", "K_3", "K_123"), 1)


def test_configuration_lifts_and_separate_exclusions(tmp_path):
    fit, counts, _ = modules()
    item = candidate(tmp_path, test=b"from . import helper\ndef test_x(): assert 1 == 1\n",
                     helpers={"tests/helper.py": b"VALUE = 1\n", "tests/__init__.py": b""})
    helper = fit.assess(item, environment())
    multi_files = dict(candidate(tmp_path).changed_files)
    multi_files["tests/test_second.py"] = multi_files["tests/test_value.py"]
    multi = fit.assess(replace(item, repository="repo-b", bug_id="bug-b", changed_files=multi_files), environment())
    third = fit.assess(replace(candidate(tmp_path, test=b"import missing\ndef test_x(): assert 1 == 1\n"),
                               repository="repo-c", bug_id="bug-c"), environment())
    result = counts.summarize([helper, multi, third])
    assert result["counts"] == {"K_M": 3, "K_0": 0, "K_1": 1, "K_2": 1, "K_3": 1, "K_123": 3}
    for code, bug in (("V1", "bug-b"), ("V2", "bug-a"), ("V3", "bug-c")):
        assert [r["bug_id"] for r in result["exclusions"][code]] == [bug]


@pytest.mark.parametrize("limit", ["file", "count", "total", "marker"])
def test_helpers_and_markers_rechecked_against_case_limits(tmp_path, limit):
    _, counts, _ = modules()
    helpers = {"tests/helper.py": b"VALUE = 1\n", "tests/__init__.py": b""}
    if limit == "file":
        helpers["tests/helper.py"] = b"#" * 131073
    elif limit == "marker":
        helpers["tests/__init__.py"] = b"#" * 131073
    elif limit == "count":
        helpers.update({f"tests/h{i}.py": b"" for i in range(128)})
    else:
        helpers.update({f"tests/h{i}.py": b"#" * 131072 for i in range(9)})
    row = record(tmp_path, test=b"from . import helper\ndef test_x(): assert 1 == 1\n", helpers=helpers)
    result = counts.summarize([row])
    assert result["counts"]["K_M"] == 1
    assert result["counts"]["K_1"] == result["counts"]["K_123"] == 0
    assert result["configuration_exclusions"]["K_1"][0]["reason"] == "case_limits"


def test_conftest_never_staged(tmp_path):
    _, counts, _ = modules()
    row = record(tmp_path, test=b"from . import helper\ndef test_x(): assert 1 == 1\n",
                 helpers={"tests/helper.py": b"VALUE=1\n", "tests/conftest.py": b"#" * 131073})
    cases = counts.staged_cases(row, "K_1")
    assert len(cases) == 1
    assert "tests/helper.py" in cases[0].files
    assert "tests/conftest.py" not in cases[0].files
    assert counts.summarize([row])["counts"]["K_1"] == 1


def test_prepared_loaded_rule_and_recount(tmp_path):
    fit, counts, _ = modules()
    item = candidate(tmp_path, test=b"import missing\ndef test_x(): assert 1 == 1\n")
    row = fit.assess(item, replace(environment(), loaded_packages=frozenset({"widget"})))
    result = counts.summarize([row])
    assert result["counts"]["K_M"] == 0
    assert result["counts"]["K_3"] == result["counts"]["K_123"] == 1
    recounted = counts.summarize([row], prepared_environment=environment())
    assert recounted["counts"]["K_3"] == 0
    assert recounted["counts"]["K_123"] == 0
    collision = replace(row.environment, pytest_dependencies=frozenset({"widget"}))
    assert counts.summarize([fit.assess(item, collision)])["counts"]["K_3"] == 0


@pytest.mark.parametrize("n,tier,d4", [(9, "stop", "stop"), (10, "reduced", "draw"),
                                      (14, "reduced", "draw"), (15, "full", "draw")])
def test_d4_boundaries(n, tier, d4):
    _, _, chooser = modules()
    choice = chooser.choose(dict.fromkeys(("K_0", "K_1", "K_2", "K_3", "K_123"), n))
    assert (choice.configuration, choice.count, choice.tier, choice.d4) == ("K_0", n, tier, d4)
    assert choice.stop_wording == ("the single-change design did not reach 10" if n < 10 else "")
    assert choice.reuse == ("not measured" if tier == "reduced" else "2" if tier == "full" else "none")


@pytest.mark.parametrize("values,exists,recount,selected", [
    ((10, 14, 14, 14, 99), True, 14, "K_0"),
    ((9, 10, 15, 15, 99), True, 15, "K_2"),
    ((9, 15, 15, 15, 99), True, 15, "K_1"),
    ((9, 10, 10, 15, 99), True, 15, "K_3"),
    ((9, 10, 10, 15, 99), False, None, "K_1"),
    ((9, 9, 9, 9, 99), False, None, "K_0"),
    ((9, 10, 10, 15, 99), True, 14, "K_1"),
    ((9, 9, 10, 15, 99), False, None, "K_2"),
    ((9, 9, 9, 15, 99), True, None, "K_0"),
    ((15, 99, 99, 99, 99), True, 99, "K_0"),
])
def test_frozen_chooser(values, exists, recount, selected):
    _, _, chooser = modules()
    counts = dict(zip(("K_0", "K_1", "K_2", "K_3", "K_123"), values, strict=True))
    result = chooser.choose(counts, interpreter_exists=exists, prepared_count=recount)
    assert result.configuration == selected
    assert result.configuration != "K_123"


def test_cli_public_json_and_repository_output_refusal(tmp_path, capsys):
    fit, _, _ = modules()
    item = candidate(tmp_path)
    def encode(b):
        return base64.b64encode(b).decode() if b is not None else None
    data = [{"repository": item.repository, "bug_id": item.bug_id, "parent_tree": str(item.parent_tree),
             "changed_files": {p: {"before": encode(c.before), "after": encode(c.after)}
                               for p, c in item.changed_files.items()}, "test_package_files": {}}]
    source = tmp_path / "candidates.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    env = tmp_path / "environment.json"
    env.write_text(json.dumps({"resolvable_modules": [], "loaded_packages": [], "pytest_dependencies": []}),
                   encoding="utf-8")
    # The real worktree is refused before any candidate is opened.
    with pytest.raises(SystemExit) as error:
        fit.main(["--candidates", str(source), "--environment", str(env),
                  "--output", str(Path(__file__).resolve().parents[2] / ".build" / "refused.json")])
    assert error.value.code == 2
    # A synthetic repository boundary permits checking successful output within tmp_path.
    output = tmp_path / "inventory.json"
    assert fit.main(["--candidates", str(source), "--environment", str(env), "--output", str(output)],
                    repository_root=item.parent_tree) == 0
    text = output.read_text(encoding="utf-8")
    assert json.loads(text)["counts"]["K_0"] == 1
    for secret in ("return 2", "assert value", str(item.parent_tree), encode(b"def value():\n    return 2\n")):
        assert secret not in text
    assert "expected output outside repository" in capsys.readouterr().err


def test_cli_errors_do_not_echo_candidate_source(tmp_path, capsys):
    fit, _, _ = modules()
    source = tmp_path / "bad.json"
    source.write_text('{"PRIVATE_SOURCE":', encoding="utf-8")
    with pytest.raises(SystemExit):
        fit.main(["--candidates", str(source), "--environment", str(source),
                  "--output", str(tmp_path / "out.json")], repository_root=tmp_path / "synthetic-repo")
    assert "PRIVATE_SOURCE" not in capsys.readouterr().err


@pytest.mark.parametrize("statement", ["from . import helper", "from tests import helper", "import tests.helper"])
def test_namespace_import_graph_has_no_external_test_package(statement):
    fit, _, _ = modules()
    failed, flags = fit._imports({"tests/test_x.py": statement, "tests/helper.py": "VALUE = 1"},
                                ("tests/test_x.py",), frozenset({"tests/helper.py"}), frozenset())
    assert failed == {"V2"}
    assert flags == set()


@pytest.mark.parametrize("wrapper", ["def later():", "class Later:", "if True:", "try:"])
def test_graph_does_not_follow_guarded_edge(wrapper):
    fit, _, _ = modules()
    end = "\nexcept ImportError: pass" if wrapper == "try:" else ""
    failed, flags = fit._imports({"tests/test_x.py": f"{wrapper}\n    from . import helper{end}",
                                 "tests/helper.py": "import missing"}, ("tests/test_x.py",),
                                frozenset({"tests/helper.py"}), frozenset())
    assert failed == set()
    assert flags == {"guarded_V2"}


def test_graph_unguarded_cycle_and_initializer():
    fit, _, _ = modules()
    sources = {"tests/test_x.py": "import tests.helper", "tests/__init__.py": "import init_missing",
               "tests/helper.py": "import tests.other", "tests/other.py": "import tests.helper\nimport missing"}
    failed, _ = fit._imports(sources, ("tests/test_x.py",), frozenset(sources), frozenset())
    assert failed == {"V2", "V3"}


def memory_record(*, failed=(), helpers=None, tests=None):
    """Unit-level case material; this helper neither opens nor creates a directory."""
    fit, _, _ = modules()
    env = environment()
    item = fit.Candidate("repo-a", "bug-a", Path("unused-parent"), {}, {})
    return fit.FitRecord("repo-a", "bug-a", failed, (), 1, ("erase_assertion",), env, item,
                         {"widget.py": "VALUE = 2\n"}, tests or {"tests/test_x.py": ("assert 1", "assert 2")},
                         helpers or {}, "widget.py", (1,), "VALUE = 1\n", frozenset({"widget"}))


@pytest.mark.parametrize("code,key", [("V1", "K_2"), ("V2", "K_1"), ("V3", "K_3")])
def test_count_single_lifts_in_memory(code, key):
    _, counts, _ = modules()
    row = memory_record(failed=(code,))
    result = counts.summarize([row, replace(row, bug_id="bug-b")])
    assert result["counts"]["K_M"] == result["counts"][key] == result["counts"]["K_123"] == 1
    assert result["counts"]["K_0"] == 0
    assert len(result["exclusions"][code]) == 2


@pytest.mark.parametrize("helpers", [
    {"tests/helper.py": "#" * 131073},
    {"tests/__init__.py": "#" * 131073},
    {f"tests/h{i}.py": "" for i in range(128)},
    {f"tests/h{i}.py": "#" * 131072 for i in range(9)},
])
def test_count_full_helper_limits_in_memory(helpers):
    _, counts, _ = modules()
    row = memory_record(failed=("V2",), helpers=helpers)
    result = counts.summarize([row])
    assert result["counts"]["K_M"] == 1
    assert result["counts"]["K_1"] == result["counts"]["K_123"] == 0


def test_count_conftest_and_changed_tests_not_helpers():
    _, counts, _ = modules()
    row = memory_record(helpers={"tests/conftest.py": "#" * 131073, "tests/__init__.py": "",
                                 "tests/test_x.py": "assert 2", "tests/helper.py": "VALUE=1"})
    case, = counts.staged_cases(row, "K_1")
    assert set(case.files) == {"widget.py", "tests/__init__.py", "tests/helper.py"}


def test_count_prepared_collision_and_actual_recount_in_memory():
    _, counts, _ = modules()
    row = memory_record(failed=("M5", "V3"), tests={"tests/test_x.py": ("assert 1", "import missing\nassert 2")})
    assert counts.summarize([row])["counts"]["K_3"] == 1
    assert counts.summarize([row], prepared_environment=environment())["counts"]["K_3"] == 0
    env = replace(environment(), resolvable_modules=frozenset({"missing"}))
    assert counts.summarize([row], prepared_environment=env)["counts"]["K_3"] == 1
    row = replace(row, environment=replace(row.environment, pytest_dependencies=frozenset({"widget"})))
    assert counts.summarize([row])["counts"]["K_3"] == 0


def memory_candidate():
    fit, _, _ = modules()
    return fit.Candidate("repo-a", "bug-a", Path("unused-parent"), {
        "widget.py": fit.ChangedFile(b"def value():\n    return 1\n", b"def value():\n    return 2\n"),
        "tests/test_x.py": fit.ChangedFile(b"def test_x(): assert 1 == 1\n", b"def test_x(): assert 2 == 2\n"),
    }, {})


@pytest.mark.parametrize("rule", [None, "M1", "M2", "M3", "M4", "M5", "M6"])
def test_static_engine_mandatory_independence(rule):
    fit, _, _ = modules()
    item = memory_candidate()
    files = dict(item.changed_files)
    parent, env = {}, environment()
    if rule == "M1":
        files["widget.py"] = replace(files["widget.py"], before=None)
    elif rule == "M2":
        parent["large.py"] = "#" * 131073
    elif rule == "M3":
        files["tests/test_x.py"] = replace(files["tests/test_x.py"], before=b"#" * 131073)
    elif rule == "M4":
        files["widget.py"] = replace(files["widget.py"], after=files["widget.py"].before)
    elif rule == "M5":
        env = replace(env, loaded_packages=frozenset({"widget"}))
    elif rule == "M6":
        files["tests/test_x.py"] = fit.ChangedFile(b"def test_x(): pass", b"def test_x(): pass")
    row = fit._assess_sources(replace(item, changed_files=files), env, parent)
    assert row.failed_rules == (() if rule is None else (rule,))
    assert "return 2" not in json.dumps(row.public())


def test_static_engine_cumulative_failures_and_flags():
    fit, _, _ = modules()
    item = memory_candidate()
    files = dict(item.changed_files)
    files["widget.py"] = fit.ChangedFile(None, b"# fixed\n")
    files["tests/test_x.py"] = fit.ChangedFile(b"pass", b"import missing\ndef test_x(fixture): pass")
    row = fit._assess_sources(replace(item, changed_files=files),
                              replace(environment(), loaded_packages=frozenset({"widget"})),
                              {"large.py": "#" * 131073})
    assert set(row.failed_rules) == {"M1", "M2", "M4", "M5", "M6", "V3"}
    assert row.flags == ("fixture_not_in_module",)


def test_static_engine_imports_counts_and_source_privacy():
    fit, counts, _ = modules()
    item = memory_candidate()
    files = dict(item.changed_files)
    files["tests/test_x.py"] = replace(files["tests/test_x.py"],
                                      after=b"from . import helper\ndef test_x(): assert 2 == 2\n")
    row = fit._assess_sources(replace(item, changed_files=files,
                                      test_package_files={"tests/helper.py": b"import absent\n"}), environment(), {})
    assert row.failed_rules == ("V2", "V3")
    result = counts.summarize([row])
    assert result["counts"] == {"K_M": 1, "K_0": 0, "K_1": 0, "K_2": 0, "K_3": 0, "K_123": 1}
    text = json.dumps({"record": row.public(), "inventory": result})
    assert "return 2" not in text and "import absent" not in text


def test_static_engine_reuses_census_and_public_constructors(monkeypatch):
    fit, _, _ = modules()
    calls = []

    def sample(source, lines, policy):
        calls.append(("sample", lines, policy.min_mutants))
        return {"a": "first", "b": "second"}

    def apply(source, operator):
        calls.append(("operator", operator))
        if operator != "erase_assertion":
            raise ValueError("no applicable variant")
        return "pass"

    monkeypatch.setattr(fit.census, "sample", sample)
    monkeypatch.setattr(fit.operators, "apply_operator", apply)
    row = fit._assess_sources(memory_candidate(), environment(), {})
    assert row.mutant_count == 2 and row.variants == ("erase_assertion",)
    assert calls[0] == ("sample", (2,), 1)
    assert len(calls) == 1 + len(fit.operators.TRAINING) + len(fit.operators.LEGITIMATE)


def test_static_engine_ignores_unstaged_non_python_diff():
    fit, _, _ = modules()
    item = memory_candidate()
    files = {**item.changed_files, "README.md": fit.ChangedFile(b"old", b"new")}
    row = fit._assess_sources(replace(item, changed_files=files), environment(), {})
    assert row.failed_rules == ()
    assert set(row.program) == {"widget.py"}


def test_static_engine_emits_no_constructor_warnings():
    fit, _, _ = modules()
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        fit._assess_sources(memory_candidate(), environment(), {})
    assert observed == []


def test_static_engine_loaded_submodule_collision():
    fit, _, _ = modules()
    row = fit._assess_sources(memory_candidate(),
                              replace(environment(), loaded_packages=frozenset({"widget.submodule"})), {})
    assert row.failed_rules == ("M5",)


def test_static_engine_invalid_compilation_is_m6():
    fit, _, _ = modules()
    item = memory_candidate()
    files = dict(item.changed_files)
    files["tests/test_x.py"] = fit.ChangedFile(b"assert 1", b"return\ndef test_x(): assert 2 == 2")
    row = fit._assess_sources(replace(item, changed_files=files), environment(), {})
    assert row.failed_rules == ("M6",)


def test_module_name_keeps_root_src_py():
    fit, _, _ = modules()
    assert fit._module("src.py") == "src"
    assert fit._module("src/widget/__init__.py") == "widget"


def test_static_engine_never_runs_source():
    fit, _, _ = modules()
    item = memory_candidate()
    files = {path: replace(pair, after=b"raise AssertionError('candidate ran')\n" + pair.after)
             for path, pair in item.changed_files.items()}
    row = fit._assess_sources(replace(item, changed_files=files), environment(), {})
    assert row.failed_rules == ()


def test_staged_record_preserves_bug_identity_and_its_limit():
    _, counts, _ = modules()
    row = memory_record()
    case, = counts.staged_cases(row, "K_0")
    assert case.unit_id == row.bug_id
    invalid = replace(row, bug_id="b" * 257)
    assert counts.summarize([invalid])["counts"] == dict.fromkeys(counts.CONFIGURATIONS, 0)
