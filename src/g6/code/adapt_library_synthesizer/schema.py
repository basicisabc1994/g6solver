# SPDX-License-Identifier: Apache-2.0
"""Paper specs and caller-owned example oracles, kept separate from model output."""
import keyword
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) or keyword.iskeyword(value):
        raise ValueError("function name must be an ASCII Python identifier")
    return value


Identifier = Annotated[str, AfterValidator(identifier)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Example(Strict):
    function: Identifier
    args: list[JsonValue] = Field(default_factory=list, max_length=50)
    kwargs: dict[str, JsonValue] = Field(default_factory=dict, max_length=50)
    expected: JsonValue
    paper_quote: str = Field("", max_length=2000)


class AlgorithmSpec(Strict):
    name: Identifier
    inputs: list[str] = Field(default_factory=list, max_length=50)
    outputs: list[str] = Field(default_factory=list, max_length=50)
    steps: list[str] = Field(min_length=1, max_length=100)
    invariants: list[str] = Field(default_factory=list, max_length=50)
    complexity: str = Field("", max_length=1000)
    hyperparameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=50)


class ModelCode(Strict):
    implementation: str = Field(min_length=1, max_length=500_000)
    smoke_tests: str = Field("", max_length=100_000)


class AdaptLibrarySynthesizerInput(Strict):
    op: Literal["extract_spec", "build", "run_checks", "fetch_paper", "write_files",
                "backend_capabilities", "ops", "help"] = "help"
    kind: str = Field("python_module", max_length=60)
    paper_text: str = Field("", max_length=200_000)
    name: str = Field("algorithm", max_length=80)
    url: str = Field("", max_length=2000)
    spec: dict = Field(default_factory=dict)
    examples: list[Example] = Field(default_factory=list, max_length=100)
    files: dict[str, str] = Field(default_factory=dict, max_length=200)
    dry_run: bool = True
    dest: str = Field("", max_length=1000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)
