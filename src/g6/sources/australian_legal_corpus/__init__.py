# SPDX-License-Identifier: Apache-2.0
"""g6.sources.australian_legal_corpus — search Australian law on your own computer, each passage with its jurisdiction, link and collection date, never as current law.

An extension in the sources family. The Open Australian Legal Corpus (Umar Butler, published by Isaacus; CC BY 4.0,
each document also under its source's terms; doi:10.57967/hf/2784) holds about 232,000 documents from the
Commonwealth, New South Wales, Queensland, Western Australia, South Australia, Tasmania and Norfolk Island, collected
on 10 March 2025; none from Victoria, the ACT or the Northern Territory. G6 hosts, bundles and publishes none of it:
``fetch`` downloads the pinned revision's ``corpus.jsonl`` from the publisher's Hugging Face dataset when the person
asks (a dry run first, then through ``g6.action_gating`` and ``g6.core.http``'s checked download, its SHA-256 checked),
or ``register`` records a copy the person downloaded themselves. ``build`` indexes passages in SQLite FTS5 under G6's
state folder, legislation only unless asked, a bounded and resumable step at a time; ``search`` and ``get`` read them
back with each document's citation, jurisdiction, type, link, version id, date and collection date.

    from g6.core.registry import get_registry
    get_registry().invoke("australian_legal_corpus", "status", {})

A jurisdiction the corpus lacks is refused by name and never answered with another jurisdiction's law. Every reply
that carries corpus text says it was collected on its date and is not current law, that amendments, repeals and later
decisions are missing, where to confirm it, that it is not legal advice, and the attribution. It gives no legal
judgement, so it is not vetted; the profiles that use it are.

Not taken from the archive's grounding corpus agent, and why: its local index (built from other provenance, and not
G6's to publish), its routing of whole domains to one corpus, and its withheld-text policy; nothing of it is copied.
"""
from g6.core.outside import OutsideProgram
from g6.sources.australian_legal_corpus import corpus, store
from g6.sources.australian_legal_corpus.block import (
    SENSITIVE_OPS,
    AustralianLegalCorpusBlock,
    AustralianLegalCorpusInput,
)
from g6.sources.australian_legal_corpus.corpus import ATTRIBUTION, PINNED, Pin

#: The dataset, an online and free service: fetch downloads it, or the person downloads it with huggingface_hub's hf.
DATASET = OutsideProgram(
    name="Open Australian Legal Corpus",
    purpose="the dataset fetch downloads from its publisher's Hugging Face page (one file of about 9.4 GB), or that "
            "you download yourself with the hf program and register; hf is needed only for that",
    install=f"Let fetch download it when you ask, or: pip install huggingface_hub, then {PINNED.command()} (rerun it "
            "after an interruption), then register the file's path",
    licence="CC BY 4.0 (Isaacus, doi:10.57967/hf/2784); each document also under its source's own terms (the "
            "dataset's LICENCE.md); G6 redistributes none of it",
    command="hf", online=True)

__g6_component__ = True
__g6_block__ = "AustralianLegalCorpusBlock"
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # fetch reaches the network and writes 9.4 GB; remove deletes
__g6_requires__: tuple[str, ...] = ("action_gating", "grounding", "knowledge")  # the gate; the shared reply
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a document's
# text can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("answered_from_memory", "stale_context_used", "wrong_jurisdiction")
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (DATASET,)
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["ATTRIBUTION", "DATASET", "PINNED", "SENSITIVE_OPS", "AustralianLegalCorpusBlock",
           "AustralianLegalCorpusInput", "Pin", "corpus", "store"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_LEGAL_CORPUS_INDEX_UNREADABLE": "none",
    "G6_E_LEGAL_CORPUS_SEARCH_TOO_BROAD": "none",
    "G6_E_LEGAL_CORPUS_BUILT": "person",
    "G6_E_LEGAL_CORPUS_DISK_SHORT": "person",
    "G6_E_LEGAL_CORPUS_DOCUMENT": "person",
    "G6_E_LEGAL_CORPUS_DRY_RUN": "person",
    "G6_E_LEGAL_CORPUS_FETCHED": "person",
    "G6_E_LEGAL_CORPUS_FETCH_FAILED": "person",
    "G6_E_LEGAL_CORPUS_FILTERS_DIFFER": "person",
    "G6_E_LEGAL_CORPUS_FTS5_UNAVAILABLE": "person",
    "G6_E_LEGAL_CORPUS_GATE_HELD": "person",
    "G6_E_LEGAL_CORPUS_INDEX_MISMATCH": "person",
    "G6_E_LEGAL_CORPUS_INPUT": "input",
    "G6_E_LEGAL_CORPUS_JURISDICTION_NOT_COVERED": "person",
    "G6_E_LEGAL_CORPUS_NOT_FOUND": "person",
    "G6_E_LEGAL_CORPUS_NOT_INDEXED": "person",
    "G6_E_LEGAL_CORPUS_NOT_PINNED_FILE": "person",
    "G6_E_LEGAL_CORPUS_NO_CORPUS": "person",
    "G6_E_LEGAL_CORPUS_NO_INDEX": "person",
    "G6_E_LEGAL_CORPUS_NO_MATCH": "person",
    "G6_E_LEGAL_CORPUS_PASSAGES": "person",
    "G6_E_LEGAL_CORPUS_PRESENT": "person",
    "G6_E_LEGAL_CORPUS_REGISTERED": "person",
    "G6_E_LEGAL_CORPUS_REMOVED": "person",
    "G6_E_LEGAL_CORPUS_REMOVE_DRY_RUN": "person",
    "G6_E_LEGAL_CORPUS_STATUS": "person",
    "G6_E_LEGAL_CORPUS_STORAGE_FAILED": "person",
}
