# SPDX-License-Identifier: Apache-2.0
"""review-mask4's three findings on claude/masking-files, each as its reviewer wrote it, and the classes they belong to.

A link's path is read where G6 decides where it is (check_then_act_race); a name git gives is put into text through the one
decoder, and named by its bytes where they are not UTF-8 (boundary_case); and the copy of the base the reviewer is shown is
the base this read read and the patch is known by (check_then_act_race). Git runs only on scratch repositories, reviewers
are recorded, and no test of a reviewer's ever runs."""
import ast
import hashlib
import inspect
import os
import re
import stat
import subprocess
import textwrap
import zlib
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.harness import completion_loop as loop
from g6.workforce import repo_tests
from tests.harness.test_review_copy import link_read
from tests.workforce.test_repo_tests import GIT_ENV, git, make_repo


def _junction(target: Path, link: Path) -> None:
    """``link`` made a folder link to ``target``: a junction on Windows, which any account may make, else a symlink."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_folder(link: Path) -> None:
    os.rmdir(link) if os.name == "nt" else link.unlink()


# --- review-mask4's three findings, each as its reviewer wrote it ---------------------------------------------------------

def test_finding_1_a_symlink_read_rechecks_its_parent_after_a_directory_swap(tmp_path, monkeypatch):
    repo, outside = tmp_path / 'repo', tmp_path / 'outside'
    folder, saved = repo / 'folder', repo / 'saved'
    folder.mkdir(parents=True)
    outside.mkdir()
    # Emulate the final file symlinks only: this Windows account cannot create
    # them. The parent-directory replacement and its containment are real.
    (folder / 'link').write_bytes(b'placeholder')
    (outside / 'link').write_bytes(b'placeholder')
    targets = {folder / 'link': 'inside-target', outside / 'link': 'outside-target'}
    outside_reads = []
    original_is_symlink, original_readlink = Path.is_symlink, os.readlink

    def is_symlink(path):
        return path == folder / 'link' or original_is_symlink(path)

    def readlink(path, *args, **kwargs):
        path = Path(path)
        if path == folder / 'link':
            actual = path.parent.resolve() / path.name
            if actual.is_relative_to(outside):
                outside_reads.append(actual)
            return targets[actual]
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'is_symlink', is_symlink)
    monkeypatch.setattr(os, 'readlink', readlink)
    original_leads_out = repo_tests._leads_out
    swapped = False

    def swap_after_check(root, rel):
        nonlocal swapped
        checked = original_leads_out(root, rel)
        if swapped:
            return checked
        assert not checked
        folder.rename(saved)
        _junction(outside, folder)
        swapped = True
        return checked

    monkeypatch.setattr(repo_tests, '_leads_out', swap_after_check)
    monkeypatch.setattr(repo_tests, '_private', lambda *args: [b'', b'folder/link\0'])
    monkeypatch.setattr(repo_tests, '_tracked_new', lambda *args: set())
    change = repo_tests.Change(repo, 'a' * 40, 'a' * 40, 'a' * 40,
                               ('folder/link',), {}, {}, (), ())
    try:
        read = repo_tests.diff_read(change)
        assert not outside_reads, 'readlink was called below the replacement link outside the repository'
        assert read.files[0].unread and read.files[0].new is None
    finally:
        _unlink_folder(folder)
        saved.rename(folder)


class Reviewer:
    model = 'recorded-reviewer'

    def complete(self, system, user):
        token = re.search(r'review [0-9a-f]{32}', user)[0]
        return f'READ: {token}\nRULE: none\nVERDICT: none\nWHY: none\n'


def _raw_name_base(repo: Path) -> str:
    """A commit of ``repo``'s tree plus a folder ``linked`` holding one file whose name has the byte 0xff, made with git's
    own plumbing (no working file can carry such a name here). Its id."""
    def git_bytes(*args, data=None):
        return subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', *args],
                              input=data, capture_output=True, check=True, env=GIT_ENV).stdout.strip()

    blob = git_bytes('hash-object', '-w', '--stdin', data=b'value = 0\n')
    subtree = git_bytes('mktree', '-z', data=b'100644 blob ' + blob + b'\todd-\xff.txt\0')
    original = git_bytes('ls-tree', '-z', 'HEAD')
    tree = git_bytes('mktree', '-z', data=original + b'040000 tree ' + subtree + b'\tlinked\0')
    return git_bytes('commit-tree', tree.decode(), '-m', 'raw filename fixture').decode()


def test_finding_2_unread_non_utf8_git_path_can_be_kept_for_the_review(tmp_path):
    repo = make_repo(tmp_path)
    base = _raw_name_base(repo)
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = repo / 'linked'
    _junction(outside, link)
    rel = b'linked/odd-\xff.txt'.decode('utf-8', 'surrogateescape')
    (repo / 'calc.py').write_bytes(b'value = 1\n')
    try:
        change = repo_tests.change(repo, base)
        assert 'calc.py' in change.sources
        read = repo_tests.diff_read(change)
        assert next(file for file in read.files if file.rel == rel).unread
        reviews = []
        failure = None
        try:
            loop.review_round(change, 'Review the unread file', Reviewer(), loop.harness(),
                              tmp_path / 'session', reviews, float('inf'))
        except UnicodeEncodeError as exc:
            failure = exc
        assert failure is None, 'a filename emitted by git crashes review_round while saving the identity'
        assert reviews[0]['verdict'] == 'none'
    finally:
        _unlink_folder(link)


def test_finding_3_a_base_object_changed_after_the_read_is_verified_or_kept_from_that_read(tmp_path):
    repo = make_repo(tmp_path)
    source = repo / 'calc.py'
    source.write_bytes(b'value = 0\n')
    git(repo, 'add', 'calc.py')
    git(repo, 'commit', '-qm', 'base object fixture')
    source.write_bytes(b'value = 1\n')
    change = repo_tests.change(repo)
    read = repo_tests.diff_read(change)
    one, = read.files
    assert '-value = 0\n' in read.text
    obj = repo / '.git' / 'objects' / one.base[:2] / one.base[2:]
    original = obj.read_bytes()
    raw = b'value = 8\n'
    refused, shown = False, None
    os.chmod(obj, stat.S_IREAD | stat.S_IWRITE)
    try:
        # A caller controlling this checkout can replace a loose object between
        # the git diff and the later cat-file; cat-file does not check its hash.
        obj.write_bytes(zlib.compress(b'blob ' + str(len(raw)).encode() + b'\0' + raw))
        try:
            shown = loop._shown(change, read)
        except repo_tests.RepoRefused as exc:
            assert exc.code == 'G6_E_GIT_COMMAND_FAILED'
            refused = True
    finally:
        obj.write_bytes(original)
    assert repo_tests.diff_read(change).text == read.text
    assert refused or '-value = 0\n' in shown.masked, 'the reviewer copy uses a base different from its unchanged identity'


# --- the inventories, built from the code ---------------------------------------------------------------------------------

def _functions(module) -> dict[str, ast.FunctionDef]:
    """Each function of ``module`` by name, those nested in another too."""
    tree = ast.parse(inspect.getsource(module))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _callee(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _own_calls(function: ast.FunctionDef):
    """The calls ``function`` makes itself, with the name of the function each is in: a nested function's own are its."""
    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                yield from walk(child, child.name)
                continue
            if isinstance(child, ast.Call):
                yield owner, child
            yield from walk(child, owner)
    yield from walk(function, function.name)


#: What reads the file system or git, by the name it is called by: a look at a path, an open, a read, a git command.
READS = {"readlink", "open", "fdopen", "read", "read_bytes", "read_text", "is_symlink", "is_file", "is_dir", "is_junction",
         "exists", "lexists", "stat", "lstat", "resolve", "realpath", "samefile", "iterdir", "listdir", "scandir", "create",
         "_linked", "_leads_out", "_read_inside", "_link_inside", "_where", "_git", "_private", "_tracked_new", "blob_bytes",
         "read_at", "texts_at", "diff_read", "diff_text", "change"}


def test_each_place_the_read_looks_at_or_reads_the_working_tree_is_one_that_decides_on_what_it_holds():
    """check_then_act_race, at its root: every place ``diff_read`` looks at the working tree or reads it, from the code. A look
    (``_leads_out``, ``is_symlink``, ``is_file``) only chooses a reader or names a file unread; each reader decides where
    what it reads lies on what it holds open (``_read_inside``: the file; ``_link_inside``: the link's folder), and reads
    nothing else by path but what that open decided, on Windows by the held folder's own final path. git is asked by commit
    and object name (``_private``, ``_tracked_new``, ``blob_bytes``). ``lexists`` (review-maskfu2, finding 3) only tells
    an untracked folder git lists, which shows nothing, from a name with nothing there, which refuses the read."""
    functions = _functions(repo_tests)
    found = {(owner, _callee(call)) for owner, call in _own_calls(functions["diff_read"]) if _callee(call) in READS}
    assert found == {("diff_read", "_private"), ("diff_read", "_tracked_new"), ("diff_read", "resolve"),
                     ("diff_read", "blob_bytes"), ("found", "_leads_out"), ("found", "is_symlink"), ("found", "is_file"),
                     ("found", "lexists"), ("found", "_link_inside"), ("found", "_read_inside")}, found
    readers = {name: [(_callee(call), ast.unparse(call)) for _, call in _own_calls(functions[name]) if _callee(call) in READS]
               for name in ("_read_inside", "_link_inside")}
    assert [callee for callee, _ in readers["_read_inside"]] == ["open", "_where", "fdopen", "read"], readers
    assert [text for _, text in readers["_link_inside"]] == [
        "os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))",
        "create(str(path.parent), 1, 1 | 2, None, 3, 33554432, None)", "_where(held)",
        "os.readlink(folder / path.name)", "os.readlink(path.name, dir_fd=held)"], readers


def test_the_copy_reads_nothing_and_is_made_from_the_read_alone():
    """check_then_act_race, for what the reviewer is shown: from the code, every function ``_shown`` reaches makes no read of
    the file system or of git, so the copy is the read's own bytes and nothing read after it."""
    functions, reached, todo, used = _functions(loop), set(), ["_shown"], set()
    while todo:
        name = todo.pop()
        if name in reached:
            continue
        reached.add(name)
        for _, call in _own_calls(functions[name]):
            callee = _callee(call)
            if isinstance(call.func, ast.Attribute) and getattr(call.func.value, "id", "") == "repo_tests":
                used.add(callee)
            elif callee in functions:        # a function of the module, or a method of its own (``Seen.masked``)
                todo.append(callee)
            assert callee not in READS, f"{name} reads ({ast.unparse(call)})"
    assert used == {"_decoded", "shown_name", "_normal", "_cut_lines", "_bytes_line", "now_line"}, used
    for name in used - {"_decoded", "_normal", "_cut_lines"}:      # the name and bytes rules read nothing either
        assert not {_callee(call) for _, call in _own_calls(_functions(repo_tests)[name])} & READS


def _bare_path(expr: ast.AST) -> bool:
    """Whether ``expr`` puts a path as git gave it (``rel``, ``one.rel``) into text other than through the one name rule."""
    if isinstance(expr, ast.Call) and _callee(expr) in {"shown_name", "name_line", "_name"}:
        return False
    if (isinstance(expr, ast.Name) and expr.id == "rel") or (isinstance(expr, ast.Attribute) and expr.attr == "rel"):
        return True
    return any(_bare_path(child) for child in ast.iter_child_nodes(expr))


def test_each_place_a_name_git_gave_is_put_into_text_goes_through_the_one_name_rule():
    """boundary_case, at its root: from the code, every text ``diff_read`` and the copy (``_shown``, ``_modes``) build, each
    formatted value and each text a line is made of, puts a path in only as ``repo_tests.shown_name`` shows it (``_name`` in
    the copy is that rule), and never as git gave it, with a byte that is not UTF-8 as a lone surrogate no text can hold."""
    places, bare = [], []
    for module, name in ((repo_tests, "diff_read"), (loop, "_shown"), (loop, "_modes")):
        tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(module, name))))
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue):
                places.append(ast.unparse(node.value))
                if _bare_path(node.value):
                    bare.append(f"{name}: {{{ast.unparse(node.value)}}}")
            elif isinstance(node, ast.Call) and _callee(node) in {"_bytes_line", "named", "unread", "own"}:
                places.append(ast.unparse(node))
                if any(_bare_path(arg) for arg in node.args):
                    bare.append(f"{name}: {ast.unparse(node)}")
    assert len(places) > 20 and not bare, bare


# --- check_then_act_race: a link read where G6 decides it lies ---------------------------------------------------------------

def _can_symlink(where: Path) -> bool:
    try:
        os.symlink("target", where / "probe-link")
    except OSError:
        return False
    os.unlink(where / "probe-link")
    return True


def test_a_links_folder_swapped_for_a_link_out_as_the_link_is_read_is_never_read_through(tmp_path, monkeypatch):
    """The swap at the worst moment: after every look G6 makes, as the link is read. Where links can be made, both are real
    and the read must be in the folder held; where not (this Windows account), the link is a placeholder and the swap is
    tried on the folder G6 holds, which Windows keeps where it is."""
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    folder, saved = repo / "folder", repo / "saved"
    folder.mkdir(parents=True)
    outside.mkdir()
    emulated = not _can_symlink(tmp_path)
    for where in (folder, outside):
        if emulated:
            (where / "link").write_bytes(b"placeholder")
        else:
            os.symlink(f"{where.name}-target", where / "link")
    real_readlink, real_is_symlink, events = os.readlink, Path.is_symlink, []
    if emulated:
        monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "link" or real_is_symlink(path))

    def readlink(path, *, dir_fd=None):
        if not events:
            try:
                folder.rename(saved)
                _junction(outside, folder)
                events.append("swapped")
            except OSError:
                events.append("held where it is")
        if emulated:                               # where the read really lands, as the system would follow the path
            return f"{Path(path).parent.resolve().name}-target"
        return real_readlink(path) if dir_fd is None else real_readlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "readlink", readlink)
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [b"", b"folder/link\0"])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    change = repo_tests.Change(repo, "a" * 40, "a" * 40, "a" * 40, ("folder/link",), {}, {}, (), ())
    try:
        read = repo_tests.diff_read(change)
    finally:
        if events == ["swapped"]:
            _unlink_folder(folder)
            saved.rename(folder)
    assert events and read.files[0].new == "folder-target", (events, read.files[0])


def test_a_link_whose_folder_cannot_be_opened_is_refused_never_read_as_something_else(tmp_path, monkeypatch):
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "link" or real_is_symlink(path))
    monkeypatch.setattr(os, "readlink", lambda *args, **kwargs: pytest.fail("a link was read with no folder held"))
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [b"", b"gone/link\0"])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    change = repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, ("gone/link",), {}, {}, (), ())
    with pytest.raises(OSError):
        repo_tests.diff_read(change)


def test_a_link_whose_folder_the_system_cannot_place_is_left_unread(tmp_path, monkeypatch):
    (tmp_path / "link").write_bytes(b"placeholder")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "link" or real_is_symlink(path))
    monkeypatch.setattr(os, "readlink", lambda *args, **kwargs: pytest.fail("a link was read where G6 could not say"))

    def nowhere(fd):
        raise OSError("the system could not say where an open file is")

    monkeypatch.setattr(repo_tests, "_where", nowhere)
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [b"", b"link\0"])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    read = repo_tests.diff_read(repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, ("link",), {}, {}, (), ()))
    assert read.files[0].unread and read.files[0].new is None


# --- boundary_case: a name that is not UTF-8, wherever git gives one ---------------------------------------------------------

def _raw(rel: str) -> bytes:
    return rel.encode("utf-8", "surrogateescape")


def _named_change(where: Path, monkeypatch, base: dict, now: dict, mode: str = "100644") -> repo_tests.Change:
    """A change of ``now`` (bytes as each file stands, None where it is gone) against a base that held ``base``, with names
    as git gives them: each byte that is not UTF-8 a lone surrogate, as ``_listing`` and ``_paths`` read them. Git never
    runs, and each file is held by the name as given (``_on_disk``): git for Windows would read another file for such a
    name, which G6 refuses to open (``_on_disk`` and that refusal have their own tests)."""
    where.mkdir(parents=True, exist_ok=True)
    for rel, raw in now.items():
        if raw is not None and not rel.startswith("linked/"):
            (where / rel).write_bytes(raw)

    def oid(rel: str) -> str:
        return hashlib.sha1(_raw(rel)).hexdigest()

    listed = b"".join(b":" + mode.encode() + b" " + (b"000000" if now.get(rel) is None else mode.encode()) + b" "
                      + oid(rel).encode() + b" " + b"0" * 40 + (b" D" if now.get(rel) is None else b" M") + b"\0"
                      + _raw(rel) + b"\0" for rel in sorted(base))
    others = b"".join(_raw(rel) + b"\0" for rel in sorted(now) if rel not in base)
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed + b"\0" if listed else b"", others])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(repo_tests, "blob_bytes", lambda repo, name, **kwargs: {oid(r): b for r, b in base.items()}[name])
    monkeypatch.setattr(repo_tests, "_on_disk", lambda rel: (rel, True))
    return repo_tests.Change(where, "a" * 40, "a" * 40, "a" * 40, tuple(sorted(set(base) | set(now))), {}, {}, (), ())


@pytest.mark.parametrize("case", ["tracked", "removed", "new", "new-link", "tracked-link", "unread"])
def test_a_name_that_is_not_utf8_is_text_in_the_patch_and_the_copy_and_two_such_names_are_two_patches(
        tmp_path, monkeypatch, case):
    texts, copies = [], []
    for number, byte in enumerate(("\udcff", "\udcfe")):
        where, rel = tmp_path / f"case-{number}", f"odd-{byte}.py"
        if case == "unread":
            (tmp_path / f"outside-{number}").mkdir()
            where.mkdir()
            _junction(tmp_path / f"outside-{number}", where / "linked")
            rel = "linked/" + rel
        base = {} if case in ("new", "new-link", "unread") else {rel: b"old/target" if case == "tracked-link" else b"a = 0\n"}
        now = {rel: None if case == "removed" else b"placeholder" if "link" in case else b"a = 1\n"}
        change = _named_change(where, monkeypatch, base, now, mode="120000" if case == "tracked-link" else "100644")
        if "link" in case:
            link, real_is_symlink = where / rel, Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path, link=link: path == link or real_is_symlink(path))
            monkeypatch.setattr(os, "readlink", link_read(link, lambda: "new/target"))
        try:
            read = repo_tests.diff_read(change)
            copy = loop._shown(change, read)
        finally:
            monkeypatch.undo()
            if case == "unread":
                _unlink_folder(where / "linked")
        for text in (read.text, copy.text, copy.masked):
            text.encode("utf-8")                       # text, which a lone surrogate is not
            # the name's byte, as git's quoting names it (review-maskfu1: the quoting replaced the line naming the bytes)
            assert f"odd-\\{ord(byte) - 0xDC00:03o}.py" in text and "odd-�.py" not in text
        texts.append(read.text)
        copies.append(copy.masked)
    assert texts[0] != texts[1] and copies[0] != copies[1]


def test_a_round_on_a_name_that_is_not_utf8_keeps_its_patch_and_a_read_after_a_restart_names_the_same_patch(tmp_path):
    """The round's state, written: the patch kept byte for byte, then the review. A restart reads the patch again, and the
    same tree is the same patch, so nothing reads as changed that did not change."""
    repo = make_repo(tmp_path)
    base = _raw_name_base(repo)
    (tmp_path / "outside").mkdir()
    _junction(tmp_path / "outside", repo / "linked")
    (repo / "calc.py").write_bytes(b"value = 1\n")
    try:
        change = repo_tests.change(repo, base)
        reviews, session = [], tmp_path / "session"
        loop.review_round(change, "Review the unread file", Reviewer(), loop.harness(), session, reviews, float("inf"))
        kept = (session / "rounds" / "round-1.patch").read_bytes()
        again = repo_tests.diff_read(repo_tests.change(repo, base))       # the read a restarted step makes
    finally:
        _unlink_folder(repo / "linked")
    assert kept == again.text.encode("utf-8") and reviews[0]["patch"] == digest(again.text)
    assert '\\ G6: "linked/odd-\\377.txt" is not read' in again.text


# --- check_then_act_race: the base the reviewer is shown is the base the patch is known by -----------------------------------

def _one_file_repo(tmp_path: Path) -> tuple[Path, repo_tests.Change]:
    repo = make_repo(tmp_path)
    (repo / "calc.py").write_bytes(b"value = 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-qm", "base object fixture")
    (repo / "calc.py").write_bytes(b"value = 1\n")
    return repo, repo_tests.change(repo)


def _swap_object(repo: Path, name: str, raw: bytes) -> bytes:
    """The loose object ``name`` replaced by a blob of ``raw`` under the same name, as a caller who controls the checkout
    can; what it held."""
    obj = repo / ".git" / "objects" / name[:2] / name[2:]
    original = obj.read_bytes()
    os.chmod(obj, stat.S_IREAD | stat.S_IWRITE)
    obj.write_bytes(zlib.compress(b"blob " + str(len(raw)).encode() + b"\0" + raw))
    return original


def test_a_base_changed_during_the_read_is_named_by_the_patch_so_the_patch_reads_as_another_once_it_is_put_back(
        tmp_path, monkeypatch):
    """The other window: the object replaced after git's diff and before G6 reads the blob, within one read. The copy shows
    the bytes G6 read, the patch is known by them, and once the object is put back the patch is another: no review of it
    can end clean."""
    repo, change = _one_file_repo(tmp_path)
    real, held = repo_tests.blob_bytes, {}

    def swapped_first(root, name, **kwargs):
        held[name] = _swap_object(repo, name, b"value = 8\n")
        return real(root, name, **kwargs)

    monkeypatch.setattr(repo_tests, "blob_bytes", swapped_first)
    read = repo_tests.diff_read(change)
    monkeypatch.undo()
    name, original = next(iter(held.items()))
    copy = loop._shown(change, read)
    assert "-value = 0\n" in read.text and "-value = 8\n" in copy.masked        # git's diff, then G6's read of the blob
    assert hashlib.sha256(b"value = 8\n").hexdigest() in read.text              # which the patch is known by
    (repo / ".git" / "objects" / name[:2] / name[2:]).write_bytes(original)
    assert repo_tests.diff_read(change).text != read.text


def test_a_base_git_will_not_give_is_named_by_the_patch_and_refuses_the_copy(tmp_path, monkeypatch):
    repo, change = _one_file_repo(tmp_path)

    def refused(*args, **kwargs):
        raise repo_tests.RepoRefused("G6_E_GIT_COMMAND_FAILED", "git cat-file failed: made-up refusal")

    monkeypatch.setattr(repo_tests, "blob_bytes", refused)
    read = repo_tests.diff_read(change)
    assert "\\ G6: the bytes of calc.py at the base could not be read\n" in read.text
    with pytest.raises(repo_tests.RepoRefused) as caught:
        loop._shown(change, read)
    assert caught.value.code == "G6_E_GIT_COMMAND_FAILED"


def test_two_bases_the_patch_tells_apart_by_their_line_endings_are_shown_apart(tmp_path):
    """one_fact_two_rules, found walking the class list over ``_shown``: whether the text shown of a file is its bytes is one
    fact, decided for the file as it stands by ``_bytes_line`` over the text with ``_normal``'s line endings, and was decided
    for the base by another rule (its bytes against the text before the endings were made newlines), which a carriage
    return the patch holds passed. The base has the one rule now."""
    reads, copies = [], []
    for number, before in enumerate((b"a = 0\r\nb = 0\n", b"a = 0\nb = 0\n")):
        repo = make_repo(tmp_path / f"case-{number}")
        (repo / "calc.py").write_bytes(before)
        git(repo, "add", "calc.py")
        git(repo, "commit", "-qm", "base")
        (repo / "calc.py").write_bytes(b"a = 0\nb = 1\n")
        change = repo_tests.change(repo)
        read = repo_tests.diff_read(change)
        reads.append(read.text)
        copies.append(loop._shown(change, read).masked)
    assert reads[0] != reads[1]
    assert copies[0] != copies[1], "the copy shows alike two bases the patch tells apart"
    named = hashlib.sha256(b"a = 0\r\nb = 0\n").hexdigest()
    assert f"\\ G6: calc.py at the base shows here only in part; its bytes have SHA-256 {named}\n" in copies[0]


def test_the_patch_names_the_base_the_copy_shows_and_a_base_unchanged_is_the_same_patch(tmp_path):
    repo, change = _one_file_repo(tmp_path)
    read = repo_tests.diff_read(change)
    one, = read.files
    assert one.old == b"value = 0\n" and hashlib.sha256(one.old).hexdigest() in read.text
    assert "-value = 0\n" in loop._shown(change, read).masked and repo_tests.diff_read(change).text == read.text
