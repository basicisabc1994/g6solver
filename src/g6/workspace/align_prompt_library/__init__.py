# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.align_prompt_library — keeps every version of a prompt, spec or test set by the SHA-256 of its content, so a result can cite the exact version it used.

An extension in the workspace family, and the family's one versioned library (``align_artifacts`` is merged into it:
its prompt, evaluation-set and specification record kinds, and its soft delete and rollback, both now new versions).
Every version is an entry in an append-only ``g6.core.ledger`` chain under the G6 home, addressed by the SHA-256 of its
content (its digest), as ``g6.metaharness.artifacts`` addresses content. Nothing is overwritten or removed: a new
version is added, a rollback adds a version holding an earlier one's content, and ``retire`` adds a tombstone, after
which every version is still readable by its number or digest.

    from g6.workspace.align_prompt_library import ops
    stored = ops.put("prompt", "summary", "Summarise {{document}} for {{audience}}.", metadata={"author": "ana"})
    stored.digest                                   # cite it in a run's evidence
    ops.render("summary", digest=stored.digest, values={"document": "the report", "audience": "a board"}).text
    ops.get(digest=stored.digest).content           # the same content, whatever happened to "summary" since

Ops: ``put``, ``get``, ``history``, ``search``, ``render`` (strict: a missing or an extra value is refused), ``retire``,
``rollback``, ``catalogue`` (the one prompt catalogue, ``catalogue.py``: each entry tied to a work mode and a pipeline
step) and ``install_catalog``. Put, retire, rollback and install_catalog are sensitive because their changes persist
into later tasks. What a caller says about a record (a
reviewer, a test result, a safety tag) is kept under ``declared`` as their word and changes nothing. Results are
``qualified-draft`` at best: a stored version is G6's record, not a check that the prompt is good.

Not ported from the archive's align_prompt_library and align_artifacts, and why: the registry that kept one row per name
(ALIGN_PROMPT_LIBRARY-01) and the deletes that removed every version (ALIGN_ARTIFACTS-01); the safety gate opened by a
declared reviewer and "passed" (ALIGN_PROMPT_LIBRARY-02: kept as declarations); every store labelled verified
(ALIGN_PROMPT_LIBRARY-03, ALIGN_ARTIFACTS-03); the alignment-spec and goal-tree system, the JSONL utilities and the
stores under the home folder (ALIGN_PROMPT_LIBRARY-04, ALIGN_ARTIFACTS-07); links, collections, bulk import, the
fail-open decorator (ALIGN_ARTIFACTS-05), the patterns, the three surfaces and the skill folders that held a developer's
name (ALIGN_PROMPT_LIBRARY-05, ALIGN_ARTIFACTS-06).
"""
from g6.workspace._envelope import WorkspaceRefused, WorkspaceResult
from g6.workspace.align_prompt_library import ops
from g6.workspace.align_prompt_library.block import SENSITIVE_OPS, AlignPromptLibraryBlock, AlignPromptLibraryInput
from g6.workspace.align_prompt_library.library import KINDS, Library, digest_of, placeholders

__g6_component__ = True
__g6_block__ = "AlignPromptLibraryBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a stored prompt's
# text can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("untracked_prompt_version",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["KINDS", "SENSITIVE_OPS", "AlignPromptLibraryBlock", "AlignPromptLibraryInput", "Library",
           "WorkspaceRefused", "WorkspaceResult", "digest_of", "ops", "placeholders"]
