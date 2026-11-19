# SPDX-License-Identifier: Apache-2.0
"""Synthetic local task supply contracts for S1."""
from __future__ import annotations

import importlib
import json
from dataclasses import replace

import pytest


@pytest.fixture
def api():
    class API:
        def __getattr__(self, name):
            return getattr(importlib.import_module("g6.recursion.tasks"), name)

    return API()


def row(source, **changes):
    if source == "mbpp":
        value = {"task_id": 7, "text": "Return the input plus two.",
                 "test_list": ["assert bump(3) == 5"], "split": "test"}
    elif source == "apps":
        value = {"problem_id": "toy-7", "question": "Print the input plus two.", "split": "train",
                 "difficulty": "introductory", "input_output": json.dumps({"inputs": ["3\n"], "outputs": ["5\n"]})}
    elif source == "codecontests":
        value = {"name": "toy-7", "description": "Print the input plus two.", "split": "train",
                 "public_tests": {"input": ["3\n"], "output": ["5\n"]}, "cf_rating": 800}
    else:
        value = {"question_id": "toy-7", "question_content": "Print the input plus two.",
                 "contest_date": "2024-10-01T00:00:00", "public_test_cases": json.dumps([
                     {"input": "3\n", "output": "5\n", "testtype": "stdin"}])}
    return value | changes


def local(tmp_path, rows):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


@pytest.mark.parametrize("source,licence,train", [
    ("mbpp", "CC-BY-4.0", False), ("apps", "MIT", True),
    ("codecontests", "Apache-2.0 AND CC-BY-4.0", True),
    ("livecodebench", "MIT-code; data-cc-unspecified", False),
])
def test_registry(api, source, licence, train):
    item = api.licence_for(source)
    assert item.source == source and item.licence == licence
    assert item.train_ok is train and item.eval_ok is True
    assert item.url.startswith("https://") and item.notes
    assert item.redistribute is False


@pytest.mark.parametrize("source", ["mbpp", "livecodebench"])
@pytest.mark.parametrize("entry", ["generic", "named", "iter", "record", "constructor", "suite", "adapter"])
def test_eval_only_never_training(api, tmp_path, source, entry):
    path = local(tmp_path, [row(source)])
    task = api.load_tasks(source, path, split="eval")[0]
    with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
        if entry == "generic":
            api.load_tasks(source, path, split="train")
        elif entry == "named":
            getattr(api, "load_" + source)(path, split="train")
        elif entry == "iter":
            list(api.TaskSource(source, path).iter("train"))
        elif entry == "record":
            record = task.to_record()
            record["split"] = "train"
            api.TaskSpec.from_record(record)
        elif entry == "constructor":
            replace(task, split="train")
        elif entry == "adapter":
            from g6.rsi.loaders import load_local

            load_local(source, path, split="train")
        else:
            api.to_suite([task], split="train")


@pytest.mark.parametrize("source,split", [("mbpp", "retention"), ("apps", "train"),
                                         ("codecontests", "train"), ("livecodebench", "held_out")])
def test_load_task_record_round_trip(api, tmp_path, source, split):
    path = local(tmp_path, [row(source)])
    task = api.load_tasks(source, path, split=split)[0]
    assert isinstance(task, api.TaskSpec)
    assert task.prompt and task.public_tests
    assert task.source == source and task.source_id == source
    assert task.licence_id == api.licence_for(source).licence_id
    assert task.provenance.original_id in ("7", "toy-7")
    assert task.provenance.source == source
    assert task.provenance.licence_id == task.licence_id
    assert len(task.provenance.content_digest) == 64
    restored = api.TaskSpec.from_record(json.loads(json.dumps(task.to_record())))
    assert restored == task
    assert list(api.TaskSource(source, path).iter(split)) == [task]
    assert getattr(api, "load_" + source)(path, split=split) == (task,)
    changed = row(source)
    prompt_key = {"mbpp": "text", "apps": "question", "codecontests": "description",
                  "livecodebench": "question_content"}[source]
    changed[prompt_key] += " Changed."
    other = api.load_tasks(source, local(tmp_path, [changed]), split=split)[0]
    assert other.provenance.content_digest != task.provenance.content_digest


@pytest.mark.parametrize("date", ["2024-08-31", "2024-09-01", "2024-09-30", "", "invalid", "2024-13-01"])
def test_lcb_old_or_invalid_dates_refused(api, tmp_path, date):
    path = local(tmp_path, [row("livecodebench", contest_date=date)])
    with pytest.raises(api.TaskError):
        api.load_livecodebench(path, split="held_out")


def test_lcb_old_can_be_evaluated_but_not_relabelled(api, tmp_path):
    task = api.load_livecodebench(local(tmp_path, [row("livecodebench", contest_date="2024-08-01")]),
                                 split="eval")[0]
    with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
        replace(task, split="held_out")


@pytest.mark.parametrize("source", ["apps", "codecontests"])
def test_test_split_cannot_train(api, tmp_path, source):
    path = local(tmp_path, [row(source, split="test")])
    with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
        api.load_tasks(source, path, split="train")


@pytest.mark.parametrize("source", ["mbpp", "apps", "codecontests", "livecodebench"])
@pytest.mark.parametrize("bad", ["truncated", "later-row", "object", "duplicate", "duplicate-key", "utf8"])
def test_bad_files_fail_atomically(api, tmp_path, source, bad):
    value = row(source)
    payload = json.dumps([value])
    if bad == "truncated":
        payload = payload[:-1]
    elif bad == "later-row":
        payload = json.dumps([value, {}])
    elif bad == "object":
        payload = json.dumps(value)
    elif bad == "duplicate":
        payload = json.dumps([value, value])
    elif bad == "duplicate-key":
        payload = '[{"task_id":1,"task_id":2}]'
    path = tmp_path / "broken.json"
    path.write_bytes(b"\xff" if bad == "utf8" else payload.encode())
    for call in (lambda: api.load_tasks(source, path, split="eval"),
                 lambda: getattr(api, "load_" + source)(path, split="eval"),
                 lambda: next(api.TaskSource(source, path).iter("eval"))):
        with pytest.raises(api.TaskError, match="^G6_E_RSI_FILE_INVALID$"):
            call()


@pytest.mark.parametrize("change", ["prompt", "digest", "licence", "eligibility", "extra"])
def test_tampered_records_refused(api, tmp_path, change):
    task = api.load_apps(local(tmp_path, [row("apps")]), split="train")[0]
    record = task.to_record()
    if change == "prompt":
        record["prompt"] = "Another task"
    elif change == "digest":
        record["provenance"]["content_digest"] = "0" * 64
    elif change == "licence":
        record["licence_row"]["train_ok"] = False
    elif change == "eligibility":
        record["split_eligibility"] = ["train", "held_out"]
    else:
        record["unexpected"] = True
    with pytest.raises(api.TaskError):
        api.TaskSpec.from_record(record)


def test_suite_reuses_schema_and_keeps_metadata(api, tmp_path):
    from g6.metaharness.schema import Suite

    task = api.load_apps(local(tmp_path, [row("apps")]), split="train")[0]
    suite = api.to_suite([task], split="train")
    assert isinstance(suite, Suite)
    assert suite.dev[0].case_id == task.task_id
    assert suite.dev[0].input == task.prompt
    assert suite.dev[0].meta["train_ok"] is True
    assert suite.dev[0].meta["licence_row"] == task.to_record()["licence_row"]
    assert suite.dev[0].meta["provenance"] == task.to_record()["provenance"]
    assert suite.dev[0].expected == task.to_record()["public_tests"]


@pytest.mark.parametrize("source,changes", [
    ("apps", {"input_output": '{"inputs":["x"],"outputs":[]}'}),
    ("apps", {"input_output": '{"inputs":[[3]],"outputs":[5],"fn_name":"bump"}'}),
    ("codecontests", {"public_tests": {"input": [], "output": []}}),
    ("mbpp", {"test_list": ["assert ("]}),
    ("livecodebench", {"public_test_cases": "opaque compressed bytes"}),
])
def test_unsupported_or_broken_checks_refused(api, tmp_path, source, changes):
    with pytest.raises(api.TaskError, match="^G6_E_RSI_FILE_INVALID$"):
        api.load_tasks(source, local(tmp_path, [row(source, **changes)]), split="eval")


def test_missing_file_stable_error(api, tmp_path):
    with pytest.raises(api.TaskError, match="^G6_E_RSI_FILE_INVALID$"):
        api.load_apps(tmp_path / "missing.json", split="train")


@pytest.fixture
def task(api):
    return api.TaskSpec(task_id="apps:toy", family="toy", source="apps", licence_row=api.licence_for("apps"),
                        prompt="Print the input plus two.", public_tests=api.CheckSpec(
                            "stdin_stdout", inputs=("3\n",), outputs=("5\n",)),
                        hidden_ref="", difficulty="introductory", created_at="", split="train",
                        source_split="train", train_ok=True)


def test_direct_record_round_trip(api, task):
    assert api.TaskSpec.from_record(json.loads(json.dumps(task.to_record()))) == task


@pytest.mark.parametrize("source", ["mbpp", "livecodebench"])
def test_direct_eval_only_cannot_claim_train_flag(api, task, source):
    with pytest.raises(api.TaskError, match="^G6_E_RSI_LICENCE_REFUSED$"):
        replace(task, task_id=f"{source}:toy", source=source, licence_row=api.licence_for(source),
                split="eval", created_at="2024-10-01", provenance=None)


@pytest.mark.parametrize("date,allowed", [("2024-08-31", False), ("2024-09-30", False), ("2024-10-01", True)])
def test_direct_lcb_cutoff(api, task, date, allowed):
    item = replace(task, task_id="livecodebench:toy", source="livecodebench",
                   licence_row=api.licence_for("livecodebench"), split="eval", created_at=date,
                   train_ok=False, provenance=None)
    assert ("held_out" in item.split_eligibility) is allowed
    assert "train" not in item.split_eligibility
    if not allowed:
        with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
            replace(item, split="held_out")


def test_record_digest_detects_changed_checks(api, task):
    record = task.to_record()
    record["public_tests"]["outputs"][0] = "6\n"
    with pytest.raises(api.TaskError, match="^G6_E_RSI_RECORD_INVALID$"):
        api.TaskSpec.from_record(record)


def test_registry_json_matches_runtime_rows(api):
    from dataclasses import asdict
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parents[2] / "validation/rsi/licences.json").read_text())
    assert data == [asdict(api.licence_for(source)) for source in
                    ("mbpp", "apps", "codecontests", "livecodebench", "generated")]


def test_direct_suite_metadata(api, task):
    suite = api.to_suite([task], split="train")
    assert suite.dev[0].meta["provenance"] == task.to_record()["provenance"]
    assert suite.dev[0].meta["train_ok"] is True


@pytest.mark.parametrize("field,value", [("train_ok", 1), ("eval_ok", 1), ("redistribute", 0)])
def test_serialized_policy_flags_must_be_booleans(api, task, field, value):
    record = task.to_record()
    record["licence_row"][field] = value
    with pytest.raises(api.TaskError):
        api.TaskSpec.from_record(record)


@pytest.mark.parametrize("date", ["2024-W01-1", "20240101", "2024-01-01T23:00:00-12:00"])
def test_lcb_alternate_date_spelling_cannot_bypass_cutoff(api, task, date):
    with pytest.raises(api.TaskError):
        replace(task, task_id="livecodebench:toy", source="livecodebench",
                licence_row=api.licence_for("livecodebench"), split="held_out", created_at=date,
                train_ok=False, provenance=None)


@pytest.mark.parametrize("source", ["mbpp", "livecodebench"])
@pytest.mark.parametrize("entry", ["constructor", "record", "suite"])
def test_direct_eval_only_training_boundaries(api, task, source, entry):
    item = replace(task, task_id=f"{source}:toy", source=source, licence_row=api.licence_for(source),
                   split="eval", created_at="2024-10-01", train_ok=False, provenance=None)
    with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
        if entry == "constructor":
            replace(item, split="train")
        elif entry == "record":
            record = item.to_record()
            record["split"] = "train"
            api.TaskSpec.from_record(record)
        else:
            api.to_suite([item], split="train")


def test_direct_item_restriction_blocks_training(api, task):
    item = replace(task, split="eval", train_ok=False, provenance=None)
    assert "train" not in item.split_eligibility
    with pytest.raises(api.TaskError, match="^G6_E_RSI_SPLIT_REFUSED$"):
        replace(item, split="train")


def test_nontraining_source_split_does_not_claim_training(api, task):
    with pytest.raises(api.TaskError, match="^G6_E_RSI_LICENCE_REFUSED$"):
        replace(task, source_split="test", split="eval", provenance=None)
