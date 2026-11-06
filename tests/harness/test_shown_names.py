# SPDX-License-Identifier: Apache-2.0
"""review-maskfu1's four findings on claude/masking-followup, each as its reviewer wrote it, and the root they share.

A name git gives is untrusted text, and it was put as it stands into text G6 also writes: the untracked file's header in what
the patch is known by (identity_from_rendering), the copy's headers, which a second rule then normalised (one_fact_two_rules),
and lines the scrubber read together with G6's own digest lines, where a mask over a name never counted
(guard_covers_less_than_it_guards). The root, removed: one quoting rule for every name (``repo_tests.shown_name``, git's own
C-style quoting), G6's own lines added after masking and never scrubbed, and a mask over a name counted by the one rule that
decides what hides the task and the patch (``completion_loop._hides``). Git runs only on scratch repositories, reviewers are
recorded, and no test of a reviewer's ever runs."""
import ast
import hashlib
import inspect
import re
import subprocess
import textwrap
from itertools import combinations
from pathlib import Path

import pytest

from g6.context.redact import MASK
from g6.core.ledger import digest
from g6.harness import completion_loop as loop
from g6.workforce import repo_tests
from tests.harness.test_masking_followup import Reviewer, _callee, _functions
from tests.workforce.test_repo_tests import GIT_ENV, git, make_repo


# --- review-maskfu1's four findings, each as its reviewer wrote it ------------------------------------------------------------

def test_finding_1_secret_shaped_name_cannot_erase_base_byte_identity(tmp_path):
    rel = "-----BEGIN PRIVATE KEY-----"
    identities, copies, verdicts = [], [], []
    for number, bad in enumerate((b"\xff", b"\xfe")):
        repo = make_repo(tmp_path / str(number))
        path = repo / rel
        path.write_bytes(b"payload = '" + bad + b"'\n")
        git(repo, "add", "--", rel)
        git(repo, "commit", "-qm", "binary fixture")
        path.unlink()
        change = repo_tests.change(repo)
        read = repo_tests.diff_read(change)
        identities.append(read.text)
        copy = loop._shown(change, read)
        copies.append(copy.masked)
        verdicts.append(loop.run_critic(change, "Review the removal", Reviewer(), "Review", tmp_path,
                                        patch=read, whole=True)["verdict"])
        assert hashlib.sha256(read.files[0].old).hexdigest() in read.text
    assert identities[0] != identities[1]
    assert copies[0] != copies[1] or verdicts != ["none", "none"], (
        "two distinct deleted base byte sequences have identical reviewer copies and both reviews end clean")


def _raw_git(repo: Path):
    def run(*args, data=None):
        return subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', *args],
                              input=data, capture_output=True, check=True, env=GIT_ENV).stdout.strip()
    return run


def test_finding_2_multiline_masks_in_git_names_cannot_end_clean(tmp_path):
    repo = make_repo(tmp_path)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.protectNTFS', 'false'], check=True)
    raw_git = _raw_git(repo)
    rel = '-----BEGIN PRIVATE KEY-----\nmade-up-name-body\n-----END PRIVATE KEY-----'
    blob = raw_git('hash-object', '-w', '--stdin', data=b'value = 0\n')
    original = raw_git('ls-tree', '-z', 'HEAD')
    tree = raw_git('mktree', '-z', data=original + b'100644 blob ' + blob + b'\t' + rel.encode() + b'\0')
    base = raw_git('commit-tree', tree.decode(), '-m', 'multiline name').decode()
    change = repo_tests.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = repo_tests.diff_read(change)
    shown = loop._shown(change, read)
    assert sum(bool(cuts) for cuts in loop._seen(rel).cuts) == 3
    counts = {}
    for row in loop._seen(shown.text).cuts:
        for number in {number for _, _, number in row}:
            counts[number] = counts.get(number, 0) + 1
    masks_multiple_lines = any(count >= 2 for count in counts.values())
    found = loop.run_critic(change, 'Review the removal', Reviewer(), 'Review', tmp_path, patch=read, whole=True)
    assert not masks_multiple_lines or (found['verdict'] == 'failed' and found['error'] == loop.HIDDEN), (
        'a mask covers three filename lines in the patch, but the review is accepted as clean')
    assert found['verdict'] == 'failed' and found['error'] == loop.HIDDEN      # the mask over the name counts, on its own


def test_finding_3_distinct_utf8_git_names_remain_distinct_in_reviewer_copy(tmp_path):
    identities, copies, verdicts = [], [], []
    for number, separator in enumerate(('\r', '\n')):
        repo = make_repo(tmp_path / str(number))
        subprocess.run(['git', '-C', str(repo), 'config', 'core.protectNTFS', 'false'], check=True)
        raw_git = _raw_git(repo)
        rel = 'odd' + separator + 'name.txt'
        blob = raw_git('hash-object', '-w', '--stdin', data=b'value = 0\n')
        tree = raw_git('mktree', '-z', data=raw_git('ls-tree', '-z', 'HEAD') +
                       b'100644 blob ' + blob + b'\t' + rel.encode() + b'\0')
        base = raw_git('commit-tree', tree.decode(), '-m', 'control name').decode()
        change = repo_tests.Change(repo, base, base, base, (rel,), {}, {}, (), ())
        read = repo_tests.diff_read(change)
        assert read.files[0].rel == rel
        identities.append(read.text)
        copies.append(loop._shown(change, read).masked)
        verdicts.append(loop.run_critic(change, 'Review the removal', Reviewer(), 'Review', tmp_path,
                                        patch=read, whole=True)['verdict'])
    assert identities[0] != identities[1]
    assert copies[0] != copies[1] or verdicts != ['none', 'none'], (
        'git names containing CR and LF become the same reviewer copy and both reviews end clean')


def test_finding_4_filename_cannot_inject_a_second_file_into_patch_identity(tmp_path, monkeypatch):
    """POSIX current-file reads emulated on Windows; names emitted by real git."""
    producer = tmp_path / 'git-producer'
    producer.mkdir()

    def git_bytes(*args, data=None):
        return subprocess.run(['git', '-C', str(producer), *args], input=data,
                              capture_output=True, check=True, env=GIT_ENV).stdout
    git_bytes('init', '-q')

    def listing(layout):
        tree = {}
        for name, raw in layout.items():
            branch = tree
            parts = name.split('/')
            for part in parts[:-1]:
                branch = branch.setdefault(part, {})
            branch[parts[-1]] = raw

        def write(branch):
            records = []
            for name, entry in branch.items():
                if isinstance(entry, dict):
                    mode, kind, oid = b'040000', b'tree', write(entry)
                else:
                    mode, kind = b'100644', b'blob'
                    oid = git_bytes('hash-object', '-w', '--stdin', data=entry).strip()
                records.append(mode + b' ' + kind + b' ' + oid + b'\t' + name.encode() + b'\0')
            return git_bytes('mktree', '-z', data=b''.join(records)).strip()
        return git_bytes('ls-tree', '-r', '-z', '--name-only', write(tree).decode())

    injected = 'a\n@@ -0,0 +1,1 @@\n+X\n--- /dev/null\n+++ b/b'
    layouts = ({'a': b'X\n', 'b': b'Z\n'}, {injected: b'Z\n'})
    producer_listings = [listing(layout) for layout in layouts]
    identities, listed_names = [], []
    real_file, real_link = Path.is_file, Path.is_symlink
    files = {}

    def rel(path):
        try:
            return path.relative_to(tmp_path).as_posix()
        except ValueError:
            return None
    monkeypatch.setattr(Path, 'is_file', lambda path: rel(path) in files if rel(path) is not None else real_file(path))
    monkeypatch.setattr(Path, 'is_symlink', lambda path: False if rel(path) is not None else real_link(path))
    monkeypatch.setattr(repo_tests, '_leads_out', lambda *args: False)
    monkeypatch.setattr(repo_tests, '_read_inside', lambda root, path: files[rel(path)])
    monkeypatch.setattr(repo_tests, '_tracked_new', lambda *args: set())
    for layout, names in zip(layouts, producer_listings):
        files = layout
        monkeypatch.setattr(repo_tests, '_private', lambda *args, names=names: [b'', names])
        change = repo_tests.Change(tmp_path, 'a' * 40, 'a' * 40, 'a' * 40, tuple(files), {}, {}, (), ())
        read = repo_tests.diff_read(change)
        listed_names.append(tuple(file.rel for file in read.files))
        identities.append(read.text)
    assert listed_names[0] != listed_names[1]
    assert identities[0] != identities[1], 'one injected filename and two ordinary files receive the exact same patch identity'


# --- the root: one quoting rule for every name, from the code -------------------------------------------------------------------

#: Where a name as git gave it may stand in the functions that build the identity and the copy, by the call it is given to:
#: the one quoting rule (``shown_name``, which ``_name`` is), the scrubber's look at it (``_found``, a yes or no), and what
#: finds the file (``found``, ``_leads_out``, a path joined to the repository's, a ``FileRead`` it is kept in).
NAME_SINKS = {"shown_name", "_name", "_found", "found", "_leads_out", "FileRead"}
#: The functions that put text into what the patch is known by (``diff_read``) and into the copy (``_shown``), with each
#: helper either calls to write a line.
BUILDERS = ((repo_tests, ("diff_read", "now_line", "_link_line", "_part_line", "_bytes_line", "shown_name")),
            (loop, ("_shown", "_modes", "_name", "_remasked", "_masked_pack", "_sent_head", "_issue")))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def test_every_place_a_name_reaches_the_identity_or_the_copy_goes_through_the_one_quoting_rule():
    """From the code: in every function that writes the identity or the copy, a name as git gave it (``rel``, ``one.rel``)
    is only ever handed to the quoting rule, the scrubber's yes-or-no look, or what finds the file on disk; never put into
    text, a format string or another rule. Each ``name`` diff_read writes into text is ``shown_name``'s."""
    bare, places = [], 0
    for module, names in BUILDERS:
        functions = _functions(module)
        for function in names:
            tree = functions[function]
            up = _parents(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "name" for t in node.targets):
                    assert isinstance(node.value, ast.Call) and _callee(node.value) == "shown_name", ast.unparse(node)
                is_rel = (isinstance(node, ast.Name) and node.id == "rel" and isinstance(node.ctx, ast.Load)) or \
                    (isinstance(node, ast.Attribute) and node.attr == "rel")
                if not is_rel:
                    continue
                places += 1
                parent = up[node]
                if isinstance(parent, ast.Call) and node in parent.args and _callee(parent) in NAME_SINKS:
                    continue
                if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):    # a path, joined to the repository's
                    continue
                if function == "shown_name" and isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add):
                    continue                                                          # the rule itself: prefix + rel
                bare.append(f"{function}: {ast.unparse(parent)}")
    assert places >= 10 and not bare, bare


def _quoted(path: bytes) -> str:
    """``path`` as git's C-style quoting writes it, with UTF-8 kept (``core.quotePath`` off), written here from git's
    ``quote_c_style`` table on its own, and with the line breaks of U+0085, U+2028 and U+2029 escaped too."""
    named = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r", 34: '\\"', 92: "\\\\"}
    text = path.decode("utf-8", "surrogateescape")
    out = []
    for ch in text:
        code = ord(ch)
        if code < 128 and code in named:
            out.append(named[code])
        elif code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029) or 0xDC80 <= code <= 0xDCFF:
            out.append("".join(f"\\{b:03o}" for b in ch.encode("utf-8", "surrogateescape")))
        else:
            out.append(ch)
    joined = "".join(out)
    return joined if joined == text else f'"{joined}"'


def _unquoted(shown: str) -> bytes:
    """``shown`` read back by git's C-style quoting grammar (``unquote_c_style``): the bytes of the name it writes."""
    if not shown.startswith('"'):
        assert '"' not in shown and "\\" not in shown, shown
        return shown.encode("utf-8")
    assert shown.endswith('"') and len(shown) >= 2, shown
    named = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
    body, out, at = shown[1:-1], bytearray(), 0
    while at < len(body):
        if body[at] == "\\":
            if body[at + 1] in "01234567":
                out.append(int(body[at + 1:at + 4], 8))
                at += 4
            else:
                out.append(named[body[at + 1]])
                at += 2
        else:
            assert body[at] != '"', shown
            out += body[at].encode("utf-8")
            at += 1
    return bytes(out)


#: The names the brief lists: CR, LF, CRLF, tab, quote, backslash, control bytes (no NUL: git holds none), bytes that are
#: not UTF-8, a key marker over several lines, names that look like a header or a hunk, and names that look empty.
EDGES = {
    "cr": b"odd\rname.py", "lf": b"odd\nname.py", "crlf": b"odd\r\nname.py", "tab": b"odd\tname.py", "quote": b'odd"name.py',
    "backslash": b"odd\\name.py", "controls": b"odd\x01\x07\x08\x0b\x0c\x1b\x7fname.py", "not-utf8": b"odd-\xff\xc3.py",
    "c1-and-separators": "odd\x85  name.py".encode(), "unicode": "café-日本.py".encode(),
    "key-over-lines": b"-----BEGIN PRIVATE KEY-----\nmade-up-name-body\n-----END PRIVATE KEY-----",
    "key-marker": b"-----BEGIN PRIVATE KEY-----", "diff-header": b"x\ndiff --git a/y b/y", "hunk-head": b"@@ -1 +1 @@",
    "hunk-injection": b"a\n@@ -0,0 +1,1 @@\n+X\n--- /dev/null\n+++ b/b", "g6-line": b"x\n\\ G6: the bytes of y",
    "space": b" ", "zero-width": "​".encode(), "quotes-only": b'""',
}
#: The longest path git holds here: PATH_MAX (4096) less its NUL, in folders of 255 bytes, and odd bytes in the last.
LONGEST = (b"d" * 255 + b"/") * 15 + b"\r\n\t\"\\\xff" + b"f" * 249


def _rel(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


@pytest.mark.parametrize("raw", [*EDGES.values(), LONGEST], ids=[*EDGES, "longest"])
def test_a_name_is_one_line_its_bytes_read_back_and_written_as_gits_quoting_writes_it(raw):
    assert len(LONGEST) == 4095
    for prefix in ("", "a/", "b/"):
        shown = repo_tests.shown_name(_rel(raw), prefix)
        shown.encode("utf-8")                                    # text, which a lone surrogate is not
        assert len(shown.splitlines()) == 1 and "\n" not in shown and "\r" not in shown, repr(shown)
        assert _unquoted(shown) == prefix.encode() + raw         # its bytes, read back: two names are two texts
        assert shown == _quoted(prefix.encode() + raw)
    assert loop._name(_rel(raw)) == repo_tests.shown_name(_rel(raw))


def test_no_two_names_are_one_text_and_a_name_no_listing_gives_is_still_text():
    shown = [repo_tests.shown_name(_rel(raw)) for raw in [*EDGES.values(), LONGEST]]
    assert len(set(shown)) == len(shown)
    odd = repo_tests.shown_name("odd-\ud800")      # a lone surrogate git's listing never gives (malformed state)
    odd.encode("utf-8")
    assert odd == '"odd-\\355\\240\\200"'


@pytest.mark.parametrize("label", ["cr", "lf", "crlf", "tab", "quote", "backslash", "controls"])
def test_gits_own_patch_quotes_each_name_as_the_one_rule_does(tmp_path, label):
    """Against git itself: the header git writes for a removed file is the header the one rule writes, byte for byte."""
    repo = make_repo(tmp_path)
    git(repo, "config", "core.protectNTFS", "false")
    raw_git = _raw_git(repo)
    blob = raw_git("hash-object", "-w", "--stdin", data=b"value = 0\n")
    tree = raw_git("mktree", "-z", data=raw_git("ls-tree", "-z", "HEAD") + b"100644 blob " + blob + b"\t" + EDGES[label] + b"\0")
    base = raw_git("commit-tree", tree.decode(), "-m", "odd name").decode()
    rel = _rel(EDGES[label])
    change = repo_tests.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = repo_tests.diff_read(change)
    assert [one.rel for one in read.files] == [rel]
    assert f"\n--- {repo_tests.shown_name(rel, 'a/')}\n" in read.text
    assert f"\n--- {repo_tests.shown_name(rel, 'a/')}\n" in loop._shown(change, read).text


def _listed(where: Path, monkeypatch, gone: dict | None = None, new: dict | None = None) -> repo_tests.Change:
    """A change with names as git's listing gives them, git never run and the working tree emulated, as no file here can
    carry such a name: ``gone``, files the base holds and the working tree lacks (name: bytes at the base), and ``new``,
    untracked files (name: bytes as they stand)."""
    gone, new = gone or {}, new or {}
    where.mkdir(parents=True, exist_ok=True)
    held = {str(where / rel): raw for rel, raw in new.items()}

    def oid(rel: str) -> str:
        return hashlib.sha1(rel.encode("utf-8", "surrogateescape")).hexdigest()

    listed = b"".join(b":100644 000000 " + oid(rel).encode() + b" " + b"0" * 40 + b" D\0"
                      + rel.encode("utf-8", "surrogateescape") + b"\0" for rel in sorted(gone))
    others = b"".join(rel.encode("utf-8", "surrogateescape") + b"\0" for rel in sorted(new))
    real_file, real_link = Path.is_file, Path.is_symlink
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed + b"\0" if listed else b"", others])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(repo_tests, "blob_bytes", lambda repo, name, **kwargs: {oid(r): b for r, b in gone.items()}[name])
    monkeypatch.setattr(repo_tests, "_leads_out", lambda *args: False)
    monkeypatch.setattr(Path, "is_file", lambda path: str(path) in held if str(path).startswith(str(where)) else real_file(path))
    monkeypatch.setattr(Path, "is_symlink", lambda path: False if str(path).startswith(str(where)) else real_link(path))
    monkeypatch.setattr(repo_tests, "_read_inside", lambda root, path: held[str(path)])
    return repo_tests.Change(where, "a" * 40, "a" * 40, "a" * 40, tuple(sorted({*gone, *new})), {}, {}, (), ())


def _both(where: Path, monkeypatch, rel: str) -> tuple[repo_tests.DiffRead, loop.Shown]:
    """The identity and the copy of a change that removes a file named ``rel`` and adds one named ``rel`` beside it."""
    change = _listed(where, monkeypatch, gone={rel: b"value = 0\n"}, new={rel + "-new": b"value = 1\n"})
    read = repo_tests.diff_read(change)
    shown = loop._shown(change, read)
    monkeypatch.undo()
    return read, shown


def test_each_name_is_one_line_of_the_identity_and_the_copy_and_two_names_are_two_of_each(tmp_path, monkeypatch):
    """Each name as one line in what the patch is known by and in the copy (as many lines as a plain name gives), two names
    two identities and two copies, unless the copy is hidden, so no review of it ends clean."""
    plain = _both(tmp_path / "plain", monkeypatch, "plain.py")
    identities, copies = {}, {}
    for number, (label, raw) in enumerate([*EDGES.items(), ("longest", LONGEST)]):
        read, shown = _both(tmp_path / f"case-{number}", monkeypatch, _rel(raw))
        for text in (read.text, shown.text, shown.masked):
            text.encode("utf-8")
        assert read.text.count("\n") == plain[0].text.count("\n"), label
        assert shown.text.count("\n") == plain[1].text.count("\n") and shown.masked.count("\n") == shown.text.count("\n")
        assert f"+++ {repo_tests.shown_name(_rel(raw) + '-new', 'b/')}\n" in read.text
        identities[label], copies[label] = read.text, (shown.masked, shown.hidden)
    assert len(set(identities.values())) == len(identities)
    for (one, (copy_one, hid_one)), (two, (copy_two, hid_two)) in combinations(copies.items(), 2):
        assert copy_one != copy_two or hid_one or hid_two, (one, two)


#: Names the scrubber finds a value in: as quoted too, and, for the last two, only as git gave them, as the escape quoting
#: puts in parts the value from its key.
KEYED = {"key-over-lines": EDGES["key-over-lines"], "key-marker": EDGES["key-marker"], "token": b"token=made-up-value-1234",
         "quoted-value": b'api_key="made-up-value-1234"', "tab-before-value": b"password\t=made-up-value-1234"}


@pytest.mark.parametrize("label", list(KEYED))
def test_a_mask_over_a_name_counts_and_masks_the_name_wherever_it_is_shown(tmp_path, monkeypatch, label):
    rel = _rel(KEYED[label])
    change = _listed(tmp_path, monkeypatch, gone={rel: b"value = 0\n"}, new={rel + "-new": b"value = 1\n"})
    read = repo_tests.diff_read(change)
    shown = loop._shown(change, read)
    reviewer = Reviewer()
    found = loop.run_critic(change, "Review the change", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert shown.hidden and found["verdict"] == "failed" and found["error"] == loop.HIDDEN
    assert "made-up" not in shown.masked and f"--- {MASK}\n+++ /dev/null\n" in shown.masked
    assert f"--- /dev/null\n+++ {MASK}\n" in shown.masked


def test_a_plain_name_beside_them_hides_nothing(tmp_path, monkeypatch):
    change = _listed(tmp_path, monkeypatch, gone={"plain.py": b"value = 0\n"}, new={"odd\nname.py": b"value = 1\n"})
    read = repo_tests.diff_read(change)
    found = loop.run_critic(change, "Review the change", Reviewer(), "Review", tmp_path, patch=read, whole=True)
    assert not loop._shown(change, read).hidden and found["verdict"] == "none"


def _digests(text: str) -> list[str]:
    return re.findall(r"SHA-256 ([0-9a-f]{64})", text)


@pytest.mark.parametrize("label", ["key-marker", "key-over-lines", "token", "g6-line"])
def test_g6s_own_lines_are_never_scrubbed_whatever_a_name_holds(tmp_path, monkeypatch, label):
    """Every digest line G6 writes into the copy is sent as written, the name in it masked or not: none is lost to a mask a
    name started (finding 1), and the base's bytes still tell two copies apart."""
    rel = _rel(EDGES.get(label) or KEYED[label])
    change = _listed(tmp_path, monkeypatch, gone={rel: b"payload = '\xff'\n", "calc.py": b"payload = '\xfe'\r\n"},
                     new={rel + "-new": b"value = '\xff'\n"})
    shown = loop._shown(change, repo_tests.diff_read(change))
    assert len(_digests(shown.text)) == 3 and _digests(shown.masked) == _digests(shown.text)


# --- the root: G6's own lines are added after masking, and every mask is read by the one hidden rule, from the code -----------

#: Every text the scrubber reads in the completion loop, by the function it is read in: the task, each file's text at the base
#: and now, the rest of a parent file a cut fell in, what a run said, and a name (``_found``'s ``text``, a yes or no). No
#: line G6 writes is among them.
SCRUBBED = {("_sent_head", "task"), ("_masked_pack", "task"), ("_shown", "old"), ("_shown", "one.new, one.limit"),
            ("_remasked", "rest"), ("_unrun", "text"), ("_found", "text")}


def test_every_line_g6_writes_is_added_after_masking():
    functions = _functions(loop)
    calls = {(name, ", ".join(ast.unparse(arg) for arg in call.args)) for name, function in functions.items()
             for call in ast.walk(function) if isinstance(call, ast.Call) and _callee(call) == "_seen"}
    nested = {"say", "own", "cuts_of", "shown_line", "numbered"}       # _shown's own, which ast.walk also visits alone
    assert {(name, args) for name, args in calls if name not in nested} == SCRUBBED, calls
    assert not {name for name, _ in calls} & nested
    for name in ("own", "say", "_modes", "_name"):         # what writes G6's lines in the copy masks nothing
        assert not {_callee(call) for call in ast.walk(functions[name]) if isinstance(call, ast.Call)} & {
            "_seen", "_masked", "masked", "secret_spans", "scrub", "_found"}, name
    assert _callee(ast.parse(textwrap.dedent(inspect.getsource(loop._sent_head))).body[0].body[-1].value.values[0].value) \
        == "_issue"     # the token and the issue's own lines go around the task once it is masked


def test_every_mask_that_reaches_the_copy_is_in_what_the_hidden_rule_reads():
    """From the code: in ``_shown`` a mask reaches the copy in two ways alone, and both are counted. ``MASK`` in place of a
    name, chosen in one statement that is followed by the count of masked names; and ``_masked`` in ``shown_line``, whose
    every use for the copy follows the statement that hands the same cuts to ``marks``. Both counts go to ``_hides``, which
    alone decides ``hidden`` for the patch and the task, and ``run_critic`` takes it from ``_masked_pack`` alone."""
    functions = _functions(loop)
    shown = functions["_shown"]
    up = _parents(shown)
    statements = {id(node): node for node in ast.walk(shown) if isinstance(node, ast.stmt)}

    def statement(node):
        while id(node) not in statements:
            node = up[node]
        return node

    masks = [node for node in ast.walk(shown) if isinstance(node, ast.Name) and node.id == "MASK"]
    assert len(masks) == 1
    chosen = statement(masks[0])
    body = up[chosen].body
    assert ast.unparse(chosen).startswith("sent = ") and ast.unparse(body[body.index(chosen) + 1]) == "names += sent != shown"
    assert [owner for owner, call in _calls_by_owner(shown) if _callee(call) == "_masked"] == ["shown_line"]
    uses = [(owner, call) for owner, call in _calls_by_owner(shown) if _callee(call) == "shown_line"]
    copy_uses = [call for owner, call in uses if ast.unparse(statement(call)).startswith("own(")]
    assert len(uses) == 2 and len(copy_uses) == 1
    own_stmt = statement(copy_uses[0])
    body = up[own_stmt].body
    before = ast.unparse(body[body.index(own_stmt) - 1])
    assert before.startswith("marks.extend(") and "for a, b, value in cuts" in before
    assert ast.unparse(copy_uses[0].args[-1]) == "cuts"
    the_other = next(call for _, call in uses if call is not copy_uses[0])
    assert "masked_parents.append" in ast.unparse(statement(the_other))        # the parents: no part a review must read whole
    assert ast.unparse(shown.body[-1]) == ("return Shown(''.join(plain), ''.join(masked), _hides(marks, names), "
                                           "tuple(parents), tuple(masked_parents))")
    callers = {name for name, function in functions.items() if name not in {"say", "own", "cuts_of", "shown_line"}
               for call in ast.walk(function) if isinstance(call, ast.Call) and _callee(call) == "_hides"}
    assert callers == {"_shown", "_masked_pack"}
    critic = functions["run_critic"]
    assert "sent, hidden = _masked_pack(change, shown, task, " in ast.unparse(critic)
    assert len([node for node in ast.walk(critic) if isinstance(node, ast.Name) and node.id == "hidden"]) == 2


def _calls_by_owner(function: ast.FunctionDef):
    """Each call in ``function`` with the name of the innermost function it is in."""
    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.Lambda)):
                yield from walk(child, getattr(child, "name", owner))
                continue
            if isinstance(child, ast.Call):
                yield owner, child
            yield from walk(child, owner)
    yield from walk(function, function.name)


def test_the_one_hidden_rule_reads_names_and_lines_alike_for_the_task_and_the_patch():
    assert not loop._hides([])
    assert not loop._hides([("a", 1), ("a", 1), ("b", 2)])
    assert loop._hides([("a", 1), ("a", 2)])
    assert loop._hides([], names=1)
    task = 'password = "made-up-first-part\rmade-up-second-part"'
    change = repo_tests.Change(Path("."), "a" * 40, "a" * 40, "a" * 40, (), {}, {}, (), ())
    empty = loop.Shown("", "", False)
    assert loop._masked_pack(change, empty, task, "review " + "0" * 32)[1]
    assert not loop._masked_pack(change, empty, 'password = "made-up-one-line"', "review " + "0" * 32)[1]


# --- a stop between the steps that write state ----------------------------------------------------------------------------------

def test_a_round_on_names_git_quotes_keeps_its_patch_and_a_read_after_a_restart_names_the_same_patch(tmp_path):
    """The round's state as it is written: the patch kept byte for byte, then the review. A restart between the two, or after
    both, reads the patch again, and the same tree is the same patch, so nothing reads as changed that did not change."""
    repo = make_repo(tmp_path)
    git(repo, "config", "core.protectNTFS", "false")
    raw_git = _raw_git(repo)
    blob = raw_git("hash-object", "-w", "--stdin", data=b"value = 0\n")
    entries = b"".join(b"100644 blob " + blob + b"\t" + raw + b"\0" for raw in (EDGES["cr"], EDGES["lf"], EDGES["quote"]))
    base = raw_git("commit-tree", raw_git("mktree", "-z", data=raw_git("ls-tree", "-z", "HEAD") + entries).decode(),
                   "-m", "odd names").decode()
    (repo / "calc.py").write_bytes(b"value = 1\n")
    rels = tuple(_rel(raw) for raw in (EDGES["cr"], EDGES["lf"], EDGES["quote"]))

    def changed() -> repo_tests.Change:     # the change, as each step finds it anew
        return repo_tests.Change(repo, base, base, base, (*rels, "calc.py"), {}, {}, ("calc.py",), ())

    reviews, session = [], tmp_path / "session"
    loop.review_round(changed(), "Review the change", Reviewer(), loop.harness(), session, reviews, float("inf"))
    kept = (session / "rounds" / "round-1.patch").read_bytes()
    again = repo_tests.diff_read(changed())       # the read a restarted step makes
    assert kept == again.text.encode("utf-8") and reviews[0]["patch"] == digest(again.text)
    assert reviews[0]["verdict"] == "none"
    for raw in (EDGES["cr"], EDGES["lf"], EDGES["quote"]):
        assert f"--- {repo_tests.shown_name(_rel(raw), 'a/')}\n" in again.text
