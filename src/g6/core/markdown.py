# SPDX-License-Identifier: Apache-2.0
"""One CommonMark reader. Source spans are line based; code content is parser-dedented."""
from __future__ import annotations

from dataclasses import dataclass
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass(frozen=True)
class CodeBlock:
    info: str
    content: str
    start: int
    end: int
    closed: bool


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    start: int
    end: int
    line: int
    stop: int


class Document:
    def __init__(self, text: str):
        self.text = text
        parts = re.split(r"(\r\n|\r|\n)", text)
        self.lines = [parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
                      for i in range(0, len(parts), 2) if parts[i] or i + 1 < len(parts)]
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line))
        reader = MarkdownIt("commonmark")
        self.tokens = reader.parse(text)
        # The parser stops reading a container's content at its nesting limit and drops it silently (a quote 20
        # deep). Every reader of a document that went unread must fail closed on ``truncated``, never on its tokens.
        limit = reader.options["maxNesting"]
        self.truncated = any(t.type in ("blockquote_open", "list_item_open") and t.level >= limit - 1
                             for t in self.tokens)

    def span(self, token: Token) -> tuple[int, int]:
        start, end = token.map or (0, 0)
        return self.offsets[start], self.offsets[min(end, len(self.lines))]

    @property
    def code_blocks(self) -> list[CodeBlock]:
        out = []
        for token in self.tokens:
            if token.type not in ("fence", "code_block"):
                continue
            start, end = self.span(token)
            first, stop = token.map
            closed = token.type != "fence" or stop - first >= token.content.count("\n") + 2
            out.append(CodeBlock(token.info.strip(), token.content, start, end, closed))
        return out

    @property
    def headings(self) -> list[Heading]:
        return [Heading(int(t.tag[1:]), self.tokens[i + 1].content, *self.span(t), *t.map)
                for i, t in enumerate(self.tokens) if t.type == "heading_open"]

    def section(self, title: str, level: int = 2, *, include_heading: bool = False) -> str | None:
        heads = self.headings
        matches = [h for h in heads if h.level == level and h.title == title]
        if len(matches) != 1:
            return None
        head = matches[0]
        end = next((h.start for h in heads if h.start > head.start and h.level <= level), len(self.text))
        return self.text[head.start if include_heading else head.end:end]

    def list_code_names(self) -> list[str]:
        """The leading inline code name in each real list item's first paragraph."""
        out = []
        for i, token in enumerate(self.tokens):
            if token.type != "list_item_open":
                continue
            inline = next((t for t in self.tokens[i + 1:] if t.type in ("inline", "list_item_close")), None)
            if inline and inline.type == "inline" and inline.children and inline.children[0].type == "code_inline":
                out.append(inline.children[0].content)
        return out

    def prose(self, *, inline_code: bool = True) -> str:
        """Visible inline text, with inline code delimited for protocol-name readers; no code blocks/HTML."""
        return "\n".join("".join((f"`{c.content}`" if inline_code else " ") if c.type == "code_inline"
                                  else "\n" if c.type in ("softbreak", "hardbreak") else c.content
                                  for c in t.children or [] if c.type != "html_inline")
                         for t in self.tokens if t.type == "inline")


def parse(text: str) -> Document:
    return Document(text)
