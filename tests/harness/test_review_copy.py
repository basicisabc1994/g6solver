# SPDX-License-Identifier: Apache-2.0
"""The reviewer's copy of a patch is made from the files, never from git's rendering of them (review-kfu4, findings 1 to 4).

Each changed file's whole text at the base and now is scrubbed alone and the two are compared here, so no git display setting
reaches what the reviewer reads, a name outside the hunk is still beside its value, and nothing crosses between files. The
patch is still known by the bytes of one read (``repo_tests.diff_read``). Reviewers are recorded, git is run only on scratch
repositories, and no test of a reviewer's ever runs."""
# A made-up key's opening marker is written here as two adjacent literals, which Python joins when it compiles the file:
# each test uses the whole marker, and the public scan (tools/export_public.py), which reads the file's text, finds no key.
import dataclasses
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from g6.context.redact import MASK, scrub
from g6.core.consent_callers import CallerWrapper
from g6.core.ledger import digest
from g6.core.llm import FakeLLM
from g6.harness import completion_hook, completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.mcp import review_loop
from g6.workforce import repo_tests
from tests.harness.test_completion_loop_followup import rig as source_rig  # noqa: F401 - isolated, Git-free fixture
from tests.workforce.test_repo_tests import git, make_repo


@pytest.fixture
def rig(source_rig):  # noqa: F811 - the fixture it builds on
    # As review-kfu4's probes: the session's own folders made first, with inherited access.
    source_rig.session.mkdir(parents=True)
    source_rig.home.joinpath(*loop.OWING).mkdir(parents=True)
    completion_hook.folder("mcp:probe-repeat").mkdir(parents=True)
    return source_rig


class Recorded(CallerWrapper):
    """A reviewer that finds nothing, and keeps what it was sent. It answers through G6's own local test caller
    (``FakeLLM``), handed on by the published wrapper contract, so the consent guard knows it for the local process it
    is: nothing goes to a cloud recipient, and no grant is asked for."""
    model = "recorded-reviewer"

    def __init__(self):
        super().__init__(FakeLLM(self.answer))
        self.sent = []

    def answer(self, system, user):
        self.sent.append(user)
        token = re.search(r"review [0-9a-f]{32}", user)[0]
        return f"READ: {token}\nRULE: no defect\nVERDICT: none\nWHY: no defect\n"


def blob_id(rel: str) -> str:
    """The object name a fake listing gives the base's blob of `rel`."""
    return hashlib.sha1(rel.encode()).hexdigest()


def link_read(link: Path, target, real=os.readlink):
    """``os.readlink`` with the placeholder ``link`` read as a link naming ``target()``, for an account that cannot make one,
    called as ``repo_tests._link_inside`` calls it: by its path (on Windows the held folder's own, in the extended form the
    system gives since review-maskfu3's finding 1, so compared in that form), or by its name in the folder it holds
    (``dir_fd``)."""
    def readlink(path, *, dir_fd=None):
        if repo_tests._extended(Path(path)) == repo_tests._extended(link) or (dir_fd is not None and path == link.name):
            return target()
        return real(path) if dir_fd is None else real(path, dir_fd=dir_fd)
    return readlink


def listing(base: dict, now: dict, modes: dict | None = None) -> bytes:
    """Git's ``--raw -z`` records for the tracked files of ``base`` and ``now``, as ``repo_tests._DIFF`` gives them first."""
    records = []
    for rel in sorted(base):
        gone = now.get(rel) is None
        old, new = (modes or {}).get(rel, ("100644", "000000" if gone else "100644"))
        records.append(f":{old} {new} {blob_id(rel)} {'0' * 40} {'D' if gone else 'M'}\0{rel}\0".encode())
    return b"".join(records)


def held(tmp_path: Path, monkeypatch, base: dict, now: dict, rendering: bytes = b"", outside: tuple = (),
         modes: dict | None = None) -> repo_tests.Change:
    """A change of the files in ``now`` (text or bytes as each stands, None where it is gone) against a base that held
    ``base`` (a file it lacks is new since), with ``rendering`` what git's own patch of them says, after git's own listing of
    the tracked ones (``listing``; ``modes``: a file's modes at the base and now, where not a plain file's). Git never runs."""
    for rel, text in now.items():
        if text is not None:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    rels = tuple(sorted(set(base) | set(now)))
    new = "".join(f"{rel}\0" for rel in rels if rel not in base).encode()
    listed = listing(base, now, modes)
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed + b"\0" + rendering if listed else rendering, new])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(repo_tests, "read_at", lambda repo, commit, rel, **kwargs: base.get(rel))
    monkeypatch.setattr(repo_tests, "blob_bytes", lambda repo, name, **kwargs: {blob_id(rel): text.encode("utf-8") if isinstance(text, str) else text for rel, text in base.items()}[name])
    return repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, rels, {}, {},
                             tuple(rel for rel in rels if rel.endswith(".py")), (), outside_links=outside)


def unified(base: dict, now: dict) -> bytes:
    """A unified diff of the files, as git's rendering of them might read."""
    return "".join(f"diff --git a/{rel} b/{rel}\n" + "".join(difflib.unified_diff(
                       (base.get(rel) or "").splitlines(True), (now.get(rel) or "").splitlines(True), fromfile=f"a/{rel}",
                       tofile=f"b/{rel}"))
                   for rel in sorted(set(base) | set(now))).encode()


def shown(change: repo_tests.Change) -> loop.Shown:
    return loop._shown(change, repo_tests.diff_read(change))


# --- review-kfu4's four findings, each as its reviewer wrote it, the patch now read from its files --------------------------

def test_finding_1_a_reindented_value_found_beside_its_name_never_reaches_the_reviewer(tmp_path, monkeypatch):
    secret = "made-up-review-value-123456789"
    before = 'cfg = {\n    "api_key":\n\n\n\n\n        "' + secret + '",\n}\n'
    after = before.replace('        "' + secret, '            "' + secret)
    patch = unified({"config.py": before}, {"config.py": after})
    assert secret not in scrub(before)
    assert b'"api_key"' not in patch  # the normal three-line hunk omits the assignment name
    change = held(tmp_path, monkeypatch, {"config.py": before}, {"config.py": after}, patch)
    reviewer = Recorded()
    loop.run_critic(change, "Keep the configuration valid", reviewer, "Review the patch", tmp_path,
                    patch=repo_tests.diff_read(change))
    assert secret not in reviewer.sent[0], "base-recognised secret leaked in the reindented added line"


def test_finding_2_a_key_in_one_file_masks_nothing_of_another(tmp_path, monkeypatch):
    parents = {
        "fixture.txt": ("-----BEGIN " "PRIVATE KEY-----\n    return 1\n-----END PRIVATE KEY-----\n"
                        + "# filler\n" * 10 + "# old\n"),
        "mod.py": "def value():\n    return 0\n",
    }
    after = {"fixture.txt": parents["fixture.txt"].replace("# old", "# updated"),
             "mod.py": parents["mod.py"].replace("return 0", "return 1")}
    patch = unified(parents, after)
    assert b"PRIVATE KEY" not in patch  # the key is not among the lines this review must read
    change = held(tmp_path, monkeypatch, parents, after, patch)
    reviewer = Recorded()
    found = loop.run_critic(change, "Update the fixture and return one", reviewer, "Review the patch", tmp_path,
                            patch=repo_tests.diff_read(change))
    assert "+    return 1\n" in reviewer.sent[0], f"fixture.txt masked mod.py's return statement; review result: {found}"
    assert found["verdict"] == "none"


def test_finding_3_gits_blank_context_setting_cannot_part_a_name_from_its_value(tmp_path, monkeypatch):
    secret = "made-up-indicator-secret-123456789"
    before = "\ncfg = {}\n"
    after = '\ncfg = {\n    "api_key":\n        "' + secret + '",\n}\n'
    old_file, new_file = tmp_path / "old.py", tmp_path / "mod.py"
    old_file.write_bytes(before.encode())
    new_file.write_bytes(after.encode())
    assert secret not in scrub(after)
    # Read-only Git operation: no repository/index/config writes and no external diff or textconv.
    result = subprocess.run([shutil.which("git"), "-c", "diff.suppressBlankEmpty=true", "diff", "--no-index",
                             "--no-ext-diff", "--no-textconv", "--no-color", "--text", "--",
                             old_file.name, new_file.name], cwd=tmp_path, capture_output=True, check=False)
    assert result.returncode == 1, result.stderr.decode(errors="replace")
    assert b'@@ -1,2 +1,5 @@\n\n-cfg' in result.stdout
    change = repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40,
                               ("mod.py",), {}, {}, ("mod.py",), ())
    # git's listing of the tracked file first, as the read asks for it, then git's own patch with the setting
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listing({"mod.py": before}, {"mod.py": after}) + b"\0"
                                                               + result.stdout, b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(repo_tests, "read_at", lambda *args: before)
    monkeypatch.setattr(repo_tests, "blob_bytes", lambda *args, **kwargs: before.encode("utf-8"))
    reviewer = Recorded()
    loop.run_critic(change, "Keep the configuration valid", reviewer, "Review the patch", tmp_path,
                    patch=repo_tests.diff_read(change))
    assert secret not in reviewer.sent[0], "Git's suppressed blank-context mark bypassed multiline-assignment masking"


def test_finding_4_a_refusals_ending_is_read_after_its_row_is_written(rig, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda _: "boundary unavailable")
    original_write = loop._write_row
    edited = False

    def write_with_concurrent_edit(session, state):
        nonlocal edited
        original_write(session, state)
        if not edited:
            # Deterministic interleaving: an editor changes the repository while this
            # refusal finishes writing its failure row, before the session is done.
            rig.source.write_bytes(b"value = 99\n")
            edited = True

    monkeypatch.setattr(loop, "_write_row", write_with_concurrent_edit)
    args = (str(rig.repo), "Keep value one", "probe-repeat")
    first = review_loop.step(*args, base=rig.base)
    assert edited and not rig.calls and first["ending"] == "untested"
    assert rig.source.read_bytes() == b"value = 99\n"
    # No file changes between the end of the first call and this retry.
    repeated = review_loop.step(*args, base=rig.base)
    assert repeated["report"] == first["report"], "unchanged ending patch was falsely reported as an earlier patch"
    assert repeated["note"] == loop.ALREADY_ENDED


# --- the class: what the reviewer reads is made from the files, and nothing of git's rendering reaches it ----------------------

def test_no_setting_of_gits_display_reaches_the_copy_and_each_still_changes_what_the_patch_is_known_by(tmp_path):
    secret = "made-up-display-secret-0123456789"
    repo = make_repo(tmp_path)
    (repo / "conf.py").write_bytes(b"a = 1\n\nb = 2\n\nvalue = 0\n")
    git(repo, "add", "conf.py")
    git(repo, "commit", "-q", "-m", "conf")
    (repo / "conf.py").write_bytes(b'a = 1\n\nb = 2\n\ncfg = {\n    "api_key":\n        "' + secret.encode() + b'",\n}\n')
    change = repo_tests.change(repo)
    last = repo_tests.diff_read(change)
    copies = [loop._shown(change, last)]
    for name, value in (("diff.suppressBlankEmpty", "true"), ("diff.context", "1"), ("diff.mnemonicPrefix", "true"),
                        ("diff.noprefix", "true"), ("diff.algorithm", "patience")):
        git(repo, "config", name, value)
        read = repo_tests.diff_read(repo_tests.change(repo))
        copies.append(loop._shown(change, read))
        if name in ("diff.mnemonicPrefix", "diff.noprefix"):     # G6 tells git the sides' prefixes (review-maskfu2, finding 1)
            assert read.text == last.text, f"{name} changed how the identity names a file"
        else:
            assert read.text != last.text or name == "diff.algorithm", f"git ignored {name}, so this case tests nothing"
        last = read
    assert all(copy == copies[0] for copy in copies)
    assert secret not in copies[0].masked and '"api_key":' in copies[0].masked


def test_whatever_git_renders_the_copy_is_the_same(tmp_path, monkeypatch):
    base, now = {"mod.py": "def f():\n    return 0\n"}, {"mod.py": "def f():\n    return 1\n"}
    copies = []
    for rendering in (unified(base, now), b"diff --git a/mod.py b/mod.py\n", b"diff --git @@ -9 +9 @@\n+not what changed\n",
                      b"diff --git \xff\xfe garbage \x00\n"):
        copies.append(shown(held(tmp_path, monkeypatch, base, now, rendering)))
    assert all(copy == copies[0] for copy in copies) and "-    return 0\n+    return 1\n" in copies[0].text


def test_a_value_moved_away_from_its_name_stays_masked(tmp_path, monkeypatch):
    secret = "made-up-moved-value-0123456789"
    before = 'cfg = {\n    "api_key":\n        "' + secret + '",\n}\n'
    after = 'values = [\n            "' + secret + '",\n]\n'      # its name taken away, and the line indented anew
    assert secret in scrub(after)      # the new text read alone has no name to find the value by
    copy = shown(held(tmp_path, monkeypatch, {"conf.py": before}, {"conf.py": after}))
    assert secret not in copy.masked and f'+            "{MASK}",' in copy.masked


def test_a_value_changed_on_its_line_shows_the_line_changed_with_both_values_masked(tmp_path, monkeypatch):
    old, new = "made-up-old-value-0000", "made-up-new-value-1234"
    before, after = (f'config = {{\n    "api_key":\n        "{value}",\n}}\n' for value in (old, new))
    copy = shown(held(tmp_path, monkeypatch, {"conf.py": before}, {"conf.py": after}))
    assert old not in copy.masked and new not in copy.masked
    assert f'-        "{MASK}",\n+        "{MASK}",\n' in copy.masked and not copy.hidden


def test_a_key_over_lines_the_copy_shows_is_hidden_and_one_away_from_them_is_not(tmp_path, monkeypatch):
    key = "-----BEGIN " "PRIVATE KEY-----\nmade-up-key-body\n-----END PRIVATE KEY-----\n"
    base = {"keys.txt": key + "# filler\n" * 10 + "# old\n"}
    away = shown(held(tmp_path, monkeypatch, base, {"keys.txt": base["keys.txt"].replace("# old", "# new")}))
    over = shown(held(tmp_path, monkeypatch, base, {"keys.txt": base["keys.txt"].replace("key-body", "key-body-2")}))
    assert not away.hidden and "PRIVATE KEY" not in away.text
    assert over.hidden and "made-up-key-body" not in over.masked


def test_what_is_left_of_a_key_whose_first_line_was_taken_out_stays_masked_where_it_is_unchanged(tmp_path, monkeypatch):
    base = {"keys.txt": "-----BEGIN " "PRIVATE KEY-----\nmade-up-key-body\n-----END PRIVATE KEY-----\n# tail\n"}
    now = {"keys.txt": base["keys.txt"].split("\n", 1)[1]}
    assert "made-up-key-body" in scrub(now["keys.txt"])        # the text as it is now no longer reads as a key
    copy = shown(held(tmp_path, monkeypatch, base, now))
    assert " made-up-key-body\n" in copy.text and "made-up-key-body" not in copy.masked and copy.hidden


def test_a_line_inside_a_key_masks_no_line_like_it_elsewhere_in_its_file(tmp_path, monkeypatch):
    base = "def f():\n    return 1\n\n\n\n\n\n\nMARK = '-----BEGIN " "PRIVATE KEY-----'\nx = 1\n    return 1\n"
    copy = shown(held(tmp_path, monkeypatch, {"mod.py": base}, {"mod.py": base.replace("def f", "def g")}))
    assert "+def g():\n     return 1\n" in copy.masked and not copy.hidden    # the key runs to the end, over the last line only


def test_a_key_marker_on_a_files_last_line_hides_no_other_line(tmp_path, monkeypatch):
    copy = shown(held(tmp_path, monkeypatch, {}, {"a.py": 'MARK = "-----BEGIN ' 'PRIVATE KEY-----"\n',
                                                  "b.py": "def kept():\n    return 1\n"}))
    assert "BEGIN PRIVATE" not in copy.masked and "+def kept():\n+    return 1\n" in copy.masked
    assert not copy.hidden       # the value runs on to the file's last newline, and over no line of it


def test_a_file_whose_bytes_are_not_utf8_is_shown_with_a_mark_for_each_and_the_line_naming_its_bytes(tmp_path, monkeypatch):
    raw = b"# coding: latin-1\nvalue = '\xe9'\n"
    copy = shown(held(tmp_path, monkeypatch, {"mod.py": "# coding: latin-1\nvalue = 'a'\n"}, {"mod.py": raw}))
    assert "+value = '�'\n" in copy.text
    assert f"\\ G6: mod.py shows here only in part; its bytes have SHA-256 {hashlib.sha256(raw).hexdigest()}\n" in copy.text


def test_a_file_that_leads_out_of_the_repository_is_named_and_never_read(tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "out.py").write_text("NEVER-READ = 1\n", encoding="utf-8")
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(tmp_path / "linked"))
        else:
            (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this system does not let the test make a link: {exc}")
    change = held(tmp_path, monkeypatch, {"linked/out.py": "kept = 1\n", "mod.py": "a = 1\n"},
                  {"linked/out.py": "unchanged", "mod.py": "a = 2\n"})
    (outside / "out.py").write_text("NEVER-READ = 1\n", encoding="utf-8")     # what lies past the link, written last
    read = repo_tests.diff_read(change)
    copy = loop._shown(change, read)
    assert "NEVER-READ" not in read.text and "NEVER-READ" not in copy.text and "-kept = 1" not in copy.text
    assert "\\ G6: linked/out.py leads out of the repository" in copy.text and "+a = 2\n" in copy.text
def test_a_removed_file_is_shown_removed_and_a_lost_last_newline_as_git_shows_it(tmp_path, monkeypatch):
    copy = shown(held(tmp_path, monkeypatch, {"gone.py": "x = 1\n", "mod.py": "y = 1\n"}, {"gone.py": None, "mod.py": "y = 1"}))
    assert "--- a/gone.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n" in copy.text
    assert "-y = 1\n+y = 1\n\\ No newline at end of file\n" in copy.text


def test_a_tracked_link_is_shown_by_the_path_it_names_and_that_path_is_part_of_what_the_patch_is_known_by(tmp_path, monkeypatch):
    change = held(tmp_path, monkeypatch, {"ln": "old/target"}, {"ln": "placeholder"}, modes={"ln": ("120000", "120000")})
    link, real_is_symlink = tmp_path / "ln", Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or real_is_symlink(self))
    monkeypatch.setattr(os, "readlink", link_read(link, lambda: "new/target"))
    read = repo_tests.diff_read(change)
    assert hashlib.sha256(b"new/target").hexdigest() in read.text
    assert "-old/target\n\\ No newline at end of file\n+new/target\n\\ No newline at end of file\n" in \
        loop._shown(change, read).text


def test_a_new_files_copy_is_the_patchs_own_section(tmp_path, monkeypatch):
    change = held(tmp_path, monkeypatch, {}, {"new.py": "a = 1\nb = 2\n"})
    read = repo_tests.diff_read(change)
    assert loop._shown(change, read).text == read.text == "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"


def test_a_base_that_cannot_be_read_asks_no_reviewer(tmp_path, monkeypatch):
    change = held(tmp_path, monkeypatch, {"mod.py": "a = 1\n"}, {"mod.py": "a = 2\n"})

    def unreadable(*args, **kwargs):
        raise repo_tests.RepoRefused("G6_E_GIT_COMMAND_FAILED", "git show failed")

    monkeypatch.setattr(repo_tests, "read_at", unreadable)
    monkeypatch.setattr(repo_tests, "blob_bytes", unreadable)
    reviewer = Recorded()
    with pytest.raises(repo_tests.RepoRefused):
        loop.run_critic(change, "Keep a at two", reviewer, "Review the patch", tmp_path, patch=repo_tests.diff_read(change))
    assert not reviewer.sent


def test_a_round_is_known_by_its_read_and_its_reviewer_reads_the_copy_made_from_that_read(rig, monkeypatch,
                                                                                            accepted_cloud_dialog):
    from g6.core.cloud_consent import input_source
    accepted_cloud_dialog(input_source("Keep value one"), "openai:api", text="Keep value one")   # this round's task
    rendering = b"diff --git a/mod.py b/mod.py\nindex 0000000..1111111 100644\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n" \
                b"-value = 0\n+value = 1\n"
    listed = listing({"mod.py": "value = 0\n"}, {"mod.py": "value = 1\n"}) + b"\0"
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed + rendering, b""])
    monkeypatch.setattr(repo_tests, "read_at", lambda *args: "value = 0\n")
    monkeypatch.setattr(repo_tests, "blob_bytes", lambda *args, **kwargs: b"value = 0\n")
    read = repo_tests.diff_read(rig.change)
    assert read.text.startswith("diff --git") and [one.rel for one in read.files] == ["mod.py"]
    ended = loop.advance(rig.repo, "Keep value one", rig.base, rig.session, critic=rig.critic)
    assert ended.ending == "clean" and len(rig.calls) == 1
    assert loop._load(rig.session)["reviews"][0]["patch"] == digest(read.text)
    assert (rig.session / "rounds" / "round-1.patch").read_bytes() == read.text.encode("utf-8")
    assert loop._shown(rig.change, read).masked in rig.calls[0]
    assert "diff --git" not in rig.calls[0] and "\\ G6: the bytes of" not in rig.calls[0]    # git's rendering, and G6's identity


def test_a_refusal_cut_short_before_its_end_ends_on_the_patch_as_it_stands_when_it_ends(rig, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda _: "boundary unavailable")
    real = loop._finish

    def killed(*args, **kwargs):
        raise KeyboardInterrupt("the hook was stopped after the row, before the end was kept")

    monkeypatch.setattr(loop, "_finish", killed)
    args = (str(rig.repo), "Keep value one", "probe-repeat")
    with pytest.raises(KeyboardInterrupt):
        review_loop.step(*args, base=rig.base)
    rig.source.write_bytes(b"value = 7\n")
    monkeypatch.setattr(loop, "_finish", real)
    first = review_loop.step(*args, base=rig.base)
    repeated = review_loop.step(*args, base=rig.base)
    assert first["ending"] == "untested" and repeated["report"] == first["report"]
    assert repeated["note"] == loop.ALREADY_ENDED and review_loop.CHANGED not in repeated["report"]
    rows = (rig.home / "completion_loop" / "failures.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["cause"] == loop.UNCONFINED
    rig.source.write_bytes(b"value = 8\n")
    assert review_loop.CHANGED in review_loop.step(*args, base=rig.base)["report"]


# --- review-mask1's eight findings: mask first, then render; one listing; what the identity covers, the copy shows ---------

@pytest.mark.parametrize("grows", [False, True], ids=["mask-shrinks", "mask-expands"])
def test_mask1_finding_1_the_length_rule_counts_the_masked_copy(tmp_path, monkeypatch, grows):
    value = "made-up-" + "x" * loop.MAX_PACK_CHARS
    raw = ("".join(f'password = "aaaa" # {i}\n' for i in range(4500)) if grows
           else f'password = "{value}"\nanswer = 42\n')
    change = held(tmp_path, monkeypatch, {}, {"mod.py": raw})
    read = repo_tests.diff_read(change)
    shown = loop._shown(change, read)
    assert not shown.hidden
    assert (len(loop._head(shown.masked, "Keep the answer", "review " + "0" * 32)) <= loop.MAX_PACK_CHARS) is not grows
    reviewer = Recorded()
    found = loop.run_critic(change, "Keep the answer", reviewer, "Review", tmp_path, patch=read, whole=True)
    if grows:
        assert not reviewer.sent and found["error"] == loop.TOO_LONG
    else:
        assert len(reviewer.sent) == 1 and found["verdict"] == "none"


def test_mask1_finding_2_a_file_below_a_link_out_is_named_and_never_opened_again(tmp_path, monkeypatch):
    repo, target = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    target.mkdir()
    (repo / "mod.py").write_text("value = 42\n", encoding="utf8")
    (target / "module.py").write_text("value = 42\n", encoding="utf8")
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(repo / "linked"))
        else:
            (repo / "linked").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this system does not let the test make a link: {exc}")
    change = repo_tests.Change(repo, "a" * 40, "a" * 40, "a" * 40, ("linked/module.py", "mod.py"), {}, {}, ("mod.py",), ())
    listed = listing({"linked/module.py": "", "mod.py": ""}, {"linked/module.py": "", "mod.py": ""}) + b"\0"
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed, b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())

    def no_git(*args, **kwargs):
        if args[1:] == ("cat-file", "blob", blob_id("mod.py")):
            return b"value = 42\n"
        raise AssertionError("the unread file's base must not be requested")

    monkeypatch.setattr(repo_tests, "_git", no_git)
    read = repo_tests.diff_read(change)
    assert read.files[0].unread and "G6 did not read it" in loop._shown(change, read).masked
    reviewer = Recorded()
    loop.run_critic(change, "Review the named change", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert len(reviewer.sent) == 1


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""], ids=["lf", "crlf", "last-line"])
def test_mask1_finding_3_a_value_is_found_in_the_whole_file_before_it_is_cut(tmp_path, monkeypatch, ending):
    value = "made-up-cut-boundary-123456789"
    padding = repo_tests.DIFF_FILE_CHARS - len('password = ""\n') - len('api_key = "') - len(value)
    raw = 'password = "' + "x" * padding + '"\n' + 'api_key = "' + value + '"' + ending
    assert value not in scrub(raw)
    change = held(tmp_path, monkeypatch, {}, {"mod.py": raw})
    reviewer = Recorded()
    loop.run_critic(change, "Keep the module valid", reviewer, "Review", tmp_path, patch=repo_tests.diff_read(change))
    assert value not in reviewer.sent[0], "cutting off the closing quote before secret_spans leaked a whole recognised value"


def test_mask1_finding_3_a_value_the_cut_falls_inside_is_masked_as_far_as_it_is_shown(tmp_path, monkeypatch):
    value = "made-up-straddling-value-" + "y" * 40
    raw = "#" * (repo_tests.DIFF_FILE_CHARS - len('token = "') - 20) + '\ntoken = "' + value + '"\n'
    shown = loop._shown(*(lambda change: (change, repo_tests.diff_read(change)))(held(tmp_path, monkeypatch, {},
                                                                                       {"mod.py": raw})))
    assert value[:15] not in shown.masked and MASK in shown.masked


def test_mask1_finding_4_the_copys_files_are_the_files_of_the_read_that_names_the_patch(tmp_path, monkeypatch):
    rendering = (b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old_a\n+new_a\n"
                 b"diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old_b\n+new_b\n")
    every = held(tmp_path, monkeypatch, {"a.py": "old_a\n", "b.py": "old_b\n"}, {"a.py": "new_a\n", "b.py": "new_b\n"},
                 rendering)
    earlier = dataclasses.replace(every, files=("a.py",), sources=("a.py",))   # change() saw only a.py
    read = repo_tests.diff_read(earlier)
    assert "+new_b" in read.text
    reviewer = Recorded()
    loop.run_critic(earlier, "Review both edits", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert "+new_b\n" in reviewer.sent[0], "the identity includes b.py but the copy uses the stale Change.files listing"


def test_mask1_finding_5_a_new_links_identity_is_the_path_it_names_not_what_it_leads_to(tmp_path, monkeypatch):
    (tmp_path / "one.py").write_text("answer = 42\n", encoding="utf8")
    (tmp_path / "two.py").write_text("answer = 42\n", encoding="utf8")
    change = held(tmp_path, monkeypatch, {}, {"new_link.py": "placeholder"})
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: {"new_link.py"})
    link, named = tmp_path / "new_link.py", ["one.py"]
    real_is_symlink, real_read_bytes = Path.is_symlink, Path.read_bytes
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or real_is_symlink(path))
    monkeypatch.setattr(os, "readlink", link_read(link, lambda: named[0]))
    monkeypatch.setattr(Path, "read_bytes", lambda path: real_read_bytes(tmp_path / named[0]) if path == link
                        else real_read_bytes(path))
    before = repo_tests.diff_read(change)
    named[0] = "two.py"
    after = repo_tests.diff_read(change)
    assert before.text != after.text, "two new tracked links naming different paths have the same patch identity"
    copy = loop._shown(change, after).text
    assert "new file mode 120000\n" in copy and "+two.py\n" in copy and "answer = 42" not in copy


def test_mask1_finding_6_and_each_field_of_the_identity_is_in_the_copy(tmp_path, monkeypatch):
    """What the identity covers, from ``diff_read``: content, mode, a link's path, a removal, an addition (a file moved is a
    removal and an addition, as ``--no-renames`` lists it), and the bytes; each is in what the reviewer is sent."""
    run = "#!/usr/bin/env python\nprint(42)\n"
    rendering = b"diff --git a/run.py b/run.py\nold mode 100644\nnew mode 100755\n"
    change = held(tmp_path, monkeypatch, {"run.py": run, "old.py": "moved = 1\n", "mod.py": "a = 1\n"},
                  {"run.py": run, "old.py": None, "new.py": "moved = 1\n", "mod.py": b"a = 2\r\n"}, rendering,
                  modes={"run.py": ("100644", "100755")})
    read = repo_tests.diff_read(change)
    assert "old mode 100644\nnew mode 100755" in read.text
    reviewer = Recorded()
    found = loop.run_critic(change, "Check executable permissions", reviewer, "Review", tmp_path, patch=read, whole=True)
    sent = reviewer.sent[0]
    assert found["verdict"] == "none" and "old mode 100644\nnew mode 100755\n" in sent       # the mode
    assert "deleted file mode 100644\n--- a/old.py\n+++ /dev/null\n" in sent and "+++ b/new.py\n@@ -0,0 +1,1 @@\n+moved = 1\n" in sent
    assert "-a = 1\n+a = 2\n" in sent and hashlib.sha256(b"a = 2\r\n").hexdigest() in sent     # content, and its bytes


def test_mask1_finding_7_a_bare_cr_inside_a_value_never_unmasks_it(tmp_path, monkeypatch):
    first, second = "made-up-carriage-part-one", "made-up-carriage-part-two"
    raw = f'password = "{first}\r{second}"\n'
    assert first not in scrub(raw) and second not in scrub(raw)
    change = held(tmp_path, monkeypatch, {}, {"config.txt": raw})
    reviewer = Recorded()
    loop.run_critic(change, "Review the configuration", reviewer, "Review", tmp_path, patch=repo_tests.diff_read(change),
                    whole=True)
    assert first not in reviewer.sent[0] and second not in reviewer.sent[0]


def test_mask1_a_value_found_at_the_base_as_read_stays_masked_in_the_base_shown_and_in_its_parent(tmp_path, monkeypatch):
    first, second = "made-up-base-part-one", "made-up-base-part-two"
    base = f'password = "{first}\r{second}"\r\nvalue = 0\r\n'
    reviewer = Recorded()
    change = held(tmp_path, monkeypatch, {"conf.py": base}, {"conf.py": base.replace("value = 0", "value = 1")})
    loop.run_critic(change, "Keep value one", reviewer, "Review", tmp_path, patch=repo_tests.diff_read(change))
    assert first not in reviewer.sent[0] and second not in reviewer.sent[0] and "parent/conf.py:\n" in reviewer.sent[0]


def test_mask1_finding_8_each_byte_that_is_not_utf8_is_one_mark(tmp_path, monkeypatch):
    raw = b'value = "\xf0\x9f\x92"\n'
    change = held(tmp_path, monkeypatch, {}, {"mod.py": raw})
    shown = loop._shown(change, repo_tests.diff_read(change))
    assert hashlib.sha256(raw).hexdigest() in shown.text
    assert shown.text.count("\ufffd") == 3, "the decoder collapsed three malformed bytes into one replacement character"

@pytest.mark.parametrize("output", [b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n",
                                    b":100644 100644 0000000 0000000 R100\0a.py\0b.py\0\0", b":100644\0a.py\0\0"],
                         ids=["patch-without-listing", "two-paths", "short-record"])
def test_a_read_git_does_not_list_whole_is_refused_never_shown_short(tmp_path, monkeypatch, output):
    change = held(tmp_path, monkeypatch, {}, {})
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [output, b""])
    with pytest.raises(repo_tests.RepoRefused):
        repo_tests.diff_read(change)

# --- review-mask2's eight findings: git's records by git's rules, one rule for what is shown, containment on the open file

@pytest.mark.parametrize("record", [
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" X\0mod.py\0\0",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" U\0mod.py\0\0",
    b":not-a-mode 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0mod.py\0\0",
    b":100644 100644 not-an-object " + b"0" * 40 + b" M\0mod.py\0\0",
    b":100644 100644 0000000 0000000 M\0mod.py\0\0",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0\0\0",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M 9\0mod.py\0\0",
], ids=["unknown-status", "unmerged", "invalid-mode", "invalid-object", "abbreviated-object", "empty-path",
        "a-shape-never-seen"])
def test_mask2_finding_1_a_record_not_in_gits_form_refuses_the_read(tmp_path, monkeypatch, record):
    change = repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (), {}, {}, (), ())
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [record, b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    with pytest.raises(repo_tests.RepoRefused, match="listing") as refused:
        repo_tests.diff_read(change)
    assert refused.value.code == "G6_E_GIT_COMMAND_FAILED"


@pytest.mark.parametrize("rel", ["-mod.py", "@mod.py"])
def test_mask2_finding_2_a_name_git_listed_is_reviewed_as_git_named_it(tmp_path, rel):
    repo = make_repo(tmp_path)
    (repo / rel).write_bytes(b"value = 0\n")
    git(repo, "add", "--", rel)
    git(repo, "commit", "-q", "-m", "base")
    (repo / rel).write_bytes(b"value = 1\n")
    base = git(repo, "rev-parse", "HEAD").strip()
    change = repo_tests.Change(repo, base, base, base, (rel,), {}, {}, (), ())
    read = repo_tests.diff_read(change)
    assert [one.rel for one in read.files] == [rel]
    reviewer = Recorded()
    found = loop.run_critic(change, "Keep value one", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert found["verdict"] == "none" and "-value = 0\n+value = 1\n" in reviewer.sent[0]


def test_mask2_finding_3_a_value_named_past_the_cut_is_masked_on_a_line_like_it_before_the_cut(tmp_path, monkeypatch):
    value = "made-up-duplicate-value-0123456789"
    duplicate = f'    "{value}",\n'
    raw = ('values = [\n' + duplicate + ']\npassword = "' + "x" * repo_tests.DIFF_FILE_CHARS
           + '"\ncfg = {\n    "api_key":\n' + duplicate + '}\n')
    assert raw.rindex(value) > repo_tests.DIFF_FILE_CHARS
    change = held(tmp_path, monkeypatch, {}, {"new.py": raw})
    reviewer = Recorded()
    found = loop.run_critic(change, "Keep the configuration valid", reviewer, "Review", tmp_path,
                            patch=repo_tests.diff_read(change), whole=True)
    assert reviewer.sent and found["verdict"] == "none" and value not in reviewer.sent[0]


def test_mask2_finding_4_a_value_over_lines_of_which_one_is_shown_hides_no_line(tmp_path, monkeypatch):
    before = ("-----BEGIN " "PRIVATE KEY-----\nmade-up-key-body\n-----END PRIVATE KEY-----\n"
              "# after one\n# after two\nvalue = 0\n")
    change = held(tmp_path, monkeypatch, {"mod.py": before}, {"mod.py": before.replace("value = 0", "value = 1")})
    read = repo_tests.diff_read(change)
    shown = loop._shown(change, read)
    assert shown.masked.count(MASK) == 1 and not shown.hidden
    task = "t" * (loop.MAX_PACK_CHARS - len(loop._sent_head(shown.masked, "", "review " + "0" * 32)))
    reviewer = Recorded()
    found = loop.run_critic(change, task, reviewer, "Review", tmp_path, patch=read, whole=True)
    assert "parent/mod.py:" not in reviewer.sent[0] and found["verdict"] == "none"
    over = shown_two = held(tmp_path, monkeypatch, {"mod.py": before},
                            {"mod.py": before.replace("# after one", "# after 1")})
    assert loop._shown(over, repo_tests.diff_read(shown_two)).hidden      # two of the key's lines are shown


def test_mask2_finding_5_an_earlier_outside_verdict_never_hides_a_file_as_it_stands_now(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "calc.py").write_bytes(b"value = 1\n")
    change = dataclasses.replace(repo_tests.change(repo), outside_links=("calc.py",))   # an earlier read's verdict
    read = repo_tests.diff_read(change)
    reviewer = Recorded()
    found = loop.run_critic(change, "Keep value one", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert found["verdict"] == "none" and "+value = 1\n" in reviewer.sent[0]


def test_mask2_finding_6_a_tracked_links_base_is_read_by_its_object_name_wherever_it_leads_now(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    link, outside = repo / "ln", tmp_path / "outside.txt"
    real_resolve, real_is_symlink = Path.resolve, Path.is_symlink
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **kw: outside if path == link else real_resolve(path, *a, **kw))
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or real_is_symlink(path))
    monkeypatch.setattr(os, "readlink", link_read(link, lambda: "../outside.txt"))
    listed = b":120000 120000 " + b"b" * 40 + b" " + b"0" * 40 + b" M\0ln\0\0"
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [listed, b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    asked = []

    def git_blob(root, *args, **kwargs):
        asked.append(args)
        assert args == ("cat-file", "blob", "b" * 40), "the base was looked up by a path"
        return b"../old-outside.txt"

    monkeypatch.setattr(repo_tests, "_git", git_blob)
    change = repo_tests.Change(repo, "a" * 40, "a" * 40, "a" * 40, ("ln",), {}, {}, (), (), outside_links=("ln",))
    read = repo_tests.diff_read(change)
    assert read.files[0].new == "../outside.txt" and not read.files[0].unread
    reviewer = Recorded()
    found = loop.run_critic(change, "Review the link target", reviewer, "Review", tmp_path, patch=read, whole=True)
    assert found["verdict"] == "none" and "-../old-outside.txt\n" in reviewer.sent[0] and "+../outside.txt\n" in reviewer.sent[0]


def test_mask2_finding_7_a_task_value_over_a_lone_cr_hides_lines_as_a_files_does(tmp_path):
    task = 'password = "made-up-first-part\rmade-up-second-part"'
    change = repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, (), {}, {}, (), ())
    reviewer = Recorded()
    found = loop.run_critic(change, task, reviewer, "Review", tmp_path, patch=repo_tests.DiffRead("", ()), whole=True)
    assert found["verdict"] == "failed" and found["error"] == loop.HIDDEN
    assert "made-up-first-part" not in reviewer.sent[0] and "made-up-second-part" not in reviewer.sent[0]


def test_mask2_finding_8_a_folder_swapped_for_a_link_out_as_the_file_is_opened_is_never_read(tmp_path, monkeypatch):
    repo, outside = make_repo(tmp_path), tmp_path / "outside"
    folder, saved = repo / "nested", repo / "saved"
    folder.mkdir()
    outside.mkdir()
    (folder / "mod.py").write_bytes(b"value = 0\n")
    (outside / "mod.py").write_bytes(b"outside_repository_content = 12345\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "nested")
    (folder / "mod.py").write_bytes(b"value = 1\n")
    change = repo_tests.change(repo)
    real_open, swapped = os.open, []

    def swap_as_it_opens(path, *args, **kwargs):
        if Path(path) == folder / "mod.py" and not swapped:      # after every check G6 makes before the open
            folder.rename(saved)
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(outside), str(folder))
            else:
                folder.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_as_it_opens)
    try:
        read = repo_tests.diff_read(change)
    finally:
        monkeypatch.setattr(os, "open", real_open)
        if swapped:
            os.rmdir(folder) if os.name == "nt" else folder.unlink()
            saved.rename(folder)
    assert swapped and read.files[0].unread and read.files[0].new is None
    assert "outside_repository_content" not in read.text

# --- review-mask3's five findings: one masking rule and one decoder, git's grammar, the identity and the copy agree --------

def test_mask3_finding_1_what_scrub_masks_is_masked_in_the_patch_and_in_its_parent(tmp_path):
    prefix, suffix = "made-up-prefix-123456789", "made-up-suffix-987654321"
    source = (f'password = "{prefix}-----BEGIN ' 'PRIVATE KEY-----\n'
              f'made-up-body\n-----END PRIVATE KEY-----{suffix}"\nvalue = 0\n')
    assert prefix not in scrub(source) and suffix not in scrub(source)
    repo = make_repo(tmp_path)
    (repo / "config.txt").write_bytes(source.encode())
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "config")
    (repo / "config.txt").write_bytes(source.replace("value = 0", "value = 1").encode())
    change = repo_tests.change(repo)
    reviewer = Recorded()
    loop.run_critic(change, "Review this edit", reviewer, "Review", tmp_path / "critic", patch=repo_tests.diff_read(change),
                    whole=True)
    patch, parent = reviewer.sent[0].split("parent/config.txt:", 1)
    assert not any(part in text for part in (prefix, suffix) for text in (patch, parent))


def test_mask3_each_span_secret_spans_names_is_what_scrub_masks_however_the_rules_meet():
    from g6.context.redact import secret_spans

    for text in ('password = "a-----BEGIN ' 'PRIVATE KEY-----\nb\n-----END PRIVATE KEY-----c"\n',
                 "token = 'sk-abcdefghijklmnop1234 and more'\n", "plain text\n", ""):
        shown, at = [], 0
        for start, end in secret_spans(text):
            shown += [text[at:start], MASK]
            at = end
        assert "".join(shown) + text[at:] == scrub(text)


@pytest.mark.parametrize("output", [
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0visible.py\0omitted.py\0\0diff --git a/omitted.py b/omitted.py\n",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0visible.py\0extra\0",
    b":100644 100644 " + b"a" * 40 + b" " + b"0" * 40 + b" M\0visible.py\0\0not git's patch\n",
], ids=["second-path", "missing-path", "trailing-field", "patch-not-git's"])
def test_mask3_finding_2_the_listing_is_read_by_gits_grammar_one_path_to_a_record(tmp_path, monkeypatch, output):
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [output, b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    change = repo_tests.Change(tmp_path, "b" * 40, "b" * 40, "b" * 40, ("visible.py", "omitted.py"), {}, {}, (), ())
    with pytest.raises(repo_tests.RepoRefused) as caught:
        repo_tests.diff_read(change)
    assert caught.value.code == "G6_E_GIT_COMMAND_FAILED"


def test_mask3_finding_3_bytes_the_identity_tells_apart_the_copy_tells_apart_removed_files_too(tmp_path):
    reads, copies = [], []
    for number, byte in enumerate((b"\xe9", b"\xf1")):
        repo = make_repo(tmp_path / f"case-{number}")
        (repo / "old.txt").write_bytes(b"value = '" + byte + b"'\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "old")
        (repo / "old.txt").unlink()
        change = repo_tests.change(repo)
        read = repo_tests.diff_read(change)
        reads.append(read.text)
        copies.append(loop._shown(change, read).masked)
    assert reads[0] != reads[1] and copies[0] != copies[1]
    assert all("old.txt at the base shows here only in part; its bytes have SHA-256" in copy for copy in copies)


def test_mask3_finding_4_a_file_the_copy_names_unread_is_named_in_what_the_patch_is_known_by(tmp_path):
    repo, outside = make_repo(tmp_path), tmp_path / "outside-repo"
    outside.mkdir()
    (outside / "first.txt").write_bytes(b"fixture that must stay unread\n")
    link = repo / "linked"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this system does not let the test make a link: {exc}")
    try:
        (repo / "calc.py").write_bytes(b"value = 1\n")
        before = repo_tests.diff_read(repo_tests.change(repo))
        (outside / "first.txt").rename(outside / "second.txt")
        after = repo_tests.diff_read(repo_tests.change(repo))
        assert any(one.rel == "linked/first.txt" and one.unread for one in before.files)
        assert before.text != after.text and "linked/second.txt" in after.text and "must stay unread" not in after.text
    finally:
        os.rmdir(link) if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("added", [True, False], ids=["new-link", "tracked-link"])
def test_mask3_finding_5_a_links_path_is_shown_through_the_one_decoder(tmp_path, monkeypatch, added):
    link = tmp_path / "link"
    link.write_bytes(b"placeholder")
    target = b"made-up-target-\xff".decode("utf-8", "surrogateescape")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or real_is_symlink(path))
    monkeypatch.setattr(os, "readlink", link_read(link, lambda: target))
    listed = b":120000 120000 " + b"b" * 40 + b" " + b"0" * 40 + b" M\0link\0\0"
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [b"" if added else listed, b"link\0" if added else b""])
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    read = repo_tests.diff_read(repo_tests.Change(tmp_path, "a" * 40, "a" * 40, "a" * 40, ("link",), {}, {}, (), ()))
    assert read.files[0].new == repo_tests._decoded(os.fsencode(target)) and "\udcff" not in read.files[0].new


def test_the_reviewer_is_shown_text_through_one_decoder_and_one_masking_rule_alone():
    """An inventory, built from the code: each place on the way to the reviewer that decodes bytes or masks text. Masking is
    ``_seen``'s, by ``secret_spans``, alone (``mask_paths`` then takes home folders out of a test's output, which is no
    secret's mask), and ``_found``'s yes or no for a name is ``secret_spans`` on the name itself (review-maskfu2, finding
    2); decoding of what is shown is ``repo_tests._decoded``'s alone. ``_listing`` decodes git's own fields and names (shown
    only through ``repo_tests.shown_name``, which ``_name`` is, and which what the patch is known by names them by too: git's
    quoting, which decodes nothing, review-maskfu1), ``_named_patch`` and ``diff_read`` decode git's patch for what the patch
    is known by, never shown, and ``_on_disk`` decodes a name only to give it to the system (review-maskfu2, finding 3).
    ``read_report`` (brought by the consent merge) is not on the way to the reviewer: it decodes G6's own saved report,
    read against the digest in its checked state, for the reply to the client."""
    import ast
    import inspect

    watched, found = {"scrub", "secret_spans", "_decoded", "decode", "mask_paths"}, set()
    path = {"diff_read", "found", "named", "unread", "_listing", "blob_bytes", "_decoded", "_normal", "_cut_lines",
            "_bytes_line", "_read_inside", "_link_inside", "_where", "_leads_out", "shown_name", "name_line", "_octal",
            "now_line", "_part", "_part_line", "_link_line", "_named_patch", "_on_disk", "_within", "_git_quoted"}
    for module, only in ((loop, None), (repo_tests, path)):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.FunctionDef) and (only is None or node.name in only):
                for call in (one for one in ast.walk(node) if isinstance(one, ast.Call)):
                    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                    if name in watched:
                        found.add((module.__name__.rsplit(".", 1)[1], node.name, name))
    assert found == {("completion_loop", "_seen", "secret_spans"), ("completion_loop", "_found", "secret_spans"),
                     ("completion_loop", "_shown", "_decoded"), ("completion_loop", "_unrun", "mask_paths"),
                     ("completion_loop", "read_report", "decode"),
                     ("repo_tests", "_decoded", "decode"), ("repo_tests", "_listing", "decode"),
                     ("repo_tests", "diff_read", "decode"), ("repo_tests", "diff_read", "_decoded"),
                     ("repo_tests", "found", "_decoded"), ("repo_tests", "_where", "decode"),
                     ("repo_tests", "_named_patch", "decode"), ("repo_tests", "_on_disk", "decode")}, found