# SPDX-License-Identifier: Apache-2.0
"""A licence table: what using a dependency under one licence means for a project released under another.

A table lookup, never a legal opinion. :func:`spdx` reads a declared licence (an SPDX identifier, a common spelling
such as "Apache 2.0", a PyPI classifier, or an SPDX expression with OR, AND, WITH and parentheses) into SPDX
identifiers; whatever it does not recognise is ``unknown``, never guessed ("BSD" alone is unknown: it does not say
which BSD; nor does "Apache Software License" say which Apache licence). An expression is read in full or not at all:
one whose every word is not used, or whose parentheses do not match, is ``unknown``. :func:`check` looks a
dependency's licence up against the project's licence and answers one of:

* ``compatible``: the table has no condition beyond keeping the dependency's notices;
* ``conditions``: allowed with conditions the table names (a weak copyleft licence's files stay under it; a library
  under the LGPL must stay replaceable; Apache-2.0 in a GPL-2.0-or-later project needs the combination released under
  GPL-3.0; MPL-2.0 joins a GPL project through its secondary licences unless its files refuse them);
* ``incompatible``: the table says the combination cannot be released under the project's licence (a GPL dependency
  in a permissive project, GPL-2.0-only with GPL-3.0, a non-commercial licence, Apache-2.0 or CC-BY-SA-4.0 in a
  GPL-2.0-only project);
* ``unknown``: either licence is not in the table, the expression does not parse, or the table cannot tell (an
  EPL-2.0 dependency in a GPL project depends on a designation the table cannot see).

For an expression, ``A OR B`` takes the better of the two (the project may choose), ``A AND B`` the worse. Every
answer names the table row it came from. What a licence's text allows in a given case is for a person who reads it.
"""
from __future__ import annotations

import re
from collections.abc import Callable

TABLE = "the g6 licence table (2026-10-21)"
VERDICTS = ("compatible", "conditions", "unknown", "incompatible")
PERMISSIVE = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense", "Zlib",
                        "PSF-2.0", "CC0-1.0", "BSL-1.0", "CC-BY-4.0", "Python-2.0"})
WEAK = frozenset({"MPL-2.0", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "EPL-2.0",
                  "CC-BY-SA-4.0"})
GPL = frozenset({"GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later"})
AGPL = frozenset({"AGPL-3.0-only", "AGPL-3.0-or-later"})
NON_COMMERCIAL = frozenset({"CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0"})
PROPRIETARY = "LicenseRef-Proprietary"
KNOWN = PERMISSIVE | WEAK | GPL | AGPL | NON_COMMERCIAL | {PROPRIETARY}
_SPELLINGS = {
    "mit": "MIT", "mit license": "MIT", "the mit license": "MIT", "expat": "MIT",
    "apache-2.0": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache2": "Apache-2.0", "apache-2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0", "apache license, version 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0", "apache 2": "Apache-2.0",
    "bsd-3-clause": "BSD-3-Clause", "bsd 3-clause": "BSD-3-Clause", "new bsd": "BSD-3-Clause", "modified bsd":
    "BSD-3-Clause", "bsd-3": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause", "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause", "freebsd": "BSD-2-Clause", "isc": "ISC", "isc license": "ISC", "0bsd": "0BSD",
    "unlicense": "Unlicense", "the unlicense": "Unlicense", "zlib": "Zlib", "psf": "PSF-2.0", "psf-2.0": "PSF-2.0",
    "python software foundation license": "PSF-2.0", "cc0-1.0": "CC0-1.0", "cc0": "CC0-1.0", "bsl-1.0": "BSL-1.0",
    "boost software license 1.0": "BSL-1.0", "cc-by-4.0": "CC-BY-4.0", "mpl-2.0": "MPL-2.0", "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0", "epl-2.0": "EPL-2.0", "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "lgpl-2.1": "LGPL-2.1-only", "lgpl-2.1-only": "LGPL-2.1-only", "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "lgpl-2.1+": "LGPL-2.1-or-later", "lgplv2.1": "LGPL-2.1-only", "lgpl-3.0": "LGPL-3.0-only", "lgpl-3.0-only":
    "LGPL-3.0-only", "lgpl-3.0-or-later": "LGPL-3.0-or-later", "lgpl-3.0+": "LGPL-3.0-or-later", "lgplv3":
    "LGPL-3.0-only", "lgpl3": "LGPL-3.0-only",
    "gpl-2.0": "GPL-2.0-only", "gpl-2.0-only": "GPL-2.0-only", "gplv2": "GPL-2.0-only", "gpl2": "GPL-2.0-only",
    "gpl-2.0-or-later": "GPL-2.0-or-later", "gpl-2.0+": "GPL-2.0-or-later", "gplv2+": "GPL-2.0-or-later",
    "gpl-3.0": "GPL-3.0-only", "gpl-3.0-only": "GPL-3.0-only", "gplv3": "GPL-3.0-only", "gpl3": "GPL-3.0-only",
    "gpl-3.0-or-later": "GPL-3.0-or-later", "gpl-3.0+": "GPL-3.0-or-later", "gplv3+": "GPL-3.0-or-later",
    "agpl-3.0": "AGPL-3.0-only", "agpl-3.0-only": "AGPL-3.0-only", "agplv3": "AGPL-3.0-only",
    "agpl-3.0-or-later": "AGPL-3.0-or-later", "agpl-3.0+": "AGPL-3.0-or-later",
    "cc-by-nc-4.0": "CC-BY-NC-4.0", "cc-by-nc-sa-4.0": "CC-BY-NC-SA-4.0", "cc-by-nc-nd-4.0": "CC-BY-NC-ND-4.0",
    "proprietary": PROPRIETARY, "licenseref-proprietary": PROPRIETARY, "closed source": PROPRIETARY,
}
_CLASSIFIERS = {
    "MIT License": "MIT", "ISC License (ISCL)": "ISC",
    "The Unlicense (Unlicense)": "Unlicense", "zlib/libpng License": "Zlib",
    "Python Software Foundation License": "PSF-2.0", "Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
    "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    "Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
}
_TOKEN = re.compile(r"\(|\)|[^\s()]+")
_OPERATORS = ("AND", "OR", "WITH")
_ONLY2, _LATER2, _ONLY3, _LATER3 = "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later"
#: (dependency, project) within the GPL family.
_GPL_ROWS: dict[tuple[str, str], tuple[str, str]] = {
    (_ONLY2, _ONLY2): ("compatible", "both are GPL-2.0-only"),
    (_ONLY2, _LATER2): ("conditions", "the combined work is released under GPL-2.0-only"),
    (_ONLY2, _ONLY3): ("incompatible", "GPL-2.0-only and GPL-3.0 cannot be combined"),
    (_ONLY2, _LATER3): ("incompatible", "GPL-2.0-only and GPL-3.0 cannot be combined"),
    (_LATER2, _ONLY2): ("compatible", "the dependency may be used under GPL-2.0"),
    (_LATER2, _LATER2): ("compatible", "both are GPL-2.0 or later"),
    (_LATER2, _ONLY3): ("compatible", "the dependency may be used under GPL-3.0"),
    (_LATER2, _LATER3): ("compatible", "the dependency may be used under GPL-3.0 or later"),
    (_ONLY3, _ONLY2): ("incompatible", "GPL-3.0 code cannot be released under GPL-2.0-only"),
    (_ONLY3, _LATER2): ("conditions", "the combined work is released under GPL-3.0"),
    (_ONLY3, _ONLY3): ("compatible", "both are GPL-3.0-only"),
    (_ONLY3, _LATER3): ("conditions", "the combined work is released under GPL-3.0-only"),
    (_LATER3, _ONLY2): ("incompatible", "GPL-3.0 code cannot be released under GPL-2.0-only"),
    (_LATER3, _LATER2): ("conditions", "the combined work is released under GPL-3.0 or later"),
    (_LATER3, _ONLY3): ("compatible", "the dependency may be used under GPL-3.0"),
    (_LATER3, _LATER3): ("compatible", "both are GPL-3.0 or later"),
}


def one(text: str) -> str:
    """One licence name as its SPDX identifier, or ``unknown``."""
    raw = " ".join(text.split()).strip()
    if raw.startswith("License ::"):
        raw = raw.rsplit("::", 1)[-1].strip()
        return _CLASSIFIERS.get(raw, "unknown")
    found = {k.lower(): k for k in KNOWN}.get(raw.lower())
    return found or _SPELLINGS.get(raw.lower(), "unknown")


Tree = str | tuple[str, list["Tree"]]


def _tree(tokens: list[str]) -> Tree:
    """An SPDX expression's tokens as a tree: a licence identifier, or ``("AND" | "OR", [parts])``. A licence ``WITH``
    an exception is the licence (an exception adds permissions). ``ValueError`` unless every token is used and the
    parentheses match."""
    pos = 0

    def peek() -> str:
        return tokens[pos].upper() if pos < len(tokens) else ""

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("the expression ends where a licence was expected")
        pos += 1
        return tokens[pos - 1]

    def licence() -> Tree:
        token = take()
        if token == "(":
            inner = either()
            if take() != ")":
                raise ValueError("a parenthesis is not closed")
            return inner
        if token == ")" or token.upper() in _OPERATORS:
            raise ValueError(f"{token!r} where a licence was expected")
        if peek() == "WITH":
            take()
            exception = take()
            if exception in ("(", ")") or exception.upper() in _OPERATORS:
                raise ValueError("WITH is not followed by an exception")
        return token

    def joined(word: str, part: Callable[[], Tree]) -> Tree:
        found = [part()]
        while peek() == word:
            take()
            found.append(part())
        return found[0] if len(found) == 1 else (word, found)

    def both() -> Tree:
        return joined("AND", licence)

    def either() -> Tree:
        return joined("OR", both)

    tree = either()
    if pos != len(tokens):
        raise ValueError(f"{tokens[pos]!r} is left over: the expression does not parse")
    return tree


def spdx(declared: str) -> str:
    """A declared licence as an SPDX identifier or expression, or ``unknown`` (an expression that does not parse in
    full included). A list of classifiers joined by ``|`` reads as AND (every classifier applies)."""
    text = " ".join((declared or "").split())
    if not text or text.upper() in ("NOASSERTION", "NONE", "OTHER", "UNKNOWN", "UNLICENSED"):
        return "unknown"
    if "|" in text:
        parts = [spdx(p) for p in text.split("|") if p.strip()]
        return " AND ".join(parts) if parts and "unknown" not in parts else "unknown"
    if one(text) != "unknown":
        return one(text)
    tokens = _TOKEN.findall(text)
    if any(t.upper() in ("OR", "AND", "WITH") for t in tokens):
        out: list[str] = []
        for t in tokens:
            if t in "()" or t.upper() in ("OR", "AND", "WITH"):
                out.append(t.upper() if t not in "()" else t)
            elif out and out[-1] == "WITH":
                out.append(t)  # an exception's identifier, kept as given
            else:
                mapped = one(t)
                if mapped == "unknown":
                    return "unknown"
                out.append(mapped)
        try:
            _tree(out)
        except ValueError:
            return "unknown"
        return " ".join(out).replace("( ", "(").replace(" )", ")")
    return "unknown"


def _pair(dep: str, project: str) -> tuple[str, str]:
    """The table's row for one dependency licence in one project licence: ``(verdict, why)``."""
    if dep == "unknown" or dep not in KNOWN:
        return "unknown", f"the dependency's licence ({dep}) is not in the table"
    if project == "unknown" or project not in KNOWN:
        return "unknown", f"the project's licence ({project}) is not in the table"
    if dep in NON_COMMERCIAL:
        return "incompatible", f"{dep} allows non-commercial use only"
    if dep == PROPRIETARY:
        return "unknown", "a proprietary dependency's terms are its own contract"
    if dep in PERMISSIVE:
        if dep == "Apache-2.0" and project == "GPL-2.0-only":
            return "incompatible", "Apache-2.0's patent terms cannot be released under GPL-2.0-only"
        if dep == "Apache-2.0" and project == "GPL-2.0-or-later":
            return "conditions", ("Apache-2.0 code joins a GPL-2.0-or-later project only if the combination is released "
                                  "under GPL-3.0 or later")
        return "compatible", f"{dep} is permissive: keep its copyright and licence notice"
    if dep in WEAK:
        if dep == "EPL-2.0" and project in GPL | AGPL:
            return "unknown", "EPL-2.0 joins a GPL project only if the dependency names GPL as a secondary licence"
        if dep == "CC-BY-SA-4.0" and project == "GPL-2.0-only":
            return "incompatible", "CC-BY-SA-4.0 material may be adapted only into GPL-3.0, not GPL-2.0-only"
        if dep.startswith("LGPL-3.0") and project == "GPL-2.0-only":
            return "incompatible", "LGPL-3.0 code cannot be released under GPL-2.0-only"
        if dep.startswith("LGPL") and project in GPL | AGPL:
            return "compatible", f"{dep} code may be used under the GPL family's terms"
        if dep == "MPL-2.0" and project in GPL | AGPL:
            return "conditions", ("MPL-2.0 joins a GPL project through its secondary licences, unless its files carry "
                                  "the notice that they are incompatible with secondary licences (which the table "
                                  "cannot see); its files stay under MPL-2.0")
        return "conditions", (f"{dep} is weak copyleft: its own files stay under {dep} and their changes must be "
                              "shared" + (", and users must be able to replace the library" if dep.startswith("LGPL")
                                          else ""))
    if dep in GPL:
        if project in PERMISSIVE | WEAK or project == PROPRIETARY:
            return "incompatible", f"{dep} requires the combined work to be released under the GPL, not {project}"
        if project in AGPL:
            if dep == "GPL-2.0-only":
                return "incompatible", "GPL-2.0-only and AGPL-3.0 cannot be combined"
            return "conditions", f"{dep} code may be combined with AGPL-3.0 code (section 13); the GPL part keeps its terms"
        return _GPL_ROWS[(dep, project)]
    if dep in AGPL:
        if project in AGPL:
            return "compatible", "both are AGPL-3.0"
        if project in ("GPL-3.0-only", "GPL-3.0-or-later"):
            return "conditions", "allowed by section 13; the AGPL part keeps its network-use clause"
        return "incompatible", f"{dep} requires the combined work, served over a network too, to be under the AGPL"
    return "unknown", "the table has no row for this pair"


def _rank(verdict: str) -> int:
    return VERDICTS.index(verdict)


def check(dep_licence: str, project_licence: str) -> tuple[str, str, str]:
    """``(verdict, why, dependency licence as SPDX)`` for a dependency in a project; see the module docstring."""
    dep, project = spdx(dep_licence), spdx(project_licence)
    if " " not in dep:
        verdict, why = _pair(dep, project)
        return verdict, why, dep
    try:
        tree = _tree(_TOKEN.findall(dep))
    except ValueError:
        return "unknown", "the licence expression could not be read in full", dep

    def judge(node: Tree) -> tuple[str, str]:
        if isinstance(node, str):
            return _pair(node, project)
        word, parts = node
        found = [judge(part) for part in parts]
        return (max if word == "AND" else min)(found, key=lambda f: _rank(f[0]))

    verdict, why = judge(tree)
    return verdict, f"{dep}: {why}", dep


__all__ = ["KNOWN", "PROPRIETARY", "TABLE", "VERDICTS", "check", "one", "spdx"]
