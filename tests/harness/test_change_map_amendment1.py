# SPDX-License-Identifier: Apache-2.0
"""Amendment regressions: local skips, exact budgets and persisted git paths."""
import json

import pytest

from g6.core.git_name import GitName
from g6.harness import change_map, patch_review
from tests.harness.test_change_map import BAR, PATCHED_BAR, change, mapped, target


API = "def target():\n    return 1\ndef neighbour():\n    return target()\n"


class TestFileIsolation:
    @pytest.mark.parametrize("bad", ["def broken(:\n", "#" * (change_map.MAX_FILE_CHARS + 1),
                                    "from missing import *\n", "# bad surrogate: \udcff\n"])
    @pytest.mark.parametrize("side", ["base", "patch", "check"])
    def test_bad_file_keeps_unrelated_neighbour(self, bad, side):
        before, after = {"api.py": API}, {"api.py": API.replace("return 1", "return 2")}
        options = {}
        if side == "check":
            options["proposed_checks"] = [{"file": "test_bad.py", "source": bad}]
        else:
            (before if side == "base" else after)["bad.py"] = bad
        record = mapped(before, after, **options)
        assert target(record, "neighbour")["rank"] == 10
        assert any("bad.py" in s["check"] for s in record["skipped"])
        assert not any(t["file"] == "bad.py" for t in record["targets"])

    def test_skipped_patch_is_not_treated_as_deletion(self):
        before = {"api.py": API, "broken.py": "def gone():\n    return 0\n"}
        after = {"api.py": API.replace("return 1", "return 2"), "broken.py": "def gone(:"}
        record = mapped(before, after)
        assert target(record, "neighbour")
        assert not any(t["file"] == "broken.py" for t in record["targets"])

    def test_git_paths_survive_json_validation_and_candidate_records(self):
        rel = GitName(b'odd\n\xff.py')
        source = API + "if flag:\n    alias = target\nelse:\n    alias = neighbour\n"
        record = mapped({rel: source}, {rel: source.replace("return 1", "return 2")})
        assert target(record, "neighbour")["file"] == rel
        candidate = next(s for s in record["skipped"] if "candidates" in s)
        assert all(c["file"] == rel for c in candidate["candidates"])
        assert '\n' not in candidate["check"] and '\udcff' not in candidate["check"]
        assert change_map.json_record(record)["targets"] == record["targets"]
        json.dumps(record, allow_nan=False)

    @pytest.mark.parametrize("failure", ["size", "read", "name"])
    def test_reader_skips_only_unavailable_file(self, tmp_path, monkeypatch, failure):
        (tmp_path / "bar.py").write_text(PATCHED_BAR, encoding="utf-8")
        (tmp_path / "bad.py").write_text("x = 0\n", encoding="utf-8")
        rt = patch_review.repo_tests
        monkeypatch.setattr(rt, "_git", lambda *a, **k: b"bar.py\0bad.py\0")
        monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
            "bar.py": ("100644", "blob", "a" * 40, str(len(BAR))),
            "bad.py": ("100644", "blob", "b" * 40, "6")})
        monkeypatch.setattr(rt, "blob_bytes", lambda *a, **k: BAR.encode())
        read, index_path = patch_review._change_map_text, rt._index_path

        def read_file(root, path, limit):
            if path.name == "bad.py":
                if failure == "read":
                    raise OSError("unreadable source")
                if failure == "size":
                    return b"#" * (limit + 1)
            return read(root, path, limit)

        def validate(root, rel):
            if rel == "bad.py" and failure == "name":
                raise ValueError("unreadable name")
            return index_path(root, rel)

        monkeypatch.setattr(patch_review, "_change_map_text", read_file)
        monkeypatch.setattr(rt, "_index_path", validate)
        record = patch_review._change_neighbourhood(change({"bar.py": BAR}, {"bar.py": PATCHED_BAR}, tmp_path),
                                                  (), (), (), deadline=10**20)
        assert target(record, "ProgressBar.update")["rank"] == 20
        assert any("bad.py" in s["check"] for s in record["skipped"])

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    @pytest.mark.parametrize("side", ["base", "patch"])
    def test_reader_byte_limit_is_local_and_checks_metadata_before_read(self, tmp_path, monkeypatch, offset, side):
        (tmp_path / "bar.py").write_text(PATCHED_BAR, encoding="utf-8")
        subject = "#" * (1000 + offset) if side == "patch" else "# short patch"
        (tmp_path / "subject.py").write_text(subject, encoding="utf-8")
        monkeypatch.setattr(change_map, "MAX_FILE_BYTES", 1000)
        rt = patch_review.repo_tests
        monkeypatch.setattr(rt, "_git", lambda *a, **k: b"bar.py\0subject.py\0")
        monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
            "bar.py": ("100644", "blob", "a" * 40, str(len(BAR))),
            "subject.py": ("100644", "blob", "b" * 40, str(1000 + offset))})

        def blob(repo, key):
            if key == "a" * 40:
                return BAR.encode()
            assert offset <= 0, "oversize base blob was read"
            return b"#" * (1000 + offset)

        monkeypatch.setattr(rt, "blob_bytes", blob)
        record = patch_review._change_neighbourhood(change({"bar.py": BAR}, {"bar.py": PATCHED_BAR}, tmp_path),
                                                  (), (), (), deadline=10**20)
        assert target(record, "ProgressBar.update")
        assert any("subject.py" in s["check"] for s in record["skipped"]) is (offset > 0)


class TestBudgetUnits:
    @pytest.fixture(autouse=True)
    def deterministic_clock(self, monkeypatch):
        # Capacity tests must not become machine-speed tests; the real deadline
        # remains covered separately by the review's two-second acceptance node.
        monkeypatch.setattr(change_map.time, "monotonic", lambda: 10.0)

    @pytest.mark.parametrize("count", range(1, change_map.MAX_FILES + 1))
    def test_small_ordinary_files_up_to_file_cap(self, count):
        before = {"api.py": API, **{f"part_{i}.py": "value = 0\n" for i in range(count - 1)}}
        after = {**before, "api.py": API.replace("return 1", "return 2")}
        for _ in range(2):
            record = mapped(before, after)
            assert target(record, "neighbour")["rank"] == 10
            assert not any("limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_file_limit_counts_distinct_python_paths(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_FILES", 4)
        before = {"api.py": API, **{f"part_{i}.py": "x = 0\n" for i in range(3 + offset)}}
        before["notes.txt"] = "not Python"
        record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")})
        if offset <= 0:
            assert target(record, "neighbour")
        else:
            assert record["targets"] == []
            assert any("file limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_file_chars_are_local_and_count_characters(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_FILE_CHARS", 100)
        before = {"api.py": API}
        record = mapped(before, {"api.py": API.replace("return 1", "return 2"),
                                 "subject.py": "#" + "é" * (99 + offset)})
        assert target(record, "neighbour")
        assert any("subject.py" in s["check"] for s in record["skipped"]) is (offset > 0)

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_total_chars_count_distinct_source_versions_once(self, monkeypatch, offset):
        before = {"api.py": API, "same.py": "x = 0\n"}
        after = {**before, "api.py": API.replace("return 1", "return 2")}
        # Two distinct api versions, one shared same.py version; reindexing
        # for check resolution must not count a fourth source version.
        total = 2 * len(API) + 6
        monkeypatch.setattr(change_map, "MAX_TOTAL_CHARS", total + offset)
        record = mapped(before, after, stated_checks=["api.py", "same.py"])
        if offset >= 0:
            assert target(record, "neighbour")
        else:
            assert record["targets"] == []
            assert any("total text limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("limit", [4, 5, 6])
    def test_node_limit_is_per_file(self, monkeypatch, limit):
        monkeypatch.setattr(change_map, "MAX_NODES", limit)
        record = mapped({}, {"a.py": "x = 1\n", "b.py": "x = 2\n"})
        # Each assignment has Module, Assign, Name, Store, Constant: five nodes.
        skips = [s for s in record["skipped"] if "AST node limit" in s["reason"]]
        assert len(skips) == (2 if limit < 5 else 0)
        assert all(".py" in s["check"] for s in skips)

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_symbol_limit_is_per_file_and_counts_definitions(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_SYMBOLS", 2)
        source = "".join(f"def f{i}():\n    pass\n" for i in range(2 + offset))
        record = mapped({}, {"a.py": source, "b.py": source})
        skips = [s for s in record["skipped"] if "symbol limit" in s["reason"]]
        assert len(skips) == (2 if offset > 0 else 0)

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_literal_limit_counts_emitted_source_characters(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_LITERAL_OUTPUT", 6)
        literal = "'" + "x" * (4 + offset) + "'"
        record = mapped({"api.py": "def f():\n    pass\n"},
                        {"api.py": f"def f():\n    return {literal}\n"})
        if offset <= 0:
            assert record["targets"][0]["literals"] == [literal]
        else:
            assert record["targets"] == []
            assert any("literal output limit" in s["reason"] for s in record["skipped"])

    def test_literal_dedup_and_budget_reset_between_builds(self, monkeypatch):
        monkeypatch.setattr(change_map, "MAX_LITERAL_OUTPUT", 4)
        for _ in range(2):
            record = mapped({"api.py": "def f():\n    pass\n"},
                            {"api.py": "def f():\n    return ('xx', 'xx')\n"})
            assert record["targets"][0]["literals"] == ["'xx'"]

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_target_limit_counts_final_targets(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_TARGETS", 4)
        source = "".join(f"def f{i}():\n    return {i}\n" for i in range(4 + offset))
        record = mapped({}, {"api.py": source})
        assert len(record["targets"]) == (4 + offset if offset <= 0 else 0)
        assert any("target limit" in s["reason"] for s in record["skipped"]) is (offset > 0)

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_check_limit_counts_selections(self, monkeypatch, offset):
        monkeypatch.setattr(change_map, "MAX_CHECKS", 4)
        checks = [f"tests/test_{i}.py" for i in range(4 + offset)]
        record = mapped({"api.py": API}, {"api.py": API.replace("return 1", "return 2")}, stated_checks=checks)
        if offset <= 0:
            assert target(record, "neighbour")
        else:
            assert record["targets"] == []
            assert any("check selection limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("offset", [-1, 0, 1])
    def test_json_limit_counts_serialized_characters(self, monkeypatch, offset):
        record = change_map.empty()
        record["skipped"] = [change_map.skip("odd\nname", "é")]
        size = len(json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
        monkeypatch.setattr(change_map, "MAX_OUTPUT_CHARS", size + offset)
        got = change_map.json_record(record)
        if offset >= 0:
            assert got == record
        else:
            assert got["targets"] == []
            assert "JSON output size limit" in got["skipped"][0]["reason"]

    @pytest.mark.parametrize("count", [3, 4, 5])
    def test_work_limit_counts_checkpoints(self, monkeypatch, count):
        monkeypatch.setattr(change_map, "MAX_WORK", 4)
        token = change_map._BUDGET.set(change_map._Budget(11.0))
        try:
            if count <= 4:
                for _ in range(count):
                    change_map._tick()
            else:
                with pytest.raises(change_map._Omitted) as caught:
                    for _ in range(count):
                        change_map._tick()
                assert caught.value.reason == "static analysis work limit reached"
        finally:
            change_map._BUDGET.reset(token)
        monkeypatch.setattr(change_map, "MAX_WORK", 1)
        record = mapped({"api.py": API}, {"api.py": API.replace("return 1", "return 2")})
        assert record["targets"] == []
        assert any("work limit" in s["reason"] for s in record["skipped"])

    @pytest.mark.parametrize("deadline", [9.0, 10.0, 11.0])
    def test_deadline_is_exclusive_and_resets_per_build(self, deadline):
        before, after = {"api.py": API}, {"api.py": API.replace("return 1", "return 2")}
        record = change_map.build(change(before, after), before, after, deadline=deadline)
        if deadline > 10:
            assert target(record, "neighbour")
        else:
            assert record["targets"] == []
            assert any("time budget" in s["reason"] for s in record["skipped"])
        assert target(mapped(before, after), "neighbour")
