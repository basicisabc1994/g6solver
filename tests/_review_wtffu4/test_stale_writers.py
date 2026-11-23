import contextvars
import json

from g6.harness import completion_loop as loop


def test_expired_run_context_cannot_erase_newer_reviews(tmp_path):
    session = tmp_path / "run"
    first = {"attempted": 1, "reviews": [{"patch": "first"}]}
    with loop._writing(session), loop._run_count(session, first, fresh=False):
        loop._save(session, first, sources=())
        stale = json.loads(json.dumps(first))
        copied = contextvars.copy_context()
    newer = {"attempted": 2, "reviews": [{"patch": "first"}, {"patch": "newer"}]}
    with loop._writing(session), loop._run_count(session, newer, fresh=False):
        loop._save(session, newer, sources=())
    copied.run(loop._save, session, stale, sources=())
    retained = loop._load(session)
    assert retained["attempted"] == 2
    assert retained["reviews"] == newer["reviews"], retained


def test_stale_standalone_write_cannot_replace_newer_debt(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    session = loop.state_path(*loop.OWING).parent / "run"
    old_row = json.dumps({"session": session.name, "cause": "old-cause"})
    new_row = json.dumps({"session": session.name, "cause": "cloud_consent_missing"})
    stale = {"attempted": 1, "row_due": old_row, "row": "old retry reason"}
    loop._save(session, stale, sources=())
    current = {"attempted": 2, "row_due": new_row, "row": "new retry reason"}
    with loop._writing(session):
        loop._save(session, current, sources=())
    mark = loop.state_path(*loop.OWING, session.name)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch()
    loop._save(session, stale, sources=())
    retained = loop._load(session)
    loop.settle()
    rows = [json.loads(line) for line in loop.state_path(*loop.FAILURES).read_text().splitlines()]
    assert (retained["row_due"], retained["row"], rows) == (
        new_row, "new retry reason", [json.loads(new_row)]
    )
