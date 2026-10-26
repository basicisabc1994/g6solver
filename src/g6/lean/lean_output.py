# SPDX-License-Identifier: Apache-2.0
"""The file G6 gives Lean, and reading what Lean reports about it, in pure Python; nothing here runs a program.

:func:`build_file` writes the caller's source unchanged except for one line G6 inserts after its imports,
``#check_failure @_root_.<name>``, which must find no such constant yet (so a theorem from an import cannot pass as the
file's own), and appends G6's block, one command per line:

* ``#print "G6_LEAN_CHECK_<nonce>_BEGIN"``, with a nonce drawn for the run, which the caller cannot know;
* ``#version``: the Lean that checked;
* ``#check @_root_.<name>``: the statement as Lean prints it;
* the same with ``pp.fullNames`` and ``pp.numericTypes``: every name in full and every numeral's type shown;
* ``set_option pp.proofs false in #print _root_.<name>``: its kind, which must be ``theorem``;
* ``#print axioms _root_.<name>``: every axiom it depends on;
* ``example : (<expected statement>) := @_root_.<name>``, when one is given: Lean must accept the theorem as a proof of
  the expected statement read on its own;
* the closing marker.

Lean runs with ``--json``, one message per line with its position. :func:`judge` reads each of G6's answers only from
the message on that command's own line, so text the caller's source prints, on the caller's lines, cannot stand in for
one; both markers must be there with the run's nonce. It fails closed: anything it does not read as one complete,
expected answer is ``G6_E_LEAN_OUTPUT_UNRECOGNISED`` or ``G6_E_LEAN_RUN_FAILED``, never a pass.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from g6.lean.envelope import (
    ELABORATION_FAILED,
    NONSTANDARD_AXIOM,
    NOT_A_THEOREM,
    OUTPUT_UNRECOGNISED,
    RUN_FAILED,
    SORRY,
    STANDARD_AXIOMS,
    STATEMENT_MISMATCH,
    THEOREM_MISSING,
)
from g6.lean.screen import MARK_PREFIX, header_lines

#: G6's markers: ``MARK + nonce + "_BEGIN"`` and ``"_END"``.
MARK = MARK_PREFIX + "_"
FILE_NAME = "Proof.lean"
MAX_MESSAGES = 200
_ANSWERS = ("version", "check", "explicit", "kind", "axioms")
_SEVERITIES = frozenset({"information", "warning", "error"})


def normalise(text: str) -> str:
    """``text`` with every run of whitespace, line breaks included, collapsed to one space."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Layout:
    """Where everything is in the file G6 built: the caller's lines, G6's inserted line and G6's block."""

    theorem: str
    expected: str
    nonce: str
    caller_lines: int
    pre_line: int
    roles: dict[str, int] = field(default_factory=dict)

    def line(self, role: str) -> int | None:
        """The file line of G6's command ``role`` (``pre`` for the inserted one), or ``None`` when there is none."""
        return self.pre_line if role == "pre" else self.roles.get(role)

    def caller_line(self, file_line: int | None) -> int | None:
        """The caller's own line number for a file line, or ``None`` when the file line is not one of the caller's."""
        if file_line is None or file_line == self.pre_line:
            return None
        line = file_line - (1 if file_line > self.pre_line else 0)
        return line if 1 <= line <= self.caller_lines else None

    def file_line(self, caller_line: int) -> int:
        """The file line of the caller's line ``caller_line``: after G6's inserted line, one further down."""
        return caller_line + (1 if caller_line >= self.pre_line else 0)


def build_file(source: str, theorem: str, expected: str, nonce: str) -> tuple[str, Layout]:
    """The text G6 gives Lean for ``source``, and where everything in it is. ``theorem`` must already be a checked
    name and ``expected`` a screened statement (it is written on one line)."""
    body = source.replace("\r\n", "\n")
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    head = min(header_lines(body), len(lines))
    target = f"_root_.{theorem}"
    statement = normalise(expected)
    rows = [("begin", f'#print "{MARK}{nonce}_BEGIN"'), ("version", "#version"), ("check", f"#check @{target}"),
            ("explicit", f"set_option pp.fullNames true in set_option pp.numericTypes true in #check @{target}"),
            ("kind", f"set_option pp.proofs false in #print {target}"), ("axioms", f"#print axioms {target}")]
    if statement:
        rows.append(("expected", f"example : ({statement}) := @{target}"))
    rows.append(("end", f'#print "{MARK}{nonce}_END"'))
    first = len(lines) + 3          # the caller's lines, G6's inserted line, then one blank line
    layout = Layout(theorem=theorem, expected=statement, nonce=nonce, caller_lines=len(lines), pre_line=head + 1,
                    roles={role: first + i for i, (role, _) in enumerate(rows)})
    text = "\n".join([*lines[:head], f"#check_failure @{target}", *lines[head:], "", *(r for _, r in rows)]) + "\n"
    return text, layout


# --- reading Lean's messages -----------------------------------------------------------------------------------------

def _position(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        line, column = value.get("line"), value.get("column")
        if isinstance(line, int) and isinstance(column, int) and not isinstance(line, bool):
            return line, column
    return None, None


def parse_messages(output: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Lean's ``--json`` output, read: its messages (file, line, column, end line and column, severity, kind and text,
    with Lean's own line numbers) and every line that is not one."""
    messages: list[dict[str, Any]] = []
    other: list[str] = []
    for raw in (output or "").replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        line, column = _position(obj.get("pos")) if isinstance(obj, dict) else (None, None)
        if (not isinstance(obj, dict) or obj.get("severity") not in _SEVERITIES or not isinstance(obj.get("data"), str)
                or line is None):
            other.append(raw[:500])
            continue
        end_line, end_column = _position(obj.get("endPos"))
        messages.append({"file": str(obj.get("fileName", "")), "line": line, "column": column, "end_line": end_line,
                         "end_column": end_column, "severity": obj["severity"], "kind": str(obj.get("kind", "")),
                         "text": obj["data"]})
    return messages, other


_CHECK = re.compile(r"\A@?(\S+) : (.+)\Z", re.DOTALL)
_KIND = re.compile(r"\A(?:@\[[^\]\n]*\]\s*)*(?:(?:private|protected|noncomputable|unsafe|partial|nonrec)\s+)*"
                   r"(theorem|def|abbrev|instance|axiom|opaque|inductive|structure|class|constructor|recursor|quot)\s+"
                   r"(\S+?)(?:\.\{[^}]*\})?\s+:", re.DOTALL)
_AXIOMS = re.compile(r"\A'(.+)' (?:does not depend on any axioms|depends on axioms: \[(.*)\])\Z", re.DOTALL)
_VERSION = re.compile(r"\ALean (\d+\.\d+\.\d+\S*)(?:\s*\nTarget: (.+?))?\s*\Z")
_VERSION_FLAG = re.compile(r"Lean \(version (\d+\.\d+\.\d+\S*), ([^,]+), commit ([0-9a-f]+), (\w+)\)")


def parse_check(text: str) -> str | None:
    """The statement in a ``#check @name`` answer (``name : statement``), or ``None``."""
    match = _CHECK.match(text or "")
    return match.group(2) if match else None


def parse_kind(text: str) -> tuple[str, str] | None:
    """The kind and full name in a ``#print name`` answer (``theorem A.foo : ...``), or ``None``."""
    match = _KIND.match(text or "")
    return (match.group(1), match.group(2)) if match else None


def parse_axioms(text: str) -> list[str] | None:
    """The axioms in a ``#print axioms name`` answer (``[]`` for none), or ``None`` when it is not one."""
    match = _AXIOMS.match(normalise(text or ""))
    if not match:
        return None
    return [a.strip() for a in (match.group(2) or "").split(",") if a.strip()]


def parse_version(text: str) -> tuple[str, str] | None:
    """The version and target in a ``#version`` answer, or ``None``."""
    match = _VERSION.match((text or "").replace("\r\n", "\n").strip())
    return (match.group(1), (match.group(2) or "").strip()) if match else None


def parse_version_flag(text: str) -> dict[str, str] | None:
    """What ``lean --version`` prints, read, or ``None``."""
    match = _VERSION_FLAG.search(text or "")
    if not match:
        return None
    return dict(zip(("version", "target", "commit", "build"), match.groups()))


# --- the judgement ---------------------------------------------------------------------------------------------------

def _where(message: dict[str, Any]) -> str:
    place = (f"line {message['line']}, column {message['column']}" if message.get("line") is not None
             else "after the end of your source")
    return f"{place}: {normalise(message['text'])[:300]}"


def judge(output: str, exit_code: int, layout: Layout) -> dict[str, Any]:
    """What Lean's ``output`` and ``exit_code`` show about ``layout.theorem``: ``code`` is ``""`` for a theorem whose
    statement, kind and axioms Lean reported and that passed every check, or the code that blocks it, with the
    ``reason`` and everything read."""
    messages, other = parse_messages(output)
    at: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_line = {line: role for role, line in layout.roles.items()} | {layout.pre_line: "pre"}
    caller: list[dict[str, Any]] = []
    for m in messages:
        role = by_line.get(m["line"])
        if role:
            at[role].append(m)
        else:
            caller.append({**m, "line": layout.caller_line(m["line"]), "end_line": layout.caller_line(m["end_line"])})
    name = layout.theorem
    verdict: dict[str, Any] = {"code": "", "reason": "", "lean_version": "", "lean_target": "", "kind": "",
                               "statement": "", "statement_explicit": "", "statement_matched": None, "axioms": None,
                               "messages": caller[:MAX_MESSAGES]}

    def block(code: str, reason: str) -> dict[str, Any]:
        return verdict | {"code": code, "reason": reason}

    def single(role: str) -> str | None:
        found = at.get(role, [])
        return found[0]["text"] if len(found) == 1 and found[0]["severity"] == "information" else None

    # 1. An error anywhere in the source, or where the source stops (on G6's own lines that are not its answers).
    ended = [{**m, "line": None, "end_line": None} for r in ("begin", "version", "end") for m in at.get(r, [])
             if m["severity"] == "error"]
    errors = [m for m in caller if m["severity"] == "error"] + ended
    if ended:
        verdict["messages"] = (caller + ended)[:MAX_MESSAGES]
    if errors:
        return block(ELABORATION_FAILED, f"Lean reported {len(errors)} error(s) in the source: "
                                         + "; ".join(_where(m) for m in errors[:3]))
    # 2. G6's markers, with this run's nonce, each on its own line.
    if single("begin") != f"{MARK}{layout.nonce}_BEGIN" or single("end") != f"{MARK}{layout.nonce}_END":
        if exit_code != 0 or other:
            tail = "; ".join(other[-3:])[:300]
            return block(RUN_FAILED, f"Lean stopped before G6's checks ran (exit code {exit_code})"
                                     + (f": {tail}" if tail else ""))
        return block(OUTPUT_UNRECOGNISED, "Lean's output does not hold G6's markers for this run where G6 wrote "
                                          "them, so nothing in it is read as an answer")
    # 3. The theorem must not exist before the source, and must exist after it.
    pre = at.get("pre", [])
    if any(m["severity"] == "error" for m in pre):
        return block(THEOREM_MISSING, f"Lean already knows {name} before your source declares anything (G6's "
                                      "#check_failure on it succeeded): it comes from an import, so your file did not "
                                      "prove it")
    if not (len(pre) == 1 and pre[0]["text"].startswith("unknown identifier")):
        return block(OUTPUT_UNRECOGNISED, f"Lean's answer to G6's first check on {name} is not the one G6 reads")
    missing = [m for r in ("check", "explicit", "kind", "axioms") for m in at.get(r, []) if m["severity"] == "error"]
    if missing:
        return block(THEOREM_MISSING, f"Lean does not know a constant named {name} after your source: "
                                      f"{normalise(missing[0]['text'])[:300]}")
    # 4. One answer per check, each read.
    read = {role: single(role) for role in _ANSWERS}
    version = parse_version(read["version"] or "")
    statement, explicit = parse_check(read["check"] or ""), parse_check(read["explicit"] or "")
    kind, axioms = parse_kind(read["kind"] or ""), parse_axioms(read["axioms"] or "")
    unread = [r for r, v in zip(_ANSWERS, (version, statement, explicit, kind, axioms)) if v is None]
    if unread:
        return block(OUTPUT_UNRECOGNISED, f"G6 could not read Lean's answer to its {', '.join(unread)} check(s)")
    verdict |= {"lean_version": version[0], "lean_target": version[1], "kind": kind[0],
                "statement": normalise(statement), "statement_explicit": normalise(explicit), "axioms": axioms}
    if kind[1] != name:
        return block(OUTPUT_UNRECOGNISED, f"Lean's #print answered for {kind[1]}, not {name}")
    if kind[0] != "theorem":
        return block(NOT_A_THEOREM, f"Lean reports {name} as {'an' if kind[0][0] in 'aeiou' else 'a'} {kind[0]}, "
                                    "not a theorem: only a theorem is a proof of its statement")
    # 5. No sorry, and no axiom beyond Lean's standard three.
    sorry = [m for m in messages if m["kind"] == "hasSorry" or "declaration uses 'sorry'" in m["text"]]
    if "sorryAx" in axioms or sorry:
        said = (f"Lean reports that {name} depends on sorryAx: the proof leaves a goal unproved" if "sorryAx" in axioms
                else f"{name} does not depend on sorryAx, but the file leaves a goal unproved")
        if sorry:
            line = layout.caller_line(sorry[0]["line"])
            said += f"; Lean warned 'declaration uses sorry'{f' at line {line}' if line else ''}"
        return block(SORRY, said)
    beyond = [a for a in axioms if a not in STANDARD_AXIOMS]
    if beyond:
        return block(NONSTANDARD_AXIOM, f"{name} depends on {', '.join(beyond)}, beyond Lean's standard axioms "
                                        f"({', '.join(STANDARD_AXIOMS)})")
    # 6. The expected statement, as printed and as Lean reads it.
    if layout.expected:
        if verdict["statement"] != layout.expected:
            verdict["statement_matched"] = False
            return block(STATEMENT_MISMATCH, f"Lean prints the statement of {name} as {verdict['statement']}, not the "
                                             f"expected {layout.expected}")
        refused = [m for m in at.get("expected", []) if m["severity"] == "error"]
        if refused:
            verdict["statement_matched"] = False
            return block(STATEMENT_MISMATCH, f"the expected statement {layout.expected} prints like {name}'s, but Lean "
                                             f"does not accept {name} as a proof of it read on its own: "
                                             f"{normalise(refused[0]['text'])[:400]}")
        verdict["statement_matched"] = True
    elif at.get("expected"):
        return block(OUTPUT_UNRECOGNISED, "Lean reported on an expected-statement line G6 did not write")
    # 7. Nothing else on G6's lines, and a clean exit.
    extra = [r for r in ("pre", *_ANSWERS, "begin", "end") if len(at.get(r, [])) != 1]
    if extra:
        return block(OUTPUT_UNRECOGNISED, f"Lean reported more than G6 asked for on its {', '.join(extra)} line(s)")
    if exit_code != 0:
        return block(OUTPUT_UNRECOGNISED, f"Lean exited with code {exit_code} though it reported no error")
    return verdict


__all__ = ["FILE_NAME", "Layout", "MARK", "build_file", "judge", "normalise", "parse_axioms", "parse_check",
           "parse_kind", "parse_messages", "parse_version", "parse_version_flag"]
