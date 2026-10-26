# SPDX-License-Identifier: Apache-2.0
"""Fixed check scripts; all run through the family's sandbox archive transport."""
import inspect
import json
import pathlib
from html.parser import HTMLParser

from g6.code._build import Check
from g6.code._types import CHECKER_NOT_INSTALLED, CodeRefused as CheckerUnavailable
from g6.core.outside import OutsideProgram

class NotRun(Exception):
    """Child script sentinel; the sandbox prelude supplies it."""

BROWSER = OutsideProgram(
    name="Playwright Chromium", purpose="measure horizontal overflow at stated breakpoints",
    install="Install g6[render], then run python -m playwright install chromium yourself.",
    licence="BSD-3-Clause and Chromium third-party notices; Playwright driver Apache-2.0",
    command="chromium",
)


class HTMLBalance(HTMLParser):
    """Conservative explicit-close subset, not a full HTML5 content model."""
    void = set("area base br col embed hr img input link meta param source track wbr".split())
    block = set("address article aside blockquote div dl fieldset footer form h1 h2 h3 h4 h5 h6 header hr main nav ol p pre section table ul".split())
    children = {"ul": {"li", "script", "template"}, "ol": {"li", "script", "template"},
                "table": {"caption", "colgroup", "thead", "tbody", "tfoot", "tr", "script", "template"},
                "thead": {"tr"}, "tbody": {"tr"}, "tfoot": {"tr"}, "tr": {"td", "th"},
                "select": {"option", "optgroup"}, "optgroup": {"option"}}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.styles = []
        self.inline_styles = []

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else ""
        if parent in self.children and tag not in self.children[parent]:
            raise ValueError(f"{tag} is not permitted directly inside {parent}")
        if "p" in self.stack and tag in self.block:
            raise ValueError("block content nested in p")
        if tag in {"a", "button", "form"} and tag in self.stack:
            raise ValueError(f"nested {tag}")
        if tag == "li" and parent not in {"ul", "ol", "menu"}:
            raise ValueError("li needs a list parent")
        if tag in {"td", "th"} and parent != "tr":
            raise ValueError("table cell needs a row")
        for key, value in attrs:
            if key == "style" and value:
                self.inline_styles.append(value)
        if tag not in self.void:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.void or not self.stack or self.stack[-1] != tag:
            raise ValueError(f"unbalanced closing tag: {tag}")
        self.stack.pop()

    def handle_data(self, text):
        if self.stack and self.stack[-1] == "style":
            self.styles.append(text)

    def finish(self):
        self.close()
        if self.stack:
            raise ValueError("unclosed tags: " + ", ".join(self.stack))


def html_script(root, evidence):
    files = list(root.rglob("*.html"))
    if not files:
        raise NotRun("no HTML files")
    for file in files:
        parser = HTMLBalance()
        parser.feed(file.read_text(encoding="utf-8"))
        parser.finish()
    evidence["files"] = [str(f.relative_to(root)) for f in files]
    evidence["blind_spot"] = "explicit tag balance and common nesting only; not a full HTML5 validator"


def css_script(root, evidence):
    styles = [(f.name, f.read_text(encoding="utf-8"), False) for f in root.rglob("*.css")]
    for file in root.rglob("*.html"):
        parser = HTMLBalance()
        parser.feed(file.read_text(encoding="utf-8"))
        styles.extend((file.name, s, False) for s in parser.styles)
        styles.extend((file.name, s, True) for s in parser.inline_styles)
    if not styles:
        raise NotRun("no CSS found")
    # Only a page that has CSS needs the CSS parser.
    try:
        import tinycss2
    except ImportError:
        raise CheckerUnavailable(CHECKER_NOT_INSTALLED, 'tinycss2 is not installed; pip install "g6[web] @ git+https://github.com/basicisabc1994/g6solver".')
    def walk(nodes):
        for node in nodes:
            if node.type == "error":
                raise ValueError(node.message)
            if node.type == "at-rule" and node.content is not None:
                if node.lower_at_keyword in {"media", "supports", "layer", "container", "keyframes"}:
                    walk(tinycss2.parse_rule_list(node.content))
                else:
                    walk(tinycss2.parse_declaration_list(node.content))
            elif node.type == "qualified-rule":
                walk(tinycss2.parse_declaration_list(node.content))
            elif node.type == "declaration":
                walk(node.value)
            for key in ("arguments",):
                if hasattr(node, key):
                    walk(getattr(node, key))
    for name, text, inline in styles:
        walk(tinycss2.parse_declaration_list(text) if inline else tinycss2.parse_stylesheet(text))
    evidence["stylesheets"] = len(styles)
    evidence["blind_spot"] = "syntax only; CSS recovery, property semantics and cascade are not validated"


def contrast_script(pairs, evidence):
    from g6.code.adapt_webpage.color_math import _contrast_ratio
    if not pairs:
        raise NotRun("no declared foreground/background pairs")
    evidence["pairs"] = []
    for pair in pairs:
        ratio = _contrast_ratio(pair["foreground"], pair["background"])
        threshold = 3.0 if pair["large"] else 4.5
        evidence["pairs"].append({**pair, "ratio": ratio, "threshold": threshold})
        assert ratio >= threshold, f"{pair['role']} contrast {ratio:.3f} is below {threshold}"
    evidence["blind_spot"] = "declared opaque tokens only; no rendered compositing or font-size measurement"


def render_script(root, evidence, breakpoints):
    from dataclasses import replace
    from g6.core.outside import OUTSIDE_PROGRAM_MISSING, OutsideProgram, require
    from urllib.parse import unquote, urlsplit
    import mimetypes
    program = OutsideProgram(name="Playwright Chromium", purpose="measure horizontal overflow",
                             install='pip install "g6[render] @ git+https://github.com/basicisabc1994/g6solver", then python -m playwright install chromium yourself.',
                             licence="BSD-3-Clause and Chromium third-party notices; Playwright driver Apache-2.0",
                             command="chromium")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # The requested renderer cannot run without its driver, even if another Chromium exists.
        ready = require(program, which=lambda command: None)
        raise CheckerUnavailable(OUTSIDE_PROGRAM_MISSING, "Playwright is not installed. " + str(ready.error))
    with sync_playwright() as playwright:
        executable = pathlib.Path(playwright.chromium.executable_path)
        ready = require(replace(program, command=str(executable)))
        if not ready.is_ok():
            raise CheckerUnavailable(OUTSIDE_PROGRAM_MISSING, str(ready.error))
        browser = playwright.chromium.launch(headless=True, timeout=15000)
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False)
            def route_local(route):
                url = urlsplit(route.request.url)
                path = (root / unquote(url.path.lstrip("/"))).resolve()
                if url.hostname != "g6.invalid" or not path.is_relative_to(root) or not path.is_file():
                    route.abort()
                    return
                route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or "text/plain")
            context.route("**/*", route_local)
            # Block WebSockets too: HTTP routing does not intercept their handshake.
            context.route_web_socket("**/*", lambda socket: socket.close())
            pages = list(root.rglob("*.html"))
            if not pages:
                raise NotRun("no HTML files to render")
            evidence["measurements"] = []
            for file in pages:
                for width in breakpoints:
                    page = context.new_page()
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto("https://g6.invalid/" + file.relative_to(root).as_posix(), wait_until="load", timeout=10000)
                    page.evaluate("document.fonts.ready")
                    measured = page.evaluate("""() => ({
                        scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
                        viewport: document.documentElement.clientWidth
                    })""")
                    overflow = measured["scroll"] - measured["viewport"]
                    evidence["measurements"].append({"file": file.name, "width": width, "overflow": overflow})
                    page.close()
            assert all(m["overflow"] <= 1 for m in evidence["measurements"]), "horizontal overflow exceeds 1px"
            evidence["blind_spot"] = "initial layout only; no interaction, clipped-text, screenshot or rendered-contrast oracle"
        finally:
            browser.close()


def _body(fn, *arguments):
    # The sandbox wrapper supplies these explicit arguments to each script function.
    return inspect.getsource(fn) + "\n" + fn.__name__ + "(" + ", ".join(arguments) + ")\n"


def checks_for(*, pairs=(), render=False, breakpoints=(360, 768, 1280)):
    parser = "from html.parser import HTMLParser\n" + inspect.getsource(HTMLBalance) + "\n"
    return [
        Check("html", parser + _body(html_script, "root", "evidence"), "G6_E_WEB_HTML"),
        Check("css", parser + _body(css_script, "root", "evidence"), "G6_E_WEB_CSS"),
        Check("contrast", "pairs = json.loads(" + repr(json.dumps(list(pairs))) + ")\n" + _body(contrast_script, "pairs", "evidence"),
              "G6_E_WEB_CONTRAST"),
        Check("render", ("breakpoints = " + repr(list(breakpoints)) + "\n" + _body(render_script, "root", "evidence", "breakpoints")) if render
              else "raise NotRun('render not requested')", "G6_E_WEB_RENDER", timeout_s=60, mem_mb=2048),
    ]
