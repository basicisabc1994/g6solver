# SPDX-License-Identifier: Apache-2.0
"""The child process ``knowledge_converter`` starts to read a PDF with pypdf (``python -I _pdf_child.py``), so a PDF
that makes pypdf slow or hungry is bounded by the parent's deadline and memory cap, never by G6's own process. It reads
the PDF's bytes on stdin and writes one JSON reply on stdout, and imports nothing of G6.

Arguments: the most pages, the most bytes of text to return (each page's text as JSON in UTF-8), the seconds it may
spend reading pages, and the memory it may use in MiB (lowered here on POSIX; on Windows the parent's job object caps
it). A PDF whose page tree declares more pages than the cap is refused before any page is loaded. Reading stops at the
text cap or when its time is up, and every page not read is named.

Reply: ``{"ok": true, "version", "texts": [...], "unread": [...]}`` or ``{"ok": false, "version", "reason"}``.
"""
import io
import json
import sys
import time

NO_TEXT_LAYER = "no text layer (a scanned image or a blank page); it was not read"


def _limit(mem_mb: int) -> None:
    try:
        import resource
    except ImportError:  # Windows: the parent's job object caps the memory
        return
    kind = getattr(resource, "RLIMIT_AS", None)
    if kind is not None:
        try:
            resource.setrlimit(kind, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        except (ValueError, OSError):
            pass


def _rest(number: int, count: int, why: str) -> str:
    return f"page {number}: {why}" if number == count else f"pages {number} to {count}: {why}"


def _read(data: bytes, max_pages: int, max_bytes: int, seconds: float) -> dict:
    began = time.monotonic()
    import pypdf  # after the limits are lowered

    reply: dict = {"ok": False, "version": pypdf.__version__}
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return dict(reply, reason="the PDF is encrypted with a password")
        declared = int(reader.trailer["/Root"]["/Pages"]["/Count"])
    except Exception as exc:  # noqa: BLE001 - a PDF pypdf cannot open is a refusal with its reason
        return dict(reply, reason=f"pypdf could not read the PDF: {type(exc).__name__}: {exc}"[:500])
    if declared > max_pages:
        return dict(reply, reason=f"the PDF says it has {declared} pages, over the {max_pages}-page cap, so no page "
                                  "was loaded")
    try:
        pages = reader.pages
        count = len(pages)
    except Exception as exc:  # noqa: BLE001
        return dict(reply, reason=f"pypdf could not read the PDF's pages: {type(exc).__name__}: {exc}"[:500])
    if count > max_pages:
        return dict(reply, reason=f"the PDF's page tree holds {count} pages, over the {max_pages}-page cap, though it "
                                  f"says {declared}")
    texts: list[str] = []
    unread: list[str] = []
    used = 0
    for number in range(1, count + 1):
        if time.monotonic() - began >= seconds:
            unread.append(_rest(number, count, f"not read: the reading time ({seconds:g} seconds) ran out"))
            break
        try:
            text = pages[number - 1].extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one page that fails is reported, not dropped in silence
            unread.append(f"page {number}: its text could not be extracted ({type(exc).__name__})")
            continue
        if not text.strip():
            unread.append(f"page {number}: {NO_TEXT_LAYER}")
            continue
        size = len(json.dumps(text, ensure_ascii=False).encode("utf-8"))
        if used + size > max_bytes:
            unread.append(_rest(number, count, f"not read: over the {max_bytes}-byte text cap"))
            break
        used += size
        texts.append(text)
    return dict(reply, ok=True, texts=texts, unread=unread)


def main(argv: list[str]) -> int:
    max_pages, max_bytes, seconds, mem_mb = int(argv[0]), int(argv[1]), float(argv[2]), int(argv[3])
    _limit(mem_mb)
    reply = _read(sys.stdin.buffer.read(), max_pages, max_bytes, seconds)
    sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))