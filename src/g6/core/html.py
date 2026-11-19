# SPDX-License-Identifier: Apache-2.0
"""HTML structure needed by SVG page inspection; no script/comment tag matching."""
from html import escape
from html.parser import HTMLParser


class _SVG(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "svg" or self.depth:
            self.depth += 1
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            self.parts.append(f"</{tag}>")
            self.depth -= 1
            if not self.depth:
                self.blocks.append("".join(self.parts))
                self.parts.clear()

    def handle_data(self, data):
        if self.depth:
            self.parts.append(escape(data))


def svg_blocks(html: str) -> list[str]:
    reader = _SVG()
    reader.feed(html)
    reader.close()
    return reader.blocks
