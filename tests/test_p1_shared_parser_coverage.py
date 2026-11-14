"""Issue #12 (P1): a recognizable UNSUPPORTED measurement-like row must never vanish behind a clean verdict.

Contract (owner-decided, 2026-11-14):
  * Such a row is reported as UNPARSED_ROW (document, line, unchanged excerpt) and counts as a coverage gap.
  * It is NOT parsed and NOT compared. No new grammar becomes supported; ENTRY/CSV_ENTRY are untouched.
  * Recognition is separate from parsing: CSV *parsing* stays gated on a `.csv` file name, but a clearly
    measurement-like comma row in .txt/.md/MCP text is now WARNED about (recognized) instead of ignored.
  * A tag does NOT need a digit to be recognized (PUMP, INLET, FLOW are legal tags in the supported grammar).
  * A conflict among compared rows stays authoritative (CONFLICTS_FOUND); coverage then reports the gap.
  * `Rev | Date | 2026-11-13` keeps its existing conservative warning (no date exemption in this patch).

The detector is a shape heuristic. Its precision/recall are NOT measured. Rows it cannot recognize are listed in
`test_known_undetected_*` so the limitation is explicit and cannot change silently."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import report
from app.api import app as api_app
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows

ROOT = Path(__file__).resolve().parent.parent
SUP = "P-101 | flow | 100 L/min"      # a supported pipe row that matches in both documents


def unparsed(r):
    return [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"]


def names(ext):
    return (f"a{ext}", f"b{ext}")


# (id, file extension, row in A, row in B). The two rows describe a real disagreement (10 vs 16 bar, 12 vs 1 L/min, ...).
HIDDEN = [
    ("md-4cell-description", ".md", "| P-102 | Feed pump | pressure | 10 bar |", "| P-102 | Feed pump | pressure | 16 bar |"),
    ("md-4cell-unit-then-value", ".md", "P-102 | pressure | bar | 10", "P-102 | pressure | bar | 16"),
    ("csv-4cell-description", ".csv", "P-102,Feed pump,pressure,10 bar", "P-102,Feed pump,pressure,16 bar"),
    ("csv-4cell-unit-then-value", ".csv", "P-102,pressure,bar,10", "P-102,pressure,bar,16"),
    ("md-qualifier-max", ".md", "P-102 | pressure | max 10 bar", "P-102 | pressure | max 16 bar"),
    ("md-tag-without-digit-PUMP", ".md", "PUMP | pressure | max 10 bar", "PUMP | pressure | max 16 bar"),
    ("md-tag-without-digit-INLET", ".md", "INLET | pressure | min 10 bar", "INLET | pressure | min 16 bar"),
    ("md-tag-without-digit-FLOW", ".md", "FLOW | rate | nom 12 L/min", "FLOW | rate | nom 1 L/min"),
    ("csv-qualifier-tag-without-digit", ".csv", "PUMP,pressure,max 10 bar", "PUMP,pressure,max 16 bar"),
    ("md-4cell-gauge-unit-column", ".md", "| P-102 | pressure | barg | 10 |", "| P-102 | pressure | barg | 16 |"),
    ("md-5cell-value-late", ".md", "| P-102 | Feed pump | Grundfos | pressure | 10 bar |", "| P-102 | Feed pump | Grundfos | pressure | 16 bar |"),
    ("md-4cell-unsupported-unit-with-value", ".md", "| P-102 | Feed pump | pressure | 10 barg |", "| P-102 | Feed pump | pressure | 16 barg |"),
    ("csv-file-quoted-pipe-row", ".csv", '"P-102" | "pressure" | "10 bar"', '"P-102" | "pressure" | "16 bar"'),
    ("csv-file-malformed-pipe-row", ".csv", "P-102 | pressure | 10 | bar", "P-102 | pressure | 16 | bar"),
    ("txt-comma-3cell", ".txt", "P-102,pressure,10 bar", "P-102,pressure,16 bar"),
    ("txt-comma-tag-without-digit", ".txt", "PUMP,pressure,10 bar", "PUMP,pressure,16 bar"),
    ("txt-comma-4cell-value-unit", ".txt", "P-102,pressure,10,bar", "P-102,pressure,16,bar"),
    ("txt-quoted-comma", ".txt", '"P-102","pressure","10 bar"', '"P-102","pressure","16 bar"'),
    ("md-comma-row", ".md", "P-102,pressure,10 bar", "P-102,pressure,16 bar"),
    ("no-extension-comma-row", "", "P-102,pressure,10 bar", "P-102,pressure,16 bar"),
]
IDS = [h[0] for h in HIDDEN]


# ---------- 1. one supported pair + a hidden, conflicting unsupported pair
@pytest.mark.parametrize("case", HIDDEN, ids=IDS)
def test_hidden_unsupported_row_makes_coverage_incomplete(case):
    _, ext, row_a, row_b = case
    na, nb = names(ext)
    r = audit_documents({na: SUP + "\n" + row_a, nb: SUP + "\n" + row_b})
    w = unparsed(r)
    assert [(x["document"], x["line"], x["excerpt"]) for x in w] == [(na, 2, row_a.strip()), (nb, 2, row_b.strip())], r["warnings"]
    assert all(x["reason"] for x in w)
    assert r["comparisons"] == 1 and len(r["matches"]) == 1 and r["findings"] == []     # the hidden conflict is NOT invented
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    assert r["coverage"][na]["rows_unparsed"] == 1 and r["coverage"][nb]["rows_unparsed"] == 1
    assert r["coverage"][na]["rows_parsed"] == 1                                         # only the supported row was parsed
    v = report.build_view(r)
    assert v.verdict == report.VERDICT_PARTIAL and len(v.coverage) == 2 and not v.coverage_complete


# ---------- 2. only unsupported lines: nothing compared, still explained
@pytest.mark.parametrize("case", HIDDEN, ids=IDS)
def test_only_unsupported_rows_compare_nothing_and_say_why(case):
    _, ext, row_a, row_b = case
    na, nb = names(ext)
    r = audit_documents({na: row_a, nb: row_b})
    assert r["comparisons"] == 0 and r["records"] == 0
    assert [(x["document"], x["line"], x["excerpt"]) for x in unparsed(r)] == [(na, 1, row_a.strip()), (nb, 1, row_b.strip())]
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False


# ---------- 3. a real conflict stays authoritative; coverage still reports the gap
def test_conflict_precedence_is_kept_when_a_row_is_hidden():
    a = SUP + "\nP-102 | pressure | 10 bar\nP-103,pressure,10 bar"
    b = "P-101 | flow | 50 L/min\nP-102 | pressure | 900 kPa\nP-103,pressure,16 bar"
    r = audit_documents({"a.md": a, "b.md": b})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False
    assert len(r["findings"]) == 2 and r["comparisons"] == 2
    assert [(x["document"], x["line"], x["excerpt"]) for x in unparsed(r)] == [
        ("a.md", 3, "P-103,pressure,10 bar"), ("b.md", 3, "P-103,pressure,16 bar")]


# ---------- 4. supported grammars are unchanged
def test_valid_csv_rows_are_still_compared():
    r = audit_documents({"a.csv": "P-101,pressure,10,bar\nV-201,diameter,25 mm", "b.csv": "P-101,pressure,900 kPa\nV-201,diameter,0.025 m"})
    assert unparsed(r) == [] and r["comparisons"] == 2
    assert [f["tag"] for f in r["findings"]] == ["p-101"] and len(r["matches"]) == 1
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is True


@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
def test_valid_pipe_rows_are_unchanged_in_every_document_type(ext):
    na, nb = names(ext)
    r = audit_documents({na: "| P-101 | pressure | 10 bar |\nP-101 | flow | 12 L/min", nb: "P-101 | pressure | 900 kPa\n| P-101 | flow | 0.0002 m^3/s |"})
    assert unparsed(r) == [] and r["comparisons"] == 2 and r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is True


# ---------- 5. the deliberate .txt recognition-contract change: recognized and warned, never parsed or compared
@pytest.mark.parametrize("ext", ["", ".txt", ".md"])
def test_comma_measurement_row_outside_csv_is_warned_not_compared(ext):
    na, nb = names(ext)
    r = audit_documents({na: "P-1,pressure,10 bar", nb: "P-1,pressure,900 kPa"})
    assert r["comparisons"] == 0 and r["records"] == 0 and r["findings"] == []          # CSV parsing stays .csv-gated
    assert [(x["document"], x["line"], x["excerpt"]) for x in unparsed(r)] == [(na, 1, "P-1,pressure,10 bar"), (nb, 1, "P-1,pressure,900 kPa")]
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False


# ---------- 6. negatives: lines that are not measurements stay quiet in every document type
NEGATIVE_LINES = [
    "| Tag | Property | Value |\n| --- | --- | --- |",              # table header + separator
    "| Tag | Description | Property | Value |\n|:---|:---|:---|---:|",
    "TAG,property,value,unit",                                      # CSV header
    "Tag | Description | Property | Value",
    "This pump, which was installed in 2019, is fine",              # prose with commas
    "Note: pumps, valves, and 3 spares are listed below.",
    "Pumps, valves and 3 spares",
    'He said "P-101" is the main pump, not "P-102".',               # quotes in prose
    'Example: "P-101","pressure","10 bar" is not supported.',       # a quoted sample inside a sentence
    "Revision: 3, approved by QA",                                  # revision metadata (not the Rev | Date row)
    "The feed pump P-102 (10 bar design) is installed",             # alphanumeric description, no delimiter
    "Page 3 of 12",
    "| P-101 | Pump | Grundfos | 2 |",                              # BOM quantity column: a bare number after a non-unit cell
    "| P-101 | Pump | Grundfos | CR 10-3 | 2 |",
    "| R1 | The pump shall deliver flow | Open | 12 |",              # requirements table with a page column
    "| ID | Requirement | Status | Page |",
    "| 1.2 | 2026-11-13 | E. Tahiroglu | Initial issue |",            # revision history
    "| Doc | Rev | Date | 2026 |",                                 # document-control row
    "# Pump station notes, rev A",
    "",
    "    ",
]


@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
@pytest.mark.parametrize("line", NEGATIVE_LINES, ids=[f"neg{i}" for i in range(len(NEGATIVE_LINES))])
def test_non_measurement_lines_raise_no_new_warning(ext, line):
    na, nb = names(ext)
    r = audit_documents({na: SUP + "\n" + line, nb: SUP + "\n" + line})
    assert unparsed(r) == [], (ext, line)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


def test_crlf_line_numbers_and_excerpts_are_exact():
    na, nb = "a.txt", "b.txt"
    r = audit_documents({na: SUP + "\r\nnote\r\nP-102,pressure,10 bar\r\n", nb: SUP + "\r\nnote\r\nP-102,pressure,16 bar\r\n"})
    assert [(x["document"], x["line"], x["excerpt"]) for x in unparsed(r)] == [
        (na, 3, "P-102,pressure,10 bar"), (nb, 3, "P-102,pressure,16 bar")]


# ---------- 7. existing conservative behaviour is locked in, not relabelled as a clean negative
def test_rev_date_row_keeps_its_existing_conservative_warning():
    """Owner decision 2026-11-14: no date exemption in this patch. The warning exists on the frozen baseline."""
    r = audit_documents({"a.md": SUP + "\nRev | Date | 2026-11-13", "b.md": SUP + "\nRev | Date | 2026-11-14"})
    assert [(x["document"], x["line"], x["excerpt"]) for x in unparsed(r)] == [
        ("a.md", 2, "Rev | Date | 2026-11-13"), ("b.md", 2, "Rev | Date | 2026-11-14")]
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False


def test_comma_date_row_in_a_csv_keeps_its_existing_conservative_warning():
    """Existing behaviour on the frozen baseline (third comma cell starts with a digit). Not a clean negative, not changed here.
    Outside .csv the same line has no pipe-delimited shape and a non-identifier first cell, so it stays quiet."""
    line = "Prepared by Acme, Ltd, 2026-11-13"
    csv = audit_documents({"a.csv": SUP + "\n" + line, "b.csv": SUP + "\n" + line})
    assert len(unparsed(csv)) == 2 and csv["coverage_complete"] is False
    for ext in ("", ".md", ".txt"):
        na, nb = names(ext)
        assert unparsed(audit_documents({na: SUP + "\n" + line, nb: SUP + "\n" + line})) == []


def test_documented_false_positive_list_row_is_warned():
    """KNOWN FALSE POSITIVE (conservative): shape alone cannot tell `Item,count,3 pieces` from a measurement row."""
    r = audit_documents({"a.txt": "Item,count,3 pieces", "b.txt": "Item,count,4 pieces"})
    assert len(unparsed(r)) == 2 and r["coverage_complete"] is False


# ---------- 8. KNOWN UNDETECTED shapes: the limitation is explicit and locked, never described as 'all rows are found'
@pytest.mark.parametrize("name,ext,row", [
    ("description-first comma row outside .csv", ".txt", "Feed pump,P-102,pressure,10 bar"),
    ("value inside a sentence", ".md", "P-102 | pressure | design value is 10 bar"),
    ("two-cell row", ".md", "P-102 | 10 bar"),
    ("unit after extra words", ".txt", "P-102,pressure,10 bar design"),
    ("semicolon delimiter", ".txt", "P-102;pressure;10 bar"),
    ("tab delimiter", ".txt", "P-102\tpressure\t10 bar"),
    ("range in the fourth cell", ".md", "| P-102 | Feed pump | pressure | 10-16 bar |"),
    ("bare number after an unknown unit word", ".md", "| P-102 | pressure | xyzunit | 10 |"),
])
def test_known_undetected_shapes_are_documented_limits(name, ext, row):
    na, nb = names(ext)
    r = audit_documents({na: SUP + "\n" + row, nb: SUP + "\n" + row})
    assert unparsed(r) == [], name                                  # NOT detected today; README/UI copy must not claim otherwise
    assert r["coverage_complete"] is True


# ---------- 9. every surface reports the same thing
HID_A = SUP + "\n| P-102 | Feed pump | pressure | 10 bar |"
HID_B = SUP + "\n| P-102 | Feed pump | pressure | 16 bar |"


def test_api_exposes_the_incomplete_coverage():
    body = TestClient(api_app).post("/audit", json={"documents": {"a.md": HID_A, "b.md": HID_B}}).json()
    assert [w["code"] for w in body["warnings"]] == ["UNPARSED_ROW", "UNPARSED_ROW"]
    assert body["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and body["coverage_complete"] is False
    assert json.loads(json.dumps(body)) == body


@pytest.mark.parametrize("spec,bom", [
    ("P-102,pressure,10 bar", "P-102,pressure,16 bar"),                              # comma-only
    ('"P-102","pressure","10 bar"', '"P-102","pressure","16 bar"'),                  # quoted comma-only
    (SUP + "\nP-102,pressure,10 bar", SUP + "\nP-102,pressure,16 bar"),             # mixed pipe + comma
    (SUP + "\nP-102,pressure,10,bar", SUP + "\nP-102,pressure,16,bar"),             # mixed, four-column comma
    (SUP + '\n"P-102","pressure","10 bar"', SUP + '\n"P-102","pressure","16 bar"'),  # mixed, quoted
], ids=["comma-only", "quoted-comma-only", "mixed", "mixed-four-column", "mixed-quoted"])
def test_mcp_never_returns_a_complete_clean_result_for_comma_rows(spec, bom):
    r = compare_engineering_rows(spec, bom)
    assert r == audit_documents({"spec": spec, "bom": bom})                             # one engine, one answer
    assert len(unparsed(r)) == 2 and r["coverage_complete"] is False
    assert r["verdict"] in ("NOTHING_COMPARED", "NO_CONFLICTS_PARTIAL_COVERAGE")
    assert r["findings"] == []                                                          # MCP still compares pipe rows only


def test_mcp_comma_only_input_reports_nothing_compared():
    r = compare_engineering_rows("P-101,flow,99 L/min", "P-101,flow,1 L/min")
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False and r["comparisons"] == 0
    assert [(w["document"], w["line"], w["excerpt"]) for w in unparsed(r)] == [("spec", 1, "P-101,flow,99 L/min"), ("bom", 1, "P-101,flow,1 L/min")]


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60)


def test_streamlit_ui_and_exports_never_show_green_for_a_hidden_row():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("a.md", HID_A.encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", HID_B.encode(), "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception and not at.success                                          # never the green banner
    audit = at.session_state["audit"]
    assert audit["view"].verdict == report.VERDICT_PARTIAL and len(audit["view"].coverage) == 2
    exported = json.loads(audit["json"])
    assert exported["verdict"] == report.VERDICT_PARTIAL and exported["coverage_complete"] is False
    assert exported["result"]["coverage"]["a.md"]["rows_unparsed"] == 1
    assert "UNPARSED_ROW" in audit["markdown"] and "incomplete" in audit["markdown"].lower()
    assert "Feed pump" in audit["markdown"]                                              # the unchanged excerpt is in the report


def test_pump_station_fixture_is_unchanged():
    spec = (ROOT / "examples" / "pump_station" / "spec.md").read_text(encoding="utf-8")
    csv = (ROOT / "examples" / "pump_station" / "purchasing.csv").read_text(encoding="utf-8")
    r = audit_documents({"spec.md": spec, "purchasing.csv": csv})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False
    assert len(r["findings"]) == 1 and len(r["matches"]) == 2 and r["comparisons"] == 3
    from app.core import count_coverage_gaps
    assert count_coverage_gaps(r) == 4
    assert sorted(w["code"] for w in r["warnings"]) == ["AMBIGUOUS_UNIT", "UNPARSED_ROW"]


# ---------- 10. copy states the contract and does not overclaim
def test_copy_states_the_contract_without_overclaiming():
    ui = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    mcp = compare_engineering_rows.__doc__
    phrase = "Recognizable unsupported measurement-like rows"
    assert phrase in ui and phrase in readme
    assert "may be missed" in ui and "may be missed" in readme
    assert "listed, not ignored" not in ui                                    # the old absolute sentence is gone
    low = mcp.lower()
    assert "comma" in low and "warn" in low and "pipe" in low and "CSV" in mcp
    assert "does not enable CSV" in readme                                    # existing contract wording stays
    for text in (ui, readme, mcp):
        t = text.lower()
        for banned in ("all invalid measurement", "cannot bluff", "verified physical equivalence", "compliance certification",
                       "every unsupported row", "all unsupported rows"):
            assert banned not in t, banned
