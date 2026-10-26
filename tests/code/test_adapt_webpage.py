# SPDX-License-Identifier: Apache-2.0
"""Web and merged UI behaviour; all execution uses the sandbox."""
import json
from pathlib import Path

import pytest

from g6.code import _build
from g6.code.adapt_webpage import AdaptWebpageBlock, AdaptWebpageInput
from g6.core.registry import get_registry
from g6.mcp import invoke_reply


def run(op, **kwargs):
    return AdaptWebpageBlock().infer(AdaptWebpageInput(op=op, **kwargs)).unwrap()


def test_webpage_capability_through_block():
    out = run("build", spec={"title": "<My page>", "sections": ["hero", "features"]})
    assert "&lt;My page&gt;" in out["files"]["index.html"]
    assert out["provenance"]["kind"] == "template"
    assert out["completion_state"] == "qualified-draft"
# Removal: registry.build invocation in AdaptWebpageBlock.infer.


def test_adapt_webpage_02_capabilities_never_verified():
    assert run("backend_capabilities")["completion_state"] == "qualified-draft"
# Removal: answer/checked_reply qualified-draft ceiling.


def test_adapt_ui_design_01_tokens_and_all_exporters_are_drafts():
    for fmt, file in [("css", "tokens.css"), ("tailwind", "tailwind.json"), ("dtcg", "tokens.json")]:
        out = run("build", kind="design_tokens", spec={"format": fmt, "colour": "#336699"})
        assert out["completion_state"] == "qualified-draft"
        assert file in out["files"]
        if fmt != "css":
            assert json.loads(out["files"][file])
# Removal: design_tokens registration and format exporter dispatch.


def test_adapt_ui_design_02_only_declared_background_is_checked():
    out = run("run_checks", files={"index.html": "<p>Text</p>"},
              pairs=[{"foreground": "#ffffff", "background": "#000000"}], dry_run=False)
    contrast = next(c for c in out["checks_run"] if c["name"] == "contrast")
    assert contrast["passed"] is True
    assert contrast["evidence"]["pairs"][0]["ratio"] == 21.0
# Removal: each pair uses its own background in contrast_script.


def test_d1_low_contrast_pair_is_refused():
    out = run("run_checks", files={"index.html": "<p>Text</p>"},
              pairs=[{"foreground": "#aaaaaa", "background": "#bbbbbb"}], dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_WEB_CONTRAST"
# Removal: ratio < threshold assertion in contrast_script.


def test_page_without_css_needs_no_css_checker(monkeypatch):
    original = _build.sandbox.run_script

    def no_tinycss2(source, *args, **kwargs):
        prefix = ("import builtins\n_g6_import = builtins.__import__\n"
                  "def _missing(name, *args, **kwargs):\n"
                  "    if name == 'tinycss2' or name.startswith('tinycss2.'):\n"
                  "        raise ImportError('package absent for test')\n"
                  "    return _g6_import(name, *args, **kwargs)\n"
                  "builtins.__import__ = _missing\n")
        return original(prefix + source, *args, **kwargs)

    monkeypatch.setattr(_build.sandbox, "run_script", no_tinycss2)
    out = run("run_checks", files={"index.html": "<p>Text</p>"},
              pairs=[{"foreground": "#aaaaaa", "background": "#bbbbbb"}], dry_run=False)
    skipped = {c["name"]: c for c in out["checks_not_run"]}
    assert "no CSS" in skipped["css"]["reason"]
    assert out["code"] == "G6_E_WEB_CONTRAST"
# Removal: the "no CSS found" NotRun ahead of the tinycss2 import in css_script.


@pytest.mark.parametrize("html", ["<section><b>broken</section></b>", "<ul><div>bad</div></ul>",
                                  "<p><div>bad</div></p>", "<table><tr><div>x</div></tr></table>"])
def test_d1_html_balance_and_nesting_are_refused(html):
    out = run("run_checks", files={"index.html": html}, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_WEB_HTML"
# Removal: HTMLBalance stack/parent checks or parser.finish in html_script.


def test_adapt_webpage_01_render_not_requested_is_not_passed():
    out = run("run_checks", files={"index.html": "<p>Text</p>"}, dry_run=False)
    skipped = {c["name"]: c for c in out["checks_not_run"]}
    assert skipped["render"]["passed"] is None
    assert "not requested" in skipped["render"]["reason"]
# Removal: NotRun for render=False in checks_for.


def test_adapt_webpage_01_real_overflow_is_refused_when_browser_installed():
    pytest.importorskip("playwright.sync_api")
    out = run("run_checks", files={"index.html": "<div style='width:2400px'>Wide</div>"},
              render=True, breakpoints=[360, 1024], dry_run=False)
    render = next(c for c in out["checks_run"] + out["checks_not_run"] if c["name"] == "render")
    if render["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING":
        pytest.skip(render["reason"])
    assert render["passed"] is False
    assert out["completion_state"] == "blocked-escalated"
    assert render["evidence"]["measurements"][0]["overflow"] > 0
# Removal: overflow > 1 assertion in render_script.


def test_css_syntax_failure_is_refused_when_web_extra_installed():
    pytest.importorskip("tinycss2")
    out = run("run_checks", files={"index.html": "<p>OK</p>", "style.css": "p { color red; }"},
              dry_run=False)
    assert out["code"] == "G6_E_WEB_CSS"
    assert out["completion_state"] == "blocked-escalated"
# Removal: parse_declaration_list errors checked in css_script.


def test_adapt_webpage_03_no_vendored_assets():
    import g6.code.adapt_webpage as package
    names = [p for p in Path(package.__file__).parent.rglob("*") if p.is_file()]
    assert not any(p.suffix in {".ttf", ".otf", ".gz"} or "template_library" in p.parts for p in names)
# Removal guard: adding the cut fonts/template atlas/tarball makes this fail.


def test_dry_run_never_calls_sandbox(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run executed code")
    monkeypatch.setattr(_build.sandbox, "run_script", forbidden)
    out = run("run_checks", files={"index.html": "<p>Text</p>"})
    assert out["dry_run"] is True and not out["checks_run"]
# Removal: dry_run branch in AdaptWebpageBlock.infer.


def test_sensitive_mcp_surface_and_plain_build():
    registry = get_registry()
    assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(registry.invoke("adapt_webpage", "run_checks", {}, block_sensitive=True).failure())
    assert invoke_reply("adapt_webpage", "build", json.dumps({"spec": {"title": "Offline"}}))["ok"]
# Removal: __g6_sensitive_ops__ run_checks declaration or typed block registration.


def test_untrusted_approval_field_is_refused():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AdaptWebpageInput(op="run_checks", approved=True)
# Removal: extra=forbid in AdaptWebpageInput.


def test_backend_can_be_registered_from_python():
    block = AdaptWebpageBlock()
    block.backends.register("local", lambda spec: _build.Artefact.from_text({"index.html": "<p>Custom</p>"}),
                            lambda **kwargs: [])
    out = block.infer(AdaptWebpageInput(op="build", kind="local")).unwrap()
    assert "Custom" in out["files"]["index.html"]
# Removal: use self.backends rather than a hard-wired builder.

def test_component_fragments_load_their_css():
    out = run("build", spec={"mode": "component", "component_type": "hero"})
    assert 'href="style.css"' in out["files"]["index.html"]
# Removal: fragment document wrapping in build_page.


@pytest.mark.parametrize("package,extra,code", [
    ("tinycss2", "web", "G6_E_CODE_CHECKER_NOT_INSTALLED"),
    ("playwright", "render", "G6_E_OUTSIDE_PROGRAM_MISSING"),
])
def test_requested_check_missing_package_blocks(monkeypatch, package, extra, code):
    original = _build.sandbox.run_script

    def missing(source, *args, **kwargs):
        prefix = (
            "import builtins\n_g6_import = builtins.__import__\n"
            "def _missing(name, *args, **kwargs):\n"
            f"    if name == {package!r} or name.startswith({package + '.'!r}):\n"
            "        raise ImportError('package absent for test')\n"
            "    return _g6_import(name, *args, **kwargs)\n"
            "builtins.__import__ = _missing\n"
        )
        return original(prefix + source, *args, **kwargs)

    monkeypatch.setattr(_build.sandbox, "run_script", missing)
    # Only the CSS case sends CSS, so the render case does not depend on whether tinycss2 is installed.
    files = {"index.html": "<p>Text</p>"} | ({"style.css": "p { color: black; }"} if package == "tinycss2" else {})
    out = run("run_checks", files=files, render=package == "playwright", dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == code
    assert "not installed" in out["warning_card"]["message"]
    assert f'g6[{extra}]' in out["warning_card"]["message"]
    assert any(c["passed"] is False and c["code"] == code for c in out["checks_run"])
# Removal: CheckerUnavailable on import failure in css_script/render_script.


def test_requested_render_missing_browser_uses_outside_require(monkeypatch):
    original = _build.sandbox.run_script

    def missing_browser(source, *args, **kwargs):
        prefix = (
            "import sys, types\nfrom contextlib import nullcontext\n"
            "from g6.core import outside\n"
            "api = types.ModuleType('playwright.sync_api')\n"
            "api.sync_playwright = lambda: nullcontext(types.SimpleNamespace(\n"
            "    chromium=types.SimpleNamespace(executable_path='/g6-test-absent/chromium')))\n"
            "sys.modules['playwright'] = types.ModuleType('playwright')\n"
            "sys.modules['playwright.sync_api'] = api\n"
            "def _absent(program, **kwargs):\n"
            "    evidence['outside_detect_called'] = program.name\n"
            "    return outside.Detected(program.name, False, detail='browser not installed')\n"
            "outside.detect = _absent\n"
        )
        return original(prefix + source, *args, **kwargs)

    monkeypatch.setattr(_build.sandbox, "run_script", missing_browser)
    out = run("run_checks", files={"index.html": "<p>Text</p>"}, render=True, dry_run=False)
    render = next(c for c in out["checks_run"] if c["name"] == "render")
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING"
    assert render["passed"] is False
    assert render["evidence"]["outside_detect_called"] == "Playwright Chromium"
    assert "python -m playwright install chromium" in render["reason"]
# Removal: ready = require(replace(program, command=str(executable))) in render_script.
