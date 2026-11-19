# SPDX-License-Identifier: Apache-2.0
"""Strict adapters for local JSON arrays; importing dataset code is never needed."""
from __future__ import annotations

import json
from pathlib import Path

from g6.rsi.licences import licence_for
from g6.rsi.records import CheckSpec, TaskError


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError
        out[key] = value
    return out


def _json(text):
    def bad_constant(value):
        raise ValueError

    return json.loads(text, object_pairs_hook=_object, parse_constant=bad_constant)


def _mapping(value):
    if type(value) is not dict:
        raise ValueError
    return value


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError
    value.encode("utf-8")
    return value


def _texts(value):
    if type(value) is not list or not value or any(type(v) is not str for v in value):
        raise ValueError
    for item in value:
        item.encode("utf-8")
    return tuple(value)


def _checks(source, row):
    if source == "mbpp":
        return CheckSpec("function", tests=_texts(row["test_list"]))
    if source == "apps":
        tests = _mapping(_json(row["input_output"]) if type(row["input_output"]) is str else row["input_output"])
        if tests.get("fn_name"):
            raise ValueError  # Function-call APPS exports require a separate, explicit adapter.
        return CheckSpec("stdin_stdout", inputs=_texts(tests["inputs"]), outputs=_texts(tests["outputs"]))
    if source == "codecontests":
        tests = _mapping(row["public_tests"])
        return CheckSpec("stdin_stdout", inputs=_texts(tests["input"]), outputs=_texts(tests["output"]))
    tests = _json(row["public_test_cases"]) if type(row["public_test_cases"]) is str else row["public_test_cases"]
    if type(tests) is not list or not tests:
        raise ValueError
    inputs, outputs = [], []
    for item in tests:
        item = _mapping(item)
        if item.get("testtype", "stdin") != "stdin":
            raise ValueError
        inputs.append(item["input"])
        outputs.append(item["output"])
    return CheckSpec("stdin_stdout", inputs=_texts(inputs), outputs=_texts(outputs))


def load_local(source, path: str | Path, *, split: str):
    from g6.recursion.tasks import TaskSpec

    policy = licence_for(source)
    if split not in ("train", "dev", "eval", "held_out", "retention") or (
        split == "train" and not policy.train_ok
    ) or (split != "train" and not policy.eval_ok):
        raise TaskError("G6_E_RSI_SPLIT_REFUSED")
    try:
        data = _json(Path(path).read_text(encoding="utf-8"))
        if type(data) is not list:
            raise ValueError
        tasks = []
        keys = {"mbpp": ("task_id", "text"), "apps": ("problem_id", "question"),
                "codecontests": ("name", "description"), "livecodebench": ("question_id", "question_content")}
        id_key, prompt_key = keys[source]
        for raw in data:
            raw = _mapping(raw)
            original = raw[id_key]
            if type(original) not in (str, int):
                raise ValueError
            original = _text(str(original))
            source_split = raw.get("split", "test")
            allowed = raw.get("train_ok", policy.train_ok)
            if type(allowed) is not bool:
                raise ValueError
            # Construct in evaluation mode so malformed later rows cannot be hidden by a requested split.
            tasks.append(TaskSpec(task_id=f"{source}:{original}", family=source, source=source, licence_row=policy,
                                  prompt=_text(raw[prompt_key]), public_tests=_checks(source, raw), hidden_ref="",
                                  difficulty=raw.get("difficulty", raw.get("cf_rating", "")),
                                  created_at=raw.get("contest_date", raw.get("created_at", "")), split="eval",
                                  source_split=source_split,
                                  train_ok=allowed and policy.train_ok and source_split == "train"))
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise TaskError("G6_E_RSI_FILE_INVALID") from None
    from dataclasses import replace

    for task in tasks:
        task.require_split(split)
    return tuple(replace(task, split=split) for task in tasks)
