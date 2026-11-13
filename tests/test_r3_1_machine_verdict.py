"""R3.1 P1-A1: the audit result carries an authoritative, serialized `verdict` and `coverage_complete`.

`status` keeps the old comparison outcome (NO_DIFFERENCES_FOUND never establishes completeness). `verdict` and
`coverage_complete` come from one function in app.core and must agree with the Streamlit/Markdown interpretation.
`coverage_complete` is scoped to the supported structured rows; it is not a certification of a document."""
import json

import pytest
from fastapi.testclient import TestClient

from app import core, report
from app.api import app as api_app
from app.mcp_server import compare_engineering_rows

GOOD = "P-1,pressure,10 bar"
PIPE_GOOD = "P-1 | pressure | 10 bar"


def run(docs, **kw):
    return core.audit_documents(docs, **kw)


def view_of(result):
    return report.build_view(result)


def agree(result):
    """verdict/coverage_complete in the result equal the presentation layer's interpretation."""
    v = view_of(result)
    assert result["verdict"] == v.verdict
    assert result["coverage_complete"] == (not v.coverage and result["comparisons"] > 0)
    md = report.render_markdown(v, "t")
    if result["verdict"] == report.VERDICT_PARTIAL:
        assert "incomplete" in md.lower()


HIDDEN = {
    "a.csv": GOOD + '\n"P-101","flow","99 L/min"',
    "b.csv": GOOD + '\n"P-101","flow","1 L/min"',
}


def test_hidden_csv_conflict_is_partial_through_core_api_json():
    r = run(HIDDEN)
    assert r["status"] == "NO_DIFFERENCES_FOUND"                      # backward compatible
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE"
    assert r["coverage_complete"] is False
    agree(r)
    api = TestClient(api_app).post("/audit", json={"documents": HIDDEN}).json()
    assert api == r and api["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and api["coverage_complete"] is False
    exported = json.loads(json.dumps({"result": r, "input_issues": []}))
    assert exported["result"]["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE"
    assert exported["result"]["coverage_complete"] is False


def test_quoted_pipe_mcp_example_is_partial():
    a = PIPE_GOOD + '\n"P-101" | "flow" | "99 L/min"'
    b = PIPE_GOOD + '\n"P-101" | "flow" | "1 L/min"'
    r = compare_engineering_rows(a, b)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and r["warnings"]
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    agree(r)


def test_fully_supported_equivalent_pair_is_complete():
    r = compare_engineering_rows("P-1 | pressure | 10 bar", "P-1 | pressure | 1000 kPa")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    agree(r)


def test_genuine_mismatch_with_complete_coverage():
    r = run({"a": "P-1 | pressure | 10 bar", "b": "P-1 | pressure | 12 bar"})
    assert r["status"] == "DIFFERENCES"
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is True
    agree(r)


def test_conflict_plus_gap_is_conflict_with_incomplete_coverage():
    a = "P-1 | pressure | 10 bar\nP-2 | flow | 5 L/min"
    b = "P-1 | pressure | 12 bar"
    r = run({"a": a, "b": b})                                         # P-2 unmatched
    assert r["unmatched"]
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False
    agree(r)


def test_unmatched_row_makes_coverage_incomplete():
    r = run({"a": "P-1 | pressure | 10 bar\nP-2 | flow | 5 L/min", "b": "P-1 | pressure | 10 bar"})
    assert r["status"] == "NO_DIFFERENCES_FOUND"
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    agree(r)


def test_unparsed_row_makes_coverage_incomplete():
    r = run({"a": PIPE_GOOD + "\nP-2 | flow | 10,5 L/min", "b": PIPE_GOOD})
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    agree(r)


def test_no_comparable_rows():
    r = run({"a": "nothing here", "b": "nor here"})
    assert r["status"] == "NO_COMPARABLE_ROWS"
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    agree(r)
    r = run({"a": "P-1 | pressure | 10 bar", "b": "P-2 | pressure | 10 bar"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    agree(r)


def test_consistent_duplicate_is_informational_not_a_gap():
    r = run({"a": "P-1 | pressure | 10 bar\nP-1 | pressure | 1000 kPa", "b": "P-1 | pressure | 10 bar"})
    assert any(w["code"] == "DUPLICATE_IDENTIFIER" and w["consistent"] for w in r["warnings"])
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    agree(r)


def test_inconsistent_duplicate_is_a_gap_and_conflict():
    r = run({"a": "P-1 | pressure | 10 bar\nP-1 | pressure | 20 bar", "b": "P-1 | pressure | 10 bar"})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False
    agree(r)


def test_verdict_values_are_shared_constants():
    assert report.VERDICT_CONFLICTS is core.VERDICT_CONFLICTS
    assert report.VERDICT_PARTIAL is core.VERDICT_PARTIAL
    assert report.VERDICT_MATCH is core.VERDICT_MATCH
    assert report.VERDICT_NONE is core.VERDICT_NONE


def test_api_and_mcp_return_identical_serialized_fields():
    docs = {"spec": "P-1 | pressure | 10 bar", "bom": "P-1 | pressure | 1000 kPa"}
    api = TestClient(api_app).post("/audit", json={"documents": docs}).json()
    mcp = compare_engineering_rows(docs["spec"], docs["bom"])
    assert api == mcp == run(docs)
    assert {"verdict", "coverage_complete"} <= set(api)


def test_old_result_without_verdict_still_builds_a_view():
    r = run({"a": "P-1 | pressure | 10 bar", "b": "P-1 | pressure | 1000 kPa"})
    legacy = {k: v for k, v in r.items() if k not in ("verdict", "coverage_complete")}
    assert report.build_view(legacy).verdict == r["verdict"]


def test_mcp_description_states_the_csv_limitation():
    doc = compare_engineering_rows.__doc__
    assert "verdict" in doc and "coverage_complete" in doc
    assert "pipe" in doc.lower() and "CSV" in doc and "not" in doc.lower()


def test_readme_documents_fields_and_mcp_limitation():
    from pathlib import Path
    text = Path(__file__).resolve().parent.parent.joinpath("README.md").read_text(encoding="utf-8")
    for needle in ("coverage_complete", "NO_CONFLICTS_PARTIAL_COVERAGE", "NO_DIFFERENCES_FOUND", "pipe"):
        assert needle in text
    assert "does not enable CSV" in text
