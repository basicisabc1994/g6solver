# SPDX-License-Identifier: Apache-2.0
"""Progressive discovery for agents: find → describe → ops → invoke.

Built only on what components declare about themselves (package docstring, block, ``ops`` /
``help`` answers, maturity), so it cannot drift from the code the way a hand-maintained catalogue
does (audit 016, NAV-004/007). Ranking is a transparent token-overlap score over each package's
docstring, its skill's description (when to use it) and the titles of the failure modes it declares it guards
against, all read from its source without importing it, so a component that is slow or fails to import cannot
break ``find``, and an agent
can find a guard by the failure it is worried about (D6); ``describe`` imports only the one component
asked for. Ties break by score
then name, scores are returned, and an empty result means nothing matched. Nothing here composes,
validates or labels a pipeline — discovery never says anything is ``verified``.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import re
from collections import Counter
from typing import Any

from g6.core import vetting
from g6.core.confine import UNCONFINED_WARNING
from g6.core.door import THREAT_STATEMENT
from g6.core.failure_modes import FailureModeRegistry, declared
from g6.core.registry import get_registry
from g6.core.skills import front_matter, skill_text

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by can could do does for from how i in is it me my of on or our should that "
                  "the this to we what will with would you your".split())
#: Where a ported component's docstring starts listing what it does NOT do. That list is not what the component offers,
#: so it is not ranked: the solver's "customer scoping (not ported)" once outranked action_gating for a customer email.
_NOT_OFFERED = "Not ported from the archive"


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower().replace("_", " ")) if t not in _STOP]


def ops(name: str) -> list[str]:
    """The ops a component's block lists for itself (via its ``ops`` op), or ``[]``."""
    result = get_registry().invoke(name, "ops")
    if result.__class__.__name__ != "Success":
        return []
    value = result.unwrap()
    listed = value.get("ops") if isinstance(value, dict) else None
    return [str(o) for o in listed] if isinstance(listed, list) else []


def _package(name: str) -> str:
    return get_registry().refs[name].module.split(":", 1)[0]


def _docstring(name: str) -> str:
    """A component's package docstring, read from its source without importing it; ``""`` if it cannot be read."""
    try:
        spec = importlib.util.find_spec(_package(name))
        if spec is None or not (spec.origin or "").endswith(".py"):
            return ""
        with open(spec.origin, encoding="utf-8") as f:
            return ast.get_docstring(ast.parse(f.read())) or ""
    except (ImportError, OSError, SyntaxError, ValueError):
        return ""


def _guards(name: str) -> list[str]:
    """The titles of the failure modes a component declares it guards against, read without importing it."""
    codes = declared(_package(name)).get("__g6_mitigates__", ())
    return [FailureModeRegistry.title(c) for c in codes if FailureModeRegistry.is_registered(c)]


def _when(name: str) -> str:
    """The ``description`` of a component's skill (when to use it), read without importing the component."""
    return front_matter(skill_text(name) or "").get("description", "")


def _profile(name: str) -> str:
    offered = _docstring(name).split(_NOT_OFFERED, 1)[0]
    return " ".join([name, name, offered, _when(name), *_guards(name)])


def _modes(codes: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"code": c, "title": FailureModeRegistry.title(c) if FailureModeRegistry.is_registered(c) else ""}
            for c in codes]


def _installed(name: str) -> bool:
    """Whether the top-level package of the import name ``name`` is installed, without importing it."""
    try:
        return importlib.util.find_spec(name.partition(".")[0]) is not None
    except (ImportError, ValueError):
        return False


def _extras(extras: dict[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    return [{"extra": extra, "provides": list(names), "installed": all(_installed(n) for n in names)}
            for extra, names in extras.items()]


def find(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Components ranked by overlap with ``query``. Returns ``[]`` when nothing matches."""
    q = Counter(_tokens(query))
    if not q:
        return []
    names = get_registry().names()
    docs = {n: Counter(_tokens(_profile(n))) for n in names}
    df = Counter(t for d in docs.values() for t in set(d))
    n_docs = max(1, len(docs))
    scored = []
    for name, d in docs.items():
        score = sum(
            min(q[t], d[t]) * math.log(1 + n_docs / df[t]) for t in q if d.get(t)
        )
        if score > 0:
            matched = sorted(t for t in q if d.get(t))
            scored.append({"component": name, "score": round(score, 3), "matched": matched})
    scored.sort(key=lambda r: (-r["score"], r["component"]))
    return scored[:limit]


def describe(name: str) -> dict[str, Any]:
    """What a component is, how mature it is, the ops it exposes, the failure modes it guards against and can cause,
    what it needs from outside G6 (``outside``: programs you install, from ``__g6_outside__``; ``extras``: each extra,
    the imports it provides and whether they are installed, from ``__g6_extras__``; ``keys``: each key it needs and the
    key file it goes in, from ``__g6_keys__``; nothing is run to find out), its skill (``None`` when it has none), and,
    in a vetted area, whether it is still awaiting review (``vetting``, from ``__g6_vetting__``; ``None`` outside
    one)."""
    info = get_registry().describe(name)
    if info.get("available"):
        info["ops"] = ops(name)
        mod = get_registry().get_module(name)
        info["requires"] = list(getattr(mod, "__g6_requires__", ()))
        info["mitigates"] = _modes(getattr(mod, "__g6_mitigates__", ()))
        info["introduces"] = _modes(getattr(mod, "__g6_introduces__", ()))
        info["outside"] = [program.describe() for program in getattr(mod, "__g6_outside__", ())]
        info["extras"] = _extras(getattr(mod, "__g6_extras__", {}))
        info["keys"] = [key.describe() for key in getattr(mod, "__g6_keys__", ())]
        info["skill"] = skill_text(name)
        info["vetting"] = vetting.summary(_package(name))
        info["certificate"] = vetting.certificate(_package(name))
    return info


HELP = (
    "G6 discovery, in order: find(query) ranks components by what they say about themselves and the failures "
    "they guard against; describe(component) gives maturity, ops, dependencies, the failure modes it guards "
    "against and can cause, the programs and extras it needs from outside G6, and the component's skill; "
    "invoke(component, op, "
    "params) runs one op through the component's typed input and returns its result; over MCP the reply also "
    "carries completion_state and a warning_card. Results carry the component's reliability label; discovery never "
    "upgrades it, and a result that reports no label is a qualified-draft. Over MCP and g6 invoke, a component's own "
    "verified stands only when G6's own code re-runs a check at the door and reproduces it; any other is held at "
    "qualified-draft (G6_E_PROMOTION_LAW, claimed_completion_state). " + THREAT_STATEMENT + " An op that needs a model "
    "uses the one your user set in their key file (~/.g6/.env, for example G6_LLM_PROVIDER=claude_code); with none it "
    "is refused with a *_NO_MODEL code: then do that step yourself and call the op that checks it. A sensitive op (it "
    "runs a program, reaches the network or changes the machine) is refused over MCP unless your user lists it in "
    "G6_MCP_SENSITIVE where the server is registered; tell them, before they do: " + UNCONFINED_WARNING + " "
    "To learn which G6 step "
    "fits each point of your work, load the using-g6 skill first; `g6 skills install` copies it into your "
    "agent's skills folder."
)
