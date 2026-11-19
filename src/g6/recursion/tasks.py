# SPDX-License-Identifier: Apache-2.0
"""Local task sources over Suite, with source policy checked at every boundary."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from g6.metaharness.schema import Suite, SuiteCase
from g6.rsi.licences import LicenceRow, licence_for
from g6.rsi.records import CheckSpec, Provenance, TaskError, content_digest

__all__ = ["TaskError", "TaskSpec", "TaskSource", "CheckSpec", "Provenance", "LicenceRow", "licence_for",
           "load_tasks", "load_mbpp", "load_apps", "load_codecontests", "load_livecodebench", "to_suite", "run_checks"]


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    family: str
    source: str
    licence_row: LicenceRow
    prompt: str
    public_tests: CheckSpec
    hidden_ref: str
    difficulty: str | int | float
    created_at: str
    split: str
    source_split: str = "test"
    train_ok: bool = False
    provenance: Provenance | None = field(default=None)

    def __post_init__(self) -> None:
        row = licence_for(self.source)
        if self.licence_row != row or type(self.train_ok) is not bool or (
            self.train_ok and (not row.train_ok or self.source_split != "train")
        ):
            raise TaskError("G6_E_RSI_LICENCE_REFUSED")
        if any(type(v) is not str or not v.strip() for v in (self.task_id, self.family, self.prompt)):
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        if not isinstance(self.public_tests, CheckSpec) or type(self.hidden_ref) is not str:
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        if type(self.difficulty) not in (str, int, float) or (
            type(self.difficulty) in (int, float) and not math.isfinite(self.difficulty)
        ):
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        if type(self.created_at) is not str or self.source_split not in ("train", "test", "valid", "validation"):
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        if self.source == "livecodebench":
            try:
                calendar_date = date.fromisoformat(self.created_at[:10])
                if calendar_date.isoformat() != self.created_at[:10]:
                    raise ValueError
                if len(self.created_at) != 10:
                    datetime.fromisoformat(self.created_at)
            except ValueError:
                raise TaskError("G6_E_RSI_RECORD_INVALID") from None
        self.require_split(self.split)
        prefix = self.source + ":"
        if not self.task_id.startswith(prefix) or not self.task_id[len(prefix):].strip():
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        expected = Provenance(self.source, row.licence_id, self.task_id[len(prefix):], content_digest(self._content()))
        if self.provenance is None:
            object.__setattr__(self, "provenance", expected)
        elif self.provenance != expected:
            raise TaskError("G6_E_RSI_RECORD_INVALID")

    @property
    def source_id(self) -> str:
        return self.source

    @property
    def licence_id(self) -> str:
        return self.licence_row.licence_id

    @property
    def split_eligibility(self) -> tuple[str, ...]:
        row = licence_for(self.source)
        eligible = []
        if row.train_ok and self.train_ok and self.source_split == "train":
            eligible.extend(("train", "dev"))
        if row.eval_ok:
            eligible.append("eval")
            if self.source == "mbpp":
                eligible.append("retention")
            elif self.source == "livecodebench" and date.fromisoformat(self.created_at[:10]) >= date(2024, 10, 1):
                eligible.append("held_out")
            elif self.source == "codecontests" and self.source_split in ("valid", "validation"):
                eligible.append("dev")
            elif self.source == "generated" and self.source_split == "test" and not self.train_ok:
                eligible.append("held_out")
        return tuple(eligible)

    def require_split(self, split: str) -> None:
        if split not in self.split_eligibility:
            raise TaskError("G6_E_RSI_SPLIT_REFUSED")

    def _content(self) -> dict:
        return {"task_id": self.task_id, "family": self.family, "source": self.source,
                "licence_id": self.licence_id, "prompt": self.prompt, "public_tests": self.public_tests.to_record(),
                "hidden_ref": self.hidden_ref, "difficulty": self.difficulty, "created_at": self.created_at,
                "source_split": self.source_split, "train_ok": self.train_ok,
                "split_eligibility": list(self.split_eligibility)}

    def to_record(self) -> dict:
        return self._content() | {"source_id": self.source_id, "licence_row": asdict(self.licence_row),
                                  "provenance": asdict(self.provenance), "split": self.split}

    @classmethod
    def from_record(cls, record: dict) -> TaskSpec:
        try:
            task = cls(task_id=record["task_id"], family=record["family"], source=record["source"],
                       licence_row=LicenceRow(**record["licence_row"]), prompt=record["prompt"],
                       public_tests=CheckSpec.from_record(record["public_tests"]), hidden_ref=record["hidden_ref"],
                       difficulty=record["difficulty"], created_at=record["created_at"], split=record["split"],
                       source_split=record["source_split"], train_ok=record["train_ok"],
                       provenance=Provenance(**record["provenance"]))
            if task.to_record() != record:
                raise ValueError
            return task
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise TaskError("G6_E_RSI_RECORD_INVALID") from None


@dataclass(frozen=True)
class TaskSource:
    source: str
    path: Path

    def iter(self, split: str) -> Iterator[TaskSpec]:
        # Loading the complete file before returning an iterator prevents prefix leakage.
        return iter(load_tasks(self.source, self.path, split=split))


def load_tasks(source: str, path: str | Path, *, split: str) -> tuple[TaskSpec, ...]:
    from g6.rsi.loaders import load_local

    return load_local(source, path, split=split)


def load_mbpp(path: str | Path, *, split: str) -> tuple[TaskSpec, ...]:
    return load_tasks("mbpp", path, split=split)


def load_apps(path: str | Path, *, split: str) -> tuple[TaskSpec, ...]:
    return load_tasks("apps", path, split=split)


def load_codecontests(path: str | Path, *, split: str) -> tuple[TaskSpec, ...]:
    return load_tasks("codecontests", path, split=split)


def load_livecodebench(path: str | Path, *, split: str) -> tuple[TaskSpec, ...]:
    return load_tasks("livecodebench", path, split=split)


def to_suite(tasks: list[TaskSpec] | tuple[TaskSpec, ...], *, split: str) -> Suite:
    cases = []
    for task in tasks:
        task = TaskSpec.from_record(task.to_record())
        task.require_split(split)
        record = task.to_record()
        cases.append(SuiteCase(task.task_id, task.prompt, record["public_tests"],
                               {"train_ok": "train" in task.split_eligibility, "licence_row": record["licence_row"],
                                "provenance": record["provenance"], "split": split}))
    if len({case.case_id for case in cases}) != len(cases):
        raise TaskError("G6_E_RSI_RECORD_INVALID")
    return Suite(dev=tuple(cases) if split != "held_out" else (),
                 held_out=tuple(cases) if split == "held_out" else ())


def run_checks(task: TaskSpec, solution: str, *, repo: str | Path, timeout_s: int = 30) -> dict:
    from g6.rsi.checks import run_task_checks

    return run_task_checks(task, solution, repo=repo, timeout_s=timeout_s)
