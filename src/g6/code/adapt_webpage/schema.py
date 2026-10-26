# SPDX-License-Identifier: Apache-2.0
"""Web build specifications; extra fields and missing contrast colours are refused."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class DesignToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str
    category: str
    description: str = ""


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    foreground: str = Field(min_length=1, max_length=64)
    background: str = Field(min_length=1, max_length=64)
    large: bool = False
    role: str = Field("text", max_length=100)


class WebSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field("Draft page", max_length=500)
    description: str = Field("", max_length=3000)
    layout: str = Field("landing", max_length=50)
    theme: str = Field("modern", max_length=50)
    sections: list[str] = Field(default_factory=list, max_length=30)
    custom_css: str = Field("", max_length=100_000)
    mode: Literal["page", "component", "sections", "css"] = "page"
    component_type: str = Field("hero", max_length=80)


class TokenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    colour: str = Field("#336699", max_length=30)
    format: Literal["css", "tailwind", "dtcg"] = "css"
    base_font_size: int = Field(16, ge=8, le=96)
    scale_ratio: float = Field(1.25, ge=1, le=2)
    include_dark_mode: bool = False


class AdaptWebpageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["build", "backend_capabilities", "run_checks", "write_files", "ops", "help"] = "help"
    kind: str = Field("web_page", max_length=60)
    spec: dict = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict, max_length=200)
    pairs: list[Pair] = Field(default_factory=list, max_length=100)
    breakpoints: list[Annotated[int, Field(ge=240, le=3840)]] = Field(default_factory=lambda: [360, 768, 1280],
                                                                 min_length=1, max_length=10)
    render: bool = False
    dry_run: bool = True
    dest: str = Field("", max_length=1000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)
