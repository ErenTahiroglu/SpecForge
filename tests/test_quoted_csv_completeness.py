"""Issue #10: a measurement-like row with quoted fields must never be dropped silently.

An unsupported (quoted) row is NOT compared and its grammar is NOT added to the supported formats.
It must appear as an evidence-linked UNPARSED_ROW (document, line, exact excerpt, reason), raise
coverage.rows_unparsed, and keep the verdict from claiming a complete, clean audit."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import report
from app.api import app as api_app
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows

GOOD = "P-1,pressure,10 bar"


def unparsed(result):
    return [w for w in result["warnings"] if w["code"] == "UNPARSED_ROW"]


# ---------- 1. the report-32 reproducer: hidden 99 vs 1 L/min conflict + one valid matching pair
def test_quoted_rows_in_both_documents_are_reported_and_never_clean():
    a = GOOD + '\n"P-101","flow","99 L/min"'
    b = GOOD + '\n"P-101","flow","1 L/min"'
    r = audit_documents({"a.csv": a, "b.csv": b})
    w = unparsed(r)
    assert [(x["document"], x["line"], x["excerpt"]) for x in w] == [
        ("a.csv", 2, '"P-101","flow","99 L/min"'), ("b.csv", 2, '"P-101","flow","1 L/min"')]
    assert all(x["reason"] for x in w)
    assert r["coverage"]["a.csv"]["rows_unparsed"] == 1 and r["coverage"]["b.csv"]["rows_unparsed"] == 1
    assert r["comparisons"] == 1 and r["findings"] == []                  # the quoted conflict is NOT invented
    v = report.build_view(r)
    assert v.verdict == report.VERDICT_PARTIAL and "incomplete" in v.headline
    assert len(v.coverage) == 2
    md = report.render_markdown(v, "t")
    assert "99 L/min" in md and "a.csv" in md and "incomplete" in md.lower()


# ---------- 2. quoted row on one side only
def test_quoted_row_in_one_document_only():
    r = audit_documents({"a.csv": GOOD + '\n"P-101","flow","99 L/min"', "b.csv": GOOD})
    assert [(x["document"], x["line"]) for x in unparsed(r)] == [("a.csv", 2)]
    assert report.build_view(r).verdict == report.VERDICT_PARTIAL


# ---------- 3. only quoted measurements
def test_only_quoted_measurements_compare_nothing_and_say_why():
    r = audit_documents({"a.csv": '"P-101","pressure","10 bar"', "b.csv": '"P-101","pressure","900 kPa"'})
    assert r["comparisons"] == 0 and len(unparsed(r)) == 2
    v = report.build_view(r)
    assert v.verdict == report.VERDICT_NONE and len(v.coverage) == 2


# ---------- 4. quoting in every field position
@pytest.mark.parametrize("row", [
    '"P-101",flow,99 L/min',                 # tag quoted
    'P-101,"flow",99 L/min',                 # property quoted
    'P-101,flow,"99 L/min"',                 # value+unit quoted together
    'P-101,flow,"99",L/min',                 # value quoted, unit separate
    'P-101,flow,99,"L/min"',                 # unit quoted
    '"P-101","flow","99","L/min"',           # everything quoted, four fields
    ' "P-101" , "flow" , "99 L/min" ',       # spaces around quoted fields
])
def test_quoted_field_in_any_position_is_reported(row):
    r = audit_documents({"a.csv": GOOD + "\n" + row, "b.csv": GOOD})
    w = unparsed(r)
    assert len(w) == 1 and w[0]["line"] == 2 and w[0]["excerpt"] == row.strip(), row
    assert r["coverage"]["a.csv"]["rows_unparsed"] == 1
    assert report.build_view(r).verdict == report.VERDICT_PARTIAL


# ---------- 5. embedded commas / malformed CSV candidates are not parsed as measurements
@pytest.mark.parametrize("row", [
    '"P-101","flow, total","99 L/min"',      # embedded comma inside a quoted property
    'P-101,"flow, total",99 L/min',
    '"P-101","flow","99,5 L/min"',           # embedded decimal comma inside the quoted value
    '"P-101","flow","99 L/min',              # unterminated quote
    'P-101,"flow,"99 L/min"',                # stray quote
    '"P-101","flow",""99"" L/min"',          # doubled-quote escapes
])
def test_malformed_or_embedded_comma_candidates_are_never_compared(row):
    r = audit_documents({"a.csv": GOOD + "\n" + row, "b.csv": GOOD + "\n" + row})
    assert r["comparisons"] == 1 and r["findings"] == [] and len(r["matches"]) == 1     # only the supported pair
    assert r["records"] == 2                                                             # the candidate row was not parsed
    assert len(unparsed(r)) == 2, row
    assert report.build_view(r).verdict == report.VERDICT_PARTIAL


# ---------- 6. things that are not measurements must stay quiet
@pytest.mark.parametrize("line", [
    "",                                      # blank
    "   ",
    "# Pump list",                           # heading
    "TAG,property,value,unit",               # CSV header
    '"TAG","property","value","unit"',       # quoted CSV header
    '"Tag","Property","Value (2026 rev)"',   # quoted header with digits inside text
    "This pump, which was installed in 2019, is fine",    # prose with commas
    '"Notes","see section 4","n/a"',         # quoted but third field is not numeric
    "P-101,flow",                            # two cells
    '"P-101","flow"',
    '"P-101"\t"flow"\t"99 L/min"',           # not comma separated: not a CSV candidate
])
def test_non_measurement_lines_do_not_raise_misleading_warnings(line):
    r = audit_documents({"a.csv": GOOD + "\n" + line, "b.csv": GOOD})
    assert unparsed(r) == [], line
    assert r["coverage"]["a.csv"]["rows_unparsed"] == 0
    assert report.build_view(r).verdict == report.VERDICT_MATCH


# ---------- 7. supported rows keep their exact behaviour
def test_supported_unquoted_csv_and_markdown_rows_are_unchanged():
    r = audit_documents({"a.csv": "P-101,pressure,10 bar\nV-201,diameter,25,mm", "b.csv": "P-101,pressure,900 kPa\nV-201,diameter,0.025 m"})
    assert r["status"] == "DIFFERENCES" and r["comparisons"] == 2
    assert [f["tag"] for f in r["findings"]] == ["p-101"] and len(r["matches"]) == 1
    assert unparsed(r) == [] and report.build_view(r).verdict == report.VERDICT_CONFLICTS
    m = audit_documents({"a.md": "| P-101 | pressure | 10 bar |", "b.md": "P-101 | pressure | 10 bar"})
    assert m["status"] == "NO_DIFFERENCES_FOUND" and unparsed(m) == [] and report.build_view(m).verdict == report.VERDICT_MATCH


def test_existing_pipe_row_rules_keep_working():
    r = audit_documents({"a.md": "P-1 | pressure | ~10 bar", "b.md": "P-1 | pressure | 10 bar"})
    assert len(unparsed(r)) == 1 and r["comparisons"] == 0          # unchanged D06 behaviour for approximate values


def test_quoted_value_in_a_pipe_row_is_reported_too():
    r = audit_documents({"a.md": '| P-1 | pressure | 10 bar |\nP-101 | flow | "99 L/min"', "b.md": '| P-1 | pressure | 10 bar |\nP-101 | flow | "1 L/min"'})
    assert [(x["document"], x["line"]) for x in unparsed(r)] == [("a.md", 2), ("b.md", 2)]
    assert report.build_view(r).verdict == report.VERDICT_PARTIAL


def test_csv_grammar_still_applies_only_to_csv_named_documents():
    r = audit_documents({"a.txt": GOOD, "b.txt": GOOD})
    assert r["comparisons"] == 0 and unparsed(r) == []             # unchanged: bare CSV in .txt is not a row (no quotes involved)


# ---------- propagation to every surface
BOTH_A = GOOD + '\n"P-101","flow","99 L/min"'
BOTH_B = GOOD + '\n"P-101","flow","1 L/min"'


def test_api_exposes_the_incomplete_coverage():
    c = TestClient(api_app)
    body = c.post("/audit", json={"documents": {"a.csv": BOTH_A, "b.csv": BOTH_B}}).json()
    assert body["status"] == "NO_DIFFERENCES_FOUND"                   # engine status keeps its meaning ...
    assert [w["code"] for w in body["warnings"]] == ["UNPARSED_ROW", "UNPARSED_ROW"]     # ... but warnings/coverage show the gap
    assert body["coverage"]["a.csv"]["rows_unparsed"] == 1 and body["warnings"][0]["excerpt"].startswith('"P-101"')
    assert json.loads(json.dumps(body)) == body


def test_mcp_tool_result_uses_the_same_engine_result():
    spec = '| P-1 | pressure | 10 bar |\nP-101 | flow | "99 L/min"'
    bom = '| P-1 | pressure | 10 bar |\nP-101 | flow | "1 L/min"'
    r = compare_engineering_rows(spec, bom)
    assert [w["code"] for w in r["warnings"]] == ["UNPARSED_ROW", "UNPARSED_ROW"]
    assert r == audit_documents({"spec": spec, "bom": bom})


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "ui.py"), default_timeout=60)


def test_streamlit_ui_shows_partial_coverage_not_a_success_banner():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("a.csv", BOTH_A.encode(), "text/csv")
    at.get("file_uploader")[1].upload("b.csv", BOTH_B.encode(), "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    assert not at.success                                             # never the green "no conflicts" banner
    assert "incomplete" in " ".join(w.value for w in at.warning).lower()
    audit = at.session_state["audit"]
    assert audit["view"].verdict == report.VERDICT_PARTIAL and len(audit["view"].coverage) == 2
    assert "99 L/min" in audit["markdown"] and "incomplete" in audit["markdown"].lower()
    assert json.loads(audit["json"])["result"]["coverage"]["a.csv"]["rows_unparsed"] == 1


def test_streamlit_json_export_carries_the_same_verdict_and_completeness():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("a.csv", BOTH_A.encode(), "text/csv")
    at.get("file_uploader")[1].upload("b.csv", BOTH_B.encode(), "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    audit = at.session_state["audit"]
    exported = json.loads(audit["json"])
    assert exported["verdict"] == audit["view"].verdict == report.VERDICT_PARTIAL
    assert exported["coverage_complete"] is False
    assert exported["result"]["verdict"] == exported["verdict"]
