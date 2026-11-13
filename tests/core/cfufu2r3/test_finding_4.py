"""A runner-liveness read must not bypass the stop's durable-state receipt."""
from pathlib import Path
from uuid import uuid4

from g6.mcp import improve
from g6.self_maintenance import envelope as e


def test_runner_lock_metadata_failure_after_stop_writes_still_returns_a_receipt(monkeypatch):
    root = Path.cwd() / ".review" / "data" / uuid4().hex[:10]
    root.mkdir(parents=True)
    (root / "settings.json").write_text("{}", encoding="utf-8")
    (root / e.RUNNER_LOCK).mkdir()
    (root / e.RUNNER_LOCK / "mutex").write_bytes(b"")
    original = Path.is_file
    hit = []

    def unreadable(path):
        if path == root / e.RUNNER_LOCK / "mutex":
            hit.append("runner lock metadata")
            raise PermissionError("runner lock cannot be inspected")
        return original(path)

    monkeypatch.setattr(Path, "is_file", unreadable)
    try:
        reply = improve.stop(str(root), "please stop")
    except OSError as exc:
        reply = {"raised": f"{type(exc).__name__}: {exc}"}
    assert hit
    assert (root / e.STOP_FILE).read_bytes()
    assert e.canonical_stops(root)
    assert reply.get("code") == improve.STOP_ASKED, (
        f"stop is durably recorded, but its liveness read bypassed the receipt: {reply}"
    )
    assert reply["recorded"]["the stop"] is True
    assert reply["recorded"]["its line in the STOP file"] is True
