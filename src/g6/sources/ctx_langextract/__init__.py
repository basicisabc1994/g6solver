# SPDX-License-Identifier: Apache-2.0
"""g6.sources.ctx_langextract — pull named fields out of a text, each value with its offsets in the text or refused, the whole text read.

An extension in the sources family. ``extract(text, fields, examples)`` asks a model (the ``g6.core.llm`` caller you
give the block from Python) window by window over the whole text, each window fenced as data, or runs your own
regular expressions (``backend="regex"``), each in a bounded child process. Then every value is looked up in the text
itself: found exactly (``start``, ``end``, ``grounded`` true), flagged (only when case and white space are ignored),
or refused (nowhere in the text), so an invented value is never passed as found. Examples must hold their own values.
It guards against a key fact missed in two ways only: a fact past a cut-off (the whole text is read) and an invented
value (refused). A field the model gives no value for is ``not_checked``: a fact it missed is not caught here.

    from g6.sources.ctx_langextract import CtxLangExtractBlock, CtxLangExtractInput
    block = CtxLangExtractBlock(caller=my_caller)   # any g6.core.llm caller; omit it to use only backend="regex"
    out = block.infer(CtxLangExtractInput(op="extract", text="Payment is due on 1 March 2027.",
                                          fields={"due": "the due date"})).unwrap()
    out["fields"][0]["start"], out["fields"][0]["grounded"]   # 18, True when the model copied "1 March 2027"

No op is sensitive: it reaches no network and writes no file; the model is the caller you give it, or over MCP the
one the person configured in their key file. With none configured, the model backend is refused and the regex backend
runs.

Not ported from the archive's ctx_langextract, and why: Google's LangExtract (it calls models through its own provider
clients and their own HTTP, outside g6.core.llm and g6.core.http); the model chosen by environment variables and the
fall-through to the paid Claude Code door; the 2,000-character cut of the text; the built-in pattern table ("name" as
any two capitalised words); the 20 store ops for patterns, schemas, sessions and templates, and the MCP sub-package;
the rerank; and every extraction it labelled verified without checking it against the text.
"""
from g6.sources.ctx_langextract import extract as extraction
from g6.sources.ctx_langextract.block import SENSITIVE_OPS, CtxLangExtractBlock, CtxLangExtractInput, Example

__g6_component__ = True
__g6_block__ = "CtxLangExtractBlock"
__g6_model__ = "caller"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # nothing reaches the network, writes a file or acts outside G6
__g6_requires__: tuple[str, ...] = ("grounding", "knowledge")  # the knowledge family's shared reply and Source type
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (the text can
# carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("missed_key_fact",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "CtxLangExtractBlock", "CtxLangExtractInput", "Example", "extraction"]
