# SPDX-License-Identifier: Apache-2.0
"""review-maskfu2's four findings on claude/masking-followup, each as its reviewer wrote it, repair 1's four open items, and
the root they share: a name or a path handled by more than one rule.

Each crossing a name git gives makes now has one rule. Into the identity's text: the name from git's listing, through
``repo_tests.shown_name``, the rule the copy uses, with git's own header checked byte for byte against the listed name
(``_named_patch``), and every listed file's section there or the read refused. Into the scrubber's look: the name as git gave
it and as quoted, before anything changes a line ending (``completion_loop._found``). Onto the disk: the name as git's
producer encodes it there (``repo_tests._on_disk``), and a file git lists as there that G6 cannot open by it refused, never
shown as gone. Into a containment decision: the system's own final paths of what G6 holds, compared part by part with no case
folded (``repo_tests._within``). Out of git's diff: every tracked file git's own ``ls-files --deleted`` names as unseen is in
its listing, or the read refuses. Back out of the identity: one reader, the exact inverse of the quoting rule
(``repo_tests.unquoted_name``), with lines cut at newlines alone. Git runs only on scratch repositories, reviewers are
recorded, and no test of a reviewer's ever runs."""
import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from g6.context.redact import MASK, scrub, secret_spans
from g6.harness import completion_loop as loop
from g6.harness import patch_review
from g6.workforce import repo_tests as rt
from tests.harness.test_masking_followup import _callee, _functions, _own_calls
from tests.harness.test_shown_names import EDGES, LONGEST, _rel

_GIT_ENV = {**{key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMP", "TEMP") if key in os.environ},
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}


def git_bytes(repo, *args, data=None):
    """git in a scratch repository, its output as bytes (the review's own fixture, as a function)."""
    return subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', *args], input=data, capture_output=True,
                          check=True, env=_GIT_ENV).stdout


class Reviewer:
    model = 'recorded-reviewer'

    def __init__(self):
        self.sent = []

    def complete(self, system, user):
        self.sent.append(user)
        token = re.search(r'review [0-9a-f]{32}', user)[0]
        return f'READ: {token}\nRULE: none\nVERDICT: none\nWHY: none\n'


def _base(repo: Path, entries: dict[bytes, bytes], mode: bytes = b'100644') -> str:
    """A commit of a tree holding ``entries`` (name: bytes), made with git's own plumbing, as no working file here can carry
    some of these names. Its id."""
    git_bytes(repo, 'init', '-q')
    git_bytes(repo, 'config', 'core.protectNTFS', 'false')
    records = b''
    for name, raw in sorted(entries.items()):
        blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=raw).strip()
        records += mode + b' blob ' + blob + b'\t' + name + b'\0'
    tree = git_bytes(repo, 'mktree', '-z', data=records).strip()
    return git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'fixture').decode().strip()


# --- review-maskfu2's four findings, each as its reviewer wrote it --------------------------------------------------------------

def test_finding_1_git_header_and_copy_use_the_same_rule_for_a_non_utf8_name(tmp_path):
    repo = tmp_path
    git_bytes(repo, 'init', '-q')
    raw_name = b'odd-\xff.py'
    rel = raw_name.decode('utf-8', 'surrogateescape')
    blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'value = 0\n').strip()
    tree = git_bytes(repo, 'mktree', '-z', data=b'100644 blob ' + blob + b'\t' + raw_name + b'\0').strip()
    base = git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'non-UTF8 name fixture').decode().strip()
    change = rt.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = rt.diff_read(change)
    assert [one.rel for one in read.files] == [rel]
    shown = loop._shown(change, read)
    expected = '--- ' + rt.shown_name(rel, 'a/')
    copy_header = next(line for line in shown.text.split('\n') if line.startswith('--- '))
    identity_header = next(line for line in read.text.split('\n') if line.startswith('--- '))
    assert copy_header == expected
    assert identity_header == expected, 'the identity header bypasses shown_name and replaces the name byte with U+FFFD'


def test_finding_2_a_recognised_cr_only_value_in_a_git_name_is_masked_and_counts_as_hidden(tmp_path):
    repo = tmp_path
    git_bytes(repo, 'init', '-q')
    git_bytes(repo, 'config', 'core.protectNTFS', 'false')
    # Git can hold this name even though Windows cannot create its working file.
    rel = 'key="\r"'
    assert secret_spans(rel) and scrub(rel) == 'key="' + MASK + '"'
    blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'value = 0\n').strip()
    tree = git_bytes(repo, 'mktree', '-z', data=b'100644 blob ' + blob + b'\t' + rel.encode() + b'\0').strip()
    base = git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'recognised name fixture').decode().strip()
    change = rt.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = rt.diff_read(change)
    assert [one.rel for one in read.files] == [rel]
    reviewer = Reviewer()
    found = loop.run_critic(change, 'Review the deletion', reviewer, 'Review', repo / 'critic', patch=read, whole=True)
    assert reviewer.sent
    assert found['verdict'] == 'failed' and found['error'] == loop.HIDDEN, 'a recognised secret in a name permits a clean review'
    assert rt.shown_name(rel, 'a/') not in reviewer.sent[0]


@pytest.mark.skipif(os.name != 'nt', reason='Windows UTF-16 filesystem names')
def test_finding_3_a_file_git_reads_cannot_be_shown_as_deleted_because_its_name_decodes_differently(tmp_path):
    repo = tmp_path
    git_bytes(repo, 'init', '-q')
    git_bytes(repo, 'config', 'core.protectNTFS', 'false')
    rel = 'odd-\udcff.py'
    # NTFS holds this lone surrogate; Git writes it as ED B3 BF in its listing.
    exact = Path('\\\\?\\' + str(repo / rel))
    exact.write_bytes(b'actual_working_bytes = 2\n')
    (repo / 'normal.py').write_bytes(b'normal = 2\n')
    blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'base = 1\n').strip()
    entries = (b'100644 blob ' + blob + b'\tnormal.py\0'
               + b'100644 blob ' + blob + b'\t' + rel.encode('utf-8', 'surrogatepass') + b'\0')
    tree = git_bytes(repo, 'mktree', '-z', data=entries).strip()
    base = git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'Windows name fixture').decode().strip()
    git_bytes(repo, 'update-ref', 'HEAD', base)  # Only this test's scratch repository.
    git_bytes(repo, 'read-tree', base)
    reviewer = Reviewer()
    try:
        changed = rt.change(repo, base)
        assert 'normal.py' in changed.sources
        read = rt.diff_read(changed)
        assert '+actual_working_bytes = 2\n' in read.text, 'git must have read the actual file for this probe'
        assert rt.diff_read(rt.change(repo, base)).text == read.text
        found = loop.run_critic(changed, 'Review both edits', reviewer, 'Review', repo / 'critic', patch=read, whole=True)
    except rt.RepoRefused as exc:
        assert exc.code == 'G6_E_GIT_COMMAND_FAILED'
        return
    assert exact.read_bytes() == b'actual_working_bytes = 2\n'
    assert found['verdict'] != 'none' or '+actual_working_bytes = 2\n' in reviewer.sent[0], (
        'a stable identity contains the actual edit, but the clean reviewer copy omits it and shows the file as deleted')


def _case_sensitive(folder: Path, scratch: Path) -> None:
    """``folder``, a folder of this test's own scratch space, made case sensitive (NTFS), or the test skipped where the system
    cannot."""
    import ctypes
    from ctypes import wintypes

    assert folder.resolve().is_relative_to(scratch.resolve())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    setinfo = kernel.SetFileInformationByHandle
    setinfo.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    setinfo.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(folder), 0x100, 7, None, 3, 0x02000000, None)  # FILE_WRITE_ATTRIBUTES, backup semantics
    if handle in (None, ctypes.c_void_p(-1).value):
        pytest.skip(f'cannot open scratch folder for case sensitivity: {ctypes.get_last_error()}')
    try:
        flags = wintypes.DWORD(1)  # FILE_CS_FLAG_CASE_SENSITIVE_DIR
        if not setinfo(handle, 23, ctypes.byref(flags), ctypes.sizeof(flags)):  # FileCaseSensitiveInfo
            pytest.skip(f'case sensitive NTFS folders unavailable: {ctypes.get_last_error()}')
    finally:
        close(handle)


def _emulate_final_link(monkeypatch, marker: bytes) -> None:
    """The final file ``payload.py`` as a symlink naming ``marker``: this account cannot make final-file symlinks, so only
    that entry is emulated; folders, junctions and what G6 holds are real."""
    real_link, real_readlink = Path.is_symlink, os.readlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path.name == 'payload.py' or real_link(path))

    def readlink(path, *, dir_fd=None):
        if Path(path).name == 'payload.py':
            return marker.decode()
        return real_readlink(path) if dir_fd is None else real_readlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, 'readlink', readlink)


@pytest.mark.skipif(os.name != 'nt', reason='Windows case sensitive NTFS folder')
@pytest.mark.parametrize('kind', ['regular', 'link'])
def test_finding_4_case_sensitive_sibling_is_not_inside_the_repository(tmp_path, tmp_path_factory, monkeypatch, kind):
    import _winapi

    _case_sensitive(tmp_path, tmp_path_factory.getbasetemp())
    repo, outside = tmp_path / 'repo', tmp_path / 'Repo'
    repo.mkdir()
    outside.mkdir()
    assert not os.path.samefile(repo, outside)
    marker = b'outside_repository_fixture = 12345\n' if kind == 'regular' else b'outside_repository_fixture_target'
    (outside / 'payload.py').write_bytes(marker if kind == 'regular' else b'placeholder')
    _winapi.CreateJunction(str(outside), str(repo / 'linked'))
    try:
        if kind == 'link':
            _emulate_final_link(monkeypatch, marker)
        git_bytes(repo, 'init', '-q')
        blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'normal = 0\n').strip()
        tree = git_bytes(repo, 'mktree', '-z', data=b'100644 blob ' + blob + b'\tnormal.py\0').strip()
        base = git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'containment fixture').decode().strip()
        git_bytes(repo, 'update-ref', 'HEAD', base)  # Only this test's scratch repository.
        git_bytes(repo, 'read-tree', base)
        (repo / 'normal.py').write_bytes(b'normal = 1\n')
        changed = rt.change(repo, base)
        assert 'linked/payload.py' in changed.files
        read = rt.diff_read(changed)
        one = next(one for one in read.files if one.rel == 'linked/payload.py')
        reviewer = Reviewer()
        loop.run_critic(changed, 'Review this repository', reviewer, 'Review', repo / 'critic', patch=read, whole=True)
        assert (one.unread, one.new, marker.decode() in reviewer.sent[0]) == (True, None, False), (
            'the case-insensitive containment comparison reads a distinct sibling through a junction and sends its bytes')
    finally:
        os.rmdir(repo / 'linked')


# --- finding 1, at its root: every name in the identity's text is the listing's, through the one rule -----------------------------

#: Names a tree can hold (no slash) from repair 1's edges, with a space, a trailing space, a C1 control and a line separator.
_NAMES = {label: raw for label, raw in EDGES.items() if b'/' not in raw}
_NAMES.update({'space-inside': b'sp ace.py', 'space-last': b'trail.py ', 'line-separator': 'ls .py'.encode(),
               'not-utf8-ff': b'odd-\xff.py', 'not-utf8-fe': b'odd-\xfe.py'})


def _headers(text: str) -> list[str]:
    return [line for line in text.split('\n') if line.startswith(('diff --git ', '--- ', '+++ '))]


def test_every_name_git_writes_a_header_for_is_named_in_the_identity_as_the_copy_names_it(tmp_path):
    """Against git itself, every name at once (each removed), with a tracked file of a name with a space changed: each
    header of the identity names its file as ``shown_name`` does, which is how the copy names it, and no header holds git's
    own rendering (a raw byte that is not UTF-8, a U+FFFD, a line separator, git's tab after a name with a space)."""
    entries = {raw: b'value = 0\n' for raw in _NAMES.values()}
    entries[b'sp ace changed.py'] = b'value = 0\n'
    repo = tmp_path / 'repo'
    repo.mkdir()
    base = _base(repo, entries)
    (repo / 'sp ace changed.py').write_bytes(b'value = 1\n')
    rels = tuple(_rel(raw) for raw in entries)
    change = rt.Change(repo, base, base, base, rels, {}, {}, (), ())
    read = rt.diff_read(change)
    assert sorted(one.rel for one in read.files) == sorted(rels)
    copy = loop._shown(change, read)
    expected = sorted(f'diff --git {rt.shown_name(one.rel, "a/")} {rt.shown_name(one.rel, "b/")}' for one in read.files)
    got = _headers(read.text)
    assert sorted(line for line in got if line.startswith('diff --git ')) == expected
    copy_lines = {line for line in _headers(copy.text)}
    assert {line for line in got if line.startswith(('--- ', '+++ '))} <= copy_lines | {'+++ /dev/null', '--- /dev/null'}
    for line in got:
        line.encode('utf-8')
        assert '�' not in line and ' ' not in line and not line.endswith('\t'), repr(line)
    assert rt.diff_read(change).text == read.text           # the same tree is the same identity, read again


def test_two_names_that_differ_only_in_a_byte_that_is_not_utf8_are_two_identities_and_two_headers(tmp_path):
    texts = []
    for number, byte in enumerate((b'\xff', b'\xfe')):
        repo = tmp_path / str(number)
        repo.mkdir()
        base = _base(repo, {b'odd-' + byte + b'.py': b'value = 0\n'})
        rel = _rel(b'odd-' + byte + b'.py')
        read = rt.diff_read(rt.Change(repo, base, base, base, (rel,), {}, {}, (), ()))
        assert _headers(read.text) == [f'diff --git {rt.shown_name(rel, "a/")} {rt.shown_name(rel, "b/")}',
                                       f'--- {rt.shown_name(rel, "a/")}', '+++ /dev/null']
        texts.append(read.text)
    assert texts[0] != texts[1]


@pytest.mark.parametrize('setting', [('diff.noprefix', 'true'), ('diff.mnemonicPrefix', 'true')])
def test_a_repository_that_renames_gits_prefixes_is_read_by_the_same_rule(tmp_path, setting):
    """The repository's own config (the agent's) cannot change how the identity names a file: git is told its prefixes."""
    texts = []
    for number, config in enumerate((None, setting)):
        repo = tmp_path / str(number)
        repo.mkdir()
        base = _base(repo, {b'calc.py': b'value = 0\n'})
        git_bytes(repo, 'update-ref', 'HEAD', base)
        git_bytes(repo, 'read-tree', base)
        (repo / 'calc.py').write_bytes(b'value = 1\n')
        if config:
            git_bytes(repo, 'config', *config)
        texts.append(rt.diff_read(rt.Change(repo, base, base, base, ('calc.py',), {}, {}, (), ())).text)
    assert texts[0] == texts[1] and '--- a/calc.py\n+++ b/calc.py\n' in texts[0]


def _listed(monkeypatch, where: Path, records: bytes, patch: bytes) -> rt.Change:
    """A read of a listing and a patch as given, git never run (a patch git would not write, for malformed state)."""
    monkeypatch.setattr(rt, '_private', lambda *args: [records + b'\0' + patch, b''])
    monkeypatch.setattr(rt, '_tracked_new', lambda *args: set())
    monkeypatch.setattr(rt, 'blob_bytes', lambda *args, **kwargs: b'value = 0\n')
    return rt.Change(where, 'a' * 40, 'a' * 40, 'a' * 40, (), {}, {}, (), ())


def _record(name: bytes, status: bytes = b'D') -> bytes:
    return b':100644 000000 ' + b'b' * 40 + b' ' + b'0' * 40 + b' ' + status + b'\0' + name + b'\0'


_GONE = b'diff --git a/{n} b/{n}\ndeleted file mode 100644\nindex bbbbbbb..0000000\n--- a/{n}\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 0\n'


def _gone(name: bytes) -> bytes:
    return _GONE.replace(b'{n}', name)


@pytest.mark.parametrize('case', ['another-name', 'section-not-listed', 'out-of-order', 'twice', 'hunk-cut-short',
                                  'header-not-gits', 'line-git-never-writes'])
def test_a_rendering_git_does_not_write_for_a_listed_file_is_left_as_read_and_the_copy_is_still_the_files(
        tmp_path, monkeypatch, case):
    """Only a header git writes for a file its listing names is renamed by the one rule; any other line is left as read
    (decoded, as before, the bytes line covering what that leaves out), and the copy, made from the files, is the copy git's
    own rendering gives (the class of review-kfu4's ``test_whatever_git_renders_the_copy_is_the_same``)."""
    odd = b'odd-\xff.py'
    records, patch = _record(b'a.py') + _record(odd), _gone(b'a.py') + _gone(odd)
    own = _gone(b'a.py') + _gone(odd)
    if case == 'another-name':
        patch = _gone(b'b.py') + _gone(odd)
    elif case == 'out-of-order':
        patch = _gone(odd) + _gone(b'a.py')
    elif case == 'twice':
        patch += _gone(b'a.py')
    elif case == 'section-not-listed':
        patch += _gone(b'b.py')
    elif case == 'hunk-cut-short':
        patch = patch.replace(b'@@ -1 +0,0 @@', b'@@ -1,2 +0,0 @@', 1)
    elif case == 'header-not-gits':
        patch = patch.replace(b'--- a/' + odd, b'--- "a/' + odd + b'"')
    else:
        patch = patch.replace(b'-value = 0\n', b'-value = 0\n?what\n', 1)
    expected = copies = None
    for rendering in (own, patch):
        change = _listed(monkeypatch, tmp_path, records, rendering)
        read = rt.diff_read(change)
        copy = loop._shown(change, read)
        if rendering is own:
            expected, copies = read.text, copy
            assert f'diff --git {rt.shown_name(_rel(odd), "a/")} {rt.shown_name(_rel(odd), "b/")}\n' in read.text
            continue
        assert copy == copies
        unnamed = rendering.replace(b'diff --git a/' + odd + b' b/' + odd, b'').replace(b'--- a/' + odd + b'\n', b'')
        assert all(line in read.text for line in unnamed.decode('utf-8', 'replace').split('\n'))
        assert read.text != expected or case == 'out-of-order'


def test_a_listed_file_with_no_section_is_still_named_by_its_bytes(tmp_path, monkeypatch):
    """git writes no listed file without its section (one queue gives both); a listing handed in alone, as a fake gives it,
    is not refused, and the identity still names the file's bytes at the base (G6's own line), as the copy shows them."""
    change = _listed(monkeypatch, tmp_path, _record(b'a.py') + _record(b'b.py'), _gone(b'a.py'))
    read = rt.diff_read(change)
    assert '\\ G6: the bytes of b.py at the base have SHA-256 ' in read.text and 'diff --git a/b.py' not in read.text
    assert '--- a/b.py\n+++ /dev/null\n' in loop._shown(change, read).masked


def test_the_patch_as_git_writes_it_with_no_newline_a_typechange_and_a_mode_is_read_whole(tmp_path, monkeypatch):
    """Each shape git writes for a file, one section each but a typechange, which git writes as two."""
    patch = (_gone(b'a.py').replace(b'-value = 0\n', b'-value = 0\n\\ No newline at end of file\n')
             + b'diff --git a/m.py b/m.py\nold mode 100644\nnew mode 100755\n'
             + b'diff --git a/t.py b/t.py\ndeleted file mode 120000\nindex bbbbbbb..0000000\n--- a/t.py\n+++ /dev/null\n'
             b'@@ -1 +0,0 @@\n-target.py\n\\ No newline at end of file\ndiff --git a/t.py b/t.py\nnew file mode 100644\n'
             b'index 0000000..1add1fa\n--- /dev/null\n+++ b/t.py\n@@ -0,0 +1 @@\n+x = 1\n'
             + b'diff --git a/e.py b/e.py\ndeleted file mode 100644\nindex e69de29..0000000\n')
    records = (_record(b'a.py') + b':100644 100755 ' + b'b' * 40 + b' ' + b'0' * 40 + b' M\0m.py\0'
               + b':120000 100644 ' + b'b' * 40 + b' ' + b'0' * 40 + b' T\0t.py\0' + _record(b'e.py'))
    change = _listed(monkeypatch, tmp_path, records, patch)
    monkeypatch.setattr(rt, '_read_inside', lambda root, path: b'x = 1\n')
    monkeypatch.setattr(Path, 'is_file', lambda path: path.name in ('m.py', 't.py'))
    monkeypatch.setattr(Path, 'is_symlink', lambda path: False)
    read = rt.diff_read(change)
    assert read.text.startswith(patch.decode())


# --- finding 2, at its root: a name is checked for secrets as git gave it and as quoted, before anything normalises it -----------

@pytest.mark.parametrize('value', ['\r', '\n', '\r\n', '\t', ''], ids=['cr', 'lf', 'crlf', 'tab', 'empty'])
def test_a_value_in_a_name_is_found_as_the_name_stands_and_a_found_one_hides_the_name(tmp_path, value):
    rel = f'key="{value}"'
    repo = tmp_path / 'repo'
    repo.mkdir()
    base = _base(repo, {rel.encode(): b'value = 0\n'})
    change = rt.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = rt.diff_read(change)
    reviewer = Reviewer()
    found = loop.run_critic(change, 'Review the deletion', reviewer, 'Review', repo / 'critic', patch=read, whole=True)
    named = bool(secret_spans(rel)) or any(secret_spans(rt.shown_name(rel, prefix)) for prefix in ('', 'a/', 'b/'))
    assert named == (value in ('\r', '\t'))               # the scrubber's own word on the name, as it stands and as quoted
    if named:
        assert (found['verdict'], found['error']) == ('failed', loop.HIDDEN)
        assert rt.shown_name(rel, 'a/') not in reviewer.sent[0] and f'--- {MASK}\n' in reviewer.sent[0]
    else:
        assert found['verdict'] == 'none' and f'--- {rt.shown_name(rel, "a/")}\n' in reviewer.sent[0]


def test_the_name_check_reads_the_name_before_any_line_ending_rule():
    """From the code: whether a name holds a value is ``secret_spans`` on the text itself, never ``_seen``'s, whose line
    endings are made newlines first; and ``_shown`` asks it of the name as git gave it and of each form it is shown in."""
    functions = _functions(loop)
    callees = {_callee(call) for call in ast.walk(functions['_found']) if isinstance(call, ast.Call)}
    assert 'secret_spans' in callees and not callees & {'_seen', '_normal', 'replace', 'splitlines'}, callees
    chosen = next(node for node in ast.walk(functions['_shown']) if isinstance(node, ast.Assign)
                  and ast.unparse(node.targets[0]) == 'sent')
    assert '_found(one.rel)' in ast.unparse(chosen) and 'any((_found(name) for name in shown))' in ast.unparse(chosen)


# --- finding 3, at its root: a path git names is opened as the producer encodes it, or the read refuses ----------------------------

def test_the_name_on_disk_is_the_bytes_git_gives_as_the_system_takes_them():
    assert rt._on_disk('plain/name.py') == ('plain/name.py', True)
    assert rt._on_disk('café.py') == ('café.py', True)
    wtf8 = _rel('odd-\udcff.py'.encode('utf-8', 'surrogatepass'))            # git for Windows' bytes for a lone surrogate
    not_utf8 = _rel(b'odd-\xff.py')
    if os.name == 'nt':
        assert rt._on_disk(wtf8) == ('odd-\udcff.py', True)
        assert rt._on_disk(not_utf8) == (not_utf8, False)           # git for Windows reads another file for it
    else:
        assert rt._on_disk(wtf8) == (os.fsdecode(wtf8.encode('utf-8', 'surrogateescape')), True)
        assert rt._on_disk(not_utf8) == (os.fsdecode(b'odd-\xff.py'), True)


def _surrogate_repo(tmp_path: Path, *, excluded: bool) -> tuple[Path, str, bytes]:
    """A repository whose base tracks a file named with a lone surrogate, changed in the working tree. git for Windows
    lists the working file a second time, as untracked, under a name with U+FFFD in its place (its own lossy listing of a
    folder); ``excluded`` keeps that second name out by the repository's own exclude file."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    name = 'odd-\udcff.py'
    raw = name.encode('utf-8', 'surrogatepass')
    base = _base(repo, {raw: b'base = 1\n'})
    git_bytes(repo, 'update-ref', 'HEAD', base)
    git_bytes(repo, 'read-tree', base)
    (repo / name).write_bytes(b'now = 2\n')
    if excluded:
        (repo / '.git' / 'info').mkdir(exist_ok=True)
        (repo / '.git' / 'info' / 'exclude').write_text('odd-*\n', encoding='utf-8')
    return repo, base, raw


@pytest.mark.skipif(os.name != 'nt', reason='git for Windows names a file by its UTF-16 name')
def test_git_for_windows_lists_a_lone_surrogate_name_by_the_bytes_the_rule_reads_back(tmp_path):
    """What the producer writes: git's listing of a tracked file whose NTFS name holds a lone surrogate is that name's
    UTF-8 with the surrogate passed, the bytes ``_on_disk`` reads back to the file git read; and the copy shows what git
    read, under the name the identity gives it."""
    repo, base, raw = _surrogate_repo(tmp_path, excluded=True)
    assert git_bytes(repo, 'ls-files', '-z').rstrip(b'\0') == raw
    change = rt.change(repo, base)
    read = rt.diff_read(change)
    one, = read.files
    assert one.rel == _rel(raw) and rt._on_disk(one.rel) == ('odd-\udcff.py', True) and one.new == 'now = 2\n'
    copy = loop._shown(change, read)
    assert '+now = 2\n' in copy.masked and f'+++ {rt.shown_name(one.rel, "b/")}\n' in copy.masked
    assert rt.shown_name(one.rel) == '"odd-\\355\\263\\277.py"'


@pytest.mark.skipif(os.name != 'nt', reason="git for Windows' lossy listing of a folder")
def test_an_untracked_name_git_lists_with_nothing_there_refuses_the_read(tmp_path):
    """git lists the working file again as untracked, named with U+FFFD, a name nothing has: G6 cannot read what git means
    by it, so the read refuses rather than show nothing for it. A folder git lists as untracked still shows nothing."""
    repo, base, _ = _surrogate_repo(tmp_path, excluded=False)
    assert b'odd-\xef\xbf\xbd.py' in git_bytes(repo, 'ls-files', '--others', '-z')
    _refused_by_the_copy(rt.change(repo, base), f'odd-�.py {rt.NOT_THERE}')


def _refused_by_the_copy(change: rt.Change, named: str) -> None:
    """The read names the file as git lists it and G6 could not open it, and the copy, so the review, refuses: no reviewer
    is asked, and the file is never shown as gone."""
    read = rt.diff_read(change)
    assert f'\\ G6: {named}\n' in read.text
    with pytest.raises(rt.RepoRefused) as refused:
        loop._shown(change, read)
    assert refused.value.code == 'G6_E_GIT_COMMAND_FAILED' and str(refused.value) == named
    reviewer = Reviewer()
    with pytest.raises(rt.RepoRefused):
        loop.run_critic(change, 'Review the change', reviewer, 'Review', change.repo / 'critic', patch=read, whole=True)
    assert not reviewer.sent


@pytest.mark.skipif(os.name != 'nt', reason='git for Windows reads a name that is not UTF-8 as Latin-1')
def test_a_changed_file_git_names_by_bytes_the_system_cannot_be_given_refuses_the_read(tmp_path):
    """Git for Windows reads the tracked name ``odd-\\xff.py`` as ``odd-ÿ.py`` (its own fallback for bytes that are not
    UTF-8), which G6 does not copy: G6 cannot open what git read, so the read refuses rather than show it gone."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    base = _base(repo, {b'odd-\xff.py': b'base = 1\n'})
    git_bytes(repo, 'update-ref', 'HEAD', base)
    git_bytes(repo, 'read-tree', base)
    (repo / 'odd-ÿ.py').write_bytes(b'base = 2\n')
    assert b'+base = 2' in git_bytes(repo, '-c', 'core.quotepath=off', 'diff')        # git read a file for it
    rel = _rel(b'odd-\xff.py')
    _refused_by_the_copy(rt.Change(repo, base, base, base, (rel,), {}, {}, (), ()), f'{rt.shown_name(rel)} {rt.CANNOT_OPEN}')


def test_a_file_git_lists_as_changed_that_g6_cannot_find_refuses_never_reads_as_gone(tmp_path, monkeypatch):
    records = b':100644 100644 ' + b'b' * 40 + b' ' + b'0' * 40 + b' M\0mod.py\0'
    patch = b'diff --git a/mod.py b/mod.py\nindex bbbbbbb..ccccccc 100644\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n'
    _refused_by_the_copy(_listed(monkeypatch, tmp_path, records, patch), f'mod.py {rt.NOT_THERE}')


def test_a_file_git_lists_as_removed_is_never_opened(tmp_path, monkeypatch):
    (tmp_path / 'a.py').write_bytes(b'something git did not read = 1\n')
    change = _listed(monkeypatch, tmp_path, _record(b'a.py'), _gone(b'a.py'))
    monkeypatch.setattr(rt, '_read_inside', lambda *args: pytest.fail('a file git lists as removed was opened'))
    read = rt.diff_read(change)
    assert read.files[0].new is None and 'something' not in loop._shown(change, read).masked


# --- finding 4, at its root: containment by what a folder is ---------------------------------------------------------------------

@pytest.mark.skipif(os.name != 'nt', reason='Windows folders, case sensitive and not')
@pytest.mark.parametrize('case', ['sensitive-sibling', 'sensitive-itself', 'insensitive-other-spelling',
                                  'insensitive-outside'])
@pytest.mark.parametrize('kind', ['regular', 'link'])
def test_containment_is_decided_by_the_folder_whatever_its_spelling(tmp_path, tmp_path_factory, monkeypatch, case, kind):
    import _winapi

    if case.startswith('sensitive'):
        _case_sensitive(tmp_path, tmp_path_factory.getbasetemp())
    repo = tmp_path / 'repo'
    repo.mkdir()
    target = {'sensitive-sibling': tmp_path / 'Repo', 'sensitive-itself': repo,
              'insensitive-other-spelling': tmp_path / 'REPO', 'insensitive-outside': tmp_path / 'other'}[case]
    target.mkdir(exist_ok=True)
    inside = case in ('sensitive-itself', 'insensitive-other-spelling')
    marker = b'contained_fixture = 1\n' if kind == 'regular' else b'contained_fixture_target'
    (target / 'payload.py').write_bytes(marker if kind == 'regular' else b'placeholder')
    _winapi.CreateJunction(str(target), str(repo / 'linked'))
    try:
        if kind == 'link':
            _emulate_final_link(monkeypatch, marker)
        monkeypatch.setattr(rt, '_private', lambda *args: [b'', b'linked/payload.py\0'])
        monkeypatch.setattr(rt, '_tracked_new', lambda *args: set())
        change = rt.Change(repo, 'a' * 40, 'a' * 40, 'a' * 40, ('linked/payload.py',), {}, {}, (), ())
        one, = rt.diff_read(change).files
        assert (one.unread, one.new) == ((False, marker.decode()) if inside else (True, None)), (case, one)
        root = repo.resolve()
        assert rt._within(root, root / 'x') and not rt._within(root, root.parent / root.name.upper() / 'x')
    finally:
        os.rmdir(repo / 'linked')


def test_every_containment_decision_in_the_read_is_the_one_rule():
    """From the code: each place the read decides whether what it looks at or holds lies in the repository is ``_within``,
    never a path comparison of its own (``is_relative_to``, ``relative_to``, which fold case on Windows)."""
    functions = _functions(rt)
    for name in ('_read_inside', '_link_inside', '_leads_out'):
        callees = [_callee(call) for _, call in _own_calls(functions[name])]
        assert '_within' in callees and not {'is_relative_to', 'relative_to', 'startswith', 'commonpath'} & set(callees), (
            name, callees)
    within = {_callee(call) for call in ast.walk(functions['_within']) if isinstance(call, ast.Call)}
    assert not within & {'is_relative_to', 'relative_to', 'lower', 'casefold', 'normcase'}, within


# --- open item A: a file git leaves out of its diff is a refusal --------------------------------------------------------------------

def _long(repo: Path, total: int) -> str:
    """A path in ``repo`` whose full path is ``total`` characters long, in one folder."""
    room = total - len(str(repo)) - 1
    folder = 'd' * min(60, room // 2)
    return folder + '/' + 'f' * (room - len(folder) - 1)


@pytest.mark.skipif(os.name != 'nt', reason="git for Windows' path length limit (MAX_PATH) without core.longpaths")
@pytest.mark.parametrize('longpaths', ['false', 'true'])
@pytest.mark.parametrize('total', [259, 260, 300], ids=['at-the-limit', 'one-past', 'far-past'])
def test_a_tracked_file_git_cannot_stat_refuses_the_read_never_vanishes(tmp_path, monkeypatch, total, longpaths):
    """Past the limit, git for Windows without ``core.longpaths`` cannot stat a tracked file: its diff leaves it out, says so
    on its error output alone and ends well, and the read refuses. With ``core.longpaths`` git reads it, and the identity
    holds it (or, where this system cannot open so long a path, the read refuses). It never vanishes. git takes the
    setting from the user's global config, not the repository's, so G6's git is given a scratch home that sets it or not."""
    home = tmp_path / 'home'
    home.mkdir()
    if longpaths == 'true':
        (home / '.gitconfig').write_text('[core]\n\tlongpaths = true\n', encoding='utf-8')
    for name in ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME'):
        monkeypatch.setenv(name, str(home))
    repo = tmp_path / 'r'
    repo.mkdir()
    rel = _long(repo, total)
    base = _base(repo, {b'calc.py': b'value = 0\n'})
    blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'deep = 0\n').strip()
    folder, name = rel.split('/')
    sub = git_bytes(repo, 'mktree', '-z', data=b'100644 blob ' + blob + b'\t' + name.encode() + b'\0').strip()
    tree = git_bytes(repo, 'mktree', '-z', data=git_bytes(repo, 'ls-tree', '-z', base) + b'040000 tree ' + sub + b'\t'
                     + folder.encode() + b'\0').strip()
    base = git_bytes(repo, 'commit-tree', tree.decode(), '-m', 'long path').decode().strip()
    full = '\\\\?\\' + str(repo / folder)
    os.mkdir(full)
    try:
        with open(full + '\\' + name, 'wb') as handle:
            handle.write(b'deep = 1\n')
        (repo / 'calc.py').write_bytes(b'value = 1\n')
        change = rt.Change(repo, base, base, base, (rel, 'calc.py'), {}, {}, (), ())
        if total < 260 or longpaths == 'true':
            try:
                read = rt.diff_read(change)
            except rt.RepoRefused as refused:
                assert total >= 260 and refused.value.code == 'G6_E_GIT_COMMAND_FAILED'
                return
            assert {one.rel for one in read.files} == {rel, 'calc.py'} and '+deep = 1\n' in read.text
        else:
            with pytest.raises(rt.RepoRefused) as refused:
                rt.diff_read(change)
            assert refused.value.code == 'G6_E_GIT_COMMAND_FAILED' and rt.shown_name(rel)[:40] in str(refused.value)
    finally:
        for leftover in (full + '\\' + name,):
            if os.path.lexists(leftover):
                os.unlink(leftover)
        os.rmdir(full)


@pytest.mark.parametrize('case', ['listed', 'skipped', 'gitlink', 'unknown-tag', 'malformed'])
def test_every_file_gits_own_listing_cannot_see_must_be_in_its_diff(case):
    """``ls-files --deleted`` names each tracked file git's lstat fails on (gone, or a path git cannot use); the diff names
    the gone ones and skips the others. One it skips refuses; a gitlink, which the diff never enters, does not; a record git
    does not write refuses."""
    diffed = _record(b'gone.py') + b'\0' + _gone(b'gone.py')
    unseen = {'listed': b'H 100644 ' + b'b' * 40 + b' 0\tgone.py\0R 100644 ' + b'b' * 40 + b' 0\tgone.py\0',
              'skipped': b'R 100644 ' + b'b' * 40 + b' 0\tdeep/too-long.py\0',
              'gitlink': b'H 160000 ' + b'1' * 40 + b' 0\tsub\0R 160000 ' + b'1' * 40 + b' 0\tsub\0',
              'unknown-tag': b'K 100644 ' + b'b' * 40 + b' 0\tgone.py\0',
              'malformed': b'R 100644 gone.py\0'}[case]
    if case in ('listed', 'gitlink'):
        rt._left_none_out(diffed, unseen, 40)
        return
    with pytest.raises(rt.RepoRefused) as refused:
        rt._left_none_out(diffed, unseen, 40)
    assert refused.value.code == 'G6_E_GIT_COMMAND_FAILED'


def test_the_diff_is_never_run_without_gits_listing_of_what_it_cannot_see():
    """From the code: ``_private`` runs ``_UNSEEN`` with the same private index whenever it runs ``_DIFF``, and hands both to
    ``_left_none_out``; ``diff_read`` asks for ``_DIFF`` through ``_private`` alone."""
    functions = _functions(rt)
    body = ast.unparse(functions['_private'])
    assert '_DIFF in commands' in body and '_left_none_out(' in body and '_UNSEEN' in body and 'index=index' in body
    assert rt._UNSEEN[:2] == ('ls-files', '--deleted') and '--stage' in rt._UNSEEN and '-t' in rt._UNSEEN
    reads = [ast.unparse(call) for _, call in _own_calls(functions['diff_read']) if _callee(call) in ('_git', '_private')]
    assert len(reads) == 1 and '_DIFF' in reads[0], reads


# --- open item B: one reader for the one quoting rule ---------------------------------------------------------------------------------

@pytest.mark.parametrize('prefix', ['', 'a/', 'b/'])
def test_the_one_reader_reads_back_every_name_the_one_rule_writes(prefix):
    names = [b'x' + bytes([byte]) + b'.py' for byte in range(1, 256)]
    names += [*EDGES.values(), LONGEST, 'odd-\udcff.py'.encode('utf-8', 'surrogatepass'), 'x\U0001F600.py'.encode()]
    for raw in names:
        rel = _rel(raw)
        assert rt.unquoted_name(rt.shown_name(rel, prefix)) == prefix + rel, raw


@pytest.mark.parametrize('letter,char', [('a', '\a'), ('b', '\b'), ('t', '\t'), ('n', '\n'), ('v', '\v'), ('f', '\f'),
                                         ('r', '\r'), ('"', '"'), ('\\', '\\')])
def test_patch_review_reads_gits_named_escapes_as_their_characters(letter, char):
    assert patch_review._unquoted(f'"q\\{letter}z.py"') == f'q{char}z.py'
    assert patch_review._diff_name(f'"a/q\\{letter}z.py"\n') == f'q{char}z.py'


@pytest.mark.parametrize('shown', ['"unterminated', '"bad\\qescape"', '"\\12"', 'bare"quote', 'bare\\slash', '"a"b"',
                                   'bare\ttab', '"\\777"'])
def test_text_the_one_rule_cannot_write_is_not_read_as_a_name(shown):
    with pytest.raises(ValueError):
        rt.unquoted_name(shown)


def test_each_name_in_the_identity_is_read_back_by_patch_review_as_git_gave_it(tmp_path, monkeypatch):
    rels = sorted({_rel(raw) for raw in EDGES.values()} | {_rel(LONGEST)})
    others = b''.join(rel.encode('utf-8', 'surrogateescape') + b'\0' for rel in rels)
    monkeypatch.setattr(rt, '_private', lambda *args: [b'', others])
    monkeypatch.setattr(rt, '_tracked_new', lambda *args: set())
    monkeypatch.setattr(rt, '_on_disk', lambda rel: (rel, True))
    monkeypatch.setattr(rt, '_leads_out', lambda *args: False)
    monkeypatch.setattr(Path, 'is_symlink', lambda path: False)
    monkeypatch.setattr(Path, 'is_file', lambda path: True)
    monkeypatch.setattr(rt, '_read_inside', lambda root, path: b'value = 1\n')
    text = rt.diff_read(rt.Change(tmp_path, 'a' * 40, 'a' * 40, 'a' * 40, tuple(rels), {}, {}, (), ())).text
    monkeypatch.undo()
    assert [names for names, _ in patch_review._diff_files(text)] == [{rel} for rel in rels]


# --- open item C: lines are cut as git writes them -------------------------------------------------------------------------------------

@pytest.mark.parametrize('separator', ['\x85', ' ', ' ', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\r'],
                         ids=['nel', 'ls', 'ps', 'vt', 'ff', 'fs', 'gs', 'rs', 'cr'])
def test_the_diff_is_cut_into_files_at_newlines_alone(separator):
    diff = (f'diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1,2 +0,0 @@\n-x{separator}-z\n--- y\n'
            'diff --git a/tests/test_b.py b/tests/test_b.py\n--- a/tests/test_b.py\n+++ b/tests/test_b.py\n@@ -1 +1 @@\n'
            '-    assert x\n+    pass\n')
    sections = patch_review._diff_files(diff)
    assert [sorted(names) for names, _ in sections] == [['src/a.py'], ['tests/test_b.py']]
    assert ''.join(text for _, text in sections) == diff
    assert 'assert x' in patch_review._test_diff(diff)


def test_patch_review_reads_names_by_the_one_reader_and_lines_at_newlines():
    """From the code: patch review keeps no quoting rule of its own (its reader is ``repo_tests.unquoted_name``), and no
    function that reads the diff cuts it with ``splitlines``."""
    functions = _functions(patch_review)
    assert 'unquoted_name' in {_callee(call) for call in ast.walk(functions['_unquoted']) if isinstance(call, ast.Call)}
    assert not hasattr(patch_review, '_ESCAPE')
    for name in ('_unquoted', '_diff_name', '_diff_files', '_test_diff', '_patch_imports_missing'):
        assert 'splitlines' not in {_callee(call) for call in ast.walk(functions[name]) if isinstance(call, ast.Call)}, name


# --- the inventories: each crossing a name makes in the read, from the code ------------------------------------------------------

def _names_a_path(node: ast.AST) -> bool:
    """Whether ``node`` is a name as git gave it (``rel``, ``one.rel``)."""
    return (isinstance(node, ast.Name) and node.id == 'rel') or (isinstance(node, ast.Attribute) and node.attr == 'rel')


def _nested(outer: ast.FunctionDef, name: str) -> ast.FunctionDef:
    return next(node for node in ast.walk(outer) if isinstance(node, ast.FunctionDef) and node.name == name)


def test_every_path_the_read_opens_or_looks_at_is_made_by_the_one_os_name_rule():
    """From the code: in ``diff_read`` (its ``found``) and ``_tracked_new``, a name git gave is never joined to the
    repository's path as it stands: the path is made from ``_on_disk``'s name, and each look and open uses that path."""
    functions = _functions(rt)
    found = _nested(functions['diff_read'], 'found')
    for name, function in (('diff_read', functions['diff_read']), ('found', found), ('_tracked_new', functions['_tracked_new'])):
        joined = [ast.unparse(node) for node in ast.walk(function) if isinstance(node, ast.BinOp)
                  and isinstance(node.op, ast.Div) and any(_names_a_path(part) for part in ast.walk(node.right))]
        assert not joined, (name, joined)
        looks = [ast.unparse(call) for _, call in _own_calls(function) if _callee(call) in ('_leads_out', 'lexists')]
        assert looks and all('rel' not in re.findall(r'\w+', text) for text in looks), (name, looks)
    assert 'on_disk, exact = _on_disk(rel)' in ast.unparse(found)


def test_every_name_the_read_puts_into_text_goes_through_the_one_quoting_rule():
    """From the code, widening repair 1's inventory to what this repair writes: every formatted value in the functions that
    write the identity's text of git's patch, and the refusals the read gives, puts a name in only through ``shown_name``;
    and ``diff_read`` decodes git's patch only to compare it with its bytes (``_bytes_line``), never into the text."""
    from tests.harness.test_masking_followup import _bare_path
    functions = _functions(rt)
    bare, places = [], 0
    for name in ('_named_patch', '_left_none_out', 'diff_read'):     # diff_read's walk holds its ``found`` too
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.FormattedValue):
                places += 1
                if _bare_path(node.value):
                    bare.append(f'{name}: {ast.unparse(node.value)}')
    assert places >= 10 and not bare, bare
    read = functions['diff_read']
    up = {child: node for node in ast.walk(read) for child in ast.iter_child_nodes(node)}
    decoded = [node for node in ast.walk(read) if isinstance(node, ast.Call) and _callee(node) == 'decode']
    assert decoded
    for node in decoded:
        parent = up[node]
        while not (isinstance(parent, ast.Call) and _callee(parent) == '_bytes_line'):
            assert parent in up, f"git's patch decoded into the text: {ast.unparse(node)}"
            parent = up[parent]
    assert '_named_patch(tracked, diffed)' in ast.unparse(read)


def test_the_header_check_is_gits_own_quoting_written_out_for_every_byte():
    """``_git_quoted`` writes a name as git's patch does with ``core.quotePath`` off; against git itself, for every byte a
    tree can hold in a name, the header git writes for a removed file is the one ``_named_patch`` expects (it would refuse
    the read otherwise), so no name git can give is refused for its spelling."""
    names = [b'n-' + bytes([byte]) + b'.py' for byte in range(1, 256) if byte != 0x2F]
    assert len(set(rt._git_quoted(b'a/' + raw) for raw in names)) == len(names)
    assert rt._git_quoted(b'a/x\x85.py') == b'a/x\x85.py' and rt._git_quoted(b'a/x\x7f.py') == b'"a/x\\177.py"'


def test_gits_header_for_every_byte_a_name_can_hold_is_the_one_the_read_expects(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    # ``n-`` first: git for Windows refuses a path that starts like a drive (``x:``) in any index
    names = [b'n-' + bytes([byte]) + b'.py' for byte in range(1, 256) if byte != 0x2F]
    base = _base(repo, {raw: b'value = 0\n' for raw in names})
    read = rt.diff_read(rt.Change(repo, base, base, base, tuple(_rel(raw) for raw in names), {}, {}, (), ()))
    assert len(read.files) == len(names)
    rels = [_rel(raw) for raw in names]
    got = _headers(read.text)
    assert sorted(line for line in got if line.startswith('diff --git ')) == sorted(
        f'diff --git {rt.shown_name(rel, "a/")} {rt.shown_name(rel, "b/")}' for rel in rels)   # each renamed: none left as git's
    assert sorted(line for line in got if line.startswith('--- ')) == sorted(f'--- {rt.shown_name(rel, "a/")}' for rel in rels)


def test_a_round_on_names_git_renders_its_own_way_keeps_its_patch_and_a_read_after_a_restart_names_the_same_patch(tmp_path):
    """The round's state as it is written: the patch kept byte for byte, then the review. A restart reads the patch again,
    and the same tree is the same patch."""
    from g6.core.ledger import digest
    repo = tmp_path / 'repo'
    repo.mkdir()
    entries = {b'sp ace.py': b'value = 0\n', b'odd-\xff.py': b'value = 0\n', 'ls .py'.encode(): b'value = 0\n',
               b'calc.py': b'value = 0\n'}
    base = _base(repo, entries)
    git_bytes(repo, 'update-ref', 'HEAD', base)
    git_bytes(repo, 'read-tree', base)
    (repo / 'calc.py').write_bytes(b'value = 1\n')
    rels = tuple(_rel(raw) for raw in entries)

    def changed():
        return rt.Change(repo, base, base, base, rels, {}, {}, ('calc.py',), ())

    reviews, session = [], tmp_path / 'session'
    loop.review_round(changed(), 'Review the change', Reviewer(), loop.harness(), session, reviews, float('inf'))
    kept = (session / 'rounds' / 'round-1.patch').read_bytes()
    again = rt.diff_read(changed())
    assert kept == again.text.encode('utf-8') and reviews[0]['patch'] == digest(again.text)
    assert f'--- {rt.shown_name(_rel(b"odd-" + bytes([0xFF]) + b".py"), "a/")}\n' in again.text

