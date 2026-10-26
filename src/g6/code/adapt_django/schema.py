# SPDX-License-Identifier: Apache-2.0
"""Validated Django names; no executable expressions or interpreter/directory inputs."""
import keyword
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) or keyword.iskeyword(value):
        raise ValueError("expected an ASCII Python identifier, not code")
    return value


Identifier = Annotated[str, AfterValidator(identifier)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldSpec(Strict):
    name: Identifier
    field_type: Literal["CharField", "TextField", "IntegerField", "FloatField", "BooleanField", "DateField",
                        "DateTimeField", "DecimalField", "EmailField", "URLField", "ForeignKey",
                        "OneToOneField", "ManyToManyField"] = "CharField"
    max_length: int = Field(255, ge=1, le=10000)
    default: JsonValue = None
    choices: list[tuple[str | int, str]] = Field(default_factory=list, max_length=100)
    null: bool = False
    blank: bool = False
    unique: bool = False
    related_model: str = Field("", max_length=170)
    on_delete: Literal["CASCADE", "PROTECT", "RESTRICT", "SET_NULL", "SET_DEFAULT", "DO_NOTHING"] = "CASCADE"
    help_text: str = Field("", max_length=1000)
    verbose_name: str = Field("", max_length=100)
    max_digits: int = Field(10, ge=1, le=100)
    decimal_places: int = Field(2, ge=0, le=100)

    @model_validator(mode="after")
    def relations(self):
        if self.field_type in {"ForeignKey", "OneToOneField", "ManyToManyField"}:
            if self.related_model != "self":
                parts = self.related_model.split(".")
                if not 1 <= len(parts) <= 2:
                    raise ValueError("related_model must be self, Model, or app.Model")
                for part in parts:
                    identifier(part)
        if self.decimal_places > self.max_digits:
            raise ValueError("decimal_places exceeds max_digits")
        return self


class ModelSpec(Strict):
    name: Identifier
    fields: list[FieldSpec] = Field(default_factory=list, max_length=100)
    str_field: str = ""
    meta_ordering: list[str] = Field(default_factory=list, max_length=20)
    abstract: bool = False

    @model_validator(mode="after")
    def field_names(self):
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate model field")
        if self.str_field:
            identifier(self.str_field)
            if self.str_field not in names:
                raise ValueError("str_field must name a declared field")
        for name in self.meta_ordering:
            identifier(name.removeprefix("-"))
        return self


class ViewSpec(Strict):
    name: Identifier
    model_name: Identifier
    view_type: Literal["list", "detail", "create", "update", "delete"] = "list"
    template_name: str = Field("", max_length=200)
    fields: list[Identifier] = Field(default_factory=list, max_length=100)
    filter_fields: list[Identifier] = Field(default_factory=list, max_length=100)
    paginate_by: int = Field(20, ge=1, le=500)
    login_required: bool = False


class DjangoSpec(Strict):
    app_name: Identifier = "app"
    project_name: Identifier = "project"
    models: list[ModelSpec] = Field(default_factory=list, max_length=30)
    views: list[ViewSpec] = Field(default_factory=list, max_length=60)
    parts: list[Literal["models", "views", "urls", "admin", "forms", "serializers", "settings"]] = Field(
        default_factory=lambda: ["models", "views", "urls", "admin", "forms", "serializers", "settings"],
        min_length=1, max_length=7)

    @model_validator(mode="after")
    def unique_names(self):
        if self.app_name == self.project_name:
            raise ValueError("app and project names must differ")
        for values in (self.models, self.views):
            names = [v.name for v in values]
            if len(names) != len(set(names)):
                raise ValueError("duplicate name")
        return self


class AdaptDjangoInput(Strict):
    op: Literal["build", "run_checks", "write_files", "ops", "help"] = "help"
    kind: str = Field("django", max_length=60)
    spec: dict = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict, max_length=200)
    settings_module: str = Field("project.settings", max_length=240)
    dry_run: bool = True
    dest: str = Field("", max_length=1000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)
