# SPDX-License-Identifier: Apache-2.0
"""Offline component certificates, issued only by g6.evidence.certificate.

Each rule's implementation is its own Rule(implements="path:function"), inside the rule table's hash. checks.json
records the authors, the falsification seeds/count/precision/bounds and any boundary inputs beyond the cases. cases.json
holds, for a computes rule, literal decimal strings at their printed precision, whether the source prints them and how
it rounds (its register entry's rounding), with a verbatim quote for a printed one; for a guard, the input that fires at
its threshold, one that holds just inside it and a recall set. A property rule's cases are never printed ones: no
printed value G6's functions can be run on checks it. The independent second_route.evaluate and
falsify.generate/invariant functions run in fresh bounded children. G6 compares their observations; a supplied
verdict is never a receipt. Positions are supplied by the repository tool. No package code calls git.
"""
from __future__ import annotations

import ast
import copy
import datetime as dt
import html
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from g6.core import component_certificate as records
from g6.core import confine
from g6.core.ledger import ChainLedger
from g6.evidence import certificate as issuer
from g6.workforce import _certificate_formal, mutation_check
from g6.workforce._certificate_runner import observe

MAX_MUTANTS = 12
MAX_CASES = 1000
#: The longest quote a printed case may rest on, in characters once whitespace is normalised (G6's own choice: a
#: worked example's paragraph, never numbers a page prints only far apart).
QUOTE_MAX = 1500
#: What a guard case's observation must be: 1 when the guard fires, 0 when it holds.
ROLES = {"fires": "1", "recall": "1", "holds": "0"}
#: How the source rounds a printed value: to the nearest (half a unit in the last place), or by cutting digits off.
ROUNDING = ("half_even", "truncate")
_REFERENCE_IMPORTS = {"decimal", "fractions", "math", "cmath", "random", "statistics", "itertools", "functools",
                      "operator", "mpmath", "scipy", "numpy"}
_FORBIDDEN_CALLS = {"__import__", "exec", "eval", "compile", "open", "globals", "locals", "vars", "getattr", "setattr"}
_FINDING = {"rule", "clause_fair", "code_matches", "source_sha256", "finding"}
#: An absolute folder of the issuing machine (a drive path, or one under /home or /Users), up to a quote, space, comma
#: or semicolon: what confine's reason for an unconfined run names, and what a committed certificate never holds.
_FOLDER = re.compile(r'[A-Za-z]:\\[^"\s,;]*|/(?:home|Users)/[^"\s,;]*')
#: What a reader may attest beyond a finding: a property rule's tier (tier_justified, tier_reason), a clause read by
#: context only (context_attested, reason) and a guard's code doing only what its effect says (effect_only,
#: effect_reason).
_ATTESTATIONS = {"tier_justified", "tier_reason", "context_attested", "reason", "effect_only", "effect_reason"}
#: A power of ten as a source prints it: 9.4 × 10^-21 or 9.4x10-21; or 9.4 10−21, where a conversion lost the
#: multiplication sign (then a space before the 10 and a signed exponent are required, so 2104 stays 2104).
_SCIENTIFIC = re.compile(r"\s*([+-]?\d+(?:\.\d+)?)(?:\s*(?:[×x*]|\\times)\s*10\s*\^?\s*\{?\s*([+\-−]?\d+)"
                         r"|\s+10\s*\^?\s*\{?\s*([+\-−]\d+))\s*\}?\s*")


@dataclass(frozen=True)
class Positions:
    artifact: int
    frozen: Mapping[str, int]
    controls: int
    positive: int
    receipts: int
    commits: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Bundle:
    package: str
    root: Path
    folder: Path
    declaration: dict
    files: dict[str, bytes]
    rules: dict
    covered: dict
    register: list[dict]
    cases: list[dict]
    config: dict
    cert: Path
    inherits: dict | None
    scope: list[str]
    independent: dict
    context_only: list[dict]
    attested: list[dict]
    source_files: Mapping[str, bytes]
    source_bytes_required_at_issuance: list[dict]
    printed: list[dict]
    imported: tuple[str, ...]


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _independent(path: Path) -> None:
    """Conservative AST import policy; no component imports, dynamic loaders or local helpers."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            _require(all(a.name.split(".")[0] in _REFERENCE_IMPORTS for a in node.names),
                     "second route/falsification is not independent: prohibited import")
        elif isinstance(node, ast.ImportFrom):
            _require(not node.level and (node.module or "").split(".")[0] in _REFERENCE_IMPORTS,
                     "second route/falsification is not independent: prohibited import")
        elif isinstance(node, ast.Name):
            _require(node.id not in _FORBIDDEN_CALLS, "independent route cannot use dynamic execution or loading")
        elif isinstance(node, ast.Attribute):
            _require(not node.attr.startswith("__"), "independent route cannot use interpreter internals")


def _adapter_imports(data: bytes) -> None:
    """C1's import rule for a candidate adapter (D9 skips the certificate folder): the standard library, g6 and the
    reference libraries only, never a relative import."""
    allowed = {"g6", *sys.stdlib_module_names, *_REFERENCE_IMPORTS}
    for node in ast.walk(ast.parse(data.decode("utf-8"))):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        _require(not getattr(node, "level", 0) and all(n.split(".")[0] in allowed for n in names),
                 "the candidate adapter imports only the standard library, g6 and the reference libraries")


def _precision(value: Any) -> int:
    _require(type(value) is int and 0 <= value <= 30, "precision must be decimal places from 0 to 30")
    return value


def _number(value: Any) -> Decimal:
    _require(isinstance(value, (str, int)) and not isinstance(value, bool), "expected a literal decimal value")
    try:
        found = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("expected a literal decimal value") from exc
    _require(found.is_finite() and abs(found) < Decimal("1e100"), "expected a bounded finite decimal value")
    return found


def _numeric(value: Any) -> None:
    if isinstance(value, list):
        _require(bool(value), "expected values cannot be empty")
        for item in value:
            _numeric(item)
    elif isinstance(value, dict):
        _require(bool(value), "expected values cannot be empty")
        for item in value.values():
            _numeric(item)
    else:
        _number(value)


def _format(value: Any, precision: int, rounding: str = "half_even") -> Any:
    if isinstance(value, list):
        return [_format(v, precision, rounding) for v in value]
    if isinstance(value, dict):
        return {k: _format(v, precision, rounding) for k, v in sorted(value.items())}
    with localcontext() as ctx:
        ctx.prec = 160
        mode = ROUND_DOWN if rounding == "truncate" else ROUND_HALF_EVEN
        return format(_number(value).quantize(Decimal(1).scaleb(-precision), rounding=mode), f".{precision}f")


def _close(actual: Any, expected: Any, precision: int, rounding: str = "half_even") -> bool:
    """Within half a unit of the last printed place; or, for a source that prints by cutting digits off, equal once
    the actual value's digits past that place are cut off."""
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_close(a, e, precision, rounding) for a, e in zip(actual, expected)))
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(_close(actual[k], e, precision, rounding) for k, e in expected.items()))
    try:
        with localcontext() as ctx:
            ctx.prec = 160
            if rounding == "truncate":
                cut = _number(actual).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN)
                return cut == _number(expected)
            return abs(_number(actual) - _number(expected)) <= Decimal(5).scaleb(-precision - 1)
    except (ValueError, InvalidOperation):
        return False


def _plain_inputs(value: Any) -> None:
    _require(isinstance(value, dict) and bool(value), "input must be a non-empty mapping")
    _require(len(records.canonical(value)) <= 65536, "input exceeds the bounded protocol")


def _degenerate(value: Any) -> bool:
    """An input made only of 0 and 1 (or of no number at all): it checks an identity point, not a worked value."""
    found: list[Decimal] = []

    def walk(item: Any) -> None:
        if isinstance(item, bool):
            return
        if isinstance(item, (int, str)):
            try:
                number = Decimal(item)
            except InvalidOperation:
                return
            if number.is_finite():
                found.append(number)
        elif isinstance(item, dict):
            for v in item.values():
                walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)
    walk(value)
    return all(number in (0, 1) for number in found)


def _leaves(value: Any) -> list[str]:
    if isinstance(value, list):
        return [x for v in value for x in _leaves(v)]
    if isinstance(value, dict):
        return [x for _, v in sorted(value.items()) for x in _leaves(v)]
    return [value]


def _as_number(text: str) -> Decimal:
    """A number as a source prints it: a decimal, or a power of ten (9.4 × 10^-21, 9.4e-21)."""
    found = _SCIENTIFIC.fullmatch(text)
    with localcontext() as ctx:
        ctx.prec = 160
        if found:
            exponent = found.group(2) or found.group(3)
            return Decimal(found.group(1)).scaleb(int(exponent.replace("−", "-")))
        return Decimal(text.strip().replace("−", "-"))


def _occurs(number: str, text: str) -> bool:
    """Whether ``number`` appears in ``text`` as itself, not inside a longer number (3 is not in 3.375 or 13)."""
    return re.search(r"(?<![0-9.])" + re.escape(number) + r"(?![0-9]|\.[0-9])", text) is not None


def _normal(text: str) -> str:
    return " ".join(text.split())


_QUOTED = re.compile(r"[+\-−]?(?:\d+(?:\.\d*)?|\.\d+)")
#: A number grouped by thousands (22,000 or 1,234,567.5): a comma before exactly three digits, then no further digit.
_GROUPED = re.compile(r"(?<![\d.,])[+\-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])")


def _quoted_numbers(quote: str) -> set[Decimal]:
    """Every number a quote prints, signed and unsigned (a hyphen before a number may be a dash), each piece of a
    comma-grouped number on its own and the grouped number whole (the check only asks whether a case's number
    occurs, so the whole reading adds only a number the quote prints grouped)."""
    found = set()
    for text in _QUOTED.findall(quote) + [g.replace(",", "") for g in _GROUPED.findall(quote)]:
        number = Decimal(text.replace("−", "-"))
        found.update((number, abs(number)))
    return found


def _input_numbers(value: Any) -> list[Decimal]:
    """The numbers in a case's input: each leaf that is an integer or a decimal string (labels and modes are not)."""
    out = []
    for leaf in _leaves(value):
        if isinstance(leaf, bool) or not isinstance(leaf, (int, str)):
            continue
        try:
            number = Decimal(leaf)
        except InvalidOperation:
            continue
        if number.is_finite():
            out.append(number)
    return out


def _quote_holds(case: Mapping[str, Any], values: list[str], texts: tuple[str, ...], quote: str | None = None) -> bool:
    """Whether the case's quote (``quote``, for a withheld one) is in the source's text, at most :data:`QUOTE_MAX`
    characters, and prints every value it names as the source prints it and every number of its input."""
    quote = _normal(case["quote"] if quote is None else quote)
    return (len(quote) <= QUOTE_MAX and any(quote in text for text in texts)
            and all(_occurs(_normal(v), quote) for v in values)
            and set(_input_numbers(case["input"])) <= _quoted_numbers(quote))


def _texts(data: str) -> tuple[str, ...]:
    """A source's text as held, and with any markup taken out (a page's visible text), whitespace-normalised only."""
    return _normal(data), _normal(html.unescape(re.sub(r"<[^>]*>", "", data)))


def _printed(cases: list[dict], by_id: Mapping[str, dict], held: Mapping[str, bytes], prior: dict | None,
             quotes: Mapping[str, str] | None = None) -> list[dict]:
    """For each case its source prints, whether its quote holds (:func:`_quote_holds`): in the clause itself for a
    clause-hashed source, in the held bytes otherwise. Offline, without the bytes, the issuance record stands in. A
    withheld source's quote comes from ``quotes`` (by SHA-256, supplied at issuance), and the row keeps only its hash."""
    rows = []
    for case in cases:
        if not case.get("printed"):
            continue
        entry = by_id[case["source"]]
        named = case.get("printed_as", _leaves(case["expected"]))
        values = named if isinstance(named, list) else [named]
        withheld = "quote_sha256" in case
        row = {"rule": case["rule"], "location": case["location"], "source": case["source"], "values": values,
               **({"quote_sha256": case["quote_sha256"]} if withheld else {"quote": case["quote"]})}
        quote = (quotes or {}).get(case["quote_sha256"]) if withheld else case["quote"]
        if entry.get("hash_scope", "retrieved_bytes") == "clause_utf8":
            texts = _texts(entry["clause"])
        elif case["source"] in held:
            _require(quote is not None, f"{case['rule']}: this run holds the bytes of {case['source']} but not the "
                                        "withheld quote its case hashes: pass it with --quotes")
            texts = _texts(held[case["source"]].decode("utf-8", "replace"))
        else:
            earlier = [r for r in (prior or {}).get("printed_values", [])
                       if {k: r.get(k) for k in row} == row and type(r.get("found")) is bool]
            _require(earlier, f"{case['rule']}: a printed value cannot be rechecked offline without the issuance "
                              "record or the source's bytes")
            rows.append(earlier[0])
            continue
        row["found"] = _quote_holds(case, values, texts, quote)
        rows.append(row)
    return rows


def _rounding(entry: Mapping[str, Any] | None) -> str:
    """How a register entry's source rounds the numbers it prints (half_even when it does not say)."""
    rounding = (entry or {}).get("rounding", "half_even")
    _require(rounding in ROUNDING, "a register entry's rounding is half_even or truncate")
    return rounding


def _case(case: Mapping[str, Any], rule: Mapping[str, Any], rounding: str = "half_even", *,
          withheld: bool = False) -> None:
    """One case of a covered rule, as its tier needs it; ``rounding`` is its register entry's, and ``withheld`` whether
    that entry's text is not published (a printed case then gives its quote's SHA-256, never the quote)."""
    name = case["rule"]
    _require(case.get("source") == rule["source"], "case source is not its rule's entry")
    _require(isinstance(case.get("location"), str) and case["location"].strip(), "case location is missing")
    _plain_inputs(case.get("input"))
    if rule["tier"] == "property":
        # A known value the case author computed (an identity point): a value the source prints makes the rule
        # computes, never property.
        _require(case.get("printed") is False and not {"printed_as", "quote", "quote_sha256"} & set(case),
                 f"{name}: a property rule's known cases are printed: false (a value its source prints makes the "
                 "rule computes)")
    if rule["tier"] == "guard":
        _require(case.get("role") in ROLES and not {"expected", "precision", "printed"} & set(case),
                 f"{name}: a guard case is a role (fires, holds or recall), a location and an input")
        return
    _require("role" not in case, f"{name}: only a guard's cases have roles")
    _precision(case.get("precision"))
    _numeric(case.get("expected"))
    # How the source prints its numbers is the source's, reviewed with its register entry; a case inherits it.
    _require("rounding" not in case, f"{name}: rounding is declared on the register entry the case cites")
    _require(case["expected"] == _format(case["expected"], case["precision"], rounding),
             "expected values must be decimal strings at their printed precision")
    _require(type(case.get("printed")) is bool, f"{name}: a case says whether its source prints its value (printed)")
    _require(case["printed"] or not {"printed_as", "quote", "quote_sha256"} & set(case),
             f"{name}: only a printed case has a quote, a quote_sha256 or printed_as")
    if case["printed"] and withheld:
        _require("quote" not in case and isinstance(case.get("quote_sha256"), str)
                 and re.fullmatch(r"[0-9a-f]{64}", case["quote_sha256"]),
                 f"{name}: a printed case citing a withheld source gives its quote's SHA-256 (quote_sha256), never "
                 "the quote")
    elif case["printed"]:
        _require("quote_sha256" not in case,
                 f"{name}: only a withheld source's printed case gives a quote_sha256 in place of its quote: what can "
                 "be published is published")
        _require(isinstance(case.get("quote"), str) and case["quote"].strip(),
                 f"{name}: a printed case quotes the source verbatim (quote), with every number it rests on")
    if "printed_as" in case:
        named = case["printed_as"] if isinstance(case["printed_as"], list) else [case["printed_as"]]
        leaves = _leaves(case["expected"])
        try:
            values = [_format(str(_as_number(v)), case["precision"], rounding) for v in named
                      if isinstance(v, str)]
        except (InvalidOperation, ValueError):
            values = []
        _require(named and len(values) == len(named) and sorted(values) == sorted(leaves),
                 f"{name}: printed_as names every expected value, as the source prints it")


def load(package: str, *, source_files: Mapping[str, bytes] | None = None, quotes: list[str] | None = None,
         review: bool = True) -> Bundle:
    """Validate committed inputs without running the component.

    source_files maps register ids to owner-held bytes for corpus/retrieved hashes.
    Those bytes are rehashed, never copied into the certificate or wheel. quotes are
    the withheld quotes of sources whose text is not published (register entry
    ``withheld: true``, owner 2026-10-25): each is matched to its case by SHA-256,
    checked in the held bytes and never written. With review=False the independent
    pass is neither needed nor read (a precheck of everything mechanical before a
    reader is asked); such a bundle is never written.
    """
    root, folder, declaration = records.layout(package, allow_missing_review=not review)
    cert, inherits = records.certificate_dir(root, folder)
    config = records.read_json(cert / "checks.json")
    for name in ("producer", "known_author", "route_author"):
        _require(isinstance(config.get(name), str) and config[name].strip(), f"missing recorded {name}")
    _require("implementations" not in config, "each rule's implementation is its Rule's implements, not checks.json")
    scope = records.read_json(folder / "certificate/scope.json").get("not_checked")
    _require(isinstance(scope, list) and scope and all(isinstance(s, str) and s.strip() for s in scope),
             "scope must name what was not checked")
    rules = records.rules_from(records.rule_path(root, declaration))
    covered = records.covered(root, folder, declaration, rules)
    _require(any(rule["kind"] == "register" for rule in covered.values()), "no register rules")
    entries = records.read_json(records.inside(root, declaration["register"]))["entries"]
    _require(isinstance(entries, list) and len({e["id"] for e in entries}) == len(entries), "duplicate source id")
    _require(all(e["id"] != records.OWN_THRESHOLD for e in entries),
             f'a register entry named "{records.OWN_THRESHOLD}" would pass as G6\'s own threshold (no source hash, '
             "no staleness): name it after its source")
    by_id = {e["id"]: e for e in entries}
    held = dict(source_files or {})
    unknown = sorted(set(held) - set(by_id))
    _require(not unknown, f"source bytes name an unknown source id: {', '.join(unknown)}")
    for entry in entries:
        _require(all(entry.get(k) not in (None, "", []) for k in records.FIELDS), "source register field missing")
        _require(all(isinstance(entry[k], str) and entry[k].strip()
                     for k in records.FIELDS if k != "review_months"), "source register text field malformed")
        records.due(entry)
        _rounding(entry)
        _require(type(entry.get("withheld", False)) is bool, f"{entry['id']}: withheld is true or false")
        _require(not entry.get("withheld") or entry.get("hash_scope", "retrieved_bytes") != "clause_utf8",
                 f"{entry['id']}: a withheld source is checked in its held bytes, never in its clause (the clause is "
                 "the register's own words)")
    supplied = {records.sha(q.encode("utf-8")): q for q in quotes or ()}
    prior = None
    path = folder / "certificate/certificate.json"
    needs_record = any(e.get("hash_scope", "retrieved_bytes") != "clause_utf8" and e["id"] not in held
                       for e in entries)
    if path.is_file() and needs_record:
        prior = records.read_json(path)
        _require(prior.get("digest") == records.digest(prior) and prior.get("component") == package
                 and prior.get("schema") == records.SCHEMA, "source issuance certificate is invalid")
    checked = records.source_checks(entries, held, prior)
    for name, rule in covered.items():
        _require(rule["source"] in by_id or (rule["tier"] == "guard" and rule["source"] == records.OWN_THRESHOLD),
                 f"{name}: source missing from register")
    raw = records.read_json(cert / "cases.json").get("cases")
    _require(isinstance(raw, list) and 0 < len(raw) <= MAX_CASES, "source cases are missing or excessive")
    tiered = {n for n, r in rules.items() if r["tier"]}
    cases = []
    for case in raw:
        _require(isinstance(case, dict) and case.get("rule") in tiered, "case names an unknown rule")
        if case["rule"] in covered:  # a shared set may hold another member's rules
            _case(case, covered[case["rule"]], _rounding(by_id.get(case.get("source"))),
                  withheld=bool(by_id.get(case.get("source"), {}).get("withheld")))
            cases.append(case)
    _require(cases, "every computes rule needs a source case")
    hashed = {c["quote_sha256"] for c in cases if "quote_sha256" in c}
    stray = sorted(sha for sha in supplied if sha not in hashed)
    _require(not stray, f"a supplied withheld quote matches no case's quote_sha256 ({', '.join(s[:12] for s in stray)}): "
                        "each quote must be the exact text its case author hashed")
    for name, rule in covered.items():
        mine = [c for c in cases if c["rule"] == name]
        if rule["tier"] == "computes":
            _require(any(c["printed"] and not _degenerate(c["input"]) for c in mine),
                     f"{name}: a computes rule needs a source case printed in its source whose input is not "
                     "degenerate (not only 0, 1 or no number)")
        elif rule["tier"] == "guard":
            _require({c["role"] for c in mine} == set(ROLES),
                     f"{name}: a guard needs a case that fires at its threshold, one that holds just inside it and "
                     "a recall set")
    files = records.input_files(root, folder, declaration)
    for name, rule in covered.items():
        file, function = records.implementation(rule)
        _require(file in files and "/certificate/" not in file, f"{name}: implementation is outside the artifact")
        tree = ast.parse(files[file].decode("utf-8"))
        _require(any(isinstance(n, ast.FunctionDef) and n.name == function for n in tree.body),
                 f"{name}: implements must name a top-level function")
    certificate = cert.relative_to(root).as_posix() + "/"
    adapter = config.get("adapter")
    if adapter:
        _require(adapter in files and adapter.startswith(certificate) and adapter.endswith(".py"),
                 "candidate adapter must be a certificate Python input")
        _adapter_imports(files[adapter])
    refusals = config.get("refusals", [])
    _require(isinstance(refusals, list) and all(isinstance(r, str) and r.isidentifier() for r in refusals)
             and (adapter or not refusals), "refusals name the exception classes a guard's adapter reads as firing")
    proofs = config.get("proofs", {})
    _require(isinstance(proofs, dict)
             and all(covered.get(r, {}).get("tier") == "computes" for r in proofs), "proof names an unknown rule")
    for proof_path in proofs.values():
        _require(proof_path in files and "/certificate/" in proof_path, "proof must be a certificate input")
        proof = records.read_json(records.inside(root, proof_path))
        _require(isinstance(proof.get("author"), str) and proof["author"].strip(), "proof needs an author")
        reference = proof.get("reference", {})
        _require(isinstance(reference, dict) and reference.get("file") in files
                 and "/certificate/" in reference["file"] and isinstance(reference.get("function"), str)
                 and reference["function"].isidentifier(), "proof needs an independent reference file/function")
        _independent(records.inside(root, reference["file"]))
        _require(isinstance(proof.get("parameters"), list) and proof["parameters"]
                 and isinstance(proof.get("expression"), str) and proof["expression"].strip(),
                 "proof needs integer parameters and an SMT-LIB expression")
    _independent(cert / "second_route.py")
    _independent(cert / "falsify.py")
    falsify = config.get("falsification", {})
    seeds = falsify.get("seeds")
    _require(isinstance(seeds, list) and 0 < len(seeds) <= 32
             and all(type(s) is int and 0 <= s < 2**32 for s in seeds)
             and len(set(seeds)) == len(seeds), "falsification requires unique recorded seeds")
    _require(type(falsify.get("count")) is int and 1 <= falsify["count"] * len(seeds) <= MAX_CASES,
             "falsification count outside bound")
    _precision(falsify.get("precision"))
    _require(isinstance(falsify.get("bounds"), str) and falsify["bounds"].strip(), "falsification needs declared bounds")
    boundaries = falsify.get("boundaries", {})
    _require(isinstance(boundaries, dict)
             and set(boundaries) <= {n for n, r in rules.items() if r["tier"] in ("computes", "property")},
             "extra boundary inputs name computes or property rules")
    for group in boundaries.values():
        _require(isinstance(group, list) and 0 < len(group) <= MAX_CASES, "falsification needs boundary inputs")
        for inputs in group:
            _plain_inputs(inputs)
    cited = {r["source"] for r in covered.values() if r["source"] != records.OWN_THRESHOLD}
    imported = tuple(records.imported_code(root, folder, declaration, files))
    if not review:
        unread = {"sha256": None, "reviewer": None, "reviewed": records.reviewed(package),
                  "cases_author": config["known_author"], "coverage": sorted(covered), "code_matches": None}
        return Bundle(package, root, folder, declaration, files, rules, covered,
                      sorted((by_id[s] for s in cited), key=lambda e: e["id"]), cases, config, cert, inherits, scope,
                      unread, [], [], held, checked, _printed(cases, by_id, held, prior, supplied), imported)
    review_path = records.inside(root, declaration["independent_pass"])
    review = records.read_json(review_path)
    _require(isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
             and review["reviewer"] != config["producer"], "independent pass must name a different reader")
    findings = review.get("rules", [])
    _require(isinstance(findings, list) and len(findings) == len(covered)
             and {r.get("rule") for r in findings} == set(covered), "independent pass rule coverage differs")
    context_only, attested = [], []
    for finding in findings:
        name = finding["rule"]
        rule = covered[name]
        _require(_FINDING <= set(finding) <= _FINDING | _ATTESTATIONS,
                 f"{name}: an independent-pass finding records rule, clause_fair, code_matches, source_sha256 and "
                 "finding (the hashes are recomputed at issuance)")
        _require(finding["code_matches"] is True and isinstance(finding["finding"], str) and finding["finding"].strip(),
                 f"{name}: independent pass must have read the code and found that it matches")
        if rule["source"] == records.OWN_THRESHOLD:
            _require(finding["source_sha256"] is None and finding["clause_fair"] is None,
                     f"{name}: a guard on G6's own threshold has no source clause for the reader to hash or judge")
        else:
            # The reader's own hash of the source bytes it read; otherwise nothing records that it held them.
            _require(finding["source_sha256"] == by_id[rule["source"]]["sha256"],
                     f"{name}: independent pass must record the source hash it computed from the bytes")
            if finding["clause_fair"] == records.CONTEXT_ONLY:
                _require(finding.get("context_attested") is True and isinstance(finding.get("reason"), str)
                         and finding["reason"].strip(),
                         f"{name}: a clause read by context only needs the reader's attestation and reason")
                context_only.append({"rule": name, "context_attested": True, "reason": finding["reason"]})
            else:
                _require(finding["clause_fair"] is True,
                         f"{name}: independent pass must have read every clause and found code matches")
        if rule["tier"] == "property":
            _require(finding.get("tier_justified") is True,
                     f"{name}: a property rule needs the reader's word that no printed value checks it "
                     "(tier_justified)")
            _require(isinstance(finding.get("tier_reason"), str) and finding["tier_reason"].strip(),
                     f"{name}: a property rule's attestation says why no printed value checks it (tier_reason)")
            attested.append({"rule": name, "tier": "property", "reason": finding["tier_reason"]})
        if rule["tier"] == "guard":
            # The cases see only whether the guard fired: the reader's word covers what else its code does.
            _require(finding.get("effect_only") is True and isinstance(finding.get("effect_reason"), str)
                     and finding["effect_reason"].strip(),
                     f"{name}: a guard needs the reader's word that its code does only what its effect says "
                     "(effect_only, with effect_reason)")
            attested.append({"rule": name, "tier": "guard", "reason": finding["effect_reason"]})
    reviewed = records.reviewed(package)
    _require(review.get("reviewed") == reviewed, "independent pass reviewed hashes differ from current bytes")
    independent = {"sha256": records.sha(review_path.read_bytes()), "reviewer": review["reviewer"],
                   "reviewed": reviewed, "cases_author": config["known_author"],
                   "coverage": sorted(covered), "code_matches": all(r["code_matches"] is True for r in findings)}
    return Bundle(package, root, folder, declaration, files, rules, covered,
                  sorted((by_id[s] for s in cited), key=lambda e: e["id"]),
                  cases, config, cert, inherits, scope, independent, sorted(context_only, key=lambda r: r["rule"]),
                  sorted(attested, key=lambda r: r["rule"]), held, checked, _printed(cases, by_id, held, prior, supplied),
                  imported)


def _relative(bundle: Bundle, name: str, *, own: bool = False) -> str:
    folder = bundle.folder / "certificate" if own else bundle.cert
    return (folder / name).relative_to(bundle.root).as_posix()


def check_contents(bundle: Bundle) -> dict[str, dict[str, bytes]]:
    """The exact frozen inputs for each check, used by the history resolver too."""
    frozen = {_relative(bundle, n): bundle.files[_relative(bundle, n)]
              for n in ("cases.json", "checks.json", "second_route.py", "falsify.py")}
    for name in ("checks.json", "scope.json"):
        frozen[_relative(bundle, name, own=True)] = bundle.files[_relative(bundle, name, own=True)]
    if bundle.config.get("adapter"):
        path = bundle.config["adapter"]
        frozen[path] = bundle.files[path]
    rule = records.rule_path(bundle.root, bundle.declaration).relative_to(bundle.root).as_posix()
    frozen[rule] = bundle.files[rule]
    # Changing the harness comparisons cannot inherit their old freeze position.
    frozen.update(records.harness_files())
    result = {f"{rule}:{kind}": dict(frozen) for rule in sorted(bundle.covered) for kind in _kinds(bundle, rule)}
    for rule, path in bundle.config.get("proofs", {}).items():
        proof = records.read_json(records.inside(bundle.root, path))
        reference_path = proof["reference"]["file"]
        result[f"{rule}:formal"] = {**frozen, path: bundle.files[path], reference_path: bundle.files[reference_path]}
    return result


def _kinds(bundle: Bundle, rule: str) -> tuple[str, ...]:
    return records.check_kinds(bundle.covered[rule]["tier"], any(c["rule"] == rule for c in bundle.cases))


def _check_sha(name: str, files: Mapping[str, bytes]) -> str:
    return records.sha(name.encode("utf-8") + b"\n" + records.artifact_bytes(files))


def _values(found: dict | None, count: int) -> list | None:
    if found is None or not isinstance(found.get("values"), list) or len(found["values"]) != count:
        return None
    values = found["values"]
    if not all(isinstance(v, dict) and (set(v) == {"value"} or set(v) == {"error"}) for v in values):
        return None
    return values


def _measure(values: list | None, expected: list, precisions: list[int],
             roundings: list[str] | None = None) -> tuple[str, list]:
    """pass, fail or unknown. A value that differs from what is expected is a failure even when other rows raised
    (a wrong value is observed, whatever else crashed); rows that only raised, or no observation at all, are unknown
    and never count as a failure, nor as a killed seeded defect."""
    roundings = roundings or ["half_even"] * len(expected)
    if values is None:
        return "unknown", []
    if any("error" in value for value in values):
        wrong = any("value" in value and not _close(value["value"], wanted, precision, rounding)
                    for value, wanted, precision, rounding in zip(values, expected, precisions, roundings))
        return ("fail" if wrong else "unknown"), []
    rows, passed = [], True
    for value, wanted, precision, rounding in zip(values, expected, precisions, roundings):
        good = "value" in value and _close(value["value"], wanted, precision, rounding)
        passed = passed and good
        try:
            actual = _format(value["value"], precision, rounding) if "value" in value else None
        except (ValueError, InvalidOperation):
            actual = None
        rows.append({"actual": actual, "expected": _format(wanted, precision, rounding), "precision": precision,
                     "passed": good})
    return ("pass" if passed else "fail"), rows


def _expected(case: Mapping[str, Any]) -> Any:
    return ROLES[case["role"]] if "role" in case else case["expected"]


def _case_rounding(bundle: Bundle, case: Mapping[str, Any]) -> str:
    return _rounding(next((e for e in bundle.register if e["id"] == case["source"]), None))


def _case_precision(case: Mapping[str, Any]) -> int:
    return 0 if "role" in case else case["precision"]


def _target_values(bundle: Bundle, rule: str, inputs: list, files: Mapping[str, bytes]) -> list | None:
    file, function = records.implementation(bundle.covered[rule])
    selected = {k: v for k, v in files.items() if "/certificate/" not in k}
    adapter = bundle.config.get("adapter")
    if adapter:
        selected[adapter] = bundle.files[adapter]
    refusals = bundle.config.get("refusals", []) if bundle.covered[rule]["tier"] == "guard" else []
    return _values(observe(selected, "candidate", file=file, function=function, adapter=adapter, rule=rule,
                           inputs=inputs, refusals=refusals), len(inputs))


def _reference_values(bundle: Bundle, rule: str, inputs: list) -> list | None:
    path = _relative(bundle, "second_route.py")
    return _values(observe({path: bundle.files[path]}, "reference", file=path, rule=rule, inputs=inputs), len(inputs))


def _generated(bundle: Bundle, rule: str) -> list | None:
    """Any declared boundary inputs beyond the cases, then the seeded generator's inputs (the cases themselves are
    always compared too)."""
    cfg, path = bundle.config["falsification"], _relative(bundle, "falsify.py")
    inputs = list(cfg.get("boundaries", {}).get(rule, []))
    for seed in cfg["seeds"]:
        found = observe({path: bundle.files[path]}, "generate", file=path, rule=rule, seed=seed, count=cfg["count"])
        if not found or not isinstance(found.get("inputs"), list) or len(found["inputs"]) != cfg["count"]:
            return None
        try:
            for item in found["inputs"]:
                _plain_inputs(item)
        except ValueError:
            return None
        inputs.extend(found["inputs"])
    return inputs


def _invariants(bundle: Bundle, rule: str, inputs: list, values: list | None) -> bool | None:
    if values is None or any("value" not in v for v in values):
        return None
    path = _relative(bundle, "falsify.py")
    found = observe({path: bundle.files[path]}, "invariants", file=path, rule=rule,
                    inputs=inputs, values=[v["value"] for v in values])
    if not found or not isinstance(found.get("invariants"), list) or len(found["invariants"]) != len(inputs):
        return None
    if not all(type(v) is bool for v in found["invariants"]):
        return None
    return all(found["invariants"])


def _rule_runs(bundle: Bundle, rule: str, files: Mapping[str, bytes], generated: list | None,
               reference: list | None) -> tuple[dict, dict]:
    cases = [c for c in bundle.cases if c["rule"] == rule]
    kinds = _kinds(bundle, rule)
    inputs = [c["input"] for c in cases] + (generated or [])
    values = _target_values(bundle, rule, inputs, files)
    results, rows = {}, {}
    if "known" in kinds:
        results["known"], rows["known"] = _measure(values[:len(cases)] if values is not None else None,
                                                   [_expected(c) for c in cases], [_case_precision(c) for c in cases],
                                                   [_case_rounding(bundle, c) for c in cases])
    if "second_route" in kinds:
        second, second_rows, invariant = "unknown", [], None
        if generated is not None and reference is not None and all("value" in v for v in reference):
            precision = [c["precision"] for c in cases] + [bundle.config["falsification"]["precision"]] * len(generated)
            second, second_rows = _measure(values, [v["value"] for v in reference], precision)
            invariant = _invariants(bundle, rule, inputs, values)
            if invariant is False:
                second = "fail"
            elif invariant is None and second == "pass":
                second = "unknown"
        results["second_route"], rows["second_route"], rows["invariants"] = second, second_rows, invariant
    return results, rows


def removal(source: str, function: str) -> str:
    """G6's seeded defect for a guard: the implementing function does nothing (its body is ``return None``)."""
    tree = ast.parse(source)
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    target.body = [ast.Return(value=ast.Constant(value=None))]
    return ast.unparse(ast.fix_missing_locations(tree))


_TWIN = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}


def _numeric_literal(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value != 0


def _scaled(value: int | float) -> list[int | float]:
    """A threshold doubled and halved; a threshold of zero moved to one either side."""
    if value == 0:
        return [type(value)(1), type(value)(-1)]
    return [value * 2, value // 2 if type(value) is int and value % 2 == 0 else value / 2]


def boundary(source: str, function: str) -> list[str]:
    """G6's seeded boundary defects for a guard, each one change inside ``function``: a comparison flipped at its
    boundary (< and <=, > and >=), a number it compares against doubled and halved (zero moved to one either side),
    and a module constant it names (a number assigned at the module's top level: a tolerance, a limit) doubled and
    halved. A guard whose cases sit at its threshold catches them; cases far from it catch only the guard's
    removal."""
    tree = ast.parse(source)
    constants = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, ast.AnnAssign) else []
        if _numeric_literal(getattr(node, "value", None)):
            constants.update({t.id: node.value.value for t in targets if isinstance(t, ast.Name)})
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    inside = {id(n) for n in ast.walk(target)}
    compared = {id(n) for c in ast.walk(target) if isinstance(c, ast.Compare) for n in ast.walk(c)}
    nodes = list(ast.walk(tree))
    found, seen = [], {ast.unparse(tree)}

    def changed(index: int, change) -> None:
        copy_tree = copy.deepcopy(tree)
        copied = list(ast.walk(copy_tree))
        node = copied[index]
        replacement = change(node)
        if replacement is not node:
            for parent in copied:
                for name, value in ast.iter_fields(parent):
                    if value is node:
                        setattr(parent, name, replacement)
                    elif isinstance(value, list):
                        setattr(parent, name, [replacement if v is node else v for v in value])
        text = ast.unparse(ast.fix_missing_locations(copy_tree))
        if text not in seen:
            seen.add(text)
            found.append(text)

    for index, node in enumerate(nodes):
        if id(node) not in inside:
            continue
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                if type(op) in _TWIN:
                    def flip(n, k=k):
                        n.ops[k] = _TWIN[type(n.ops[k])]()
                        return n
                    changed(index, flip)
        elif (isinstance(node, ast.Constant) and type(node.value) in (int, float)) and id(node) in compared:
            for value in _scaled(node.value):
                changed(index, lambda n, value=value: ast.Constant(value=value))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in constants:
            for value in _scaled(constants[node.id]):
                changed(index, lambda n, value=value: ast.Constant(value=value))
    if len(found) <= MAX_MUTANTS:
        return found
    step = len(found) / MAX_MUTANTS
    return [found[int(i * step)] for i in range(MAX_MUTANTS)]


def issue(bundle: Bundle, positions: Positions, *, today: dt.date | None = None) -> dict:
    """Run checks and G6-seeded controls; the sole issuer decides the returned state. A check counts as independent
    only when every child the issue started ran confined by the operating system (``g6.core.confine``): one that ran
    with the owner's permissions could have changed what G6 reads. The record's ``isolation`` says which.

    Fresh issuance requires the full bytes of every source hashed over a page or a file.
    """
    with confine.recording() as runs:
        return _issue(bundle, positions, runs, today=today, recheck=False)


def _reissue(bundle: Bundle, positions: Positions, *, today: dt.date | None = None,
             isolation: Any = None) -> dict:
    """The offline rerun that verify and the tool's --check compare with a committed record.

    It needs no source bytes, so its record is never written unless the bytes were held (write). ``isolation``, the
    record's, stands for how the issuer's children were confined: this machine's confinement is its own (Windows only,
    after the one-time grant), so every observation reruns here and the checks' kinds are decided as at issuance.
    """
    with confine.recording() as runs:
        return _issue(bundle, positions, runs, today=today, recheck=True, stated=isolation)


def _issue(bundle: Bundle, positions: Positions, runs: list[dict], *, today: dt.date | None, recheck: bool,
           stated: Any = None) -> dict:
    day = today or dt.date.today()
    if not recheck:
        _require(all(row["id"] in bundle.source_files for row in bundle.source_bytes_required_at_issuance),
                 "fresh issuance requires source bytes")
    contents = check_contents(bundle)
    _require(set(positions.frozen) == set(contents), "positions must cover every frozen check")
    for value in [positions.artifact, positions.controls, positions.positive, positions.receipts,
                  *positions.frozen.values()]:
        _require(type(value) is int and value >= 0, "positions must be non-negative integers")
    _require(positions.controls >= max(positions.frozen.values())
             and positions.controls < positions.positive < positions.receipts
             and positions.artifact <= positions.controls, "control/positive/receipt positions are out of order")
    code = records.code_files(bundle.files, bundle.declaration)
    artifact = issuer.Artifact(records.sha(records.artifact_bytes(code)), bundle.config["producer"], positions.artifact)
    checks, receipts, observations, falsification, gaps, unmoved = [], [], {}, {}, [], []
    stale =[e["id"] for e in bundle.register if day > records.due(e)]
    cfg = bundle.config["falsification"]
    for rule_index, rule in enumerate(sorted(bundle.covered)):
        offset = rule_index * (positions.receipts - positions.controls + 1)
        tier = bundle.covered[rule]["tier"]
        routed = "second_route" in records.CHECK_KINDS[tier]
        cases = [c for c in bundle.cases if c["rule"] == rule]
        generated = _generated(bundle, rule) if routed else []
        inputs = [c["input"] for c in cases] + (generated or [])
        reference = _reference_values(bundle, rule, inputs) if routed else None
        if routed:
            falsification[rule] = {"seeds": cfg["seeds"], "count": len(generated) if generated is not None else 0,
                                   "requested_per_seed": cfg["count"], "bounds": cfg["bounds"],
                                   "inputs": generated or [], "precision": cfg["precision"]}
        proof_path = bundle.config.get("proofs", {}).get(rule)
        proof = records.read_json(records.inside(bundle.root, proof_path)) if proof_path else None
        controls = {kind: [] for kind in _kinds(bundle, rule)}
        if proof:
            controls["formal"] = []
        file, function_name = records.implementation(bundle.covered[rule])
        source = bundle.files[file].decode("utf-8")
        function = next(n for n in ast.parse(source).body
                        if isinstance(n, ast.FunctionDef) and n.name == function_name)
        candidates = [mutant.source for mutant in mutation_check.mutants(
            source, MAX_MUTANTS, lines=range(function.lineno, function.end_lineno + 1), calls=True)]
        labels = ["mutant"] * len(candidates)
        pinned = None  # the seeded defect a caught one of shows the check tests the rule: any, but for a guard
        if tier == "guard":
            # A guard's removal, then its boundary moved: only a caught boundary change shows the cases pin the
            # threshold (the removal alone is caught by cases anywhere on the firing side). A guard with no threshold
            # to move rests on its caught removal, a named gap (THRESHOLD_GATE).
            edges = boundary(source, function_name)
            candidates = [removal(source, function_name), *edges, *(c for c in candidates if c not in edges)]
            labels = ["removal"] + ["boundary"] * len(edges) + ["mutant"] * (len(candidates) - len(edges) - 1)
            pinned = ":boundary:" if edges else ":removal:"
            if not edges:
                unmoved.append(rule)
        removed = None
        for index, mutant in enumerate(candidates):
            changed = dict(bundle.files)
            changed[file] = mutant.encode("utf-8")
            results, rows = _rule_runs(bundle, rule, changed, generated, reference)
            if tier == "guard" and index == 0:
                removed = rows["known"]
            if proof:
                results["formal"] = _certificate_formal.check(mutant, function_name, proof)
            for kind in controls:
                check_id = f"{rule}:{kind}"
                label = labels[index]
                controls[kind].append(issuer.Control(
                    f"{check_id}:{label}:{index}:{records.sha(changed[file])}",
                    _check_sha(check_id, contents[check_id]), rule, issuer.G6, positions.controls + offset,
                    results[kind] == "fail"))
        # The recorded chronology is also the execution chronology: controls,
        # fresh positive reference, then receipts on the unchanged artifact.
        positive = {}
        positive_reference = None
        if routed:
            positive_reference = _reference_values(bundle, rule, inputs)
            _require(positive_reference == reference, "second route changed between control and positive runs")
            known_positive, _ = _measure(positive_reference[:len(cases)] if positive_reference is not None else None,
                                         [c["expected"] for c in cases], [c["precision"] for c in cases],
                                         [_case_rounding(bundle, c) for c in cases])
            if "known" in controls:  # a computes rule, or a property rule with known cases
                positive["known"] = known_positive
            positive["second_route"] = "unknown"
            if generated is not None and positive_reference is not None:
                precision = [c["precision"] for c in cases] + [cfg["precision"]] * len(generated)
                if all("value" in value for value in positive_reference):
                    agreement, _ = _measure(positive_reference, [v["value"] for v in positive_reference], precision)
                    invariant = _invariants(bundle, rule, inputs, positive_reference)
                    positive["second_route"] = ("pass" if known_positive == agreement == "pass" and invariant is True
                                                else "fail" if invariant is False else "unknown")
        if proof:
            reference_spec = proof["reference"]
            positive["formal"] = _certificate_formal.check(
                bundle.files[reference_spec["file"]].decode("utf-8"), reference_spec["function"], proof)
        outcomes, rows = _rule_runs(bundle, rule, bundle.files, generated, positive_reference)
        if proof:
            outcomes["formal"] = _certificate_formal.check(source, function_name, proof)
        observations[rule] = rows
        for kind in controls:
            name = f"{rule}:{kind}"
            author = proof["author"] if kind == "formal" else bundle.config[
                "known_author" if kind == "known" else "route_author"]
            frozen = positions.frozen[name]
            independent = author != artifact.producer and frozen < artifact.seq
            # A guard's cases have no independent reference to calibrate them, so a failure there is unresolved,
            # never a refutation.
            calibrated = positive.get(kind) == "pass"
            check = issuer.Check(name, (rule,), author,
                                 "formal_checked" if kind == "formal" and independent else (
                                     "independent_executable" if independent else "partial_falsifier"),
                                 _check_sha(name, contents[name]), frozen, tuple(controls[kind]),
                                 certificate=records.sha(bundle.files[proof_path]) if kind == "formal" else "",
                                 positive_seq=positions.positive + offset if calibrated else None)
            checks.append(check)
            receipts.append(issuer.Receipt(name + ":receipt", name, check.check_sha, artifact.sha256,
                                           outcomes[kind], positions.receipts + offset))
            if not any(c.killed for c in controls[kind] if pinned is None or pinned in c.control_id):
                gaps.append((name, "no_seeded_defect"))
            if kind in positive and not calibrated:
                gaps.append((name, "positive_reference_not_passed"))
        if tier == "guard":
            firing = [i for i, c in enumerate(cases) if c["role"] == "fires"]
            if not removed or not any(not removed[i]["passed"] for i in firing):
                gaps.append((f"{rule}:known", "guard_removal_not_caught"))
        if any(row["rule"] == rule and not row["found"] for row in bundle.printed):
            gaps.append((f"{rule}:known", "printed_value_not_in_source"))
    # only what a reissue gives again: each run's own container (its SID and profile) is new every time
    isolation = {key: confine.summary(runs)[key] for key in ("backend", "why", "network")}
    # confine's reason names the issuing machine's folders (and so its user); a committed certificate keeps neither
    if isinstance(isolation["why"], str):
        isolation["why"] = _FOLDER.sub("<folder>", isolation["why"])
    if recheck and isinstance(stated, Mapping) and set(stated) == set(isolation):
        isolation = dict(stated)
    if not isolation["backend"]:  # a run with the owner's permissions: every check can refute, none can support
        checks = [replace(check, kind="partial_falsifier") for check in checks]
    # One passing check cannot mask another check's missing controls.
    gaps += [(s, "stale_source") for s in stale]
    predicates = tuple(sorted(bundle.covered))
    standard = records.sha(records.canonical({c.check_id: c.check_sha for c in checks}))
    mission = records.sha(records.canonical(bundle.rules))
    # A clause read by context only is a gate nobody decides here: a named gap that holds the state at qualified-draft
    # without a violation, so it does not by itself block listing.
    # A threshold of G6's own choice in a register rule is the same kind of named gap, and so is a guard with no
    # threshold to move.
    gates = tuple([f"{records.CONTEXT_GATE}:{row['rule']}" for row in bundle.context_only]
                  + [f"{records.CHOICE_GATE}:{name}" for name in predicates if bundle.covered[name]["own_choice"]]
                  + [f"{records.THRESHOLD_GATE}:{name}" for name in unmoved])
    facts = issuer.MissionFacts(mission, standard, standard, predicates, predicates, gates=gates,
        violations=tuple((name, reason, name + ": " + reason) for name, reason in gaps), cost_cents=None)
    # EVIDENCE-01: the controls and receipts this issuance ran go to its own ledger, which the issuer resolves them in.
    observed = ChainLedger()
    issuer.record(observed, *(control for check in checks for control in check.controls), *receipts)
    decided = issuer.issue(facts, artifact, checks, receipts, ledger=observed).as_dict()
    body = {"SPDX-License-Identifier": "Apache-2.0", "schema": records.SCHEMA,
            "component": bundle.package, "issuer": decided, "rules": bundle.rules,
            "inherits": bundle.inherits, "imported_code": list(bundle.imported),
            "inputs_sha256": records.sha(records.artifact_bytes(bundle.files)),
            "harness_sha256": {name: records.sha(data) for name, data in records.harness_files().items()},
            "register": bundle.register, records.BYTES_AT_ISSUE: bundle.source_bytes_required_at_issuance,
            "printed_values": bundle.printed, "independent_pass": bundle.independent,
            "context_only": bundle.context_only, "attested": bundle.attested,
            "falsification": falsification, "observations": observations,
            "not_checked": [records.state_line(decided, bundle.covered), *bundle.scope],
            "stale_sources": stale, "positions": asdict(positions), "isolation": isolation,
            # owner, 2026-10-23 (DECISIONS #39): no component needs a qualified person's signature to be listed
            "review": {"required": False,
                       "path": bundle.declaration["signed_review"],
                       "present": records.review_present(bundle.root, bundle.declaration),
                       "binding": "SSH signature over the certificate's content digest (the record without its "
                                  "history positions), namespace g6-certificate; verified when given, never "
                                  "required to list"}}
    body["digest"] = records.digest(body)
    _require(records.input_files(bundle.root, bundle.folder, bundle.declaration) == bundle.files,
             "component inputs changed during execution")
    return body


def verify(bundle: Bundle, body: Mapping[str, Any], *, today: dt.date | None = None) -> list[str]:
    """Re-decide the issuer record, then rerun observations, including seeded defects."""
    problems = []
    try:
        if body.get("digest") != records.digest(body):
            problems.append("component certificate digest differs")
        problems += issuer.verify(body["issuer"], records.artifact_bytes(records.code_files(bundle.files, bundle.declaration)),
                                  mission_sha=records.sha(records.canonical(bundle.rules)),
                                  predicates=sorted(bundle.covered))
        again = _reissue(bundle, Positions(**body["positions"]), today=today, isolation=body.get("isolation"))
        if records.canonical(again) != records.canonical(body):
            problems.append("component certificate differs from offline reissue")
        if body["issuer"]["violations"] or any(r["result"] != "pass" for r in body["issuer"]["receipts"]):
            problems.append("component checks or seeded controls did not all pass")
        if body.get("stale_sources"):
            problems.append("certificate has stale sources")
    except (KeyError, TypeError, ValueError, OSError, SyntaxError, InvalidOperation) as exc:
        problems.append(f"component certificate cannot be rechecked: {exc}")
    return problems


def _tier_note(name: str, body: Mapping[str, Any]) -> str:
    """What a rule's tier rests on beyond its checks, in the words of the record: the reader's attestations and any
    threshold G6 chose."""
    rule = body["rules"][name]
    said = {row["tier"]: row["reason"] for row in body["attested"] if row["rule"] == name}
    note = ""
    if rule["tier"] == "property":
        note += f" (no printed value is checked; the independent reader's attestation: {said['property']})"
    if rule["own_choice"]:
        note += " (G6's own choice: no source sets it)"
    if rule["tier"] == "guard" and rule["source"] == records.OWN_THRESHOLD:
        note += " (G6's own threshold: no source sets it)"
    if name in records.gated(body["issuer"], records.THRESHOLD_GATE):
        note += " (no threshold G6 can move: only its removal tests its cases)"
    if rule["tier"] == "guard":
        note += f" (its code does only what its effect says, by the independent reader's attestation: {said['guard']})"
    return note


def markdown(body: dict) -> bytes:
    """A human-readable view of the complete record, with no additional claim."""
    state = body["issuer"]["state"]
    text = ("<!-- SPDX-License-Identifier: Apache-2.0 -->\n"
            f"# Component certificate: {body['component']}\n\n"
            f"Issuer state: {state}.\n\n"
            "Checks apply only to the recorded bytes, source cases and seeded inputs.\n"
            "A detached SSH signature, when one is given, covers the certificate's content digest (this record without "
            "its history positions) in namespace g6-certificate.\n"
            + "".join(f"Read by context only (a named gap that holds the state at qualified-draft; a signature covers "
                      f"it too): {row['rule']}: {row['reason']}\n" for row in body["context_only"])
            + "\n"
            f"Case values were transcribed by {body['independent_pass']['cases_author']} and checked against "
            f"the source by the independent pass ({body['independent_pass']['reviewer']}); the issuing tool also "
            "searched the source's text for each printed value (printed_values in the record).\n\n")
    withheld = sorted({row["source"] for row in body.get("printed_values", []) if "quote_sha256" in row})
    if withheld:
        text += (f"Quotes from {', '.join(withheld)} are withheld: their text is not published. The record keeps each "
                 "quote's SHA-256 and whether the issuing tool found it, with every value it names, in the source's "
                 "held bytes; a later check without the quote trusts that record.\n\n")
    text += "## Rules by tier\n\n" + "".join(
        f"- {name}: {body['rules'][name]['tier']}{_tier_note(name, body)}\n"
        for name in sorted(body["independent_pass"]["coverage"]))
    if body["context_only"]:
        text += ("\n## Read by context only\n\nThe independent reader found these clauses support their rule only by "
                 "their context, and attested it; each is a named gap:\n\n"
                 + "".join(f"- {row['rule']}: {row['reason']}\n" for row in body["context_only"]))
    text += ("\n## What was not checked\n\n"
             "A deliberately deceptive implementation shares the child process with the harness; "
             "the independent code review is what covers that limitation.\n\n")
    held = [row["id"] for row in body[records.BYTES_AT_ISSUE]]
    if held:
        text += (f"{', '.join(held)}: their full bytes were hashed by the issuing tool at issue; checks without the "
                 "bytes recompute only the clause hash and trust this unsigned record. Nothing here shows that the "
                 "bytes were held beyond that record, or that the source's URL still serves them.\n\n")
    text += "".join(f"- {s}\n" for s in body["not_checked"])
    fence = chr(96) * 3
    text += "\n## Record\n\n" + fence + "json\n" + records.canonical(body).decode("utf-8") + fence + "\n"
    return text.encode("utf-8")


def write(bundle: Bundle, body: dict) -> None:
    """Write two files only, without timestamps or platform newline conversion.

    A record of source bytes is written only by a run that held exactly those bytes.
    """
    _require(bundle.independent.get("sha256"), "cannot write: no independent pass was read (a precheck is never written)")
    recorded = body.get(records.BYTES_AT_ISSUE)
    _require(recorded == bundle.source_bytes_required_at_issuance,
             "cannot write: the record of source bytes differs from this run's sources")
    for row in recorded:
        found = bundle.source_files.get(row["id"])
        _require(found is not None and records.sha(found) == row["sha256"],
                 f"cannot write: this run holds no bytes for source {row['id']}; pass them with --source-file")
    folder = bundle.folder / "certificate"
    folder.mkdir(exist_ok=True)
    (folder / "certificate.json").write_bytes(records.canonical(body))
    (folder / "CERTIFICATE.md").write_bytes(markdown(body))
