# SPDX-License-Identifier: Apache-2.0
"""Split a SQL script into statements and say what kind each one is, before any of it reaches a database.

The tokenizer knows SQL's quotes and comments: ``'...'`` (with ``''``), ``E'...'`` (with backslash escapes),
``$tag$...$tag$``, ``"..."`` and ``` `...` ``` names, ``[...]`` names, ``--`` and ``/* */``. A semicolon inside any of
them never ends a statement (DATABASE-03: the archive split on ``;``, which broke a string holding one). A script that
cannot be read (a quote or comment never closed, unbalanced brackets, or a ``/*`` inside a block comment, which
PostgreSQL and DuckDB read as a nested comment and SQLite does not) raises :class:`SQLSyntaxError` and none of it runs.

A statement's kind comes from its leading keyword, after a WITH clause's common table expressions, from an allowlist.
Where a form can remove or overwrite rows, it is destructive, so a form G6 does not recognise fails closed:

* ``read``: SELECT, VALUES, TABLE, and DuckDB's FROM-first, DESCRIBE and SUMMARIZE;
* ``write``: INSERT, and UPDATE and DELETE with a WHERE clause of their own and a target table G6 can read, CREATE
  TABLE, VIEW, INDEX, SEQUENCE or SCHEMA (a schema with nothing after its name and owner), and ALTER TABLE that neither
  drops nor retypes anything;
* ``destructive``: DROP, TRUNCATE, any MERGE, REPLACE, INSERT OR REPLACE, UPDATE OR REPLACE, an INSERT that updates on
  conflict (``ON CONFLICT ... DO UPDATE``), a WITH whose common table expressions hold INSERT, UPDATE, DELETE or MERGE,
  DELETE or UPDATE with no WHERE clause of its own (one inside a subquery does not count) or with a target G6 cannot
  read, ALTER TABLE ... DROP, ALTER TABLE ... TYPE or ... USING, and CREATE OR REPLACE TABLE;
* ``refused``: everything else, such as ATTACH, PRAGMA, COPY, INSTALL, LOAD, SET, GRANT, VACUUM, CALL, DO, EXPLAIN
  (the explain op adds it) and transaction control (G6 runs each call in a transaction of its own), CREATE of anything
  but the five kinds above, and CREATE SCHEMA with anything after its name and owner (PostgreSQL runs a GRANT or a
  CREATE given there). These never run.

This is the first check, not the only one: each backend's engine also refuses writes on its read connection and
refuses ATTACH and settings changes, and a change the dry run finds touching every row of a table counts as
destructive whatever its WHERE clause says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["read", "write", "destructive", "refused"]

READ = frozenset({"SELECT", "VALUES", "TABLE", "FROM", "DESCRIBE", "SUMMARIZE"})
CHANGE = frozenset({"INSERT", "REPLACE", "MERGE", "UPDATE", "DELETE"})
_AFTER_WITH = frozenset({"SELECT", "VALUES", "FROM"}) | CHANGE
OBJECTS = frozenset({"TABLE", "VIEW", "INDEX", "SEQUENCE", "SCHEMA"})
_CREATE_WORDS = frozenset({"OR", "REPLACE", "TEMP", "TEMPORARY", "UNIQUE", "IF", "NOT", "EXISTS", "UNLOGGED"})

_REFUSED = {
    "ATTACH": "it opens another database file", "DETACH": "it changes which databases are open",
    "PRAGMA": "it reads or changes the database's own settings", "COPY": "it reads or writes files",
    "EXPORT": "it writes files", "IMPORT": "it reads files", "LOAD": "it loads code into the database",
    "INSTALL": "it downloads code into the database", "SET": "it changes the session's settings",
    "RESET": "it changes the session's settings", "GRANT": "it changes permissions", "REVOKE": "it changes permissions",
    "VACUUM": "it can write a copy of the database to a file", "CALL": "it runs code G6 cannot inspect",
    "DO": "it runs code G6 cannot inspect", "EXECUTE": "it runs code G6 cannot inspect",
    "PREPARE": "it runs code G6 cannot inspect", "EXPLAIN": "the explain op adds EXPLAIN itself, and never ANALYZE",
    "USE": "it changes which database is in use",
}
_TRANSACTION = frozenset({"BEGIN", "COMMIT", "ROLLBACK", "END", "SAVEPOINT", "RELEASE", "START", "ABORT"})
#: Statements that change rows; inside a WITH clause's common table expressions they run too (PostgreSQL).
_ROW_CHANGES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})
#: What may follow CREATE SCHEMA, as the shape :func:`_bare_schema` builds: a name (N) and an owner, nothing else.
_SCHEMA_TAIL = re.compile(r"(?:IF NOT EXISTS )?(?:N(?: \. N){0,2}(?: AUTHORIZATION N)?|AUTHORIZATION N)")

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


class SQLSyntaxError(ValueError):
    """The script cannot be read safely, so none of it runs."""


@dataclass(frozen=True)
class Token:
    kind: str    # "word", "name" (a quoted or bracketed identifier), "string", "number", "punct"
    text: str
    depth: int   # how many brackets enclose it
    start: int
    end: int


@dataclass(frozen=True)
class Statement:
    """One statement as written (without its ``;``), its 1-based place in the script, and what kind it is."""

    index: int
    text: str
    keyword: str
    kind: Kind
    why: str = ""
    table: str = ""   # the table a DELETE or UPDATE names, as written ("" when it names none G6 could read)


def _quoted(script: str, i: int, quote: str, backslash: bool = False) -> int:
    """The index just past the closing ``quote`` of the quoted run starting at ``i``; a doubled quote is one."""
    j = i + 1
    while True:
        k = script.find(quote, j)
        if k < 0:
            raise SQLSyntaxError(f"a {quote} quote opened at character {i + 1} is never closed")
        if backslash and _escaped(script, j, k):
            j = k + 1
            continue
        if script.startswith(quote * 2, k):
            j = k + 2
            continue
        return k + 1


def _escaped(script: str, start: int, k: int) -> bool:
    slashes = 0
    while k - 1 - slashes >= start and script[k - 1 - slashes] == "\\":
        slashes += 1
    return slashes % 2 == 1


def tokens(script: str) -> list[Token]:
    """The script's tokens, without whitespace and comments. Raises :class:`SQLSyntaxError`."""
    out: list[Token] = []
    i, n, depth, brackets = 0, len(script), 0, 0
    while i < n:
        c = script[i]
        if c.isspace():
            i += 1
        elif script.startswith("--", i):
            j = script.find("\n", i)
            i = n if j < 0 else j + 1
        elif script.startswith("/*", i):
            j = script.find("*/", i + 2)
            if j < 0:
                raise SQLSyntaxError(f"a /* comment opened at character {i + 1} is never closed")
            if "/*" in script[i + 2:j]:
                raise SQLSyntaxError(f"a /* comment opened at character {i + 1} has another /* inside it: PostgreSQL "
                                     "and DuckDB read that as a comment inside a comment and SQLite does not, so G6 "
                                     "cannot tell where it ends")
            i = j + 2
        elif c in "Ee" and script.startswith("'", i + 1):
            j = _quoted(script, i + 1, "'", backslash=True)
            out.append(Token("string", script[i:j], depth, i, j))
            i = j
        elif c == "'":
            j = _quoted(script, i, "'")
            out.append(Token("string", script[i:j], depth, i, j))
            i = j
        elif c in "\"`":
            j = _quoted(script, i, c)
            out.append(Token("name", script[i:j], depth, i, j))
            i = j
        elif c == "$" and (m := _DOLLAR.match(script, i)):
            tag = m.group(0)
            j = script.find(tag, m.end())
            if j < 0:
                raise SQLSyntaxError(f"a {tag} quote opened at character {i + 1} is never closed")
            out.append(Token("string", script[i:j + len(tag)], depth, i, j + len(tag)))
            i = j + len(tag)
        elif m := _WORD.match(script, i):
            out.append(Token("name" if brackets else "word", m.group(0), depth, i, m.end()))
            i = m.end()
        elif m := _NUMBER.match(script, i):
            out.append(Token("number", m.group(0), depth, i, m.end()))
            i = m.end()
        else:
            if c in ")]":
                depth -= 1
                brackets -= c == "]"
                if depth < 0 or brackets < 0:
                    raise SQLSyntaxError(f"a {c} at character {i + 1} closes nothing")
            out.append(Token("punct", c, depth, i, i + 1))
            if c in "([":
                depth += 1
                brackets += c == "["
            i += 1
    if depth or brackets:
        raise SQLSyntaxError("a bracket is opened and never closed")
    return out


def _words(toks: list[Token]) -> list[Token]:
    return [t for t in toks if t.kind == "word" and t.depth == 0]


def _target(after: list[Token]) -> str:
    """The table named first in ``after`` (past FROM, ONLY and SQLite's OR <conflict>), as written."""
    toks = list(after)
    while toks and toks[0].kind == "word" and toks[0].text.upper() in ("FROM", "ONLY"):
        toks = toks[1:]
    if len(toks) >= 2 and toks[0].kind == "word" and toks[0].text.upper() == "OR":
        toks = toks[2:]
    parts: list[str] = []
    while toks and toks[0].kind in ("word", "name"):
        parts.append(toks[0].text)
        if len(toks) >= 2 and toks[1].text == ".":
            toks = toks[2:]
            continue
        break
    return ".".join(parts) if parts and len(parts) <= 3 else ""


def _bare_schema(after: list[Token]) -> bool:
    """Whether CREATE SCHEMA has only ``[IF NOT EXISTS] name [AUTHORIZATION role]`` or ``[IF NOT EXISTS]
    AUTHORIZATION role`` after it: PostgreSQL runs a schema element given there (a GRANT, a CREATE TRIGGER)."""
    at = next(i for i, t in enumerate(after) if t.kind == "word" and t.text.upper() == "SCHEMA")
    shape = []
    for t in after[at + 1:]:
        word = t.text.upper() if t.kind == "word" else ""
        shape.append(word if word in ("IF", "NOT", "EXISTS", "AUTHORIZATION")
                     else "N" if t.kind in ("word", "name") else t.text)
    return bool(_SCHEMA_TAIL.fullmatch(" ".join(shape)))


def classify(index: int, text: str, toks: list[Token]) -> Statement:
    """What kind of statement ``text`` is, from its own tokens."""
    def made(keyword: str, kind: Kind, why: str = "", table: str = "") -> Statement:
        return Statement(index, text, keyword, kind, why, table)

    first = toks[0]
    if first.kind != "word":
        return made(first.text, "refused", "a statement starts with a keyword such as SELECT")
    main = first
    if first.text.upper() == "WITH":
        # the statement starts right after the last common table expression's closing bracket, so a CTE named like a
        # keyword (WITH insert AS (...) DELETE ...) is never taken for the statement
        main = next((t for before, t in zip(toks, toks[1:]) if before.kind == "punct" and before.text == ")"
                     and before.depth == 0 and t.kind == "word" and t.depth == 0 and t.text.upper() in _AFTER_WITH),
                    first)
        if main is first:
            return made("WITH", "refused", "a WITH clause needs a statement after it")
    keyword = main.text.upper()
    after = toks[toks.index(main) + 1:]
    upper = [t.text.upper() for t in _words(after)]
    if any(t.kind == "word" and t.depth > 0 and t.text.upper() in _ROW_CHANGES for t in toks[1:toks.index(main)]):
        return made(keyword, "destructive", "a WITH clause whose common table expressions hold INSERT, UPDATE, DELETE "
                                            "or MERGE changes rows too, and G6 cannot count them")
    if keyword in READ:
        return made(keyword, "read")
    if keyword in ("UPDATE", "DELETE"):
        table = _target(after)
        if upper[:2] == ["OR", "REPLACE"]:
            return made(keyword, "destructive", "UPDATE OR REPLACE deletes any row its new values conflict with",
                        table)
        if not table:
            return made(keyword, "destructive", f"G6 cannot read which table this {keyword} changes, so it cannot "
                                                "check whether it changes every row")
        if "WHERE" not in upper:
            return made(keyword, "destructive", f"a {keyword} with no WHERE clause of its own changes every row",
                        table)
        return made(keyword, "write", table=table)
    if keyword == "MERGE":
        return made(keyword, "destructive", "MERGE can update or delete every row it matches")
    if keyword == "REPLACE" or (keyword == "INSERT" and upper[:2] == ["OR", "REPLACE"]):
        return made(keyword, "destructive", f"{'REPLACE' if keyword == 'REPLACE' else 'INSERT OR REPLACE'} deletes "
                                            "any row it conflicts with before it inserts")
    if keyword == "INSERT" and "UPDATE" in upper:
        return made(keyword, "destructive", "an INSERT that updates on conflict (ON CONFLICT ... DO UPDATE) overwrites "
                                            "rows that are already there")
    if keyword in CHANGE:
        return made(keyword, "write")
    if keyword == "CREATE":
        rest = [w for w in upper if w not in _CREATE_WORDS]
        thing = rest[0] if rest else ""
        if thing not in OBJECTS:
            return made(keyword, "refused", "G6 creates tables, views, indexes, sequences and schemas only")
        if thing == "TABLE" and upper[:2] == ["OR", "REPLACE"]:
            return made(keyword, "destructive", "CREATE OR REPLACE TABLE drops the table it replaces")
        if thing == "SCHEMA" and not _bare_schema(after):
            return made(keyword, "refused", "CREATE SCHEMA takes a name and an owner (AUTHORIZATION) only: PostgreSQL "
                                            "runs what follows them, such as a GRANT, as part of it")
        return made(keyword, "write")
    if keyword == "ALTER":
        if not upper or upper[0] != "TABLE":
            return made(keyword, "refused", "G6 alters tables only")
        if "DROP" in upper:
            return made(keyword, "destructive", "ALTER TABLE ... DROP removes a column or constraint and its data")
        if "USING" in upper or ("ALTER" in upper[1:] and "TYPE" in upper[upper.index("ALTER", 1):]):
            return made(keyword, "destructive", "ALTER TABLE ... TYPE rewrites every value in a column (USING says "
                                                "how), which can lose data")
        return made(keyword, "write")
    if keyword == "DROP":
        thing = upper[0] if upper else ""
        if thing not in OBJECTS:
            return made(keyword, "refused", "G6 drops tables, views, indexes, sequences and schemas only")
        return made(keyword, "destructive", f"DROP {thing} removes it and everything in it")
    if keyword == "TRUNCATE":
        return made(keyword, "destructive", "TRUNCATE removes every row")
    if keyword in _TRANSACTION:
        return made(keyword, "refused", "G6 runs each call in a transaction of its own")
    return made(keyword, "refused", _REFUSED.get(keyword, f"G6 does not run {keyword} statements"))


def parse(script: str) -> list[Statement]:
    """Each statement in ``script``, in order, classified. Raises :class:`SQLSyntaxError` for a script that cannot be
    read, so none of it runs."""
    toks = tokens(script)
    out: list[Statement] = []
    current: list[Token] = []
    for token in [*toks, Token("punct", ";", 0, len(script), len(script))]:
        if token.text == ";" and token.kind == "punct" and token.depth == 0:
            if current:
                text = script[current[0].start:current[-1].end]
                base = current[0].start
                shifted = [Token(t.kind, t.text, t.depth, t.start - base, t.end - base) for t in current]
                out.append(classify(len(out) + 1, text, shifted))
            current = []
        else:
            current.append(token)
    return out


__all__ = ["CHANGE", "OBJECTS", "READ", "SQLSyntaxError", "Statement", "Token", "classify", "parse", "tokens"]
