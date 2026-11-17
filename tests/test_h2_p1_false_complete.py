"""H2-001 (issue #37) and H2-002 (issue #38): two independently reproduced false-complete results.

H2-001: an absolute temperature and a temperature difference for the same item in ONE source were collapsed as a
"consistent repeat", so the audit reported a clean, complete result. The outcome also depended on row order.
H2-002: a metric prefix (kgal, kcal, kBtu, kton, mhp, ...) bypassed the refusal of ambiguous unit names, so Pint's
default meaning was silently used.

Rule for both: never a clean or complete result for these rows. Normal conversions and explicit units stay comparable.
These tests were written RED first against e007e9be081ca612111f04eb8465779b233417f2.
"""
import json

import pytest

from app.core import ambiguous_unit, audit_documents
from app.report import build_view, render_markdown

COMPLETE_CLEAN = ("NO_CONFLICTS_IN_COMPARED_ROWS", True)


def _codes(result):
    return [w["code"] for w in result["warnings"]]


def _outcome(result):
    return result["verdict"], result["coverage_complete"]


def _assert_fail_closed(result):
    assert _outcome(result) != COMPLETE_CLEAN
    assert result["coverage_complete"] is False
    json.dumps(result, allow_nan=False)                                   # finite, standard JSON


# ======================================================================================================== H2-001
ABS, INT = "T-01 | temperature | 0 degC", "T-01 | temperature | 273.15 delta_degC"
B_ABS = "T-01 | temperature | 0 degC"


@pytest.mark.parametrize("rows", [(ABS, INT), (INT, ABS)], ids=["absolute-first", "interval-first"])
def test_h2_001_same_source_absolute_and_interval_never_complete(rows):
    r = audit_documents({"a.md": "\n".join(rows), "b.md": B_ABS})
    _assert_fail_closed(r)
    assert r["verdict"] == "NOTHING_COMPARED" and r["comparisons"] == 0
    same = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS" and w.get("scope") == "same_source"]
    assert len(same) == 1 and same[0]["detail"] == "interval_vs_absolute"
    assert {same[0]["reference"]["line"], same[0]["other"]["line"]} == {1, 2}
    dup = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]
    assert len(dup) == 1 and dup[0]["consistent"] is False                # not an informational note
    assert "AMBIGUOUS_COMPARISON" in _codes(r)                            # no row may represent the source
    assert not r["matches"] and not r["findings"]


def test_h2_001_verdict_does_not_depend_on_row_or_source_order():
    variants = [
        {"a.md": f"{ABS}\n{INT}", "b.md": B_ABS},
        {"a.md": f"{INT}\n{ABS}", "b.md": B_ABS},
        {"b.md": B_ABS, "a.md": f"{ABS}\n{INT}"},
        {"b.md": B_ABS, "a.md": f"{INT}\n{ABS}"},
    ]
    outcomes = {(_outcome(r), r["comparisons"], tuple(sorted(_codes(r)))) for r in map(audit_documents, variants)}
    assert len(outcomes) == 1
    assert next(iter(outcomes))[0] == ("NOTHING_COMPARED", False)


@pytest.mark.parametrize("pages", [(ABS, INT), (INT, ABS)], ids=["page1-absolute", "page1-interval"])
def test_h2_001_pdf_pages_of_one_file_are_one_source(pages):
    docs = {"spec.pdf:page-1": pages[0], "spec.pdf:page-2": pages[1], "b.md": B_ABS}
    r = audit_documents(docs)
    _assert_fail_closed(r)
    same = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS" and w.get("scope") == "same_source"]
    assert len(same) == 1
    assert {same[0]["reference"]["document"], same[0]["other"]["document"]} == {"spec.pdf:page-1", "spec.pdf:page-2"}


@pytest.mark.parametrize("rows", [
    (ABS, ABS, INT), (INT, ABS, ABS), (ABS, INT, ABS),
    ("T-01 | temperature | 273.15 K", INT),                                # K under 'temperature' is absolute
], ids=["abs-abs-int", "int-abs-abs", "abs-int-abs", "kelvin-vs-delta"])
def test_h2_001_any_mixed_group_is_a_gap(rows):
    _assert_fail_closed(audit_documents({"a.md": "\n".join(rows), "b.md": B_ABS}))


def test_h2_001_unknown_kind_against_interval_in_one_source_is_a_gap():
    a = "S-01 | setpoint | 300 K\nS-01 | setpoint | 26.85 delta_degC"
    r = audit_documents({"a.md": a, "b.md": "S-01 | setpoint | 300 K"})
    _assert_fail_closed(r)
    same = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS" and w.get("scope") == "same_source"]
    assert len(same) == 1 and same[0]["detail"] == "uncertain"


def test_h2_001_control_same_kind_absolute_repeats_stay_comparable():
    r = audit_documents({"a.md": "T-01 | temperature | 0 degC\nT-01 | temperature | 273.15 K", "b.md": B_ABS})
    assert _outcome(r) == COMPLETE_CLEAN and r["comparisons"] == 1
    dup = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]
    assert len(dup) == 1 and dup[0]["consistent"] is True
    assert "TEMPERATURE_SEMANTICS" not in _codes(r)


def test_h2_001_control_same_kind_interval_repeats_stay_comparable():
    a = "DT-1 | temperature rise | 5 delta_degC\nDT-1 | temperature rise | 5 K"
    r = audit_documents({"a.md": a, "b.md": "DT-1 | temperature rise | 9 delta_degF"})
    assert _outcome(r) == COMPLETE_CLEAN and r["comparisons"] == 1


def test_h2_001_control_cross_source_mismatch_is_still_reported():
    r = audit_documents({"a.md": INT, "b.md": B_ABS})
    assert [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS"][0].get("scope") is None
    _assert_fail_closed(r)


def test_h2_001_report_and_markdown_show_the_gap():
    r = audit_documents({"a.md": f"{ABS}\n{INT}", "b.md": B_ABS})
    view = build_view(r, {"a.md": "Document A", "b.md": "Document B"})
    assert view.coverage_complete is False
    assert any(item.code == "TEMPERATURE_SEMANTICS" for item in view.coverage)
    md = render_markdown(view, "2026-11-18 00:00 UTC")
    assert "Coverage complete: no" in md and "NOTHING_COMPARED" in md


# ======================================================================================================== H2-002
PREFIXED_AMBIGUOUS = [
    "kgal", "kilogallon", "kgals", "Mgal", "mGal", "kcal", "kilocalorie", "Mcal", "kBtu", "MBtu", "kton", "megaton",
    "Mton", "mhp", "khp", "kqt", "kpint", "kgal/min", "kcal/h", "kBtu/h", "m^3/kgal", "kgal*s",
]


@pytest.mark.parametrize("unit", PREFIXED_AMBIGUOUS)
def test_h2_002_prefixed_ambiguous_units_are_refused(unit):
    assert ambiguous_unit(unit) is not None, unit
    r = audit_documents({"a.md": f"X-1 | value | 1 {unit}", "b.md": f"X-1 | value | 1 {unit}"})
    assert "AMBIGUOUS_UNIT" in _codes(r)
    assert r["comparisons"] == 0
    _assert_fail_closed(r)


def test_h2_002_exact_reproduction_is_no_longer_clean():
    r = audit_documents({"a.md": "FL-01 | volume | 1 kgal", "b.md": "FL-01 | volume | 3785.411784 L"})
    assert r["verdict"] == "NOTHING_COMPARED"
    _assert_fail_closed(r)
    w = [w for w in r["warnings"] if w["code"] == "AMBIGUOUS_UNIT"][0]
    assert w["ambiguous_name"] == "kgal" and "US_liquid_gallon" in w["alternatives"] and w["line"] == 1


@pytest.mark.parametrize("unit", ["gal", "gallon", "gallons", "cal", "calorie", "hp", "horsepower", "Btu", "BTU",
                                  "ton", "tons", "quart", "qt", "pint", "pt", "bbl", "barrel", "Mbbl"])
def test_h2_002_control_bare_ambiguous_units_stay_refused(unit):
    assert ambiguous_unit(unit) is not None, unit


EXPLICIT_OR_UNRELATED = [
    "US_liquid_gallon", "imperial_gallon", "US_liquid_quart", "imperial_quart", "imperial_pint", "thermochemical_calorie",
    "international_calorie", "metric_horsepower", "electrical_horsepower", "boiler_horsepower", "short_ton", "long_ton",
    "metric_ton", "tonne", "oil_barrel", "imperial_barrel", "kPa", "hPa", "MPa", "Pa", "kW", "MW", "kWh", "kJ", "MJ",
    "kg", "km", "mm", "um", "L/min", "m^3/s", "degC", "delta_degC", "K", "bar", "psi", "N*m", "rpm", "Hz", "kHz", "A",
    "mA", "V", "kV", "ohm", "lb", "ft", "inch", "mi",
]


@pytest.mark.parametrize("unit", EXPLICIT_OR_UNRELATED)
def test_h2_002_control_explicit_and_si_prefixed_units_stay_comparable(unit):
    assert ambiguous_unit(unit) is None, unit
    r = audit_documents({"a.md": f"X-1 | value | 2 {unit}", "b.md": f"X-1 | value | 2 {unit}"})
    assert _outcome(r) == COMPLETE_CLEAN, (unit, _codes(r))


def test_h2_002_control_explicit_gallon_pair_stays_clean():
    r = audit_documents({"a.md": "FL-01 | volume | 1000 US_liquid_gallon", "b.md": "FL-01 | volume | 3785.411784 L"})
    assert _outcome(r) == COMPLETE_CLEAN


# ======================================================================================================== cross-surface
CASES = {
    "h2_001": (f"{ABS}\n{INT}", B_ABS),
    "h2_001_reversed": (f"{INT}\n{ABS}", B_ABS),
    "h2_002": ("FL-01 | volume | 1 kgal", "FL-01 | volume | 3785.411784 L"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_cross_surface_api(case):
    from fastapi.testclient import TestClient
    from app.api import app
    a, b = CASES[case]
    resp = TestClient(app).post("/audit", json={"documents": {"a.md": a, "b.md": b}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["coverage_complete"] is False and body["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"


@pytest.mark.parametrize("case", list(CASES))
def test_cross_surface_mcp(case):
    from app.mcp_server import compare_engineering_rows
    a, b = CASES[case]
    _assert_fail_closed(compare_engineering_rows(a, b))


def _ui_audit(a, b):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "ui.py"), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", a.encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", b.encode(), "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception
    return at


@pytest.mark.parametrize("case", list(CASES))
def test_cross_surface_streamlit_both_views_and_exports(case):
    a, b = CASES[case]
    at = _ui_audit(a, b)
    audit = at.session_state["audit"]
    exported = json.loads(audit["json"])
    assert exported["coverage_complete"] is False and exported["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert "Coverage complete: no" in audit["markdown"]
    raw = (audit["json"], audit["markdown"])
    for view in ("Overview", "Detailed evidence", "Overview"):
        at.segmented_control(key="view_mode").set_value(view).run()
        assert not at.exception
        assert len(at.success) == 0                                       # never the green "clean" state
        assert (at.session_state["audit"]["json"], at.session_state["audit"]["markdown"]) == raw
    assert at.session_state["audit_count"] == 1                          # the view switch never re-runs the audit
    # an input change removes the old result and both downloads
    at.get("file_uploader")[1].upload("b.md", b"FL-01 | volume | 1 L", "text/markdown")
    at.run()
    assert "audit" not in at.session_state or not at.session_state["audit"]
    assert len(at.get("download_button")) == 0
