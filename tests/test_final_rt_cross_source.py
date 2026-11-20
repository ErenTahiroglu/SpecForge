"""FINAL-RT-001 (#51): repeated rows in two sources. The first row of a source must not stand for the whole source.

Rule under test: two sources are clean only when EVERY value of one source is within the effective tolerance of EVERY value of
the other (and every pair is dimension- and temperature-compatible). Each source is already internally consistent when this
check runs, so the decision needs only four extreme pairs, not all pairs:
  - same-sign groups: 1 - (smaller / larger magnitude) grows with the distance of the magnitudes, so for a fixed value of one
    source the worst partner is the minimum or the maximum of the other, and the worst pair overall is one of
    (min A, min B), (min A, max B), (max A, min B), (max A, max B);
  - opposite signs, or zero against a nonzero value: every such pair differs by 1 or more, so the extreme pair of the two
    groups already contains one when it exists (allowed difference is at most 0.2).
The same documents must give the same verdict in every row order and in either upload order.
Multi-source reference-star behaviour (issue #19) is NOT changed and is pinned at the end of this file.
"""
import itertools
import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app.core import ZERO_TOLERANCE_FLOOR, audit_documents, relative_difference
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown

TOL = 0.01
UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"


def rows(values, unit="m", tag="MR-27", field="length"):
    return "\n".join(f"{tag} | {field} | {v} {unit}" for v in values) + "\n"


def two(a_values, b_values, tol=TOL, unit="m", swap=False):
    a, b = rows(a_values, unit), rows(b_values, unit)
    docs = {"b.md": b, "a.md": a} if swap else {"a.md": a, "b.md": b}
    return audit_documents(docs, tol)


def assert_not_clean(r):
    assert r["coverage_complete"] is False or r["verdict"] == "CONFLICTS_FOUND"
    assert r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"


def pair_excerpts(finding):
    return {finding["reference"]["excerpt"], finding["other"]["excerpt"]}


# ---- the reported fixture, every row order and both upload orders ---------------------------------------------------
@pytest.mark.parametrize("a_values", [["100", "101"], ["101", "100"]])
@pytest.mark.parametrize("swap", [False, True])
def test_reported_fixture_is_a_conflict_in_every_order(a_values, swap):
    r = two(a_values, ["99.5"], swap=swap)
    assert r["verdict"] == "CONFLICTS_FOUND" and r["comparisons"] == 1
    mismatch = [f for f in r["findings"] if f["code"] == "VALUE_MISMATCH"]
    assert len(mismatch) == 1
    assert pair_excerpts(mismatch[0]) == {"MR-27 | length | 101 m", "MR-27 | length | 99.5 m"}
    assert mismatch[0]["relative_difference"] == pytest.approx(1.5 / 101, abs=1e-9)
    assert not r["matches"]


def test_decisive_rows_keep_document_line_and_excerpt_on_both_sides():
    r = two(["100", "101"], ["99.5"])
    f = [f for f in r["findings"] if f["code"] == "VALUE_MISMATCH"][0]
    by_doc = {f["reference"]["document"]: f["reference"], f["other"]["document"]: f["other"]}
    assert by_doc["a.md"]["line"] == 2 and by_doc["a.md"]["value"] == 101.0
    assert by_doc["b.md"]["line"] == 1 and by_doc["b.md"]["value"] == 99.5


# ---- repeats on both sides ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a_order", list(itertools.permutations(["100", "100.8"])))
@pytest.mark.parametrize("b_order", list(itertools.permutations(["99.3", "99.9"])))
@pytest.mark.parametrize("swap", [False, True])
def test_two_repeat_groups_that_disagree_are_never_clean(a_order, b_order, swap):
    r = two(list(a_order), list(b_order), swap=swap)
    assert r["verdict"] == "CONFLICTS_FOUND"
    f = [f for f in r["findings"] if f["code"] == "VALUE_MISMATCH"][0]
    assert pair_excerpts(f) == {"MR-27 | length | 100.8 m", "MR-27 | length | 99.3 m"}


@pytest.mark.parametrize("a_order", list(itertools.permutations(["100", "100.4"])))
@pytest.mark.parametrize("b_order", list(itertools.permutations(["100.2", "99.9"])))
def test_two_repeat_groups_that_agree_stay_clean_and_complete(a_order, b_order):
    r = two(list(a_order), list(b_order))
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and r["comparisons"] == 1
    assert len(r["matches"]) == 1 and not r["findings"]
    assert pair_excerpts(r["matches"][0]) == {"MR-27 | length | 100.4 m", "MR-27 | length | 99.9 m"}   # the worst pair is shown


def test_exact_duplicates_are_still_a_clean_note():
    r = two(["10", "10"], ["10"])
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    assert r["matches"][0]["relative_difference"] == 0.0
    assert two(["10", "10"], ["11"])["verdict"] == "CONFLICTS_FOUND"


# ---- units, signs, zeros, threshold ---------------------------------------------------------------------------------
def test_converted_units_across_repeats():
    a = "MR-27 | pressure | 100 kPa\nMR-27 | pressure | 100.5 kPa\n"
    for b, verdict in (("MR-27 | pressure | 0.996 bar\n", "NO_CONFLICTS_IN_COMPARED_ROWS"),     # 99.6 kPa: worst pair 0.90 %
                       ("MR-27 | pressure | 0.994 bar\n", "CONFLICTS_FOUND"),                  # 99.4 kPa: 100.5 vs 99.4 is 1.09 %
                       ("MR-27 | pressure | 0.99 bar\n", "CONFLICTS_FOUND")):
        for docs in ({"a.md": a, "b.md": b}, {"a.md": b, "b.md": a}):
            assert audit_documents(docs, TOL)["verdict"] == verdict, (b, list(docs))


@pytest.mark.parametrize("a,b,verdict", [
    (["0", "0"], ["0"], "NO_CONFLICTS_IN_COMPARED_ROWS"), (["0", "0"], ["1"], "CONFLICTS_FOUND"), (["0"], ["0", "0.001"], "CONFLICTS_FOUND"),
    (["-100", "-100.5"], ["-99.4"], "CONFLICTS_FOUND"), (["-100", "-100.5"], ["-100.2"], "NO_CONFLICTS_IN_COMPARED_ROWS"),
    (["100", "100.4"], ["-100"], "CONFLICTS_FOUND"), (["-1", "-1"], ["1"], "CONFLICTS_FOUND"),
])
@pytest.mark.parametrize("swap", [False, True])
def test_sign_and_zero_groups(a, b, verdict, swap):
    assert two(a, b, swap=swap)["verdict"] == verdict


@pytest.mark.parametrize("a,b,verdict", [
    (["100", "100.9"], ["99.9"], "NO_CONFLICTS_IN_COMPARED_ROWS"),     # worst pair 0.991 %
    (["100", "100.9"], ["99.8"], "CONFLICTS_FOUND"),                   # worst pair 1.09 %
])
def test_values_close_to_the_threshold(a, b, verdict):
    for swap in (False, True):
        for a_order in (a, a[::-1]):
            assert two(a_order, b, swap=swap)["verdict"] == verdict


def test_extreme_magnitudes_do_not_overflow():
    big = "1" + "0" * 300
    r = two([big, big], ["-" + big])
    assert r["verdict"] == "CONFLICTS_FOUND" and r["findings"][0]["relative_difference"] == 2.0
    json.dumps(r, allow_nan=False)


def test_pages_of_one_pdf_are_one_source():
    docs = {"a.pdf:page-1": rows(["100"]), "a.pdf:page-2": rows(["101"]), "b.md": rows(["99.5"])}
    r = audit_documents(docs, TOL)
    f = [f for f in r["findings"] if f["code"] == "VALUE_MISMATCH"][0]
    assert r["verdict"] == "CONFLICTS_FOUND"
    assert {f["reference"]["document"], f["other"]["document"]} == {"a.pdf:page-2", "b.md"}


def test_dimension_and_temperature_semantics_are_not_lost_across_repeats():
    r = audit_documents({"a.md": "MR-27 | length | 100 m\nMR-27 | length | 100.2 m\n", "b.md": "MR-27 | length | 100 kg\n"}, TOL)
    assert any(w["code"] == "DIMENSION_MISMATCH" for w in r["warnings"]) and r["coverage_complete"] is False
    t = audit_documents({"a.md": "T-1 | temperature | 0 degC\nT-1 | temperature | 273.15 K\n", "b.md": "T-1 | temperature | 5 delta_degC\n"}, TOL)
    assert any(w["code"] == "TEMPERATURE_SEMANTICS" for w in t["warnings"]) and t["coverage_complete"] is False


# ---- brute-force equivalence ----------------------------------------------------------------------------------------
@settings(max_examples=400, deadline=None)
@given(st.lists(st.integers(-30000, 30000), min_size=1, max_size=5), st.lists(st.integers(-30000, 30000), min_size=1, max_size=5),
       st.sampled_from([0.0, 0.001, 0.01, 0.05, 0.2]), st.randoms(use_true_random=False))
def test_property_two_sources_are_clean_exactly_when_every_pair_is_within_tolerance(raw_a, raw_b, tol, rnd):
    values_a, values_b = [f"{v / 1000:.3f}" for v in raw_a], [f"{v / 1000:.3f}" for v in raw_b]
    allowed = max(tol, ZERO_TOLERANCE_FLOOR)
    na, nb = [float(v) for v in values_a], [float(v) for v in values_b]
    # a clean result needs every pair inside A, inside B and across A and B to be within the tolerance
    every_pair = all(relative_difference(x, y) <= allowed for x, y in itertools.combinations(na + nb, 2))
    r = two(values_a, values_b, tol)
    clean = r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    assert clean == every_pair, (values_a, values_b, tol)
    shuffled_a, shuffled_b = values_a[:], values_b[:]
    rnd.shuffle(shuffled_a)
    rnd.shuffle(shuffled_b)
    r2 = two(shuffled_a, shuffled_b, tol, swap=rnd.choice([False, True]))
    assert (r2["verdict"], r2["coverage_complete"]) == (r["verdict"], r["coverage_complete"])


# ---- every surface agrees --------------------------------------------------------------------------------------------
FIXTURE_A, FIXTURE_B = rows(["100", "101"]), rows(["99.5"])


def test_mcp_and_api_agree_with_the_engine():
    m = compare_engineering_rows(FIXTURE_A, FIXTURE_B)
    assert (m["verdict"], m["coverage_complete"]) == ("CONFLICTS_FOUND", True) and m["comparisons"] == 1
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": FIXTURE_A, "b.md": FIXTURE_B}}).json()
    assert (body["verdict"], body["coverage_complete"], body["comparisons"]) == ("CONFLICTS_FOUND", True, 1)


def test_markdown_and_view_show_both_decisive_rows():
    r = audit_documents({"a.md": FIXTURE_A, "b.md": FIXTURE_B}, TOL)
    view = build_view(r, {"a.md": "Document A", "b.md": "Document B"})
    assert view.verdict == "CONFLICTS_FOUND" and not view.equivalents
    md = render_markdown(view, "2026-11-19 00:00 UTC")
    assert "101 m" in md and "99.5 m" in md and "Coverage complete: yes" in md


@pytest.mark.parametrize("a_values,swap", [(["100", "101"], False), (["101", "100"], False), (["100", "101"], True)])
def test_streamlit_shows_no_green_result_and_exports_agree(a_values, swap):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    first, second = (rows(["99.5"]), rows(a_values)) if swap else (rows(a_values), rows(["99.5"]))
    at.get("file_uploader")[0].upload("a.md", first.encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", second.encode(), "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception and len(at.success) == 0 and len(at.error) >= 1
    exported = json.loads(at.session_state["audit"]["json"])
    assert exported["verdict"] == "CONFLICTS_FOUND" and exported["result"]["matches"] == []


# ---- issue #19 is not changed by this fix ---------------------------------------------------------------------------
def test_three_source_reference_star_behaviour_is_unchanged():
    docs = {"a.md": rows(["10"], "bar", field="pressure"), "b.md": rows(["10.09"], "bar", field="pressure"),
            "c.md": rows(["9.91"], "bar", field="pressure")}
    clean = audit_documents(docs, TOL)
    assert clean["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and clean["comparisons"] == 2     # known limit #19, deliberately kept
    reordered = audit_documents({k: docs[k] for k in ("b.md", "a.md", "c.md")}, TOL)
    assert reordered["verdict"] == "CONFLICTS_FOUND"
