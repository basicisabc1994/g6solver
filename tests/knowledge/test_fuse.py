# SPDX-License-Identifier: Apache-2.0
"""G6's fusion: keyed on the canonical passage id, by rank and never by score, every contribution and every backend
state kept, near-duplicates that disagree kept as two cited items, a lexical fallback counted once, and a refusal
rather than a silent truncation."""
from __future__ import annotations

import pytest

from g6._context_engine._envelope import BackendReport, Checked, PassageItem
from g6._context_engine._fuse import MAX_CANDIDATES, OVER_BUDGET, RRF_K, TOO_MANY, fuse, tokens_of
from g6.knowledge._index import Hit
from g6.knowledge._types import Document, Source
from g6._context_engine._view import view_of


def passage(text: str, name: str = "a"):
    source = Source.of(f"g6-knowledge://src-{'0' * 16}/{name}.md", text.encode(), retrieved_at="2026-11-11T12:00:00Z")
    return Document(text=text, source=source).passage(0, len(text))


def hits(passages, scale="fts5_bm25", scores=None):
    return [Hit(passage=p, score=(scores[n] if scores else 10.0 - n), score_scale=scale, rank=n + 1)
            for n, p in enumerate(passages)]


def report(backend, state="answered", route="exact", rung=""):
    return BackendReport(backend=backend, route=route, state=state, rung=rung)


def render(hit, rank, contributions):
    return PassageItem(passage_id=hit.passage.id, source="src-" + "0" * 16, path="a.md", start=hit.passage.start,
                       end=hit.passage.end, view=view_of(hit.passage.text), rank=rank, contributions=contributions,
                       checked=Checked(outcome="intact", reread_sha256="0" * 64, at="2026-11-11T12:00:00Z"))


A, B, C = passage("alpha one", "a"), passage("beta two", "b"), passage("gamma three", "c")


def test_fusion_keys_on_passage_id_and_keeps_every_contribution():
    fused = fuse([(report("sqlite"), hits([A, B]), 3), (report("elastic_managed"), hits([B, C], "es_bm25"), 3)],
                 top_k=10, render=render, query="q")
    assert fused.ranked[0] == B.id and set(fused.ranked) == {A.id, B.id, C.id}
    assert len(fused.contributions) == 4
    top = fused.items[0]
    assert top.passage_id == B.id and {(c.backend, c.rank, c.score_scale) for c in top.contributions} == {
        ("sqlite", 2, "fts5_bm25"), ("elastic_managed", 1, "es_bm25")}
    assert all(c.generation == 3 and c.query == "q" and c.passage_id for c in fused.contributions)
    assert [i.rank for i in fused.items] == [1, 2, 3]


def test_ranks_not_scores_decide():
    one = fuse([(report("sqlite"), hits([A, B], scores=[9.0, 8.0]), 1),
                (report("elastic_managed"), hits([B, A], "es_bm25", scores=[0.002, 0.001]), 1)], top_k=5)
    other = fuse([(report("sqlite"), hits([A, B], scores=[0.1, 99999.0]), 1),
                  (report("elastic_managed"), hits([B, A], "es_bm25", scores=[500.0, 4000.0]), 1)], top_k=5)
    assert one.ranked == other.ranked


def test_ties_break_by_passage_id():
    fused = fuse([(report("sqlite"), hits([A]), 1), (report("elastic_managed"), hits([B], "es_bm25"), 1)], top_k=5)
    assert list(fused.ranked) == sorted([A.id, B.id])
    again = fuse([(report("elastic_managed"), hits([B], "es_bm25"), 1), (report("sqlite"), hits([A]), 1)], top_k=5)
    assert again.ranked == fused.ranked


def test_near_duplicates_that_disagree_stay_two_items_with_two_citations():
    must = passage("An empty value must return zero for every caller in the billing path, version 2.", "old")
    must_not = passage("An empty value must not return zero for every caller in the billing path, version 3.", "new")
    fused = fuse([(report("sqlite"), hits([must, must_not, C]), 1)], top_k=10, render=render)
    assert {i.passage_id for i in fused.items} == {must.id, must_not.id, C.id}
    assert fused.groups == ((must.id, must_not.id),)
    kinds = {c["kind"]: c for c in fused.cautions}
    assert set(kinds) == {"negation", "number"} and set(kinds["negation"]["passages"]) == {must.id, must_not.id}
    same = passage("An empty value must return zero for every caller in the billing path, version 2!", "copy")
    alike = fuse([(report("sqlite"), hits([must, same]), 1)], top_k=10, render=render)
    assert len(alike.items) == 2 and alike.groups == ((must.id, same.id),) and alike.cautions == ()


def test_bm25_fallback_is_counted_once():
    lexical = (report("sqlite"), hits([A, B]), 1)
    fallback = (report("colbert_persistent", route="meaning", rung="bm25"), hits([B, A], "bm25"), 1)
    semantic = (report("colbert_persistent", route="meaning", rung="colbert"), hits([B, A], "colbert_maxsim"), 1)
    with_fallback = fuse([lexical, fallback], top_k=5)
    assert with_fallback.ranked == fuse([lexical], top_k=5).ranked == (A.id, B.id), "the fallback added no vote"
    assert len(with_fallback.contributions) == 4
    assert {c.reason for c in with_fallback.contributions if c.backend == "colbert_persistent"} == {
        "bm25 fallback: lexical, counted once with the lexical backend"}
    alone = fuse([fallback], top_k=5)
    assert alone.ranked == (B.id, A.id) and "not semantic" in alone.contributions[0].reason
    assert fuse([lexical, semantic], top_k=5).ranked == tuple(sorted([A.id, B.id])), "a semantic vote counts: the two now tie"
    assert fuse([lexical, semantic], top_k=5).contributions[2].reason == "meaning match"


def test_unavailable_backend_is_reported_and_is_not_absence():
    down = (BackendReport(backend="elastic_managed", route="exact", state="unavailable", reason="no server"), [], 1)
    fused = fuse([(report("sqlite"), hits([A]), 1), down], top_k=5, render=render)
    assert [i.passage_id for i in fused.items] == [A.id]
    assert {b.backend: b.state for b in fused.backends} == {"sqlite": "answered", "elastic_managed": "unavailable"}
    assert fuse([down], top_k=5).backends[0].reason == "no server"


def test_partial_and_failed_states_survive_fusion():
    states = ["partial", "failed", "empty", "unsupported", "refused"]
    results = [(BackendReport(backend=f"b{n}", route="exact", state=s, reason=s), hits([A]) if s == "partial" else [], 1)
               for n, s in enumerate(states)]
    fused = fuse(results, top_k=5)
    assert [b.state for b in fused.backends] == states and [b.reason for b in fused.backends] == states
    assert fused.ranked == (A.id,)


def test_over_2000_candidates_is_refused():
    many = [passage(f"passage number {n}", f"p{n}") for n in range(MAX_CANDIDATES // 2 + 1)]
    fused = fuse([(report("sqlite"), hits(many), 1), (report("elastic_managed"), hits(many, "es_bm25"), 1)],
                 top_k=5, render=render)
    assert fused.refused.startswith(f"[{TOO_MANY}]") and fused.items == () and fused.ranked == ()
    assert len(fused.backends) == 2
    assert fuse([(report("sqlite"), hits(many[:1000]), 1), (report("e"), hits(many[:1000]), 1)], top_k=5).refused == ""


def test_budget_counts_all_rendered_fields():
    one = render(hits([A])[0], 1, ())
    cost = tokens_of(one)
    assert cost > len(A.text) // 4 + 20, "provenance and the rendered fields are counted, not only the text"
    results = [(report("sqlite"), hits([A, B, C]), 1)]
    wide = fuse(results, top_k=5, budget_tokens=10_000, render=render)
    costs = [tokens_of(i) for i in wide.items]
    assert len(costs) == 3 and wide.tokens == sum(costs) and all(c > cost for c in costs), "contributions are counted too"
    budget = costs[0] + costs[1] + costs[2] // 2
    two = fuse(results, top_k=5, budget_tokens=budget, render=render)
    assert len(two.items) == 2 and two.omitted == 1 and two.tokens == costs[0] + costs[1] <= budget and two.refused == ""
    none = fuse(results, top_k=5, budget_tokens=cost // 2, render=render)
    assert none.items == () and none.refused.startswith(f"[{OVER_BUDGET}]") and none.omitted == 3
    for bad in (-1, 1.5, "100"):
        assert fuse(results, top_k=5, budget_tokens=bad, render=render).refused.startswith(f"[{OVER_BUDGET}]")


def test_fused_items_carry_views_only():
    secret = passage('deploy with api_key = "sk-PLANTEDSECRET0123456789abcdef" today', "s")
    fused = fuse([(report("sqlite"), hits([secret]), 1)], top_k=5, render=render)
    dumped = fused.model_dump_json()
    assert "PLANTEDSECRET" not in dumped and "REDACTED" in dumped
    assert fuse([(report("sqlite"), hits([secret]), 1)], top_k=5).items == (), "without a renderer nothing is rendered"
    dropped = fuse([(report("sqlite"), hits([A, B]), 1)], top_k=5, render=lambda h, r, c: None if h.passage.id == A.id
                   else render(h, r, c))
    assert [i.passage_id for i in dropped.items] == [B.id] and dropped.items[0].rank == 1


def test_the_rank_constant_is_the_declared_one():
    fused = fuse([(report("sqlite"), hits([A, B]), 1)], top_k=5)
    assert RRF_K == 60 and fused.ranked == (A.id, B.id)
    with pytest.raises(Exception):
        fused.ranked = ()
