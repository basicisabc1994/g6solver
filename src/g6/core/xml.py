# SPDX-License-Identifier: Apache-2.0
"""Bounded XML with no DTD or entities, including in non-UTF-8 encodings."""
from pathlib import Path
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

MAX_XML_BYTES = 8 * 1024 * 1024


def parse_xml(data: bytes, *, limit: int = MAX_XML_BYTES, svg_public_doctype: bool = False) -> Element:
    if len(data) > limit:
        raise ValueError("XML exceeds byte limit")
    builder, parser = TreeBuilder(), expat.ParserCreate(namespace_separator="}")
    depth = count = 0

    def refuse(*args):
        raise ValueError("XML DOCTYPE and entities are refused")

    def doctype(name, system, public, internal):
        if not (svg_public_doctype and name == "svg" and not internal
                and system == "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"
                and public == "-//W3C//DTD SVG 1.1//EN"):
            refuse()

    def start(name, attrs):
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > 128 or count > 100_000:
            raise ValueError("XML exceeds structure limit")
        builder.start("{" + name if "}" in name else name,
                      {("{" + key if "}" in key else key): value for key, value in attrs.items()})

    def end(name):
        nonlocal depth
        builder.end("{" + name if "}" in name else name)
        depth -= 1

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
        return builder.close()
    except (expat.ExpatError, LookupError, UnicodeError) as exc:
        raise ValueError("unreadable XML") from exc


def read_xml(path: Path, *, limit: int = MAX_XML_BYTES) -> Element:
    with path.open("rb") as stream:
        return parse_xml(stream.read(limit + 1), limit=limit)
