"""The no-confirmation MCP path must preserve the base reply and persisted fields."""
import json
from g6.mcp import behaviour, review_reply
from .helpers import TASK, repo


def test_no_question_mcp_review_does_not_add_persisted_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    root = repo(tmp_path)
    base_reply = {"ok": True, "assessment": {"outcome": "insufficient_evidence"}}
    assert behaviour.enforced(str(root), TASK) is None
    reply = behaviour.reviewed(lambda *a, **k: base_reply, str(root), TASK)
    path = review_reply.save(reply, "a" * 32)
    saved = json.loads(open(path, encoding="utf-8").read())
    assert set(saved) == set(base_reply), "unconfirmed MCP review adds persisted task_identity"
