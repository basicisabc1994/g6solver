# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.knowledge_converter — turn PDFs, web pages, Markdown and text into a document that says what converted it and what could not be read.

An extension in the knowledge family. Converters are registered by media type: text and Markdown as they are, HTML
through the standard library's parser (scripts, styles and hidden text dropped, the title kept apart) and PDF through
pypdf's text layer (``pip install 'g6[docs]'``; never PyMuPDF). It converts the bytes it is given, or the bytes
``knowledge_fetcher`` kept by their hash, and never fetches anything. A converted document records what converted it
and what it could not read: a PDF page without a text layer is listed in ``unread`` (``partial``), and a source with
no readable text at all is refused (``nothing_readable``), never returned as an empty document.

    from g6.knowledge.knowledge_converter import convert
    found = convert(open("report.pdf", "rb").read(), "application/pdf", uri="https://example.org/report.pdf")
    found.outcome, found.document.text, found.document.unread   # "partial", the pages read, the pages that were not

Ops: ``convert`` and ``types``, both pure. A result is ``qualified-draft`` at best.

Not ported from the archive's knowledge_converter, and why: OCR posted to a fixed local Ollama model, and model text
stamped as the source's own words; CLIP and CLAP embeddings and video frames (the index stage's job, and heavy
undeclared downloads); Whisper transcription (a heavy model downloaded on first use, outside G6's checked fetch); the
HTML route that fetched the page again from its address instead of converting the bytes it was given; the routes
through other extensions; and pdf2image, which needs poppler (GPL), since no page is rendered here.
"""
from g6.knowledge.knowledge_converter import converters
from g6.knowledge.knowledge_converter.block import SENSITIVE_OPS, KnowledgeConverterBlock, KnowledgeConverterInput
from g6.knowledge.knowledge_converter.converters import CONVERTERS, Converted, Converter, convert, convert_kept

__g6_component__ = True
__g6_block__ = "KnowledgeConverterBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # it converts the bytes it is given; nothing is fetched or run
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a PDF's or a
# page's text can carry instructions an agent might follow, as a fetched page's can).
__g6_mitigates__: tuple[str, ...] = ("unreadable_source",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"docs": ("pypdf",)}

__all__ = ["CONVERTERS", "SENSITIVE_OPS", "Converted", "Converter", "KnowledgeConverterBlock", "KnowledgeConverterInput",
           "convert", "convert_kept", "converters"]