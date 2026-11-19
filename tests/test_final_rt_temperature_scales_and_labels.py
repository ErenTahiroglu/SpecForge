"""FINAL-RT-002 (#52) and FINAL-RT-003 (#53): temperature scales and temperature labels.

RT-002 policy under test (README, "Temperature semantics"): a pure-temperature unit is read only when it is on a finite list:
kelvin (with SI prefixes), degree_Rankine (linear), degree_Celsius and degree_Fahrenheit (offset scales, absolute and as
delta_ units). Any other unit of the temperature dimension (degree_Reaumur and its delta, Planck or atomic temperature units)
is refused BEFORE conversion to base units: UNSUPPORTED_UNIT, reason UnsupportedTemperatureScale, coverage incomplete.

RT-003 policy under test: the kind of a temperature is taken from a finite label grammar, not from one word anywhere in the
name. A label is ABSOLUTE only if every word is `temperature`/`temp` or a listed qualifier (design, operating, ambient ...).
A label is an INTERVAL only if every word is `temperature`/`temp`/`t`/`delta`, a listed qualifier or a difference word, and
at least one is a difference word (difference, differential, rise, drop, delta, interval). Everything else is UNKNOWN, and an
unknown kind is not compared when an offset unit (degC, degF) is involved. A delta_ unit decides by itself.
"""
import itertools
import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"
BOTH_ORDERS = (False, True)


def T(a, b, field="temperature", tol=1e-6, swap=False):
    docs = {"a.md": f"MR-27 | {field} | {a}\n", "b.md": f"MR-27 | {field} | {b}\n"}
    if swap:
        docs = {"a.md": docs["b.md"], "b.md": docs["a.md"]}
    return audit_documents(docs, tol)


def refused_scale(r):
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False and r["comparisons"] == 0
    assert not r["matches"] and not r["findings"]
    hits = [w for w in r["warnings"] if w["code"] == "UNSUPPORTED_UNIT" and w.get("reason") == "UnsupportedTemperatureScale"]
    assert hits and all(h["line"] == 1 and h["excerpt"].startswith("MR-27 |") and h["detail"] == "TEMPERATURE_SCALE_NOT_SUPPORTED" for h in hits)
    json.dumps(r, allow_nan=False)
    return hits


def refused_kind(r):
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False and r["comparisons"] == 0
    assert not r["matches"] and not r["findings"]
    hits = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS"]
    assert len(hits) == 1 and hits[0]["detail"] in ("unknown_kind", "uncertain")
    json.dumps(r, allow_nan=False)
    return hits[0]


def clean(r):
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True and len(r["matches"]) == 1, r["warnings"]


# =================================================== RT-002: scales ====================================================
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_reaumur_zero_rise_is_not_a_match_for_273_15_kelvin(swap):
    refused_scale(T("0 degRe", "273.15 K", "temperature rise", swap=swap))


@pytest.mark.parametrize("a,b", [("0 degRe", "0 K"), ("5 degRe", "6.25 K"), ("5 delta_degRe", "6.25 K"), ("0 degRe", "273.15 K")])
@pytest.mark.parametrize("field", ["temperature rise", "temperature", "reading"])
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_reaumur_is_refused_for_rise_absolute_and_unlabelled(a, b, field, swap):
    refused_scale(T(a, b, field, swap=swap))


@pytest.mark.parametrize("unit", ["degRe", "reaumur", "degree_Reaumur", "\u00b0Re", "delta_degRe", "planck_temperature",
                                  "atomic_unit_of_temperature", "mdegRe"])
def test_every_unlisted_temperature_unit_is_refused_before_conversion(unit):
    r = T(f"1 {unit}", "1 K", "temperature rise")
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert any(w["code"] == "UNSUPPORTED_UNIT" for w in r["warnings"])
    assert r["records"] == 1                      # only the K row was read


def test_a_refused_scale_never_reaches_base_unit_conversion(monkeypatch):
    seen = []
    real = core.UNITS.Quantity.to_base_units

    def spy(self, *args, **kwargs):
        seen.append(str(self.units))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(core.UNITS.Quantity, "to_base_units", spy)
    r = T("1 degRe", "1 K", "temperature rise")
    assert r["coverage_complete"] is False
    assert "kelvin" in seen                                     # the supported row was converted (the spy works)
    assert not any("Reaumur" in name for name in seen)


def test_the_policy_list_is_finite_and_documented_in_code():
    assert set(core.SUPPORTED_TEMPERATURE_SCALES) == {"kelvin", "degree_Rankine", "degree_Celsius", "degree_Fahrenheit",
                                                      "delta_degree_Celsius", "delta_degree_Fahrenheit"}


ALIASES = {
    "kelvin": ["K", "kelvin", "degK", "mK", "kK"], "degree_Rankine": ["degR", "rankine", "degree_Rankine"],
    "degree_Celsius": ["degC", "celsius", "degree_Celsius", "\u00b0C"], "degree_Fahrenheit": ["degF", "fahrenheit", "\u00b0F"],
    "delta_degree_Celsius": ["delta_degC", "delta_degree_Celsius"], "delta_degree_Fahrenheit": ["delta_degF"],
}


@pytest.mark.parametrize("canonical,spellings", sorted(ALIASES.items()))
def test_every_supported_spelling_classifies_to_the_finite_list(canonical, spellings):
    for spelling in spellings:
        r = audit_documents({"a.md": f"X-1 | temperature | 1 {spelling}\n", "b.md": "X-1 | temperature | 1 K\n"}, 0.2)
        assert r["records"] == 2 and not any(w.get("reason") == "UnsupportedTemperatureScale" for w in r["warnings"]), spelling


# ---- positive controls: absolute points, intervals, linear scales ---------------------------------------------------
@pytest.mark.parametrize("a,b,field", [
    ("0 degC", "273.15 K", "temperature"), ("0 celsius", "273.15 kelvin", "temperature"), ("32 fahrenheit", "491.67 rankine", "temperature"),
    ("32 degF", "0 degC", "temperature"), ("491.67 degR", "0 degC", "temperature"), ("273150 mK", "0 \u00b0C", "temperature"),
    ("100 degC", "212 degF", "design temperature"), ("-40 degC", "-40 degF", "operating temperature"),
    ("5 delta_degC", "5 K", "temperature difference"), ("9 delta_degF", "5 K", "temperature difference"),
    ("5 degC", "9 degF", "temperature rise"), ("9 degR", "5 K", "temperature rise"), ("0 degC", "0 K", "temperature rise"),
    ("5 delta_degC", "9 delta_degF", "reading"),
])
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_supported_scales_still_match_in_both_orders(a, b, field, swap):
    clean(T(a, b, field, swap=swap))


@pytest.mark.parametrize("a,b,field", [("0 degC", "0 K", "temperature"), ("0 degC", "273.15 K", "temperature rise"), ("5 degC", "278.15 K", "temperature rise")])
def test_supported_scales_still_conflict_when_the_meaning_differs(a, b, field):
    assert T(a, b, field)["verdict"] == "CONFLICTS_FOUND"


def test_unknown_unit_text_is_a_visible_gap():
    r = T("1 degZ", "1 K", "temperature rise")
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert [w["code"] for w in r["warnings"]] == ["UNSUPPORTED_UNIT"]


# ==================================================== RT-003: labels ===================================================
UNCERTAIN_LABELS = [
    "temperature uncertainty", "uncertainty of temperature", "temperature measurement uncertainty", "Temperature Uncertainty",
    "TEMPERATURE UNCERTAINTY", "temperature_uncertainty", "temperatureUncertainty", "uncertainty", "temperature deviation",
    "temperature error", "temperature tolerance", "temperature margin", "temperature accuracy", "superheat temperature",
    "max temperature uncertainty", "temperature drift", "temperature spread", "DT", "dT", "diff", "dTemp", "reading", "value",
    "temperature of rise", "temperature rise uncertainty",
]


@pytest.mark.parametrize("label", UNCERTAIN_LABELS)
@pytest.mark.parametrize("pair", [("1 degC", "274.15 K"), ("1 degC", "1 K"), ("1 degF", "1 degC")])
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_compound_or_unclear_labels_never_give_an_absolute_match(label, pair, swap):
    refused_kind(T(*pair, label, swap=swap))


def test_the_reported_uncertainty_fixture_is_not_clean():
    r = T("1 degC", "274.15 K", "temperature uncertainty", tol=0.01)
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False


ABSOLUTE_LABELS = ["temperature", "temp", "Temperature", "design temperature", "operating temperature", "ambient temperature",
                   "Max Temperature", "outlet_temperature", "design temp", "inlet temperature", "setpoint temperature",
                   "operatingTemperature", "minimum design temperature"]
INTERVAL_LABELS = ["temperature difference", "temperature rise", "temperature drop", "delta T", "deltaT", "delta temperature",
                   "temperature differential", "max temperature rise", "Temperature Difference", "temp rise", "temperature interval",
                   "design temperature rise"]


@pytest.mark.parametrize("label", ABSOLUTE_LABELS)
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_clear_absolute_labels_keep_working(label, swap):
    clean(T("100 degC", "212 degF", label, swap=swap))
    clean(T("0 degC", "273.15 K", label, swap=swap))
    assert T("0 degC", "0 K", label)["verdict"] == "CONFLICTS_FOUND"


@pytest.mark.parametrize("label", INTERVAL_LABELS)
@pytest.mark.parametrize("swap", BOTH_ORDERS)
def test_clear_interval_labels_keep_working(label, swap):
    clean(T("10 degC", "18 degF", label, swap=swap))
    clean(T("10 degC", "10 K", label, swap=swap))
    assert T("10 degC", "283.15 K", label)["verdict"] == "CONFLICTS_FOUND"


def test_a_delta_unit_decides_the_kind_even_under_an_unclear_label():
    clean(T("5 delta_degC", "9 delta_degF", "temperature uncertainty"))
    refused_kind(T("5 delta_degC", "5 K", "temperature uncertainty"))          # interval beside unknown: still refused


@pytest.mark.parametrize("label", UNCERTAIN_LABELS[:6])
def test_same_source_repeats_under_an_unclear_label_are_a_gap(label):
    r = audit_documents({"a.md": f"MR-27 | {label} | 1 degC\nMR-27 | {label} | 274.15 K\n", "b.md": f"MR-27 | {label} | 1 degC\n"}, 0.01)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert any(w["code"] == "TEMPERATURE_SEMANTICS" for w in r["warnings"])


def test_identical_text_under_an_unclear_label_is_still_comparable():
    clean(T("1 degC", "1 degC", "temperature uncertainty"))


# ---- the rule is a grammar, not a list of hostile strings -----------------------------------------------------------
ABS_WORDS = ["temperature", "temp", "design", "operating", "ambient", "maximum", "outlet"]
INT_WORDS = ["difference", "rise", "drop", "delta", "differential", "interval", "t"]
OTHER = ["uncertainty", "error", "deviation", "margin", "superheat", "of", "measurement", "dt", "diff", "range", "banana", "x", "value"]


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(ABS_WORDS + INT_WORDS + OTHER), min_size=1, max_size=5), st.sampled_from([" ", "_", "-", "  "]))
def test_property_any_unlisted_word_makes_the_kind_unknown(words, glue):
    kind = core._temperature_kind(["degree_Celsius"], glue.join(words))
    if any(w in OTHER for w in words):
        assert kind == "unknown", words
    if kind == "absolute":
        assert not any(w in INT_WORDS for w in words if w != "t") and any(w in ("temperature", "temp") for w in words)
    if kind == "interval":
        assert any(w in ("difference", "rise", "drop", "delta", "differential", "interval") for w in words)


# ---- every entry point ----------------------------------------------------------------------------------------------
def test_mcp_matches_the_engine():
    bad = compare_engineering_rows("MR-27 | temperature uncertainty | 1 degC\n", "MR-27 | temperature uncertainty | 274.15 K\n")
    assert (bad["verdict"], bad["coverage_complete"]) == ("NOTHING_COMPARED", False)
    bad = compare_engineering_rows("MR-27 | temperature rise | 0 degRe\n", "MR-27 | temperature rise | 273.15 K\n")
    assert (bad["verdict"], bad["coverage_complete"]) == ("NOTHING_COMPARED", False)


def test_api_matches_the_engine():
    from fastapi.testclient import TestClient
    from app.api import app
    for field, a, b in (("temperature uncertainty", "1 degC", "274.15 K"), ("temperature rise", "0 degRe", "273.15 K")):
        body = TestClient(app).post("/audit", json={"documents": {"a.md": f"MR-27 | {field} | {a}\n", "b.md": f"MR-27 | {field} | {b}\n"}}).json()
        assert (body["verdict"], body["coverage_complete"]) == ("NOTHING_COMPARED", False)


@pytest.mark.parametrize("field,a,b", [("temperature uncertainty", "1 degC", "274.15 K"), ("temperature rise", "0 degRe", "273.15 K")])
def test_streamlit_and_exports_show_no_clean_result(field, a, b):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", f"MR-27 | {field} | {a}\n".encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", f"MR-27 | {field} | {b}\n".encode(), "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception and len(at.success) == 0
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NOTHING_COMPARED", False)
    assert "Coverage complete: no" in at.session_state["audit"]["markdown"]
