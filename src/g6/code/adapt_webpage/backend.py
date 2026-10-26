# SPDX-License-Identifier: Apache-2.0
"""The retained WebpageBackend protocol with deterministic offline generators."""
from dataclasses import dataclass, field
from typing import Any, Protocol

from g6.code._build import Artefact, BuildRegistry, Provenance
from g6.code.adapt_webpage import generators
from g6.code.adapt_webpage.color_math import _generate_shades
from g6.code.adapt_webpage.export import _export_css, _export_json_dtcg, _export_tailwind
from g6.code.adapt_webpage.schema import TokenSpec, WebSpec
from g6.code.adapt_webpage.tokens import (
    _generate_breakpoints, _generate_radii, _generate_shadows, _generate_spacing, _generate_typography,
)


@dataclass(frozen=True)
class BackendResult:
    ok: bool = True
    html: str = ""
    css: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class WebpageBackend(Protocol):
    backend_id: str

    def capabilities(self, *, allow_network: bool = False, allow_cdn: bool = False,
                     allow_webgl: bool = False) -> dict[str, Any]: ...

    def schema_for(self, op: str) -> BackendResult: ...

    def execute(self, op: str, payload: dict[str, Any], *, allow_network: bool = False,
                allow_cdn: bool = False, allow_webgl: bool = False, dry_run: bool = False) -> BackendResult: ...


class TemplateBackend:
    backend_id = "deterministic_webpage_backend"

    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        return {"html_generation": True, "css_generation": True, "external_fetch": False,
                "render_requires": "g6[render] and a user-installed Playwright Chromium browser",
                "modes": ["page", "component", "sections", "css"]}

    def schema_for(self, op: str) -> BackendResult:
        return BackendResult(ok=op == "build", metadata=WebSpec.model_json_schema())

    def execute(self, op: str, payload: dict[str, Any], **kwargs: Any) -> BackendResult:
        if op != "build" or any(kwargs.get(k) for k in ("allow_network", "allow_cdn", "allow_webgl")):
            raise ValueError("only offline build is supported")
        spec = WebSpec.model_validate(payload)
        if spec.mode == "page":
            out = generators.generate_page(spec.title, spec.layout, spec.theme, spec.sections,
                                           spec.custom_css, spec.description, "", "")
        elif spec.mode == "component":
            out = generators.generate_component(spec.component_type, spec.title, spec.theme)
        elif spec.mode == "sections":
            out = generators.compose_sections(spec.sections, spec.title, spec.theme)
        else:
            out = generators.generate_css(spec.theme, spec.custom_css)
        return BackendResult(html=out.get("html", ""), css=out.get("css", ""), metadata=out["metadata"])


def build_page(spec: dict) -> Artefact:
    out = TemplateBackend().execute("build", spec)
    files = {"style.css": out.css.encode()}
    if out.html:
        html = out.html
        if WebSpec.model_validate(spec).mode in {"component", "sections"}:
            html = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<link rel="stylesheet" href="style.css"></head><body>' + html + '</body></html>')
        files["index.html"] = html.encode()
    return Artefact(files, Provenance("template", template="web_page/" + str(out.metadata)))


def build_tokens(spec: dict) -> Artefact:
    data = TokenSpec.model_validate(spec)
    tokens = [t.model_dump() for t in (
        _generate_shades(data.colour) + _generate_typography(data.base_font_size, data.scale_ratio)
        + _generate_spacing() + _generate_shadows() + _generate_radii() + _generate_breakpoints())]
    if data.format == "css":
        file, content = "tokens.css", _export_css(tokens, data.include_dark_mode)
    elif data.format == "tailwind":
        file, content = "tailwind.json", _export_tailwind(tokens)
    else:
        file, content = "tokens.json", _export_json_dtcg(tokens)
    return Artefact({file: content.encode()}, Provenance("template", template="design_tokens/" + data.format))


def registry() -> BuildRegistry:
    from g6.code.adapt_webpage.checks import checks_for
    backends = BuildRegistry()
    backends.register("web_page", build_page, checks_for)
    backends.register("design_tokens", build_tokens, checks_for)
    return backends
