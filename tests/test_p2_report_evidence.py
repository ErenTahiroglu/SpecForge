"""#55: a coverage gap built from two rows must show BOTH original files, locations and excerpts, in the interface and in the
Markdown export. Two files often both have a line 1, so a bare line number is not evidence. Core JSON keys are unchanged.
"""
import copy
import json
from pathlib import Path

import pytest

from app.core import audit_documents
from app.report import build_view, render_markdown

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"
LABELS = {"a.md": "Document A", "b.md": "Document B"}


def md_of(result, labels=None):
    view = build_view(result, labels or LABELS)
    return view, render_markdown(view, "2026-11-19 00:00 UTC")


def gap(view, code):
    return [i for i in view.coverage if i.code == code]


DIM = {"a.md": "MR-27 | length | 100 m\n", "b.md": "MR-27 | length | 100 kg\n"}
UNKNOWN_KIND = {"a.md": "MR-27 | temperature uncertainty | 1 degC\n", "b.md": "MR-27 | temperature uncertainty | 274.15 K\n"}
IVA = {"a.md": "MR-27 | temperature | 5 delta_degC\n", "b.md": "MR-27 | temperature | 278.15 K\n"}


@pytest.mark.parametrize("docs,code,first,second", [
    (DIM, "DIMENSION_MISMATCH", "MR-27 | length | 100 m", "MR-27 | length | 100 kg"),
    (UNKNOWN_KIND, "TEMPERATURE_SEMANTICS", "MR-27 | temperature uncertainty | 1 degC", "MR-27 | temperature uncertainty | 274.15 K"),
    (IVA, "TEMPERATURE_SEMANTICS", "MR-27 | temperature | 5 delta_degC", "MR-27 | temperature | 278.15 K"),
])
def test_two_sided_gap_keeps_both_files_locations_and_excerpts(docs, code, first, second):
    r = audit_documents(docs)
    view, md = md_of(r)
    item = gap(view, code)[0]
    assert [e["document"] for e in item.evidence] == ["a.md", "b.md"]
    assert [e["excerpt"] for e in item.evidence] == [first, second]
    assert all("line 1" in e["location"] for e in item.evidence)
    assert "a.md" in item.location and "b.md" in item.location                      # equal line numbers, but the files are named
    lines = [l for l in md.splitlines() if l.strip().startswith("- ") and "line 1" in l]
    assert any("a.md" in l and first.replace("|", "|") in l for l in lines), md
    assert any("b.md" in l and second in l for l in lines), md                     # the second excerpt is not dropped
    assert md.count(second) >= 1 and md.count(first) >= 1


def test_json_keys_and_gap_counts_are_unchanged():
    r = audit_documents(DIM)
    w = [w for w in r["warnings"] if w["code"] == "DIMENSION_MISMATCH"][0]
    assert set(w) == {"code", "tag", "field", "reference", "other"}
    assert w["reference"]["excerpt"] == "MR-27 | length | 100 m" and w["other"]["excerpt"] == "MR-27 | length | 100 kg"
    view, md = md_of(r)
    assert view.metrics["not_compared"] == len(view.coverage) and f"({view.metrics['not_compared']} items not compared)" in md
    assert r["coverage_complete"] is False and view.coverage_complete is False


def test_pdf_pages_are_named_with_the_file_and_the_page():
    r = audit_documents({"x.pdf:page-1": "MR-27 | length | 100 m\n", "y.pdf:page-3": "MR-27 | length | 100 kg\n"})
    view, md = md_of(r, {"x.pdf": "Document A", "y.pdf": "Document B"})
    item = gap(view, "DIMENSION_MISMATCH")[0]
    assert item.evidence[0]["document"] == "x.pdf:page-1" and "page 1" in item.evidence[0]["location"]
    assert item.evidence[1]["document"] == "y.pdf:page-3" and "page 3" in item.evidence[1]["location"]
    plain = md.replace("\\", "")                               # the export escapes ":" and "-" for Markdown
    assert "x.pdf:page-1" in plain and "y.pdf:page-3" in plain and "not a visual line" in plain


def test_hostile_and_bidi_names_are_escaped_and_revealed_in_markdown():
    hostile = "<img src=x onerror=alert(1)>[l](http://example.invalid)\u202egpj.md"
    r = audit_documents({hostile: DIM["a.md"], "b.md": DIM["b.md"]})
    view, md = md_of(r, {hostile: "Document A", "b.md": "Document B"})
    assert "<img" not in md.replace("\\<img", "") and "](http" not in md      # markup stays escaped (a backslash before every "<")
    assert md.count("a.md") == 0 and "gpj.md" in md


def test_a_multi_line_or_markup_excerpt_is_never_cut_silently_or_made_active():
    r = audit_documents(DIM)
    r = copy.deepcopy(r)
    w = [w for w in r["warnings"] if w["code"] == "DIMENSION_MISMATCH"][0]
    w["reference"]["excerpt"] = "first part\u2028second `part` <script>alert(1)</script> [x](http://example.invalid)"
    view, md = md_of(r)
    assert "second" in md and "\u2028" not in md and "\u23ce" in md                 # visible line-break marker
    for line in md.splitlines():
        if "second" in line:
            assert line.count("`") >= 2                                             # inside a code span


def test_repeated_rows_are_listed_one_per_record_with_their_files():
    docs = {"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.09 bar\nP-1 | pressure | 9.91 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}
    view, md = md_of(audit_documents(docs, 0.01))
    item = gap(view, "DUPLICATE_IDENTIFIER")[0]
    assert [e["excerpt"] for e in item.evidence] == ["P-1 | pressure | 10 bar", "P-1 | pressure | 10.09 bar", "P-1 | pressure | 9.91 bar"]
    assert [e["location"] for e in item.evidence] == ["a.md, line 1", "a.md, line 2", "a.md, line 3"] or all("a.md" in e["document"] for e in item.evidence)
    for excerpt in ("10 bar", "10.09 bar", "9.91 bar"):
        assert excerpt in md


def test_consistent_repeat_note_no_longer_says_the_first_row_represents_the_file():
    docs = {"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.02 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}
    view, md = md_of(audit_documents(docs, 0.01))
    assert view.notes and view.coverage_complete is True
    assert "first row represents" not in md and "first row represents" not in view.notes[0].summary
    assert "lowest and highest" in view.notes[0].summary


# ---- positive controls: one-sided items and ordinary cards are unchanged ------------------------------------------------
def test_one_sided_unmatched_row_still_renders_on_one_line():
    r = audit_documents({"a.md": "MR-27 | length | 100 m\nMR-28 | length | 5 m\n", "b.md": "MR-27 | length | 100 m\n"})
    view, md = md_of(r)
    item = gap(view, "UNMATCHED_RECORD")[0]
    assert item.location == "a.md, line 2" and not getattr(item, "evidence", None)
    assert "- **UNMATCHED_RECORD**" in md and "MR-28 | length | 5 m" in md


def test_a_normal_conflict_card_and_equivalent_pair_render_as_before():
    r = audit_documents({"a.md": "P-101 | pressure | 10 bar\nP-101 | flow | 12 L/min\n", "b.md": "P-101 | pressure | 900 kPa\nP-101 | flow | 0.0002 m^3/s\n"})
    view, md = md_of(r)
    assert len(view.conflicts) == 1 and len(view.equivalents) == 1 and not view.coverage
    assert "10 bar" in md and "900 kPa" in md and "Every supported row found was compared." in md


# ---- the interface -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("view_name", ["Overview", "Detailed evidence"])
def test_streamlit_shows_both_files_and_both_excerpts_for_a_two_sided_gap(view_name):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", DIM["a.md"].encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", DIM["b.md"].encode(), "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    at.session_state["view_pref"] = view_name
    at.run()
    assert not at.exception
    code_text = "\n".join(c.value for c in at.code)
    page_text = " ".join(m.value for m in at.markdown)
    assert "MR-27 | length | 100 m" in code_text and "MR-27 | length | 100 kg" in code_text
    assert "a.md" in page_text and "b.md" in page_text
    exported = json.loads(at.session_state["audit"]["json"])
    assert exported["coverage_complete"] is False


# ---- the Notes section of the Markdown report (owner review of PR #59) --------------------------------------------------------
def notes_section(md):
    """Only the text of '## Notes (do not reduce coverage)', up to the next heading."""
    assert "## Notes (do not reduce coverage)" in md
    return md.split("## Notes (do not reduce coverage)", 1)[1].split("\n## ", 1)[0]


def test_consistent_repeats_list_every_original_row_in_the_markdown_notes_section():
    docs = {"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.02 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}
    view, md = md_of(audit_documents(docs, 0.01))
    notes = notes_section(md)                 # the comparison table elsewhere also shows a row: only this section counts
    records = [l for l in notes.splitlines() if l.startswith("  - ")]
    assert len(records) == 2
    assert records[0] == "  - a.md, line 1: `P-1 | pressure | 10 bar`"
    assert records[1] == "  - a.md, line 2: `P-1 | pressure | 10.02 bar`"
    assert "- **DUPLICATE_IDENTIFIER**" in notes and "lowest and highest" in notes


def test_three_consistent_repeats_keep_their_own_lines_and_files():
    docs = {"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.01 bar\nP-1 | pressure | 10.02 bar\n",
            "b.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.03 bar\n"}
    view, md = md_of(audit_documents(docs, 0.01))
    records = [l for l in notes_section(md).splitlines() if l.startswith("  - ")]
    assert [r.split(":")[0] for r in records] == ["  - a.md, line 1", "  - a.md, line 2", "  - a.md, line 3", "  - b.md, line 1", "  - b.md, line 2"]


def test_note_evidence_is_escaped_fenced_and_never_cut_by_a_line_break():
    r = copy.deepcopy(audit_documents({"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.02 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}, 0.01))
    dup = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"][0]
    dup["excerpts"][1] = "first part\u2028second `part` <script>alert(1)</script> [x](http://example.invalid) \u202e"
    dup["documents"][0] = "<img src=x onerror=alert(1)>.md"
    view, md = md_of(r, {"a.md": "Document A", "b.md": "Document B"})
    records = [l for l in notes_section(md).splitlines() if l.startswith("  - ")]
    assert len(records) == 2 and "\u2028" not in notes_section(md)
    assert "<img" not in records[0].replace("\\<img", "") and records[0].startswith("  - \\<img")
    assert "\u23ce" in records[1] and "second" in records[1]                 # the break is marked, nothing after it is lost
    assert "``" in records[1] or records[1].count("`") >= 4                  # the backtick in the text needs a longer fence
    assert "[x](http" in records[1]                                          # inside a code span, so it is not a link


def test_a_single_row_note_keeps_its_one_line_form():
    from app.report import CoverageItem
    view, _ = md_of(audit_documents({"a.md": "P-1 | pressure | 10 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}))
    view.notes = [CoverageItem("SOME_NOTE", "One row note", "a.md, line 1", "P-1 | pressure | 10 bar", informational=True)]
    md = render_markdown(view, "2026-11-19 00:00 UTC")
    assert "- **SOME_NOTE** — One row note (a.md, line 1)" in notes_section(md)
    assert not [l for l in notes_section(md).splitlines() if l.startswith("  - ")]


def test_notes_do_not_change_verdict_counters_or_json():
    docs = {"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10.02 bar\n", "b.md": "P-1 | pressure | 10 bar\n"}
    r = audit_documents(docs, 0.01)
    view, md = md_of(r)
    assert view.coverage_complete is True and view.metrics["not_compared"] == 0 and r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert "Coverage complete: yes" in md and "## Not compared (coverage)\n\nEvery supported row found was compared." in md
    assert [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"][0]["consistent"] is True
