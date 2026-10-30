# SPDX-License-Identifier: Apache-2.0
"""Data-only code-application workflows. Expected answers never enter the candidate's run folder.

The host and its filesystem permissions remain trusted. A same-user shell with unrestricted
access can read this data; hiding it from such a shell requires a separate host boundary.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.core.llm import NoModel
from g6.harness import Check
from g6.metaharness.schema import Model, Suite, SuiteCase, Workflow


def valid_files(files: dict[str, str]) -> bool:
    return bool(files) and len({name.casefold() for name in files}) == len(files) and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\.py", name)
                               and not name.startswith("_g6_") and len(text) <= 200_000
                               and name.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(10)}, *{f"LPT{i}" for i in range(10)}}
                               for name, text in files.items())


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(max_length=100_000)
    expected: str = Field(max_length=100_000)


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    task: str = Field(max_length=8000)
    cases: list[Case] = Field(min_length=2, max_length=1000)
    baseline_code: dict[str, str]
    check: Literal["exact", "contains", "number"] = "exact"
    tolerance: float = Field(0.0, ge=0, allow_inf_nan=False)
    seed: int = 0

    @model_validator(mode="after")
    def validate_application(self):
        if "{input}" not in self.task:
            raise ValueError("task must include {input}")
        if not valid_files(self.baseline_code) or "main.py" not in self.baseline_code:
            raise ValueError("baseline_code needs flat Python filenames including main.py with solve(task)")
        if len({row.input for row in self.cases}) != len(self.cases):
            raise ValueError("case inputs must be distinct before splitting")
        if self.check == "contains" and any(not row.expected for row in self.cases):
            raise ValueError("contains checks require nonempty expected strings")
        return self


@dataclass(frozen=True)
class Oracle:
    kind: str
    tolerance: float

    def __call__(self, output, expected):
        if self.kind == "exact":
            return output.strip() == str(expected).strip()
        if self.kind == "contains":
            return bool(str(expected)) and str(expected) in output
        try:
            actual, wanted = float(output), float(expected)
            return math.isfinite(actual) and math.isfinite(wanted) and abs(actual - wanted) <= self.tolerance
        except (TypeError, ValueError):
            return False


def nonempty(output: str) -> bool:
    return bool(output.strip())


def entry(spec: WorkflowInput, folder: Path) -> dict:
    rows = list(enumerate(spec.cases))
    random.Random(spec.seed).shuffle(rows)
    ndev = max(1, len(rows) // 5)
    cases = tuple(SuiteCase(f"case-{i}", row.input, row.expected) for i, row in rows)
    workflow = Workflow(spec.name, spec.task,
                        (Check("produces a nonempty answer", nonempty, author="g6", known_bad=("",),
                               known_good="an answer"),), Oracle(spec.check, spec.tolerance),
                        Suite(cases[:ndev], cases[ndev:]), {"code": Model(NoModel())}, code_root=folder / "code")
    return {"workflow": workflow, "workspace": folder / "runs"}


def create(payload: dict, root: Path) -> dict:
    spec = WorkflowInput.model_validate(payload)
    folder = root.resolve() / spec.name
    if folder.exists() or folder.is_symlink():
        raise ValueError("workflow already exists; create a new name rather than replace its evaluator")
    # The data schema is validated before writing; execution still requires confirmation.
    folder.mkdir(parents=True, exist_ok=False)
    code = folder / "code"
    code.mkdir()
    for name, content in spec.baseline_code.items():
        (code / name).write_text(content, encoding="utf-8")
    (folder / "workflow.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return entry(spec, folder)


def discover(root: Path) -> dict:
    found = {}
    if not root.is_dir():
        return found
    for file in sorted(root.glob("*/workflow.json")):
        if not file.resolve().is_relative_to(root.resolve()):
            continue
        spec = WorkflowInput.model_validate(json.loads(file.read_text(encoding="utf-8")))
        if spec.name != file.parent.name:
            raise ValueError("workflow name does not match its directory")
        # The baseline is frozen too: do not silently accept edits to its source.
        actual = {target.name for target in (file.parent / "code").iterdir()}
        if actual != set(spec.baseline_code):
            raise ValueError(f"workflow {spec.name}: baseline file set changed")
        for name, text in spec.baseline_code.items():
            target = file.parent / "code" / name
            if not target.resolve().is_relative_to(file.parent.resolve()) or target.read_text(encoding="utf-8") != text:
                raise ValueError(f"workflow {spec.name}: baseline source changed")
        found[spec.name] = entry(spec, file.parent)
    return found
