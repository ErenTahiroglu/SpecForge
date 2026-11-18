"""FR-02 (#46): repeats of one tag+property inside ONE source must be mutually consistent, not just close to the first row.

Metric: relative difference |a-b| / max(|a|,|b|), effective tolerance max(user tolerance, 1e-12) (README, "Number handling").
Correctness argument for the O(n) test (also in app/core.py): the allowed difference is below 1. A pair of values with
opposite signs, or a zero with a nonzero value, has relative difference 1 or more, so it is always inconsistent, and
min/max have such a pair whenever the group is not strictly one-signed. For a strictly one-signed group the relative
difference is 1 - (smaller magnitude / larger magnitude), which is largest for the smallest and largest magnitude, and
those are the minimum and the maximum of the group. So the group is consistent exactly when relative_difference(min, max)
is within the allowed difference. This is not the case for 'compare with the first row only'.
"""
import itertools
import json

import pytest
from hypothesis import given, settings, strategies as st

from app.core import ZERO_TOLERANCE_FLOOR, audit_documents, relative_difference
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown

TOL = 0.01


def rows(values, unit="bar", tag="P-1", field="pressure"):
    return "\n".join(f"{tag} | {field} | {v} {unit}" for v in values) + "\n"


def run(values_a, values_b=("10",), tol=TOL, unit="bar"):
    return audit_documents({"a.md": rows(values_a, unit), "b.md": rows(values_b, unit)}, tol)


def internal(r):
    return [f for f in r["findings"] if f["code"] == "INTERNAL_CONTRADICTION"]


def dup(r):
    return [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]


def assert_not_clean(r):
    assert r["verdict"] in ("CONFLICTS_FOUND", "NO_CONFLICTS_PARTIAL_COVERAGE", "NOTHING_COMPARED"), r["verdict"]
    assert r["coverage_complete"] is False
    assert [d["consistent"] for d in dup(r)] == [False]
    assert any(w["code"] == "AMBIGUOUS_COMPARISON" for w in r["warnings"])


# ---- the reported counterexample, every row order -------------------------------------------------------------------
@pytest.mark.parametrize("order", list(itertools.permutations(["10", "10.09", "9.91"])))
def test_three_row_counterexample_is_never_clean_in_any_order(order):
    r = run(order)
    assert_not_clean(r)
    assert r["verdict"] == "CONFLICTS_FOUND" and r["comparisons"] == 0
    assert len(internal(r)) >= 1 and all(f["scope"] == "same_source" for f in internal(r))
    decisive = {(f["reference"]["excerpt"], f["other"]["excerpt"]) for f in internal(r)}
    assert any({a, b} == {"P-1 | pressure | 10.09 bar", "P-1 | pressure | 9.91 bar"} for a, b in decisive)


def test_decisive_records_keep_source_line_and_excerpt():
    r = run(["10", "10.09", "9.91"])
    finding = [f for f in internal(r) if {f["reference"]["line"], f["other"]["line"]} == {2, 3}][0]
    for side in ("reference", "other"):
        assert finding[side]["document"] == "a.md" and finding[side]["excerpt"].startswith("P-1 | pressure |")
    assert finding["relative_difference"] == pytest.approx(0.017839445, abs=1e-8)


def test_verdict_and_decisive_pair_do_not_depend_on_row_order():
    outcomes = set()
    for order in itertools.permutations(["10", "10.09", "9.91"]):
        r = run(order)
        outcomes.add((r["verdict"], r["coverage_complete"], r["comparisons"],
                      frozenset(frozenset((f["reference"]["excerpt"], f["other"]["excerpt"])) for f in internal(r)
                                if {f["reference"]["excerpt"], f["other"]["excerpt"]} == {"P-1 | pressure | 10.09 bar", "P-1 | pressure | 9.91 bar"})))
    assert len(outcomes) == 1


# ---- four and more rows: center plus both sides ---------------------------------------------------------------------
@pytest.mark.parametrize("order", list(itertools.permutations(["100", "99.4", "100.6", "100"])))
def test_center_plus_both_sides_beyond_tolerance_is_inconsistent_in_all_24_orders(order):
    r = run(order, ["100"])
    assert_not_clean(r)


@pytest.mark.parametrize("order", list(itertools.permutations(["100", "99.6", "100.4", "100"])))
def test_center_plus_both_sides_inside_tolerance_stays_consistent_in_all_24_orders(order):
    r = run(order, ["100"])
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and r["comparisons"] == 1
    assert [d["consistent"] for d in dup(r)] == [True]
    assert not internal(r)


def test_five_rows_where_only_the_two_extremes_disagree():
    r = run(["100", "100.2", "99.9", "100.9", "99.5"], ["100"])
    assert_not_clean(r)
    assert any({f["reference"]["excerpt"], f["other"]["excerpt"]} == {"P-1 | pressure | 100.9 bar", "P-1 | pressure | 99.5 bar"}
               for f in internal(r))


# ---- tolerance edges ------------------------------------------------------------------------------------------------
def test_tolerance_zero_means_the_floor_not_exact_equality_for_repeats():
    ok = run(["1", "1.0000000000005", "1"], ["1"], tol=0.0)
    assert ok["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and [d["consistent"] for d in dup(ok)] == [True]
    # each repeat is within the floor of the FIRST row (8e-13), but the extremes differ by 1.6e-12
    bad = run(["1.0000000000008", "1", "1.0000000000016"], ["1"], tol=0.0)
    assert_not_clean(bad)


def test_twenty_percent_tolerance_boundaries():
    inside = run(["100", "110", "90"], ["100"], tol=0.2)
    assert inside["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and inside["coverage_complete"] is True
    outside = run(["100", "120", "80"], ["100"], tol=0.2)
    assert_not_clean(outside)


# ---- sign, zero, magnitude ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("values", [["0", "0", "0"], ["-100", "-99.6", "-100.4"]])
def test_zero_and_negative_groups_that_agree_are_consistent(values):
    r = run(values, [values[0]], unit="m")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


@pytest.mark.parametrize("values", [["-100", "-99.4", "-100.6"], ["0", "5", "0"], ["-5", "5", "5"], ["5", "0", "5"], ["-1", "-1", "1"]])
@pytest.mark.parametrize("flip", [False, True])
def test_zero_negative_and_opposite_sign_groups_that_disagree_are_flagged(values, flip):
    vals = values[::-1] if flip else values
    assert_not_clean(run(vals, [values[0]], unit="m"))


def test_extreme_magnitudes_do_not_overflow_or_hide_a_contradiction():
    big = "1" + "0" * 300
    r = audit_documents({"a.md": rows([big, "-" + big, big], "m"), "b.md": rows([big], "m")})
    assert_not_clean(r)
    json.dumps(r, allow_nan=False)
    assert internal(r)[0]["relative_difference"] == 2.0


# ---- other incompatibilities, identical duplicates ------------------------------------------------------------------
def test_identical_duplicates_are_a_consistent_note_not_a_gap():
    r = run(["10", "10", "10"])
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    assert [d["consistent"] for d in dup(r)] == [True]


def test_different_dimension_repeat_is_a_dimension_mismatch_and_not_consistent():
    r = audit_documents({"a.md": "P-1 | pressure | 10 bar\nP-1 | pressure | 10 m\n", "b.md": "P-1 | pressure | 10 bar\n"})
    assert "DIMENSION_MISMATCH" in [w["code"] for w in r["warnings"]]
    assert_not_clean(r)


def test_pages_of_one_pdf_are_one_source():
    docs = {"a.pdf:page-1": rows(["10"]), "a.pdf:page-2": rows(["10.09"]), "a.pdf:page-3": rows(["9.91"]), "b.md": rows(["10"])}
    r = audit_documents(docs, TOL)
    assert_not_clean(r)
    pair = [f for f in internal(r) if {f["reference"]["excerpt"], f["other"]["excerpt"]} == {"P-1 | pressure | 10.09 bar", "P-1 | pressure | 9.91 bar"}]
    assert pair and {pair[0]["reference"]["document"], pair[0]["other"]["document"]} == {"a.pdf:page-2", "a.pdf:page-3"}


def test_two_source_positive_controls_are_unchanged():
    ok = audit_documents({"a.md": rows(["10"]), "b.md": rows(["10.05"])}, TOL)
    assert ok["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and ok["comparisons"] == 1 and ok["coverage_complete"] is True
    far = audit_documents({"a.md": rows(["10"]), "b.md": rows(["11"])}, TOL)
    assert far["verdict"] == "CONFLICTS_FOUND" and far["findings"][0]["code"] == "VALUE_MISMATCH"


def test_repeats_in_both_sources_are_each_checked():
    r = audit_documents({"a.md": rows(["10", "10.09", "9.91"]), "b.md": rows(["10", "10.09", "9.91"])}, TOL)
    assert r["coverage_complete"] is False and r["comparisons"] == 0
    assert [d["consistent"] for d in dup(r)] == [False, False]
    assert any(w["code"] == "AMBIGUOUS_COMPARISON" for w in r["warnings"])


# ---- the O(n) test equals the all-pairs definition ------------------------------------------------------------------
@settings(max_examples=300, deadline=None)
@given(st.lists(st.integers(-30000, 30000), min_size=2, max_size=7),
       st.sampled_from([0.0, 1e-13, 0.001, 0.01, 0.05, 0.2]))
def test_property_extrema_decision_equals_all_pairs(raw, tol):
    values = [f"{v / 1000:.3f}" for v in raw]
    allowed = max(tol, ZERO_TOLERANCE_FLOOR)
    numbers = [float(v) for v in values]
    expected_inconsistent = any(relative_difference(a, b) > allowed for a, b in itertools.combinations(numbers, 2))
    r = audit_documents({"a.md": rows(values, "m"), "b.md": rows(["1"], "m")}, tol)
    flagged = dup(r)[0]["consistent"] is False
    assert flagged == expected_inconsistent, (values, tol)


@settings(max_examples=150, deadline=None)
@given(st.lists(st.integers(1, 30000), min_size=3, max_size=6), st.randoms(use_true_random=False))
def test_property_verdict_is_order_invariant(raw, rnd):
    values = [f"{v / 100:.2f}" for v in raw]
    shuffled = values[:]
    rnd.shuffle(shuffled)
    a, b = run(values, ["100"], unit="m"), run(shuffled, ["100"], unit="m")
    assert (a["verdict"], a["coverage_complete"]) == (b["verdict"], b["coverage_complete"])


# ---- same result through MCP, JSON, Markdown, Streamlit -------------------------------------------------------------
def test_mcp_returns_the_same_not_clean_outcome():
    r = compare_engineering_rows(rows(["10", "10.09", "9.91"]), rows(["10"]))
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False


def test_api_returns_the_same_not_clean_outcome():
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": rows(["10", "10.09", "9.91"]), "b.md": rows(["10"])}}).json()
    assert body["verdict"] == "CONFLICTS_FOUND" and body["coverage_complete"] is False


def test_markdown_and_view_show_both_decisive_rows():
    r = run(["10", "10.09", "9.91"])
    view = build_view(r, {"a.md": "Document A", "b.md": "Document B"})
    assert view.coverage_complete is False and view.internal >= 1
    md = render_markdown(view, "2026-11-19 00:00 UTC")
    assert "Coverage complete: no" in md and "10.09 bar" in md and "9.91 bar" in md and "One document contradicts itself" in md


def test_streamlit_two_uploads_do_not_show_a_green_clean_result():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    ui = Path(__file__).resolve().parent.parent / "app" / "ui.py"
    for order in (["10", "10.09", "9.91"], ["10.09", "10", "9.91"], ["9.91", "10.09", "10"]):
        at = AppTest.from_file(str(ui), default_timeout=60)
        at.run()
        at.get("file_uploader")[0].upload("a.md", rows(order).encode(), "text/markdown")
        at.get("file_uploader")[1].upload("b.md", rows(["10"]).encode(), "text/markdown")
        at.run()
        next(b for b in at.button if b.label == "Run audit").click().run()
        assert not at.exception and len(at.success) == 0 and len(at.error) >= 1
        exported = json.loads(at.session_state["audit"]["json"])
        assert exported["verdict"] == "CONFLICTS_FOUND" and exported["coverage_complete"] is False
