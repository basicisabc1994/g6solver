# SPDX-License-Identifier: Apache-2.0
"""Build datasets from JSONL files, and split, sample, merge or filter them.

Ported from the archive's ``learning_layer/dataset_builder.py``. Not kept: ``discover_extension_datasets`` (it scanned
``~/.g6/extensions``) and ``from_generator`` (``Dataset(name, generator.generate(n))`` does the same). ``sample`` is a
utility for the caller; the runner never subsamples an arm (audit 006, LL-004).
"""
from __future__ import annotations

import json
import os
import random
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from g6.learning.schema import Dataset, TaskInstance

_INPUT_KEYS = ("input", "prompt", "question", "goal", "task", "description")
_EXPECTED_KEYS = ("expected", "expected_output", "answer", "target", "ground_truth", "reference", "reference_answer",
                  "output")
_META_KEYS = ("rubric", "criteria", "score_mode", "score_threshold", "domain")


class DatasetBuilder:
    @staticmethod
    def from_jsonl(path: str | os.PathLike[str], name: str | None = None) -> Dataset:
        """One task per non-blank line. Each needs task text and an expected answer or a rubric."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"G6_E_LEARNING_DATASET_NOT_FOUND: {p}")
        instances: list[TaskInstance] = []
        for line_num, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"G6_E_LEARNING_DATASET_INVALID: line {line_num} is not JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"G6_E_LEARNING_DATASET_INVALID: line {line_num} is not a JSON object")
            text, expected = _first(obj, _INPUT_KEYS), _first(obj, _EXPECTED_KEYS)
            metadata = dict(obj.get("metadata") or {})
            for key in _META_KEYS:
                if key in obj:
                    metadata.setdefault(key, obj[key])
            if text is None:
                raise ValueError(f"G6_E_LEARNING_DATASET_INVALID: line {line_num} has no task text "
                                 f"({', '.join(_INPUT_KEYS)})")
            if expected is None and not (metadata.get("rubric") or metadata.get("criteria")):
                raise ValueError(f"G6_E_LEARNING_DATASET_INVALID: line {line_num} has no expected answer or rubric")
            instances.append(TaskInstance(input=str(text), expected_output="" if expected is None else str(expected),
                                          instance_id=str(obj.get("instance_id", obj.get("id", "")) or ""),
                                          metadata=metadata))
        return Dataset(name=name or p.stem, instances=instances)

    @staticmethod
    def split(dataset: Dataset, train_ratio: float = 0.8, seed: int | None = None) -> tuple[Dataset, Dataset]:
        """A shuffled train / validation split. The validation half has ``split="validation"``, which a training run
        refuses to learn from (LL-012)."""
        if not 0.0 < train_ratio < 1.0:
            raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")
        instances = list(dataset.instances)
        random.Random(seed).shuffle(instances)
        cut = max(1, int(len(instances) * train_ratio))
        return (Dataset(f"{dataset.name}_train", instances[:cut], split="train"),
                Dataset(f"{dataset.name}_val", instances[cut:], split="validation"))

    @staticmethod
    def sample(dataset: Dataset, n: int, seed: int | None = None) -> Dataset:
        chosen = list(dataset.instances) if n >= len(dataset.instances) else random.Random(seed).sample(
            dataset.instances, n)
        return Dataset(f"{dataset.name}_sample", chosen, split=dataset.split)

    @staticmethod
    def merge(datasets: list[Dataset], name: str | None = None) -> Dataset:
        return Dataset(name or "+".join(d.name for d in datasets), [i for d in datasets for i in d.instances])

    @staticmethod
    def filter(dataset: Dataset, predicate: Callable[[TaskInstance], bool]) -> Dataset:
        return Dataset(f"{dataset.name}_filtered", [i for i in dataset.instances if predicate(i)], split=dataset.split)


def _first(obj: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


__all__ = ["DatasetBuilder"]
