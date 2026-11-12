"""One rule for a date after the present of whoever holds it, for the reader and for every writer.

No clock is compared with a bound: "the clock was ahead" is a date after the real present, or a store whose own
clock is set ahead for one write, the way a machine's would have been.
"""
import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6 import allowance
from g6._allowance import store as store_module
from g6._allowance.readers import claude_reading, codex_reading
from g6._allowance.store import AHEAD, Store
from g6.allowance import Window, ahead, report

SRC = Path(__file__).resolve().parents[3] / "src" / "g6"
RESET = 4000000000


def _claude(percent):
    return {"rate_limits": {"five_hour": {"used_percentage": percent, "resets_at": RESET}}}


def _codex(percent, at):
    return codex_reading({"ordinaryUsageAllowed": True, "rateLimits": {
        "primary": {"usedPercent": percent, "windowDurationMins": 300, "resetsAt": RESET}}}, "p", at)


def _window(reading, identity):
    return next(w for w in reading.windows if w.identity == identity)


def _bound(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    root = tmp_path / "allowances"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"profiles": {"claude": "p", "codex": "p"}}), encoding="utf-8")
    report(refresh=False)
    return root


@pytest.fixture
def clock_ahead(monkeypatch):
    """A store whose own clock reads an hour ahead for as long as the block lasts."""
    from contextlib import contextmanager

    @contextmanager
    def an_hour():
        with monkeypatch.context() as patched:
            patched.setattr(store_module, "utc_now", lambda: datetime.now(UTC) + timedelta(hours=1))
            yield datetime.now(UTC) + timedelta(hours=1)
    return an_hour


# ---- the rule, and who holds it

def test_the_rule_is_one_function_and_its_edge_is_the_present_itself():
    now = datetime.now(UTC)
    assert not ahead(now, now) and ahead(now + timedelta(microseconds=1), now) and not ahead(now - timedelta(1), now)
    window = Window("codex", "p", "w", 18000, .5, now + timedelta(hours=5), "unknown", now)
    assert window.availability(now, 120) == "available"
    assert window.availability(now - timedelta(microseconds=1), 120) == "future observation"


def test_every_comparison_of_a_held_date_is_beside_the_rule_from_the_code():
    """Where the cache or the reader weighs one date against another, the rule is used in that same function."""
    for path, expected in ((SRC / "_allowance" / "store.py", {"_save", "claim_refresh", "observe_claude"}),
                           (SRC / "allowance.py", {"availability"})):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        weighing, ruled = set(), set()
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            for node in ast.walk(function):
                if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops):
                    text = ast.unparse(node)
                    if any(word in text for word in ("attempted_at", "observed_at", "reset_at", "now", "stored", "saved", "held")):
                        weighing.add(function.name)
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "ahead":
                    ruled.add(function.name)
        assert weighing == expected, path.name
        assert weighing <= ruled, (path.name, weighing - ruled)
    load = next(node for node in ast.walk(ast.parse((SRC / "_allowance" / "store.py").read_text(encoding="utf-8")))
                if isinstance(node, ast.FunctionDef) and node.name == "load")
    assert any(isinstance(node, ast.Call) and ast.unparse(node.func) == "ahead" for node in ast.walk(load))
    assert allowance.ahead is store_module.ahead


# ---- writers

def test_a_reading_dated_after_its_writers_present_is_kept_as_a_constraint_and_is_never_a_good_one(tmp_path):
    store = Store(tmp_path)
    store.bind("codex", "p")
    now = datetime.now(UTC)
    store.save(_codex(97, now + timedelta(hours=1)))
    held = _window(store.load("codex", "p"), "default.primary")
    assert held.used_share == .97 and held.failure == AHEAD and held.last_good_at is None
    # Whatever the present of a later reader: before its date, on it, after it, and past its age.
    for later in (-3600, -1, 0, 1, 10, 120, 121, 86400):
        at = now + timedelta(hours=1, seconds=later)
        assert held.availability(at, 120) != "available", later


def test_a_stored_reading_dated_after_the_writers_present_is_replaced_whole(tmp_path, clock_ahead):
    store = Store(tmp_path)
    store.bind("codex", "p")
    with clock_ahead() as then:  # the machine's clock was an hour ahead when this was read and written
        store.save(codex_reading({"rateLimits": {"primary": {"usedPercent": 50, "windowDurationMins": 300, "resetsAt": RESET},
                                                 "secondary": {"usedPercent": 20, "windowDurationMins": 10080,
                                                               "resetsAt": RESET}}}, "p", then))
    now = datetime.now(UTC)
    store.save(_codex(99, now))
    held = store.load("codex", "p")
    assert held.attempted_at == now and _window(held, "default.primary").used_share == .99
    # Replaced whole: what only the earlier reading reported is not carried over from a date that orders nothing.
    assert _window(held, "default.secondary").used_share is None


def test_a_reader_that_finds_a_date_after_its_own_present_marks_it_for_good(tmp_path, monkeypatch, clock_ahead):
    """Nothing was observed after the clock was set back; the reading must still never be shown as a good one."""
    root = _bound(tmp_path, monkeypatch)
    with clock_ahead() as then:
        Store(root).save(_codex(50, then))
        assert _window(Store(root).load("codex", "p"), "default.primary").failure == ""  # nothing to see yet
    first = report(refresh=False)["providers"]["codex"]["windows"][0]
    assert first["availability"] == "future observation"
    raw = Store(root).load("codex", "p")
    assert _window(raw, "default.primary").failure == AHEAD and _window(raw, "default.primary").used_share == .5
    for later in (1, 10, 120, 121, 7200):  # the clock reaches the old date, and passes it
        row = report(now=then + timedelta(seconds=later), refresh=False)["providers"]["codex"]["windows"][0]
        assert row["availability"] != "available" and row["used_share"] == .5, later


def test_a_capture_dated_after_the_writers_present_is_replaced_and_one_so_dated_is_not_published(tmp_path, clock_ahead):
    store = Store(tmp_path)
    store.bind("claude", "p")
    with clock_ahead() as then:
        store.observe_claude(_claude(50), "p", then, "2.1.319")
    path = store.capture_path("p")
    assert datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["read_at"]) == then
    now = datetime.now(UTC)
    store.observe_claude(_claude(99), "p", now, "2.1.319")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(saved["read_at"]) == now and saved["rate_limits"]["five_hour"]["used_percentage"] == 99
    # An observation dated after this writer's present publishes nothing, and never relaxes what is held: the
    # stronger of the two figures stays, as a constraint, and the window is no longer a good reading.
    store.observe_claude(_claude(70), "p", now + timedelta(hours=2), "2.1.319")
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    held = _window(store.load("claude", "p"), "five_hour")
    assert held.used_share == .99 and held.failure == AHEAD


def test_a_lease_dated_after_the_writers_present_holds_nothing_and_one_of_the_present_still_holds(tmp_path):
    store = Store(tmp_path)
    store.bind("codex", "p")
    now = datetime.now(UTC)
    assert store.claim_refresh("codex", "p", now + timedelta(hours=1), 60)
    assert store.claim_refresh("codex", "p", now, 60), "a lease from a clock that was ahead held the present's refresh"
    assert not store.claim_refresh("codex", "p", now + timedelta(seconds=59), 60), "the present's own lease holds"
    assert store.claim_refresh("codex", "p", now + timedelta(seconds=60), 60)


# ---- what stays as it was

@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_an_older_observation_that_arrives_late_is_still_dropped(tmp_path, provider):
    store = Store(tmp_path)
    store.bind(provider, "p")
    now = datetime.now(UTC)

    def write(percent, at):
        if provider == "claude":
            store.observe_claude(_claude(percent), "p", at, "2.1.319")
        else:
            store.save(_codex(percent, at))

    write(70, now - timedelta(seconds=5))
    write(50, now - timedelta(seconds=10))
    identity = "five_hour" if provider == "claude" else "default.primary"
    held = _window(store.load(provider, "p"), identity)
    assert held.used_share == .7 and held.failure == ""
    if provider == "claude":
        saved = json.loads(store.capture_path("p").read_text(encoding="utf-8"))
        assert saved["rate_limits"]["five_hour"]["used_percentage"] == 70


def test_a_reading_of_the_present_is_untouched_by_readers(tmp_path):
    store = Store(tmp_path)
    store.bind("claude", "p")
    at = datetime.now(UTC) - timedelta(seconds=1)
    store.save(claude_reading(_claude(50), "p", at, version="2.1.319"))
    before = store.load("claude", "p")
    assert store.load("claude", "p") == before and _window(before, "five_hour").failure == ""
    assert _window(before, "five_hour").availability(at + timedelta(seconds=10), 120) == "available"
