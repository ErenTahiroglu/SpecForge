"""P1.2 regression tests: two engineering-unit defects found in the P1.1 acceptance review.

H01  `bbl` / `barrel` is ambiguous (US fluid barrel 119.24 L vs oil barrel 158.99 L). Pint silently picks the
     US fluid barrel, so `1 bbl` was shown as "verified equivalent" to 119.24 L. It must be refused, not guessed.
H02  Dimensions were compared as strings. Pint prints one dimension with its terms in different orders, so
     `lbf` vs `N`, `kgf` vs `N`, `metric_horsepower` vs `W` and `ft*lbf` vs `J` gave a false DIMENSION_MISMATCH.

Written BEFORE the fixes. The comparison formula (difference divided by the LARGER absolute value) is pinned, not changed.
"""
import pytest

from app import report
from app.core import UNITS, audit_documents

US_FLUID_BARREL_L = 119.24047119599997     # Pint: bbl = barrel = 31.5 US gallons
OIL_BARREL_L = 158.98729492799993          # Pint: oil_barrel = 42 US gallons


def A(a, b, tol=0.01, field="volume"):
    return audit_documents({"a.md": f"| M-1 | {field} | {a} |", "b.md": f"| M-1 | {field} | {b} |"}, tol)


def codes(result):
    return [w["code"] for w in result["warnings"]]


# =============================== H01 ambiguous barrel ===============================

def test_h01_pint_facts_behind_the_policy():
    """Pins the numbers the policy rests on. If a Pint upgrade changes them, this test says so."""
    assert UNITS.Quantity(1, "bbl").to("liter").magnitude == pytest.approx(119.24, abs=0.01)
    assert UNITS.Quantity(1, "barrel").to("liter").magnitude == pytest.approx(119.24, abs=0.01)
    assert UNITS.Quantity(1, "oil_barrel").to("liter").magnitude == pytest.approx(158.99, abs=0.01)
    assert UNITS.Quantity(1, "bbl").to("liter") != UNITS.Quantity(1, "oil_barrel").to("liter")


AMBIGUOUS_BARRELS = ["bbl", "barrel", "bbls", "barrels", "BBL", "Bbl", "kbbl", "Mbbl", "mbbl", "kilobarrel", "millibarrel"]


@pytest.mark.parametrize("unit", AMBIGUOUS_BARRELS)
@pytest.mark.parametrize("other", ["119.24 L", "158.987 L"])
def test_h01_unqualified_barrel_is_never_compared_in_either_interpretation(unit, other):
    r = A(f"1 {unit}", other)
    w = [x for x in r["warnings"] if x["code"] == "AMBIGUOUS_UNIT"]
    assert len(w) == 1 and w[0]["document"] == "a.md" and w[0]["line"] == 1
    assert w[0]["excerpt"] == f"| M-1 | volume | 1 {unit} |"            # source evidence preserved verbatim
    assert w[0]["unit"] == unit and "oil_barrel" in w[0]["alternatives"]
    assert r["comparisons"] == 0 and r["findings"] == [] and r["matches"] == []
    assert r["status"] == "NO_COMPARABLE_ROWS"
    assert r["coverage"]["a.md"]["rows_ambiguous_unit"] == 1 and r["coverage"]["a.md"]["rows_parsed"] == 0
    assert r["coverage"]["b.md"]["rows_parsed"] == 1


@pytest.mark.parametrize("unit", ["bbl/day", "bbl/d", "barrel/h", "bbl/min", "kbbl/day", "bbl*bar"])
def test_h01_ambiguous_barrel_inside_a_compound_unit_is_detected(unit):
    r = A(f"1 {unit}", "1 m^3/s" if "bar" not in unit.split("*")[-1] else "1 J")
    assert codes(r) == ["AMBIGUOUS_UNIT"] and r["comparisons"] == 0 and r["matches"] == [] and r["findings"] == []


def test_h01_ambiguous_barrel_on_the_second_side_is_also_refused():
    r = A("119.24 L", "1 bbl")
    w = r["warnings"][0]
    assert w["code"] == "AMBIGUOUS_UNIT" and w["document"] == "b.md" and r["matches"] == [] and r["findings"] == []


@pytest.mark.parametrize("a,b", [
    ("1 oil_barrel", "158.98729 L"), ("1 oil_bbl", "158.98729 L"), ("1 imperial_barrel", "163.65924 L"),
    ("1 UK_bbl", "163.65924 L"), ("1 beer_barrel", "117.34777 L"), ("1 dry_barrel", "115.62712 L"),
    ("1 oil_barrel/day", "158.98729 L/day"),
])
def test_h01_explicitly_named_barrels_still_convert(a, b):
    r = A(a, b, 1e-6)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and not r["warnings"], r["warnings"]


def test_h01_explicit_oil_barrel_against_the_us_fluid_value_is_a_real_conflict():
    r = A("1 oil_barrel", "119.24047 L")
    assert [f["code"] for f in r["findings"]] == ["VALUE_MISMATCH"]


def test_h01_the_refusal_does_not_hide_other_rows():
    docs = {"a.md": "| M-1 | volume | 1 bbl |\n| P-1 | pressure | 10 bar |",
            "b.md": "| M-1 | volume | 119.24 L |\n| P-1 | pressure | 900 kPa |"}
    r = audit_documents(docs, 0.01)
    assert codes(r) == ["AMBIGUOUS_UNIT"]
    assert [f["tag"].upper() for f in r["findings"]] == ["P-1"] and r["comparisons"] == 1 and r["matches"] == []


def test_h01_barrel_row_is_not_counted_as_verified_equivalent_in_the_report():
    r = A("1 bbl", "119.24 L")
    v = report.build_view(r, {"a.md": "Document A", "b.md": "Document B"}, [], [])
    assert v.verdict == report.VERDICT_NONE
    assert v.metrics["equivalent"] == 0 and v.metrics["compared"] == 0 and v.metrics["not_compared"] >= 1
    item = [i for i in v.coverage if i.code == "AMBIGUOUS_UNIT"][0]
    assert "oil_barrel" in item.summary and "bbl" in item.summary and item.excerpt == "| M-1 | volume | 1 bbl |"
    md = report.render_markdown(v)
    section = md.split("## Verified equivalent after unit conversion")[1].split("\n## ")[0]
    assert "oil_barrel" in md.replace("\\", "") and "None." in section and "119.24 L" not in section


# ---- relative-difference formula: pinned, not changed ----

def test_formula_between_the_two_barrels_is_25_percent_with_the_larger_value_as_denominator():
    r = A("1 oil_barrel", "119.24047 L", 0.01)        # 158.987 L vs 119.240 L, explicit unit on the oil side
    assert [f["code"] for f in r["findings"]] == ["VALUE_MISMATCH"]
    rel = r["findings"][0]["relative_difference"]
    assert rel == pytest.approx((158.98729 - 119.24047) / 158.98729, abs=1e-6)
    assert rel == pytest.approx(0.25, abs=1e-3)
    assert rel != pytest.approx(1 / 3, abs=0.01)                    # the earlier "33%" is the SMALLER-value denominator


def test_formula_rounded_values_119_24_and_158_99():
    r = A("119.24 L", "158.99 L")
    assert r["findings"][0]["relative_difference"] == pytest.approx(0.25, abs=1e-3)


def test_original_10_bar_vs_900_kpa_example_is_unchanged():
    r = audit_documents({"a.md": "| P-101 | pressure | 10 bar |", "b.md": "| P-101 | pressure | 900 kPa |"}, 0.01)
    f = r["findings"][0]
    assert r["status"] == "DIFFERENCES" and f["code"] == "VALUE_MISMATCH" and f["scope"] == "cross_source"
    assert f["relative_difference"] == pytest.approx(0.1) and f["reference"]["si_value"] == 1_000_000
    assert f["other"]["si_value"] == 900_000 and f["reference"]["line"] == 1 and f["other"]["line"] == 1


# =============================== H02 structural dimensionality ===============================

COMPATIBLE = [
    ("1 lbf", "4.4482216 N"), ("1 kgf", "9.80665 N"), ("1 metric_horsepower", "735.49875 W"),
    ("1 ft*lbf", "1.3558179 J"), ("1 foot_pound", "1.3558179 J"), ("1 lbf*ft", "1.3558179 N*m"),
    ("1 kgf*m", "9.80665 N*m"), ("1 hydraulic_horsepower", "745.69987 W"),
    ("1 kgf/cm^2", "98.0665 kPa"), ("1 lbf/in^2", "6.8947573 kPa"), ("1 psi", "6.8947573 kPa"),
]


@pytest.mark.parametrize("a,b", COMPATIBLE)
def test_h02_compatible_units_are_compared_and_equivalent(a, b):
    r = A(a, b, 1e-4)
    assert "DIMENSION_MISMATCH" not in codes(r), r["warnings"]
    assert r["comparisons"] == 1 and r["status"] == "NO_DIFFERENCES_FOUND" and len(r["matches"]) == 1


@pytest.mark.parametrize("a,b", COMPATIBLE)
def test_h02_compatible_units_in_the_other_order(a, b):
    r = A(b, a, 1e-4)
    assert "DIMENSION_MISMATCH" not in codes(r) and r["comparisons"] == 1 and r["status"] == "NO_DIFFERENCES_FOUND"


@pytest.mark.parametrize("a,b,rel", [("1 lbf", "5 N", (5 - 4.4482216) / 5), ("1 kgf", "5 N", (9.80665 - 5) / 9.80665),
                                     ("1 metric_horsepower", "500 W", (735.49875 - 500) / 735.49875),
                                     ("1 ft*lbf", "1 J", (1.3558179 - 1) / 1.3558179)])
def test_h02_a_real_difference_between_compatible_units_is_a_value_conflict(a, b, rel):
    r = A(a, b)
    assert codes(r) == [] and [f["code"] for f in r["findings"]] == ["VALUE_MISMATCH"]
    assert r["findings"][0]["relative_difference"] == pytest.approx(rel, abs=1e-6)


@pytest.mark.parametrize("a,b", [
    ("1 lbf", "1 J"), ("1 kgf", "1 kg"), ("1 N", "1 m"), ("1 W", "1 J"), ("1 metric_horsepower", "1 N"),
    ("1 ft*lbf", "1 W"), ("1 psi", "1 lbf"), ("1 kgf/cm^2", "1 kgf"), ("1 m", "1 s"), ("1 bar", "1 K"),
])
def test_h02_genuinely_incompatible_units_remain_a_dimension_mismatch(a, b):
    r = A(a, b)
    assert codes(r) == ["DIMENSION_MISMATCH"] and r["comparisons"] == 0 and r["matches"] == [] and r["findings"] == []


def test_h02_dimensionless_values_still_compare():
    r = A("50 percent", "0.5 dimensionless", 1e-6)
    assert r["comparisons"] == 1 and "DIMENSION_MISMATCH" not in codes(r)


def test_h02_temperature_semantics_are_unchanged():
    ok = A("20 degC", "293.15 K", 1e-6, "temperature")
    assert ok["status"] == "NO_DIFFERENCES_FOUND" and not ok["warnings"]
    bad = A("20 degC", "20 K", 1e-6, "temperature")
    assert [f["code"] for f in bad["findings"]] == ["VALUE_MISMATCH"]
    mixed = A("5 delta_degC", "278.15 K", 1e-6, "temperature")
    assert codes(mixed) == ["TEMPERATURE_SEMANTICS"] and mixed["comparisons"] == 0
    assert codes(A("20 degC", "1 m", 1e-6, "temperature")) == ["DIMENSION_MISMATCH"]


def test_h02_ambiguous_unit_restrictions_are_unchanged():
    assert codes(A("1 hp", "745.7 W")) == ["AMBIGUOUS_UNIT"]
    assert codes(A("1 horsepower", "745.7 W")) == ["AMBIGUOUS_UNIT"]


def test_h02_duplicate_rows_in_one_file_with_compatible_but_reordered_dimensions_are_not_a_mismatch():
    docs = {"a.md": "| F-1 | force | 1 lbf |\n| F-1 | force | 4.4482216 N |", "b.md": "| F-1 | force | 4.4482216 N |"}
    r = audit_documents(docs, 1e-4)
    assert "DIMENSION_MISMATCH" not in codes(r) and "AMBIGUOUS_COMPARISON" not in codes(r)
    assert r["comparisons"] == 1 and r["findings"] == []


@pytest.mark.parametrize("a,b", [(x, y) for x in ["N", "lbf", "kgf", "W", "metric_horsepower", "J", "ft*lbf", "N*m", "Pa", "psi",
                                                 "kgf/cm^2", "m", "m/s", "kg", "degC", "K", "percent", "L/min", "m^3/s", "bar"]
                                 for y in ["N", "lbf", "W", "J", "N*m", "Pa", "m", "K", "percent", "m^3/s"]])
def test_h02_engine_agrees_with_pint_on_compatibility_for_every_pair(a, b):
    """Cross-check against Pint's own `is_compatible_with`; temperature pairs are excluded from the semantic gate."""
    pint_compatible = UNITS.Quantity(1, a).is_compatible_with(UNITS.Quantity(1, b))
    r = A(f"1 {a}", f"1 {b}", 0.2, "temperature" if "K" in (a, b) or "degC" in (a, b) else "value")
    mismatch = "DIMENSION_MISMATCH" in codes(r)
    assert mismatch == (not pint_compatible), (a, b, r["warnings"])


def test_h02_pressure_basis_note_survives_unit_spelling():
    for a in ["1 psi", "1 lbf/in^2", "1 kgf/cm^2", "1 bar"]:
        r = A(a, "50 kPa", 0.01, "pressure")
        v = report.build_view(r, {"a.md": "Document A", "b.md": "Document B"}, [], [])
        assert v.conflicts and "Pressure basis" in v.conflicts[0].explanation, a
