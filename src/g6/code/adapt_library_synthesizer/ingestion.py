# SPDX-License-Identifier: Apache-2.0
"""Bounded paper fetches through core.http, allow-listed at every redirect hop."""
from urllib.parse import urlsplit

from g6.code._types import CHECKER_NOT_INSTALLED, CodeRefused
from g6.core import http, sandbox

ALLOWED_HOSTS = ("arxiv.org", "acm.org", "ieee.org", "springer.com", "sciencedirect.com",
                 "openreview.net", "aclanthology.org", "proceedings.neurips.cc", "papers.neurips.cc",
                 "jmlr.org", "plos.org", "biorxiv.org", "medrxiv.org", "semanticscholar.org")
MAX_BYTES = 25 * 1024 * 1024
MAX_TEXT = 200_000


def allowed(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise CodeRefused("G6_E_PAPER_HOST", "paper URL host is not in the publication allow-list")


class PaperClient(http.Client):
    def _checked(self, url: str, *, robots: bool) -> str:
        allowed(url)
        return super()._checked(url, robots=robots)


_PDF = """
import pathlib
try:
    from pypdf import PdfReader
except ImportError:
    pathlib.Path("missing.txt").write_text('pypdf is not installed; pip install "g6[docs]".', encoding="utf-8")
else:
    reader = PdfReader("paper.pdf")
    if len(reader.pages) > 500:
        raise ValueError("paper exceeds 500 pages")
    chunks = []
    length = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        length += len(text)
        if length > 200000:
            raise ValueError("extracted text exceeds 200000 characters")
        chunks.append(text)
    text = "\\n".join(chunks)
    if not text.strip():
        raise ValueError("PDF has no extractable text; provide a transcription")
    pathlib.Path("paper.txt").write_text(text, encoding="utf-8")
"""


def pdf_text(body: bytes) -> str:
    ran = sandbox.run_script(_PDF, {"paper.pdf": body}, timeout_s=30, mem_mb=1024, max_output_bytes=900_000)
    if "missing.txt" in ran.outputs:
        raise CodeRefused(CHECKER_NOT_INSTALLED, ran.outputs["missing.txt"].decode())
    if ran.status != "ok" or "paper.txt" not in ran.outputs:
        raise CodeRefused("G6_E_PAPER_PDF", "pypdf could not extract bounded text",
                          [{"kind": "pdf_parse", "status": ran.status, "stderr": ran.stderr[-1000:]}])
    return ran.outputs["paper.txt"].decode("utf-8")


def fetch_paper(url: str, *, transport=None, resolver=None) -> tuple[str, dict]:
    allowed(url)
    client = PaperClient(transport=transport, resolver=resolver, timeout=30, max_bytes=MAX_BYTES)
    response = client.get(url)
    media = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.body.startswith(b"%PDF") or media in {"application/pdf", "application/x-pdf"}:
        text = pdf_text(response.body)
        parser = "pypdf in core.sandbox"
    elif media in {"text/plain", "text/html", "text/markdown", "application/xhtml+xml"}:
        text = response.body.decode("utf-8")
        parser = "UTF-8 text; HTML remains source text"
    else:
        raise CodeRefused("G6_E_PAPER_TYPE", "response is not PDF or supported text")
    if not text.strip() or len(text) > MAX_TEXT:
        raise CodeRefused("G6_E_PAPER_SIZE", "paper text is empty or exceeds 200000 characters")
    return text, {"kind": "paper_fetch", "url": response.url, "bytes": len(response.body), "parser": parser}
