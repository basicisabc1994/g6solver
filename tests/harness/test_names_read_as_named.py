# SPDX-License-Identifier: Apache-2.0
"""A name git lists is opened only where Windows reads it as itself (review-maskfu4, findings 1 and 2).

Win32 rewrites some paths before it opens them: trailing dots and spaces are dropped, and a device name such as ``nul`` is
read as the device. A file stored under such a name (made by its literal, extended-form name, or by another system) is
listed by git, and a plain open of the listed name reaches another file, or a device, or nothing. So:

* the one disk rule (``repo_tests._on_disk``) asks Windows itself how it reads the name, and a name it reads as another's
  is never exact, so no reader in ``repo_tests`` opens it;
* the read of a patch (``diff_read``) refuses a listing that holds such a name as there (untracked, added or changed),
  before it looks at anything, naming it. One git lists as removed is read as removed where nothing is stored under the
  name itself (review revmaskfu2a, finding 2: tests/harness/test_listed_things_are_named.py).

The rule is the system's own (``GetFullPathNameW``), not a list kept here, so the inventory test below checks it against
what a plain open reaches for a family of names, on the system the tests run on. A name Windows cannot store at all (a
control character, ``< > " | ? *``) is not rewritten: its open fails, and the read's existing refusals cover it (the
control cases here). An 8.3 short name is not covered: stated as a limit where the rule is written.
"""
import ast
import inspect
import os
from pathlib import Path

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt
from tests.harness.test_name_crossings import _base, _gone, _record, git_bytes
from tests.harness.test_shown_names import EDGES
from tests.workforce.test_repo_tests import git, make_repo

_EXTENDED = "\\\\?\\"
windows = pytest.mark.skipif(os.name != "nt", reason="Win32 rewrites a path before it opens it")

#: Names Windows reads as another's, on every Windows there is: a last part ending in dots or spaces, a folder ending in
#: one dot, the ``nul`` device (with dots or spaces after it), and a name that is only dots or spaces.
ANOTHERS = ["x.py.", "x.py ", "x.py..", "x.py. .", "dir./x.py", "nul", "NUL", "dir/nul", "nul.", "nul ", " ", "..."]
#: Names Windows reads as themselves here, among them the ones it cannot store (their open fails; nothing else is read).
THEMSELVES = ["plain.py", "dir/plain.py", "dir /x.py", "dir../x.py", ".gitignore", "café.py", "x.py:stream",
              "odd\\name.py", "a\nb", "x?.py", "x*.py", 'x".py', "x<.py", "x>.py", "x|.py", "x.py\t"]


@pytest.fixture
def literal(tmp_path):
    """Writes a file by its literal name (the extended form: Win32 rewrites nothing in it), and takes each one away again
    by that name when the test ends, as no plain removal reaches it."""
    made: list[Path] = []

    def write(path: Path, data: bytes) -> bool:
        full = Path(_EXTENDED + str(path))
        try:
            for folder in reversed(full.parents):
                if len(folder.parts) > len(Path(_EXTENDED + str(tmp_path)).parts) and not folder.exists():
                    folder.mkdir()
                    made.append(folder)
            existed = os.path.lexists(full)
            full.write_bytes(data)
        except OSError:
            return False        # a name this volume cannot store
        if not existed:
            made.append(full)
        return True

    yield write
    for path in reversed(made):
        try:
            os.rmdir(path) if path.is_dir() else os.unlink(path)
        except OSError:
            pass


# --- review-maskfu4's two findings, each as its reviewer wrote it ---------------------------------------------------------------

def _read_text_or_refusal(repo: Path):
    try:
        return rt.diff_read(rt.change(repo, "HEAD"))
    except rt.RepoRefused:
        return None


@windows
def test_finding_1_a_listed_name_with_a_trailing_dot_is_read_as_that_file_or_refused(tmp_path, literal):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "x.py").write_bytes(b"value = 1\n")
    git(repo, "add", "x.py")
    git(repo, "commit", "-q", "-m", "base")
    listed = repo / "x.py."
    assert literal(listed, b"payload = 2\n")
    assert b"x.py.\0" in rt._git(repo, "ls-files", "--others", "--exclude-standard", "-z")   # git lists it
    first = _read_text_or_refusal(repo)
    assert literal(listed, b"payload = 3\n")
    second = _read_text_or_refusal(repo)
    if first is None or second is None:
        return      # refused: the file G6 could not open by its name was never read as another
    one = next(f for f in first.files if f.rel == "x.py.")
    assert one.refusal is not None or one.unread or one.new == "payload = 2\n", (
        f"the reviewer is shown {one.new!r} as the text of x.py., the bytes of x.py")
    assert first.text != second.text, "two trees that differ in the bytes of x.py. give one identity"


@windows
def test_finding_2_an_untracked_file_g6_cannot_open_by_its_name_is_named_and_refuses_the_copy(tmp_path, literal):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "calc.py").write_bytes(b"value = 1\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "base")
    assert literal(repo / "nul", b"payload = 2\n")
    assert Path(_EXTENDED + str(repo / "nul")).is_file()     # a regular file, with bytes
    assert b"nul\0" in rt._git(repo, "ls-files", "--others", "--exclude-standard", "-z")   # git lists it as there
    change = rt.change(repo, "HEAD")
    try:
        read = rt.diff_read(change)
        one = next(f for f in read.files if f.rel == "nul")
        loop._shown(change, read)
    except rt.RepoRefused:
        return      # refused: never shown as nothing
    assert one.new == "payload = 2\n" or one.unread or rt.shown_name(one.rel) in read.text, (
        "a file git lists as there, which G6 could not open by its name, has no line in what the patch is known by, "
        "and the reviewer's copy shows nothing of it")


# --- the root: the one disk rule asks Windows how it reads a name ------------------------------------------------------------------

def _name(text: str) -> rt.GitName:
    return rt.GitName(text.encode("utf-8"))


@pytest.mark.parametrize("name", ANOTHERS)
def test_a_name_windows_reads_as_anothers_is_never_exact_and_elsewhere_is_its_bytes(name):
    assert rt._on_disk(_name(name)) == (name, os.name != "nt")
    assert (rt._disk(Path(os.getcwd()), _name(name)) is None) == (os.name == "nt")


@pytest.mark.parametrize("name", THEMSELVES + [raw.decode() for label, raw in EDGES.items()
                                               if label not in ("not-utf8", "space")])
def test_a_name_windows_reads_as_itself_stays_exact(name):
    assert rt._on_disk(_name(name)) == (name, True)


#: The family the inventory walks: each stem under each ending, as a file at the top and in a folder, and as a folder, in
#: both cases (the lower case in a folder of its own, as one folder holds one of them). The stems are a plain name, a name
#: of characters outside the BMP and the device names Windows documents; the endings are the ones it documents as dropped, and some it keeps.
_DEVICES = ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
            *[f"{port}{digit}" for port in ("COM", "LPT") for digit in "019¹"]]
#: A stem of two characters outside the Basic Multilingual Plane, each two UTF-16 units to Windows (review revmaskfu2a,
#: finding 3: the rule counted them as one each, and called every such name rewritten).
_BEYOND = "\U0001F642\U00020000"
_ENDINGS = ["", ".", " ", "..", " .", ". ", ".py", ".py.", ".py ", " .py", ".tar.gz", ":s", " "]
FAMILY = sorted({case + shape.format(stem=stem.lower() if case else stem, end=end)
                 for stem in ["PLAIN", _BEYOND, *_DEVICES] for end in _ENDINGS for case in ("", "lower/")
                 for shape in ("{stem}{end}", "dir/{stem}{end}", "as-folder/{stem}{end}/inner.py")})


@windows
def test_wherever_the_disk_rule_says_exact_a_plain_open_reaches_the_file_stored_under_that_name(tmp_path, literal):
    """The inventory, from the system itself and not from the names the reviews found: every name of the family is stored
    by its literal name, each with its own bytes (the name a rewritten path reaches is in the family too, so it is there
    with other bytes); then, for each one the rule calls exact, the bytes a plain open reads (``_read_inside``, the read's
    own) are the bytes stored under that name, or nothing is read. One the rule does not call exact is never opened
    (``_disk`` gives no path). The rule does not refuse them all: the plain names are read."""
    root = tmp_path.resolve()
    stored = {name: literal(root / name, b"stored under " + name.encode()) for name in FAMILY}
    assert sum(stored.values()) > len(FAMILY) // 2      # the volume stores most of them: the walk is not empty
    exact, wrong = [], []
    for name in sorted(stored):
        on_disk, as_itself = rt._on_disk(_name(name))
        if not as_itself:
            assert rt._disk(root, _name(name)) is None
            continue
        exact.append(name)
        try:
            read = rt._read_inside(root, root / on_disk)
        except OSError:
            read = None
        if read is not None and not (stored[name] and read == b"stored under " + name.encode()):
            wrong.append((name, read))
    assert not wrong, wrong
    assert {"PLAIN.py", "dir/PLAIN.py", "lower/plain", "as-folder/PLAIN/inner.py", _BEYOND + ".py",
            f"as-folder/{_BEYOND}/inner.py"} <= set(exact)


#: Each function of ``repo_tests`` that takes a listed name's path without asking whether the name is exact (``_in``, or
#: ``_on_disk``'s name alone), with how often and what it does with it: none opens a file of the repository to show it. A
#: place not here fails the inventory below.
NAME_ALONE = {
    ("_in", "_on_disk[0]"): (1, "the rule's own: the name for a look, or for a place in a copy"),
    ("_gitlinks", "_on_disk[0]"): (1, "looks whether a submodule's folder was set up, where git would run in it"),
    ("_held", "_on_disk[0]"): (1, "looks whether a marked file is in the working tree; its bytes are read by _disk"),
    ("_index_path", "_on_disk[0]"): (1, "looks at each folder on the way for a link"),
    ("links_outside", "_on_disk[0]"): (1, "looks for a link on the way and where it resolves: a yes or no"),
    ("_submodule", "_on_disk[0]"): (1, "hashes a submodule's folder where git and a run find it: a digest, compared"),
    ("_test_id", "_on_disk[0]"): (1, "a pytest id's file, its shape and where it resolves; the run opens it, not G6"),
    ("loadable_changes", "_on_disk[0]"): (2, "looks whether a file is there, or is a link; what is read is _disk's"),
    ("ignored_inputs.looked", "_on_disk[0]"): (1, "looks at an ignored path: a link, a folder; what is read is opened's"),
    ("_unscanned", "_in"): (1, "looks for an environment's marker on the way"),
    ("_joined", "_in"): (1, "a place in G6's own copy, while a link is resolved inside it"),
    ("_copy_links", "_in"): (6, "places in G6's own copy: made, linked, removed"),
    ("repository_copy", "_in"): (7, "the folders on the way looked at; a tracked link's own text and a submodule's folder "
                                    "taken where git and a run find them; the place in the copy. A file's bytes: source_of"),
    ("_base_revision", "_in"): (2, "looks in G6's own copy; what is written there is _disk's"),
}


def test_every_path_made_from_a_listed_name_asks_whether_it_is_exact_or_is_ruled_on():
    """The inventory of the readers, from the code: each call of the disk rule in ``repo_tests`` either gives no path for a
    name that is not exact (``_disk``), or takes the answer with the name and reads it (``on_disk, exact = ...``), or is
    one of ``NAME_ALONE``."""
    tree = ast.parse(inspect.getsource(rt))
    up = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node: ast.AST) -> str:
        names = []
        while node in up:
            node = up[node]
            if isinstance(node, ast.FunctionDef):
                names.append(node.name)
        return ".".join(reversed(names))

    alone, asked, total = {}, set(), 0
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        if call.func.id not in ("_on_disk", "_disk", "_in"):
            continue
        total += 1
        above = up[call]
        if call.func.id == "_disk":
            continue
        if call.func.id == "_on_disk" and isinstance(above, ast.Assign) and isinstance(above.targets[0], ast.Tuple):
            assert [name.id for name in above.targets[0].elts] == ["on_disk", "exact"], ast.unparse(above)
            asked.add(owner(call))
            continue
        assert call.func.id == "_in" or (isinstance(above, ast.Subscript) and ast.unparse(above.slice) == "0"), (
            f"{owner(call)}: {ast.unparse(above)}")
        key = (owner(call), "_in" if call.func.id == "_in" else "_on_disk[0]")
        alone[key] = alone.get(key, 0) + 1
    assert alone == {key: count for key, (count, _) in NAME_ALONE.items()}
    assert asked == {"_disk", "_tracked_new", "diff_read.found"} and total == 40
    functions = {owner(node) + "." + node.name if owner(node) else node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    for name in asked:      # the answer is read, not dropped
        assert sum(isinstance(node, ast.Name) and node.id == "exact" for node in ast.walk(functions[name])) >= 2, name


# --- the read refuses such a name git lists as there, before it looks at anything ---------------------------------------------------

def _refuses(change: rt.Change, name: str) -> None:
    with pytest.raises(rt.RepoRefused) as refused:
        rt.diff_read(change)
    assert refused.value.code == "G6_E_GIT_COMMAND_FAILED"
    assert str(refused.value) == f"{rt.shown_name(_name(name))} {rt.READ_AS_ANOTHER}"


def _fail_on(monkeypatch, *readers: str) -> None:
    def looked(*args, **kwargs):
        pytest.fail("the read looked at the disk for a name Windows reads as another's")

    for reader in readers:
        monkeypatch.setattr(rt, reader, looked)


def _listing(monkeypatch, name: str, listed: str) -> None:
    """git's listing of one file, as given (git never run): untracked, changed, or removed."""
    raw = name.encode()
    changed = (b":100644 100644 " + b"b" * 40 + b" " + b"0" * 40 + b" M\0" + raw + b"\0\0" + b"diff --git a/" + raw + b" b/"
               + raw + b"\nindex bbbbbbb..ccccccc 100644\n--- a/" + raw + b"\n+++ b/" + raw + b"\n@@ -1 +1 @@\n-a\n+b\n")
    tracked = {"untracked": b"", "removed": _record(raw) + b"\0" + _gone(raw), "changed": changed}[listed]
    monkeypatch.setattr(rt, "_private", lambda *args: [tracked, raw + b"\0" if listed == "untracked" else b""])
    monkeypatch.setattr(rt, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(rt, "blob_bytes", lambda *args, **kwargs: b"value = 0\n")


@windows
@pytest.mark.parametrize("name", ANOTHERS)
@pytest.mark.parametrize("listed", ["untracked", "changed"])
def test_the_read_refuses_a_name_git_lists_as_there_that_windows_reads_as_anothers(tmp_path, monkeypatch, name, listed):
    """Untracked, or changed: what git says of a changed one's bytes it read through the same rewriting."""
    (tmp_path / "x.py").write_bytes(b"another file's bytes\n")
    _listing(monkeypatch, name, listed)
    _fail_on(monkeypatch, "_leads_out", "_read_inside", "_link_inside")
    _refuses(rt.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ()), name)


@windows
@pytest.mark.parametrize("name", ANOTHERS)
def test_a_name_git_lists_as_removed_is_read_as_removed_and_never_opened(tmp_path, monkeypatch, name):
    """git's word that nothing is there is taken, as for any removed file (a base made elsewhere may hold a name Windows
    cannot): its bytes at the base are git's own, and nothing is opened."""
    (tmp_path / "x.py").write_bytes(b"another file's bytes\n")
    _listing(monkeypatch, name, "removed")
    _fail_on(monkeypatch, "_read_inside", "_link_inside")
    read = rt.diff_read(rt.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ()))
    one, = read.files
    assert (one.new, one.refusal, one.old) == (None, None, b"value = 0\n") and "another file's bytes" not in read.text


@windows
def test_against_git_itself_a_changed_file_under_such_a_name_refuses_and_a_removed_one_reads_as_removed(tmp_path):
    """The base tree holds ``x.py.`` and ``nul`` (made by git's plumbing). git for Windows looks through the same
    rewriting: it reads ``x.py`` for ``x.py.`` and lists it as changed, and lists ``nul`` as removed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    base = _base(repo, {b"x.py.": b"value = 0\n", b"nul": b"value = 0\n", b"x.py": b"value = 0\n"})
    git_bytes(repo, "update-ref", "HEAD", base)
    git_bytes(repo, "read-tree", base)
    (repo / "x.py").write_bytes(b"value = 1\n")       # what the rewritten name reaches
    listed = git_bytes(repo, "diff", "--name-status", "-z").split(b"\0")
    assert dict(zip(listed[1::2], listed[::2])) == {b"x.py.": b"M", b"x.py": b"M", b"nul": b"D"}
    _refuses(rt.Change(repo, base, base, base, (_name("x.py."), _name("nul"), _name("x.py")), {}, {}, (), ()), "x.py.")
    (repo / "x.py").unlink()                          # now git lists all three as removed
    read = rt.diff_read(rt.Change(repo, base, base, base, (_name("x.py."), _name("nul"), _name("x.py")), {}, {}, (), ()))
    assert {one.rel: (one.new, one.refusal) for one in read.files} == dict.fromkeys(("nul", "x.py", "x.py."), (None, None))

@windows
def test_no_reader_of_a_changes_files_opens_a_name_windows_reads_as_anothers(tmp_path, monkeypatch):
    """The readers beside the patch's own (``change``'s texts and sources, ``related_texts``, the snapshot): each asks the
    one disk rule, so none reads the other file's bytes under the listed name."""
    repo = make_repo(tmp_path)
    alias = _name("tests./test_calc.py")          # Windows reads tests/test_calc.py for it
    real = rt._private

    def private(root, commit, *commands):
        found = real(root, commit, *commands)
        return [out + alias.raw + b"\0" if command[:2] == ("ls-files", "--others") else out
                for command, out in zip(commands, found)]

    monkeypatch.setattr(rt, "_private", private)
    change = rt.change(repo)
    assert alias in change.files and rt.is_test_file(alias)
    assert alias not in change.after and alias not in change.sources
    assert alias not in rt.related_texts(change)[1]
    assert rt.snapshot(repo, [alias])[alias] == "unreadable"
    monkeypatch.undo()
    assert rt.derive_tests(rt.Change(repo, "HEAD", change.base_commit, change.head, (alias,), {}, {}, (), ())) == ([], 0)


# --- controls: what Windows cannot store is not rewritten, and what it reads as itself is read ---------------------------------------

@windows
@pytest.mark.parametrize("char", [*'<>"|?*', "\x01", "\t", "\n", "\x1f"])
def test_a_listed_name_windows_cannot_store_reads_no_other_files_bytes(tmp_path, monkeypatch, char):
    """A character Windows keeps out of every name makes no path it rewrites: the open fails, so the read names the file as
    listed and not found, and the copy refuses. ``?`` and ``*`` match no decoy here."""
    name = f"x{char}.py"
    for decoy in ("x.py", "xa.py", "x..py"):
        (tmp_path / decoy).write_bytes(b"another file's bytes\n")
    monkeypatch.setattr(rt, "_private", lambda *args: [b"", name.encode() + b"\0"])
    monkeypatch.setattr(rt, "_tracked_new", lambda *args: set())
    change = rt.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ())
    assert rt._on_disk(_name(name)) == (name, True)
    read = rt.diff_read(change)
    assert "another file's bytes" not in read.text and read.files[0].new is None
    assert f"\\ G6: {rt.shown_name(_name(name))} {rt.NOT_THERE}\n" in read.text
    with pytest.raises(rt.RepoRefused):
        loop._shown(change, read)


@windows
@pytest.mark.parametrize("name", ["dir /x.py", "dir../x.py"])
def test_a_listed_name_windows_reads_as_itself_is_read_as_that_file(tmp_path, monkeypatch, literal, name):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "x.py").write_bytes(b"another file's bytes\n")
    assert literal(tmp_path / name, b"listed = 1\n")
    monkeypatch.setattr(rt, "_private", lambda *args: [b"", name.encode() + b"\0"])
    monkeypatch.setattr(rt, "_tracked_new", lambda *args: set())
    read = rt.diff_read(rt.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ()))
    assert [one.new for one in read.files] == ["listed = 1\n"]
