# SPDX-License-Identifier: Apache-2.0
"""The study-trust follow-up's review 2: eight findings, each a test that failed on 33c5ef1 for the reason the review gave, and the
classes they belong to, each removed at its root with an inventory built from the code.

1. The layers reader keeps every distinction Python makes (findings 2, 3 and 7; identity_from_rendering and
   own_parser_for_foreign_format, met for the third time): each side of a hunk is the tokenizer's tokens, type and text, all
   kept but a named set that carries no meaning (``layers.DROPPED``), each with its reason; every token type Python defines is
   kept or named.
2. A verdict binds its run's whole ending, from whichever store it was read (finding 1; check_then_act_race): one digest, from
   one function, of every ending's source, which the writer compares last.
3. A proposal is recorded before it is committed (finding 4; state_not_durable): a stop between any two steps never asks for a
   second.
4. Every store folder is distinct from every other by identity, on every path in (finding 5; one_fact_two_rules).
5. A reader that fails is a failure (finding 6; failure_read_as_success): every background reader of a process's output, and
   its failure path.
6. One run has one set of facts (finding 8; one_fact_two_rules): a run whose starts disagree has none.

Fakes only: no Docker, no model, no network."""
from __future__ import annotations

import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
import sys
import token
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import evolve  # noqa: E402
import final_review as fr  # noqa: E402
import layers  # noqa: E402
import loop  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"}
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _called(func) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


class _Stop(BaseException):
    """A stop between two steps (a kill, a power cut as the process sees it)."""


@contextlib.contextmanager
def junction(target: Path, alias: Path):
    """A real Windows junction (a directory symlink elsewhere) from ``alias`` to ``target``, removed after."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        yield alias
    finally:
        os.rmdir(alias) if os.name == "nt" else alias.unlink()


# --- 1: the layers reader keeps every distinction Python makes ---------------------------------------------------------------
def _patch(base: str, code: str, name: str = "api.py") -> str:
    return f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
        base.splitlines(keepends=True), code.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"))


def _values(sources, name, *args):
    got = []
    for source in sources:
        namespace: dict = {}
        exec(compile(source, "api.py", "exec"), namespace)
        got.append(namespace[name](*args))
    return got


def test_finding_2_an_escaped_brace_and_an_expression_brace_are_two_changes():
    base = 'def value(x):\n    return x\n'
    candidates = ['def value(x):\n    return f"{{{x}}}"\n', 'def value(x):\n    return f"{ {x} }"\n']
    assert _values(candidates, "value", "abc") == ["{abc}", "{'abc'}"]
    runs = [(hashlib.sha256(_patch(base, one).encode()).hexdigest(), layers.change(_patch(base, one)), False) for one in candidates]
    assert runs[0][1] != runs[1][1], "literal escaped braces and expression braces collapsed to the same change"
    assert set(layers.kinds_of(runs).values()) == {"other wrong"}


def test_finding_3_a_statement_end_and_a_backslash_continuation_are_two_changes():
    base = 'def value():\n    x = 0\n    + 2\n    return x\n'
    sources = [base.replace('x = 0', 'x = 1'), base.replace('x = 0', 'x = 1 ' + chr(92))]
    assert _values(sources, "value") == [1, 3]
    assert layers.change(_patch(base, sources[0])) != layers.change(_patch(base, sources[1])), \
        "a statement-ending newline and an explicit continuation collapsed"


def test_finding_7_a_return_in_the_function_and_one_in_its_block_are_two_changes():
    base = 'def value(flag):\n    if flag:\n        return 0\n    return 1\n'
    sources = [base.replace('    return 1', '    return 2'), base.replace('    return 1', '        return 2')]
    assert [[got] for got in _values(sources, "value", False)] == [[2], [None]]
    assert layers.change(_patch(base, sources[0])) != layers.change(_patch(base, sources[1])), \
        "a return in the function and a return in its conditional block collapsed"


def test_a_dedent_keeps_the_level_it_returns_to():
    """The level the old pattern missed: two dedents from one block, to the module and to the class, are one DEDENT token each,
    so each keeps the indentation of its line, the level the hunk cannot show otherwise."""
    base = 'class A:\n    def f(self):\n        a = 0\n        b = 0\n        c = 0\n'
    sources = [base + 'w = 1\n', base + '    w = 1\n']
    held = []
    for source in sources:
        namespace: dict = {}
        exec(compile(source, "api.py", "exec"), namespace)
        held.append(("w" in namespace, hasattr(namespace["A"], "w")))
    assert held == [(True, False), (False, True)]
    assert layers.change(_patch(base, sources[0])) != layers.change(_patch(base, sources[1]))


def test_a_hunks_first_statement_keeps_its_indentation():
    def patch(new):
        return f"diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -1,2 +1,2 @@\n-x = 0\n+{new}\n y = 2\n"

    with pytest.raises(IndentationError):
        compile("    x = 1\ny = 2\n", "api.py", "exec")
    assert layers.change(patch("x = 1")) != layers.change(patch("    x = 1"))


def test_an_equals_field_prints_its_spaces_so_they_are_part_of_the_change():
    base = 'def value(x):\n    return x\n'
    sources = ['def value(x):\n    return f"{x=}"\n', 'def value(x):\n    return f"{x = }"\n']
    assert _values(sources, "value", 1) == ["x=1", "x = 1"]
    assert layers.change(_patch(base, sources[0])) != layers.change(_patch(base, sources[1]))


def test_an_f_string_nested_in_an_f_string_is_kept_as_written():
    base = 'def value(x):\n    return x\n'
    sources = ["def value(x):\n    return f\"{f'{x}'}\"\n", "def value(x):\n    return f\"{f'{{x}}'}\"\n"]
    assert _values(sources, "value", "abc") == ["abc", "{x}"]
    one, two = (layers.change(_patch(base, s)) for s in sources)
    assert one != two
    assert ("+", "STRING", "f\"{f'{x}'}\"") in one[0][1]           # one literal, as written
    assert layers.change(_patch(base, sources[0].replace("\n    return", "\n    # a note\n    return"))) == one


def test_an_f_string_the_span_does_not_close_is_kept_as_written():
    patch = ('diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = f"""{x}\n'
             '+y = f"""{x} and\n')
    got = layers.change(patch)[0][1]
    assert ("-", layers.AS_WRITTEN, 'y = f"""{x}') in got and ("+", layers.AS_WRITTEN, 'y = f"""{x} and') in got


def test_a_tab_and_spaces_are_kept_as_written():
    """Ruled: indentation is kept as written (an INDENT's text, and a DEDENT given its line's), since a hunk does not hold the
    levels before it and the width is what tells them apart; two programs that differ only in tabs against spaces behave the same
    and are two changes."""
    base = 'def value(x):\n    return x\n\n\ndef other():\n    return 0\n'
    sources = [base.replace('def other():\n    return 0', 'def other():\n\tif True:\n\t\treturn 1'),
               base.replace('def other():\n    return 0', 'def other():\n    if True:\n        return 1')]
    assert _values(sources, "other") == [1, 1]
    tabs, spaces = (layers.change(_patch(base, s)) for s in sources)
    assert tabs != spaces and ("+", "INDENT", "\t\t") in tabs[0][1] and ("+", "INDENT", "        ") in spaces[0][1]


def test_a_coding_declaration_is_kept_as_python_reads_it():
    """A comment Python reads (PEP 263): the same bytes are two programs."""
    code = "x = 'é'\n"
    declared = "# -*- coding: latin-1 -*-\n" + code
    values = []
    for source in (declared, code):
        namespace: dict = {}
        exec(compile(source.encode("utf-8"), "api.py", "exec"), namespace)
        values.append(namespace["x"])
    assert values[0] != values[1]
    assert layers.change(_patch("x = 1\n", declared)) != layers.change(_patch("x = 1\n", code))
    assert layers.change(_patch("x = 1\n", "# a note\n" + code)) == layers.change(_patch("x = 1\n", code))


#: For each token the reader drops, programs that differ only in it, with what to call them with.
DROPPED_CASES = {
    "COMMENT": [
        ('def value(x):\n    return x\n', 'def value(x):\n    return x + 1  # one more\n', 'def value(x):\n    return x + 1\n',
         (1,)),
        ('def value(x):\n    if x:\n        y = 1\n    return 0\n',                 # a comment line at an indentation of its own
         'def value(x):\n    if x:\n        y = 1\n  # odd\n    return 2\n', 'def value(x):\n    if x:\n        y = 1\n    return 2\n',
         (1,)),
        ('def value(x):\n    if x:\n        return 0\n    return 2\n',               # between a block's opening and its body
         'def value(x):\n    if x:\n        # why\n        return 1\n    return 2\n',
         'def value(x):\n    if x:\n        return 1\n    return 2\n', (1,)),
    ],
    "NL": [
        ('def value(x):\n    y = x\n    return y\n', 'def value(x):\n    y = x + 1\n\n    return y\n',
         'def value(x):\n    y = x + 1\n    return y\n', (1,)),
        ('def value(x):\n    return x\n', 'def value(x):\n    return max(x,\n               1)\n',      # a continuation inside brackets
         'def value(x):\n    return max(x, 1)\n', (0,)),
    ],
}


@pytest.mark.parametrize("member", ["COMMENT", "NL"])
def test_each_token_the_reader_drops_carries_no_meaning(member):
    assert sorted(layers.DROPPED) == sorted(DROPPED_CASES) and member in layers.DROPPED
    for base, one, other, args in DROPPED_CASES[member]:
        assert _values([one, other], "value", *args)[0] == _values([one, other], "value", *args)[1]
        assert layers.change(_patch(base, one)) == layers.change(_patch(base, other)), (member, one)


def test_every_token_type_python_defines_is_kept_or_dropped_with_its_reason():
    """The inventory, from the interpreter's own ``token`` module: every type it defines is kept (an exact operator type is given
    as ``OP``), dropped with its reason, or no token type (its counts). A type a later Python adds fails here until it is named."""
    exact = set(token.EXACT_TOKEN_TYPES.values())
    named = layers.KEPT | set(layers.DROPPED) | layers.NOT_TOKENS
    unnamed = sorted(name for number, name in token.tok_name.items() if number not in exact and name not in named)
    assert unnamed == []
    assert "OP" in layers.KEPT and not layers.KEPT & set(layers.DROPPED) and not layers.NOT_TOKENS & (layers.KEPT | set(layers.DROPPED))
    assert all(len(why.split()) >= 10 for why in layers.DROPPED.values())       # each with its reason, in the code
    assert layers.AS_WRITTEN not in token.tok_name.values()
    assert tokenize.cookie_re.match("# -*- coding: latin-1 -*-")


# --- 2: a verdict binds its run's whole ending, from whichever store it was read ---------------------------------------------
def test_finding_1_a_container_ending_changed_during_the_rows_write_is_refused(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True}, "finished")
    assert c.run_facts(archive, run.rel).ending["status"] == "finished"
    monkeypatch.setattr(we, "amendment", lambda task: None)
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps(
        {"run": run.rel} if command == "run" else {"checks": [{"patch": "final", "passes": True, "failing": []}]}))
    real_fsync, changes = c.os.fsync, []

    def fsync(fd):                  # after the caller's reading again, while the new row is synced
        record = run.path / "run.json"
        data = json.loads(record.read_text("utf-8"))
        data["status"] = "error"
        record.write_text(json.dumps(data), "utf-8")
        changes.append(c.run_facts(archive, run.rel).ending["status"])
        return real_fsync(fd)

    monkeypatch.setattr(c.os, "fsync", fsync)
    with pytest.raises(SystemExit, match="ending changed"):
        loop.one(tmp_path, "g6", "A", "bug", 1)
    assert changes == ["error"]
    assert not (tmp_path / c.SAVED / "A-bug-1.json").exists()


def _made(archive: c.Archive, source: str):
    """A finished run whose ending is read from ``source``, and how to change that source's bytes."""
    if source == c.ENDING:
        run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
        run.finish({"agent_started": True, "executor": "host"}, "finished")
        return run, lambda: run.failed("a failure after the ending", agent_started=True)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    if source == "run.json":
        def rewrite():
            record = json.loads((run.path / "run.json").read_text("utf-8"))
            (run.path / "run.json").write_text(json.dumps({**record, "status": "error"}), "utf-8")
        return run, rewrite
    (run.path / "run.json").unlink()            # its end in the index is all there is
    return run, lambda: archive.append({"event": "ended", "run": run.rel, "status": "error", "at": c.now(), "cost_usd": 0.0,
                                        "agent_started": True})


@pytest.mark.parametrize("source", ["ending.json", "run.json", "index"])
def test_a_verdict_binds_its_runs_ending_from_whichever_store_it_was_read(tmp_path, monkeypatch, source):
    assert c.ENDING_FROM == (c.ENDING, "run.json", "index")
    archive = c.Archive(tmp_path)
    run, change = _made(archive, source)
    facts = c.run_facts(archive, run.rel)
    assert facts.ending["status"] == "finished" and facts.ending_from == source and facts.ending_sha256
    row = {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: facts.ending_sha256}
    real = c.os.fsync

    def fsync(fd):                  # after the writer's first reading, before its last check and the replace
        change()
        return real(fd)

    monkeypatch.setattr(c.os, "fsync", fsync)
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, row)
    assert not (tmp_path / c.SAVED / "A-bug-1.json").exists()
    assert c.run_facts(archive, run.rel).ending_sha256 != facts.ending_sha256


def test_a_runs_own_record_that_cannot_be_read_is_part_of_its_endings_digest(tmp_path):
    archive = c.Archive(tmp_path)
    run, _ = _made(archive, "run.json")
    before = c.run_facts(archive, run.rel)
    (run.path / "run.json").write_bytes(b'{"status": "finish')          # torn: the index's end of the run is read instead
    after = c.run_facts(archive, run.rel)
    assert after.ending["status"] == "finished" and after.ending_from == "index"
    assert after.ending_sha256 and after.ending_sha256 != before.ending_sha256
    assert c.verdict(archive, run.rel, {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: before.ending_sha256}) is None


def test_a_row_kept_before_its_runs_own_record_was_digested_holds_as_it_did(tmp_path, monkeypatch):
    """Ruled: the record stands. A row loop.one kept before this change names no digest when its run's ending is its own record
    (run.json), and is read as it was read when it was kept; every verdict written from now on records the digest of its run's
    ending, whatever its source, and is bound to it."""
    monkeypatch.setattr(we, "amendment", lambda task: None)
    archive = c.Archive(tmp_path)
    run, _ = _made(archive, "run.json")
    old = {"arm": "A", "task": "bug", "repeat": 1, "run": run.rel, "status": "finished", "passes": True, "failing": 0, "tests": []}
    (tmp_path / c.SAVED).mkdir()
    (tmp_path / c.SAVED / "A-bug-1.json").write_text(json.dumps(old), "utf-8")
    assert c.verdict(archive, run.rel, old) is True and c.holds(archive, run.rel, old)
    new = {**old, c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    assert c.holds(archive, run.rel, new)
    record = json.loads((run.path / "run.json").read_text("utf-8"))
    (run.path / "run.json").write_text(json.dumps({**record, "why": "rewritten"}), "utf-8")
    assert not c.holds(archive, run.rel, new) and c.verdict(archive, run.rel, new) is None
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, {**old, "passes": False})          # the writer keeps no verdict without its ending's digest


def test_every_ending_has_one_digest_from_one_function():
    tree = ast.parse((STUDY / "wecommon.py").read_text(encoding="utf-8"))
    makers = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n.func) == "RunFacts"]
    assert len(makers) == 1
    digest = next(k.value for k in makers[0].keywords if k.arg == "ending_sha256")
    assert isinstance(digest, ast.Call) and _called(digest.func) == "_digest"
    hashing = {fn.name for fn in tree.body if isinstance(fn, FUNCS)
               for n in ast.walk(fn) if isinstance(n, ast.Call) and _called(n.func) == "sha256"}
    assert hashing == {"sha256_file", "_baseline", "_digest"}
    reader = next(fn for fn in tree.body if isinstance(fn, FUNCS) and fn.name == "_ending_from")
    for ret in (n for n in ast.walk(reader) if isinstance(n, ast.Return)):
        assert isinstance(ret.value, ast.Tuple) and len(ret.value.elts) == 3, ast.unparse(ret)


# --- 3: a proposal is recorded before it is committed ----------------------------------------------------------------------
def _trainer(root: Path, committed: list[bytes]) -> evolve.Training:
    """A restarted trainer: only the process's own state is new; every file survives."""
    trainer = evolve.Training.__new__(evolve.Training)
    trainer.root, trainer.parent, trainer.text = root, "L9", {}
    trainer.rows, trainer.shelves, trainer.seen = [], [], []
    trainer.caller = SimpleNamespace(cost_usd=0.0)
    trainer.committed = lambda path: path.is_file() and path.read_bytes() in committed        # committed as it is
    return trainer


@pytest.fixture
def proposing(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    monkeypatch.setattr(we, "HARNESS", harness)
    generated, committed = [], []

    def generate(*a, **kw):
        text = ["Keep prior behavior intact.", "Exercise every boundary case."][len(generated)]
        generated.append(text)
        return text, []

    monkeypatch.setattr(evolve.distill, "distill", generate)
    monkeypatch.setattr(evolve.agentic, "choice", lambda *a, **kw: ("fixer", SimpleNamespace(as_dict=lambda: {"choice": "fixer"})))
    monkeypatch.setattr(loop, "commit", lambda path, message: committed.append(path.read_bytes()))
    return harness, generated, committed


CHILD = SimpleNamespace(id="candidate", operator="prompt_revision")


@pytest.mark.parametrize("at", ["the record", "the harness file", "the commit", "after the commit"])
def test_finding_4_a_stop_between_any_two_steps_of_a_proposal_never_asks_for_a_second(tmp_path, monkeypatch, proposing, at):
    """The review's case is "after the commit": the harness committed, and nothing else done. A stop in the record's own write
    leaves nothing recorded and nothing committed, so a restart proposes again, once; after it, never."""
    harness, generated, committed = proposing
    real_commit, real_created = loop.commit, c.created

    def created(path, *a, **kw):     # the harness file is made by ``wecommon.created`` (study follow-up review 3, finding 3)
        if path.parent == harness:
            raise _Stop()
        return real_created(path, *a, **kw)

    with monkeypatch.context() as patch:
        if at == "the record":
            patch.setattr(c.os, "replace", lambda *a: (_ for _ in ()).throw(_Stop()))
        elif at == "the harness file":
            patch.setattr(c, "created", created)
        elif at == "the commit":
            patch.setattr(loop, "commit", lambda path, message: (_ for _ in ()).throw(_Stop()))
        else:
            patch.setattr(loop, "commit", lambda path, message: (real_commit(path, message), (_ for _ in ()).throw(_Stop())))
        with pytest.raises(_Stop):
            _trainer(tmp_path, committed).proposed(CHILD, "L10")
    got = _trainer(tmp_path, committed).proposed(CHILD, "L10")
    assert got["outcome"] == "to run"
    assert len(generated) == (2 if at == "the record" else 1), "a restart asked for a second proposal"
    assert len(committed) == 1 and (harness / "L10.json").read_bytes() == committed[0]
    assert json.loads(committed[0])["fixer"] == got["text"] == generated[-1]
    assert json.loads((tmp_path / c.SAVED / "evolve_L10.json").read_text("utf-8"))["text"] == generated[-1]
    assert _trainer(tmp_path, committed).proposed(CHILD, "L10")["text"] == generated[-1] and len(committed) == 1


@pytest.mark.parametrize("record", [b"{not json", b"[]", b'{"field": "fixer"}',
                                    b'{"operator": "boundary", "field": "scout", "text": "t", "decision": {}}'])
def test_a_recorded_proposal_that_cannot_be_finished_is_refused_and_never_asked_again(tmp_path, proposing, record):
    """Unreadable or malformed state: a record that cannot be read, or one kept before its harness's bytes were kept with it
    whose harness is not committed as it is, is refused; nothing is proposed again."""
    harness, generated, committed = proposing
    (tmp_path / c.SAVED).mkdir()
    (tmp_path / c.SAVED / "evolve_L10.json").write_bytes(record)
    with pytest.raises(ValueError, match="evolve_L10.json"):
        _trainer(tmp_path, committed).proposed(CHILD, "L10")
    assert generated == [] and committed == [] and not (harness / "L10.json").exists()


# --- 4: every store folder is distinct from every other, on every path in ----------------------------------------------------
def test_finding_5_the_amended_writer_refuses_an_amended_folder_that_is_a_review_store(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    store = c.beside(archive, fr.FOLDER)
    store.mkdir(parents=True)
    with junction(store, tmp_path / c.SAVED / c.AMENDED) as alias:
        assert os.path.samefile(alias, store)
        with pytest.raises(ValueError):
            c.beside(archive, fr.FOLDER)
        row = {"run": run.rel, "passes": True, "amended_passes": True, c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
        with pytest.raises((ValueError, c.ArchiveError)):
            c.keep(archive, row, amended=True)
        assert not list(store.iterdir())


@pytest.mark.parametrize(("target", "alias"), [("store_a", "store_b"), (c.AMENDED, "store_b"), ("store_a", c.AMENDED)])
def test_every_store_folder_is_distinct_from_every_other_on_every_path_in(tmp_path, monkeypatch, target, alias):
    """Two review stores, a store and the amended folder, the amended folder and a store: one function refuses any two that are
    one folder, and every reader and writer of the rows and every store asks it."""
    monkeypatch.setattr(we, "amendment", lambda task: None)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    row = {"run": run.rel, "passes": True, "failing": 0, "amended_passes": True,
           c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    rows = tmp_path / c.SAVED
    (rows / target).mkdir(parents=True)
    paths = {"the rows' writer": lambda: c.keep(archive, row), "the amended rows' writer": lambda: c.keep(archive, row, amended=True),
             "the rows' reader": lambda: c.shelf(archive, lambda task: None),
             "a review store": lambda: c.beside(archive, fr.FOLDER),
             "a training file": lambda: c.write_whole(lambda: c.beside(archive, "cegis.json"), "{}")}
    with junction(rows / target, rows / alias):
        for what, enter in paths.items():
            with pytest.raises(ValueError, match="by a junction or a link"):
                enter()
                pytest.fail(f"{what} went in")
        assert not [p for p in rows.rglob("*") if p.is_file()]


#: Every path that opens a store for writing, by module and function, from the code: each call of the one writer of store files
#: (``write_whole``) and of the replay's lock (``one_runner``), and what its destination is made by: ``beside`` (every store) or
#: ``keep``'s own ``where`` (a row). Both ask the one rule of the stores' identity (``_folders``).
OPENERS = {
    ("evolve", "Training.proposed"): ["beside"],
    ("evolve", "train"): ["beside"],
    ("final_review", "keep"): ["beside"],
    ("loop", "main"): ["beside"],
    ("replay3", "main"): ["beside"],
    ("replay3", "review_bug"): ["beside"],
    ("wecommon", "keep"): ["keep"],
}


def _openers() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(STUDY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        besides = {fn.name for fn in ast.walk(tree) if isinstance(fn, FUNCS)
                   and any(isinstance(n, ast.Call) and _called(n.func) == "beside" for n in ast.walk(fn))}

        def made_by(arg) -> str:
            if isinstance(arg, ast.Name) and arg.id == "where":
                return "keep"
            names = {_called(n.func) for n in ast.walk(arg) if isinstance(n, ast.Call)}
            return "beside" if names & ({"beside"} | besides) else "?"

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (*FUNCS, ast.ClassDef)):
                    walk(child, f"{where}.{child.name}" if where else child.name)
                    continue
                if isinstance(child, ast.Call) and _called(child.func) in ("write_whole", "one_runner") and child.args:
                    found.setdefault((path.stem, where), []).append(made_by(child.args[0]))
                walk(child, where)

        walk(tree, "")
    return found


def test_every_path_that_opens_a_store_for_writing_asks_the_one_rule_of_their_identity():
    assert _openers() == OPENERS
    tree = ast.parse((STUDY / "wecommon.py").read_text(encoding="utf-8"))
    fns = {fn.name: fn for fn in ast.walk(tree) if isinstance(fn, FUNCS)}
    for name in ("beside", "where", "shelf"):         # every store, every row written, and the rows' reader
        assert any(isinstance(n, ast.Call) and _called(n.func) == "_folders" for n in ast.walk(fns[name])), name
    rule = fns["_folders"]
    assert any(isinstance(n, ast.Call) and _called(n.func) == "scandir" for n in ast.walk(rule))     # every folder there


# --- 5: a reader that fails is a failure ----------------------------------------------------------------------------------
def test_finding_6_a_failed_stdout_reader_is_no_clean_end_of_a_session(tmp_path, monkeypatch):
    reads = []

    def readline():
        reads.append(1)
        if len(reads) == 1:
            return json.dumps(SUCCESS).encode() + b"\n"
        raise OSError("session stdout could not be read to its end")

    proc = SimpleNamespace(stdin=io.BytesIO(), stdout=SimpleNamespace(readline=readline), poll=lambda: 0, wait=lambda **kw: 0)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    ctx = SimpleNamespace(archive=archive, docker=SimpleNamespace(popen=lambda *a, **kw: proc), client_env=lambda key: {})
    real_thread, threads = we.threading.Thread, []

    def thread(*a, **kw):
        started = real_thread(*a, **kw)
        threads.append(started)
        return started

    monkeypatch.setattr(we.threading, "Thread", thread)
    try:
        ended = we.run_session(ctx, run, "agent", ["exec"], "", c.Tally(), "prompt", "stream.jsonl", seconds=60, stop_usd=10)
    finally:
        for started in threads:
            started.join(timeout=1)
            assert not started.is_alive()
    assert len(reads) == 2
    assert ended == "error", "a failed stdout reader was treated as normal EOF and the session finished"


def test_a_stream_whose_reader_fails_ends_error_and_one_that_ends_ends_clean():
    def failing():
        yield json.dumps(SUCCESS).encode() + b"\n"
        raise OSError("the pipe broke")

    for lines, ended in ((failing(), "error"), (iter([json.dumps(SUCCESS).encode() + b"\n"]), "")):
        assert we.read_stream(lines, c.Tally(), deadline=float("inf"), write=lambda line: None) == ended


class _Unread:
    """A process's stderr that cannot be read to its end."""

    def read(self):
        raise OSError("the session's stderr could not be read to its end")


def test_a_host_sessions_stderr_that_cannot_be_read_ends_it_error(tmp_path, monkeypatch):
    records = [{"type": "system", "subtype": "init", "tools": ["Read", "Edit", "Write", "Glob", "Grep", "mcp__box__run"],
                "mcp_servers": [{"name": "box", "status": "connected"}]}, SUCCESS]
    proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in records)),
                           stderr=_Unread(), poll=lambda: 0, wait=lambda **kw: 0)
    monkeypatch.setattr(we, "host_start", lambda *a, **kw: SimpleNamespace(proc=proc))
    monkeypatch.setattr(we, "end_session", lambda *a: "")
    monkeypatch.setattr(we.shutil, "which", lambda name: name)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    ctx = SimpleNamespace(model=we.R3_MODELS[1], starter=None, archive=archive, environ={})
    ended = we.host_session(ctx, run, tmp_path, tmp_path / "mcp", tmp_path / "settings", "prompt", "stream.jsonl",
                            seconds=60, tally=c.Tally())
    assert ended == "error"
    assert c.ending(archive, run.rel)["status"] == "error"


def test_a_container_first_sessions_stderr_that_cannot_be_read_ends_its_run_error(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("stfu2_k3_helpers", ROOT / "tests" / "validation" / "test_k3_rule_step.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    task, events, sent = we.load_tasks()[0], [], []
    docker = helper._Docker(task, events, sent)
    docker.popen = lambda *a, **kw: SimpleNamespace(stdin=helper._Stdin(sent), stdout=io.BytesIO(json.dumps(SUCCESS).encode() + b"\n"),
                                                    stderr=_Unread(), poll=lambda: 0, wait=lambda **kw: 0)
    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    ctx = we.Context(archive=c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: "dummy-key-value",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm="A", environ={})
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    assert record["status"] == "error", record["status"]


#: Every background thread the study's code starts, by module and function, from the code, and its target: the readers of a
#: process's output (``pump``, a session's stdout; ``read_all``, its stderr), whose failure is their reading's end; and those
#: that read no process's output (``feed`` writes the prompt; each ``watch`` is a timer, or reads a usage figure).
THREADS = {
    ("run", "read_stream"): ["pump"],
    ("run", "host_session"): ["read_all", "feed"],
    ("run", "_run_task"): ["read_all"],
    ("run", "Watchdog.start"): ["watch"],
    ("stage3_tick", "main"): ["watch"],
}
READERS = {"pump", "read_all"}


def _threads() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(STUDY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (*FUNCS, ast.ClassDef)):
                    walk(child, f"{where}.{child.name}" if where else child.name)
                    continue
                if isinstance(child, ast.Call) and _called(child.func) == "Thread":
                    target = next((k.value for k in child.keywords if k.arg == "target"), None)
                    found.setdefault((path.stem, where), []).append(target.id if isinstance(target, ast.Name) else "lambda")
                walk(child, where)

        walk(tree, "")
    return found


def test_every_background_reader_of_a_process_output_has_its_failure_path():
    assert _threads() == THREADS
    tree = ast.parse((STUDY / "run.py").read_text(encoding="utf-8"))
    fns = {fn.name: fn for fn in ast.walk(tree) if isinstance(fn, FUNCS)}
    for (module, where), targets in THREADS.items():
        if "read_all" in targets:          # what it read is folded into its session's ending (``unread``)
            calls = {_called(n.func) for n in ast.walk(fns[where.split(".")[-1]]) if isinstance(n, ast.Call)}
            assert "unread" in calls, where
    pump = fns["pump"]                     # its failure is posted, never the end's sentinel alone
    handlers = [h for h in ast.walk(pump) if isinstance(h, ast.ExceptHandler)]
    assert handlers and any(_called(n.func) == "put" for h in handlers for n in ast.walk(h) if isinstance(n, ast.Call))
    kept: list = []
    we.read_all(_Unread(), kept)
    assert len(kept) == 1 and isinstance(kept[0], OSError)


# --- 6: one run has one set of facts -------------------------------------------------------------------------------------
def test_finding_8_recovery_never_gives_one_runs_ending_to_another_start(tmp_path):
    archive = c.Archive(tmp_path)
    model = we.R3_MODELS[1]
    run = archive.start("run", "first-bug", arm="A", repeat=1, model=model)
    run.finish({"agent_started": True}, "finished")
    archive.append({"event": "started", "kind": "run", "run": run.rel, "task": "second-bug", "arm": "K2", "repeat": 2,
                    "model": model})
    try:
        recovered = we.r3_made(archive.rows(), tmp_path, (model, "second-bug", "K2", 1))
    except (ValueError, c.ArchiveError):
        return
    assert recovered is None, "recovery relabelled the first bug's run as an already-made run of the second bug"


def test_a_run_whose_starts_disagree_has_no_facts_blocks_nothing_recovers_nothing_and_is_reported(tmp_path, capsys):
    archive = c.Archive(tmp_path / "round3")
    model = we.R3_MODELS[1]
    run = archive.start("run", "first-bug", arm="A", repeat=1, model=model)
    run.finish({"agent_started": True}, "finished")
    archive.append({"event": "started", "kind": "run", "run": run.rel, "task": "second-bug", "arm": "K2", "repeat": 2,
                    "model": model})
    rows = archive.rows()
    assert c.run_facts(archive, run.rel) is None
    assert we.r3_done(rows, archive.root) == {}
    for key in ((model, "first-bug", "A", 1), (model, "second-bug", "K2", 2), (model, "second-bug", "K2", 1)):
        assert we.r3_made(rows, archive.root, key) is None
    assert set(c.conflicting(rows)) == {run.rel}
    assert we.main(["schedule", "--round", "3", "--archive", str(tmp_path)]) == 0
    assert set(json.loads(capsys.readouterr().out)["conflicting_starts"]) == {run.rel}


def test_a_start_written_twice_alike_is_one_run(tmp_path):
    archive = c.Archive(tmp_path)
    model = we.R3_MODELS[1]
    run = archive.start("run", "bug", arm="A", repeat=1, model=model)
    run.finish({"agent_started": True}, "finished")
    archive.append({"event": "started", "run": run.rel, "kind": "run", "task": "bug", "at": c.now(), "arm": "A", "repeat": 1,
                    "model": model})
    facts = c.run_facts(archive, run.rel)
    assert facts is not None and (facts.model, facts.task, facts.arm, facts.repeat, facts.kind) == (model, "bug", "A", 1, "run")
    assert we.r3_made(archive.rows(), tmp_path, (model, "bug", "A", 1)) == run.rel and c.conflicting(archive.rows()) == {}


def test_a_runs_key_comes_from_its_facts_alone():
    """From the code: recovery and scheduling read of an index row only which run it starts; every fact of the key is
    ``run_facts``'s."""
    tree = ast.parse((STUDY / "run.py").read_text(encoding="utf-8"))
    made = next(fn for fn in tree.body if isinstance(fn, FUNCS) and fn.name == "_r3_made")
    keys = {n.args[0].value for n in ast.walk(made) if isinstance(n, ast.Call) and _called(n.func) == "get"
            and n.args and isinstance(n.args[0], ast.Constant)}
    keys |= {n.slice.value for n in ast.walk(made) if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)}
    assert keys <= {"event", "kind", "run", "status", "agent_started"}, keys
