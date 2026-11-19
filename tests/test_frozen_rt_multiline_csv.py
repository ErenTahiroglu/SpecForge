"""FROZEN-RT-002 (#61): a measurement-shaped physical line INSIDE a multiline quoted CSV field is part of a note, not a row.

Policy under test (README, "Source format and limits"): in a .csv file the text is first split into logical records with a small
quote-aware scan (same rules as Python's csv module: a quote opens a quoted field only at the start of a field, `""` is an escaped
quote, a line break inside quotes belongs to the field). No physical line inside a quoted field is passed to the row grammar.
Quoted engineering fields stay unsupported: when such a region holds measurement-like text, or the quote never closes, the opening
line is reported as one UNPARSED_ROW (detail MULTILINE_QUOTED_FIELD or UNCLOSED_QUOTE, with its end line) and coverage is
incomplete. A note without measurement-like text is silent, as before. Python's csv.reader is used here only as an independent oracle.
"""
import csv
import io
import itertools
import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import MAX_LINE_CHARS, audit_documents
from app.report import build_view, render_markdown

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"
FORGED = "P-9,pressure,999 bar"
GOOD_A = "P-1,pressure,10 bar\n"
GOOD_B = "P-1,pressure,10 bar\n"


def oracle_continuation_lines(text):
    """Physical line numbers (1-based) that the csv module reads as part of a record that began on an earlier line."""
    reader = csv.reader(io.StringIO(text, newline=""))
    lines, previous = set(), 0
    for _ in reader:
        for number in range(previous + 2, reader.line_num + 1):
            lines.add(number)
        previous = reader.line_num
    return lines


def audit(a, b=GOOD_B, tol=0.01, names=("a.csv", "b.csv")):
    return audit_documents({names[0]: a, names[1]: b}, tol)


def unparsed(r):
    return [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"]


def assert_no_forgery(r):
    blob = json.dumps({"matches": r["matches"], "findings": r["findings"], "unmatched": r["unmatched"]})
    assert "P-9" not in blob and "999" not in blob


# ---- the reported pattern -----------------------------------------------------------------------------------------------------
NOTE = f'N-1,note,"see the list\n{FORGED}\nend of note",x\n'


def test_the_oracle_agrees_that_the_forged_line_is_inside_one_field():
    assert oracle_continuation_lines(GOOD_A + NOTE) == {3, 4}
    assert list(csv.reader(io.StringIO(NOTE, newline="")))[0][2] == f"see the list\n{FORGED}\nend of note"


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("swap", [False, True])
def test_a_measurement_line_inside_a_quoted_note_is_never_an_observation(eol, swap):
    a = (GOOD_A + NOTE + "P-2,length,5 m\n").replace("\n", eol)
    b = (GOOD_B + f"{FORGED}\n" + "P-2,length,5 m\n").replace("\n", eol)
    records, warnings, coverage = core._parse("a.csv", a)
    assert all(rec.tag != "P-9" for rec in records) and [rec.tag for rec in records] == ["P-1", "P-2"]
    docs = {"b.csv": b, "a.csv": a} if swap else {"a.csv": a, "b.csv": b}
    r = audit_documents(docs, 0.01)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert not any(f["reference"]["tag"] == "P-9" for f in r["findings"] + r["matches"])
    blocks = [w for w in r["warnings"] if w.get("detail") == "MULTILINE_QUOTED_FIELD"]
    assert len(blocks) == 1 and blocks[0]["document"] == "a.csv" and blocks[0]["line"] == 2 and blocks[0]["end_line"] == 4
    assert blocks[0]["excerpt"].startswith("N-1,note,") and "999" not in blocks[0]["excerpt"]


def test_the_forged_observation_is_absent_from_records_cards_and_exports():
    r = audit(GOOD_A + NOTE)                                          # the forged text exists only inside a.csv's note
    assert r["records"] == 2 and r["comparisons"] == 1                # P-1 in a.csv and in b.csv
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    assert FORGED not in json.dumps(r) and "999" not in json.dumps(r)
    assert_no_forgery(r)
    view = build_view(r, {"a.csv": "Document A", "b.csv": "Document B"})
    md = render_markdown(view, "2026-11-19 00:00 UTC")
    assert not any("P-9" in e.excerpt for card in view.conflicts + view.equivalents for e in card.original)
    assert "Coverage complete: no" in md and "UNPARSED_ROW" in md and "999" not in md and "P-9" not in md


@pytest.mark.parametrize("note", [
    f'N-1,note,"a ""quoted"" word\n{FORGED}\nend",x\n',                         # escaped quotes
    f'N-1,note,"has, commas, and | pipes\n{FORGED}\nend",x\n',                  # delimiters inside the field
    f'N-1,note,"line one\n| P-9 | pressure | 999 bar |\nend",x\n',                  # a pipe row inside the note
    f'N-1,note,"line one\nP-9 | pressure | 999 bar\nend",x\n',
    f'N-1,"first cell note\n{FORGED}\nend",y,z\n',                                # quote opens in the FIRST field
    f'N-1,note,"x\n\n{FORGED}\n\nend",x\n',                                       # blank lines inside the field
    f'N-1,note,"x\n{FORGED}"\n',                                                  # the closing quote ends the continuation line
    f'N-1,note,"x\n   {FORGED}  \nend",x\n',
])
def test_variants_of_the_quoted_note_never_make_an_observation_and_are_incomplete(note):
    text = GOOD_A + note + "P-2,length,5 m\n"
    records, warnings, coverage = core._parse("a.csv", text)
    assert [rec.tag for rec in records] == ["P-1", "P-2"]
    assert set(rec.line for rec in records).isdisjoint(oracle_continuation_lines(text))
    r = audit(text, GOOD_B + "P-2,length,5 m\n")
    assert r["coverage_complete"] is False and r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE"
    assert len(unparsed(r)) == 1 and coverage["rows_unparsed"] == 1


# ---- quiet notes, ambiguity, unclosed quotes -----------------------------------------------------------------------------------
def test_a_note_without_measurement_text_stays_silent_and_the_audit_stays_complete():
    note = 'N-1,note,"just words\nmore words, with a comma\nthe end",x\n'
    r = audit(GOOD_A + note + "P-2,length,5 m\n", GOOD_B + "P-2,length,5 m\n")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and not unparsed(r)


@pytest.mark.parametrize("tail", ['N-1,note,"never closed\nP-2,length,5 m\n', 'N-1,note,"never closed\n', 'N-1,note,"never closed'])
def test_an_unclosed_quote_is_a_visible_gap_even_without_measurement_text(tail):
    text = GOOD_A + tail
    records, warnings, _ = core._parse("a.csv", text)
    assert [rec.tag for rec in records] == ["P-1"]
    (w,) = [w for w in warnings if w["code"] == "UNPARSED_ROW"]
    assert w["detail"] == "UNCLOSED_QUOTE" and w["line"] == 2
    r = audit(text)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"


@pytest.mark.parametrize("text", ['P-1,pressure,10 bar,"trailing\nP-2,length,5 m",x\n', 'N-1,note,"a"b\nP-2,length,5 m\n', 'N-1,"note"x,"y\nP-3,length,5 m",z\n'])
def test_malformed_quotes_follow_the_oracle_and_never_hide_a_row_silently(text):
    records, warnings, _ = core._parse("a.csv", text)
    continuation = oracle_continuation_lines(text)
    assert set(rec.line for rec in records).isdisjoint(continuation)
    if continuation:
        assert any(w["code"] == "UNPARSED_ROW" for w in warnings)        # measurement-like continuation: reported, not dropped


def test_a_quote_in_the_middle_of_an_unquoted_field_is_literal_like_the_csv_module():
    text = 'P-1,pressure,10 bar\nN-1,5" pipe,x\nP-2,length,5 m\n'
    assert oracle_continuation_lines(text) == set()
    records, _, _ = core._parse("a.csv", text)
    assert [rec.tag for rec in records] == ["P-1", "P-2"]


# ---- regressions: quote-free CSV, Markdown/TXT, the earlier one-line quoted-row controls ---------------------------------------
def test_quote_free_csv_and_pipe_rows_are_unchanged():
    r = audit("P-1,pressure,10 bar\nP-2,flow,12 L/min\n", "P-1,pressure,900 kPa\nP-2,flow,0.0002 m^3/s\n")
    assert r["verdict"] == "CONFLICTS_FOUND" and r["comparisons"] == 2 and r["coverage_complete"] is True
    md = audit_documents({"a.md": 'X-1 | length | 1 m\nNote "open\nX-1 | length | 9 m\n', "b.md": "X-1 | length | 1 m\n"})
    assert md["records"] == 3                                       # a quote in a Markdown file is not CSV quoting
    txt = audit_documents({"a.txt": '"note\nX-1 | length | 1 m\n', "b.txt": "X-1 | length | 1 m\n"})
    assert txt["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"


def test_a_single_line_quoted_row_is_still_reported_as_before():
    r = audit('"P-1","pressure","10 bar"\nP-2,length,5 m\n', "P-1,pressure,10 bar\nP-2,length,5 m\n")
    assert r["coverage_complete"] is False and len(unparsed(r)) == 1
    assert "quoted fields" in unparsed(r)[0]["reason"] and "detail" not in unparsed(r)[0]


def test_balanced_quotes_on_one_line_do_not_open_a_block():
    records, warnings, _ = core._parse("a.csv", 'N-1,"a, b",x\nP-1,pressure,10 bar\n')
    assert [rec.tag for rec in records] == ["P-1"] and not warnings


# ---- bounds ---------------------------------------------------------------------------------------------------------------------
def test_a_very_long_line_inside_a_quoted_note_is_not_inspected_and_fails_closed():
    text = 'N-1,note,"start\n' + "x" * (MAX_LINE_CHARS + 50) + '\nend",x\n'
    r = audit(GOOD_A + text)
    assert r["coverage_complete"] is False and len(unparsed(r)) == 1


def test_one_warning_per_block_not_per_continuation_line():
    note = 'N-1,note,"start\n' + "\n".join(f"P-{i},pressure,{i} bar" for i in range(200)) + '\nend",x\n'
    records, warnings, coverage = core._parse("a.csv", GOOD_A + note)
    assert len(records) == 1 and len(warnings) == 1 and coverage["rows_unparsed"] == 1 and warnings[0]["end_line"] == 203


def test_document_limits_still_apply_to_csv():
    with pytest.raises(ValueError):
        audit_documents({"a.csv": "x\n" * 5001, "b.csv": GOOD_B})
    with pytest.raises(ValueError):
        audit_documents({"a.csv": "x" * 200001, "b.csv": GOOD_B})


def test_the_scan_is_linear_on_a_large_quoted_region():
    import time
    note = 'N-1,note,"' + "\n".join(["a note line with words and a number 12"] * 4000) + '"\n'
    started = time.perf_counter()
    core._parse("a.csv", note)
    assert time.perf_counter() - started < 2.0


# ---- property: the scan reproduces the csv module's record boundaries ------------------------------------------------------------
FRAGMENTS = ['P-1,pressure,10 bar', 'P-2,length,5 m', 'note', '"open', 'close"', '"a ""b"" c"', 'x,"y', 'z",w', '', '5" tall', FORGED, "| P-3 | length | 2 m |"]


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(FRAGMENTS), min_size=1, max_size=9), st.sampled_from(["\n", "\r\n"]))
def test_property_no_observation_comes_from_a_line_the_csv_module_reads_inside_a_record(parts, eol):
    text = eol.join(parts) + eol
    records, warnings, _ = core._parse("a.csv", text)
    inside = oracle_continuation_lines(text)
    assert set(rec.line for rec in records).isdisjoint(inside), (parts, inside)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.sampled_from(FRAGMENTS), min_size=2, max_size=7))
def test_property_a_note_region_with_a_measurement_never_yields_a_complete_verdict(parts):
    text = "\n".join(parts) + "\n"
    lines = text.split("\n")
    hidden = [n for n in oracle_continuation_lines(text) if n - 1 < len(lines) and (core.ENTRY.fullmatch(lines[n - 1]) or core.CSV_ENTRY.fullmatch(lines[n - 1]))]
    r = audit(text, GOOD_B)
    if hidden:
        assert r["coverage_complete"] is False


# ---- every entry point -----------------------------------------------------------------------------------------------------------
def test_fastapi_with_csv_names_agrees():
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.csv": GOOD_A + NOTE, "b.csv": GOOD_B}}).json()
    assert body["coverage_complete"] is False and body["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE"
    assert any(w.get("detail") == "MULTILINE_QUOTED_FIELD" for w in body["warnings"])


def test_the_mcp_tool_has_no_csv_grammar_and_is_unchanged():
    from app.mcp_server import compare_engineering_rows
    r = compare_engineering_rows(GOOD_A + NOTE, GOOD_B)
    assert r["coverage_complete"] is False                             # comma rows are not read there at all (README)


def test_streamlit_shows_no_green_result_and_exports_agree():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.csv", (GOOD_A + NOTE).encode(), "text/csv")
    at.get("file_uploader")[1].upload("b.csv", GOOD_B.encode(), "text/csv")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception and len(at.success) == 0
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NO_CONFLICTS_PARTIAL_COVERAGE", False)
    md = at.session_state["audit"]["markdown"]
    assert "Coverage complete: no" in md and "UNPARSED_ROW" in md and "999 bar" not in md
