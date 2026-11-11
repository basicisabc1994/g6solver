# SPDX-License-Identifier: Apache-2.0
"""The search planner: fixed priorities in which every feature changes a rule, an unknown feature stays unknown, an
unavailable route stays unavailable, coverage is never claimed beyond what was returned, limits are fixed before the
run, a model's reply becomes typed search fields only, and the decision record holds no text."""
from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from g6.core import host_models
from g6.core.llm import FakeLLM
from g6.knowledge import _dispatch, _planner
from g6.knowledge import _engine as engine
from g6.knowledge._planner import Features, Limits
from g6.knowledge._registry import ProjectStore
from tests.knowledge.corpus import MODULES, SECRET, SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

ONE, TWO = SUBJECTS[0], SUBJECTS[2]
CLOCK = {"clock": lambda: NOW}
READY = [(route, "") for route in _dispatch.ROUTES]
NOTHING = Features(has_identifier=False, has_path_or_error=False, is_relation_question=False, is_conceptual=False)


def only(*names: str):
    return [(route, "" if route.name in names else f"the {route.backend} backend is not selected for this project")
            for route in _dispatch.ROUTES]


def planned(rig, query, needs=(), **kw):
    return _planner.run(rig.local("plan"), query, list(needs), **CLOCK, **kw)


def log_of(rig) -> list[dict]:
    path = ProjectStore(rig.binding).folder / _planner.LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_identifier_goes_exact_first(tmp_path, monkeypatch):
    for query in ("where is parse_limit defined", "`limit` handling in the service", "parseLimit fails on empty input",
                  "retry() is slow today", "HttpClient setup for uploads"):
        found = _planner.features_of(query)
        assert found.has_identifier is True and found.is_conceptual is False, query
        assert _planner.plan(found, READY) == (("exact", "structure", "meaning"), "exact-first"), query
    assert _planner.plan(_planner.features_of("ValueError in src/ledger.py line 3"), READY) == (
        ("exact", "structure"), "exact-first"), "a path or an error text is matched literally: no meaning route"
    rig = make(tmp_path, monkeypatch)
    rig.add()
    response, event, refs = planned(rig, f"{MODULES[0]}_step", [f"{MODULES[0]}_step"])
    assert event.rule == "exact-first" and event.chosen == ("exact",) and event.actual_rung == {"exact": "sqlite"}
    assert event.stop_reason == "needs_met" and event.needs[0].state == "found"
    items = response.payload["items"]
    assert set(event.needs[0].witnesses) <= {i["passage_id"] for i in items} and len(refs) == len(items) > 0
    assert items[0]["path"] == f"src/{MODULES[0]}.py"


def test_conceptual_tries_the_available_meaning_rung():
    found = _planner.features_of("how does the team decide when a release is safe")
    assert found == Features(has_identifier=False, has_path_or_error=False, is_relation_question=False, is_conceptual=True)
    assert _planner.plan(found, READY) == (("meaning", "exact", "structure"), "meaning-first")
    chosen, rule = _planner.plan(found, only("exact", "structure"))
    assert (chosen, rule) == (("exact", "structure"), "meaning-first"), "the rule stands; the route that is not ready is left out"


def test_relation_question_starts_with_witnessed_edges(tmp_path, monkeypatch):
    found = _planner.features_of(f"which files does src/{MODULES[0]}.py import")
    assert found.is_relation_question is True and found.has_path_or_error is True
    assert _planner.plan(found, READY) == (("relations", "exact", "structure"), "relation-first")
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    response, event, refs = planned(rig, f"which files does src/{MODULES[0]}.py import")
    assert event.chosen[0] == "relations" and event.rule == "relation-first" and "relations" in event.available
    first = response.payload["paths"][0]
    assert first["endpoints"] == [f"src/{MODULES[0]}.py", f"src/{MODULES[1]}.py"]
    assert first["edges"][0]["certainty"] == "possible" and "possible dependency" in first["edges"][0]["means"]
    assert first["edges"][0]["passage_id"] in {r.item_id for r in refs}, "a path's witness is cited as a source"
    assert response.authorisation_revisions == {source: 1}
    # to a provider the source is not shared with, the same question returns no path and no passage
    hidden, event, refs = _planner.run(rig.to("anthropic:claude_code", "plan"), f"which files does src/{MODULES[0]}.py import",
                                       [], **CLOCK)
    assert "paths" not in hidden.payload and hidden.payload["items"] == [] and refs == () and event.grants == {}
    assert MODULES[1] not in hidden.model_dump_json()


@pytest.mark.parametrize("feature", ["has_identifier", "has_path_or_error", "is_relation_question", "is_conceptual"])
def test_each_feature_changes_a_rule(feature):
    base = (_planner.admissible_routes(NOTHING), *_planner.plan(NOTHING, READY))
    assert base == (("exact", "meaning", "structure"), ("exact", "meaning", "structure"), "default")
    flipped = NOTHING.model_copy(update={feature: True})
    after = (_planner.admissible_routes(flipped), *_planner.plan(flipped, READY))
    assert after[0] != base[0] or after[1] != base[1], f"{feature} changes what is admissible or the order"
    assert after[2] != "default"


def test_unknown_feature_stays_unknown():
    assert _planner.features_of("") == Features() == _planner.features_of("  ?! ")
    short = _planner.features_of("limits")
    assert short == Features(has_identifier=False, has_path_or_error=False), "too short to tell: unknown, not a guess"
    assert short.is_relation_question is None and short.is_conceptual is None
    assert _planner.features_of("parse_limit") == Features(has_identifier=True, has_path_or_error=False)
    assert _planner.plan(Features(), READY) == (("exact", "meaning", "structure"), "default"), "unknown changes no rule"
    assert _planner.admissible_routes(Features()) == ("exact", "meaning", "structure")


def test_unavailable_route_stays_unavailable_and_fallback_is_recorded(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    response, event, _ = planned(rig, f"how is the {ONE} convention followed by callers", [f"{ONE} convention"])
    assert event.features.is_conceptual is True and event.rule == "meaning-first"
    assert event.chosen == ("exact",) and event.available == ("exact",), "the meaning route is not ready here"
    assert event.admissible == ("exact", "meaning", "structure")
    assert {a["route"]: a["why"].split(":")[0] for a in event.alternatives} == {"meaning": "unavailable",
                                                                                "structure": "unavailable"}
    assert all("not selected" in a["why"] for a in event.alternatives)
    assert event.expected == {"exact": _planner.EXPECTED["exact"]} and event.actual_rung == {"exact": "sqlite"}
    assert [b.route for b in response.backends] == ["exact"], "a route that is not ready is not asked and not reported as empty"
    # nothing indexed at all: every route is unavailable and every need is unknown
    empty = make(tmp_path, monkeypatch, name="empty", build=False)
    response, event, refs = planned(empty, f"how is the {ONE} convention followed", [f"{ONE} convention"])
    assert event.stop_reason == "unavailable" and event.chosen == () and event.generation is None
    assert [n.state for n in event.needs] == ["unknown"] and response.payload["items"] == [] and refs == ()


def test_bm25_not_counted_twice():
    def item(*reasons):
        return {"contributions": [{"reason": reason} for reason in reasons]}

    alone = [item("exact match"), item("exact match")]
    with_fallback = [item("exact match", "bm25 fallback: lexical, counted once with the lexical backend"),
                     item("exact match", "a model-proposed lead: a way to find the passage, no evidence weight")]
    assert _planner._counted(alone) == _planner._counted(with_fallback) == 2
    assert _planner._counted([item("exact match", "meaning match")]) == 2, "a second backend that ranked by meaning counts"


def test_no_new_passage_stops_the_branch_without_claiming_coverage(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    again = FakeLLM(lambda system, user: json.dumps({"query": f"{ONE} convention"}))
    response, event, _ = planned(rig, f"{ONE} convention", [f"{ONE} convention", "a rollback procedure for zeppelins"],
                                 limits=Limits(model_calls=2), reformulate=again)
    assert event.stop_reason == "no_new_passage" and len(again.calls) == 1 and event.costs["model_calls"] == 1
    assert [n.state for n in event.needs] == ["found", "missing"]
    assert "does not say the sources hold nothing more" in response.payload["coverage"]
    assert event.needs[1].witnesses == () and "unmet_needs" in again.calls[0][1] and "zeppelins" in again.calls[0][1]
    assert SECRET not in again.calls[0][1] and "must be followed" not in again.calls[0][1], "no passage text is sent"
    # with no caller the first round is the only one
    _, event, _ = planned(rig, f"{ONE} convention", ["a rollback procedure for zeppelins"])
    assert event.stop_reason == "no_new_passage" and event.costs == {**event.costs, "searches": 1, "model_calls": 0}


def test_budget_exhaustion_leaves_needs_unknown(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    response, event, refs = planned(rig, f"{ONE} convention", [f"{ONE} convention"], limits=Limits(searches=0))
    assert event.stop_reason == "budget" and event.costs["searches"] == 0
    assert [n.state for n in event.needs] == ["unknown"] and response.payload["items"] == [] and refs == ()
    # a token limit that holds one passage: the rest is counted as omitted, and a need it would have met is unknown
    whole, _, _ = planned(rig, "convention", [])
    assert len(whole.payload["items"]) > 1
    response, event, refs = planned(rig, "convention", ["a rollback procedure for zeppelins"],
                                    limits=Limits(tokens=event_tokens(whole.payload["items"][0]) + 1))
    assert event.stop_reason == "budget" and len(response.payload["items"]) == len(refs) == 1
    assert response.payload["omitted"] == len(whole.payload["items"]) - 1 and event.needs[0].state == "unknown"
    # a model-call limit that is spent ends the run as a budget stop
    again = FakeLLM(lambda system, user: json.dumps({"query": TWO}))
    _, event, _ = planned(rig, ONE, ["a rollback procedure for zeppelins"], limits=Limits(model_calls=1), reformulate=again)
    assert event.stop_reason == "budget" and len(again.calls) == 1 and event.costs["searches"] == 2
    assert event.needs[0].state == "unknown"


def event_tokens(item: dict) -> int:
    from g6.knowledge._envelope import PassageItem
    from g6.knowledge._fuse import tokens_of

    return tokens_of(PassageItem(**item))


def test_model_output_cannot_become_raw_query_json(tmp_path, monkeypatch):
    assert _planner._typed('{"query": {"bool": {"must": [{"match_all": {}}]}}}') is None
    assert _planner._typed("no json here") is None and _planner._typed('["a list"]') is None
    assert _planner._typed('{"query": "  "}') is None
    reply = json.dumps({"query": "x" * 900, "routes": ["relations", "exact", "leads", {"term": 1}], "top_k": 10 ** 6,
                        "sources": ["src-" + "0" * 16], "filters": {"registration_id": "*"}, "limits": {"searches": 99},
                        "raw": True})
    assert _planner._typed("Here you go: " + reply) == {"query": "x" * 400, "routes": ("exact",), "top_k": 20}
    rig = make(tmp_path, monkeypatch)
    rig.add()
    seen = []
    real = engine.search
    monkeypatch.setattr(engine, "search", lambda acc, request, **kw: seen.append((acc, request)) or real(acc, request, **kw))
    wide = FakeLLM(lambda system, user: json.dumps({"query": TWO, "sources": ["*"], "routes": ["structure", "meaning"],
                                                    "top_k": 500}))
    acc = rig.local("plan", sources=[])   # narrowed to no source at all
    response, event, refs = _planner.run(acc, ONE, ["zeppelins"], limits=Limits(model_calls=1), reformulate=wide, **CLOCK)
    assert response.payload["items"] == [] and refs == () and wide.calls == [], "nothing new came back: no model is asked"
    response, event, _ = planned(rig, ONE, ["zeppelins"], limits=Limits(model_calls=1), reformulate=wide, top_k=3)
    requests = [request for _, request in seen[1:]]
    assert [r.query for r in requests] == [ONE, TWO] and all(r.sources is None for r in requests)
    assert [r.routes for r in requests] == [("exact",), ("exact",)], "a route that is not ready cannot be asked for"
    assert [r.top_k for r in requests] == [3, 3], "nor a larger result count than the run was given"
    assert all(a is b for (a, _), (b, _) in zip(seen[1:], seen[2:], strict=False)), "the same access throughout"


def test_every_internal_read_rechecks_revocation(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    real = engine.search

    def then_revoke(acc, request, **kw):
        out = real(acc, request, **kw)
        engine.revoke(rig.local("revoke"), source)   # the person revokes while the planner holds the results
        return out

    monkeypatch.setattr(engine, "search", then_revoke)
    response, event, refs = planned(rig, f"{ONE} convention", [f"{ONE} convention"])
    assert response.payload["items"] == [] and refs == (), "the last check before the reply honours the revoke"
    assert event.stop_reason == "refused" and [n.state for n in event.needs] == ["unknown"]
    assert [n.witnesses for n in event.needs] == [()]
    monkeypatch.setattr(engine, "search", real)
    _, event, refs = planned(rig, f"{ONE} convention", [f"{ONE} convention"])
    assert refs == () and event.grants == {} and event.needs[0].state != "found"
    source_text = __import__("inspect").getsource(_planner.run)
    assert source_text.count("at=clock()") >= 1 and "scope_of" in source_text.split("engine.search")[0], (
        "scope is decided before the first search, and the final use check gives the time")


def test_event_records_all_fields_and_no_text(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    query, need = f"how is the {ONE} convention followed", f"{ONE} convention"
    response, event, _ = planned(rig, query, [need])
    assert set(_planner.DecisionEvent.model_fields) == {
        "needs", "features", "available", "grants", "generation", "admissible", "rule", "chosen", "alternatives",
        "expected", "limits", "actual_rung", "contributions", "costs", "stop_reason"}
    assert event.grants == {source: 1} and event.generation == 1 and event.contributions == len(response.payload["items"])
    assert set(event.costs) == {"searches", "model_calls", "seconds", "tokens"} and event.limits == Limits()
    line, = log_of(rig)
    assert set(line) == (set(_planner.DecisionEvent.model_fields) | {"at", "query_sha256"})
    raw = (ProjectStore(rig.binding).folder / _planner.LOG).read_text(encoding="utf-8")
    assert query not in raw and need not in raw and ONE not in raw, "no query text and no need text"
    assert line["needs"] == [{"id": "need-1", "state": "found", "text_sha256": line["needs"][0]["text_sha256"],
                              "witnesses": len(event.needs[0].witnesses)}]
    held = {i["passage_id"] for i in response.payload["items"]}
    assert held and not held & set(re.findall(r"[0-9a-f]{64}", raw)) - {line["query_sha256"]}, "no passage id"
    assert not any(i["view"]["text"][:40] in raw for i in response.payload["items"]), "no passage text"
    planned(rig, query, [need])
    assert len(log_of(rig)) == 2, "one line per run, appended"
    assert "not training data" in (_planner.__doc__ or "")


def test_limits_are_fixed_before_the_run(tmp_path, monkeypatch):
    limits = Limits()
    assert (limits.searches, limits.model_calls, limits.tokens, limits.seconds, limits.candidates) == (6, 0, 8_000, 20.0, 2_000)
    with pytest.raises(ValidationError):
        limits.searches = 99
    with pytest.raises(ValidationError):
        Limits(retries=3)
    rig = make(tmp_path, monkeypatch)
    rig.add()
    turn = iter(SUBJECTS[1:])
    eager = FakeLLM(lambda system, user: json.dumps({"query": next(turn), "limits": {"searches": 99, "model_calls": 99}}))
    _, event, _ = planned(rig, ONE, ["zeppelins"], limits=Limits(searches=3, model_calls=9), reformulate=eager)
    assert event.costs["searches"] == 3 and event.costs["model_calls"] == 3 and event.stop_reason == "budget"
    assert event.limits == Limits(searches=3, model_calls=9), "what the model wrote about limits is dropped"
    # reformulation is off unless the limits allow a model call, whatever caller is given
    quiet = FakeLLM(lambda system, user: json.dumps({"query": TWO}))
    _, event, _ = planned(rig, ONE, ["zeppelins"], reformulate=quiet)
    assert quiet.calls == [] and event.costs["model_calls"] == 0 and event.stop_reason == "no_new_passage"


def test_reformulation_by_a_model_the_sources_are_not_shared_with_is_refused(tmp_path, monkeypatch, accepted_cloud_dialog):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    sent: list[str] = []
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "complete",
                        lambda self, system, user, **kw: sent.append(user) or json.dumps({"query": TWO}))
    accepted_cloud_dialog("model_inputs", "anthropic:claude_code", text="the planner's question")
    run = {"limits": Limits(model_calls=1), "reformulate": host_models.ClaudeCodeCaller()}
    response, event, _ = planned(rig, ONE, ["zeppelins"], **run)
    assert sent == [] and event.stop_reason == "refused" and event.costs["model_calls"] == 0
    assert event.needs[0].state == "unknown" and response.payload["items"], "what was found locally is still returned"
    accepted_cloud_dialog(f"knowledge:{source}", "anthropic:claude_code", text="synthetic corpus")
    _, event, _ = planned(rig, ONE, ["zeppelins"], **run)
    assert len(sent) == 1 and event.costs == {**event.costs, "model_calls": 1, "searches": 2}
