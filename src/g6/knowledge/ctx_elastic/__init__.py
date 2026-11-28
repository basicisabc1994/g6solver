# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.ctx_elastic — search the Elasticsearch you run over fields its mapping holds, so a query that cannot match is refused rather than answered with no hits.

An extension in the knowledge family. Four read-only ops reach the Elasticsearch server the person runs on this
machine (a declared outside service, at ``http://127.0.0.1:9200`` or the local address ``G6_ELASTIC_URL`` names,
reached only through ``g6.core.outside``; G6 never starts, installs or bundles it): ``search`` the caller's words over
chosen fields with exact filters, ``get_doc``, ``count`` and ``mapping``. Each is sensitive. The query is built here
from typed parts, never taken as JSON from a caller or a model, and every field is checked against the index's
mapping first: a field the index does not hold, an exact filter on analysed text and a field inside a nested object
are refused with what the mapping holds, because Elasticsearch would answer each with no hits. A search whose shards
did not all answer says it is partial. Hits come back as the family's passages, untrusted text, scored ``es_bm25``.

    from g6.knowledge.ctx_elastic import ElasticIndex
    index = ElasticIndex("handbook", fields=("title", "body"))   # also a g6.grounding retriever
    found = index.find("parental leave")    # outcome, hits, total, evidence (fields, shards, partial)

The index is also the ``elastic`` backend of the family's index registry (read only: G6 never writes to the server).

Not ported from the archive's ctx_elastic, and why: the verified label on every search; the natural-language op that
ran a model's query JSON unchecked and cached its answers; the hard-coded ``content`` field; host and credentials
from the environment; writes, bulk indexing, index administration, reindexing and aliases; sessions, templates,
bookmarks and the model cache; and the re-ranker that was on by default.
"""
from g6.knowledge.ctx_elastic import elastic
from g6.knowledge.ctx_elastic.block import SENSITIVE_OPS, CtxElasticBlock, CtxElasticInput
from g6.knowledge.ctx_elastic.elastic import ELASTIC_KEY, ELASTICSEARCH, ElasticError, ElasticIndex

__g6_component__ = True
__g6_block__ = "CtxElasticBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # every op reaches the Elasticsearch server
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a hit is outside
# text that can carry instructions).
__g6_mitigates__: tuple[str, ...] = ("under_retrieval",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (ELASTICSEARCH,)
__g6_keys__ = (ELASTIC_KEY,)  # optional: from the person's key file, sent only to their server
__g6_extras__: dict[str, tuple[str, ...]] = {}
__g6_index_backends__: tuple[str, ...] = ("elastic",)  # the family's registry imports this package to find it

__all__ = ["ELASTICSEARCH", "ELASTIC_KEY", "SENSITIVE_OPS", "CtxElasticBlock", "CtxElasticInput", "ElasticError",
           "ElasticIndex", "elastic"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_KNOWLEDGE_ELASTIC_COUNTED": "none",
    "G6_E_KNOWLEDGE_ELASTIC_COUNT_PARTIAL": "none",
    "G6_E_KNOWLEDGE_ELASTIC_DENIED": "person",
    "G6_E_KNOWLEDGE_ELASTIC_DOCUMENT": "none",
    "G6_E_KNOWLEDGE_ELASTIC_HELD": "none",
    "G6_E_KNOWLEDGE_ELASTIC_INPUT": "input",
    "G6_E_KNOWLEDGE_ELASTIC_MAPPING": "none",
    "G6_E_KNOWLEDGE_ELASTIC_NOT_FOUND": "none",
    "G6_E_KNOWLEDGE_ELASTIC_NO_MATCH": "none",
    "G6_E_KNOWLEDGE_ELASTIC_PARSE_FAILED": "none",
    "G6_E_KNOWLEDGE_ELASTIC_PARTIAL": "none",
    "G6_E_KNOWLEDGE_ELASTIC_REDIRECTED": "none",
    "G6_E_KNOWLEDGE_ELASTIC_REJECTED": "none",
    "G6_E_KNOWLEDGE_ELASTIC_SEARCHED": "none",
    "G6_E_KNOWLEDGE_ELASTIC_SERVER_ERROR": "none",
    "G6_E_KNOWLEDGE_ELASTIC_TARGET": "none",
    "G6_E_KNOWLEDGE_ELASTIC_TIMEOUT": "person",
    "G6_E_KNOWLEDGE_FIELD_NOT_IN_MAPPING": "input",
    "G6_E_KNOWLEDGE_FILTER_ON_TEXT": "none",
    "G6_E_KNOWLEDGE_NESTED_FIELD": "input",
    "G6_E_KNOWLEDGE_NO_TEXT_FIELDS": "none",
}
