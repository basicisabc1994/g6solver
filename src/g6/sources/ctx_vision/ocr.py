# SPDX-License-Identifier: Apache-2.0
"""An independent reading of a picture's text: Tesseract OCR, an outside program the person installs, compared with
the model's reading.

:func:`read` writes the decoded picture to a scratch folder and runs ``tesseract <file> stdout`` as a bounded child
(``g6.core.component_process``: a time limit, a clean environment, the whole process tree ended, the reply capped).
A missing tesseract is ``G6_E_OUTSIDE_PROGRAM_MISSING`` with the install steps (``g6.core.outside.require``), never a
silent skip. :func:`compare` sets the two readings side by side as ordered word sequences after case and spacing are
ignored (``difflib.SequenceMatcher`` over the word lists), so the same words in another order (two amounts swapped,
two names reordered) disagree, and names each span where they differ; the word counts are kept as diagnostics. Two
readers agreeing is not proof the text is right, so the comparison is evidence, never a label.
"""
from __future__ import annotations

import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from g6.core import component_process, outside
from g6.sources.ctx_vision.images import Picture

TESSERACT = outside.OutsideProgram(
    name="tesseract", purpose="an independent OCR reading that ctx_vision's extract_text compares with the model's",
    install="Install Tesseract OCR (github.com/tesseract-ocr/tesseract; on Windows the UB Mannheim installer) and put "
            "tesseract on PATH.", licence="Apache-2.0", command="tesseract")
SECONDS = 60.0
MAX_COMPARED = 20_000
#: The most words of each reading compared in order; beyond it the two are never said to agree.
MAX_WORDS = 5_000


class OcrFailed(Exception):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


def read(picture: Picture) -> dict[str, Any]:
    """Tesseract's text for ``picture`` and which tesseract read it; :class:`OcrFailed` when it is missing or fails."""
    found = outside.require(TESSERACT)
    if found.is_fail():
        raise OcrFailed(outside.OUTSIDE_PROGRAM_MISSING, found.error.partition("] ")[2] or found.error)
    program = found.value
    with tempfile.TemporaryDirectory(prefix="g6-ocr-") as scratch:
        image = Path(scratch) / f"picture.{picture.format}"
        image.write_bytes(picture.data)
        try:
            with component_process.window(SECONDS):
                done = component_process.run_contained([program.where, str(image), "stdout"],
                                                       env=outside.clean_environment(), cwd=scratch)
        except component_process.ExecutionInterrupted:
            raise OcrFailed("G6_E_VISION_OCR_FAILED", f"tesseract did not finish within {SECONDS:g} seconds") from None
        except OSError as exc:
            raise OcrFailed("G6_E_VISION_OCR_FAILED", f"tesseract could not be run: {type(exc).__name__}") from None
    if done.returncode != 0:
        tail = " ".join(done.stderr.decode("utf-8", "replace").split())[-300:]
        raise OcrFailed("G6_E_VISION_OCR_FAILED", f"tesseract stopped with exit code {done.returncode}: {tail}")
    return {"text": done.stdout.decode("utf-8", "replace").strip(), "program": program.where,
            "version": program.version}


def _words(text: str) -> list[str]:
    return text.casefold().split()


def compare(model_text: str, ocr_text: str) -> dict[str, Any]:
    """The two readings in order: each span where they differ, how many words match in order, and, as diagnostics,
    the words only one has (counted) and the similarity of the whole."""
    model_words, ocr_words = _words(model_text), _words(ocr_text)
    cut = max(len(model_words), len(ocr_words)) > MAX_WORDS
    matcher = SequenceMatcher(None, model_words[:MAX_WORDS], ocr_words[:MAX_WORDS], autojunk=False)
    differences = [{"model": " ".join(model_words[i1:i2]), "ocr": " ".join(ocr_words[j1:j2]), "at_model_word": i1,
                    "at_ocr_word": j1} for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]
    model, ocr = Counter(model_words), Counter(ocr_words)
    a, b = " ".join(model_words)[:MAX_COMPARED], " ".join(ocr_words)[:MAX_COMPARED]
    return {"model_words": len(model_words), "ocr_words": len(ocr_words),
            "matching_words": sum(block.size for block in matcher.get_matching_blocks()),
            "differences": differences[:50], "differing_spans": len(differences), "agree": not differences and not cut,
            "compared_words": MAX_WORDS if cut else max(len(model_words), len(ocr_words)),
            "only_in_model": sorted((model - ocr).elements())[:50], "only_in_ocr": sorted((ocr - model).elements())[:50],
            "character_similarity": round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 4) if a or b else 1.0,
            "how": "both readings case-folded and split on white space, compared in order as word sequences "
                   "(difflib.SequenceMatcher); the word counts only_in_model and only_in_ocr are diagnostics"}


def spans_said(compared: dict[str, Any], most: int = 5) -> str:
    """The differing spans of ``compare``'s result, in words, the first ``most`` of them."""
    shown = [f"the model read {d['model'] or '(nothing)'!r} where Tesseract read {d['ocr'] or '(nothing)'!r}"
             for d in compared["differences"][:most]]
    more = compared["differing_spans"] - len(shown)
    return "; ".join(shown) + (f"; and {more} more" if more > 0 else "")


__all__ = ["MAX_WORDS", "SECONDS", "TESSERACT", "OcrFailed", "compare", "read", "spans_said"]
