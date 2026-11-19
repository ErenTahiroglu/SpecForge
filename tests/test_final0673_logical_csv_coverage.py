"""FINAL0673-RT-001 (#63): a measurement-shaped LOGICAL CSV record that spans several physical lines must be reported, even when no
single physical line looks like a row.

Policy under test (README, "Source format and limits"): for a .csv file, every quoted multi-line region is one logical record. The
engine splits that record into fields with the csv module's quote rules (bounded, linear) and reports it as ONE UNPARSED_ROW
(detail MULTILINE_QUOTED_FIELD, line .. end_line) when it is measurement-shaped by the same cell rules as a one-line unsupported
row (a number, or a number and unit, from the third field on; a bare number after a unit field), or when a physical line of it is
measurement-like on its own, too long to inspect, or the quote never closes. A prose note stays silent. Quoted values are never
compared. Python's csv.reader is only an independent boundary oracle in these tests; the expectations below are written by hand
or built constructively, not computed with the production helpers.
"""
import csv
import io
import json
import random
import time
from pathlib import Path

import pytest

from app import core
from app.core import MAX_LINE_CHARS, audit_documents
from app.report import build_view, render_markdown

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"
A = "P-9,pressure,10 bar\n"
B = 'P-9,pressure,10 bar\nP-9,"pressure\n",999,bar\n'            # the reported fixture, literally
LABELS = {"a.csv": "Document A", "b.csv": "Document B"}


def oracle_records(text):
    """[(first_line, last_line, fields)] for every logical record, from the csv module (independent of the engine)."""
    reader = csv.reader(io.StringIO(text, newline=""))
    out, previous = [], 0
    for fields in reader:
        out.append((previous + 1, reader.line_num, fields))
        previous = reader.line_num
    return out


def multiline_spans(text):
    return [(a, b) for a, b, _ in oracle_records(text) if b > a]


def unparsed(r):
    return [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"]


def audit(a, b, tol=0.01):
    return audit_documents({"a.csv": a, "b.csv": b}, tol)


def assert_partial_with_one_warning(r, document, span):
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and r["status"] == "NO_DIFFERENCES_FOUND"
    (w,) = unparsed(r)
    assert (w["document"], w["line"], w["end_line"], w["detail"]) == (document, span[0], span[1], "MULTILINE_QUOTED_FIELD")
    assert r["coverage"][document]["rows_unparsed"] == 1


# ---- the reported fixture ---------------------------------------------------------------------------------------------------------
def test_the_oracle_reads_physical_lines_2_and_3_as_one_logical_record():
    assert oracle_records(B)[1] == (2, 3, ["P-9", "pressure\n", "999", "bar"])
    for line in ('P-9,"pressure', '",999,bar'):                         # the failure class: no single physical line looks like a row
        assert not core.ENTRY.fullmatch(line) and not core.CSV_ENTRY.fullmatch(line) and not core._looks_like_row(line, True)


@pytest.mark.parametrize("swap", [False, True])
def test_the_reported_fixture_is_partial_with_one_warning_on_lines_2_to_3(swap):
    docs = {"b.csv": B, "a.csv": A} if swap else {"a.csv": A, "b.csv": B}
    r = audit_documents(docs, 0.01)
    assert_partial_with_one_warning(r, "b.csv", (2, 3))
    assert r["comparisons"] == 1 and r["records"] == 2 and r["findings"] == []
    (m,) = r["matches"]
    assert {m["reference"]["excerpt"], m["other"]["excerpt"]} == {"P-9,pressure,10 bar"} and m["relative_difference"] == 0.0
    assert {m["reference"]["line"], m["other"]["line"]} == {1}
    assert "999" not in json.dumps({"m": r["matches"], "f": r["findings"], "u": r["unmatched"]})
    assert unparsed(r)[0]["excerpt"] == 'P-9,"pressure'                   # the opening line, verbatim; the span names the rest


def test_the_hidden_record_is_never_an_observation():
    records, warnings, coverage = core._parse("b.csv", B)
    assert [(r.line, r.tag, r.value) for r in records] == [(1, "P-9", 10.0)]
    assert coverage["rows_unparsed"] == 1 and len(warnings) == 1


def test_a_repeated_valid_comparison_stays_consistent_beside_the_hidden_record():
    r = audit(A + A, B + "P-9,pressure,10 bar\n")
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and len(unparsed(r)) == 1


# ---- variants: every layout of a measurement-shaped logical record -------------------------------------------------------------
FLAGGED = {
    "reported": 'P-9,"pressure\n",999,bar\n',
    "all fields quoted": '"P-9","pressure\n","999","bar"\n',
    "tag split": '"P-\n9",pressure,999,bar\n',
    "number split": 'P-9,pressure,"9\n99",bar\n',
    "unit split": 'P-9,pressure,999,"b\nar"\n',
    "value and unit in one quoted field": 'P-9,"pressure\n","999 bar"\n',
    "value and unit split over lines": 'P-9,pressure,"999\nbar"\n',
    "number in the fourth field after a unit": 'P-9,"pressure\n",bar,999\n',
    "escaped quote in the property": 'P-9,"pres""\nsure",999,bar\n',
    "comma inside the quoted property": 'P-9,"pressure,\nstatic",999,bar\n',
    "pipe row inside one quoted field": '"P-9 | pressure |\n999 bar"\n',
    "mixed comma and pipe text": 'P-9 | x,"y\n",999,bar\n',
    "two adjacent quoted fields": 'P-9,"a\n","999\nx",y\n',
    "leading spaces before the number": 'P-9,pressure,"  \n 999 bar"\n',
    "signed number": 'P-9,"pressure\n",-999,bar\n',
    "qualifier word cut by the line break": 'P-9,pressure,"ma\nx 999",bar\n',
    "unit word cut by the line break": 'P-9,pressure,"999 b\nar"\n',
    "qualifier word": 'P-9,"pressure\n",max 999,bar\n',
}
QUIET = {
    "prose note": 'N-1,note,"see item 3 on page 12\nand more, here",x\n',
    "split prose": 'N-1,"first part\nsecond part",third\n',
    "digits later in a long note": 'N-1,note,"call the office\nbetween 9 and 5 on weekdays",x\n',
    "count in a later field": 'N-1,note,"words\nmore words",x,y\n',
    "two quiet adjacent fields": 'N-1,"a\nb","c\nd",e\n',
}


@pytest.mark.parametrize("name", sorted(FLAGGED))
@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("swap", [False, True])
def test_every_measurement_shaped_logical_record_is_reported_once(name, eol, swap):
    block = FLAGGED[name].replace("\n", eol)
    text = ("P-9,pressure,10 bar" + eol) + block + ("P-2,length,5 m" + eol)
    spans = multiline_spans(text)
    assert len(spans) == 1, name                                     # the oracle sees exactly one multi-line logical record
    other = ("P-9,pressure,10 bar" + eol) + ("P-2,length,5 m" + eol)
    docs = {"b.csv": other, "a.csv": text} if swap else {"a.csv": text, "b.csv": other}
    records, warnings, coverage = core._parse("a.csv", text)
    assert [(r.tag, r.line) for r in records] == [("P-9", 1), ("P-2", spans[0][1] + 1)]
    r = audit_documents(docs, 0.01)
    assert_partial_with_one_warning(r, "a.csv", spans[0])
    assert r["comparisons"] == 2 and not r["findings"] and "999" not in json.dumps(r["matches"])


@pytest.mark.parametrize("name", sorted(QUIET))
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_a_note_without_a_measurement_shape_stays_quiet_and_the_rows_around_it_are_compared(name, eol):
    text = ("P-9,pressure,10 bar" + eol) + QUIET[name].replace("\n", eol) + ("P-2,length,5 m" + eol)
    assert len(multiline_spans(text)) == 1
    other = ("P-9,pressure,10 bar" + eol) + ("P-2,length,5 m" + eol)
    r = audit(text, other)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and r["comparisons"] == 2 and not unparsed(r)


def test_no_terminal_newline_and_a_record_at_the_very_end():
    text = 'P-9,pressure,10 bar\nP-9,"pressure\n",999,bar'                       # no final line break
    assert multiline_spans(text) == [(2, 3)]
    r = audit(A, text)
    assert_partial_with_one_warning(r, "b.csv", (2, 3))


def test_two_separate_logical_records_give_two_warnings_in_order():
    text = A + 'P-8,"x\n",11,bar\nP-7,length,5 m\nP-6,"y\n",12,bar\n'
    spans = multiline_spans(text)
    assert spans == [(2, 3), (5, 6)]
    r = audit(A + "P-7,length,5 m\n", text)
    assert [(w["line"], w["end_line"]) for w in unparsed(r)] == spans and r["coverage"]["b.csv"]["rows_unparsed"] == 2
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["comparisons"] == 2


def test_an_unclosed_quote_never_gives_a_clean_result():
    text = A + 'P-9,"pressure\n,999,bar\n'
    r = audit(A, text)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    (w,) = unparsed(r)
    assert w["detail"] == "UNCLOSED_QUOTE" and w["line"] == 2


def test_the_policy_boundary_is_the_cell_rule_of_one_line_rows():
    one_line = 'P-9,"pressure",999,bar\n'                                           # a one-line quoted row: unsupported, reported (#10)
    assert unparsed(audit(A, A + one_line)) and "quoted fields" in unparsed(audit(A, A + one_line))[0]["reason"]
    assert not unparsed(audit(A, A + 'P-9,"pressure\n",bar\n'))                    # two fields after the tag, no number anywhere: a note
    assert not unparsed(audit(A, A + 'P-9,"pressure\n",many\n'))


# ---- earlier fixes stay ------------------------------------------------------------------------------------------------------------
def test_the_original_61_hidden_physical_line_and_the_adjacent_field_fixtures_still_work():
    note = f'N-1,note,"see the list\nP-9,pressure,999 bar\nend of note",x\n'
    r = audit(A + note, A)
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and [(w["line"], w["end_line"]) for w in unparsed(r)] == [(2, 4)]
    adjacent = A + 'N-1,"plain start\nplain end","second starts\nP-9,pressure,999 bar\nsecond end",x\n'
    r = audit(adjacent, A)
    assert [(w["line"], w["end_line"]) for w in unparsed(r)] == [(2, 5)] and r["coverage_complete"] is False


def test_supported_unquoted_csv_rows_are_unchanged():
    r = audit("P-1,pressure,10 bar\nP-2,flow,12 L/min\nP-3,length,5,m\n", "P-1,pressure,900 kPa\nP-2,flow,0.0002 m^3/s\nP-3,length,5 m\n")
    assert r["verdict"] == "CONFLICTS_FOUND" and r["comparisons"] == 3 and r["coverage_complete"] is True


def test_the_temperature_label_rule_of_60_is_unchanged():
    d = "\u0394"
    r = audit_documents({"a.md": f"MR-27 | temperature_{d} | 1 degC\n", "b.md": f"MR-27 | temperature_{d} | 274.15 K\n"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False


# ---- constructive, seeded generator (expectations are by construction, not computed with the production code) --------------------------
def make_record(rnd, measurement):
    """A CSV record built from known parts, with line breaks placed inside quoted fields. Returns (text, expected_flagged)."""
    tag, prop = rnd.choice(["P-9", "T-12", "FL-01"]), rnd.choice(["pressure", "flow", "length"])
    number, unit = str(rnd.choice([1, 25, 999, 4.5, 0.2])), rnd.choice(["bar", "kPa", "m", "L/min"])
    if measurement:
        layout = rnd.choice(["3cell", "4cell"])          # a bare number after a unit field is covered by a fixed variant above
        fields = [tag, prop] + ([f"{number} {unit}"] if layout == "3cell" else [number, unit])
        force = rnd.randrange(len(fields))
    else:
        fields = [tag, rnd.choice(["note", "comment", "remark"]), rnd.choice(["see page 12", "ask the office", "call later"]), rnd.choice(["x", "y"])]
        force = rnd.randrange(len(fields))
    out = []
    for index, field in enumerate(fields):
        cut = rnd.randrange(1, len(field)) if len(field) > 1 else 0
        broken = field[:cut] + "\n" + field[cut:] if index == force else field
        if index == force or rnd.random() < 0.4:
            broken = '"' + broken.replace('"', '""') + '"'
        out.append(broken)
    if not any("\n" in f for f in out):
        out[0] = '"' + fields[0][:1] + "\n" + fields[0][1:] + '"'
    return ",".join(out) + "\n", measurement


def test_seeded_generator_logical_boundaries_and_coverage():
    rnd = random.Random(20261120)
    checked = flagged_total = 0
    for _ in range(600):
        parts = [make_record(rnd, rnd.random() < 0.5) for _ in range(rnd.randint(1, 4))]
        filler = ["P-1,pressure,10 bar\n", "P-2,length,5 m\n"]
        expected, text = [], ""
        for text_part, measurement in parts:
            text += rnd.choice(filler) + text_part
            expected.append(measurement)
        text += "P-3,flow,7 L/min\n"
        spans = multiline_spans(text)
        assert len(spans) == len(parts)                                          # the oracle boundary agrees with the construction
        records, warnings, coverage = core._parse("a.csv", text)
        got = [(w["line"], w["end_line"]) for w in warnings if w["code"] == "UNPARSED_ROW"]
        want = [span for span, flag in zip(spans, expected) if flag]
        assert got == want, (text, got, want)                                     # (ii) every unsupported logical record, no duplicates (iv)
        inside = {n for a, b in spans for n in range(a + 1, b + 1)}
        assert not {r.line for r in records} & inside                             # (iii) nothing comes from a continuation line
        reference = "P-1,pressure,10 bar\nP-2,length,5 m\nP-3,flow,7 L/min\n"
        forward, reverse = audit(text, reference), audit_documents({"b.csv": reference, "a.csv": text}, 0.01)
        assert (forward["verdict"], forward["coverage_complete"]) == (reverse["verdict"], reverse["coverage_complete"])   # (v)
        if want:
            assert forward["coverage_complete"] is False and forward["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
        checked += 1
        flagged_total += len(want)
    assert checked == 600 and flagged_total > 200                                 # the generator really produced both classes


# ---- every entry point on the reported fixture ---------------------------------------------------------------------------------------
def test_fastapi_agrees():
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.csv": A, "b.csv": B}}).json()
    assert (body["verdict"], body["coverage_complete"], body["comparisons"]) == ("NO_CONFLICTS_PARTIAL_COVERAGE", False, 1)
    assert [(w["document"], w["line"], w["end_line"]) for w in body["warnings"] if w["code"] == "UNPARSED_ROW"] == [("b.csv", 2, 3)]


def test_markdown_and_json_show_the_source_lines_and_no_999_comparison():
    r = audit(A, B)
    md = render_markdown(build_view(r, LABELS), "2026-11-20 00:00 UTC")
    assert "Coverage complete: no" in md and "lines 2-3" in md.replace("\\", "") and "b.csv" in md
    assert "999" not in md and "NO_CONFLICTS_PARTIAL_COVERAGE" in md


@pytest.mark.parametrize("view_name", ["Overview", "Detailed evidence"])
def test_streamlit_shows_the_gap_in_both_views_and_no_green_result(view_name):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.csv", A.encode(), "text/csv")
    at.get("file_uploader")[1].upload("b.csv", B.encode(), "text/csv")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    at.session_state["view_pref"] = view_name
    at.run()
    assert not at.exception and len(at.success) == 0
    page = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.code)
    assert "b.csv" in page and "999" not in page
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NO_CONFLICTS_PARTIAL_COVERAGE", False)
    assert "lines 2-3" in at.session_state["audit"]["markdown"].replace("\\", "")


def test_the_mcp_tool_is_still_pipe_only():
    from app.mcp_server import compare_engineering_rows
    r = compare_engineering_rows(A, B)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"


# ---- bounds --------------------------------------------------------------------------------------------------------------------------
def test_a_long_quiet_multiline_note_is_not_flagged_and_stays_fast():
    note = 'N-1,note,"' + "\n".join(["a quiet line of words about nothing in particular"] * 3500) + '",x\n'
    started = time.perf_counter()
    records, warnings, _ = core._parse("a.csv", A + note)
    assert time.perf_counter() - started < 2.0 and not warnings and [r.tag for r in records] == ["P-9"]


def test_a_long_measurement_shaped_region_is_flagged_once_and_stays_fast():
    body = "\n".join(["words"] * 3000)
    text = A + 'P-8,"' + body + '\n",12,bar\n'
    started = time.perf_counter()
    records, warnings, _ = core._parse("a.csv", text)
    assert time.perf_counter() - started < 2.0 and [(w["line"], w["end_line"]) for w in warnings] == [(2, 3002)]


def test_many_adjacent_quoted_fields_stay_one_region():
    lines = ['P-8,"start'] + ['mid","m2'] * 2500 + ['end",12 bar']
    text = A + "\n".join(lines) + "\n"
    started = time.perf_counter()
    _, warnings, _ = core._parse("a.csv", text)
    assert time.perf_counter() - started < 2.0 and len(warnings) == 1 and warnings[0]["end_line"] == 2503


def test_a_line_over_the_guard_inside_a_record_fails_closed():
    text = A + 'N-1,note,"start\n' + "x" * (MAX_LINE_CHARS + 10) + '\nend",x\n'
    assert len(unparsed(audit(A, text))) == 1


def test_document_limits_are_unchanged():
    assert (core.MAX_CHARS, core.MAX_LINES, core.MAX_LINE_CHARS) == (200_000, 5_000, 2_000)
    with pytest.raises(ValueError):
        audit_documents({"a.csv": "x\n" * 5001, "b.csv": A})
