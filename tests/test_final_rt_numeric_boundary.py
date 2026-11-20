"""FINAL-RT-004 and FINAL-RT-005 (#54): inclusive tolerance boundary and truthful effective tolerance.

relative_difference must agree with an exact rational oracle (fractions.Fraction on the float values), and a pair that is
exactly on the tolerance must be within it. No global epsilon is added. Limit, stated in the README: a decimal text that is
not exactly representable in binary (0.099) or a unit factor that rounds (mm to m) can place a mathematically exact
boundary pair about 1e-16 on either side of the threshold.
"""
import itertools
import json
import math
import random
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import assume, given, settings, strategies as st

from app.core import ZERO_TOLERANCE_FLOOR, audit_documents, relative_difference
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"


def exact(a, b):
    a, b = Fraction(a), Fraction(b)
    scale = max(abs(a), abs(b))
    return Fraction(0) if scale == 0 else abs(a - b) / scale


def run(a, b, tol=0.01, unit="m"):
    return audit_documents({"a.md": f"X-1 | length | {a} {unit}\n", "b.md": f"X-1 | length | {b} {unit}\n"}, tol)


# ---- relative_difference against an independent oracle -------------------------------------------------------------
@settings(max_examples=2000, deadline=None)
@given(st.floats(allow_nan=False, allow_infinity=False, width=64), st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_property_relative_difference_matches_the_rational_oracle(a, b):
    got, want = relative_difference(a, b), exact(a, b)
    assert 0.0 <= got <= 2.0 and math.isfinite(got)
    if want == 0:
        assert got == 0.0
    else:
        assert abs(Fraction(got) - want) <= want * Fraction(5, 10**16) + Fraction(1, 10**300)


@pytest.mark.parametrize("a,b", [(99.0, 100.0), (100.0, 99.0), (98.0, 100.0), (95.0, 100.0), (80.0, 100.0), (-99.0, -100.0), (99e20, 100e20), (0.0, 0.0),
                                 (0.0, 5.0), (-0.0, 0.0), (1e308, -1e308), (1.7e308, 1.7e308), (5e-324, 1e-323), (3.0, -3.0)])
def test_relative_difference_edge_values(a, b):
    got = relative_difference(a, b)
    assert math.isfinite(got)
    assert abs(Fraction(got) - exact(a, b)) <= Fraction(1, 10**15)


def test_same_sign_pair_on_the_one_percent_boundary_is_exactly_the_decimal_boundary():
    assert relative_difference(99.0, 100.0) == 0.01 and relative_difference(100.0, 99.0) == 0.01
    assert relative_difference(98.0, 100.0) == 0.02 and relative_difference(95.0, 100.0) == 0.05 and relative_difference(80.0, 100.0) == 0.2


# ---- inclusive boundary through the engine --------------------------------------------------------------------------
@pytest.mark.parametrize("tol,a,b", [(0.01, 99, 100), (0.02, 98, 100), (0.05, 95, 100), (0.2, 80, 100), (0.01, 990, 1000), (0.2, 8, 10)])
@pytest.mark.parametrize("swap", [False, True])
def test_a_pair_exactly_on_the_tolerance_is_within_it(tol, a, b, swap):
    r = run(b, a, tol) if swap else run(a, b, tol)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and not r["findings"]


@pytest.mark.parametrize("tol,a,b", [(0.01, 98.99, 100), (0.02, 97.99, 100), (0.05, 94.99, 100), (0.2, 79.9, 100), (0.01, 99, 101)])
def test_a_pair_just_outside_the_tolerance_conflicts(tol, a, b):
    assert run(a, b, tol)["verdict"] == "CONFLICTS_FOUND"


def test_the_reported_case_99_m_against_100_m_at_the_default_tolerance():
    r = audit_documents({"a.md": "X-1 | length | 99 m\n", "b.md": "X-1 | length | 100 m\n"})
    assert r["tolerance"] == 0.01 and r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["matches"][0]["relative_difference"] == 0.01


@pytest.mark.parametrize("a,b", [("99", "100"), ("-99", "-100"), ("990000000000000000000", "1000000000000000000000"), ("0.0966796875", "0.09765625")])
def test_boundary_holds_for_signs_and_magnitudes(a, b):
    assert run(a, b)["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"


def test_boundary_holds_after_si_conversion():
    for a, b, unit in (("99", "100", "bar"), ("99", "100", "kPa"), ("99", "100", "MPa"), ("99", "100", "km")):
        assert run(a, b, 0.01, unit)["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS", unit


@settings(max_examples=600, deadline=None)
@given(st.integers(1, 10**6), st.integers(1, 10**6), st.sampled_from(["0.01", "0.02", "0.05", "0.2", "0.1"]))
def test_property_classification_equals_the_exact_oracle_away_from_the_boundary(a, b, tol_text):
    want = exact(a, b)
    limit = Fraction(Decimal(tol_text))
    assume(abs(want - limit) > Fraction(1, 10**12))              # float noise can only matter closer than that
    r = run(a, b, float(tol_text))
    assert (r["verdict"] == "CONFLICTS_FOUND") == (want > limit)


@settings(max_examples=300, deadline=None)
@given(st.integers(1, 10**6), st.sampled_from([(1, 100), (2, 100), (5, 100), (20, 100)]))
def test_property_a_constructed_exact_boundary_pair_is_within(base, ratio):
    num, den = ratio
    a = base * den
    b = a - base * num                                           # |a-b|/a == num/den exactly, all integers
    r = run(a, b, num / den)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS", (a, b, num / den)


# ---- tolerance floor and the old H2-004 controls ---------------------------------------------------------------------
def test_tolerance_zero_is_the_floor_not_exact_equality():
    r = audit_documents({"a.md": "F-1 | flow | 12 L/min\n", "b.md": "F-1 | flow | 0.0002 m^3/s\n"}, 0.0)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert run("1", "1.0000000000009", 0.0)["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert run("1", "1.000000000002", 0.0)["verdict"] == "CONFLICTS_FOUND"


def test_extreme_finite_pair_stays_finite_and_json_safe():
    big = "1" + "7" * 308
    r = run(big, "-" + big)
    assert r["findings"][0]["relative_difference"] == 2.0
    json.dumps(r, allow_nan=False)


# ---- requested and effective tolerance, in every output --------------------------------------------------------------
@pytest.mark.parametrize("requested,effective", [(0.0, ZERO_TOLERANCE_FLOOR), (1e-13, ZERO_TOLERANCE_FLOOR), (0.01, 0.01), (0.2, 0.2)])
def test_result_reports_requested_and_effective_tolerance(requested, effective):
    r = run("1", "1", requested)
    assert r["tolerance"] == requested and r["effective_tolerance"] == effective
    json.dumps(r, allow_nan=False)


def test_old_json_fields_are_kept():
    r = run("1", "1")
    for key in ("status", "records", "comparisons", "tolerance", "findings", "matches", "unmatched", "warnings", "coverage", "scope",
                "verdict", "coverage_complete"):
        assert key in r


def test_mcp_and_api_report_the_effective_tolerance():
    m = compare_engineering_rows("X-1 | length | 1 m\n", "X-1 | length | 1 m\n", 0.0)
    assert m["tolerance"] == 0.0 and m["effective_tolerance"] == ZERO_TOLERANCE_FLOOR
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": "X-1 | length | 1 m\n", "b.md": "X-1 | length | 1 m\n"}, "tolerance": 0}).json()
    assert body["tolerance"] == 0 and body["effective_tolerance"] == ZERO_TOLERANCE_FLOOR


def test_markdown_states_requested_and_enforced_tolerance_when_they_differ():
    r = run("1", "1", 0.0)
    md = render_markdown(build_view(r, {"a.md": "Document A", "b.md": "Document B"}), "2026-11-19 00:00 UTC")
    assert "requested 0 %" in md and "enforced" in md and "1e-10 %" in md
    plain = render_markdown(build_view(run("1", "1", 0.01), {"a.md": "Document A", "b.md": "Document B"}), "2026-11-19 00:00 UTC")
    assert "Allowed difference: 1 %." in plain and "enforced" not in plain


# ---- wording: within tolerance is not literal equality ---------------------------------------------------------------
def test_a_nonzero_difference_inside_the_tolerance_is_not_written_with_an_equals_sign():
    r = run("99", "100")
    view = build_view(r, {"a.md": "Document A", "b.md": "Document B"})
    summary = view.equivalents[0].summary
    assert " = " not in summary and "within tolerance" in summary and "1 %" in summary


def test_a_difference_at_rounding_noise_level_keeps_the_equals_sign():
    r = audit_documents({"a.md": "F-1 | flow | 12 L/min\n", "b.md": "F-1 | flow | 0.0002 m^3/s\n"}, 0.01)
    assert build_view(r, {}).equivalents[0].summary == "12 L/min = 0.0002 m^3/s"


def test_streamlit_overview_does_not_claim_equality_for_a_one_percent_pair():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", b"X-1 | length | 99 m\n", "text/markdown")
    at.get("file_uploader")[1].upload("b.md", b"X-1 | length | 100 m\n", "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "99 m = 100 m" not in text and "within tolerance" in text
    exported = json.loads(at.session_state["audit"]["json"])
    assert exported["result"]["effective_tolerance"] == 0.01
