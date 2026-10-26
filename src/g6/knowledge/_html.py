# SPDX-License-Identifier: Apache-2.0
"""HTML to readable text with the standard library's ``html.parser``: what a person would see on the page, as text.
``_``-prefixed, so it never registers as a component.

:func:`read_html` drops what a reader never sees: scripts, styles, templates, embedded SVG, MathML, frames and objects,
comments, and any element marked hidden (the ``hidden`` attribute, ``aria-hidden="true"``, or an inline style with
``display: none`` or ``visibility: hidden``), with everything inside it. Hidden text is not dropped in silence: how
many hidden elements held text, and how much, is returned as unread. ``noscript`` is read, as a reader who runs no
scripts sees it. Elements end where a browser ends them: a paragraph at the next block, a list item at the next item,
``<dt>`` and ``<dd>``, table rows, cells and sections, and options the same way, and an end tag closes only what it
would close in a browser, so a hidden element never swallows the visible text after it. Elements nested more than
:data:`MAX_DEPTH` deep are not read, and that is returned as unread too. Headings become ``#`` lines, list items
``-`` lines, block elements paragraphs, ``<br>`` a line break; runs of spaces collapse except inside ``<pre>``;
entities are decoded (a no-break space stays one). The page's ``<title>`` is returned beside the text, not inside it.
Nothing is fetched: images, links and style sheets are left where they are. Each tag is handled in constant time, so a
page of stray end tags under many open elements reads in linear time.

What it cannot see: text placed by scripts, text hidden by a style sheet rather than an inline style, text drawn in
images, and a page that differs for other readers.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

#: What a document converted here says converted it.
CONVERTED_BY = "g6.knowledge html_to_text (the standard library's html.parser)"
#: The media types read as HTML.
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
#: Elements whose content a reader never sees as text.
DROPPED = frozenset({"script", "style", "template", "svg", "math", "iframe", "object", "embed", "canvas"})
#: The deepest nesting read; text inside elements nested deeper is reported as unread.
MAX_DEPTH = 512
_BLOCK = frozenset({"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption",
                    "figure", "footer", "form", "header", "hr", "main", "nav", "ol", "p", "pre", "section", "summary",
                    "table", "ul", "h1", "h2", "h3", "h4", "h5", "h6"})
_LINE = frozenset({"li", "dt", "dd", "tr", "caption"})
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# The sets of elements the HTML standard's tree builder consults (its "special" category and its scopes). Each open
# element in a set has its stack index kept in that set's own stack, so "is there a <p> in button scope" is a compare
# of two indices, never a walk down the stack.
_SPECIAL = frozenset({
    "address", "applet", "area", "article", "aside", "base", "basefont", "bgsound", "blockquote", "body", "br", "button",
    "caption", "center", "col", "colgroup", "dd", "details", "dir", "div", "dl", "dt", "embed", "fieldset",
    "figcaption", "figure", "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header",
    "hgroup", "hr", "html", "iframe", "img", "input", "keygen", "li", "link", "listing", "main", "marquee", "menu",
    "meta", "nav", "noembed", "noframes", "noscript", "object", "ol", "p", "param", "plaintext", "pre", "script",
    "search", "section", "select", "source", "style", "summary", "table", "tbody", "td", "template", "textarea", "tfoot",
    "th", "thead", "title", "tr", "track", "ul", "wbr", "xmp"})
_SCOPE = frozenset({"applet", "caption", "html", "table", "td", "th", "marquee", "object", "template"})
_TABLE_SCOPE = frozenset({"html", "table", "template"})
_SETS: dict[str, frozenset[str]] = {
    "special": _SPECIAL,
    "scope": _SCOPE,
    "button": _SCOPE | {"button"},
    "list": _SCOPE | {"ol", "ul"},
    "table": _TABLE_SCOPE,
    "li_stop": _SPECIAL - {"address", "div", "p", "li"},
    "dd_stop": _SPECIAL - {"address", "div", "p", "dd", "dt"},
    "body_stop": _TABLE_SCOPE | {"tbody", "thead", "tfoot"},
}
#: Start tags that close an open <p> in button scope.
_CLOSES_P = frozenset({
    "address", "article", "aside", "blockquote", "center", "dd", "details", "dialog", "dir", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr",
    "li", "listing", "main", "menu", "nav", "ol", "p", "plaintext", "pre", "search", "section", "summary", "table", "ul",
    "xmp"})
#: (the start tags, the open elements they close, the set whose open members stop the search)
_IMPLIED: tuple[tuple[frozenset[str], tuple[str, ...], str], ...] = (
    (frozenset({"li"}), ("li",), "li_stop"),
    (frozenset({"dd", "dt"}), ("dd", "dt"), "dd_stop"),
    (frozenset({"tr", "tbody", "thead", "tfoot"}), ("tr",), "body_stop"),
    (frozenset({"td", "th", "tr", "tbody", "thead", "tfoot"}), ("td", "th"), "table"),
    (frozenset({"tbody", "thead", "tfoot"}), ("tbody", "thead", "tfoot"), "table"),
    (_CLOSES_P, ("p",), "button"),
)
_TABLE_PARTS = frozenset({"caption", "table", "tbody", "td", "tfoot", "th", "thead", "tr"})
_KEEP_OPEN = frozenset({"html", "body"})  # their end tags close nothing a reader sees (text after them is still read)


def _hidden(attrs: list[tuple[str, str | None]]) -> bool:
    found = {name.lower(): (value or "") for name, value in attrs}
    return ("hidden" in found or found.get("aria-hidden", "").strip().lower() == "true"
            or bool(_HIDDEN_STYLE.search(found.get("style", ""))))


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[bool, str]] = []
        self.current: list[str] = []
        self.title: list[str] = []
        self.stack: list[tuple[str, str]] = []  # (name, why it is skipped: "", "dropped" or "hidden")
        self.where: dict[str, list[int]] = {}  # each open name's stack indices
        self.marks: dict[str, list[int]] = {name: [] for name in _SETS}
        self.deeper: dict[str, int] = {}  # elements opened past MAX_DEPTH, by name
        self.skip = 0
        self.skip_why = ""
        self.skip_chars = 0
        self.pre = 0
        self.in_title = False
        self.hidden_elements = self.hidden_chars = self.deep_chars = 0

    def flush(self) -> None:
        if self.current:
            self.blocks.append((self.pre > 0, "".join(self.current)))
            self.current = []

    def _top(self, *names: str) -> int:
        return max((self.where[n][-1] for n in names if n in self.where), default=-1)

    def _mark(self, name: str) -> int:
        found = self.marks[name]
        return found[-1] if found else -1

    def _push(self, tag: str, why: str) -> None:
        index = len(self.stack)
        self.stack.append((tag, why))
        self.where.setdefault(tag, []).append(index)
        for name, members in _SETS.items():
            if tag in members:
                self.marks[name].append(index)

    def _pop(self) -> None:
        tag, why = self.stack.pop()
        found = self.where[tag]
        found.pop()
        if not found:
            del self.where[tag]
        for name, members in _SETS.items():
            if tag in members:
                self.marks[name].pop()
        if why:
            self.skip -= 1
            if not self.skip:
                self._dropped()
        elif not self.skip:
            if tag == "title":
                self.in_title = False
            if tag in _BLOCK:
                self.flush()
                self.pre -= tag == "pre"

    def _pop_to(self, index: int) -> None:
        while len(self.stack) > index:
            self._pop()

    def _dropped(self) -> None:
        if self.skip_why == "hidden" and self.skip_chars:
            self.hidden_elements += 1
            self.hidden_chars += self.skip_chars
        self.skip_why, self.skip_chars = "", 0

    def _close_implied(self, tag: str) -> None:
        for starts, closes, stop in _IMPLIED:
            if tag in starts:
                target = self._top(*closes)
                if target > self._mark(stop):
                    self._pop_to(target)
        if tag in ("option", "optgroup") and self.stack and self.stack[-1][0] == "option":
            self._pop()
        if tag == "optgroup" and self.stack and self.stack[-1][0] == "optgroup":
            self._pop()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.deeper:
            if tag not in _VOID:
                self.deeper[tag] = self.deeper.get(tag, 0) + 1
            return
        self._close_implied(tag)
        if tag in _VOID:
            if not self.skip and tag == "br":
                self.current.append("\n")
            elif not self.skip and tag == "hr":
                self.flush()
            return
        if len(self.stack) >= MAX_DEPTH:
            self.deeper[tag] = 1
            return
        why = "dropped" if tag in DROPPED else "hidden" if _hidden(attrs) else ""
        self._push(tag, why)
        if why:
            if not self.skip:
                self.skip_why, self.skip_chars = why, 0
            self.skip += 1
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        if tag in _BLOCK:
            self.flush()
            self.pre += tag == "pre"
        if tag in _HEADINGS:
            self.current.append("#" * int(tag[1]) + " ")
        elif tag in _LINE:
            self.current.append("\n- " if tag == "li" else "\n")
        elif tag in ("td", "th"):
            self.current.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.deeper:
            if tag in self.deeper:
                self.deeper[tag] -= 1
                if not self.deeper[tag]:
                    del self.deeper[tag]
                return
            if tag not in self.where:
                return  # a stray end tag closes nothing
            self.deeper = {}  # it closes an element below the cap, and so every element opened past it
        if tag not in self.where or tag in _KEEP_OPEN:
            return  # a stray end tag closes nothing
        target = self.where[tag][-1]
        if tag in _SPECIAL:
            scope = "button" if tag == "p" else "list" if tag == "li" else "table" if tag in _TABLE_PARTS else "scope"
            if self._mark(scope) > target:
                return  # not in scope: a browser ignores it
        elif self._mark("special") > target:
            return  # a block is open inside it: a browser ignores it
        self._pop_to(target)

    def handle_data(self, data: str) -> None:
        if self.deeper:
            self.deep_chars += len(data.strip())
            return
        if self.skip:
            if self.skip_why == "hidden":
                self.skip_chars += len(data.strip())
            return
        if self.in_title:
            self.title.append(data)
        else:
            self.current.append(data.replace("\r\n", "\n") if self.pre else _SPACES.sub(" ", data))

    def unread(self) -> tuple[str, ...]:
        if self.skip:
            self._dropped()  # a hidden element still open at the end of the page
        out = []
        if self.hidden_chars:
            out.append(f"hidden text: {self.hidden_elements} element(s) marked hidden (the hidden attribute, "
                       f'aria-hidden="true", or an inline display: none or visibility: hidden) held '
                       f"{self.hidden_chars} character(s) of text; it was dropped, not read")
        if self.deep_chars:
            out.append(f"{self.deep_chars} character(s) of text nested more than {MAX_DEPTH} elements deep; not read")
        return tuple(out)


def read_html(html: str) -> tuple[str, str, tuple[str, ...]]:
    """``(title, text, unread)`` of a page: the text a reader sees, and what was dropped that a reader might have seen
    (hidden text, and text nested too deep), as the module docstring describes."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    reader.flush()
    paragraphs = []
    for pre, block in reader.blocks:
        if pre:
            text = block.strip("\n")
        else:
            text = "\n".join(line.strip(" ") for line in block.split("\n") if line.strip(" "))
        if text.strip():
            paragraphs.append(text)
    return _SPACES.sub(" ", "".join(reader.title)).strip(), "\n\n".join(paragraphs), reader.unread()


def html_to_text(html: str) -> tuple[str, str]:
    """``(title, text)`` of a page: :func:`read_html` without what it reports as unread."""
    title, text, _ = read_html(html)
    return title, text


__all__ = ["CONVERTED_BY", "DROPPED", "HTML_TYPES", "MAX_DEPTH", "html_to_text", "read_html"]