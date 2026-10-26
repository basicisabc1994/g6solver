# SPDX-License-Identifier: Apache-2.0
"""The failure-mode map the site shows (FW-03): read from ``catalog/failure_modes.json``, which
``tools/gen_failure_map.py`` writes from the registry and each component's own declarations, so no page types a
failure mode, or what guards it, in by hand. A package planned to guard a mode is shown as planned, never as guarding,
and only while the site's switch is on (``pages.extensions.SHOW_PLANNED``): off, a mode nothing in the library guards is
shown as not guarded yet, and no planned package is named. A component built in a vetted area whose certificate is not
current is shown as awaiting its certificate, whatever the switch says: it is built, not planned."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MAP = Path(__file__).resolve().parents[2] / "catalog" / "failure_modes.json"


@lru_cache(maxsize=1)
def modes() -> tuple[dict, ...]:
    return tuple(json.loads(MAP.read_text(encoding="utf-8")))


def _links(names: list[str], core: set[str]) -> list[dict]:
    """Each component with its entry on the Components page: the core list or the extensions list."""
    return [{"name": n, "href": f"/components/#{'core' if n in core else 'ext'}-{n}"} for n in names]


def _shown(mode: dict, core: set[str]) -> dict:
    # a component built in a vetted area whose certificate is not current guards nothing yet (g6.core.vetting); a
    # planned package is named only while the switch is on
    from pages import extensions

    return dict(mode, guards=_links(mode["guarded_by"], core), causes=_links(mode["introduced_by"], core),
                awaiting=_links(mode.get("awaiting_review", []), core),
                planned=list(mode["planned"]) if extensions.SHOW_PLANNED else [])


def _core() -> set[str]:
    from pages import catalogue

    return {c["name"] for c in catalogue.components()}


def context() -> dict:
    from pages import extensions

    core = _core()
    shown = [_shown(m, core) for m in modes()]
    guarded = [m for m in shown if m["guarded_by"]]
    if not extensions.SHOW_PLANNED:
        unguarded = [m for m in shown if not m["guarded_by"]]
        # COPY: the two groups of the map while only what is in the library is shown.
        groups = [
            {"key": "guarded", "title": "Guarded today", "modes": guarded,
             "lede": "Each of these has at least one component in the library that declares it guards against it."},
            {"key": "unguarded", "title": "No guard yet", "modes": unguarded,
             "lede": "Nothing in the library guards against these yet: a hole in every slice."},
        ]
        return {"groups": groups, "show_planned": False,
                "counts": {"total": len(shown), "guarded": len(guarded), "unguarded": len(unguarded)}}
    planned = [m for m in shown if not m["guarded_by"] and (m["planned"] or m["awaiting"])]
    unguarded = [m for m in shown if not m["guarded_by"] and not m["planned"] and not m["awaiting"]]
    # COPY: the three groups of the map.
    groups = [
        {"key": "guarded", "title": "Guarded today", "modes": guarded,
         "lede": "Each of these has at least one component in the library that declares it guards against it."},
        {"key": "planned", "title": "Planned", "modes": planned,
         "lede": "Nothing in the library guards against these yet. The packages named are planned to; until they are "
                 "in the library and declare the mode themselves, they guard nothing."},
        {"key": "unguarded", "title": "No guard yet", "modes": unguarded,
         "lede": "Named, and neither guarded nor planned: a hole in every slice."},
    ]
    return {"groups": groups, "show_planned": True,
            "counts": {"total": len(shown), "guarded": len(guarded), "planned": len(planned),
                       "unguarded": len(unguarded)}}


def mode_context(code: str) -> dict:
    from pages import extensions

    return {"mode": _shown(next(m for m in modes() if m["code"] == code), _core()),
            "show_planned": extensions.SHOW_PLANNED}
