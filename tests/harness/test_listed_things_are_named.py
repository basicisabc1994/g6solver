# SPDX-License-Identifier: Apache-2.0
"""Whatever git lists as there and G6 holds no bytes for is named, and refuses (review revmaskfu2a, findings 1 to 4).

review-maskfu4's finding 2 was one member of a class: something git lists as there, for which the read holds no bytes,
dropped unsaid. Its repair kept ``nul`` away from the branch that dropped it; the branch stood, for what it was written
for: a folder git lists whole and never looks into (a repository of its own, or a folder with a one-line ``.git`` file).
The class is removed here at its root:

* in the read (``diff_read``'s ``found``) every listed name ends as its bytes, or as removed on git's word, or as left
  unread and said so, or as a refusal the identity names and the copy raises: the inventory below reads that from the code;
* the snapshot hashes such a folder whole, as it hashes a submodule's folder, and says so where it cannot;
* patch review counts such a folder among the files that can change what the tests load;
* a name Windows reads as another's that git lists as removed refuses where something is stored under the name itself;
* every module that joins a listed name to the repository does it by the disk rule, or is ruled on here.
"""
import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import completion_loop as loop
from g6.harness import patch_review
from g6.metaharness import coding
from g6.workforce import repo_tests as rt
from tests.harness import test_git_names as flows
from tests.harness.test_name_crossings import Reviewer, _base, _gone, _record, git_bytes
from tests.harness.test_patch_review import ADD, BAD, GOOD, NEGATIVE, TEST, Recorded, result, review
from tests.workforce.test_repo_tests import git

_EXTENDED = "\\\\?\\"
windows = pytest.mark.skipif(os.name != "nt", reason="Win32 rewrites a path before it opens it")


def _name(text: str) -> rt.GitName:
    return rt.GitName(text.encode("utf-8"))


# --- finding 1, as its reviewer wrote it ------------------------------------------------------------------------------------------

HONEST = "def add(a, b):\n    return a + b\n"
RIGGED = "def add(a, b):\n    return {(2, 3): 5, (-2, -3): -5}[(a, b)]\n"      # right only for the two inputs the tests use
HOW = ["a .git file", "git init", "a package over the reviewed module"]


def _repo(tmp_path, how: str):
    """A committed repository, the change in it, and the file git never lists: ``hidden/impl.py``, which the changed
    ``calc.py`` imports, below a folder holding a ``.git`` file that names the repository's own git folder, or a repository
    of its own; or ``calc/__init__.py``, a package Python imports in place of the honestly repaired ``calc.py`` beside it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(BAD)
    (repo / "test_calc.py").write_text(TEST)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    folder, name = ("calc", "__init__.py") if how == HOW[2] else ("hidden", "impl.py")
    (repo / "calc.py").write_text(GOOD if how == HOW[2] else "from hidden.impl import add\n")
    (repo / folder).mkdir()
    if how == HOW[1]:
        git(repo / folder, "init", "-q")
    else:
        (repo / folder / ".git").write_text("gitdir: ../.git\n")
    (repo / folder / name).write_text(HONEST)
    assert rt._git(repo, "ls-files", "--others", "--exclude-standard", "-z") == f"{folder}/".encode() + b"\0"   # not its file
    return repo, f"{folder}/", repo / folder / name


def _in_place(repo) -> tuple[int, str]:
    """What the working tree's code does when run where it stands, as a review's own test run runs it."""
    ran = subprocess.run([sys.executable, "-c", "from calc import add; print(add(1, 1))"], cwd=repo, capture_output=True,
                         text=True)
    return ran.returncode, ran.stdout.strip()


@pytest.mark.parametrize("how", HOW)
def test_finding_1_a_folder_git_lists_as_there_is_named_in_what_the_patch_is_known_by_or_the_copy_refuses(tmp_path, how):
    repo, listed, _ = _repo(tmp_path, how)
    change = rt.change(repo, "HEAD")
    assert listed in change.files
    try:
        read = rt.diff_read(change)
        one = next(f for f in read.files if f.rel == listed)
        loop._shown(change, read)
    except rt.RepoRefused:
        return      # refused: never shown as nothing
    assert one.new is not None or one.unread or rt.shown_name(one.rel) in read.text, (
        "a folder git lists as there has no line in what the patch is known by, and the reviewer's copy shows nothing of "
        "it or of the files below it")


@pytest.mark.parametrize("how", HOW)
def test_finding_1_two_trees_that_run_differently_never_share_one_identity_and_one_clean_review(tmp_path, how):
    repo, _, unlisted = _repo(tmp_path, how)
    seen = []
    for payload in (HONEST, RIGGED):
        unlisted.write_text(payload)
        change = rt.change(repo, "HEAD")
        try:
            read = rt.diff_read(change)
            reviewer = Reviewer()
            found = loop.run_critic(change, "Repair addition", reviewer, "Review", tmp_path / "critic", patch=read, whole=True)
        except rt.RepoRefused:
            return      # refused: no clean review
        seen.append({"text": read.text, "verdict": found["verdict"], "shown": "{(2, 3): 5" in reviewer.sent[0],
                     "runs": _in_place(repo), "artifact": patch_review.digest(rt.snapshot(repo))})
    assert seen[0]["runs"] == (0, "2") and seen[1]["runs"][0] != 0      # the probe is sound: the two trees run differently
    assert not (seen[0]["text"] == seen[1]["text"] and seen[0]["verdict"] == seen[1]["verdict"] == "none"), (
        f"two trees whose code runs differently in place give one identity, and each is reviewed clean; the reviewer was "
        f"shown the rigged file: {seen[1]['shown']}; one artifact digest for both: {seen[0]['artifact'] == seen[1]['artifact']}")


def test_finding_1_patch_review_does_not_support_a_patch_whose_code_it_never_read(tmp_path, monkeypatch):
    repo, listed, unlisted = _repo(tmp_path, HOW[2])
    first = patch_review.digest(rt.snapshot(repo))
    unlisted.write_text(RIGGED)
    second = patch_review.digest(rt.snapshot(repo))
    assert _in_place(repo)[0] != 0      # the package is what runs in place, not the calc.py the diff shows
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assessment = got.get("assessment") or {}
    said = str(assessment.get("reasons")) + str(assessment.get("unknowns")) + str(got.get("warning_card"))
    assert assessment.get("outcome") != "supported_within_scope" or listed in said or first != second, (
        "supported_within_scope, with no reason or unknown naming the folder git listed, and one artifact digest for two "
        "trees that differ in the code the tests ran")
    # the root, each part on its own: the outcome, the reason that names the folder, and the digest
    assert assessment.get("outcome") != "supported_within_scope"
    assert listed in str(assessment.get("reasons")) and first != second


# --- the root of finding 1: one rule for whatever git lists as there ------------------------------------------------------------

@pytest.mark.parametrize("how", HOW[:2])
def test_a_listed_folder_is_named_refuses_the_copy_is_hashed_whole_and_counts_as_loadable(tmp_path, how):
    repo, listed, unlisted = _repo(tmp_path, how)
    change = rt.change(repo, "HEAD")
    read = rt.diff_read(change)
    one = next(f for f in read.files if f.rel == listed)
    said = f"{rt.shown_name(_name(listed))} {rt.NOT_A_FILE}"
    assert (one.new, one.unread, str(one.refusal)) == (None, False, said) and f"\\ G6: {said}\n" in read.text
    with pytest.raises(rt.RepoRefused) as refused:
        loop._shown(change, read)
    assert refused.value.code == "G6_E_GIT_COMMAND_FAILED" and str(refused.value) == said
    reviewer = Reviewer()
    with pytest.raises(rt.RepoRefused):
        loop.run_critic(change, "Repair addition", reviewer, "Review", tmp_path / "critic", patch=read, whole=True)
    assert not reviewer.sent
    key = rt._SUBMODULE + listed
    before = rt.snapshot(repo)
    unlisted.write_text(RIGGED)
    after = rt.snapshot(repo)
    assert key in before and before[key] != after[key] and len(before[key]) == 64      # every file in it, hashed
    assert rt._moved(before, after) == [rt.SAID["submodule_name"].format(path=rt.shown_name(_name(listed)))]
    assert listed in rt.loadable_changes(change)


def test_a_listed_folder_the_snapshot_cannot_hash_is_said_never_read_as_unchanged(tmp_path, monkeypatch):
    repo, listed, _ = _repo(tmp_path, HOW[0])
    monkeypatch.setattr(rt, "COPY_MAX_FILES", 1)
    found = rt.snapshot(repo)
    assert found[rt._SUBMODULE + listed].startswith(rt.NOT_HASHED)
    assert list(rt.submodules_not_hashed(found)) == [listed]


def _listing(monkeypatch, tracked: bytes = b"", others: bytes = b"") -> None:
    monkeypatch.setattr(rt, "_private", lambda *args: [tracked, others])
    monkeypatch.setattr(rt, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(rt, "blob_bytes", lambda *args, **kwargs: b"value = 0\n")


def _one(where: Path, name: str) -> tuple[rt.DiffRead, rt.FileRead, rt.Change]:
    change = rt.Change(where, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ())
    read = rt.diff_read(change)
    one, = read.files
    return read, one, change


def _named_and_refused(read: rt.DiffRead, one: rt.FileRead, change: rt.Change, why: str) -> None:
    said = f"{rt.shown_name(one.rel)} {why}"
    assert one.new is None and not one.unread and str(one.refusal) == said and f"\\ G6: {said}\n" in read.text
    with pytest.raises(rt.RepoRefused):
        loop._shown(change, read)


@pytest.mark.parametrize("listed", ["untracked", "changed"])
def test_a_file_that_goes_between_gits_listing_and_the_read_is_named_and_refuses(tmp_path, monkeypatch, listed):
    (tmp_path / "x.py").write_bytes(b"value = 1\n")
    changed = (b":100644 100644 " + b"b" * 40 + b" " + b"0" * 40 + b" M\0x.py\0\0diff --git a/x.py b/x.py\n"
               b"index bbbbbbb..ccccccc 100644\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n")
    _listing(monkeypatch, *((changed, b"") if listed == "changed" else (b"", b"x.py\0")))
    real = rt._read_inside

    def gone_by_now(root, path):
        os.unlink(path)
        return real(root, path)

    monkeypatch.setattr(rt, "_read_inside", gone_by_now)
    _named_and_refused(*_one(tmp_path, "x.py"), rt.NOT_THERE)


@pytest.mark.parametrize("mode", [b"160000", b"100644"])
def test_a_tracked_name_git_lists_as_there_where_a_folder_stands_is_named_and_refuses(tmp_path, monkeypatch, mode):
    """A submodule's folder (never set up, so nothing in it is git's to read), or a folder where the file was."""
    (tmp_path / "sub").mkdir()
    _listing(monkeypatch, b":" + mode + b" " + mode + b" " + b"b" * 40 + b" " + b"0" * 40 + b" M\0sub\0\0")
    _named_and_refused(*_one(tmp_path, "sub"), rt.NOT_A_FILE)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a system with named pipes")
def test_a_listed_pipe_is_named_and_refuses_and_is_never_opened(tmp_path, monkeypatch):
    os.mkfifo(tmp_path / "pipe")
    _listing(monkeypatch, others=b"pipe\0")
    monkeypatch.setattr(rt, "_read_inside", lambda *args: pytest.fail("a pipe was opened"))
    _named_and_refused(*_one(tmp_path, "pipe"), rt.NOT_A_FILE)
    assert rt._digest(tmp_path / "pipe") == "unreadable"


def test_every_way_the_read_ends_with_no_bytes_for_a_listed_name_is_said_from_the_code():
    """The inventory, from the code: in ``found`` each ``return`` of a file with no text (``FileRead(rel, None, ...)``) is
    one of three: left unread (``unread=True``, which the identity and the copy each say), removed on git's word (under
    ``if gone:``), or a refusal (``refusal=``, which the identity names and the copy raises). No fourth way is there; and
    in ``diff_read`` every file with a refusal gets its line."""
    functions = {node.name: node for node in ast.walk(ast.parse(inspect.getsource(rt))) if isinstance(node, ast.FunctionDef)}
    found = next(node for node in ast.walk(functions["diff_read"]) if isinstance(node, ast.FunctionDef) and node.name == "found")
    up = {child: node for node in ast.walk(found) for child in ast.iter_child_nodes(node)}
    ways = []
    for node in ast.walk(found):
        made = isinstance(node, ast.Call) and getattr(node.func, "id", "") == "FileRead"
        if not made or not (isinstance(node.args[1], ast.Constant) and node.args[1].value is None):
            continue
        keys = {key.arg: key.value for key in node.keywords}
        above = node
        while not isinstance(above, ast.If) and above in up:
            above = up[above]
        under_gone = isinstance(above, ast.If) and ast.unparse(above.test) == "gone"
        ways.append("unread" if isinstance(keys.get("unread"), ast.Constant) and keys["unread"].value is True
                    else "refusal" if "refusal" in keys else "removed" if under_gone else ast.unparse(node))
    assert sorted(set(ways)) == ["refusal", "removed", "unread"] and len(ways) >= 5, ways
    text = ast.unparse(functions["diff_read"])
    assert text.count("{one.refusal}") == 2      # the tracked files' loop and the untracked files' loop each write the line


def test_the_snapshot_reads_nothing_only_where_nothing_is_or_gits_own_listing_covers_it_from_the_code():
    """From the code: ``_digest`` gives ``""`` in two places alone, a plain folder (whose files git lists on their own) and
    a name with nothing there; and ``snapshot`` hashes a folder git lists whole by ``_submodule``, every file in it."""
    functions = {node.name: node for node in ast.walk(ast.parse(inspect.getsource(rt))) if isinstance(node, ast.FunctionDef)}
    empty = [ast.unparse(node) for node in ast.walk(functions["_digest"]) if isinstance(node, ast.Return)
             and ((isinstance(node.value, ast.Constant) and node.value.value == "")
                  or (isinstance(node.value, ast.IfExp) and '""' in ast.unparse(node.value).replace("''", '""')))]
    assert sorted(empty) == ["return ''", "return 'unreadable' if os.path.lexists(path) else ''"], empty
    assert "_submodule(repo, rel, budget)" in ast.unparse(functions["snapshot"])
    assert ast.unparse(functions["snapshot"]).count("_submodule(repo, rel, budget)") == 2      # a listed folder, a gitlink


# --- finding 2, as its reviewer wrote it, and its root ------------------------------------------------------------------------------

@windows
@pytest.mark.parametrize("name", ["x.py.", "nul", "x.py "])
def test_finding_2_a_tracked_file_stored_under_such_a_name_is_never_reviewed_clean_as_removed(tmp_path, name):
    repo = tmp_path / "repo"
    repo.mkdir()
    base = _base(repo, {name.encode(): b"value = 0\n", b"keep.py": b"keep = 0\n"})
    git_bytes(repo, "update-ref", "HEAD", base)
    git_bytes(repo, "read-tree", base)
    (repo / "keep.py").write_bytes(b"keep = 1\n")
    listed = Path(_EXTENDED + str(repo / name))
    seen = []
    try:
        for payload in (b"payload = 2\n", b"payload = 3\n"):
            listed.write_bytes(payload)
            assert listed.is_file() and listed.read_bytes() == payload      # a regular file, with bytes, under the listed name
            change = rt.change(repo, base)
            assert name in change.files
            try:
                read = rt.diff_read(change)
                reviewer = Reviewer()
                found = loop.run_critic(change, "Review the change", reviewer, "Review", tmp_path / "critic", patch=read,
                                        whole=True)
            except rt.RepoRefused:
                return      # refused: the present file was never shown as removed
            one = next(f for f in read.files if f.rel == name)
            seen.append({"text": read.text, "verdict": found["verdict"], "new": one.new, "refusal": one.refusal,
                         "sent": reviewer.sent[0]})
    finally:
        if os.path.lexists(listed):
            os.unlink(listed)
    assert not (seen[0]["text"] == seen[1]["text"] and seen[0]["verdict"] == seen[1]["verdict"] == "none"), (
        f"a regular file is stored under {name!r}, which the change lists; the reviewer is shown it as removed "
        f"({'+++ /dev/null' in seen[0]['sent']}), two trees that differ only in its bytes give one identity, and each is "
        "reviewed clean")


@windows
@pytest.mark.parametrize("name", ["x.py.", "x.py ", "nul", "dir./x.py", " "])
@pytest.mark.parametrize("stored", ["a file", "a folder", "nothing"])
def test_a_removed_name_windows_reads_as_anothers_refuses_exactly_where_something_is_stored_under_it(
        tmp_path, monkeypatch, name, stored):
    """git's word that nothing is there came through the rewriting. So the name itself is looked up, by the literal form of
    the path, which Windows rewrites nothing in: a file or a folder stored under it refuses the read; nothing there is
    removed, as git said. Nothing is opened either way."""
    (tmp_path / "x.py").write_bytes(b"another file's bytes\n")      # what the rewritten name reaches
    literal = Path(_EXTENDED + str(tmp_path.resolve()) + "\\" + name.replace("/", "\\"))
    made = []
    try:
        if stored != "nothing":
            for folder in reversed(literal.parents):
                if not folder.exists():
                    folder.mkdir()
                    made.append(folder)
            literal.write_bytes(b"stored\n") if stored == "a file" else literal.mkdir()
            made.append(literal)
        _listing(monkeypatch, _record(name.encode()) + b"\0" + _gone(name.encode()))
        for reader in ("_read_inside", "_link_inside"):
            monkeypatch.setattr(rt, reader, lambda *args: pytest.fail("the read opened something for a removed name"))
        change = rt.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (_name(name),), {}, {}, (), ())
        if stored == "nothing":
            one, = rt.diff_read(change).files
            assert (one.new, one.refusal, one.unread) == (None, None, False)
        else:
            with pytest.raises(rt.RepoRefused) as refused:
                rt.diff_read(change)
            assert str(refused.value) == f"{rt.shown_name(_name(name))} {rt.READ_AS_ANOTHER}"
    finally:
        for path in reversed(made):
            os.rmdir(path) if path.is_dir() else os.unlink(path)


# --- finding 3, as its reviewer wrote it ------------------------------------------------------------------------------------------

BEYOND = ["\U0001F642\U0001F642.py", "docs/\U00020000\U00020001.md", "\U0001F389\U0001F389\U0001F389.txt"]


@windows
@pytest.mark.parametrize("name", BEYOND)
def test_finding_3_a_name_windows_reads_as_itself_is_exact_whatever_plane_its_characters_are_in(name):
    assert rt._as_named(name), "Windows rewrites nothing in this name; the rule's buffer is too short to hear it say so"
    assert rt._on_disk(_name(name)) == (name, True)


@windows
def test_finding_3_a_new_file_named_with_two_such_characters_is_read_and_the_patch_is_not_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"a = 1\n")
    git(repo, "init", "-q")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "base")
    name = BEYOND[0]
    (repo / name).write_bytes(b"smile = 2\n")
    assert (repo / name).read_bytes() == b"smile = 2\n"      # a plain open reads the file stored under the name
    change = rt.change(repo, "HEAD")
    assert name in change.files
    try:
        read = rt.diff_read(change)
    except rt.RepoRefused as refused:
        pytest.fail(f"the patch is not read: {ascii(str(refused))}")
    assert [one.new for one in read.files if one.rel == name] == ["smile = 2\n"]
    assert name in change.sources


@windows
@pytest.mark.parametrize("name", ["\U0001F642\U0001F642.py.", "\U00020000\U00020001/nul", "\U0001F389" * 40 + " "])
def test_a_rewritten_name_with_such_characters_is_still_never_exact(name):
    assert not rt._as_named(name) and rt._on_disk(_name(name)) == (name, False)


@windows
def test_a_buffer_too_small_is_asked_again_and_is_never_the_answer(monkeypatch):
    """The rule's own retry: Windows answers a buffer that is too small with the size it needs (here one more than it
    was given, for the first question about each name), and the rule asks again with that size."""
    import ctypes

    asked = []
    real = ctypes.windll.kernel32.GetFullPathNameW

    class Short:
        def GetFullPathNameW(self, given, size, buffer, part):
            asked.append(size)
            return size + 1 if len(asked) % 2 else real(given, size, buffer, part)

    monkeypatch.setattr(ctypes.windll, "kernel32", Short())
    assert rt._as_named("plain/name.py") and not rt._as_named("plain/name.py.")
    assert len(asked) == 4 and asked[1] == asked[0] + 2 and asked[3] == asked[2] + 2


# --- finding 4, as its reviewer wrote it, and its root: every join of a listed name, in every module that imports the reader --------

OTHER = "def add(a, b):\n    return a + b\n"
LISTED = "LISTED = 'what is stored under the listed name'\n"


@windows
def test_finding_4_the_mutation_oracle_never_answers_from_another_files_text_under_a_listed_name(tmp_path):
    repo = tmp_path / "repo"
    (repo / "dir").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "dir" / "x.py").write_text(OTHER)
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    folder = Path(_EXTENDED + str(repo / "dir."))
    folder.mkdir()
    (folder / "x.py").write_text(LISTED)
    tried, verdict = [], None
    try:
        assert (folder / "x.py").read_text() == LISTED and (repo / "dir" / "x.py").read_text() == OTHER    # two files
        change = rt.change(repo)
        assert change.files == ("dir./x.py",) and change.sources == ()      # git lists it; the disk rule gives it no path

        def run(where, tests, *, timeout_s, mutant=None):
            tried.append(mutant)
            return {"verdict": "failed", "passed": 0}      # every changed version is caught

        try:
            verdict = coding._mutants_killed(repo, ["tests/test_x.py"], timeout_s=60, run=run)
        except (rt.RepoRefused, OSError):
            return      # refused: no answer from another file's bytes
    finally:
        os.unlink(folder / "x.py")
        os.rmdir(folder)
    assert not tried, (
        f"the oracle answered {verdict} from {len(tried)} changed versions of dir/x.py's text, each run under the listed "
        f"name {str.__str__(tried[0][0])!r}; the file stored under that name was never read")
    assert verdict is False      # and it does not answer that the tests pin a file it could not read


#: Each place, in any module that imports the reader, where a name git listed is joined to a folder or given to a look or
#: an open other than by the disk rule (``repo_tests._disk``, ``_on_disk``, ``_in``), by its module and source text, with
#: why what is read there cannot be another file's bytes under a listed name in a verdict, a report or evidence. A place
#: not here fails the inventory; an entry no place matches any more fails it too.
_SOURCES = "the name is one of Change.sources or Change.after, which hold only names the disk rule gave a path for"
JOINED = {
    "g6.bundles.agent_change: repo / rel": _SOURCES,
    "g6.code.swe_diagnostics.block: repo / rel": _SOURCES,
    "g6.harness.verification_proposer: change.repo / rel": _SOURCES,
    "g6.harness.verification_search: change.repo / rel": _SOURCES,
    "g6.harness.patch_review: root / rel": ("_execute: a stated check's or an extra test's file, a path the caller gave "
                                           "(shape and containment by repo_tests.inside), or one derive_tests gave, which "
                                           "keeps only names the disk rule gave a path for"),
    "g6.workforce.verify: Path(rel)": "a pure path, for a file's name, stem or pattern: no disk is looked at",
    "g6.workforce.verify: Path(workdir) / rel": ("run_set: a candidate test file searched for a changed module's name; "
                                                 "repo_tests.derive_tests, its one caller with names git listed, keeps "
                                                 "of what it returns only names the disk rule gave a path for"),
}


def test_every_join_of_a_listed_name_to_the_repository_in_every_module_is_the_disk_rule_or_ruled_on():
    """The disk-rule inventory, widened (finding 4): not the calls of the three rule functions alone, but every place a
    name git listed becomes a path for the system, by the flow of the type through every module the reader's names reach.
    ``repo_tests`` itself has none; the others have the ruled ones alone."""
    program = flows._Program(flows.ROOTS)
    found = {f"{line.split(':')[0]}: {line.split(': ', 1)[1]}" for flow in program.flows.values()
             for line in flows._disk_sinks(flow)}
    assert found == set(JOINED), sorted(found ^ set(JOINED))
    assert not [key for key in found if key.startswith("g6.workforce.repo_tests:")]


def test_every_module_that_imports_the_reader_is_one_the_inventory_starts_from():
    """From the code: each module of G6 that imports ``g6.workforce.repo_tests``, at its top or inside a function."""
    source = Path(inspect.getsourcefile(rt)).parents[1]
    importing = set()
    for path in source.rglob("*.py"):
        module = "g6." + ".".join(path.relative_to(source).with_suffix("").parts).removesuffix(".__init__")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                     [f"{node.module}.{alias.name}" for alias in node.names] + [node.module]
                     if isinstance(node, ast.ImportFrom) and node.module and not node.level else
                     ["g6.workforce." + alias.name for alias in node.names]
                     if isinstance(node, ast.ImportFrom) and node.level and module.startswith("g6.workforce") and not node.module
                     else [])
            if any(name == "g6.workforce.repo_tests" or name.startswith("g6.workforce.repo_tests.") for name in named):
                importing.add(module)
    assert importing and importing <= set(flows.ROOTS), sorted(importing - set(flows.ROOTS))
