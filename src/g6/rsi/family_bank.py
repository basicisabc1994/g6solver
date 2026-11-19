# SPDX-License-Identifier: Apache-2.0
"""Registration metadata for generated families, without sealed pool contents."""
from __future__ import annotations

import json
import re

from g6.recursion.generators import GeneratedFamily, family_digest
from g6.recursion.tasks import TaskSpec
from g6.rsi.records import TaskError


class FamilyBank:
    """Keep every registered family id and content digest in just one split."""

    def __init__(self) -> None:
        self._families: dict[str, dict] = {}
        self._digests: dict[str, str] = {}

    def add(self, family: GeneratedFamily) -> None:
        if not isinstance(family, GeneratedFamily) or not family.tasks:
            raise TaskError("G6_E_RSI_FAMILY_INVALID")
        tasks = tuple(TaskSpec.from_record(task.to_record()) for task in family.tasks)
        first = tasks[0]
        if (first.split not in ("train", "dev", "held_out") or first.source != "generated"
                or any(task.family != first.family or task.split != first.split
                       or family_digest(task) != family.family_digest for task in tasks)):
            raise TaskError("G6_E_RSI_FAMILY_INVALID")
        key, digest = first.family, family.family_digest
        self._put({"family_id": key, "content_digest": digest, "split": first.split,
                   "repository_id": family.repository_id, "bug_class": family.bug_class,
                   "seed": family.seed, "task_ids": [task.task_id for task in tasks]})

    def _put(self, record: dict) -> None:
        fields = {"family_id", "content_digest", "split", "repository_id", "bug_class", "seed", "task_ids"}
        if (type(record) is not dict or set(record) != fields
                or any(type(record[key]) is not str or not record[key].strip()
                       for key in ("family_id", "content_digest", "split", "repository_id", "bug_class"))
                or record["split"] not in ("train", "dev", "held_out")
                or not re.fullmatch(r"[a-f0-9]{64}", record["content_digest"])
                or type(record["seed"]) is not int or record["seed"] < 0
                or type(record["task_ids"]) is not list or not record["task_ids"]
                or any(type(key) is not str or not key.startswith("generated:") for key in record["task_ids"])
                or len(set(record["task_ids"])) != len(record["task_ids"])):
            raise TaskError("G6_E_RSI_FAMILY_INVALID")
        key, digest = record["family_id"], record["content_digest"]
        alias = any(existing["repository_id"] == record["repository_id"]
                    and (existing["split"] != record["split"] or existing["bug_class"] == record["bug_class"])
                    for existing in self._families.values())
        if key in self._families or digest in self._digests or alias:
            raise TaskError("G6_E_RSI_FAMILY_OVERLAP")
        self._families[key] = json.loads(json.dumps(record))
        self._digests[digest] = key

    @classmethod
    def from_record(cls, record: dict) -> FamilyBank:
        if type(record) is not dict or set(record) != {"families"} or type(record["families"]) is not list:
            raise TaskError("G6_E_RSI_FAMILY_INVALID")
        bank = cls()
        for family in record["families"]:
            bank._put(family)
        return bank

    def to_record(self) -> dict:
        # Return a detached JSON record so callers cannot mutate bank ownership.
        return json.loads(json.dumps({"families": [self._families[key] for key in sorted(self._families)]}))
