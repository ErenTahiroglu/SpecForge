"""FINAL74-RT-001 (#65): whether a measurement-shaped logical CSV record is reported must NOT depend on where the value starts inside
an allowed-length field, or on how long an unrelated note in the same record is.

Policy under test (README, "Source format and limits"): for a quoted multi-line region, each field is normalized before the cell
shape rules run: leading whitespace, invisible format characters and qualifier marks or words are skipped in one linear pass, so the
amount of padding in front of a value cannot move it out of view. A delimiter-bearing field longer than the 2,000-character line
guard, and a record with more than 256 fields, are not inspected: they are reported (fail closed), as LINE_TOO_LONG is for a
one-line row. A short prose note stays silent. A quoted value is never compared.

Expectations here are by construction (the record is built from a known measurement) and from Python's csv.reader for record
boundaries. They are not computed with the production classifier.
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
LABELS = {"a.csv": "Document A", "b.csv": "Document B"}
WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa"


def prose(length):
    text = (WORDS + " ") * (length // len(WORDS) + 2)
    return text[:length].rstrip()


def oracle(text):
    """[(first_line, last_line, fields)] per logical record, from the csv module."""
    reader = csv.reader(io.StringIO(text, newline=""))
    out, previous = [], 0
    for fields in reader:
        out.append((previous + 1, reader.line_num, fields))
        previous = reader.line_num
    return out


def multiline(text):
    return [(a, b, f) for a, b, f in oracle(text) if b > a]


def unparsed(r):
    return [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"]


def make_b(qualifier, pad, note1, note2, eol="\n", value="999 bar"):
    """b.csv: the supported row, then ONE logical record whose third field is qualifier + padding + value. No physical line of it is
    a row, and every physical line stays within MAX_LINE_CHARS."""
    third = f"{qualifier}{' ' * pad}{value}"
    record = f'P-9,"pressure note {prose(note1)}{eol}{prose(note2)}","{third}"{eol}'
    return "P-9,pressure,10 bar" + eol + record, third


def audit_pair(b, swap=False):
    docs = {"b.csv": b, "a.csv": A} if swap else {"a.csv": A, "b.csv": b}
    return audit_documents(docs, 0.01)


def assert_reported_once(r, span, document="b.csv"):
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and r["status"] == "NO_DIFFERENCES_FOUND"
    (w,) = unparsed(r)
    assert (w["document"], w["line"], w["end_line"], w["detail"]) == (document, span[0], span[1], "MULTILINE_QUOTED_FIELD")
    assert r["coverage"][document]["rows_unparsed"] == 1
    assert r["comparisons"] == 1 and len(r["matches"]) == 1 and r["findings"] == []
    assert {r["matches"][0]["reference"]["excerpt"], r["matches"][0]["other"]["excerpt"]} == {"P-9,pressure,10 bar"}
    assert "999" not in json.dumps({"m": r["matches"], "f": r["findings"], "u": r["unmatched"]})


# ---- the reported flip: 62 spaces is reported, 63 is not (unchanged baseline) --------------------------------------------------------
@pytest.mark.parametrize("pad", [62, 63])
@pytest.mark.parametrize("swap", [False, True])
def test_the_reported_flip_62_and_63_spaces_with_a_long_note(pad, swap):
    b, third = make_b("+", pad, 1800, 1800)
    spans = multiline(b)
    assert [(s, e) for s, e, _ in spans] == [(2, 3)] and spans[0][2][2] == third             # csv.reader: one record, lines 2-3
    assert all(len(line) <= MAX_LINE_CHARS for line in b.split("\n")) and len(b) < core.MAX_CHARS
    for line in b.split("\n")[1:3]:                                                          # no physical line is a row on its own
        assert not core.ENTRY.fullmatch(line) and not core.CSV_ENTRY.fullmatch(line) and not core._looks_like_row(line, True)
    assert_reported_once(audit_pair(b, swap), (2, 3))


# ---- invariance matrix --------------------------------------------------------------------------------------------------------------------
PADS = [0, 1, 60, 61, 62, 63, 64, 65, 66, 127, 128, 900]
QUALIFIERS = ["", "+", "-", "\u00b1", "~", "max ", "min ", "approx. ", "ca "]
NOTES = [(0, 0), (40, 40), (1200, 600), (1800, 1800), (1900, 10)]


@pytest.mark.parametrize("qualifier", QUALIFIERS)
@pytest.mark.parametrize("pad", PADS)
def test_padding_and_qualifier_never_decide_whether_the_record_is_reported(qualifier, pad):
    for note1, note2 in NOTES:
        b, third = make_b(qualifier, pad, note1, note2)
        spans = [(s, e) for s, e, _ in multiline(b)]
        assert spans == [(2, 3)], (qualifier, pad, note1, note2)
        assert_reported_once(audit_pair(b), spans[0])


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("pad", [62, 63, 64, 300])
def test_line_endings(eol, pad):
    b, _ = make_b("+", pad, 1500, 1500, eol)
    spans = [(s, e) for s, e, _ in multiline(b)]
    assert spans == [(2, 3)]
    assert_reported_once(audit_pair(b), spans[0])


def test_a_note_longer_than_the_old_fallback_threshold_never_turns_a_reported_record_silent():
    flags = []
    for note in range(0, 1901, 100):
        b, _ = make_b("+", 63, note, note)
        flags.append(bool(unparsed(audit_pair(b))))
    assert flags == [True] * len(flags)


# ---- split values, adjacent fields, odd structure --------------------------------------------------------------------------------------
VARIANTS = {
    "padding before a split number": 'P-9,"pressure note\n","+' + " " * 70 + '9\n99 bar"\n',
    "padding then value then unit split": 'P-9,pressure,"+' + " " * 70 + '999 b\nar"\n',
    "value after leading and trailing spaces": 'P-9,"pressure\n","   +' + " " * 70 + '999 bar' + " " * 80 + '"\n',
    "escaped quotes in the note": 'P-9,"a ""quoted"" note\nmore","+' + " " * 70 + '999 bar"\n',
    "adjacent quoted fields": 'P-9,"a\n","+' + " " * 70 + '999\nx",y\n',
    "four-field number then unit": 'P-9,"pressure\n","+' + " " * 70 + '999",bar\n',
    "unit field then padded number": 'P-9,"pressure\n",bar,"+' + " " * 70 + '999"\n',
    "long run of qualifier marks": 'P-9,"pressure\n","' + "+" * 90 + '999 bar"\n',
    "mixed qualifier words and spaces": 'P-9,"pressure\n","max ' + " " * 80 + "~ " + " " * 80 + '999 bar"\n',
    "invisible characters before the value": 'P-9,"pressure\n","' + "\u200b" * 80 + '999 bar"\n',
    "tag split and padded value": '"P-\n9",pressure,"+' + " " * 70 + '999 bar"\n',
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("long_note", [False, True])
@pytest.mark.parametrize("swap", [False, True])
def test_variants_are_reported_once_with_the_oracle_span(name, long_note, swap):
    record = VARIANTS[name]
    if long_note:                                           # a long unrelated note in the same record, as in the report
        record = record.rstrip("\n") + "," + prose(1500) + "\n"
    b = "P-9,pressure,10 bar\n" + record + "P-2,length,5 m\n"
    spans = [(s, e) for s, e, _ in multiline(b)]
    assert len(spans) == 1, name
    assert all(len(line) <= MAX_LINE_CHARS for line in b.split("\n"))
    other = "P-9,pressure,10 bar\nP-2,length,5 m\n"
    docs = {"b.csv": other, "a.csv": b} if swap else {"a.csv": b, "b.csv": other}
    r = audit_documents(docs, 0.01)
    (w,) = unparsed(r)
    assert (w["document"], w["line"], w["end_line"], w["detail"]) == ("a.csv", spans[0][0], spans[0][1], "MULTILINE_QUOTED_FIELD")
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and r["comparisons"] == 2
    assert "999" not in json.dumps(r["matches"])


def test_two_independent_records_are_two_warnings_and_the_rest_is_compared():
    b = "P-9,pressure,10 bar\n" + make_b("+", 80, 1500, 1500)[0].split("\n", 1)[1] + "P-7,length,5 m\n" + make_b("-", 90, 1700, 100)[0].split("\n", 1)[1]
    spans = [(s, e) for s, e, _ in multiline(b)]
    assert len(spans) == 2
    r = audit_documents({"a.csv": A + "P-7,length,5 m\n", "b.csv": b}, 0.01)
    assert [(w["line"], w["end_line"]) for w in unparsed(r)] == spans and r["comparisons"] == 2 and r["coverage_complete"] is False


def test_an_unclosed_record_and_a_missing_final_newline_stay_reported():
    unclosed = "P-9,pressure,10 bar\n" + 'P-9,"pressure ' + prose(1500) + "\n" + prose(100) + ',+' + " " * 70 + "999 bar\n"
    r = audit_pair(unclosed)
    assert r["coverage_complete"] is False and [w["detail"] for w in unparsed(r)] == ["UNCLOSED_QUOTE"]
    no_newline = make_b("+", 63, 1800, 1800)[0].rstrip("\n")
    assert_reported_once(audit_pair(no_newline), (2, 3))


# ---- pipe and comma rows folded inside one long quoted field -----------------------------------------------------------------------------
def test_a_pipe_row_inside_a_quoted_field_in_a_long_record():
    record = 'N-1,"' + prose(1500) + '","P-9 | pressure |\n999 bar"\n'
    b = "P-9,pressure,10 bar\n" + record
    spans = [(s, e) for s, e, _ in multiline(b)]
    assert spans == [(2, 3)] and all(len(l) <= MAX_LINE_CHARS for l in b.split("\n"))
    assert_reported_once(audit_pair(b), (2, 3))


def test_a_comma_row_inside_a_quoted_field_in_a_long_record():
    record = 'N-1,"' + prose(1500) + '","P-9,pressure,\n999 bar"\n'
    b = "P-9,pressure,10 bar\n" + record
    assert [(s, e) for s, e, _ in multiline(b)] == [(2, 3)]
    assert_reported_once(audit_pair(b), (2, 3))


# ---- benign prose stays quiet; the supported rows around it are compared ---------------------------------------------------------------
@pytest.mark.parametrize("length", [0, 20, 63, 64, 65, 500, 1500, 1900])
def test_a_long_prose_field_stays_quiet(length):
    for third in (prose(length), "See page 12 of 40 in the 2024 report, dated 2024-05-17: " + prose(length)):
        record = f'N-1,"note\n","{third}"\n'
        b = "P-9,pressure,10 bar\n" + record + "P-2,length,5 m\n"
        assert len(multiline(b)) == 1 and all(len(l) <= MAX_LINE_CHARS for l in b.split("\n"))
        r = audit_documents({"a.csv": A + "P-2,length,5 m\n", "b.csv": b}, 0.01)
        assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and not unparsed(r) and r["comparisons"] == 2


@pytest.mark.parametrize("cell_length", [62, 63, 64, 65, 66])
def test_a_cell_just_under_equal_and_over_the_old_cutoff(cell_length):
    prose_cell = ("remark " * 20)[:cell_length].rstrip()
    quiet = f'N-1,"note\nmore","{prose_cell}"\n'
    assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + quiet))
    value = "999 bar"
    measured = f'N-1,"note\nmore","{"+" + " " * (cell_length - 1 - len(value)) + value}"\n'      # total cell length == cell_length
    assert any(len(f) == cell_length for f in multiline(measured)[0][2])
    r = audit_pair("P-9,pressure,10 bar\n" + measured)
    assert len(unparsed(r)) == 1 and r["coverage_complete"] is False


# ---- the new fail-closed branches, each with a positive and a negative control ----------------------------------------------------------
def test_a_delimiter_bearing_field_over_the_line_guard_is_reported_but_the_same_text_without_a_delimiter_is_quiet():
    chunk = (prose(80) + ", ") * 12                                                   # 984 characters, with commas
    comma = 'N-1,"' + chunk + "\n" + chunk + "\n" + chunk + '\nend",x\n'               # one field of ~2,950 characters over 4 lines
    plain = comma.replace(",", "", ).replace('N-1"', 'N-1,"')
    plain = 'N-1,"' + chunk.replace(",", "") + "\n" + chunk.replace(",", "") + "\n" + chunk.replace(",", "") + '\nend",x\n'
    assert max(len(f) for f in multiline("P-9,pressure,10 bar\n" + comma)[0][2]) > MAX_LINE_CHARS
    assert unparsed(audit_pair("P-9,pressure,10 bar\n" + comma))
    assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + plain))


def test_a_record_with_more_than_256_fields_is_reported_but_256_fields_of_prose_are_quiet():
    many = 'N-1,"note\nmore",' + ",".join(["word"] * 300) + "\n"
    few = 'N-1,"note\nmore",' + ",".join(["word"] * 200) + "\n"
    assert unparsed(audit_pair("P-9,pressure,10 bar\n" + many))
    assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + few))


def test_invisible_characters_before_a_value_do_not_hide_it_but_do_not_create_one():
    hidden = 'P-9,"pressure\n","' + "\u200b" * 80 + '999 bar"\n'
    none = 'P-9,"pressure\n","' + "\u200b" * 80 + 'no value here"\n'
    assert unparsed(audit_pair("P-9,pressure,10 bar\n" + hidden))
    assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + none))


def test_a_qualifier_word_at_the_start_of_prose_is_not_a_measurement():
    quiet = 'P-9,"pressure\n","maximum allowed use is described in the manual"\n'
    assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + quiet))
    assert unparsed(audit_pair("P-9,pressure,10 bar\n" + 'P-9,"pressure\n","max ' + " " * 100 + '999 bar"\n'))


# ---- seeded property: monotone in padding, qualifier and note length -----------------------------------------------------------------------
def test_seeded_invariance_over_padding_qualifier_note_and_split_position():
    rnd = random.Random(20261120)
    for _ in range(500):
        qualifier, pad = rnd.choice(QUALIFIERS), rnd.randint(0, 400)
        n1, n2 = rnd.randint(0, 1900), rnd.randint(0, 1500)
        value = rnd.choice(["999 bar", "12 kPa", "4.5 m", "0.2 L/min"])
        cut = rnd.randrange(1, len(value))
        third = f"{qualifier}{' ' * pad}{value[:cut]}"                      # the value may be cut by the line break below
        record = f'P-9,"pressure {prose(n1)}\n{prose(n2)}","{third}\n{value[cut:]}"\n'
        b = "P-9,pressure,10 bar\n" + record
        spans = [(s, e) for s, e, _ in multiline(b)]
        assert len(spans) == 1
        r = audit_pair(b)
        assert [(w["line"], w["end_line"]) for w in unparsed(r)] == spans, (qualifier, pad, n1, n2, value, cut)
        assert r["coverage_complete"] is False
        # benign twin: the same lengths, a prose third field
        twin = f'P-9,"pressure {prose(n1)}\n{prose(n2)}","{" " * pad}{prose(30)}"\n'
        assert not unparsed(audit_pair("P-9,pressure,10 bar\n" + twin)), (pad, n1, n2)


# ---- earlier fixes and every surface ----------------------------------------------------------------------------------------------------
def test_the_earlier_fixtures_still_hold():
    r = audit_documents({"a.csv": A, "b.csv": 'P-9,pressure,10 bar\nP-9,"pressure\n",999,bar\n'}, 0.01)          # #63
    assert [(w["line"], w["end_line"]) for w in unparsed(r)] == [(2, 3)] and r["coverage_complete"] is False
    note = 'N-1,note,"see the list\nP-9,pressure,999 bar\nend of note",x\n'                                           # #61
    assert [(w["line"], w["end_line"]) for w in unparsed(audit_documents({"a.csv": A + note, "b.csv": A}, 0.01))] == [(2, 4)]
    adjacent = A + 'N-1,"plain start\nplain end","second starts\nP-9,pressure,999 bar\nsecond end",x\n'
    assert [(w["line"], w["end_line"]) for w in unparsed(audit_documents({"a.csv": adjacent, "b.csv": A}, 0.01))] == [(2, 5)]
    d = "\u0394"                                                                                                       # #60
    t = audit_documents({"a.md": f"MR-27 | temperature_{d} | 1 degC\n", "b.md": f"MR-27 | temperature_{d} | 274.15 K\n"})
    assert t["verdict"] == "NOTHING_COMPARED"
    ok = audit_documents({"a.csv": "P-1,pressure,10 bar\nP-2,flow,12 L/min\n", "b.csv": "P-1,pressure,900 kPa\nP-2,flow,0.0002 m^3/s\n"})
    assert ok["verdict"] == "CONFLICTS_FOUND" and ok["comparisons"] == 2 and ok["coverage_complete"] is True


B63 = make_b("+", 63, 1800, 1800)[0]


def test_fastapi_json_and_markdown_agree():
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.csv": A, "b.csv": B63}}).json()
    assert (body["verdict"], body["coverage_complete"], body["comparisons"]) == ("NO_CONFLICTS_PARTIAL_COVERAGE", False, 1)
    assert [(w["document"], w["line"], w["end_line"], w["detail"]) for w in body["warnings"] if w["code"] == "UNPARSED_ROW"] == [("b.csv", 2, 3, "MULTILINE_QUOTED_FIELD")]
    md = render_markdown(build_view(audit_pair(B63), LABELS), "2026-11-20 00:00 UTC")
    assert "Coverage complete: no" in md and "lines 2-3" in md.replace("\\", "") and "NO_CONFLICTS_PARTIAL_COVERAGE" in md and "999" not in md


@pytest.mark.parametrize("view_name", ["Overview", "Detailed evidence"])
def test_streamlit_shows_the_gap_and_no_green_banner(view_name):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.csv", A.encode(), "text/csv")
    at.get("file_uploader")[1].upload("b.csv", B63.encode(), "text/csv")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    at.session_state["view_pref"] = view_name
    at.run()
    assert not at.exception and len(at.success) == 0
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NO_CONFLICTS_PARTIAL_COVERAGE", False)
    assert "lines 2-3" in at.session_state["audit"]["markdown"].replace("\\", "")


def test_the_mcp_tool_is_still_pipe_only():
    from app.mcp_server import compare_engineering_rows
    r = compare_engineering_rows(A, B63)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"


# ---- bounds ---------------------------------------------------------------------------------------------------------------------------------
def test_limits_unchanged_and_adversarial_records_stay_fast():
    assert (core.MAX_CHARS, core.MAX_LINES, core.MAX_LINE_CHARS) == (200_000, 5_000, 2_000)
    cases = {
        "4000 short prose lines in one field": 'N-1,"' + "\n".join([prose(40)] * 4000) + '",x\n',
        "300 padded measurement records": "".join(f'P-{i},"x\n","+{" " * 400}{i} bar"\n' for i in range(300)),
        "a 1900-char padded value field": 'P-9,"pressure\n","+' + " " * 1800 + '999 bar"\n',
        "250 fields in one record": 'N-1,"a\nb",' + ",".join(["w"] * 250) + "\n",
    }
    for label, text in cases.items():
        started = time.perf_counter()
        core._parse("a.csv", A + text)
        assert time.perf_counter() - started < 2.0, label
