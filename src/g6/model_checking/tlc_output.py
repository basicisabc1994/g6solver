# SPDX-License-Identifier: Apache-2.0
"""Reading what TLC and SANY print, in pure Python; nothing here runs a program.

TLC runs in its ``-tool`` mode, where every message is framed ``@!@!@STARTMSG code:severity @!@!@`` ... ``@!@!@ENDMSG
code @!@!@`` and messages can nest. :func:`parse_tlc_output` reads the frames, then decides TLC's verdict from the
messages and the exit code together, failing closed: a verdict is read only when the message and TLC's exit code agree
(0 with "Model checking completed. No error has been found." and the state counts; 12 with an invariant violation; 13
with a temporal or action property; 11 with a deadlock; 10 with a false ASSUME; 14 with a false Assert; 75 to 77 an
evaluation error; 150 and 151 a spec or config error; 152, 153 and 255 TLC failing). Anything else, a run cut off
mid-message included, is ``unrecognised``, never a pass.

Each state of a trace keeps TLC's own text for every variable (``assignments``) and a reading of it as JSON
(``variables``, by :func:`parse_value`): integers and booleans as themselves, a string as a string, a set as
``{"set": [...]}``, a sequence or tuple as a list, a record as an object, a function as ``{"function": [[key, value],
...]}``, a model value as ``{"model_value": name}``, and anything else as ``{"text": ...}``.
"""
from __future__ import annotations

import re
from typing import Any

_START = re.compile(r"@!@!@STARTMSG (\d+):(\d+) @!@!@\Z")
_END = re.compile(r"@!@!@ENDMSG (\d+) @!@!@\Z")
_STATE = re.compile(r"(\d+): (.*)\Z", re.DOTALL)
_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*) = (.*)\Z", re.DOTALL)
_TRACE_HEADERS = {2121, 2264}
_STATE_CODES = {2217, 2218, 2122}

# exit codes (tlc2.output.EC.ExitStatus) and the messages that must come with them
_INVARIANT, _INITIAL, _ACTION, _TEMPORAL, _DEADLOCK, _ASSUME, _ASSERT = 2110, 2107, 2112, 2116, 2114, 2104, 2132
_SUCCESS, _STATS, _DEPTH, _VERSION = 2193, 2199, 2194, 2262
_FAILED = {152, 153, 255, -1, 4294967295}


def _frames(text: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    """The top-level messages (each with its nested children), the lines outside every message, and whether every
    message that opened was closed."""
    top: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    outside: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        start = _START.match(line)
        if start:
            message = {"code": int(start.group(1)), "severity": int(start.group(2)), "lines": [], "children": []}
            (stack[-1]["children"] if stack else top).append(message)
            stack.append(message)
            continue
        end = _END.match(line)
        if end and stack and int(end.group(1)) == stack[-1]["code"]:
            stack.pop()
            continue
        (stack[-1]["lines"] if stack else outside).append(line)
    return top, outside, not stack


def _text(message: dict[str, Any]) -> str:
    parts = ["\n".join(message["lines"]).strip()] + [_text(child) for child in message["children"]]
    return "\n".join(p for p in parts if p)


def _assignments(lines: list[str]) -> dict[str, str]:
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return {}
    if any(ln.startswith("/\\ ") for ln in lines):
        chunks: list[list[str]] = []
        for ln in lines:
            if ln.startswith("/\\ ") or not chunks:
                chunks.append([ln.removeprefix("/\\ ")])
            else:
                chunks[-1].append(ln)
    else:
        chunks = [lines]
    out = {}
    for chunk in chunks:
        match = _ASSIGN.match("\n".join(chunk).strip())
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def _state(number: int, action: str, lines: list[str]) -> dict[str, Any]:
    assigned = _assignments(lines)
    return {"number": number, "action": action.strip().removeprefix("<").removesuffix(">").strip(),
            "variables": {name: parse_value(text) for name, text in assigned.items()}, "assignments": assigned}


def _count(text: str) -> int:
    return int(text.replace(",", ""))


def parse_tlc_output(text: str, returncode: int) -> dict[str, Any]:
    """TLC's ``-tool`` output and exit code, read: its verdict, what it explored, the violation and its trace, and
    the error messages it gave."""
    top, outside, closed = _frames(text or "")
    run: dict[str, Any] = {"verdict": "unrecognised", "version": "", "exit_code": returncode, "violated": "",
                           "trace": [], "trace_end": {}, "states_generated": None, "distinct_states": None,
                           "states_left": None, "depth": None, "fingerprint_collision": "", "errors": [],
                           "sany_errors": _sany_errors("\n".join(outside), "")}
    codes = set()
    for message in top:
        code, body = message["code"], _text(message)
        if code != _SUCCESS or "Model checking completed. No error has been found." in body:
            codes.add(code)  # the success code counts only with TLC's own sentence
        if code == _VERSION and (m := re.search(r"TLC2 Version (.+)", body)):
            run["version"] = m.group(1).strip()
        elif code == _STATS and (m := re.search(r"([\d,]+) states generated, ([\d,]+) distinct states found, "
                                                r"([\d,]+) states left on queue", body)):
            run["states_generated"], run["distinct_states"], run["states_left"] = (_count(g) for g in m.groups())
        elif code == _DEPTH and (m := re.search(r"search is (\d+)", body)):
            run["depth"] = int(m.group(1))
        elif code == _SUCCESS and (m := re.search(r"calculated \(optimistic\):\s*val = (\S+)", body)):
            run["fingerprint_collision"] = m.group(1)
        elif code in (_INVARIANT, _INITIAL) and (m := re.search(r"Invariant (\S+) is violated", body)):
            run["violated"] = m.group(1)
            if code == _INITIAL:
                run["trace"].append(_state(1, "Initial predicate", message["lines"][1:]))
        elif code == _ACTION and (m := re.search(r"Action property (\S+) is violated", body)):
            run["violated"] = m.group(1)
        elif code == _TEMPORAL:
            run["violated"] = "temporal properties"
        if code in _STATE_CODES and (m := _STATE.match("\n".join(message["lines"]).strip())):
            number, rest = int(m.group(1)), m.group(2)
            if code == 2218:
                run["trace_end"] = {"kind": "stuttering", "state": number}
            elif code == 2122:
                run["trace_end"] = {"kind": "back_to_state", "state": number}
            else:
                head, *lines = rest.split("\n")
                run["trace"].append(_state(number, head, lines))
        if message["severity"] == 1 and code not in _TRACE_HEADERS:
            run["errors"].append({"code": code, "text": body[:2000]})
    run["verdict"] = _verdict(run, codes, returncode, closed)
    return run


def _verdict(run: dict[str, Any], codes: set[int], returncode: int, closed: bool) -> str:
    if not closed:
        return "unrecognised"
    expected = {12: bool(codes & {_INVARIANT, _INITIAL}), 13: bool(codes & {_ACTION, _TEMPORAL}),
                11: _DEADLOCK in codes, 10: _ASSUME in codes, 14: _ASSERT in codes}
    names = {12: "invariant_violated", 13: "property_violated", 11: "deadlock", 10: "assumption_violated",
             14: "assertion_failed"}
    if returncode == 0:
        counted = run["states_generated"] is not None and run["distinct_states"] is not None
        return "no_error" if _SUCCESS in codes and not run["errors"] and counted else "unrecognised"
    if _SUCCESS in codes:
        return "unrecognised"
    if returncode in expected:
        return names[returncode] if expected[returncode] else "unrecognised"
    if returncode in (75, 76, 77) and run["errors"]:
        return "evaluation_error"
    if returncode in (150, 151) and run["errors"]:
        return "spec_error"
    if returncode in _FAILED:
        return "failed"
    return "unrecognised"


# --- SANY ------------------------------------------------------------------------------------------------------------

_LOCATION = re.compile(r"line (\d+), col (\d+) to line \d+, col \d+ of module (\w+)\Z")


def _sany_errors(text: str, module: str) -> list[dict[str, Any]]:
    text = text.replace("\r\n", "\n")
    errors = []
    for block in re.findall(r"\*\*\*Parse Error\*\*\*\n(.*?)(?:\n\s*\n|\Z)", text, re.DOTALL):
        where = re.search(r"at line (\d+), column (\d+)", block)
        errors.append({"line": int(where.group(1)) if where else None, "column": int(where.group(2)) if where else None,
                       "module": module, "message": " ".join(ln.strip() for ln in block.split("\n") if ln.strip())})
    for line, column, message in re.findall(r"Lexical error at line (\d+), column (\d+)\.\s*(.*)", text):
        errors.append({"line": int(line), "column": int(column), "module": module, "message": message.strip()})
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for here, after in zip(paragraphs, paragraphs[1:]):
        if where := _LOCATION.match(here):
            errors.append({"line": int(where.group(1)), "column": int(where.group(2)), "module": where.group(3),
                           "message": " ".join(after.split())})
    return errors[:100]


def parse_sany_output(text: str, returncode: int, module: str) -> dict[str, Any]:
    """SANY's output and exit code for ``module``, read. Fails closed: ``no_error`` only with a clean exit, no error
    reported and SANY's own line that it processed ``module``."""
    text = (text or "").replace("\r\n", "\n")
    version = re.search(r"SANY2 Version (.+)", text)
    errors = _sany_errors(text, module)
    marked = any(m in text for m in ("***Parse Error***", "Semantic errors:", "*** Errors:", "Fatal errors",
                                     "Lexical error"))
    processed = bool(module) and f"Semantic processing of module {module}\n" in text + "\n"
    failed = bool(errors) or marked or returncode != 0 or not processed
    if failed and not errors:
        why = (f"SANY exited with code {returncode}" if returncode != 0 else
               f"SANY did not report processing module {module or '(none named)'}" if not processed else
               "SANY reported errors G6 could not place")
        errors = [{"line": None, "column": None, "module": module, "message": why}]
    return {"verdict": "errors" if failed else "no_error", "version": version.group(1).strip() if version else "",
            "module": module, "exit_code": returncode, "errors": errors}


# --- values ----------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|<<|>>|\|->|:>|@@|[\[\]{}(),]|-?\d+|[A-Za-z_][A-Za-z0-9_]*)')
_MAX_NESTING = 100


class _Unread(Exception):
    pass


def _tokens(text: str) -> list[str]:
    out, at = [], 0
    while match := _TOKEN.match(text, at):
        out.append(match.group(1))
        at = match.end()
    if text[at:].strip():
        raise _Unread
    return out


def _string(token: str) -> str:
    body, out, i = token[1:-1], [], 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


class _Reader:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens, self.at = tokens, 0

    def peek(self) -> str:
        return self.tokens[self.at] if self.at < len(self.tokens) else ""

    def take(self, expected: str | None = None) -> str:
        token = self.peek()
        if not token or (expected is not None and token != expected):
            raise _Unread
        self.at += 1
        return token

    def items(self, close: str, depth: int) -> list[Any]:
        out: list[Any] = []
        if self.peek() == close:
            self.take()
            return out
        while True:
            out.append(self.value(depth))
            if self.take() == close:
                return out
            if self.tokens[self.at - 1] != ",":
                raise _Unread

    def value(self, depth: int = 0) -> Any:
        if depth > _MAX_NESTING:
            raise _Unread
        token = self.take()
        if token == "{":
            return {"set": self.items("}", depth + 1)}
        if token == "<<":
            return self.items(">>", depth + 1)
        if token == "[":
            record = {}
            while True:
                name = self.take()
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise _Unread
                self.take("|->")
                record[name] = self.value(depth + 1)
                if self.take() == "]":
                    return record
                if self.tokens[self.at - 1] != ",":
                    raise _Unread
        if token == "(":
            pairs = []
            while True:
                key = self.value(depth + 1)
                self.take(":>")
                pairs.append([key, self.value(depth + 1)])
                if self.take() == ")":
                    return {"function": pairs}
                if self.tokens[self.at - 1] != "@@":
                    raise _Unread
        if token.startswith('"'):
            return _string(token)
        if token in ("TRUE", "FALSE"):
            return token == "TRUE"
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            return {"model_value": token}
        raise _Unread


def parse_value(text: str) -> Any:
    """One TLC value, as JSON (see the module's docstring); ``{"text": text}`` when it is not one G6 reads."""
    try:
        reader = _Reader(_tokens(text))
        value = reader.value()
        if reader.at != len(reader.tokens):
            raise _Unread
        return value
    except _Unread:
        return {"text": text}


__all__ = ["parse_sany_output", "parse_tlc_output", "parse_value"]
