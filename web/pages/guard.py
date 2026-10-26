# SPDX-License-Identifier: Apache-2.0
"""Checks on rendered pages: no hosted-service text (spec red line "must not look like SaaS"), the site's own
links, and the inline scripts the security policy must hash."""
from __future__ import annotations

import html
import re

# The spec's list, plus the hosted SSE endpoint ("no hosted-service language"), "hosted" and "premium" themselves, and
# schema.org's price field (a price offer in a page's structured data is read by search engines though no visitor
# sees it).
GUARD: tuple[str, ...] = ("sign up", "sign in", "log in", "pricing", "free trial", "/accounts/", "/billing/", "stripe",
                          "api key", "tier", "hosted", "premium", "g6solver.com/mcp", "priceCurrency")

# Inflections a word may take and still be the same hosted-product text: "signing up", "logged in", "API keys".
_INFLECTED = {"sign": "sign(?:s|ed|ing)?", "log": "log(?:s|ged|ging)?", "key": "keys?", "trial": "trials?"}
# Invisible or look-alike characters an entity can put inside a phrase ("sign&shy;in", "API&#8209;key").
_LOOKALIKE = str.maketrans({"­": None, "​": None, "⁠": None, "‐": "-", "‑": "-"})


def _pattern(term: str) -> re.Pattern[str]:
    if "/" in term:                                   # a path: anywhere, as written
        return re.compile(re.escape(term), re.IGNORECASE)
    # A word or phrase, whole words only. A space also matches a hyphen or nothing, so "sign-up" and "signup" count
    # as "sign up": the text must stop describing a hosted service, not be respelled around the check.
    words = r"[\s\-]*".join(_INFLECTED.get(w.lower(), re.escape(w)) for w in term.split())
    return re.compile(rf"\b{words}\b", re.IGNORECASE)


_GUARDS = tuple((term, _pattern(term)) for term in GUARD)

# Phrases that use a guard word for something that is not a hosted service. Each is cut out before the check, so the
# same word anywhere else still counts. Keep this list short and say why.
ALLOWED: tuple[tuple[str, str], ...] = (
    (r"\btiers? [0-3](?: and [0-3])?\b|\btier-[0-3]-", "the guide's autonomy tiers T0-T3, the library's own term"),
    (r"\bown API key\b", "bring your own model provider's key"),
    (r"\bfailed login attempts\b", "security advice in the guide"),
    (r"\bno sign-?up\b", "says there is nothing to register"),
    (r"\b(?:per-token|AI|consumption-based|the current AI) pricing\b|\bpricing (?:model|punishes)\b"
     r"|\bsafety constraints, pricing, and access\b", "essays about the AI industry's pricing, not G6's"),
    (r"\bStripe all built\b", "an essay naming companies that moved fast"),
    (r"\bno hosted service\b", "says there is no service to sign up to"),
    (r"\bHardcoded API keys\b|\bAre API keys and secrets in environment variables\b"
     r"|\bNo API keys, passwords, or tokens in the code\b|\bthe user needs to be logged in\b",
     "the guide's security advice for the apps an agent builds"),
    (r"\bextract premium rents\b", "an essay on the economics of AI models"),
)
_ALLOWED = re.compile("|".join(f"(?:{pattern})" for pattern, _ in ALLOWED), re.IGNORECASE)
_LINK = re.compile(r"""\b(?:href|src)\s*=\s*["'](/(?!/)[^"'#?]*)""", re.IGNORECASE)
# Not a URL with a scheme ("https:", "mailto:", "data:"), not from the site root, not a fragment.
_RELATIVE = re.compile(r"""(?<![\w-])(?:href|src)\s*=\s*["'](?![A-Za-z][A-Za-z0-9+.\-]*:|/|#)([^"']+)""",
                       re.IGNORECASE)
_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def saas_hits(page: str) -> list[str]:
    """Each guard term found in the raw HTML (attributes and scripts included), in the guard's order, after reading
    character entities as the text they show and cutting out the ``ALLOWED`` phrases."""
    text = _ALLOWED.sub(" ", html.unescape(page).translate(_LOOKALIKE))
    return [term for term, pattern in _GUARDS if pattern.search(text)]


def internal_links(html: str) -> set[str]:
    """The site's own paths a page links to or loads (``href``/``src`` starting with one ``/``), without query or
    fragment."""
    return {m.group(1) for m in _LINK.finditer(html)}


def relative_links(html: str) -> set[str]:
    """Links and loads written relative to the page (``../x/``, ``pic.png``): each resolves differently on each page,
    and the link check cannot follow them."""
    return {m.group(1) for m in _RELATIVE.finditer(html)}


def inline_scripts(html: str) -> list[str]:
    """The bodies of ``<script>`` tags without ``src``, exactly as written (what a CSP hash covers)."""
    return [m.group(1) for m in _INLINE_SCRIPT.finditer(html)]
