"""P1 engineering-correctness tests for app.core (deterministic + Hypothesis).

Conventions: documents are plain strings. "Editor line" means a line split only by
\\n, \\r\\n or \\r (what a text editor shows).
"""
import math
import re
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.core import audit_documents

PRESSURE_A = "P-101 | pressure | 10 bar"
PRESSURE_B = "P-101 | pressure | 900 kPa"


def audit(a, b, tol=0.01, name_a="a.md", name_b="b.md"):
    return audit_documents({name_a: a, name_b: b}, tol)


def assert_evidence(result):
    """Every finding and match must cite both sources, with a line and a verbatim excerpt."""
    for item in result["findings"] + result["matches"]:
        for side in ("reference", "other"):
            ev = item[side]
            assert ev["document"] and ev["line"] >= 1 and ev["excerpt"].strip()


# ---------- mandatory engineering cases ----------

def test_mandatory_pressure_mismatch_with_exact_evidence():
    a = "# Spec\n\n" + PRESSURE_A + "\n"
    b = "intro\nmore\nthird\n| TAG | property | value |\n| --- | --- | --- |\n| P-101 | pressure | 900 kPa |\n"
    r = audit(a, b, name_a="spec.md", name_b="bom.md")
    assert r["status"] == "DIFFERENCES"
    f = r["findings"][0]
    assert f["code"] == "VALUE_MISMATCH"
    assert f["reference"]["si_value"] == 1_000_000.0 and f["other"]["si_value"] == 900_000.0
    assert math.isclose(f["relative_difference"], 0.10, abs_tol=1e-12)
    assert (f["reference"]["document"], f["reference"]["line"], f["reference"]["excerpt"]) == ("spec.md", 3, PRESSURE_A)
    assert (f["other"]["document"], f["other"]["line"], f["other"]["excerpt"]) == ("bom.md", 6, "| P-101 | pressure | 900 kPa |")
    assert_evidence(r)


@pytest.mark.parametrize("a,b", [
    ("P-101 | flow | 12 L/min", "P-101 | flow | 0.0002 m^3/s"),
    ("V-201 | diameter | 25 mm", "V-201 | diameter | 0.025 m"),
    ("P-101 | pressure | 10 bar", "P-101 | pressure | 1000 kPa"),
])
def test_mandatory_equivalences_are_listed_not_flagged(a, b):
    r = audit(a, b, tol=1e-9)
    assert r["status"] == "NO_DIFFERENCES_FOUND"
    assert r["findings"] == [] and len(r["matches"]) == 1
    assert r["matches"][0]["code"] == "WITHIN_TOLERANCE"
    assert_evidence(r)


def test_temperature_scales_convert_deterministically():
    r = audit("T-1 | temperature | 20 degC", "T-1 | temperature | 293.15 K", tol=1e-9)
    assert r["status"] == "NO_DIFFERENCES_FOUND"


# ---------- tolerance boundary ----------

def test_tolerance_boundary_is_strictly_greater_than():
    # (5 - 4) / 5 == 0.2 exactly in binary floating point (same double as the literal 0.2).
    assert audit("X-1 | load | 5 Pa", "X-1 | load | 4 Pa", tol=0.2)["findings"] == []
    assert len(audit("X-1 | load | 5 Pa", "X-1 | load | 3.99 Pa", tol=0.2)["findings"]) == 1
    assert len(audit("X-1 | load | 5 Pa", "X-1 | load | 4.99 Pa", tol=0.0)["findings"]) == 1
    assert audit("X-1 | load | 5 Pa", "X-1 | load | 5 Pa", tol=0.0)["findings"] == []


@pytest.mark.parametrize("bad", [-0.001, 0.2000001, float("nan"), float("inf")])
def test_tolerance_validation(bad):
    with pytest.raises(ValueError):
        audit(PRESSURE_A, PRESSURE_B, tol=bad)


def test_zero_values():
    assert audit("Z-1 | p | 0 bar", "Z-1 | p | 0 kPa")["status"] == "NO_DIFFERENCES_FOUND"
    r = audit("Z-1 | p | 0 bar", "Z-1 | p | 1 bar")
    assert r["findings"][0]["relative_difference"] == 1.0


# ---------- incomplete / unsupported analysis must not look like success ----------

def test_incompatible_dimensions_warn_and_do_not_pass():
    r = audit("P-1 | pressure | 2 bar", "P-1 | pressure | 3 mm")
    assert r["status"] == "NO_COMPARABLE_ROWS" and r["comparisons"] == 0
    assert [w["code"] for w in r["warnings"]] == ["DIMENSION_MISMATCH"]


@pytest.mark.parametrize("unit", ["lollipops", "barg", "psig", "bara"])
def test_unknown_or_basis_specific_units_are_unsupported(unit):
    r = audit("P-1 | pressure | 5 " + unit, "P-1 | pressure | 5 bar")
    assert r["status"] == "NO_COMPARABLE_ROWS"
    w = r["warnings"][0]
    assert w["code"] == "UNSUPPORTED_UNIT" and w["document"] == "a.md" and w["line"] == 1


def test_no_comparable_records_at_all():
    r = audit("just prose\nno rows here", "")
    assert r["status"] == "NO_COMPARABLE_ROWS" and r["records"] == 0 and r["comparisons"] == 0
    assert r["coverage"]["a.md"]["lines"] == 2 and r["coverage"]["b.md"]["lines"] == 0


def test_records_present_in_only_one_document_are_reported():
    r = audit("P-1 | pressure | 5 bar\nV-9 | length | 3 m", "P-1 | pressure | 5 bar")
    assert r["status"] == "NO_DIFFERENCES_FOUND"
    assert [(u["tag"], u["document"], u["line"]) for u in r["unmatched"]] == [("V-9", "a.md", 2)]


def test_single_document_has_everything_unmatched():
    r = audit_documents({"only.md": PRESSURE_A})
    assert r["status"] == "NO_COMPARABLE_ROWS" and len(r["unmatched"]) == 1


# ---------- malformed input must be reported, never silently skipped ----------

@pytest.mark.parametrize("row", [
    "P-1 | pressure | 10,5 bar",        # decimal comma
    "P-1 | pressure | 1e3 Pa",          # scientific notation
    "P-1 | pressure | 10",              # no unit
    "P-1 | pressure | 10 bar | extra",  # extra column
    "P-1 | pressure | 10 kPa(g)",       # parenthesis in unit
    "P-1 | pressure | 1 000 kPa",       # grouped digits
])
def test_malformed_rows_are_reported_as_unparsed(row):
    r = audit("title\n" + row, PRESSURE_B)
    assert r["records"] == 1  # only the good row in b.md
    w = [x for x in r["warnings"] if x["code"] == "UNPARSED_ROW"]
    assert len(w) == 1 and w[0]["document"] == "a.md" and w[0]["line"] == 2 and w[0]["excerpt"] == row
    assert r["coverage"]["a.md"]["rows_unparsed"] == 1


def test_headers_separators_and_prose_are_not_flagged():
    text = "# Pump station\n\n| TAG | property | required value |\n| --- | --- | --- |\nNotes: see section 4 | 2026\nprose, with, commas 10\n"
    r = audit(text, text)
    assert r["warnings"] == []


# ---------- duplicates ----------

def test_duplicate_identifier_in_one_document_is_flagged_with_both_lines():
    r = audit("P-1 | pressure | 5 bar\nfiller\nP-1 | pressure | 5 bar", "P-1 | pressure | 5 bar")
    d = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]
    assert len(d) == 1 and d[0]["document"] == "a.md" and d[0]["lines"] == [1, 3]


def test_conflicting_duplicates_in_one_document_are_a_finding_with_both_citations():
    r = audit("P-1 | pressure | 5 bar\nP-1 | pressure | 6 bar", "")
    f = r["findings"][0]
    assert f["reference"]["document"] == f["other"]["document"] == "a.md"
    assert (f["reference"]["line"], f["other"]["line"]) == (1, 2)


# ---------- CSV ----------

def test_comma_separated_csv_rows_are_read_with_original_excerpt():
    a = "TAG,property,value,unit\nP-101,pressure,10,bar\n"
    b = "TAG,property,value\nP-101,pressure,900 kPa\n"
    r = audit(a, b, name_a="a.csv", name_b="b.csv")
    f = r["findings"][0]
    assert f["reference"]["excerpt"] == "P-101,pressure,10,bar" and f["reference"]["line"] == 2
    assert f["other"]["excerpt"] == "P-101,pressure,900 kPa" and f["other"]["line"] == 2
    assert r["warnings"] == []


def test_commas_are_only_rows_in_csv_files():
    assert audit("P-101,pressure,10 bar", "P-101,pressure,900 kPa", name_a="a.md", name_b="b.txt")["records"] == 0


# ---------- source-location accuracy ----------

@pytest.mark.parametrize("sep", ["\n", "\r\n", "\r"])
def test_line_numbers_follow_editor_lines(sep):
    text = sep.join(["h", "", PRESSURE_A, "tail"])
    assert audit(text, PRESSURE_B)["findings"][0]["reference"]["line"] == 3


def test_form_feed_and_unicode_separators_do_not_shift_line_numbers():
    # \x0c and \u2028 are not line breaks in text editors; the row is on editor line 2.
    text = "intro\x0c\u2028\x85still line one\n" + PRESSURE_A
    f = audit(text, PRESSURE_B)["findings"][0]
    assert f["reference"]["line"] == 2 and f["reference"]["excerpt"] == PRESSURE_A


# ---------- ordering / determinism ----------

def test_results_are_deterministic_and_sorted_by_tag_then_field():
    a = "V-201 | diameter | 25 mm\nP-101 | pressure | 10 bar\nP-101 | flow | 12 L/min"
    b = "P-101 | flow | 20 L/min\nV-201 | diameter | 30 mm\nP-101 | pressure | 900 kPa"
    first, second = audit(a, b), audit(a, b)
    assert first == second
    assert [(f["tag"], f["field"]) for f in first["findings"]] == [("p-101", "flow"), ("p-101", "pressure"), ("v-201", "diameter")]


def test_tag_and_field_matching_ignores_case():
    r = audit("p-101 | Pressure | 10 bar", "P-101 | pressure | 900 kPa")
    assert len(r["findings"]) == 1


# ---------- limits ----------

def test_document_count_limits_and_types():
    with pytest.raises(ValueError):
        audit_documents({})
    with pytest.raises(ValueError):
        audit_documents({"d%d" % i: PRESSURE_A for i in range(13)})
    with pytest.raises(ValueError):
        audit_documents({"a": 123})


def test_huge_exponent_units_do_not_crash():
    r = audit("L-1 | size | 1 km^999", "L-1 | size | 1 km^999")
    assert r["status"] in {"NO_COMPARABLE_ROWS", "NO_DIFFERENCES_FOUND"}


# ---------- Hypothesis invariants ----------

UNIT_FACTORS = {"Pa": 1, "kPa": 10**3, "MPa": 10**6, "bar": 10**5, "mbar": 10**2}


@settings(max_examples=150, deadline=None)
@given(v=st.integers(1, 10**6), u1=st.sampled_from(sorted(UNIT_FACTORS)), u2=st.sampled_from(sorted(UNIT_FACTORS)))
def test_property_equal_pressures_in_any_units_match(v, u1, u2):
    other = Decimal(v) * Decimal(UNIT_FACTORS[u1]) / Decimal(UNIT_FACTORS[u2])
    r = audit("P-1 | pressure | %d %s" % (v, u1), "P-1 | pressure | %s %s" % (format(other, "f"), u2), tol=1e-9)
    assert r["findings"] == [] and r["comparisons"] == 1 and len(r["matches"]) == 1


@settings(max_examples=150, deadline=None)
@given(a=st.integers(1, 10**6), b=st.integers(1, 10**6))
def test_property_relative_difference_is_symmetric(a, b):
    ra = audit("X-1 | load | %d bar" % a, "X-1 | load | %d kPa" % b, tol=0.0)
    rb = audit("X-1 | load | %d kPa" % b, "X-1 | load | %d bar" % a, tol=0.0)
    da = (ra["findings"] or ra["matches"])[0]["relative_difference"]
    db = (rb["findings"] or rb["matches"])[0]["relative_difference"]
    assert da == db and 0.0 <= da <= 1.0


@settings(max_examples=100, deadline=None)
@given(a=st.integers(1, 10**6), b=st.integers(1, 10**6), k=st.integers(1, 1000))
def test_property_relative_difference_ignores_common_scale(a, b, k):
    r1 = audit("X-1 | load | %d Pa" % a, "X-1 | load | %d Pa" % b, tol=0.0)
    r2 = audit("X-1 | load | %d Pa" % (a * k), "X-1 | load | %d Pa" % (b * k), tol=0.0)
    d1 = (r1["findings"] or r1["matches"])[0]["relative_difference"]
    d2 = (r2["findings"] or r2["matches"])[0]["relative_difference"]
    assert math.isclose(d1, d2, abs_tol=1e-9)


@settings(max_examples=100, deadline=None)
@given(a=st.integers(1, 10**4), b=st.integers(1, 10**4),
       t1=st.floats(0, 0.2, allow_nan=False), t2=st.floats(0, 0.2, allow_nan=False))
def test_property_larger_tolerance_never_adds_findings(a, b, t1, t2):
    lo, hi = sorted((t1, t2))
    n_lo = len(audit("X-1 | load | %d Pa" % a, "X-1 | load | %d Pa" % b, tol=lo)["findings"])
    n_hi = len(audit("X-1 | load | %d Pa" % a, "X-1 | load | %d Pa" % b, tol=hi)["findings"])
    assert n_hi <= n_lo


_FILLER = st.text(alphabet=st.characters(blacklist_characters="|,\n\r", blacklist_categories=("Cs",)), max_size=25)


@settings(max_examples=200, deadline=None)
@given(pre_a=st.lists(_FILLER, max_size=6), post_a=st.lists(_FILLER, max_size=4),
       pre_b=st.lists(_FILLER, max_size=6), sep=st.sampled_from(["\n", "\r\n", "\r"]))
def test_property_reported_location_matches_editor_line(pre_a, post_a, pre_b, sep):
    doc_a = sep.join(pre_a + [PRESSURE_A] + post_a)
    doc_b = sep.join(pre_b + ["| P-101 | pressure | 900 kPa |"])
    r = audit(doc_a, doc_b)
    f = r["findings"][0]
    assert f["reference"]["line"] == len(pre_a) + 1 and f["reference"]["excerpt"] == PRESSURE_A
    assert f["other"]["line"] == len(pre_b) + 1 and f["other"]["excerpt"] == "| P-101 | pressure | 900 kPa |"


_UNIT = st.from_regex(r"[A-Za-z][A-Za-z0-9/^*.%-]{0,12}", fullmatch=True)


@settings(max_examples=200, deadline=None)
@given(u1=_UNIT, u2=_UNIT, v1=st.integers(0, 10**9), v2=st.integers(0, 10**9))
def test_property_arbitrary_units_never_crash(u1, u2, v1, v2):
    r = audit("A-1 | x | %d %s" % (v1, u1), "A-1 | x | %d %s" % (v2, u2))
    assert r["status"] in {"DIFFERENCES", "NO_DIFFERENCES_FOUND", "NO_COMPARABLE_ROWS"}
    assert_evidence(r) if "matches" in r else None


@settings(max_examples=150, deadline=None)
@given(text=st.text(max_size=300))
def test_property_arbitrary_text_never_crashes(text):
    r = audit(text, text)
    assert r["status"] in {"DIFFERENCES", "NO_DIFFERENCES_FOUND", "NO_COMPARABLE_ROWS"}
