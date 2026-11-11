# SPDX-License-Identifier: Apache-2.0
"""Public copy says "checked" for what G6 ran and "challenged" for what an independent reviewer or a replay tried to
break; it never says that G6 verified work (the owner's ruling, 2026-11-05).

What it guards against: honest drift, a claim written in good faith by the owner, an agent or a contributor. It does
not guard against deliberate evasion: anyone can hide a word in an image, in CSS or behind look-alike characters, and
no reading of the text stops that.

The copy is read as a reader sees it: Markdown rendered by the site's own renderer (``pages.views.markdown_html``) or,
for the docs site, with the extensions site/mkdocs.yml names; a skill's front matter as G6 reads it
(``g6.core.skills.front_matter``); HTML read by an HTML parser, its visible text, the attributes and the structured-data
fields a reader or a search engine is shown, with only block-level elements ending a run of text. The reliability state
keeps its name: a code span is passed over when it holds a state, the three states listed together, a Python identifier
or dotted name, or code Python parses as code (`verified`, `verify_answer`, `verify(body, artifact)`); a code block is
code, but a diagram is read. The three states named together as one list pass, and so does the bare word in quotation
marks, which mentions it.

``LEFT`` holds the names the owner kept (2026-11-05): a failure mode's title and description, an interface's name, the
guide's Pattern 5, a field of study and an essay's title, a third party's task, an essay's heading, a registered
milestone. Not covered here: the research papers (``docs/theory``), the registered study records (``docs/validation``),
the design records (``docs/superpowers``), the release checklist (``docs/releases/PLAN_AUDIT.md``), the vendored skills,
the certificates and the generated repository graph. The essays may discuss verification as a field, but no sentence in
them may say G6 does it."""
from __future__ import annotations

import ast
import html
import json
import re
import shutil
import unicodedata
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import markdown
import pytest
import yaml
from django.template.loader import render_to_string
from django.test import RequestFactory

from g6.core.skills import front_matter
from pages import views
from pages.routes import PAGES

OSS = Path(__file__).resolve().parents[2]

#: One closed vocabulary for claims and quoted mentions. Counter is retained from the earlier tested vocabulary.
#: Identifiers keep the existing word boundary: digits and underscores are not English inflections.
VERIFICATION_ENDINGS = ('y', 'ies', 'ied', 'ying', 'ier', 'iers', 'iable', 'iably', 'iability',
                        'ication', 'ications', 'icatory')
VERIFICATION_PREFIXES = ('re', 'un', 'pre', 'non', 'self', 'auto', 'co', 'counter')
VERIFICATION_WORD = (rf"(?:(?:{'|'.join(VERIFICATION_PREFIXES)})-?)?"
                     rf"verif(?:{'|'.join(VERIFICATION_ENDINGS)})")
CLAIM = re.compile(rf"\b{VERIFICATION_WORD}\b", re.IGNORECASE)
STATE_NAMES = ("verified", "qualified-draft", "blocked-escalated")
_STATE = r"(?:verified|qualified-draft|blocked-escalated)"
_SEP = r"(?:\s*[,;/]\s*|\s+)(?:(?:or|and)\s+)?"
#: Three states in one list (a list is kept only when it names all three): the home page's chips, llms.txt, a note.
STATE_LIST = re.compile(rf"\b{_STATE}{_SEP}{_STATE}{_SEP}{_STATE}\b", re.IGNORECASE)


def _all_states(text: str) -> bool:
    return {state.lower() for state in re.findall(_STATE, text, re.IGNORECASE)} == set(STATE_NAMES)
#: The bare word in quotation marks, double or single, straight or curly, mentions it; a quoted phrase is read.
MENTION = re.compile(rf'(?:"\s*{VERIFICATION_WORD}[.,;:!?]?\s*"'
                     rf"|(?<!\w)'\s*{VERIFICATION_WORD}[.,;:!?]?\s*'(?!\w)"
                     rf"|\u201c\s*{VERIFICATION_WORD}[.,;:!?]?\s*\u201d"
                     rf"|\u2018\s*{VERIFICATION_WORD}[.,;:!?]?\s*\u2019)", re.IGNORECASE)
#: Names left as they are, each with the reason; the report lists where each one stands.
LEFT = {
    "work called verified when the evidence does not support it":
        "the title of the failure mode overstated_verification in src/g6/core/data/failure_modes/core.json",
    "labelled checked or verified beyond what its evidence shows": "the same failure mode's description, there",
    "verification plan": "the action gate's field (verification_plan, G6-INV-05)",
    "verification contract": "action_logic's control, which checks an action's verification plan",
    "verification-contract": "the same control's name, hyphenated before a noun",
    "verified learning event": "the ECHA spec's milestone (VLE-1) and the V4 study's registered question",
    "Self-Verification Impossibility": "the guide's Pattern 5, Model 16 of the Unified Theory",
    "formal verification": "the field of study (the About page) and an essay's title (the using-g6 lifecycle)",
    "fact-verification": "SciFact's task, a third party's name",
    "Move deliberately and verify things": "an essay's heading, quoted",
    "--no-verify": "git's flag that skips its hooks, an interface name (the coding agent gate's skill)",
    "verification-before-completion": "the vendored superpowers skill's name, a third party's (the using-g6 lifecycle)",
}

#: The Markdown and text a reader sees on GitHub and on the docs site, and the skills G6 installs.
MARKDOWN = sorted({
    *(OSS / name for name in ("README.md", "AGENT_GUIDE.md", "examples/README.md", "docs/AI_FOR_AI_WORKFLOW.md",
                              "docs/AI_FOR_AI_CONTINUATION.md", "docs/skills/using-g6/TESTING.md",
                              "docs/releases/launch.md", "docs/releases/v0.1.1.md", "docs/releases/OWNER_PILOT.md",
                              "docs/releases/RELEASE_TRIALS.md", "site/docs/llms.txt")),
    *(OSS / "site" / "docs").rglob("*.md"),
    *(OSS / "web" / "content").glob("*.md"),
    *(OSS / "src" / "g6" / "skills" / "using-g6").rglob("*.md"),
    *(path for path in (OSS / "src" / "g6").rglob("skill/SKILL.md") if "vendor" not in path.parts),
})
#: Elements that end a run of text: the HTML block-level elements, as Python-Markdown, the site's renderer, lists them.
#: Every other element (a, span, em, br, tspan) sits inside a line, and a line break is whitespace.
_BLOCK = frozenset(markdown.util.BLOCK_LEVEL_ELEMENTS)
#: HTML Living Standard, https://html.spec.whatwg.org/multipage/indices.html#attributes-3
#: Global title; image/area/input alternative text; input/textarea placeholders;
#: option/optgroup/track labels; th abbreviation. WAI-ARIA: author-supplied spoken/braille text
#: (https://www.w3.org/TR/wai-aria/#state_prop_def).
#: Empty tag tuples mean global attributes. This table drives both extraction and its planted inventory.
SHOWN_ATTRIBUTES = {
    'title': (), 'alt': ('img', 'area', 'input'), 'placeholder': ('input', 'textarea'),
    'label': ('option', 'optgroup', 'track'), 'abbr': ('th',),
    'aria-label': (), 'aria-description': (), 'aria-valuetext': (), 'aria-roledescription': (),
    'aria-placeholder': (), 'aria-braillelabel': (), 'aria-brailleroledescription': (),
}
#: HTML input states that display their value. Invalid/missing types use the standard's text default.
#: Passwords, non-textual controls and hidden inputs do not display value as copy.
INPUT_VALUE_STATES = {
    'text': True, 'search': True, 'tel': True, 'url': True, 'email': True, 'date': True, 'month': True,
    'week': True, 'time': True, 'datetime-local': True, 'number': True, 'button': True, 'submit': True, 'reset': True,
    'hidden': False, 'password': False, 'checkbox': False, 'radio': False, 'file': False,
    'color': False, 'range': False, 'image': False,
}
SHOWN_INPUT_TYPES = tuple(kind for kind, shown in INPUT_VALUE_STATES.items() if shown)
#: Every structured string is copy unless its own key names machine data. Objects beneath a URL-capable
#: field still have their children read: an image's caption or name is copy, its URL is an address.
_MACHINE_FIELDS = frozenset({"@context", "@type", "@id", "url", "image", "sameAs", "logo", "contentUrl",
                             "thumbnailUrl", "license", "datePublished", "dateModified", "dateCreated",
                             "identifier", "width", "height"})
#: The inventory is separate from extraction: a new field is read immediately, and needs classification here.
_COPY_FIELDS = frozenset({"headline", "description", "name", "alternativeHeadline", "abstract", "text",
                          "articleBody", "keywords", "jobTitle", "programmingLanguage", "caption"})
_OBJECT_FIELDS = frozenset({"author", "publisher", "mainEntityOfPage", "mainEntity", "acceptedAnswer", "@graph"})
#: JSON-LD 1.1, section 3's syntax keywords; @context is an opaque machine subtree, at every depth.
_JSONLD_KEYWORDS = frozenset({'@base', '@container', '@context', '@direction', '@graph', '@id', '@import',
                            '@included', '@index', '@json', '@language', '@list', '@nest', '@none', '@prefix',
                            '@propagate', '@protected', '@reverse', '@set', '@type', '@value', '@version', '@vocab'})
#: The meta tags a reader or a search engine is shown, by their name or property; an address tag is not one.
#: HTML standard metadata names, Open Graph text properties and Twitter card text fields. Addresses, tokens,
#: dimensions and presentation hints (theme-color, viewport, robots) are machine data, as before.
SHOWN_META_NAMES = frozenset({'application-name', 'description', 'author', 'keywords',
                             'og:title', 'og:description', 'og:site_name', 'og:image:alt',
                             'twitter:title', 'twitter:description', 'twitter:image:alt',
                             'twitter:label1', 'twitter:data1', 'twitter:label2', 'twitter:data2'})
#: Characters a reader never sees: the soft hyphen and the zero-width ones.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")
#: What an exempt code span becomes: a neutral word, so the words and the quotation marks around it stay as they were.
PLACEHOLDER = "CODE"


def _normalise(text: str) -> str:
    """The text as a reader reads it: compatibility forms folded (NFKC: a ligature is its letters), the characters
    nobody sees taken out, the marks on a letter taken off (a combining mark cannot split a word), whitespace
    collapsed."""
    # A discretionary wrap removes the hyphen AND its following newline/indentation, before whitespace folds.
    text = re.sub(r"\u00ad(?:(?:\r\n|[\n\r\v\f\x85\u2028\u2029])[ \t]*)?", "",
                  unicodedata.normalize("NFKC", text))
    text = _INVISIBLE.sub("", text)
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    return " ".join(unicodedata.normalize("NFC", text).split())


def _diagram(source: str) -> str:
    """A diagram's words as prose, as the drawn diagram shows them: Mermaid's own escapes (#quot;, #9829;) decoded and
    its emphasis marks (*, _, `) taken out; then a node labelled with a state's name alone names the state, and every
    other label is read, quotation marks and all."""
    source = re.sub(r"#(\d+|[A-Za-z]+);", lambda m: html.unescape(f"&{'#' if m.group(1).isdigit() else ''}"
                                                                  f"{m.group(1)};"), source)
    source = re.sub(r"[*_`]", "", source)
    source = re.sub(r'\["\s*(?:verified|qualified-draft|blocked-escalated)\s*"\]', " ", source)
    return source.replace('"', " ")


#: Python that prose cannot be: a call, an import, an attribute, a subscript, an assignment, a keyword, an ordering.
_CODE = (ast.Call, ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript, ast.Assign, ast.keyword)
_ORDER = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _is_name(code: str) -> bool:
    """A code span that is not prose: a state, the three states listed together, a Python identifier or dotted name
    (a field, a tool, a module), or code Python's own parser reads as code (``verify(body, artifact_bytes)``, an
    import line)."""
    code = _normalise(code)
    if code.lower() in STATE_NAMES or (STATE_LIST.fullmatch(code) and _all_states(code)):
        return True
    if code and all(part.isidentifier() for part in code.split(".")):
        return True
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    return any(isinstance(node, _CODE) or (isinstance(node, ast.Compare) and all(isinstance(op, _ORDER)
                                                                             for op in node.ops))
               for node in ast.walk(tree))


class _Span(str):
    """An inline code span's text, kept apart until the words around it are known."""


def _is_address(value: str) -> bool:
    """JSON-LD string values which are URLs or site paths, through the URL reader, wherever they occur."""
    if not value or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme or parts.netloc or value.startswith(('/', './', '../', '#')))


def _fields(value: object, key: str = ""):
    """All structured strings, except a scalar explicitly named as machine data, at every depth."""
    if key == '@context':
        return
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _fields(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from _fields(item, key)
    elif isinstance(value, str) and key not in _MACHINE_FIELDS and not _is_address(value):
        yield value


class _Reader(HTMLParser):
    """What a reader sees: each block's text, with inline markup joined into it, and each attribute and structured-data
    field a reader or a search engine is shown, as units of their own. Scripts and styles are not read (structured
    data is, through json), nor code blocks except diagrams."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[list[str]] = []
        self.run: list[str] = []
        self.hidden = 0                       # inside <script> or <style>
        self.pre: list[bool] = []             # the open <pre> blocks: True for a diagram
        self.code: list[str] | None = None    # an inline code span being read
        self.data: list[str] | None = None    # a structured-data script being read
        self.structured: list[object] = []
        self.json_errors: list[str] = []
        self.lead: list[str] | None = None
        self.continuations: list[tuple[str, list[str]]] = []
        self.links: list[str] = []
        self.site_names: set[str] = set()

    def end_run(self) -> None:
        if "".join(self.run).strip():
            prefix = self.continuations[-1][1] if self.continuations else []
            unit = [*prefix, ' ', *self.run] if prefix else self.run
            self.units.append(unit)
            self.lead = unit if ''.join(self.run).rstrip().endswith(':') else None
        self.run = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}     # html.parser gives attribute names in lower case
        name, prop = values.get("name", "").lower(), values.get("property", "").lower()
        self.units += [[values[shown]] for shown, tags in SHOWN_ATTRIBUTES.items()
                       if values.get(shown) and (not tags or tag in tags)]
        shown_value = INPUT_VALUE_STATES.get(values.get('type', 'text').lower(), INPUT_VALUE_STATES['text'])
        if tag == "input" and shown_value and values.get("value"):
            self.units.append([values["value"]])
        if tag == "meta" and values.get("content") and ({name, prop} & SHOWN_META_NAMES):
            self.units.append([values["content"]])                  # read by its key; an address key is not one
        classes = values.get("class", "").lower().split()
        if tag in ('a', 'area') and values.get('href'):
            self.links.append(values['href'])
        if tag == 'meta' and prop == 'og:site_name' and values.get('content'):
            self.site_names.add(values['content'])
        json_ld = False
        if tag == 'script':
            media = Message()
            media['content-type'] = values.get('type', '')
            json_ld = media.get_content_type() == 'application/ld+json'
        if tag == "script" and json_ld:
            self.data = []
        elif tag in ("script", "style"):
            self.hidden += 1
        elif tag == "pre":
            self.end_run()
            self.pre.append("mermaid" in classes)                   # the website's diagram
        elif tag == "code" and self.pre:
            self.pre[-1] = self.pre[-1] or "language-mermaid" in classes   # the docs site's mermaid fence
        elif tag == "code":
            self.code = []
        elif tag == "br":
            (self.code if self.code is not None else self.run).append("\n")    # a line break is whitespace
        elif tag in _BLOCK:
            self.end_run()
            if tag in ('ul', 'ol', 'dd'):
                inherited = self.continuations[-1][1] if self.continuations else []
                self.continuations.append((tag, self.lead or inherited))
                self.lead = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.data is not None:
            try:
                data = json.loads("".join(self.data), parse_constant=_invalid_json_constant)
            except ValueError as error:
                self.json_errors.append(str(error))
                data = None                                          # unreadable copy also fails the inventory
            self.structured.append(data)
            self.units += [[text] for text in _fields(data)]
            self.data = None
        elif tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag == "pre" and self.pre:
            self.pre.pop()
            self.end_run()
        elif tag == "code" and self.code is not None:
            self.run.append(_Span("".join(self.code)))
            self.code = None
        elif tag in _BLOCK:
            self.end_run()
            if self.continuations and self.continuations[-1][0] == tag:
                self.continuations.pop()
                self.lead = None

    def handle_data(self, data: str) -> None:
        if self.data is not None:
            self.data.append(data)
            return
        if self.hidden:
            return
        if self.code is not None:
            self.code.append(data)
        elif self.pre:
            if self.pre[-1]:
                self.run.append(_diagram(data))
        else:
            self.run.append(data)


def _invalid_json_constant(value: str):
    raise ValueError(f'Not a JSON constant: {value}')


def _reader(page: str) -> _Reader:
    reader = _Reader()
    reader.feed(page)
    reader.close()
    reader.end_run()
    return reader


def _units(page: str) -> list[list[str]]:
    return _reader(page).units


def structured_inventory(page: str) -> set[str]:
    """Fail on unreadable structured data or a field whose purpose has not been recorded."""
    reader = _reader(page)
    assert not reader.json_errors, reader.json_errors
    fields: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                fields.add(key)
                if key != '@context':
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for data in reader.structured:
        visit(data)
    unknown = fields - _COPY_FIELDS - _MACHINE_FIELDS - _OBJECT_FIELDS - _JSONLD_KEYWORDS
    assert not unknown, f"Unclassified JSON-LD fields: {sorted(unknown)}"
    return fields


def _resolve(unit: list[str], mark=lambda span: PLACEHOLDER) -> str:
    """A unit's text. A code span is exempt (``mark`` stands in for it) when it only names something and stands as a
    word of its own; inside a word, or as prose, it is read as the words it holds."""
    out: list[str] = []
    for index, part in enumerate(unit):
        if isinstance(part, _Span):
            before = _normalise("".join(out) + "|")[-2:-1]          # the visible character either side of it
            after = _normalise("|" + "".join(unit[index + 1:]))[1:2]
            in_word = bool(re.match(r"\w", before) or re.match(r"\w", after))
            part = mark(part) if _is_name(part) and not in_word else str(part)
        out.append(part)
    return "".join(out)


def _page_copy(page: str) -> str:
    """What a visitor reads on a page: its blocks and its shown attributes, one paragraph each."""
    return "\n\n".join(_resolve(unit) for unit in _units(page))


#: The docs site's Markdown extensions: MkDocs' own (toc, tables, fenced_code) and those site/mkdocs.yml adds, but
#: pymdownx, which is not a dependency here. Of those that change how indented text renders, admonition and
#: md_in_html are installed; pymdownx.details (a collapsible note, ???) and pymdownx.tabbed (a tab, === "Title") are
#: read as notes (!!!), with the same words; pymdownx.superfences' fences are read as Python-Markdown's, so a fence
#: inside an indented block is read as prose (more is read, nothing is hidden).
DOCS_SITE = ("toc", "tables", "fenced_code", "admonition", "attr_list", "md_in_html")
DOCS_SITE_PYMDOWNX = ("pymdownx.superfences", "pymdownx.tabbed", "pymdownx.details")


def docs_extensions(path: Path) -> set[str]:
    """Read the whole YAML list through YAML, including mapping entries and blank lines."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "MkDocs configuration must be a mapping"
    extensions = config.get("markdown_extensions")
    assert isinstance(extensions, list), "markdown_extensions must be a list"
    names: set[str] = set()
    for entry in extensions:
        if isinstance(entry, dict):
            assert len(entry) == 1, f"An extension entry needs one name: {entry}"
            name, options = next(iter(entry.items()))
            assert options is None or isinstance(options, dict), f"Malformed extension options: {entry}"
        else:
            name = entry
        assert isinstance(name, str) and name, f"Malformed extension name: {name}"
        names.add(name)
    return names


def _docs_site_html(text: str) -> str:
    text = re.sub(r"^([ \t]*)\?\?\?\+?(?=[ \t]+\S)", r"\1!!!", text, flags=re.MULTILINE)        # a collapsible note
    text = re.sub(r'^([ \t]*)===[+!]*(?=[ \t]+")', r"\1!!! tab", text, flags=re.MULTILINE)     # a tab
    return markdown.markdown(text, extensions=DOCS_SITE)


def _markdown_copy(text: str, *, docs_site: bool = False) -> str:
    """What a reader reads in Markdown: the front matter's values as G6 reads them, then the page as it is rendered,
    by MkDocs for the docs site and by the website's own renderer for everything else. Words the site holds back
    until extensions.SHOW_PLANNED is on are read too: GitHub shows them."""
    text = views._PLANNED.sub(lambda m: m.group(1), text)
    page = _docs_site_html(text) if docs_site else views.markdown_html(text)
    return "\n\n".join([*front_matter(text).values(), _page_copy(page)])


def sentences(text: str) -> list[str]:
    """End only at a certain terminal boundary; uncertain abbreviations/initials keep the subject with its claim.

    Terminal punctuation may have closing quotes/brackets before whitespace, followed by a capital or an opening
    quote. A single letter, dotted token or capitalised token of up to three letters may be an abbreviation.
    This intentionally refuses the ambiguous 'G6 checks C. Other tools verify it.' (owner, repair 1).
    """
    text = _normalise(text)
    boundaries = []
    for match in re.finditer(r'''(?P<token>[\w.]+)[.!?]["'”’\)\]\}]*\s+(?=(?P<next>[^\W\d_]|["'“‘]))''', text):
        token, following = match.group('token', 'next')
        uncertain = ('.' in token or len(token) == 1
                     or (token.isalpha() and token[0].isupper() and len(token) <= 3))
        if not uncertain and (following.isupper() or following in '\"\'“‘'):
            boundaries.append(match.end())
    return [text[start:end].strip() for start, end in zip([0, *boundaries], [*boundaries, len(text)])
            if text[start:end].strip()]


def product_names() -> tuple[str, ...]:
    """Identity names from the templates as rendered with site settings, rather than a second spelling list."""
    names = set()
    for template in ('base.html', 'blog/base_post.html'):
        reader = _reader(render_to_string(template, request=RequestFactory().get('/')))
        assert not reader.json_errors, reader.json_errors
        names.update(reader.site_names)
        for data in reader.structured:
            if isinstance(data, dict):
                publisher = data.get('publisher', {})
                if isinstance(publisher, dict) and publisher.get('name'):
                    names.add(publisher['name'])
    assert names, 'No site identity names'
    return tuple(sorted(names))


PRODUCT_NAMES = product_names()


def exported_pages() -> dict[str, str]:
    """The static builder's actual HTML export list, including auxiliary pages (not just routed pages)."""
    from pages.management.commands.build_static import build

    workspace = (OSS / '.build').resolve()
    folder = (workspace / ('honesty-export-' + uuid4().hex)).resolve()
    assert folder.is_relative_to(workspace)
    folder.mkdir(parents=True)
    try:
        problems = build(folder)
        assert not problems, problems
        return {path.relative_to(folder).as_posix(): path.read_text(encoding='utf-8')
                for path in sorted(folder.rglob('*.html'))}
    finally:
        assert folder.resolve().is_relative_to(workspace) and not folder.is_symlink()
        shutil.rmtree(folder)


def _excluded_document(path: Path) -> bool:
    """Only the already documented scope exclusions; this is not a claim exemption."""
    rel = path.relative_to(OSS).as_posix()
    return (rel.startswith(('docs/theory/', 'docs/validation/', 'docs/superpowers/'))
            or rel == 'docs/releases/PLAN_AUDIT.md' or 'vendor' in path.relative_to(OSS).parts
            or 'certificate' in path.relative_to(OSS).parts)


def public_documents(seeds) -> list[Path]:
    """Transitive public-document links, parsed as Markdown/HTML, constrained to the repository and old exclusions."""
    seen, pending = set(seeds), list(seeds)
    while pending:
        path = pending.pop()
        # Read Markdown links before the site's URL rewrite; the docs renderer uses the same Markdown reader.
        page = _docs_site_html(views._PLANNED.sub(lambda m: m.group(1), path.read_text(encoding='utf-8')))
        for link in _reader(page).links:
            address = urlsplit(link)
            # Public repository links use the site's own canonical source prefix (no network access).
            repository = views.evidence.REPO
            if link.startswith(repository):
                address = urlsplit('/' + link[len(repository):])
            if address.scheme or address.netloc or not address.path:
                continue
            target = ((OSS if address.path.startswith('/') else path.parent)
                      / unquote(address.path).lstrip('/')).resolve()
            if not target.is_relative_to(OSS) or target.suffix.lower() not in {'.md', '.txt'}:
                continue
            if target in seen or _excluded_document(target) or not target.is_file():
                continue
            seen.add(target)
            pending.append(target)
    return sorted(seen)


MARKDOWN = public_documents(MARKDOWN)


def claims(copy: str) -> list[str]:
    """The single claim decision, after the owner's named mentions and reliability states are resolved."""
    copy = _normalise(copy)
    for name in LEFT:
        copy = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", " ", copy, flags=re.IGNORECASE)
    copy = MENTION.sub(" ", copy)
    copy = STATE_LIST.sub(lambda m: " " if _all_states(m.group(0)) else m.group(0), copy)
    return [sentence[max(0, m.start() - 60):m.end() + 60]
            for sentence in sentences(copy) for m in CLAIM.finditer(sentence)]


def g6_claims(page: str) -> list[str]:
    """Each sentence of a page that names G6, in any case and in a code span too, and makes a claim form."""
    found = []
    for unit in _units(page):
        spans: dict[str, str] = {}

        def mark(span: str, spans: dict[str, str] = spans) -> str:
            key = f"\x00{len(spans)}\x00"
            spans[key] = str(span)
            return key

        for sentence in sentences(_resolve(unit, mark)):
            read = sentence
            for key, value in spans.items():
                read, sentence = read.replace(key, value), sentence.replace(key, PLACEHOLDER)
            names = '|'.join(re.escape(_normalise(name)) for name in PRODUCT_NAMES)
            if re.search(rf"\b(?:{names})\b", _normalise(read), re.IGNORECASE) and claims(sentence):
                found.append(_normalise(read))
    return found


def test_the_check_refuses_the_claim_and_allows_the_state_in_code_format():
    # the instrument itself: a claim is found in prose and in a page, and each allowed form passes
    assert claims("Each change is verified in its own worktree.")
    assert claims("G6 verifies the patch") and claims("Verification: does it work?") and claims("we verify it")
    assert claims(_page_copy('<p class="x">The result is <strong>verified</strong>.</p>'))
    assert claims(_page_copy('<meta name="description" content="Every answer verified.">'))
    assert claims(_page_copy('<meta property="og:description" content="Every answer verified.">'))
    assert claims(_page_copy('<img alt="A verified answer"><a title="Verified">x</a><svg aria-label="verified"/>'))
    assert claims(_markdown_copy("A [link](x.md) to a verified fix.\n"))
    assert claims(_markdown_copy("---\nname: x\ndescription: Use when a patch must be verified.\n---\n\n# X\n"))
    assert not claims(_markdown_copy("The state is `verified`, `qualified-draft` or `blocked-escalated`.\n"))
    assert not claims(_markdown_copy("Call `coding_agent_gate.verify_answer` with a `verifier`.\n"))
    # a code span Python reads as code is code; one it reads as words is prose
    assert not claims(_markdown_copy("`verify(body, artifact_bytes)` names it; `from g6.evidence import verify`.\n"))
    for prose in ("`patch is verified`", "`verified and complete`", "`not verified`", "`G6 verified it`"):
        assert claims(_markdown_copy(f"The patch: {prose}.\n")), prose
    assert not claims(_markdown_copy("```python\nassert verify(body) == []\n```\n"))
    # a diagram is read: its caption and its labels, quoted or not; a node labelled with the state alone names it
    assert claims(_markdown_copy("```mermaid A caption\nflowchart TD\n  a --> b[\"verify it\"]\n```\n"))
    assert claims(_markdown_copy("```mermaid Only a pass reaches verified.\nflowchart TD\n  a --> b\n```\n"))
    assert claims(_page_copy('<pre class="mermaid">a --&gt; b[&quot;verify it&quot;]</pre>'))
    assert not claims(_markdown_copy("```mermaid A caption\nflowchart TD\n  a --> ok[\"verified\"]\n```\n"))
    assert not claims(_page_copy('<pre class="mermaid">a --&gt; ok[&quot;verified&quot;]:::approve</pre>'))
    assert not claims(_page_copy("<p>Every result is <code>verified</code>.</p>"))
    assert not claims('Never summarise it as "done" or "verified".')
    assert not claims("Every result is verified, qualified-draft or blocked-escalated.")
    assert not claims(_page_copy('<span class="state">verified</span> <span class="state">qualified-draft</span> '
                                 '<span class="state">blocked-escalated</span>'))
    assert not claims("work called verified when the evidence does not support it")
    assert not claims(_page_copy('<meta property="og:url" content="https://g6solver.com/essays/verify-it/">'))
    assert not claims(_page_copy("<script>verify()</script><style>.verified{}</style><p>Checked.</p>"))
    # a LEFT phrase is left only whole; an exempt code span stands in as a word, keeping the words around it
    assert not claims("The field of formal verification.") and claims("A non-formal verification of it.")
    assert not claims(_page_copy('<p>Never call it "<code>verified</code>".</p>'))
    assert _page_copy("<p>The <code>verify_answer</code> op.</p>") == f"The {PLACEHOLDER} op."
    assert g6_claims("<p><code>G6</code> checks it.</p>") == [] and g6_claims("<p>G\u200b6 verifies it.</p>")
    # a word is read as it looks: a combining mark does not split it, and a name keeps its letters
    assert claims(_page_copy("<p>G6 produces v\u0301erified answers.</p>")) and not claims("G\u00f6del's theorem.")
    # structured data is read by its reader-facing fields, button values and placeholders are read, and so is a word
    # the site holds back for now
    assert claims(_page_copy('<script type="application/ld+json">{"a": {"description": "G6 verifies it."}}</script>'))
    assert not claims(_page_copy('<script type="application/ld+json">{"a": "G6 "verifies" it"}</script>'))  # unreadable
    assert claims(_page_copy('<script type="application/ld+json">{"keywords": "verification"}</script>'))
    assert not claims(_page_copy('<meta property="og:url" content="G6 verifies it">'))     # an address tag, by its key
    assert claims(_page_copy('<input type="submit" value="Verify">')) and claims(_page_copy('<input placeholder="Verify">'))
    assert claims(_markdown_copy("Text. <!-- planned -->G6 verifies it.<!-- /planned -->\n"))
    # a diagram is read as drawn: Mermaid's escapes decoded, its emphasis marks gone
    assert claims(_markdown_copy('```mermaid A caption\nflowchart TD\n  a["G6 veri**fies** it"]\n```\n'))
    assert claims(_markdown_copy('```mermaid A caption\nflowchart TD\n  a["#118;erified it"]\n```\n'))


def test_the_essay_check_reads_sentences_not_the_page():
    assert g6_claims("<p>The field. G6 verifies every answer.</p>") == ["G6 verifies every answer."]
    assert g6_claims("<p>G6 checks it. Formal methods verify programs.</p>") == []
    # each attribute is a sentence of its own: the logo's label does not join a figure's
    assert g6_claims('<a aria-label="G6, home"></a><svg aria-label="A pipeline with a verification gap"></svg>') == []
    # each block is read on its own: a heading does not join the paragraph after it
    assert g6_claims("<h2>G6</h2><p>Formal methods verify programs.</p>") == []


# Review 1 of the check (2026-11-05): six ways past it, each read here as a reader reads it.
def test_a_word_split_by_markup_is_read_whole():
    assert claims(_markdown_copy("G6 veri**fies** every patch."))
    assert claims(_page_copy("<p>G6 veri<em>fies</em> every patch.</p>"))


def test_quotation_marks_exempt_a_mention_of_the_word_not_a_claim_in_them():
    assert not claims(_markdown_copy('Never summarise it as "done" or "verified".'))
    assert claims(_markdown_copy('G6 produces "verified answers" for every request.'))


def test_the_state_list_needs_all_three_states():
    assert not claims("Every result is verified, qualified-draft or blocked-escalated.")
    assert claims("G6 produces verified, qualified-draft answers.")


def test_an_attribute_is_read_whatever_its_quotes():
    assert claims(_page_copy("<meta name='description' content='G6 verifies every patch.'>"))


def test_code_format_exempts_a_state_or_an_identifier_not_prose():
    assert not claims(_markdown_copy("The state is `verified`."))
    assert claims(_markdown_copy("G6 `verifies every patch` before it ships."))


def test_the_essay_check_reads_g6_in_any_case():
    assert g6_claims("<p>g6 verifies every answer.</p>")


# Review 2 of the check (2026-11-05): five more, each closed once, at the text every source becomes.
def test_a_left_phrase_is_a_whole_phrase_not_part_of_another_word():
    assert claims(_markdown_copy("G6 performs informal verification of every answer."))


@pytest.mark.parametrize("source, essay", [('G6 produces "verified `answers`" for every request.', False),
                                           ("G6 veri`fies` every patch.", False),
                                           ("`G6` verifies every answer.", True)])
def test_an_exempt_code_span_keeps_the_words_around_it(source, essay):
    page = views.markdown_html(source)
    assert "<code>" in page
    assert g6_claims(page) if essay else claims(_page_copy(page))


def test_attribute_values_that_name_a_meta_are_read_in_any_case():
    assert claims(_page_copy('<meta name="DESCRIPTION" content="G6 verifies every answer.">'))
    assert claims(_page_copy('<meta property="OG:Description" content="G6 verifies every answer.">'))


def test_a_diagram_on_the_docs_site_is_read():
    assert claims(_markdown_copy('```mermaid\nflowchart TD\n  a["G6 verifies every patch"]\n```\n', docs_site=True))


def test_soft_hyphens_and_zero_width_characters_do_not_split_a_word():
    for hidden in ("&shy;", "\u200b", "\u200d", "\u2060", "\ufeff"):
        assert claims(_page_copy(f"<p>G6 produces veri{hidden}fied answers.</p>")), repr(hidden)
    assert claims(_page_copy("<p>G6 produces veri\ufb01ed answers.</p>"))       # a ligature, read as the letters


# Review 3 of the check (2026-11-05): the rest of the class, read as a reader reads honest copy.
def test_a_code_span_is_exempt_only_as_a_state_a_name_or_python_code():
    assert claims(_markdown_copy("G6 outputs are `verified!`"))
    assert not claims(_markdown_copy("`verified`, `coding_agent_gate.verify_answer` and `verify(body, artifact)`."))


def test_an_inline_element_keeps_a_sentence_and_a_word_whole():
    assert g6_claims("<p>G6<br>verifies every answer.</p>")
    assert claims(_page_copy("<svg><text>G6 veri<tspan>fies</tspan> every patch.</text></svg>"))
    assert g6_claims("<p><a href='/x'>G6</a> <span>verifies</span> every answer.</p>")


def test_a_name_in_another_alphabet_is_not_refused():
    assert claims(_markdown_copy("G6 is used in Hawai\u02bbi.")) == []


def test_ascii_single_quotes_mark_a_mention_as_curly_ones_do():
    assert claims("Avoid the word \u2018verified\u2019 in public copy.") == []
    assert claims(_markdown_copy("Avoid the word 'verified' in public copy.")) == []
    assert claims(_markdown_copy("G6 gives 'verified answers' every time."))
    assert claims("G6's verified answers' quality.")                     # an apostrophe is not a quotation mark


def test_a_state_only_diagram_label_is_exempt_after_its_emphasis_is_taken_out():
    assert claims(_markdown_copy('```mermaid\nflowchart TD\n  a["`**verified**`"]\n```\n')) == []


def test_structured_data_is_read_by_field_whatever_its_text_starts_with():
    data = '{"@type": "WebPage", "description": "/ G6 verifies every patch.", "url": "/verified/"}'
    assert claims(_page_copy(f'<script type="application/ld+json">{data}</script>'))
    nested = '{"author": {"name": "G6 verified"}, "image": {"name": "verified.png"}, "sameAs": ["/verify"]}'
    assert len(claims(_page_copy(f'<script type="application/ld+json">{nested}</script>'))) == 2


def test_a_docs_site_tab_is_read_as_prose():
    assert claims(_markdown_copy('=== "Result"\n\n    G6 verifies every patch.\n', docs_site=True))


def test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names():
    named = docs_extensions(OSS / "site" / "mkdocs.yml")
    # every extension the docs site names is either rendered here or read as this test says (DOCS_SITE_PYMDOWNX)
    assert named == (set(DOCS_SITE) - {"tables", "fenced_code"}) | set(DOCS_SITE_PYMDOWNX), named
    # a collapsible note's and a tab's words are read, not taken for an indented code block
    assert claims(_markdown_copy('??? question "Why"\n    G6 verifies it.\n', docs_site=True))
    assert claims(_markdown_copy('===+ "One"\n    G6 verifies it.\n', docs_site=True))
    # a setext heading's underline is not a tab
    assert "<h1" in _docs_site_html("A title\n===\n")


def _file_copy(path: Path) -> str:
    return _markdown_copy(path.read_text(encoding="utf-8"), docs_site=(OSS / "site" / "docs") in path.parents)


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.relative_to(OSS).as_posix())
def test_the_markdown_a_reader_sees_never_says_g6_verified_work(path):
    assert claims(_file_copy(path)) == [], path.relative_to(OSS).as_posix()


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace != "blog"])
def test_no_page_of_the_site_says_g6_verified_work(client, path):
    assert claims(_page_copy(client.get(path).content.decode())) == [], path


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace == "blog"])
def test_no_essay_says_that_g6_verifies(client, path):
    assert g6_claims(client.get(path).content.decode()) == [], path
