"""FR-03 (#47): conservative temperature semantics.

Policy under test (README, "Temperature semantics"): a value in an offset unit (degC, degF) converts differently as an
absolute temperature and as a temperature difference. The kind is taken from the UNIT (delta_degC, delta_degF) or from a
clear property label. Abbreviations such as DT or diff, and domain terms such as superheat, are NOT clear labels. A row
whose kind is unknown is not compared with a different value when an offset unit is involved: TEMPERATURE_SEMANTICS
(detail unknown_kind) and incomplete coverage. Plain K, degR and other linear units do not depend on the kind.
"""
import itertools
import json

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import Observation, audit_documents
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown


def T(a, b, field="temperature", fa=None, fb=None, tol=1e-6):
    return audit_documents({"a.md": f"T-1 | {fa or field} | {a}\n", "b.md": f"T-1 | {fb or field} | {b}\n"}, tol)


def codes(r):
    return [w["code"] for w in r["warnings"]]


def assert_refused(r, detail=None):
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False and r["comparisons"] == 0
    assert r["matches"] == [] and r["findings"] == []
    temp = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS"]
    assert len(temp) == 1
    if detail:
        assert temp[0]["detail"] == detail
    assert temp[0]["reference"]["line"] == 1 and temp[0]["other"]["line"] == 1
    assert temp[0]["reference"]["excerpt"].startswith("T-1 |") and temp[0]["other"]["excerpt"].startswith("T-1 |")
    json.dumps(r, allow_nan=False)
    return temp[0]


def assert_clean_match(r):
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and len(r["matches"]) == 1


# ---- the two reported false equivalences, both row orders -----------------------------------------------------------
@pytest.mark.parametrize("a,b", [("100 degC", "180 degF"), ("180 degF", "100 degC"), ("100 degC", "212 degF")])
@pytest.mark.parametrize("field", ["DT", "dT", "dt", "diff", "Diff", "dTemp"])
def test_abbreviations_are_never_a_clear_label(a, b, field):
    assert_refused(T(a, b, field), "unknown_kind")


@pytest.mark.parametrize("a,b", [("5 degC", "41 degF"), ("41 degF", "5 degC"), ("5 degC", "9 degF")])
@pytest.mark.parametrize("field", ["superheat", "subcooling", "approach", "range", "spread", "superheat temperature"])
def test_domain_dependent_terms_are_never_a_clear_label(a, b, field):
    assert_refused(T(a, b, field), "unknown_kind")


def test_an_unlabelled_offset_value_is_not_assumed_absolute():
    assert_refused(T("20 degC", "293.15 K", "reading"), "unknown_kind")
    assert_refused(T("68 degF", "20 degC", "value"), "unknown_kind")


# ---- same unit and identical value: kind cannot change the result ---------------------------------------------------
def test_identical_unlabelled_offset_values_are_still_comparable():
    assert_clean_match(T("80 degC", "80 degC", "setpoint"))
    assert_clean_match(T("80 degC", "80 degC", "DT"))


def test_different_unlabelled_offset_values_in_the_same_unit_are_refused():
    assert_refused(T("80 degC", "80.5 degC", "setpoint"), "unknown_kind")


def test_plain_kelvin_without_a_label_is_comparable_with_plain_kelvin_and_rankine():
    assert_clean_match(T("300 K", "300 K", "reading"))
    assert_clean_match(T("300 K", "540 degR", "reading"))
    r = T("300 K", "301 K", "reading", tol=0.01)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and len(r["matches"]) == 1
    r = T("300 K", "330 K", "reading")
    assert r["verdict"] == "CONFLICTS_FOUND" and r["findings"][0]["code"] == "VALUE_MISMATCH"


# ---- positive controls that must keep working -----------------------------------------------------------------------
@pytest.mark.parametrize("a,b,field", [
    ("100 degC", "212 degF", "design temperature"), ("212 degF", "100 degC", "design temperature"),
    ("0 degC", "273.15 K", "temperature"), ("20 degC", "293.15 K", "temp"), ("68 degF", "20 degC", "temperature"),
    ("20 degC", "293.15 K", "operating_temperature"), ("-40 degC", "-40 degF", "Temperature"),
])
def test_explicitly_labelled_absolute_temperatures_still_convert_correctly(a, b, field):
    assert_clean_match(T(a, b, field))


def test_absolute_labelled_wrong_value_is_a_conflict():
    assert T("20 degC", "20 K", "temperature")["verdict"] == "CONFLICTS_FOUND"


@pytest.mark.parametrize("a,b,field", [
    ("10 delta_degC", "18 delta_degF", "temperature difference"), ("5 delta_degC", "5 K", "temperature difference"),
    ("5 degC", "5 K", "temperature rise"), ("9 degF", "5 K", "temperature rise"), ("5 delta_degF", "2.7777777778 K", "delta T"),
    ("10 degC", "18 degF", "deltaT"), ("10 delta_degC", "18 delta_degF", "reading"), ("5 degC", "9 degF", "temperature drop"),
])
def test_explicit_intervals_still_convert_as_differences(a, b, field):
    assert_clean_match(T(a, b, field, tol=1e-9))


def test_tolerance_zero_uses_the_effective_floor_for_temperatures():
    assert_clean_match(T("0 degC", "273.15 K", "temperature", tol=0.0))
    assert T("0 degC", "273.1500000005 K", "temperature", tol=0.0)["verdict"] == "CONFLICTS_FOUND"


# ---- absolute against interval (#37 regression), all orders ---------------------------------------------------------
@pytest.mark.parametrize("a,b", [("5 delta_degC", "278.15 K"), ("278.15 K", "5 delta_degC")])
def test_interval_against_absolute_is_still_refused(a, b):
    assert_refused(T(a, b, "temperature"), "interval_vs_absolute")


def test_interval_against_unknown_is_still_refused():
    assert_refused(T("5 delta_degC", "5 K", "reading"), "uncertain")


ABS, INT = "T-01 | temperature | 0 degC", "T-01 | temperature | 273.15 delta_degC"


@pytest.mark.parametrize("rows", list(itertools.permutations([ABS, INT, ABS])))
def test_same_source_absolute_and_interval_repeats_stay_a_gap(rows):
    r = audit_documents({"a.md": "\n".join(rows) + "\n", "b.md": ABS + "\n"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert any(w["code"] == "TEMPERATURE_SEMANTICS" and w.get("scope") == "same_source" for w in r["warnings"])


# ---- same-source repeats with an unknown kind -----------------------------------------------------------------------
def test_same_source_unknown_offset_repeats_with_different_values_are_a_gap():
    r = audit_documents({"a.md": "S-1 | setpoint | 80 degC\nS-1 | setpoint | 80.4 degC\n", "b.md": "S-1 | setpoint | 80 degC\n"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert any(w["code"] == "TEMPERATURE_SEMANTICS" and w["detail"] == "unknown_kind" and w["scope"] == "same_source" for w in r["warnings"])


def test_same_source_identical_unknown_offset_repeats_stay_consistent():
    r = audit_documents({"a.md": "S-1 | setpoint | 80 degC\nS-1 | setpoint | 80 degC\n", "b.md": "S-1 | setpoint | 80 degC\n"})
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


# ---- group rule equals the pairwise rule ----------------------------------------------------------------------------
def obs(kind, unit, value, line):
    offset_names = {"degC", "degF"}
    return Observation("a.md", line, "T-1", "f", value, unit, value, "kelvin", "[temperature]", f"T-1 | f | {value} {unit}", kind)


KINDS = ["absolute", "interval", "unknown"]
UNITS = ["degC", "K", "delta_degC", "degF"]


@settings(max_examples=400, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(KINDS), st.sampled_from(UNITS), st.sampled_from([1.0, 2.0])), min_size=1, max_size=6))
def test_property_group_check_finds_a_problem_exactly_when_some_pair_has_one(items):
    records = [obs(k, u, v, i + 1) for i, (k, u, v) in enumerate(items)]
    pairwise = any(core._temperature_pair(a, b) for a, b in itertools.combinations(records, 2))
    group = core._temperature_group_problem(records)
    assert bool(group) == pairwise
    if group:
        _, _, first, second = group
        assert core._temperature_pair(first, second)


# ---- machine-readable outcome and display parity --------------------------------------------------------------------
def test_mcp_matches_the_engine_for_dt_and_for_a_labelled_pair():
    bad = compare_engineering_rows("T-1 | DT | 100 degC\n", "T-1 | DT | 180 degF\n")
    assert bad["verdict"] == "NOTHING_COMPARED" and bad["coverage_complete"] is False
    good = compare_engineering_rows("T-1 | design temperature | 100 degC\n", "T-1 | design temperature | 212 degF\n")
    assert good["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and good["coverage_complete"] is True


def test_api_matches_the_engine():
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": "T-1 | superheat | 5 degC\n", "b.md": "T-1 | superheat | 41 degF\n"}}).json()
    assert body["verdict"] == "NOTHING_COMPARED" and body["coverage_complete"] is False


def test_report_and_markdown_explain_the_unknown_kind():
    r = T("100 degC", "180 degF", "DT")
    view = build_view(r, {"a.md": "Document A", "b.md": "Document B"})
    item = [i for i in view.coverage if i.code == "TEMPERATURE_SEMANTICS"][0]
    assert "not clear" in item.summary.lower() or "unknown" in item.summary.lower()
    assert "delta_degC" in item.summary
    md = render_markdown(view, "2026-11-19 00:00 UTC")
    assert "Coverage complete: no" in md and "TEMPERATURE_SEMANTICS" in md and "DT" in md


@pytest.mark.parametrize("rows_a,rows_b,expected", [
    ("T-1 | DT | 100 degC\n", "T-1 | DT | 180 degF\n", ("NOTHING_COMPARED", False)),
    ("T-1 | design temperature | 100 degC\n", "T-1 | design temperature | 212 degF\n", ("NO_CONFLICTS_IN_COMPARED_ROWS", True)),
])
def test_streamlit_export_matches_the_engine(rows_a, rows_b, expected):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "ui.py"), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", rows_a.encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", rows_b.encode(), "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == expected
    assert (len(at.success) == 1) == expected[1]
