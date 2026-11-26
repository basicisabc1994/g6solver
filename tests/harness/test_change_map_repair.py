# SPDX-License-Identifier: Apache-2.0
"""Class regressions for advisory isolation, lexical bindings and pytest selection."""
import json
import time

import pytest

from tests.harness.collection_fixtures import collection_answer

from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change, mapped, target


API = "def target():\n    return 1\ndef neighbour():\n    return target()\n"


class TestAdvisoryIsolation:
    def test_syntax_error_keeps_other_valid_targets(self):
        record = mapped({"api.py": API}, {"api.py": API.replace("return 1", "return 2"),
                                               "bad.py": "def broken(:\n"})
        assert target(record, "neighbour")["rank"] == 10
        assert any("bad.py" in s["check"] and "syntax" in s["reason"] for s in record["skipped"])
    @pytest.mark.parametrize("literal", [
        "1e309", "-1e309", "1e-999", "3j", "b'bytes'", "...", "None", "True",
        "0xff", "1_000", "-0.0", "'snow: ☃'", '"a\\nb"',
        *[f"{n}e{power}" for n in range(1, 8) for power in (0, 10, 309)],
    ])
    def test_literals_are_json_source_text(self, literal):
        record = mapped({"api.py": "def f(x):\n    return False\n"},
                        {"api.py": f"def f(x):\n    return x == {literal}\n"})
        json.dumps(record, allow_nan=False)
        points = [p for p in record["targets"] if p.get("decision") == "comparison"]
        assert points and points[0]["literals"] == [literal]

    def test_deadline_omits_entire_map(self):
        before, after = {"api.py": API}, {"api.py": API.replace("return 1", "return 2")}
        record = change_map.build(change(before, after), before, after, deadline=time.monotonic() - 1)
        assert record["targets"] == []
        assert any("budget" in s["reason"] for s in record["skipped"])

    def test_exception_omits_entire_map(self, monkeypatch):
        def fail(*args, **kwargs):
            raise ValueError("analysis failed")
        monkeypatch.setattr(change_map, "_points", fail)
        record = mapped({"api.py": API}, {"api.py": API.replace("return 1", "return 2")})
        assert record["targets"] == []
        assert any("ValueError" in s["reason"] for s in record["skipped"])

    def test_size_limit_keeps_other_valid_targets(self):
        record = mapped({"api.py": API}, {"api.py": API.replace("return 1", "return 2"),
                                               "large.py": "#" * (change_map.MAX_FILE_CHARS + 1)})
        assert target(record, "neighbour")["rank"] == 10
        assert any("large.py" in s["check"] and "limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("record", [{}, {"skipped": []}, {**change_map.empty(), "skipped": [None]},
                                         {**change_map.empty(), "targets": [{"value": float("nan")}]}])
    def test_final_boundary_omits_malformed_records(self, record):
        got = change_map.json_record(record)
        assert got["targets"] == [] and got["skipped"]
        json.dumps(got, allow_nan=False)

    def test_build_is_inside_the_reader_window(self, tmp_path, monkeypatch):
        from tests.harness.test_change_map import BAR, PATCHED_BAR
        rt = patch_review.repo_tests
        (tmp_path / "bar.py").write_text(PATCHED_BAR, encoding="utf-8")
        monkeypatch.setattr(rt, "_git", lambda *a, **k: b"bar.py\0")
        monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
            "bar.py": ("100644", "blob", "a" * 40, str(len(BAR)))})
        monkeypatch.setattr(rt, "blob_bytes", lambda *a, **k: BAR.encode())
        active = []
        real_within, real_build = rt.within, change_map.build

        def within(seconds, read):
            active.append(True)
            try:
                return real_within(seconds, read)
            finally:
                active.pop()

        def build(*args, **kwargs):
            assert active, "analysis escaped the reader window"
            assert kwargs["deadline"] <= time.monotonic() + 2
            return real_build(*args, **kwargs)

        monkeypatch.setattr(rt, "within", within)
        monkeypatch.setattr(change_map, "build", build)
        record = patch_review._change_neighbourhood(
            change({"bar.py": BAR}, {"bar.py": PATCHED_BAR}, tmp_path), (), (), (),
            deadline=time.monotonic() + 60)
        assert record["targets"]


@pytest.mark.usefixtures("declared_mcp_grants")
class TestReviewIsolation:
    @pytest.mark.parametrize("failure", ["literal", "exception", "deadline", "non_json"])
    def test_decided_fields_and_serialization_survive(self, tmp_path, monkeypatch, failure):
        from tests.harness.test_patch_review import BAD, GOOD, SUM, TEST, Recorded, _git, result
        (tmp_path / "calc.py").write_text(BAD)
        (tmp_path / "test_calc.py").write_text(TEST)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c",
             "commit.gpgsign=false", "commit", "-qm", "base")
        (tmp_path / "calc.py").write_text(GOOD)
        Recorded(monkeypatch, default=result(["test_calc.py::test_add", "test_calc.py::test_negative"]),
                 at_base=result(failed=["test_calc.py::test_add", "test_calc.py::test_negative"]))
        real = patch_review._change_neighbourhood
        with monkeypatch.context() as scoped:
            scoped.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: change_map.empty())
            baseline = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)

        if failure == "literal":
            monkeypatch.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: mapped(
                {"api.py": "def f(x):\n    return False\n"},
                {"api.py": "def f(x):\n    return x < 1e309\n"}))
        elif failure == "exception":
            def failed(*args, **kwargs):
                raise RuntimeError("analysis failed")
            monkeypatch.setattr(patch_review, "_change_neighbourhood", failed)
        elif failure == "deadline":
            monkeypatch.setattr(patch_review, "_change_neighbourhood",
                                lambda *a, **k: real(*a, **{**k, "deadline": 0.0}))
        else:
            invalid = {**change_map.empty(), "targets": [{"value": float("inf")}]}
            monkeypatch.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: invalid)
        got = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)
        assert baseline["ok"] and got["ok"]
        json.dumps(got, allow_nan=False)
        keys = ("outcome", "reasons", "requirements", "requirements_without_adequate_check", "summary", "next_actions")
        assert json.dumps({k: got["assessment"][k] for k in keys}, sort_keys=True) == json.dumps(
            {k: baseline["assessment"][k] for k in keys}, sort_keys=True)
        assert got["gate"] == baseline["gate"]
        if failure != "literal":
            record = got["assessment"]["completion"]["neighbourhood"]
            assert record["targets"] == [] and record["skipped"]


class TestPythonBindings:
    def test_rebound_receiver_does_not_create_state_neighbour(self):
        source = ("class C:\n    def changed(self):\n        self = other\n        return 1\n"
                  "    def writer(self):\n        self.value = 0\n")
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return self.value")})
        assert not any(p.get("symbol") == "C.writer" and p["kind"] == "sibling" for p in record["targets"])

    @pytest.mark.parametrize("receiver", ["self", "receiver"])
    def test_nested_function_captures_method_receiver(self, receiver):
        source = (f"class C:\n    def target({receiver}):\n        return 1\n"
                  f"    def outer({receiver}):\n        def caller():\n            return {receiver}.target()\n"
                  "        return caller\n")
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return 2")})
        assert target(record, "C.outer.caller")["rank"] == 10

    def test_class_body_uses_preceding_class_assignment(self):
        source = API + "class C:\n    target = other\n    value = target()\n"
        index = change_map._Index({"api.py": source}, (), [], "patch")
        cls = index.classes["api.C"]
        call = cls.node.body[-1].value
        assert index.resolve(call.func, cls) == "api.other"

    def test_static_method_self_parameter_is_an_unknown_local(self):
        source = API + ("class C:\n    def target(self):\n        return 1\n"
                        "    @staticmethod\n    def caller(self):\n        return self.target()\n")
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return 2")})
        assert not any(p.get("symbol") == "C.caller" and p["kind"] == "sibling" for p in record["targets"])
    @pytest.mark.parametrize("statement", [
        "for neighbour in [lambda: 0]:\n        neighbour()",
        "with manager() as neighbour:\n        neighbour()",
        "try:\n        work()\n    except ValueError as neighbour:\n        neighbour()",
        "if (neighbour := other):\n        neighbour()",
        "del neighbour\n    neighbour()",
        "neighbour()\n    neighbour = other",
        "[neighbour() for neighbour in values]",
        "(lambda neighbour: neighbour())(other)",
        "neighbour, other = values\n    neighbour()",
    ])
    def test_local_targets_do_not_resolve_to_imports(self, statement):
        source = "from api import neighbour\ndef test_call():\n    " + statement + "\n"
        before = {"api.py": API, "test_api.py": source}
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                        stated_checks=["test_api.py::test_call"])
        assert target(record, "neighbour")["exercised"] is False

    def test_comprehension_binding_does_not_leak(self):
        source = ("from api import neighbour\ndef test_call():\n"
                  "    [neighbour for neighbour in values]\n    neighbour()\n")
        before = {"api.py": API, "test_api.py": source}
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                        stated_checks=["test_api.py::test_call"])
        assert target(record, "neighbour")["exercised"] is True

    @pytest.mark.parametrize("declaration", ["", "nonlocal target\n        "])
    def test_enclosing_function_binding(self, declaration):
        source = ("def outer():\n    def target():\n        return 1\n"
                  "    def caller():\n        " + declaration + "return target()\n    return caller\n")
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return 2")})
        assert target(record, "outer.caller")["rank"] == 10

    def test_global_declaration_bypasses_enclosing_binding(self):
        source = API + ("def outer():\n    target = other\n    def caller():\n"
                        "        global target\n        return target()\n    return caller\n")
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return 2")})
        assert target(record, "outer.caller")["rank"] == 10

    def test_method_skips_class_body_binding(self):
        source = API + "class C:\n    target = other\n    def caller(self):\n        return target()\n"
        record = mapped({"api.py": source}, {"api.py": source.replace("return 1", "return 2")})
        assert target(record, "C.caller")["rank"] == 10

    @pytest.mark.parametrize("source", [
        "if flag:\n    def target():\n        return 1\nelse:\n    def target():\n        return 2\n",
        "def target():\n    return 1\ndef target():\n    return 2\n",
        "try:\n    from first import target\nexcept ImportError:\n    from second import target\n",
    ])
    def test_multiple_bindings_name_candidates(self, source):
        record = mapped({"api.py": source}, {"api.py": source.replace("1", "3") + "\nx = 0\n"})
        skips = [s for s in record["skipped"] if "target" in s["check"] and "ambig" in s["reason"]]
        assert skips and len(skips[0]["candidates"]) == 2
        assert all(isinstance(c["line"], int) for c in skips[0]["candidates"])


class TestControlDecisions:
    def test_only_added_else_keyword_has_its_own_location(self):
        before = {"api.py": "def f(x):\n    if x:\n        work()\n    work()\n"}
        after = {"api.py": "def f(x):\n    if x:\n        work()\n    else :\n        work()\n"}
        record = mapped(before, after)
        assert any(p.get("decision") == "else" and p["line"] == 4 for p in record["targets"])

    @pytest.mark.parametrize("body, expected", [
        ("return 0", {"return"}), ("raise ValueError('stop')", {"raise"}),
        ("if x:\n        return 1\n    elif other:\n        return 2\n    else:\n        return 3",
         {"if", "elif", "else", "return"}),
        ("for y in x:\n        break\n    else:\n        return 0", {"for", "break", "else"}),
        ("while x:\n        continue", {"while", "continue"}),
        ("try:\n        work()\n    except ValueError:\n        raise\n    finally:\n        work()",
         {"try", "except", "finally", "raise"}),
        ("assert x", {"assert"}), ("return 1 if x else 2", {"conditional", "return"}),
        ("return [y for y in x if y]", {"return", "for", "if"}),
        ("match x:\n        case 1:\n            return 1\n        case _:\n            return 0", {"case", "return"}),
    ])
    def test_each_branch_and_exit(self, body, expected):
        record = mapped({"api.py": "def f(x):\n    work()\n"},
                        {"api.py": "def f(x):\n    " + body + "\n"})
        assert {p.get("decision") for p in record["targets"]} >= expected


class TestPytestSelections:
    @pytest.mark.parametrize("body", ["__test__ = False", "def __init__(self):\n        pass"])
    def test_non_collected_class_does_not_exercise_methods(self, body):
        source = ("from api import neighbour\nclass TestAPI:\n    " + body +
                  "\n    def test_call(self):\n        neighbour()\n")
        before = {"api.py": API, "test_api.py": source}
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                        stated_checks=["test_api.py::TestAPI"])
        assert target(record, "neighbour")["exercised"] == "uncertain"
    @pytest.mark.parametrize("selection", [
        "test_api.py", "test_api.py::TestAPI", "test_api.py::TestAPI::test_call",
        "test_api.py::TestAPI::test_call[value]",
    ])
    @pytest.mark.parametrize("inherited", [False, True])
    def test_file_class_method_and_parameter_id(self, selection, inherited):
        source = ("from api import neighbour\nclass " + ("Base" if inherited else "TestAPI") +
                  ":\n    def test_call(self):\n        neighbour()\n")
        if inherited:
            source += "class TestAPI(Base):\n    pass\n"
        before = {"api.py": API, "test_api.py": source}
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                        stated_checks=[selection], collector=lambda *a, **k: collection_answer(before, {
                            "status": "available", "nodes": ["test_api.py::TestAPI::test_call[value]"]}))
        assert target(record, "neighbour")["checks"] == [selection]

    def test_other_selected_method_does_not_include_caller(self):
        source = ("from api import neighbour\nclass TestAPI:\n"
                  "    def test_call(self):\n        neighbour()\n    def test_other(self):\n        pass\n")
        before = {"api.py": API, "test_api.py": source}
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                        stated_checks=["test_api.py::TestAPI::test_other"])
        assert target(record, "neighbour")["exercised"] is False
