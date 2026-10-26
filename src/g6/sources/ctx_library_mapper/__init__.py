# SPDX-License-Identifier: Apache-2.0
"""g6.sources.ctx_library_mapper — find candidate libraries in PyPI, GitHub and Hugging Face, their declared licences looked up against yours.

An extension in the sources family. ``search_candidates(query, sources)`` asks each source you name through G6's
checked HTTP (``g6.core.http``: every hop checked, a size cap, a timeout, a pause per host; no key) and says what each
did: answered, answered with nothing, or the failure it was, so a source that could not be asked is never read as "no
such library exists". PyPI has no search API, so it is asked for the query as an exact project name. Candidates are
the registries' own words (name, address, description, declared licence, their own star and download counts); nothing
judges whether one implements a method. ``licence_check(candidates, project_licence)`` reads each declared licence into
SPDX and looks it up against the project's licence in a fixed table: ``compatible``, ``conditions``, ``incompatible``
or ``unknown`` (whatever the table does not hold). It is a table lookup, never a legal opinion.

    from g6.core.registry import get_registry
    out = get_registry().invoke("ctx_library_mapper", "licence_check", {
        "candidates": [{"name": "somelib", "declared_licence": "GPL-3.0-only"}], "project_licence": "MIT"}).unwrap()
    out["results"][0]["verdict"]   # "incompatible": GPL-3.0 needs the combined work under the GPL

``search_candidates`` is sensitive (it reaches the network); ``licence_check`` and ``sources`` reach nothing. Sources
are registered by name (``SOURCES.register``).

Not ported from the archive's ctx_library_mapper, and why: its default source, which invented a library with a licence
and a star count for any paper; the PyPI search page read with regular expressions; the store, the sessions and the
MCP sub-package; the quality score (stars over ten thousand plus description length) and the licence score (four names
scored 1.0 and everything else, GPL and unknown included, 0.5); the model rerank; and every result it labelled
verified.
"""
from g6.sources.ctx_library_mapper import licences, sources
from g6.sources.ctx_library_mapper.block import SENSITIVE_OPS, CtxLibraryMapperBlock, CtxLibraryMapperInput
from g6.sources.ctx_library_mapper.sources import SOURCES, Candidate, CandidateSource, SourceAnswer

__g6_component__ = True
__g6_block__ = "CtxLibraryMapperBlock"
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # search_candidates reaches the network
__g6_requires__: tuple[str, ...] = ("grounding", "knowledge")  # the knowledge family's shared reply and Source type
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a package's
# description can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("reinvented_method",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "SOURCES", "Candidate", "CandidateSource", "CtxLibraryMapperBlock", "CtxLibraryMapperInput",
           "SourceAnswer", "licences", "sources"]
