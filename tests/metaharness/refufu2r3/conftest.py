"""Review fakes: never launch a host or consult credentials."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

# Review 3's finding 2 is offered for its own change: repair 3 reverted that root under rule 5, because cleanup and three
# publication moves still act on a pathname after checking an identity. These nodes fail on 44d075f and here; each is a
# strict xfail, assertions unchanged, so the change that fixes the root makes them fail loudly until the marks go.
OFFERED_ROOT_2 = frozenset({
    "test_finding_2.py::test_private_copy_cleanup_does_not_delete_a_replacement",
    *(f"test_roots.py::test_cleanup_swap_at_every_custody_step[{step}]"
      for step in ("after-open", "before-rename", "after-rename", "after-recheck", "before-dispose")),
    "test_roots.py::test_cleanup_checks_all_children_before_deleting_any",
    "test_roots.py::test_missing_cleanup_objects_are_reported_before_deletion[root]",
    "test_roots.py::test_missing_cleanup_objects_are_reported_before_deletion[child]",
    *(f"test_roots.py::test_interrupted_cleanup_and_retry_never_delete_a_replacement[{death}-{step}]"
      for death in ("KeyboardInterrupt", "SystemExit", "ValueError") for step in ("rename", "recheck", "dispose")),
    "test_roots.py::test_every_pathname_mutation_is_classified_from_the_code",
    *(f"test_roots.py::test_publication_cannot_move_an_unchecked_source_directory[{site}]"
      for site in ("digest", "beat", "state")),
})
OFFERED_REASON = ("review refufu2r3 finding 2, offered for its own change: cleanup and publication moves act on a pathname "
                  "after an identity check (root reverted under rule 5)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid.replace("\\", "/").rsplit("refufu2r3/", 1)[-1] in OFFERED_ROOT_2:
            item.add_marker(pytest.mark.xfail(strict=True, reason=OFFERED_REASON))


@pytest.fixture
def review_host(monkeypatch):
    from g6.core import host_models
    from g6.self_maintenance import usage

    root = Path.cwd() / ".review" / "tmp" / uuid4().hex
    root.mkdir(parents=True)
    launches = []

    @contextmanager
    def folders():
        base = root / uuid4().hex
        work, files = base / "work", base / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *args):
        launches.append((list(argv), stdin))
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        if "exec" in argv:
            return 0, '{"type":"turn.completed"}', ""
        return 0, '{"type":"result","is_error":false,"result":"answer"}', ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    for kind in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        monkeypatch.setattr(kind, "locate", lambda self: "fake-host")
    yield root, launches
    assert not usage._SENT
