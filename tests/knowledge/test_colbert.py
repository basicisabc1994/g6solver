# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.ctx_colbert: one call's passages ranked by ColBERT, else sentence embeddings, else BM25, every rung
passed over named; requires_dense refuses rather than fall back; weights only from the person's own cache.

The dense encoders here are test doubles registered from outside the package (a small table of word meanings), so
the ladder, the weights check and the late-interaction scoring run without the heavy extras; they say nothing about
how well ColBERT itself retrieves."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import g6.knowledge.ctx_colbert as package
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.grounding.retrieval import Retriever
from g6.knowledge._index import index_backends
from g6.knowledge._types import Document, Source
from g6.knowledge.ctx_colbert import ColbertIndex, CtxColbertBlock, CtxColbertInput, rank
from g6.knowledge.ctx_colbert.rungs import RUNGS, ColbertRung, EmbeddingsRung, Weights, cached, maxsim
from g6.mcp import describe_reply, invoke_reply

np = pytest.importorskip("numpy")  # the late-interaction scoring; the extras bring it, and so does g6[abstraction]

POLICY = {"refunds": "Refunds are issued within 14 days of a return.",
          "shipping": "Shipping is free over 50 euros.",
          "credit": "Store credit never expires."}
PARAPHRASE = "how do I get my money back"
#: What each word means to the test encoder: a refund and getting money back are the same idea.
MEANING = {"refunds": 0, "refund": 0, "money": 0, "back": 0, "return": 0, "issued": 1, "days": 1, "14": 1,
           "shipping": 2, "free": 2, "euros": 2, "credit": 3, "store": 3, "expires": 3, "never": 3}
SMALL = Weights("test-org/test-colbert", "MIT (a test)", "a few bytes", 64)


def _passages(texts: dict[str, str] = POLICY) -> list:
    out = []
    for label, text in texts.items():
        doc = Document(text=text, source=Source.of(f"https://shop.example.org/{label}", text.encode(),
                                                   provenance="fetched"))
        out.append(doc.passage(0, len(text)))
    return out


def _vector(word: str):
    v = np.zeros(5)
    v[MEANING.get(word.strip(".,?").lower(), 4)] = 1.0
    return v


class _Encoder:
    """A model double: one vector per word, from the meaning table."""

    calls: list[str] = []

    def encode(self, texts, is_query=False):
        self.calls.append("query" if is_query else "passages")
        return [np.array([_vector(w) for w in t.split()]) for t in texts]


def _cache(tmp: Path, weights: Weights = SMALL, *, size: int = 128, incomplete: bool = False,
           weights_file: str = "model.safetensors") -> Path:
    repo = tmp / ("models--" + weights.repo.replace("/", "--"))
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc123")
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    (snapshot / weights_file).write_bytes(b"w" * size)
    (repo / "blobs").mkdir()
    if incomplete:
        (repo / "blobs" / "f00d.incomplete").write_bytes(b"part")
    return tmp


@pytest.fixture
def dense(tmp_path):
    """A ready ColBERT rung: the test encoder, and weights complete in a cache of the test's own."""
    loaded: list[str] = []

    def loader(path: str) -> _Encoder:
        loaded.append(path)
        return _Encoder()

    rung = ColbertRung(SMALL, cache=_cache(tmp_path), loader=loader)
    RUNGS.register(rung, replace=True)
    yield loaded
    RUNGS.register(ColbertRung(), replace=True)


def _run(op: str, **params) -> dict:
    return CtxColbertBlock().infer(CtxColbertInput(op=op, **params)).unwrap()


# -- D1 (under_retrieval): a required dense ranking is refused, never served by word matching -----------------------

def test_d1_requires_dense_refuses_when_only_word_matching_could_answer() -> None:
    out = _run("search", query=PARAPHRASE, passages=[p.model_dump() for p in _passages()], requires_dense=True)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_KNOWLEDGE_DENSE_REQUIRED"
    assert [s["rung"] for s in out["skipped"]] == ["colbert", "embeddings", "bm25"]
    assert "pip install 'g6[colbert] @ git+https://github.com/basicisabc1994/g6solver'" in out["reason"] and "dense retrieval is required" in out["reason"]
    assert out["hits"] == []


def test_d1_without_requires_dense_the_fallback_says_it_is_word_matching_and_misses_the_paraphrase() -> None:
    # the case the refusal is for: BM25 shares no word with the paraphrase, so it finds nothing
    out = _run("search", query=PARAPHRASE, passages=[p.model_dump() for p in _passages()])
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_KNOWLEDGE_RANK_NO_MATCH"
    assert out["rung"] == "bm25" and "misses paraphrase" in out["warning_card"]["suggested_action"]
    assert "not installed" in out["warning_card"]["message"]


def test_d1_a_dense_rung_finds_the_paraphrase_the_word_match_missed(dense) -> None:
    out = _run("search", query=PARAPHRASE, passages=[p.model_dump() for p in _passages()], requires_dense=True)
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_KNOWLEDGE_RANKED"
    assert out["rung"] == "colbert" and out["score_scale"] == "colbert_maxsim"
    assert out["hits"][0]["passage"]["text"] == POLICY["refunds"]


# -- CTX_COLBERT-02: weights are never downloaded; a cache without them is not taken for them ---------------------------

def test_p2_weights_not_in_the_cache_are_never_loaded_and_the_reply_says_how_to_get_them(tmp_path) -> None:
    def loader(path: str):
        raise AssertionError("the loader was called: it would fetch the weights")

    for cache in (tmp_path / "empty", _cache(tmp_path / "partial", incomplete=True),
                  _cache(tmp_path / "metadata", size=10)):
        cache.mkdir(exist_ok=True)
        rung = ColbertRung(SMALL, cache=cache, loader=loader)
        assert rung.ready() and "G6 never downloads model weights" in rung.ready()
        RUNGS.register(rung, replace=True)
        try:
            out = _run("search", query="refunds", passages=[p.model_dump() for p in _passages()], rung="colbert")
        finally:
            RUNGS.register(ColbertRung(), replace=True)
        assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_KNOWLEDGE_RUNG_UNAVAILABLE"
        assert "hf download test-org/test-colbert" in out["reason"]


def test_weights_only_in_pickle_form_are_refused_with_how_to_convert_them_and_never_loaded(tmp_path) -> None:
    # a pickle runs code when it is loaded: G6 loads weights only in safetensors form
    def loader(path: str):
        raise AssertionError("the loader was called on pickled weights")

    pickled = _cache(tmp_path / "pickle", weights_file="pytorch_model.bin")
    layer = _cache(tmp_path / "layer")  # safetensors at the top, and a pickled layer with none beside it
    dense_layer = layer / ("models--" + SMALL.repo.replace("/", "--")) / "snapshots" / "abc123" / "1_Dense"
    dense_layer.mkdir()
    (dense_layer / "pytorch_model.bin").write_bytes(b"p" * 128)
    for cache in (pickled, layer):
        where, why = cached(SMALL, cache)
        assert where is None and "safetensors" in why and "pickle" in why and "convert" in why, why
        RUNGS.register(ColbertRung(SMALL, cache=cache, loader=loader), replace=True)
        try:
            out = _run("search", query="refunds", passages=[p.model_dump() for p in _passages()], rung="colbert")
        finally:
            RUNGS.register(ColbertRung(), replace=True)
        assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_KNOWLEDGE_RUNG_UNAVAILABLE"
    both = _cache(tmp_path / "both")  # a pickle beside its safetensors copy: the libraries load the safetensors one
    snapshot = both / ("models--" + SMALL.repo.replace("/", "--")) / "snapshots" / "abc123"
    (snapshot / "pytorch_model.bin").write_bytes(b"p" * 128)
    assert cached(SMALL, both) == (snapshot, "")


def test_complete_weights_are_found_and_loaded_from_the_cache_folder(dense, tmp_path) -> None:
    where, why = cached(SMALL, tmp_path)
    assert why == "" and where == tmp_path / "models--test-org--test-colbert" / "snapshots" / "abc123"
    _run("search", query="store credit", passages=[p.model_dump() for p in _passages()])
    assert dense == [str(where)]


# -- CTX_COLBERT-01 (P1): no litellm, no model re-ranker, no embedding service ---------------------------------------

@pytest.mark.parametrize("claim", [{"agentic_rerank": True}, {"rerank": True}, {"embedding_model": "ollama/x"},
                                   {"model": "gpt-4"}, {"completion_state": "verified"}])
def test_p1_g3_no_field_names_a_reranker_a_model_or_a_label(claim: dict) -> None:
    result = get_registry().invoke("ctx_colbert", "search", {"query": "x", "texts": {"a": "x"}, **claim})
    assert "G6_E_INPUT_VALIDATION" in str(result.failure())


def test_p1_the_package_imports_no_litellm_and_no_model_door() -> None:
    imported = set()
    for path in Path(package.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not [m for m in imported if m.split(".")[0] in ("litellm", "ragatouille", "openai", "anthropic")]
    assert not [m for m in imported if m.startswith("g6.core.llm")]  # no model call: there is no re-ranker


# -- CTX_COLBERT-03 and -04: hits keep their source and offsets, on their rung's own scale -----------------------------

def test_p2_hits_are_passages_with_their_source_and_offsets_on_the_rungs_scale(dense) -> None:
    passages = _passages()
    found = rank("store credit", passages)
    assert found.rung == "colbert" and found.searched.score_scale == "colbert_maxsim"
    top = found.searched.hits[0]
    assert top.passage == next(p for p in passages if "credit" in p.text) and top.score_scale == "colbert_maxsim"
    assert top.passage.source.uri == "https://shop.example.org/credit"
    lexical = rank("store credit", passages, rung="bm25").searched
    assert lexical.score_scale == "bm25" and lexical.hits[0].passage == top.passage


def test_maxsim_is_the_sum_of_each_query_tokens_best_cosine() -> None:
    q = np.array([[1.0, 0.0], [0.0, 2.0]])
    d = np.array([[1.0, 1.0], [3.0, 0.0]])
    # token 1: best is [3, 0] (cosine 1); token 2: best is [1, 1] (cosine 0.7071)
    assert maxsim(q, d) == pytest.approx(1 + 2 ** -0.5)
    with pytest.raises(ValueError):
        maxsim(q, np.zeros((2, 3)))


# -- per-call corpus: nothing is kept between calls ----------------------------------------------------------------------

def test_a_call_with_no_passages_is_refused_never_answered_from_an_earlier_call(dense) -> None:
    first = _run("search", query="refunds", passages=[p.model_dump() for p in _passages()])
    assert first["completion_state"] == "qualified-draft"
    later = _run("search", query="refunds")
    assert later["completion_state"] == "blocked-escalated" and later["code"] == "G6_E_KNOWLEDGE_NO_PASSAGES"
    other = _run("search", query="refunds", texts={"note": "Our office opens at nine."})
    assert [h["passage"]["source"]["uri"] for h in other["hits"]] == ["note"]


# -- G4: a rung that breaks is named, never hidden ------------------------------------------------------------------------

def test_g4_a_rung_that_breaks_is_passed_over_by_name_and_refused_when_it_was_named(tmp_path) -> None:
    class Broken(_Encoder):
        def encode(self, texts, is_query=False):
            raise RuntimeError("out of memory")

    RUNGS.register(ColbertRung(SMALL, cache=_cache(tmp_path), loader=lambda path: Broken()), replace=True)
    try:
        best = _run("search", query="store credit", passages=[p.model_dump() for p in _passages()])
        assert best["code"] == "G6_E_KNOWLEDGE_RANKED_FALLBACK" and best["rung"] == "bm25"
        assert "colbert: it failed: RuntimeError: out of memory" in best["warning_card"]["message"]
        named = _run("search", query="store credit", passages=[p.model_dump() for p in _passages()], rung="colbert")
        assert named["completion_state"] == "blocked-escalated" and named["code"] == "G6_E_KNOWLEDGE_RUNG_FAILED"
    finally:
        RUNGS.register(ColbertRung(), replace=True)


def test_g4_a_corpus_over_the_limit_and_an_unknown_rung_are_refused() -> None:
    too_many = {f"t{i}": "x" * 3000 for i in range(1800)}
    assert _run("search", query="x", texts=too_many)["code"] == "G6_E_KNOWLEDGE_CORPUS_TOO_LARGE"
    out = _run("search", query="x", texts={"a": "x"}, rung="splade")
    assert out["code"] == "G6_E_KNOWLEDGE_RUNG_UNAVAILABLE" and "no rung of that name" in out["reason"]


# -- D9: without the extras, every op answers and says what is not installed ---------------------------------------------

def test_d9_without_the_extras_the_rungs_say_what_to_install() -> None:
    listed = {r["name"]: r for r in _run("rungs")["rungs"]}
    assert listed["bm25"]["ready"] == "ready"
    for name, extra in (("colbert", "colbert"), ("embeddings", "embeddings")):
        if listed[name]["ready"] != "ready":
            assert "not installed" in listed[name]["ready"] or "G6 never downloads" in listed[name]["ready"]
            assert f"g6[{extra}]" in listed[name]["ready"] or "hf download" in listed[name]["ready"]
    assert listed["colbert"]["weights"]["licence"].startswith("MIT by its model card")
    assert listed["embeddings"]["weights"]["licence"].startswith("Apache-2.0 by its model card")


def test_the_real_colbert_rung_ranks_when_pylate_and_its_weights_are_here() -> None:
    pytest.importorskip("pylate")
    rung = ColbertRung()
    if rung.ready():
        pytest.skip(rung.ready())
    found = rank(PARAPHRASE, _passages(), rung="colbert")
    assert found.rung == "colbert" and found.searched.hits[0].passage.text == POLICY["refunds"]


def test_the_real_embeddings_rung_ranks_when_sentence_transformers_and_its_weights_are_here() -> None:
    pytest.importorskip("sentence_transformers")
    rung = EmbeddingsRung()
    if rung.ready():
        pytest.skip(rung.ready())
    found = rank(PARAPHRASE, _passages(), rung="embeddings")
    assert found.rung == "embeddings" and found.searched.score_scale == "cosine"


# -- D2, D4, D5, D6 ----------------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_guards_against_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("under_retrieval",) and package.__g6_introduces__ == ()
    assert "g6.knowledge.ctx_colbert" not in FailureModeRegistry.planned()
    assert package.__g6_sensitive_ops__ == () and set(package.__g6_extras__) == {"colbert", "embeddings"}


def test_d4_the_block_is_typed_and_lists_its_ops() -> None:
    assert get_registry().get_class("ctx_colbert") is CtxColbertBlock
    assert CtxColbertInput.model_config["extra"] == "forbid"
    assert get_registry().invoke("ctx_colbert", "ops", {}).unwrap()["ops"] == ["search", "rungs"]


def test_d5_a_rung_registered_from_outside_and_the_family_backend(dense) -> None:
    class Shortest:
        name, score_scale, dense, extra = "shortest", "cosine", True, ""

        def ready(self) -> str:
            return ""

        def score(self, query, texts):
            return [1.0 / len(t) for t in texts]

    RUNGS.register(Shortest(), replace=True)
    try:
        out = _run("search", query="anything", passages=[p.model_dump() for p in _passages()], rung="shortest")
        assert out["rung"] == "shortest" and out["hits"][0]["passage"]["text"] == POLICY["credit"]
    finally:
        RUNGS.unregister("shortest")
    backend = index_backends.get("colbert")
    assert backend is not None and not backend.persistent and not backend.network
    index = index_backends.open("colbert")
    assert isinstance(index, ColbertIndex) and isinstance(index, Retriever)
    index.add(_passages())
    assert index.search(PARAPHRASE, top_k=1)[0].text == POLICY["refunds"]
    assert index_backends.open("colbert").find("refunds").outcome == "empty"  # a new index holds nothing


def test_d6_it_is_found_by_its_failure_mode_described_and_round_trips_over_mcp() -> None:
    from g6.core import discovery

    title = FailureModeRegistry.title("under_retrieval")
    assert "ctx_colbert" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("ctx_colbert")
    assert info["ops"] == ["search", "rungs"] and info["skill"] and info["family"] == "knowledge"
    reply = invoke_reply("ctx_colbert", "search", json.dumps({"query": "store credit", "texts": POLICY}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft"
    assert invoke_reply("ctx_colbert", "rungs", "{}")["ok"]
