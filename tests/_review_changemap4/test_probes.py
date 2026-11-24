import ast

import pytest

from g6.harness import patch_review


def test_advisory_does_not_change_decided_fields(tmp_path, monkeypatch):
    from g6.harness import change_map
    from tests.harness.test_patch_review import Recorded, result, BAD, GOOD, TEST, SUM, _git

    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Review", "-c", "user.email=review@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result(["test_calc.py::test_add", "test_calc.py::test_negative"]),
             at_base=result(failed=["test_calc.py::test_add", "test_calc.py::test_negative"]))
    replies = []
    for advisory in (change_map.empty(),
                     {**change_map.empty(), "targets": [{"value": float("nan")}]},
                     {**change_map.empty(), "skipped": [{"tier": 0, "check": "advice", "reason": "unknown"}]}):
        with monkeypatch.context() as scope:
            scope.setattr(patch_review, "_change_neighbourhood", lambda *a, **k: advisory)
            reply = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)
        assert reply["ok"], reply
        assessment = reply["assessment"]
        replies.append({k: assessment[k] for k in (
            "outcome", "reasons", "requirements", "requirements_without_adequate_check", "summary", "next_actions")})
    assert replies[0] == replies[1] == replies[2]


@pytest.mark.parametrize("source, extra, exercised", [
    ("from base import Ancestor as Parent\nclass TestLocal(Parent):\n    pass\n",
     {"base.py": "from api import neighbour\nclass Ancestor:\n"
                 "    def test_call(self):\n        neighbour()\n"}, True),
    ("from base import *\nclass TestLocal(Ancestor):\n    pass\n",
     {"base.py": "from api import neighbour\nclass Ancestor:\n"
                 "    def test_call(self):\n        neighbour()\n"}, False),
    ("from api import neighbour\nclass Parent:\n"
     "    def test_call(self):\n        neighbour()\n"
     "flag = True\nclass TestLocal(Parent if flag else object):\n    pass\n", {}, False),
])
def test_base_resolution_keeps_uncertainty(tmp_path, source, extra, exercised):
    from tests._review_changemap4.test_review import mapped

    target, record = mapped(tmp_path, source, extra)
    assert target["exercised"] is exercised, record
    if not exercised:
        assert any("test_api.py" in s["check"] for s in record["skipped"])


def test_collisions_keep_both_callers_and_note(tmp_path):
    from tests._review_changemap4.test_review import mapped, API

    target, record = mapped(tmp_path, "from api import neighbour\ndef test_call():\n    neighbour()\n",
                            {"src/api.py": API})
    # Only api.py changed; a separate file with the same import name cannot hide its caller.
    assert target["file"] == "api.py" and target["exercised"] is False
    assert any("api.py" in s["check"] and "ambiguous import name" in s["reason"]
               for s in record["skipped"])


@pytest.mark.parametrize("raw", [
    prefix + body
    for prefix in (
        b"\n# coding: latin-1\n", b"\\\n# coding: latin-1\n",
        b" \t\f\n# coding: latin-1\n", b"#\n# coding: latin-1\n",
        b"\xef\xbb\xbf# coding: utf-8\n", b"\xef\xbb\xbf# coding: latin-1\n",
        b"# coding: no_such_codec\n", b"# coding: utf-8-sig\n",
        b"# coding: ascii\n", b"# coding: unicode_escape\n",
        b"# coding: raw_unicode_escape\n", b"# coding: utf-7\n",
        b"# coding: latin-1\r", b"# coding: iso2022_jp\n",
    )
    for body in (b"x = 1\n", b"x = '\xe9'\n", b"x = '\\u0061'\n",
                 b"x = '+AKM-'\n", b"x = 1\\nprint(x)\n")
])
def test_decoder_agrees_with_compiler(raw):
    try:
        expected = compile(raw, "api.py", "exec", flags=ast.PyCF_ONLY_AST)
    except (SyntaxError, UnicodeError, LookupError):
        try:
            actual = ast.parse(patch_review._python_source(raw))
        except (SyntaxError, UnicodeError, LookupError):
            return
        pytest.fail(f"compiler rejects but reader accepts {raw!r}: {ast.dump(actual)}")
    actual = ast.parse(patch_review._python_source(raw))
    assert ast.dump(actual) == ast.dump(expected), repr(raw)
