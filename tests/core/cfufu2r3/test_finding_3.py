"""An unreadable stop note must not bypass the stop's read-back receipt."""
import json
from pathlib import Path
from uuid import uuid4

from g6.mcp import improve
from g6.self_maintenance import envelope as e


def test_ledger_metadata_read_failure_after_stop_writes_still_returns_a_receipt(monkeypatch):
    root = Path.cwd() / ".review" / "data" / uuid4().hex[:10]
    root.mkdir(parents=True)
    (root / "settings.json").write_text(json.dumps({}), encoding="utf-8")
    log = e.CycleLog(root / e.CYCLE_LOG)
    log.start("Ada", stops=0)
    original = Path.is_file
    hit = []

    def unreadable(path):
        if path == root / e.CYCLE_LOG and (root / e.STOP_RECORDS).exists():
            hit.append("constraint log metadata")
            raise PermissionError("constraint log cannot be inspected after stop was written")
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
        f"stop is durably recorded, but its ledger metadata read bypassed the receipt: {reply}"
    )
    assert reply["recorded"]["its line in the STOP file"] is True
    assert reply["recorded"]["its note in the constraint log"] is None
