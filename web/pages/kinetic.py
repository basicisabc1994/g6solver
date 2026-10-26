# SPDX-License-Identifier: Apache-2.0
"""Which pages set their headings in motion as they scroll (owner, 2026-10-19): the content pages and every essay, and
the home page below its scene (owner, 2026-10-20: "extend animations/typography to the remaining landing page"). The
home page keeps its lattice as its one picture, so it does not get the Bauhaus forms the other pages move behind their
text. ``base.html`` reads ``KINETIC`` to mark the body and load ``kinetic.css`` and ``kinetic.js``, and ``BAUHAUS`` to
load the forms."""
from __future__ import annotations

PATHS = frozenset({"/metaharness/", "/components/", "/research/", "/docs/", "/guide/", "/guide/view/", "/faq/"})


def kinetic(request) -> dict[str, bool]:
    path = request.path
    forms = path in PATHS or path.startswith("/essays/")
    return {"KINETIC": forms or path == "/", "BAUHAUS": forms}
