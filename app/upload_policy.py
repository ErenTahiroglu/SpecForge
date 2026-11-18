"""Which file types the Streamlit web interface accepts. One policy, used by every upload control and by the run gate.

Default: PDF upload in the web interface is OFF. Only Markdown, plain text and CSV are accepted, so a default run of
app/ui.py never passes an uploaded PDF to the PDF reader (pypdf).

Local opt-in: start the app with the environment variable SPECFORGE_ENABLE_LOCAL_PDF_UI set to exactly "1", for example
    SPECFORGE_ENABLE_LOCAL_PDF_UI=1 python -m streamlit run app/ui.py
Any other value, or no value, keeps PDF off. Do not set it on a public deployment. On Streamlit Community Cloud,
root-level secrets are also exposed as environment variables, so do not add this name as a Cloud secret either.

This module does not import the PDF reader. The PDF engine itself (app.ingest) is unchanged and stays available to
local code and tests. The JSON API and the MCP tool accept text only and are not affected.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

PDF_UI_FLAG = "SPECFORGE_ENABLE_LOCAL_PDF_UI"
TEXT_UPLOAD_EXTENSIONS = (".md", ".txt", ".csv")
PDF_EXTENSION = ".pdf"
PDF_DISABLED_CODE = "PDF_DISABLED_IN_WEB_UI"
PDF_DISABLED_MESSAGE = ("PDF upload is disabled in the public demo; use Markdown, TXT or CSV. The PDF engine remains "
                        "available only in explicitly opted-in local operation.")


def pdf_ui_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the flag is exactly "1" (no case folding, no other truthy spellings)."""
    env = os.environ if environ is None else environ
    return env.get(PDF_UI_FLAG) == "1"


def ui_extensions(pdf_enabled: bool) -> tuple:
    """Extensions the upload controls accept, lowercase and with the dot."""
    return TEXT_UPLOAD_EXTENSIONS + ((PDF_EXTENSION,) if pdf_enabled else ())


def ui_formats_text(pdf_enabled: bool) -> str:
    if pdf_enabled:
        return ("Markdown (.md), plain text (.txt), CSV (.csv) and text-based PDF (.pdf, 10 pages or fewer per file; "
                "at most 12 pages and text files in total across both uploads). PDF is on because this local run set "
                f"{PDF_UI_FLAG}=1. Scanned PDFs, images, drawings and other formats are not supported.")
    return ("Markdown (.md), plain text (.txt) and CSV (.csv). PDF upload is off in this web interface. "
            "Scanned documents, images, drawings and other formats are not supported.")


def is_pdf_name(name: str) -> bool:
    """Any case of the .pdf extension (.pdf, .PDF, .Pdf ...)."""
    return name.casefold().endswith(PDF_EXTENSION)


def refused_pdf_names(names, pdf_enabled: bool) -> list:
    """The names that this policy refuses. Empty when PDF is enabled or no name is a PDF."""
    if pdf_enabled:
        return []
    return [name for name in names if is_pdf_name(name)]
