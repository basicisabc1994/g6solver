# SPDX-License-Identifier: Apache-2.0
"""The Swiss cheese table on recorded patches (loop plan Part 25; PLAN.md, 2026-11-04), from saved reviews and rows only: no model
and no container. It prints counts, and never the text of a task, an issue, a test, a reply or a patch.

A layer is a reviewer whose verdicts are saved by patch digest. Each distinct patch of a bug is right, the commonest wrong change of
its bug, or another wrong patch. The commonest wrong change is the normalised source change (``change``) that most of the bug's
wrong runs share, counted by run and held by two runs at least; changes tied for most are all commonest. A layer is measured by kind
of patch, and each pair of layers by the wrong patches both judged: how often the second misses what the first misses, beside how
often it misses at all. Two layers with the same holes are one layer, whatever models they run on, so independence is read from the
errors they miss and never assumed from where they come from.
"""
from __future__ import annotations

import collections
import hashlib
import io
import re
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path

import product_review as pr
import replay_compare as rc
import run as we
import wecommon as c
from g6.core.binomial import lower_bound, upper_bound

KINDS = ("right", "commonest wrong", "other wrong")
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
Said = Mapping[str, Mapping[str, str]]


#: The tokens Python's tokenizer gives that carry no meaning to the program, each with why. Nothing else is dropped: every other
#: token is kept, its type with its text, so two changes Python tells apart are two changes (study follow-up review 2, findings 2,
#: 3 and 7). Whitespace between two tokens is no token, and Python reads nothing from it, but inside an f-string, where an "="
#: field prints its expression as written; so an f-string is kept as written (``_side``).
DROPPED = {
    "COMMENT": "never compiled, so the program cannot read it; but a coding declaration, which Python reads on a file's first two "
               "lines (PEP 263), is kept wherever it is, since a hunk's lines are not numbered here",
    "NL": "the end of a blank or comment-only line, or a line break inside brackets: the statement goes on, and the program reads "
          "nothing from it but the numbers of the lines after it, which only a program reading its own frames or source can see",
}
#: Every token type the tokenizer gives that is kept, type and text: names, numbers, strings, operators (``OP``: every exact
#: operator type is given as one), each statement's end (``NEWLINE``) and the input's, and each block's start and end (``INDENT``,
#: ``DEDENT``, with their line's indentation as written). An f-string's parts are kept as one literal, as written. The types the
#: tokenizer gives no text of a span (ENCODING, ERRORTOKEN, the parser's own TYPE_COMMENT, TYPE_IGNORE, SOFT_KEYWORD, ASYNC and
#: AWAIT) are kept if one ever comes. A type named neither here nor in ``DROPPED`` stops the reading, so its line is kept as written.
KEPT = frozenset({"ENDMARKER", "NAME", "NUMBER", "STRING", "NEWLINE", "INDENT", "DEDENT", "OP", "FSTRING_START", "FSTRING_MIDDLE",
                  "FSTRING_END", "ENCODING", "ERRORTOKEN", "TYPE_COMMENT", "TYPE_IGNORE", "SOFT_KEYWORD", "ASYNC", "AWAIT"})
#: What the ``token`` module names that is no token type: its counts.
NOT_TOKENS = frozenset({"N_TOKENS", "NT_OFFSET"})
#: The type a line has that the tokenizer could not read, kept as written (no token type has this name).
AS_WRITTEN = "as written"


def _ladder(starting: Sequence[str]) -> list[str]:
    """What the tokenizer is given before a side's span (``_side``), so it reads the span's indentation as Python reads it in its
    file, from the lines that start a statement (``starting``, in order: a comment, a blank line or a line inside brackets has no
    level, so none is a step here). The hunk does not show the levels before its span: one line at each indentation at or above
    the span's first statement, shallowest first, so a dedent to any of them is one the tokenizer knows; then one line a step
    deeper than that statement, so it is read as a dedent to its own indentation and keeps it. A deeper level is the span's own."""
    if not starting:
        return []
    space = [line[:len(line) - len(line.lstrip(" \t\f"))] for line in starting]
    width = [len(s.expandtabs(8)) for s in space]
    levels: dict[int, str] = {}
    for w, s in zip(width, space):
        if w <= width[0]:
            levels.setdefault(w, s)
    return [levels[w] + "_\n" for w in sorted(levels) if w] + [space[0] + " _\n"]


def _side(lines: Sequence[str]) -> list[list[tuple[str, str]] | None]:
    """One side of a hunk (its old lines or its new ones, in order: a span of the file as the patch holds it) read whole by Python's
    own tokenizer, so a line inside a string that spans lines is that string's text, never a comment or code of its own (study
    follow-up review 1, finding 3): for each line, each token on it as (type, text), a token that spans lines giving each line its
    own part (a line inside a string gets one, even an empty one). Every token is kept but those that carry no meaning
    (``DROPPED``): a statement's end is not a line break inside brackets, nor a backslash's continuation, and a block is its
    ``INDENT`` and ``DEDENT`` with their line's indentation as written (a DEDENT's own text is empty), the level a hunk's span
    cannot show otherwise (study follow-up review 2, findings 3 and 7). Where a token's text is not its source, the source is kept:
    an f-string, whose parts the tokenizer renders ("{{" as "{") without the spaces an "=" field prints, is one literal as written
    (finding 2). The tokenizer reads the span twice: once with no indentation, to find the lines that start a statement, then after
    their ladder (``_ladder``). A line from where it stopped (a span that starts inside a string or ends inside one, or cannot be
    read) is None, and is kept as written."""
    starts, fresh = [], True
    with suppress(tokenize.TokenError, SyntaxError):         # as far as it reads: the second reading stops where it stops
        for token in tokenize.generate_tokens(io.StringIO("".join(line.lstrip(" \t\f") + "\n" for line in lines)).readline):
            if token.type not in (tokenize.COMMENT, tokenize.NL):
                if fresh and token.type != tokenize.ENDMARKER:
                    starts.append(token.start[0] - 1)
                fresh = token.type == tokenize.NEWLINE
    ladder = _ladder([lines[at] for at in starts])
    source = "".join(ladder) + "".join(line + "\n" for line in lines)
    rows, off = source.split("\n"), len(ladder)
    parts: list[list[tuple[str, str]]] = [[] for _ in lines]
    read = len(lines)                   # lines from this one on were not read by the tokenizer
    opened, depth = (0, 0), 0           # where the outermost open f-string starts, and how many are open

    def put(kind: str, text: str, start: tuple[int, int], end: tuple[int, int]) -> bool:
        pieces = [text] if start[0] == end[0] else text.split("\n")
        if len(pieces) != end[0] - start[0] + 1:        # a token whose text is not its lines: read no further
            return False
        for i, piece in enumerate(pieces):
            if 0 <= start[0] - 1 - off + i < len(lines):
                parts[start[0] - 1 - off + i].append((kind, piece))
        return True

    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            kind = tokenize.tok_name.get(token.type, "")
            if kind == "FSTRING_START" or depth:          # an f-string, nested ones in it, is one literal, as written
                depth += (kind == "FSTRING_START") - (kind == "FSTRING_END")
                opened = token.start if kind == "FSTRING_START" and depth == 1 else opened
                if not depth:
                    (r0, c0), (r1, c1) = opened, token.end
                    text = rows[r0 - 1][c0:c1] if r0 == r1 else "\n".join([rows[r0 - 1][c0:], *rows[r0:r1 - 1], rows[r1 - 1][:c1]])
                    if not put("STRING", text, opened, token.end):
                        read = min(read, max(0, r0 - 1 - off))
                        break
                continue
            if kind in DROPPED and not (kind == "COMMENT" and tokenize.cookie_re.match(token.line)):
                continue
            text = rows[token.start[0] - 1][:token.start[1]] if kind == "DEDENT" else token.string
            if (kind not in KEPT and kind not in DROPPED) or not put(kind, text, token.start, token.end):
                read = min(read, max(0, token.start[0] - 1 - off))
                break
    except tokenize.TokenError as exc:
        if "statement" not in str(exc.args[0]):     # a span ending inside brackets is read whole; any other stop is not
            read = min(read, max(0, int(exc.args[1][0]) - 1 - off))
    except SyntaxError as exc:                       # IndentationError and TabError among them
        read = min(read, max(0, int(exc.lineno or 1) - 1 - off))
    if depth:                                        # an f-string the span does not close is not read
        read = min(read, max(0, opened[0] - 1 - off))
    return [got if at < read else None for at, got in enumerate(parts)]


def _hunk(lines: Sequence[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """A hunk's added and removed lines in order, as their side's tokens read them (``_side``): each token as (sign, type, text),
    one after another, so a statement broken inside brackets over two lines is the same tokens as on one line, and its type keeps
    two tokens of one text apart (an operator's "{" and an f-string's literal one). A line the tokenizer could not read is kept
    as written (``AS_WRITTEN``), a blank one dropped."""
    old = _side([body for sign, body in lines if sign != "+"])
    new = _side([body for sign, body in lines if sign != "-"])
    at, out = {"-": 0, "+": 0}, []
    for sign, body in lines:
        if sign == " ":
            at["-"], at["+"] = at["-"] + 1, at["+"] + 1
            continue
        got = (old if sign == "-" else new)[at[sign]]
        at[sign] += 1
        if got is None:
            if body:
                out.append((sign, AS_WRITTEN, body))
        else:
            out.extend((sign, kind, text) for kind, text in got)
    return out


def change(text: str) -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
    """A patch's normalised source change: for each Python file outside tests, the tokens of its added and removed lines in order,
    each as Python's tokenizer reads it in its hunk's whole old or new span (``_hunk``), with its type, and only those that carry
    no meaning dropped (``DROPPED``); files sorted by path. A file's name is read as ``git apply`` reads it, with
    ``product_review``'s own reader, from its header, which ends at its first hunk; in a hunk every line is content, whatever it
    begins with, and a line that begins "@@" begins the next hunk."""
    files: dict[str, list[str]] = {}
    current, header, hunk = None, False, []

    def flush() -> None:
        if current is not None and hunk:
            files.setdefault(current, []).extend(_hunk(hunk))
        hunk.clear()

    for line in text.split("\n"):
        line = line[:-1] if line.endswith("\r") else line
        if line.startswith("diff --git "):
            flush()
            current, header = None, True
            for old, new in pr._sides(line[len("diff --git "):]):
                path = pr._applied(pr._unquoted(new))
                if path and path == pr._applied(pr._unquoted(old)):
                    current = path
                    break
            continue
        if header:
            if line.startswith("+++ "):
                name = pr._unquoted(line[4:].split("\t", 1)[0])
                if name != "/dev/null" and pr._applied(name):
                    current = pr._applied(name)
            elif line.startswith(("@@", "GIT binary patch", "Binary files ")):
                header = False
                if not (current and current.endswith(".py") and not TEST_PATH.search(current)):
                    current = None
            continue
        if line.startswith("@@"):
            flush()
        elif current is not None and line[:1] in ("+", "-", " ", ""):
            hunk.append((line[:1] or " ", line[1:]))
    flush()
    return tuple(sorted((path, tuple(lines)) for path, lines in files.items() if lines))


def kinds_of(runs: Iterable[tuple[str, object, bool]]) -> dict[str, str]:
    """Each distinct patch's kind from its bug's runs, given as (digest, normalised change, right). A digest whose runs disagree
    on the truth has no kind."""
    runs = list(runs)
    truths: dict[str, set[bool]] = collections.defaultdict(set)
    for sha, _, right in runs:
        truths[sha].add(bool(right))
    wrong = collections.Counter(chg for sha, chg, right in runs if not right and len(truths[sha]) == 1)
    top = max(wrong.values(), default=0)
    commonest = {chg for chg, n in wrong.items() if n == top and n >= 2}
    kinds = {}
    for sha, chg, right in runs:
        if len(truths[sha]) == 1:
            kinds[sha] = "right" if right else "commonest wrong" if chg in commonest else "other wrong"
    return kinds


def mixed(runs: Mapping[str, Sequence[tuple[str, object, bool]]]) -> set[str]:
    """The bugs whose runs hold both a right and a wrong patch: the only bugs where telling patches apart can change which one
    is kept."""
    return {bug for bug, got in runs.items() if {bool(right) for _, _, right in got} == {True, False}}


def table(kinds: Mapping[str, str], said: Said) -> dict[str, dict[str, object]]:
    """Each layer's counts as (flagged, judged): by kind, and its misses on wrong patches and false alarms on right ones. A
    review counts as the comparison counts it (``replay_compare.right``): on a right patch any word but "none" is a false
    alarm; on a wrong patch only "reproduced" catches it."""
    out = {}
    for layer, verdicts in said.items():
        cells = {kind: [0, 0] for kind in KINDS}
        for sha, verdict in verdicts.items():
            if sha in kinds:
                truth = kinds[sha] == "right"
                cells[kinds[sha]][1] += 1
                cells[kinds[sha]][0] += rc.right(layer, verdict, truth) != truth
        wrong = [cells["commonest wrong"], cells["other wrong"]]
        flagged_wrong, judged_wrong = sum(x[0] for x in wrong), sum(x[1] for x in wrong)
        out[layer] = {"flagged": {kind: tuple(cells[kind]) for kind in KINDS},
                      "misses": (judged_wrong - flagged_wrong, judged_wrong), "false alarms": tuple(cells["right"])}
    return out


def overlap(kinds: Mapping[str, str], said: Said, a: str, b: str) -> dict[str, object]:
    """On the wrong patches both layers judged: how many ``a`` misses, how often ``b`` misses those, and how often ``b`` misses."""
    both = [sha for sha in said[a] if sha in said[b] and kinds.get(sha, "right") != "right"]
    a_misses = [sha for sha in both if said[a][sha] != "reproduced"]
    return {"both judged": len(both), "a misses": len(a_misses),
            "b misses given a misses": (sum(said[b][sha] != "reproduced" for sha in a_misses), len(a_misses)),
            "b misses": (sum(said[b][sha] != "reproduced" for sha in both), len(both))}


def runs_of(root: Path, tasks: Iterable[str], score: str) -> dict[str, list[tuple[str, object, bool]]]:
    """Each bug's finished runs with a non-empty final patch and a truth on ``score``, as (digest, normalised change, right). Each
    run, its bug and how it ended are its own facts, and its truth is that run's verdict, both from one read by the saved rows' one
    reader (``wecommon.shelf``): never what its saved row says, and never a score read apart from its run (study review 1, finding
    22)."""
    wanted, cache = set(tasks), {}
    out: dict[str, list[tuple[str, object, bool]]] = collections.defaultdict(list)
    for saved in c.saved_rows(root, we.amendment):
        facts = saved.facts
        patch = facts.folder / "final.patch"
        truth = saved.scores.get(score)
        if facts.task not in wanted or (facts.ending or {}).get("status") != "finished" or truth is None or not patch.is_file() \
                or not patch.stat().st_size:
            continue
        raw = patch.read_bytes()                  # one read: the digest and the change are of these bytes
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in cache:
            cache[sha] = change(raw.decode("utf-8", errors="surrogateescape"))
        out[facts.task].append((sha, cache[sha], bool(truth)))
    return out


def _share(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%}, 95% {lower_bound(k, n, 0.05):.0%} to {upper_bound(k, n, 0.05):.0%})" if n else "0/0"


def report(root: Path, tasks: Sequence[str], names: Sequence[str]) -> None:
    rows = rc.patches(root, tasks)
    for score in rc.SCORES:
        runs = runs_of(root, tasks, score)
        for where, bugs in (("all bugs", set(runs)), ("mixed bugs", mixed(runs))):
            kinds: dict[str, str] = {}
            for bug in bugs:
                kinds.update(kinds_of(runs[bug]))
            said = {name: {sha: one[name]["said"] for sha, one in rows.items() if rc.counted(one[name])} for name in names}
            print(f"[{score}, {where}: {len(bugs)}] distinct patches by kind: {dict(collections.Counter(kinds.values()))}")
            for name, got in table(kinds, said).items():
                cells = "  ".join(f"{kind} {_share(*got['flagged'][kind])}" for kind in KINDS)
                print(f"  {name:13} flagged: {cells}")
            for a in names:
                for b in names:
                    if a != b:
                        pair = overlap(kinds, said, a, b)
                        print(f"  {b} misses, of what {a} misses: {_share(*pair['b misses given a misses'])}; of all wrong both "
                              f"judged: {_share(*pair['b misses'])}")


def main() -> None:
    root = c.archive_root() / "round3"
    names = [name for name, _ in rc.REVIEWERS if name != rc.PRODUCT and name != rc.REPRODUCE]
    report(root, sorted(rc.predicted()), names)


if __name__ == "__main__":
    main()
