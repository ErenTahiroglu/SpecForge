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
from dataclasses import dataclass, field

from .core import MAX_CHARS, MAX_LINES, split_lines

MAX_FILE_BYTES = 2_000_000
MAX_PDF_PAGES = 10
PAGE_SEP = ":page-"
TEXT_EXTENSIONS = (".md", ".txt", ".csv")
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + (".pdf",)
SUPPORTED_FORMATS_TEXT = (
    "Markdown (.md), plain text (.txt), CSV (.csv) and text-based PDF (.pdf, 10 pages or fewer). "
    "Scanned PDFs, images, drawings and other formats are not supported."
)


@dataclass
class IngestResult:
    filename: str
    documents: dict = field(default_factory=dict)  # audit name -> text
    issues: list = field(default_factory=list)     # dicts: code, filename, message, page (optional)
    kind: str = "unknown"


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


def _read_pdf(result, content):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            _issue(result, "PDF_ENCRYPTED", f"{result.filename}: the PDF is encrypted. Not audited.")
            return
        pages = list(reader.pages)
    except Exception as exc:
        _issue(result, "PDF_UNREADABLE", f"{result.filename}: the PDF cannot be read ({type(exc).__name__}). Not audited.")
        return
    if len(pages) > MAX_PDF_PAGES:
        _issue(result, "PDF_TOO_MANY_PAGES", f"{result.filename}: {len(pages)} pages. The limit is {MAX_PDF_PAGES}. Not audited.")
        return
    empty = []
    for number, page in enumerate(pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            _issue(result, "PDF_PAGE_UNREADABLE", f"{result.filename}, page {number}: text extraction failed ({type(exc).__name__}).", number)
            continue
        if not text.strip():
            empty.append(number)
            continue
        _accept_text(result, f"{result.filename}{PAGE_SEP}{number}", text)
    for number in empty:
        _issue(result, "PDF_PAGE_NO_TEXT",
               f"{result.filename}, page {number}: no extractable text (possibly a scan or an image). SpecForge does not use OCR. This page was not audited.",
               number)


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
