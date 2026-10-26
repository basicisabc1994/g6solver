# SPDX-License-Identifier: Apache-2.0
"""``scrub(text)``: mask secrets in text before it enters a packet, a file or a model call (audit 032, CTX-005/007).

Pattern-based, so it masks what it recognises and can miss a secret with an unknown shape: well-known token shapes
(AWS key ids, ``sk-`` / GitHub / GitLab ``glpat-`` / Hugging Face ``hf_`` / Slack / Google / Stripe keys, JWTs, bearer
tokens, PEM private keys, passwords in URLs, Azure ``AccountKey=`` values) and any value assigned to a name that ends like a secret (``api_key``, ``secret``, ``token``, ``password``,
``access_key``, ``private_key``, ``credentials``). It errs toward masking. ``scrub`` is idempotent.

Beside it, from the archive's diagnostic collector: ``mask_emails`` (an e-mail address is personal data), ``mask_paths``
(a home folder's root, which names the user, and the temporary folder's root), ``mask_hosts`` (URL user names, host
names and IP addresses) and ``sanitize_stack_trace`` (a Python traceback's frames, without the source lines under
them). ``g6 doctor --report`` builds its error report from them; :data:`KEPT_TEXT_MASKS` says, in the words its card
uses, exactly what they mask.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

MASK = "[REDACTED]"
EMAIL_MASK = "[REDACTED_EMAIL]"
HOME_MASK = "~"
TEMP_MASK = "<temp>"
HOST_MASK, IP_MASK = "<host>", "<ip>"
#: Exactly what ``scrub``, ``mask_emails``, ``mask_paths`` and ``mask_hosts`` mask, in the words a report's card uses.
KEPT_TEXT_MASKS = ("secrets in the shapes scrub knows (a value after a name such as key, api_key, access_key, private_key, "
                   "secret, token, password, passwd, pwd, pass, passphrase or credentials; AWS key ids; sk- keys; "
                   "GitHub, GitLab glpat-, Hugging Face hf_, Slack, Google and Stripe tokens; JWTs; bearer tokens; PEM "
                   "private keys; URL passwords; Azure AccountKey values), e-mail addresses (also %40-encoded and "
                   "@localhost), home folder roots (a drive's Users, Profiles or Documents and Settings folder, /home, "
                   "/var/home, /Users, /Volumes/<volume>/Users, /mnt/<drive>/Users, a network share's server, share and "
                   "first folder, and this user's own home) and the temporary root, URL user names, host names in URLs "
                   "and in host=, hostname= and server= settings, names under common top-level domains, and IPv4 and "
                   "IPv6 addresses (full and compressed, ::1 included)")

_KEY = (r"(?:api[_\-]?key|secret(?:[_\-]?key)?|access[_\-]?key(?:[_\-]?id)?|private[_\-]?key|token|passw(?:or)?d|"
        r"credentials?|(?<![A-Za-z])(?:key|pwd|pass(?:phrase)?))")
_SEP = r"""["']?\s*[:=]\s*"""

# (pattern, replacement); order matters only in that whole-block patterns run first.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # A PEM private key; an unterminated block is masked to the end of the text.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S), MASK),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^/\s@]+@"), r"\1" + MASK + "@"),
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), MASK),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"), MASK),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(rf"(?i)\b(AccountKey\s*=\s*)(?!{re.escape(MASK)})[^;\s\"']+"), r"\1" + MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), MASK),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), MASK),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), MASK),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{12,}=*"), r"\1" + MASK),
    # name = "value" / name: 'value'
    (re.compile(rf"(?i)({_KEY}{_SEP})([\"'])(?:(?!\2).)+\2"), r"\1\2" + MASK + r"\2"),
    # name = value / name: value (unquoted; stops at a delimiter so code like ``password: str)`` is left alone;
    # skips a value that is already the mask, which keeps scrub idempotent)
    (re.compile(rf"(?i)({_KEY}{_SEP})(?![\"'])(?!{re.escape(MASK)})[^\s\"',;)\]}}]{{4,}}"), r"\1" + MASK),
)


def scrub(text: str) -> str:
    """``text`` with every recognised secret replaced by ``[REDACTED]``."""
    if not text:
        return text
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


# A real top-level domain (two letters or more), or localhost, is required, so ``pkg@1.2.3``, ``@scope/pkg`` and
# ``HEAD@{0}`` stay; ``%40`` is an at sign written into a URL.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+(?:@|%40)(?:[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|localhost\b)")
# A home folder's root in each system's shape; the part after the user's name stays. The name may hold spaces.
_NAME = r"[^\\/\r\n\"'<>|]+"
_HOMES = (re.compile(rf"(?i)(?<![\w:])(?:\\\\|//)[^\\/\s\"'<>|]+[\\/]+[^\\/\s\"'<>|]+[\\/]+{_NAME}"),   # a share
          re.compile(rf"(?i)\b[A-Z]:[\\/]+(?:Users|Profiles|Documents and Settings)[\\/]+{_NAME}"),
          re.compile(rf"(?i)(?<![\w.\-])/mnt/[a-z]/Users/{_NAME}"),
          re.compile(rf"(?<![\w.\-])/Volumes/{_NAME}/Users/{_NAME}"),
          re.compile(rf"(?<![\w.\-])/(?:var/)?home/{_NAME}"),
          re.compile(rf"(?<![\w.\-])/Users/{_NAME}"))
_TLDS = ("com|net|org|io|ai|dev|app|co|uk|au|nz|de|fr|nl|eu|us|ca|in|jp|info|biz|edu|gov|mil|cloud|online|site|"
         "local|lan|internal|corp|home|localdomain")
_HOST_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)(?:[^/\s@]*@)"), r"\1" + MASK + "@"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://(?:\[REDACTED\]@)?)(?:\[[0-9a-f:.]+\]|[^/\s:?#@\"'<>\[\]]+)"),
     r"\1" + HOST_MASK),
    (re.compile(r"(?i)\b((?:host|hostname|server)\s*[=:]\s*)([\"']?)[^\s\"',;)]+\2"), r"\1\2" + HOST_MASK + r"\2"),
    (re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}(?![\w:])"), IP_MASK),
    # compressed: groups on either side of ``::``, or none (``::1``, ``fe80::``, ``::``)
    (re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?"
                r"(?![\w:])"), IP_MASK),
    # a full stop after the address ends a sentence; only a digit after it makes a longer number
    (re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)"), IP_MASK),
    (re.compile(rf"(?i)(?<![\w.\-<])(?:[a-z0-9](?:[a-z0-9\-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})\b(?![\w\-])"), HOST_MASK),
)
#: The longest file or function name a frame keeps (a traceback line can be any length).
MAX_FRAME_FIELD = 1000
# Where a path starts and ends: not inside a longer name, and not a sibling such as ``/tmpfoo``.
_BEFORE, _AFTER = r"(?<![\w.\-/\\~])", r"(?=[/\\\s\"']|$)"
# The function name is matched greedily to its last non-blank character: linear on any line (a lazy match before
# trailing blanks took quadratic time on a long run of them).
_FRAME = re.compile(r'^[ \t]*File[ \t]+"([^"\r\n]+)",[ \t]+line[ \t]+(\d{1,9}),[ \t]+in[ \t]+(\S(?:[^\r\n]*\S)?)[ \t]*$',
                    re.MULTILINE)


def mask_emails(text: str) -> str:
    """``text`` with every e-mail address replaced by ``[REDACTED_EMAIL]``."""
    return _EMAIL.sub(EMAIL_MASK, text) if text else text


def _root(path: str) -> re.Pattern[str] | None:
    path = path.rstrip("/\\")
    return re.compile(r"(?i)" + _BEFORE + re.escape(path) + _AFTER) if path.strip("/\\") else None


def mask_paths(text: str) -> str:
    """``text`` with the temporary folder's root replaced by ``<temp>``, then each home folder's root by ``~``
    (the shapes :data:`KEPT_TEXT_MASKS` lists, and this user's own home). What lies below a root stays: a folder name
    under it can still say something about you."""
    if not text:
        return text
    temp, home = _root(tempfile.gettempdir()), _root(str(Path.home()))
    if temp is not None:
        text = temp.sub(TEMP_MASK, text)
    for pattern in (*_HOMES, *((home,) if home is not None else ())):
        text = pattern.sub(HOME_MASK, text)
    return text


def mask_hosts(text: str) -> str:
    """``text`` with URL user names masked (``[REDACTED]``), host names in URLs and in ``host=`` settings and names
    under common top-level domains replaced by ``<host>``, and IPv4 and IPv6 addresses by ``<ip>``. Run it after
    :func:`mask_emails`, so an address's domain goes with the address."""
    if not text:
        return text
    for pattern, replacement in _HOST_RULES:
        text = pattern.sub(replacement, text)
    return text


def sanitize_stack_trace(text: str) -> list[dict[str, object]]:
    """The frames of a Python traceback as ``{"file", "line", "function"}``, each file with :func:`mask_paths`
    applied and each name cut to :data:`MAX_FRAME_FIELD` characters. The source line under each frame, which shows
    the code, is never read."""
    return [{"file": mask_paths(m.group(1)[:MAX_FRAME_FIELD]), "line": int(m.group(2)),
             "function": m.group(3)[:MAX_FRAME_FIELD]} for m in _FRAME.finditer(text or "")]


__all__ = ["EMAIL_MASK", "HOME_MASK", "HOST_MASK", "IP_MASK", "KEPT_TEXT_MASKS", "MASK", "MAX_FRAME_FIELD", "TEMP_MASK",
           "mask_emails", "mask_hosts", "mask_paths", "sanitize_stack_trace", "scrub"]
