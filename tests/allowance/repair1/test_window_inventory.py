"""Every foreign-label digest and normalisation entry retains valid siblings."""
from datetime import UTC, datetime
import json

import pytest

from g6._allowance.readers import capture_block, claude_reading, codex_reading, instant

NOW = datetime(2026, 11, 8, tzinfo=UTC)
CODEX = {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 2000000000}
CLAUDE = {"used_percentage": 100, "resets_at": 2000000000}


@pytest.mark.parametrize("label", ["\ud800", "\udfff", "\x00", "x" * 10000, 42, None, ("tuple",)])
@pytest.mark.parametrize("location", ["claude", "codex-window", "codex-set"])
def test_foreign_label_inventory_preserves_full_sibling(label, location):
    if location == "claude":
        r = claude_reading({"rate_limits": {"five_hour": CLAUDE, label: None}}, "p", NOW, version="2.1.319")
        good_id = "five_hour"
    else:
        payload = {"rateLimits": {"primary": CODEX}}
        if location == "codex-window":
            payload["rateLimits"][label] = None
        else:
            payload["rateLimitsByLimitId"] = {label: {"primary": None}}
        r = codex_reading(payload, "p", NOW)
        good_id = "default.primary"
    assert len(r.windows) == 3
    assert next(w for w in r.windows if w.identity == good_id).quota_blocked is True
    assert r.windows[-1].failure
    assert all(w.identity.isascii() and len(w.identity) <= 160 for w in r.windows)


@pytest.mark.parametrize("bad", ["\ud800", "\x00", "x" * 10000, [], {}, True, None, 10**1000])
def test_decode_reset_and_capture_inventory_is_total(bad):
    payload = json.loads(json.dumps({"rate_limits": {"five_hour": CLAUDE, "seven_day": {
        "used_percentage": bad, "resets_at": bad}}}))
    r = claude_reading(payload, "p", NOW, version="2.1.319")
    assert r.windows[0].quota_blocked is True and not r.windows[0].failure
    assert r.windows[1].failure and r.windows[1].last_good_at is None
    assert instant(bad) is None
    assert capture_block(payload) == {"five_hour": CLAUDE}


def test_bad_semantics_value_is_a_window_failure_not_a_provider_failure():
    r = codex_reading({"rateLimits": {"primary": CODEX, "secondary": CODEX}}, "p", NOW,
                      semantics={"default.primary": [], "default.secondary": "fixed"})
    assert r.windows[0].failure and r.windows[0].quota_blocked is True
    assert r.windows[1].quota_blocked is True and not r.windows[1].failure


def test_one_normalisation_exception_remains_local_to_its_window(monkeypatch):
    from g6._allowance import readers
    original = readers.instant
    def failing_reset(value):
        if value == "synthetic-reset-failure":
            raise ValueError("synthetic failure")
        return original(value)
    monkeypatch.setattr(readers, "instant", failing_reset)
    bad = {**CODEX, "resetsAt": "synthetic-reset-failure"}
    r = codex_reading({"rateLimits": {"primary": bad, "secondary": CODEX}}, "p", NOW)
    assert r.windows[0].failure and r.windows[0].last_good_at is None and r.windows[0].quota_blocked is True
    assert r.windows[1].quota_blocked is True and not r.windows[1].failure


@pytest.mark.parametrize("kind", ["integer-string", "non-reflexive-floats", "unrenderable-integers"])
@pytest.mark.parametrize("location", ["claude", "codex-window", "codex-set"])
def test_malformed_label_collisions_never_discard_siblings(tmp_path, kind, location):
    from g6._allowance.store import Store
    if kind == "integer-string":
        labels = [42, "42", "int:42"]
    elif kind == "non-reflexive-floats":
        labels = [float("nan"), float("nan")]
    else:
        labels = [10**5000, 10**5000 + 1]
    if location == "claude":
        r = claude_reading({"rate_limits": {"five_hour": CLAUDE, **dict.fromkeys(labels, CLAUDE)}},
                           "p", NOW, version="2.1.319")
        identity = "five_hour"
    else:
        payload = {"rateLimits": {"primary": CODEX}}
        if location == "codex-window":
            payload["rateLimits"].update(dict.fromkeys(labels, CODEX))
        else:
            payload["rateLimitsByLimitId"] = dict.fromkeys(labels, {"primary": CODEX})
        r = codex_reading(payload, "p", NOW)
        identity = "default.primary"
    assert len(r.windows) == 2 + len(labels)
    assert len({w.identity for w in r.windows}) == len(r.windows)
    assert next(w for w in r.windows if w.identity == identity).quota_blocked is True
    s = Store(tmp_path)
    s.save(r)
    assert len(s.load(r.provider, "p", NOW).windows) == len(r.windows)


def test_hash_inventory_assigns_each_site_an_input_contract():
    import ast
    import inspect
    from g6._allowance import hook, readers, store
    sites = []
    for module in (hook, readers, store):
        for function in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(function, ast.FunctionDef):
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sha256":
                        sites.append((module.__name__.rsplit(".", 1)[-1], function.name))
    assert sorted(sites) == [("hook", "context_note"), ("readers", "_label_digest"), ("store", "capture_path")]
    # Foreign labels: all three adapter locations above. Profile: strict tokens.
    # Hook text: only fixed recommendations can enter its digest.
    from g6.allowance import profile_token
    for value in ("\ud800", "\x00", "x" * 10000, [], None):
        with pytest.raises(ValueError):
            profile_token(value)
