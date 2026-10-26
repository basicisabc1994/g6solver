# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.workspace_manager: a workspace folder per task under the G6 home, where every read and write is
resolved against the folder and refused if it would leave it, and backups, restores and clean-ups that change nothing
until asked (D1, the P1 findings, G1, G3, G4, G5), and the member through its doors (D2, D4, D6, D9)."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path

import pytest

import g6.workspace.workspace_manager as workspace_manager
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.core.storage import state_path
from g6.mcp import describe_reply, invoke_reply
from g6.workspace import _envelope as E
from g6.workspace import _files
from g6.workspace.workspace_manager import Workspace, ops

OPS = ["contained", "read", "files", "status", "write", "backup", "restore", "cleanup"]
SENSITIVE = ["write", "backup", "restore", "cleanup"]
ALLOW = frozenset(f"workspace_manager.{op}" for op in SENSITIVE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _invoke(op: str, allow: frozenset[str] = frozenset(), **params: object) -> dict:
    return invoke_reply("workspace_manager", op, json.dumps(params), allow_sensitive=allow)


def _task(home: Path, task_id: str = "t1", files: dict[str, str] | None = None) -> Workspace:
    ws = Workspace.for_task(task_id)
    for rel, text in (files or {"notes.txt": "first"}).items():
        assert ws.write(rel, text, dry_run=False)["written"]
    return ws


def _escapes(ws: Workspace, outside: Path, links) -> list[str]:  # noqa: ANN001
    """Paths that would leave ``ws``: by '..', by an absolute path, through a link to a folder outside (when one can be
    made here), and into a device name."""
    found = ["../escaped.txt", "sub/../../escaped.txt", str(outside / "escaped.txt"), "CON", "\\\\host\\share\\x"]
    ws.root.mkdir(parents=True, exist_ok=True)
    if links.folder(ws.root / "door", outside):
        found += ["door/escaped.txt", "door/secret.txt"]
    return found


# --- D1: the failure it catches, generally ----------------------------------------------------------------------------

def test_d1_a_write_that_would_leave_the_workspace_is_refused_and_nothing_outside_changes(home, outside, links) -> None:
    ws = _task(home)
    for raw in _escapes(ws, outside, links):
        with pytest.raises(E.WorkspaceRefused) as refused:
            ws.write(raw, "overwritten", dry_run=False)
        assert refused.value.code in (E.OUTSIDE_ROOT, E.INPUT_INVALID), (raw, refused.value)
        result = ops.write("t1", raw, "overwritten", dry_run=False)
        assert result.completion_state == "blocked-escalated" and result.code in (E.OUTSIDE_ROOT, E.INPUT_INVALID)
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_d1_a_hard_link_to_a_file_outside_is_never_read_and_never_written_through(home, outside, links) -> None:
    # a case containment alone never sees: a hard link resolves to its own name inside the workspace
    ws = _task(home)
    if not links.hard(ws.root / "looks-local.txt", outside / "secret.txt"):
        pytest.skip("hard links cannot be made here")
    assert ws.contained("looks-local.txt") == ws.root / "looks-local.txt"
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.read("looks-local.txt")
    assert refused.value.code == E.NOT_A_FILE
    assert ops.read("t1", "looks-local.txt").code == E.NOT_A_FILE
    assert ws.write("looks-local.txt", "replaced", dry_run=False)["written"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"     # the other name kept its own bytes
    assert ws.read("looks-local.txt") == b"replaced"


def test_d1_a_folder_swapped_for_a_link_after_the_check_is_caught_when_the_file_is_opened(home, outside, links,
                                                                                         monkeypatch) -> None:
    # WORKSPACE_MANAGER-04: the check and the write are one step; the folder is resolved again once the file is open
    ws = _task(home, files={"sub/a.txt": "a"})
    real_mkstemp = tempfile.mkstemp

    def swap_then_make(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        (ws.root / "sub").rename(ws.root / "sub-before")
        if not links.folder(ws.root / "sub", outside):
            pytest.skip("no link can be made here")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(_files.tempfile, "mkstemp", swap_then_make)
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.write("sub/b.txt", "through the link", dry_run=False)
    assert refused.value.code == E.OUTSIDE_ROOT
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]   # the partial file was removed again


def test_paths_inside_are_allowed_and_the_check_is_the_shared_one(home) -> None:
    ws = _task(home)
    assert ws.contained("a/../b.txt") == ws.root / "b.txt"
    assert ws.contained(str(ws.root / "deep" / "c.txt")) == ws.root / "deep" / "c.txt"
    reply = ops.contained("t1", "../x.txt")
    assert reply.completion_state == "qualified-draft" and reply.inside is False and "outside" in reply.reason
    assert ops.contained("t1", "a/b.txt").path == "a/b.txt"


def test_g6s_own_bookkeeping_folder_is_never_a_callers_path(home) -> None:
    ws = _task(home)
    for raw in (".g6-workspace/workspace.json", ".G6-WORKSPACE/x", "a/../.g6-workspace/backups/x.tar.gz"):
        with pytest.raises(E.WorkspaceRefused) as refused:
            ws.write(raw, "x", dry_run=False)
        assert refused.value.code == E.RESERVED, raw


# --- the P1 findings that survive the port ----------------------------------------------------------------------------

def test_p1_each_task_has_its_own_folder_under_the_g6_home_and_the_write_itself_is_contained(home) -> None:
    # WORKSPACE_MANAGER-01: the archive's "sandbox folder" was a directory in the shared temp folder, and containment
    # was a separate check a caller could ignore (adapt_library_synthesizer discarded its Result)
    one, two = Workspace.for_task("t1"), Workspace.for_task("t2")
    assert one.root == state_path("workspaces", "t1") and two.root == state_path("workspaces", "t2")
    assert home.resolve() in one.root.parents
    with pytest.raises(E.WorkspaceRefused):
        one.write("../t2/notes.txt", "from t1", dry_run=False)
    assert not (two.root / "notes.txt").exists()
    for bad in ("", "..", "a/b", "a\\b", "CON", "x" * 101, ".hidden", "t1."):
        with pytest.raises(E.WorkspaceRefused) as refused:
            Workspace.for_task(bad)
        assert refused.value.code == E.INPUT_INVALID, bad


def test_p1_a_write_and_a_backup_report_what_was_observed_and_are_never_verified(home) -> None:
    # WORKSPACE_MANAGER-02: the archive returned verified for a created folder and for a backup
    written = ops.write("t1", "notes.txt", "hello", dry_run=False)
    assert written.completion_state == "qualified-draft" and written.warning_card["code"] == E.WRITTEN
    assert written.written is True and written.sha256 == _sha("hello") and written.read_back == _sha("hello")
    backed = ops.backup("t1", dry_run=False)
    assert backed.completion_state == "qualified-draft" and backed.warning_card["code"] == E.BACKED_UP
    assert backed.members == 1 and len(backed.sha256) == 64
    replies = [written, backed, ops.status("t1"), ops.files("t1"), ops.read("t1", "notes.txt")]
    assert all(r.completion_state != "verified" and r.warning_card["verified"] is False for r in replies)


@pytest.mark.parametrize("claim", [{"confirmed": True}, {"has_git": True}, {"approved": True},
                                   {"reviewer": "the owner"}, {"base_dir": "C:/"}, {"root": "/"}])
def test_p1_a_callers_confirmation_or_setting_opens_nothing(home, claim: dict) -> None:
    # WORKSPACE_MANAGER-03: confirmed=True lifted the review gate in front of rmtree, and has_git was the caller's word
    ws = _task(home)
    for op in ("cleanup", "restore", "write"):
        reply = _invoke(op, ALLOW, task_id="t1", path="notes.txt", content="x", backup="b.tar.gz", dry_run=False,
                        **claim)
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"], (op, claim)
    assert ws.read("notes.txt") == b"first"


def test_p1_whether_a_workspace_has_git_is_observed_not_asked(home) -> None:
    ws = _task(home)
    assert ops.status("t1").git is False
    (ws.root / ".git").mkdir()
    assert ops.status("t1").git is True


# --- G4 and G5: the sensitive ops change nothing until asked, and an error tightens ---------------------------------

def test_g5_the_sensitive_ops_are_refused_over_mcp_and_on_an_untrusted_surface(home) -> None:
    ws = _task(home)
    for op in SENSITIVE:
        reply = _invoke(op, task_id="t1", path="notes.txt", content="x", backup="b.tar.gz", dry_run=False)
        assert not reply["ok"] and "G6_E_SENSITIVE_COMPONENT_DENIED" in reply["error"], op
        blocked = get_registry().invoke("workspace_manager", op, {"task_id": "t1"}, block_sensitive=True)
        assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(blocked.failure()), op
    assert ws.read("notes.txt") == b"first" and ws.root.is_dir() and ops.status("t1").backups == []


def test_g5_each_sensitive_op_is_a_dry_run_unless_asked(home) -> None:
    ws = _task(home)
    write = _invoke("write", ALLOW, task_id="t1", path="notes.txt", content="second")
    assert write["ok"] and write["result"]["written"] is False and write["result"]["replaces"] == _sha("first")
    assert write["warning_card"]["code"] == E.DRY_RUN
    backup = _invoke("backup", ALLOW, task_id="t1")
    assert backup["ok"] and backup["result"]["members"] == 1 and ops.status("t1").backups == []
    name = ops.backup("t1", dry_run=False).backup
    restore = _invoke("restore", ALLOW, task_id="t1", backup=name)
    assert restore["ok"] and restore["result"]["restored"] is False
    cleanup = _invoke("cleanup", ALLOW, task_id="t1")
    assert cleanup["ok"] and cleanup["result"]["removed"] is False and cleanup["result"]["files"] == 1
    assert ws.read("notes.txt") == b"first" and ws.root.is_dir()


def test_backup_and_restore_round_trip_and_a_restore_keeps_what_it_replaced(home) -> None:
    ws = _task(home, files={"notes.txt": "first", "sub/data.csv": "a,b\n1,2\n"})
    name = ops.backup("t1", dry_run=False).backup
    ws.write("notes.txt", "changed", dry_run=False)
    ws.write("extra.txt", "new", dry_run=False)
    restored = ops.restore("t1", name, dry_run=False)
    assert restored.completion_state == "qualified-draft" and restored.restored is True
    assert {f["path"]: f["sha256"] for f in ops.files("t1").files} == {"notes.txt": _sha("first"),
                                                                       "sub/data.csv": _sha("a,b\n1,2\n")}
    assert restored.previous_backup in [b["backup"] for b in ops.status("t1").backups]
    undo = ops.restore("t1", restored.previous_backup, dry_run=False)          # the restore can itself be undone
    assert undo.restored and ws.read("extra.txt") == b"new" and ws.read("notes.txt") == b"changed"


def _archive(ws: Workspace, name: str, members: list[tarfile.TarInfo], data: bytes = b"x") -> None:
    folder = ws.root / ".g6-workspace" / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    with tarfile.open(folder / name, "w:gz") as tar:
        for member in members:
            if member.isreg():
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            else:
                tar.addfile(member)


def _member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    return info


@pytest.mark.parametrize("members", [
    [_member("../escaped.txt")],
    [_member("ok.txt"), _member("sub/../../escaped.txt")],
    [_member("/tmp/escaped.txt")],
    [_member("door", tarfile.SYMTYPE, "../../.."), _member("door/escaped.txt")],
    [_member("hard", tarfile.LNKTYPE, "../../outside/secret.txt")],
    [_member("dev", tarfile.CHRTYPE)],
])
def test_g4_an_archive_that_would_write_outside_or_make_a_device_is_refused_whole(home, outside, members) -> None:
    ws = _task(home)
    _archive(ws, "crafted.tar.gz", members)
    before = {f["path"]: f["sha256"] for f in ops.files("t1").files}
    for dry_run in (True, False):
        result = ops.restore("t1", "crafted.tar.gz", dry_run=dry_run)
        assert result.completion_state == "blocked-escalated" and result.code == E.ARCHIVE_REFUSED, result.reason
    assert {f["path"]: f["sha256"] for f in ops.files("t1").files} == before
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    assert not any(p.name.startswith("staging") for p in (ws.root / ".g6-workspace").iterdir())


def test_g4_a_restore_takes_only_a_backup_kept_in_the_workspace(home, outside, links) -> None:
    ws = _task(home)
    for name in ("../../outside/secret.txt", "C:/x.tar.gz", "missing.tar.gz", "notes.txt"):
        assert ops.restore("t1", name).code in (E.INPUT_INVALID, E.NOT_FOUND), name
    folder = ws.root / ".g6-workspace" / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    with tarfile.open(outside / "elsewhere.tar.gz", "w:gz") as tar:
        tar.addfile(_member("planted.txt"), io.BytesIO(b""))
    if links.hard(folder / "linked.tar.gz", outside / "elsewhere.tar.gz"):
        assert ops.restore("t1", "linked.tar.gz", dry_run=False).code == E.NOT_A_FILE
    assert not (ws.root / "planted.txt").exists()


def test_g4_cleanup_removes_only_a_folder_g6_made_a_workspace_and_never_follows_a_link(home, tmp_path, outside,
                                                                                      links) -> None:
    plain = tmp_path / "someone-elses"
    plain.mkdir()
    (plain / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(E.WorkspaceRefused) as refused:
        Workspace(plain).cleanup(dry_run=False)
    assert refused.value.code == E.NOT_A_WORKSPACE and (plain / "keep.txt").exists()
    ws = _task(home)
    links.folder(ws.root / "door", outside)
    gone = ops.cleanup("t1", dry_run=False)
    assert gone.completion_state == "qualified-draft" and gone.removed is True and not ws.root.exists()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"
    assert ops.cleanup("t1").code == E.NOT_FOUND


def test_g4_a_full_workspace_refuses_a_write_and_keeps_what_it_has(home) -> None:
    ws = Workspace(state_path("workspaces", "small"), max_bytes=10)
    assert ws.write("a.txt", "12345", dry_run=False)["written"]
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.write("b.txt", "123456", dry_run=False)
    assert refused.value.code == E.RESOURCE_LIMIT and not (ws.root / "b.txt").exists()


def test_a_read_says_what_it_read_and_cuts_long_text(home) -> None:
    _task(home, files={"long.txt": "x" * 50, "data.bin": "ok"})
    (Workspace.for_task("t1").root / "raw.bin").write_bytes(b"\xff\xfe\x00")
    read = ops.read("t1", "long.txt", max_chars=10)
    assert read.text == "x" * 10 and read.truncated is True and read.sha256 == _sha("x" * 50)
    assert ops.read("t1", "raw.bin").code == E.NOT_TEXT
    assert ops.read("t1", "missing.txt").code == E.NOT_FOUND


def test_backups_skip_links_and_say_so(home, outside, links) -> None:
    ws = _task(home)
    made = links.folder(ws.root / "door", outside) | links.hard(ws.root / "twin.txt", outside / "secret.txt")
    if not made:
        pytest.skip("no link can be made here")
    plan = ops.backup("t1")
    assert plan.members == 1 and plan.skipped and all(s["reason"] for s in plan.skipped)
    with tarfile.open(ws.root / ".g6-workspace" / "backups" / ops.backup("t1", dry_run=False).backup) as tar:
        assert tar.getnames() == ["notes.txt"]


# --- the member through its doors -------------------------------------------------------------------------------------

def test_it_is_a_member_of_the_workspace_family_registered_under_its_own_name() -> None:
    ref = get_registry().refs["workspace_manager"]
    assert (ref.module, ref.family) == ("g6.workspace.workspace_manager", "workspace")


def test_its_declarations_are_registered_failure_modes() -> None:
    assert list(workspace_manager.__g6_sensitive_ops__) == SENSITIVE
    assert workspace_manager.__g6_mitigates__ == ("write_outside_workspace",)
    codes = workspace_manager.__g6_mitigates__ + workspace_manager.__g6_introduces__
    assert all(FailureModeRegistry.is_registered(c) for c in codes)
    assert workspace_manager.__g6_outside__ == () and workspace_manager.__g6_extras__ == {}
    assert "g6.workspace.workspace_manager" not in FailureModeRegistry.planned()


def test_an_agent_finds_it_by_the_failure_it_guards_against() -> None:
    assert discovery.find("an agent writing files outside its workspace")[0]["component"] == "workspace_manager"


def test_describe_lists_its_ops_and_its_skill() -> None:
    info = describe_reply("workspace_manager")
    assert info["ops"] == OPS and sorted(info["sensitive_ops"]) == sorted(SENSITIVE)
    assert info["skill"].startswith("---\nname: g6-workspace-manager")


def test_every_non_sensitive_op_round_trips_over_mcp(home) -> None:
    _task(home)
    replies = {"contained": _invoke("contained", task_id="t1", path="notes.txt"),
               "read": _invoke("read", task_id="t1", path="notes.txt"),
               "files": _invoke("files", task_id="t1"), "status": _invoke("status", task_id="t1")}
    for op, reply in replies.items():
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", (op, reply)
        assert reply["result"]["op"] == op and reply["warning_card"]["code"].startswith("G6_E_WORKSPACE_")
    assert replies["read"]["result"]["text"] == "first"
    dumped = json.dumps(replies)                         # no reply names a folder on the person's machine
    assert home.name not in dumped and home.parent.name not in dumped


def test_the_typed_input_refuses_unknown_ops_and_fields_and_says_what_is_missing() -> None:
    assert "G6_E_INPUT_VALIDATION" in _invoke("delete_everything")["error"]
    assert "G6_E_INPUT_VALIDATION" in _invoke("read", task_id="t1", path="a", folder="C:/")["error"]
    missing = _invoke("read", task_id="t1")
    assert missing["ok"] and missing["result"]["code"] == E.INPUT_INVALID
    listed = get_registry().invoke("workspace_manager", "ops").unwrap()
    assert listed["ops"] == OPS and listed["sensitive"] == SENSITIVE
    assert len(get_registry().invoke("workspace_manager", "help").unwrap()["help"]) == len(OPS)
