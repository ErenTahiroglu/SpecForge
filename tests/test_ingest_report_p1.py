"""Tests for app.ingest (file reading) and app.report (verdicts, cards, Markdown report)."""
import pytest

from app.core import audit_documents
from app.ingest import MAX_FILE_BYTES, ingest_upload, split_document_name, unique_names
from app.report import (
    VERDICT_CONFLICTS, VERDICT_MATCH, VERDICT_NONE, VERDICT_PARTIAL, ReviewCard, build_view,
    describe_location, render_markdown, table_rows,
)

SPEC = "# Spec\n\n| TAG | property | required value |\n| --- | --- | --- |\n| P-101 | pressure | 10 bar |\n| P-101 | flow | 12 L/min |\n| V-201 | diameter | 25 mm |\n"
BOM = "# BOM\n\n| TAG | property | observed value |\n| --- | --- | --- |\n| P-101 | pressure | 900 kPa |\n| P-101 | flow | 0.0002 m^3/s |\n| V-201 | diameter | 0.025 m |\n"


def minimal_pdf(pages):
    """Build a small text PDF by hand. `pages` is a list of line lists (one list per page)."""
    objs = []
    n_pages = len(pages)
    kids = " ".join("%d 0 R" % (4 + 2 * i) for i in range(n_pages))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    for i, lines in enumerate(pages):
        content = "BT /F1 11 Tf 14 TL 50 740 Td " + " ".join(
            "(%s) Tj T*" % ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines) + " ET"
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i))
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, body)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
    out += b"".join(("%010d 00000 n \n" % o).encode() for o in offsets)
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode()
    return out


# ---------------- ingest ----------------

@pytest.mark.parametrize("name", ["a.md", "a.txt", "a.csv", "A.MD"])
def test_text_formats_are_read(name):
    r = ingest_upload(name, b"P-1 | pressure | 5 bar\n")
    assert r.issues == [] and list(r.documents) == [name]


def test_utf8_bom_is_removed():
    r = ingest_upload("a.md", b"\xef\xbb\xbfP-1 | pressure | 5 bar")
    assert r.documents["a.md"].startswith("P-1")


@pytest.mark.parametrize("name,data,code", [
    ("a.docx", b"x", "UNSUPPORTED_FORMAT"),
    ("a.png", b"x", "UNSUPPORTED_FORMAT"),
    ("a.md", b"\xff\xfe\x00\x00bad", "NOT_TEXT"),
    ("a.md", "ölçü".encode("cp1254") + b" 1", "NOT_UTF8"),
    ("a.pdf", b"not a pdf", "PDF_UNREADABLE"),
])
def test_unusable_files_give_an_explicit_issue_and_no_document(name, data, code):
    r = ingest_upload(name, data)
    assert r.documents == {} and [i["code"] for i in r.issues] == [code]
    assert r.issues[0]["message"]


def test_oversize_file_is_rejected():
    r = ingest_upload("a.txt", b"x" * (MAX_FILE_BYTES + 1))
    assert r.documents == {} and r.issues[0]["code"] == "FILE_TOO_LARGE"


def test_text_longer_than_line_limit_is_rejected_not_truncated():
    r = ingest_upload("a.txt", b"x\n" * 5001)
    assert r.documents == {} and r.issues[0]["code"] == "TEXT_TOO_LONG"


def test_text_pdf_pages_become_page_level_documents():
    pdf = minimal_pdf([["Title", "P-101 | pressure | 10 bar"], ["", "", "V-201 | diameter | 25 mm"]])
    r = ingest_upload("plan.pdf", pdf)
    assert r.issues == [] and sorted(r.documents) == ["plan.pdf:page-1", "plan.pdf:page-2"]
    assert "P-101 | pressure | 10 bar" in r.documents["plan.pdf:page-1"]
    assert split_document_name("plan.pdf:page-2") == ("plan.pdf", 2)


def test_pdf_page_without_text_is_reported_as_not_audited():
    pdf = minimal_pdf([["P-101 | pressure | 10 bar"], []])
    r = ingest_upload("scan.pdf", pdf)
    assert list(r.documents) == ["scan.pdf:page-1"]
    assert [(i["code"], i["page"]) for i in r.issues] == [("PDF_PAGE_NO_TEXT", 2)]
    assert "OCR" in r.issues[0]["message"]


def test_pdf_over_page_limit_is_rejected():
    r = ingest_upload("big.pdf", minimal_pdf([["x"]] * 11))
    assert r.documents == {} and r.issues[0]["code"] == "PDF_TOO_MANY_PAGES"


def test_pdf_evidence_keeps_page_and_extracted_line_and_is_honest_about_it():
    pdf = minimal_pdf([["header"], ["intro", "P-101 | pressure | 10 bar"]])
    docs = ingest_upload("spec.pdf", pdf).documents
    r = audit_documents({**docs, "bom.md": "P-101 | pressure | 900 kPa"})
    ref = r["findings"][0]["reference"]
    assert ref["document"] == "spec.pdf:page-2" and ref["excerpt"] == "P-101 | pressure | 10 bar"
    loc = describe_location(ref["document"], ref["line"])
    assert loc.startswith("page 2, line 2") and "not a visual line" in loc
    assert describe_location("bom.md", 4) == "line 4"


# ---------------- report ----------------

def view_for(a, b, tol=0.01, issues=None, name_a="spec.md", name_b="bom.md"):
    result = audit_documents({name_a: a, name_b: b}, tol)
    return result, build_view(result, {name_a: "Document A", name_b: "Document B"}, issues, [name_a, name_b])


def test_example_documents_give_one_conflict_and_two_verified_equivalences():
    result, v = view_for(SPEC, BOM)
    assert v.verdict == VERDICT_CONFLICTS
    assert v.metrics == {"rows_read": 6, "compared": 3, "conflicts": 1, "equivalent": 2, "not_compared": 0}
    c = v.conflicts[0]
    assert c.title == "P-101 · pressure" and c.kind == "conflict"
    assert [(e.document, e.location, e.excerpt) for e in c.original] == [
        ("spec.md", "line 5", "| P-101 | pressure | 10 bar |"), ("bom.md", "line 5", "| P-101 | pressure | 900 kPa |")]
    assert "1,000,000" in c.explanation and "900,000" in c.explanation and "10 %" in c.explanation
    assert "cannot tell which value is correct" in c.explanation and "gauge or absolute" in c.explanation
    assert sorted(e.title for e in v.equivalents) == ["P-101 · flow", "V-201 · diameter"]
    assert c.suggestion is None and c.decision is None  # reserved for P2


def test_explanations_make_no_claims_beyond_the_data():
    _, v = view_for(SPEC, BOM)
    text = (v.conflicts[0].explanation + v.headline + v.detail).lower()
    for banned in ("undersized", "unsafe", "safe", "fail", "certif", "compliant", "pass", "should be", "correct value is"):
        assert banned not in text.replace("cannot tell which value is correct", "").replace("does not certify", "").replace("not a certification", "")


def test_verdict_nothing_compared_is_never_success():
    _, v = view_for("prose only", "more prose")
    assert v.verdict == VERDICT_NONE and "not a successful audit" in v.detail.lower()


def test_verdict_partial_coverage_when_something_was_not_compared():
    _, v = view_for("P-1 | pressure | 5 bar\nP-2 | pressure | 5 lollipops", "P-1 | pressure | 5 bar\nP-2 | pressure | 5 bar")
    assert v.verdict == VERDICT_PARTIAL and v.metrics["not_compared"] == 2
    assert sorted(i.code for i in v.coverage) == ["UNMATCHED_RECORD", "UNSUPPORTED_UNIT"]  # the B row has no usable partner
    assert "incomplete" in v.headline


def test_verdict_partial_when_an_input_issue_exists():
    _, v = view_for("P-1 | pressure | 5 bar", "P-1 | pressure | 5 bar",
                    issues=[{"code": "PDF_PAGE_NO_TEXT", "filename": "x.pdf", "message": "x.pdf, page 2: no text"}])
    assert v.verdict == VERDICT_PARTIAL


def test_verdict_clean_only_when_everything_was_compared():
    _, v = view_for("P-1 | pressure | 5 bar", "P-1 | pressure | 500 kPa")
    assert v.verdict == VERDICT_MATCH and "not a certification" in v.detail


def test_unmatched_and_malformed_rows_appear_in_coverage_with_location():
    _, v = view_for("P-1 | pressure | 5 bar\nV-9 | length | 3 m\nP-3 | pressure | 10,5 bar", "P-1 | pressure | 5 bar")
    codes = sorted(i.code for i in v.coverage)
    assert codes == ["UNMATCHED_RECORD", "UNPARSED_ROW"]
    assert {i.location for i in v.coverage} == {"spec.md, line 2", "spec.md, line 3"}


def test_table_rows_have_both_sources():
    _, v = view_for(SPEC, BOM)
    row = table_rows(v.conflicts)[0]
    assert row["Item"] == "P-101 · pressure" and "10 bar" in row["First: text"] and "900 kPa" in row["Second: text"]
    assert "spec.md" in row["First: source"] and "line 5" in row["First: source"] and "bom.md" in row["Second: source"] and row["Kind"] == "Between documents"


def test_markdown_report_contains_all_evidence_and_limits():
    result, v = view_for(SPEC, BOM)
    md = render_markdown(v, "2026-11-06 20:00 UTC")
    for needle in ("# SpecForge audit report", "spec.md", "bom.md", "line 5", "| P-101 | pressure | 10 bar |",
                   "| P-101 | pressure | 900 kPa |", "1,000,000", "900,000", "10 %", "Verified equivalent",
                   "Limits of this report", "does not decide which value is correct", "12 L/min"):
        assert needle in md, needle
    assert '"findings"' not in md  # human-readable, not a JSON dump


def test_markdown_report_for_incomplete_audit_lists_gaps():
    _, v = view_for("P-1 | pressure | 5 lollipops", "P-1 | pressure | 5 bar")
    md = render_markdown(v)
    assert "Nothing could be compared" in md and "UNSUPPORTED_UNIT" in md and "not a successful audit" in md.lower()


def test_review_card_has_p2_slots():
    card = ReviewCard("writing", "Sentence length", [], "Long sentence", "Why", suggestion="Short.", decision="accepted")
    assert card.suggestion == "Short." and card.decision == "accepted"


# ---------------- same file name in both slots (defect found by the browser run) ----------------

def test_same_file_name_in_both_slots_keeps_extension_so_both_are_still_readable():
    a, b = unique_names("spec.md", "spec.md")
    assert (a, b) == ("spec (A).md", "spec (B).md")
    assert ingest_upload(a, b"P-1 | pressure | 5 bar").issues == []
    assert ingest_upload(b, b"P-1 | pressure | 5 bar").issues == []
    assert unique_names("a.pdf", "b.pdf") == ("a.pdf", "b.pdf")
    assert unique_names("data.v2.csv", "data.v2.csv")[0] == "data.v2 (A).csv"
    assert unique_names("noext", "noext") == ("noext (A)", "noext (B)")


def test_si_values_are_shown_with_engineering_symbols():
    _, v = view_for(SPEC, BOM)
    text = v.conflicts[0].explanation
    assert "1,000,000 Pa and 900,000 Pa" in text and "kilogram" not in text
    flow = [c for c in v.equivalents if c.title.endswith("flow")][0].explanation
    assert "m³/s" in flow and "second **" not in flow


def test_equivalent_cards_have_a_plain_one_line_summary():
    _, v = view_for(SPEC, BOM)
    assert sorted(c.summary for c in v.equivalents) == ["12 L/min = 0.0002 m^3/s", "25 mm = 0.025 m"]
