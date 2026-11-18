"""Turn uploaded files into audit documents, with explicit reasons when a file cannot be used.

Supported:  .md  .txt  .csv  (UTF-8 text)   and   .pdf  (text-based PDF, best effort).
Not supported (reported, never guessed): scanned/image PDFs, OCR, drawings, other encodings,
encrypted PDFs, other file types.

PDF evidence is page-level. Each page becomes its own document named "<file>.pdf:page-<N>".
The reported line is a line of the text PyPDF extracted from that page. It is not a visual line.
"""
from __future__ import annotations
import hashlib
import io
import itertools
import logging
import threading
from dataclasses import dataclass, field

from .core import MAX_CHARS, MAX_DOCUMENTS, MAX_LINES, split_lines

MAX_FILE_BYTES = 2_000_000
MAX_PDF_PAGES = 10
# Aggregate limit across the two uploads: the engine accepts MAX_DOCUMENTS (12) documents. A PDF page is one document and
# a text file is one document, so (pages of PDF A or 1) + (pages of PDF B or 1) must not exceed MAX_DOCUMENTS.
# Public PDF guard (H2-005). These are fail-closed ceilings for untrusted PDFs, not a CPU/time or memory guarantee.
MAX_PDF_PAGE_CONTENT_BYTES = 400_000      # decoded content of one page (all its content streams together)
MAX_PDF_CONTENT_BYTES = 1_000_000         # decoded content of all pages of one PDF together
MAX_PDF_TEXT_CHARS = 500_000              # extracted text of all pages of one PDF together
MAX_PDF_CONTENT_STREAMS_PER_PAGE = 64
MAX_PDF_FORM_INVOCATIONS = 3              # form XObjects (reusable content) one page may invoke while text is extracted
MAX_PDF_FORMS_PER_PAGE = 16               # distinct form XObjects reachable from one page's resources
PAGE_SEP = ":page-"
TEXT_EXTENSIONS = (".md", ".txt", ".csv")
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + (".pdf",)
SUPPORTED_FORMATS_TEXT = (
    "Markdown (.md), plain text (.txt), CSV (.csv) and text-based PDF (.pdf, 10 pages or fewer per file; "
    f"at most {MAX_DOCUMENTS} pages and text files in total across both uploads). "
    "Scanned PDFs, images, drawings and other formats are not supported."
)


@dataclass
class IngestResult:
    filename: str
    documents: dict = field(default_factory=dict)  # audit name -> text
    issues: list = field(default_factory=list)     # dicts: code, filename, message, page (optional)
    kind: str = "unknown"
    pages: int = 0                                 # PDF pages counted from the page tree (0 for text files)


def _issue(result, code, message, page=None):
    item = {"code": code, "filename": result.filename, "message": message}
    if page is not None:
        item["page"] = page
    result.issues.append(item)


def _accept_text(result, name, text):
    if len(text) > MAX_CHARS or len(split_lines(text)) > MAX_LINES:
        _issue(result, "TEXT_TOO_LONG",
               f"{name} is longer than the limit ({MAX_CHARS:,} characters or {MAX_LINES:,} lines) and was not audited.")
        return False
    result.documents[name] = text
    return True


def ingest_upload(filename: str, content: bytes) -> IngestResult:
    """Read one uploaded file. Never raises for bad input; returns issues instead."""
    result = IngestResult(filename=filename)
    lower = filename.lower()
    if not lower.endswith(SUPPORTED_EXTENSIONS):
        _issue(result, "UNSUPPORTED_FORMAT", f"{filename}: this file type is not supported. {SUPPORTED_FORMATS_TEXT}")
        return result
    if len(content) > MAX_FILE_BYTES:
        _issue(result, "FILE_TOO_LARGE", f"{filename}: larger than {MAX_FILE_BYTES // 1_000_000} MB. Not audited.")
        return result
    if lower.endswith(".pdf"):
        result.kind = "pdf"
        _read_pdf(result, content)
        return result
    result.kind = "text"
    if b"\x00" in content:
        _issue(result, "NOT_TEXT", f"{filename}: contains binary data. It is not a text file. Not audited.")
        return result
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        _issue(result, "NOT_UTF8", f"{filename}: not valid UTF-8. Save the file as UTF-8 and upload it again. Not audited.")
        return result
    _accept_text(result, filename, text)
    return result


def _pdf_limits() -> dict:
    """pypdf limits for untrusted input. Applied per execution context (a ContextVar), never as process-wide state."""
    return dict(
        maximum_declared_stream_length=MAX_FILE_BYTES,
        array_based_stream_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        zlib_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        zlib_maximum_recovery_input_length=MAX_FILE_BYTES,
        lzw_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        run_length_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        brotli_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        jbig2_maximum_output_length=MAX_PDF_PAGE_CONTENT_BYTES,
        image_maximum_buffer_size=MAX_PDF_PAGE_CONTENT_BYTES,
        jbig2dec_binary=None,                       # never start an external program for untrusted input
        array_maximum_element_count=20_000,
        dictionary_maximum_element_count=20_000,
        stream_filters_maximum_length=4,
        stream_decoding_work_maximum_length=4 * MAX_PDF_PAGE_CONTENT_BYTES,
        stream_parsing_work_maximum_count=500_000,
        page_tree_maximum_entries=64,
        page_tree_maximum_depth=8,
        page_tree_maximum_page_entries=1_000,
        xform_maximum_invocations_per_extraction=MAX_PDF_FORM_INVOCATIONS,
    )


class _LimitWatch(logging.Handler):
    """pypdf reports some limits only as a log warning and then skips content (for example 'Exceeded 3 form XObject
    invocations ... further form content is skipped'). Skipped content must not look like a complete read, so warnings
    of the current thread that report an exceeded limit are collected here and the file is refused."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.thread = threading.get_ident()
        self.hits = []

    def emit(self, record):
        if record.thread == self.thread and "exceeded" in record.getMessage().lower():
            self.hits.append(record.getMessage())


class _PdfRefused(Exception):
    """A PDF limit was reached. The whole file is refused; no page of it is audited."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _open_pdf(content):
    """Parse the PDF structure and count pages from the page tree (not from the /Count entry). Caller holds the limits."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        return reader, None
    return reader, len(reader.pages)


def _stream_sizes(page):
    """Decoded byte sizes of the content streams of one page, decoded one at a time (each decode is limit-checked)."""
    from pypdf.generic import ArrayObject, StreamObject
    contents = page.get("/Contents")
    contents = contents.get_object() if contents is not None else None
    if contents is None:
        return
    items = list(contents) if isinstance(contents, ArrayObject) else [contents]
    if len(items) > MAX_PDF_CONTENT_STREAMS_PER_PAGE:
        raise _PdfRefused("PDF_LIMIT_EXCEEDED", f"more than {MAX_PDF_CONTENT_STREAMS_PER_PAGE} content streams on one page")
    for item in items:
        item = item.get_object()
        if isinstance(item, StreamObject):
            yield len(item.get_data())


def _form_sizes(resources, seen, depth=0):
    """Decoded sizes of the distinct form XObjects reachable from a resource dictionary (text extraction decodes and parses
    them on every invocation). Raises _PdfRefused when a page reaches too many forms or nests them too deeply."""
    from pypdf.generic import DictionaryObject, StreamObject
    resources = resources.get_object() if resources is not None else None
    if not isinstance(resources, DictionaryObject):
        return
    xobjects = resources.get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else None
    if not isinstance(xobjects, DictionaryObject):
        return
    for key in list(xobjects):
        ref = xobjects.raw_get(key)
        form = ref.get_object()
        if not isinstance(form, StreamObject) or form.get("/Subtype") != "/Form":
            continue                                # images are not decoded by text extraction
        ident = getattr(ref, "idnum", None) or id(form)
        if ident in seen:
            continue
        seen.add(ident)
        if len(seen) > MAX_PDF_FORMS_PER_PAGE or depth >= 4:
            raise _PdfRefused("PDF_LIMIT_EXCEEDED", "a page reaches too many or too deeply nested form objects")
        yield len(form.get_data())
        yield from _form_sizes(form.get("/Resources"), seen, depth + 1)


def pdf_page_count(content: bytes):
    """Cheap pre-extraction page count for the pair check. None when the PDF is unreadable, encrypted or over a limit."""
    try:
        from pypdf import apply_configuration
        with apply_configuration(**_pdf_limits()):
            return _open_pdf(content)[1]
    except Exception:
        return None


def _read_pdf(result, content):
    name = result.filename
    try:
        from pypdf import apply_configuration
        from pypdf.errors import LimitReachedError
    except Exception as exc:  # pragma: no cover - pypdf is a hard dependency of the hosted app
        _issue(result, "PDF_UNREADABLE", f"{name}: the PDF cannot be read ({type(exc).__name__}). Not audited.")
        return
    watch, pypdf_log = _LimitWatch(), logging.getLogger("pypdf")
    pypdf_log.addHandler(watch)
    try:
        with apply_configuration(**_pdf_limits()):
            _extract_pdf(result, content, watch)
    except _PdfRefused as refusal:
        result.documents.clear()                    # fail closed: no page of a refused PDF is audited
        _issue(result, refusal.code, f"{name}: {refusal.message}. Not audited.")
    except LimitReachedError:
        result.documents.clear()
        _issue(result, "PDF_LIMIT_EXCEEDED", f"{name}: the PDF is more complex than SpecForge reads (a safety limit was reached). Not audited.")
    except Exception as exc:
        result.documents.clear()
        _issue(result, "PDF_UNREADABLE", f"{name}: the PDF cannot be read ({type(exc).__name__}). Not audited.")
    finally:
        pypdf_log.removeHandler(watch)


def _extract_pdf(result, content, watch):
    name = result.filename
    reader, count = _open_pdf(content)
    if count is None:
        _issue(result, "PDF_ENCRYPTED", f"{name}: the PDF is encrypted. Not audited.")
        return
    result.pages = count
    if count == 0:
        _issue(result, "PDF_UNREADABLE", f"{name}: the PDF has no readable pages. Not audited.")
        return
    if count > MAX_PDF_PAGES:
        _issue(result, "PDF_TOO_MANY_PAGES", f"{name}: {count} pages. The limit is {MAX_PDF_PAGES}. Not audited.")
        return
    # Pre-extraction guard: decode every content stream once, with a per-page and a per-file ceiling, before any
    # text extraction starts. The decode itself is also bounded by the pypdf limits set in _pdf_limits.
    pages = []
    total_bytes = 0
    for number in range(count):
        page = reader.pages[number]
        page_bytes = 0
        sizes = _stream_sizes(page)
        forms = (MAX_PDF_FORM_INVOCATIONS * size for size in _form_sizes(page.get("/Resources"), set()))
        for size in itertools.chain(sizes, forms):  # a form counts once per allowed invocation
            page_bytes += size
            total_bytes += size
            if page_bytes > MAX_PDF_PAGE_CONTENT_BYTES:
                raise _PdfRefused("PDF_LIMIT_EXCEEDED", f"page {number + 1} has more than {MAX_PDF_PAGE_CONTENT_BYTES:,} bytes of decoded page content")
            if total_bytes > MAX_PDF_CONTENT_BYTES:
                raise _PdfRefused("PDF_LIMIT_EXCEEDED", f"the pages have more than {MAX_PDF_CONTENT_BYTES:,} bytes of decoded page content in total")
        pages.append(page)
    empty, total_chars = [], 0
    for number, page in enumerate(pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            if type(exc).__name__ == "LimitReachedError":
                raise
            _issue(result, "PDF_PAGE_UNREADABLE", f"{name}, page {number}: text extraction failed ({type(exc).__name__}).", number)
            continue
        if watch.hits:
            raise _PdfRefused("PDF_LIMIT_EXCEEDED", f"page {number} reached a safety limit while its text was read, so part of it would be missing")
        total_chars += len(text)
        if total_chars > MAX_PDF_TEXT_CHARS:
            raise _PdfRefused("PDF_LIMIT_EXCEEDED", f"more than {MAX_PDF_TEXT_CHARS:,} characters of extracted text in total")
        if not text.strip():
            empty.append(number)
            continue
        _accept_text(result, f"{name}{PAGE_SEP}{number}", text)
    for number in empty:
        _issue(result, "PDF_PAGE_NO_TEXT",
               f"{name}, page {number}: no extractable text (possibly a scan or an image). SpecForge does not use OCR. This page was not audited.",
               number)


def ingest_pair(name_a: str, data_a: bytes, name_b: str, data_b: bytes):
    """Read both uploads. Refuses the pair, before any text extraction, when it exceeds the engine's document limit.

    Document units: one per text file, one per PDF page (each page is a separate audit document). The limit is
    MAX_DOCUMENTS across both files, while each PDF is still limited to MAX_PDF_PAGES pages.
    """
    units = []
    for name, data in ((name_a, data_a), (name_b, data_b)):
        if len(data) > MAX_FILE_BYTES or not name.lower().endswith(SUPPORTED_EXTENSIONS):
            units.append(None)                       # ingest_upload reports the reason itself
        elif name.lower().endswith(".pdf"):
            units.append(pdf_page_count(data))
        else:
            units.append(1)
    known = [u for u in units if u is not None]
    if (len(known) == 2 and all(u <= MAX_PDF_PAGES for u in units) and sum(units) > MAX_DOCUMENTS):
        res_a, res_b = IngestResult(filename=name_a), IngestResult(filename=name_b)
        _issue(res_a, "DOCUMENT_LIMIT_EXCEEDED",
               f"Together the two files make {sum(units)} audit documents ({name_a}: {units[0]}, {name_b}: {units[1]}). "
               f"Each text file counts as 1 and each PDF page counts as 1. The limit is {MAX_DOCUMENTS} in total. "
               f"Nothing was audited. Use fewer PDF pages (for example 6 + 6) and run the audit again.")
        return res_a, res_b
    return ingest_upload(name_a, data_a), ingest_upload(name_b, data_b)


def split_document_name(name: str):
    """'plan.pdf:page-3' -> ('plan.pdf', 3); 'spec.md' -> ('spec.md', None)."""
    if PAGE_SEP in name:
        base, _, page = name.rpartition(PAGE_SEP)
        if page.isdigit():
            return base, int(page)
    return name, None


def unique_names(name_a: str, name_b: str):
    """Keep the two uploads apart when they share a file name.

    The suffix goes before the extension ("spec (A).md"), so the extension still selects the reader.
    """
    if name_a != name_b:
        return name_a, name_b

    def tag(name, label):
        dot = name.rfind(".")
        return f"{name[:dot]} ({label}){name[dot:]}" if dot > 0 else f"{name} ({label})"

    return tag(name_a, "A"), tag(name_b, "B")


def input_fingerprint(use_example: bool, name_a: str, data_a: bytes, name_b: str, data_b: bytes, tolerance_pct: float) -> str:
    """Identity of one audit request: the CONTENT of both files, their names and slots, and the settings.

    Two uploads with the same file name but different content give different fingerprints. A change of
    the allowed difference or of the example switch changes it too.
    """
    h = hashlib.sha256()
    for part in (b"specforge-input-v1", b"example" if use_example else b"upload",
                 name_a.encode("utf-8", "replace"), hashlib.sha256(data_a).digest(),
                 name_b.encode("utf-8", "replace"), hashlib.sha256(data_b).digest(),
                 repr(float(tolerance_pct)).encode()):
        h.update(len(part).to_bytes(8, "big")); h.update(part)
    return h.hexdigest()
