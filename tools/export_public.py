# SPDX-License-Identifier: Apache-2.0
"""Build a release tree from the allowlist, and refuse it if anything sensitive slips in; or scan any tree.

Usage:
    python tools/export_public.py --out DIR                 # copy the allowlisted paths, then scan
    python tools/export_public.py --scan DIR                # scan an existing tree only
    python tools/export_public.py --scan DIR --public-ci    # the public repository's CI, which has no private terms

Exit code 0 = clean, 1 = findings (printed without secret values), 2 = refused to start (no private-terms list
outside the public CI).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = REPO / "oss" / "export" / "ALLOWLIST"
# A maintainer's own list kept outside any release: one literal term per line (names, handles, emails) that must not
# ship. Every run outside the public repository's CI needs it (EXPORT_PUBLIC-02); a run without it says so.
PII_TERMS_FILE = REPO / "docs" / "transition" / "PII_TERMS"
# Reviewed waivers, "path sha256 rule" per line (owner-approved, release blocker R-08). Beside this tool, in the source
# tree and in every release. A waiver covers one PII rule in one file with exactly that content.
SCAN_EXCEPTIONS = Path(__file__).resolve().parents[1] / "export" / "SCAN_EXCEPTIONS"

# (rule id, regex). Values are never printed, only file:line and rule id.
SECRET_RULES = [
    ("openrouter-key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("stripe-key", re.compile(r"\b(?:sk|rk|pk)_(?:test|live)_[A-Za-z0-9]{16,}")),
    ("stripe-webhook", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
PII_RULES = [
    # A drive letter's Users folder, with either slash, and Git Bash's spelling of it with the drive as a folder
    # (EXPORT_PUBLIC-02 added the last).
    ("windows-user-path", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+|(?<![\w./:-])/[A-Za-z]/Users/", re.IGNORECASE)),
    # Linux /home/<name> and macOS /Users/<name>; a placeholder such as <name> or $USER is not a name, and a path
    # inside a web address is not a home folder (EXPORT_PUBLIC-02).
    ("posix-home-path", re.compile(r"(?<![\w./:~-])/(?:home|Users)/[A-Za-z0-9._-]+")),
    # Any e-mail address (EXPORT_PUBLIC-02; it was gmail only), except ALLOWED_EMAILS and reserved domains. A user
    # name in a web address ("https://user:pass@host") or an escape before a decorator ("\n@pytest") is not one.
    ("personal-email", re.compile(r"(?:(?<=mailto:)|(?<![\\/:\w.%+-]))[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                                  r"\.[A-Za-z]{2,}\b")),
]
# Addresses that are nobody's personal data, so the e-mail rule passes them:
# - noreply@anthropic.com: the commit-trailer address Anthropic publishes for Claude;
# - hello@g6solver.com: the project's own contact address, published on its security page.
ALLOWED_EMAILS = {"noreply@anthropic.com", "hello@g6solver.com"}
# Names reserved for examples and tests, never anyone's mailbox (RFC 2606, RFC 6761).
RESERVED_EMAIL_DOMAINS = ("example.com", "example.net", "example.org")
RESERVED_EMAIL_TLDS = (".example", ".invalid", ".test", ".localhost")
# Home-folder names that stand for "someone" in examples and tests, so the home-path rule passes /home/<name> and
# /Users/<name> for these exact names only:
PLACEHOLDER_HOME_NAMES = {
    "me",       # "/home/me": the reader's own folder in an example
    "user",     # "/home/user": the generic user of a how-to
    "alice",    # "/Users/alice": the first of the two conventional example people
    "bob",      # "/Users/bob": the second of them
    "jdoe",     # "/home/jdoe": John Doe, the conventional unnamed person
    "someone",  # "/home/someone": any other person, in a test
}


def _allowed(rule: str, matched: str) -> bool:
    """Whether a match is on a named allowlist: an e-mail address, or a placeholder name in a home path."""
    if rule == "posix-home-path":
        return matched.rsplit("/", 1)[-1] in PLACEHOLDER_HOME_NAMES
    if rule != "personal-email":
        return False
    address = matched.lower()
    domain = address.rsplit("@", 1)[1]
    return (address in ALLOWED_EMAILS or domain.endswith(RESERVED_EMAIL_TLDS)
            or any(domain == d or domain.endswith("." + d) for d in RESERVED_EMAIL_DOMAINS))
# File names that must never ship (answer keys, caches, local state, copyrighted books).
DENIED_NAMES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"gpqa", r"hle_questions", r"bbeh_v1", r"omnimath", r"math500", r"cruxeval",
        r"gold_cases", r"\.env(\..*)?$", r"settings\.local\.json$", r"\.(db|sqlite3?|pkl|pickle)$",
        r"EXPERT_PYTHON_PROGRAMMING",
        r"dokumen\.pub", r"pdfdrive", r"ebooksworld",   # pirated-ebook markers (audit 014, GRD-015)
    )
]
# PDFs are allowed only for the owner's theory documents.
PDF_ALLOWED_DIRS = ("docs/theory/",)
# The binary types the export carries. A file with one of these suffixes that is not UTF-8 is named with its size in
# the summary; any other file that is not UTF-8 is a finding, since the scan cannot read it (EXPORT_PUBLIC-01). A file
# that is UTF-8 is scanned as text whatever its suffix.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico",   # images
    ".woff", ".woff2",                          # fonts
    ".zip", ".whl",                             # archives
    ".pdf",                                     # the theory documents (PDF_ALLOWED_DIRS)
}


def read_allowlist(path: Path = ALLOWLIST) -> list[tuple[str, str]]:
    """Entries are ``src`` (copied to the same path) or ``src -> dst`` (``.`` is the export root)."""
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        src, _, dst = (part.strip() for part in ln.partition("->"))
        out.append((src, dst or src))
    return out


def _private_terms() -> list[tuple[str, re.Pattern[str]]]:
    if not PII_TERMS_FILE.is_file():
        return []
    terms = [t.strip() for t in PII_TERMS_FILE.read_text(encoding="utf-8").splitlines() if t.strip()]
    return [("private-term", re.compile(re.escape(t), re.IGNORECASE)) for t in terms if not t.startswith("#")]


def content_sha256(path: Path) -> str:
    """Digest with CRLF read as LF, so a Windows checkout and a Linux clone of the same commit agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_exceptions(path: Path = SCAN_EXCEPTIONS) -> set[tuple[str, str, str]]:
    """Waivers as (path, sha256, rule). Only a PII rule can be waived; a secret rule or anything else is refused."""
    if not path.is_file():
        return set()
    pii = {rule for rule, _ in PII_RULES}
    out = set()
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        rel, digest, rule = ln.split()
        if rule not in pii:
            raise ValueError(f"{path.name}: rule {rule!r} cannot be waived")
        out.add((rel, digest.lower(), rule))
    return out


def scan(root: Path, exceptions: set[tuple[str, str, str]] | None = None) -> list[str]:
    return scan_report(root, exceptions)[0]


def scan_report(root: Path, exceptions: set[tuple[str, str, str]] | None = None
                ) -> tuple[list[str], list[tuple[str, int]]]:
    """The findings, and each listed binary file (``BINARY_SUFFIXES``) that is not text, with its size in bytes."""
    findings: list[str] = []
    binaries: list[tuple[str, int]] = []
    rules = SECRET_RULES + PII_RULES + _private_terms()
    waived = read_exceptions() if exceptions is None else exceptions
    waived_paths = {rel for rel, _, _ in waived}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        if "/.git/" in f"/{rel}" or "__pycache__" in rel:
            continue
        for pat in DENIED_NAMES:
            if pat.search(f.name):
                findings.append(f"{rel}: denied-file ({pat.pattern})")
        if f.suffix.lower() == ".pdf" and not rel.startswith(PDF_ALLOWED_DIRS):
            findings.append(f"{rel}: pdf-outside-theory")
        try:
            data = f.read_bytes()
        except OSError:
            findings.append(f"{rel}: unreadable")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if f.suffix.lower() in BINARY_SUFFIXES:
                binaries.append((rel, len(data)))
            else:
                findings.append(f"{rel}: not-utf8-text")
            continue
        digest = content_sha256(f) if rel in waived_paths else ""
        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, rx in rules:
                if (any(not _allowed(rule, m.group(0)) for m in rx.finditer(line))
                        and (rel, digest, rule) not in waived):
                    findings.append(f"{rel}:{lineno}: {rule}")
    return findings, binaries


def export(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        sys.exit(f"refusing to export into non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.egg-info", "dist", "build",
                                    ".hypothesis", ".ruff_cache", ".import_linter_cache", ".venv", "node_modules")
    for entry, target in read_allowlist():
        src = REPO / entry
        dst = (out / target).resolve()
        if not dst.is_relative_to(out.resolve()):
            sys.exit(f"allowlist target escapes the export: {target}")
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            sys.exit(f"allowlisted path missing: {entry}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=Path)
    g.add_argument("--scan", type=Path)
    ap.add_argument("--public-ci", action="store_true",
                    help="the public repository's CI scan, which runs without the private-terms list (it is kept "
                         "outside every release); any other run refuses to start without that list")
    args = ap.parse_args(argv)
    terms_name = "docs/transition/PII_TERMS"
    if not PII_TERMS_FILE.is_file() and not args.public_ci:
        print(f"refusing to start: the private-terms list ({terms_name}) is missing, so names and handles on it "
              "would ship unscanned. Only the public repository's CI runs without it (--public-ci).", file=sys.stderr)
        return 2
    root = args.scan
    if args.out:
        export(args.out)
        root = args.out
    findings, binaries = scan_report(root)
    for line in findings:
        print(line)
    for rel, size in binaries:
        print(f"binary: {rel} ({size:,} bytes)")
    for rel, digest, rule in sorted(read_exceptions()):
        print(f"waived: {rel} {rule} (sha256 {digest[:12]}..., {SCAN_EXCEPTIONS.name})")
    if PII_TERMS_FILE.is_file():
        print(f"private terms: {len(_private_terms())} from {terms_name}")
    else:
        print(f"private terms: none: this scan ran without the private-terms list ({terms_name}, --public-ci)")
    print(f"{len(findings)} finding(s) in {root}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
