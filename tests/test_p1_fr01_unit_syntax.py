"""FR-01 (#45): bounded unit-expression syntax before every Pint entry point.

Claim tested: a small lexical grammar refuses unit text with chained or oversized exponents BEFORE Pint sees it.
This is bounded syntactic rejection, not a promise of denial-of-service immunity.

Safety of this file: rows that chain exponents are only ever audited in a child process (tests/_fr01_battery.py) with a
hard wall-clock timeout and a CPU limit, so a regression fails the test instead of hanging pytest. Everything that runs
in the pytest process is static or uses ordinary units.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import audit_documents

ROOT = Path(__file__).resolve().parent.parent
BATTERY = ROOT / "tests" / "_fr01_battery.py"
BATTERY_TIMEOUT_SECONDS = 40
CPU_LIMIT_SECONDS = 30


def _limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS + 5))
    except Exception:  # pragma: no cover - non-POSIX platforms run without the CPU limit
        pass


@pytest.fixture(scope="module")
def battery(tmp_path_factory):
    out = tmp_path_factory.mktemp("fr01") / "battery.json"
    try:
        done = subprocess.run([sys.executable, str(BATTERY), str(out)], cwd=ROOT, capture_output=True, text=True,
                              timeout=BATTERY_TIMEOUT_SECONDS, preexec_fn=_limits if sys.platform != "win32" else None)
    except subprocess.TimeoutExpired:
        pytest.fail(f"FR-01: the battery did not finish in {BATTERY_TIMEOUT_SECONDS} s (child process was killed)")
    assert done.returncode == 0, done.stderr[-600:]
    return json.loads(out.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ static grammar (safe, in process)
VALID = [
    "m", "mm", "m^2", "m**2", "m2", "m^-2", "m^+2", "m^0.5", "m**0.33333331", "m/s", "m/s^2", "kg/m^3", "N*m", "m^2/s",
    "L/min", "m^3/s", "W/m/K", "J/kg/K", "kg*m^2/s^2", "degC", "degF", "delta_degC", "delta_degF", "US_liquid_gallon",
    "imperial_gallon", "thermochemical_calorie", "metric_horsepower", "short_ton", "oil_barrel", "\u00b0C", "\u00b5m",
    "\u03bcm", "m\u00b2", "m\u00b3", "kg/m\u00b3", "m\u207b\u00b2", "\u00b5m\u00b2", "kPa", "bar", "psi", "Hz", "rpm", "%", "Btu_it", "kW*h",
]


@pytest.mark.parametrize("unit", VALID)
def test_supported_unit_shapes_pass_the_syntax_check(unit):
    assert core.unit_syntax_problem(unit) is None


INVALID = {
    "m" + "^9" * 3: "CHAINED_EXPONENT",
    "m" + "**9" * 3: "CHAINED_EXPONENT",
    "m^-9^9^9": "CHAINED_EXPONENT",
    "m^2^2": "CHAINED_EXPONENT",
    "m**2**2": "CHAINED_EXPONENT",
    "m^2**2": "CHAINED_EXPONENT",
    "m2^2": "CHAINED_EXPONENT",
    "s/m^2^2": "CHAINED_EXPONENT",
    "m^x": "BAD_EXPONENT",
    "m^": "BAD_EXPONENT",
    "m**": "BAD_EXPONENT",
    "m^(2)": "BAD_EXPONENT",
    "m^1e3": "BAD_SYNTAX",
    "m^999": "EXPONENT_TOO_LARGE",
    "m999": "EXPONENT_TOO_LARGE",
    "m^2.12345678901": "EXPONENT_TOO_LARGE",
    "m^2.": "BAD_SYNTAX",
    "m//s": "BAD_SYNTAX",
    "m*/s": "BAD_SYNTAX",
    "m/": "BAD_SYNTAX",
    "m/s/": "BAD_SYNTAX",
    "(m)": "BAD_SYNTAX",
    "m s": "BAD_SYNTAX",
    "m-s": "BAD_SYNTAX",
    "m.s": "BAD_SYNTAX",
    "2m": "BAD_SYNTAX",
    "m\u00b2\u00b3\u2074": "EXPONENT_TOO_LARGE",
    "m\u00b2^2": "CHAINED_EXPONENT",
    "m^2\u00b2": "EXPONENT_TOO_LARGE",
    "m\u207b": "BAD_EXPONENT",
    "m^\u0669": "BAD_EXPONENT",
    "": "EMPTY",
    "m" * 65: "TOO_LONG",
    "*".join(["m"] * 9): "TOO_MANY_TERMS",
}


@pytest.mark.parametrize("unit,code", sorted(INVALID.items()))
def test_unsupported_unit_syntax_is_refused_with_a_stable_code(unit, code):
    assert core.unit_syntax_problem(unit) == code


def test_exponent_limit_constants_are_documented_values():
    assert core.MAX_UNIT_CHARS == 64 and core.MAX_UNIT_TERMS == 8


@settings(max_examples=400, deadline=None)
@given(st.text(alphabet="mNs^*/-.0123456789 \u00b0_C\u00b2", max_size=70))
def test_property_an_accepted_unit_never_has_two_exponent_operators_in_one_term(text):
    if core.unit_syntax_problem(text) is not None:
        return
    assert len(text) <= core.MAX_UNIT_CHARS
    for term in re.split(r"[*/](?!\*)", text.replace("**", "^")):
        assert term.count("^") <= 1, text


@settings(max_examples=200, deadline=None)
@given(st.integers(2, 20))
def test_property_every_chain_of_two_or_more_exponent_operators_is_refused(depth):
    for operator in ("^", "**"):
        unit = "m" + (operator + "3") * depth
        assert core.unit_syntax_problem(unit) == "CHAINED_EXPONENT"


def test_the_syntax_check_is_linear_on_a_long_line():
    import time
    started = time.perf_counter()
    for _ in range(200):
        core.unit_syntax_problem("m" * 2000)
        core.unit_syntax_problem("m^2*" * 500)
    assert time.perf_counter() - started < 2.0


# ------------------------------------------------------------------ positive controls (ordinary units, in process)
POSITIVE_PAIRS = [
    ("1 m^2", "10000 cm^2"), ("1 m**2", "10000 cm^2"), ("1 m^3/s", "60000 L/min"), ("1 kg/m^3", "0.001 g/cm^3"),
    ("1 N*m", "1 J"), ("36 km/h", "10 m/s"), ("1 m^2/s", "1000000 mm^2/s"), ("1000 US_liquid_gallon", "3785.411784 L"),
    ("0 degC", "273.15 K"), ("10 bar", "1000 kPa"),
]


@pytest.mark.parametrize("a,b", POSITIVE_PAIRS)
@pytest.mark.parametrize("name_a,name_b", [("a.md", "b.md"), ("a.csv", "b.csv"), ("a.txt", "b.txt")])
def test_ordinary_units_still_compare_and_stay_complete(a, b, name_a, name_b):
    field = "temperature" if "degC" in a else "value"
    sep = "," if name_a.endswith(".csv") else " | "
    r = audit_documents({name_a: f"X-1{sep}{field}{sep}{a}\n", name_b: f"X-1{sep}{field}{sep}{b}\n"}, 0.001)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True, r["warnings"]


def test_existing_huge_exponent_text_is_a_visible_gap_not_a_crash():
    r = audit_documents({"a.md": "L-1 | size | 1 km^999\n", "b.md": "L-1 | size | 1 km^999\n"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert [w["code"] for w in r["warnings"]] == ["UNSUPPORTED_UNIT", "UNSUPPORTED_UNIT"]
    assert {w["detail"] for w in r["warnings"]} == {"EXPONENT_TOO_LARGE"}


def test_a_refused_unit_keeps_row_evidence_and_a_machine_readable_reason():
    r = audit_documents({"a.md": "# t\nX-1 | length | 1 m^2^2\n", "b.md": "X-1 | length | 1 m\n"})
    warning = r["warnings"][0]
    assert (warning["code"], warning["reason"], warning["detail"]) == ("UNSUPPORTED_UNIT", "UnitSyntaxError", "CHAINED_EXPONENT")
    assert warning["document"] == "a.md" and warning["line"] == 2 and warning["excerpt"] == "X-1 | length | 1 m^2^2"
    assert r["coverage"]["a.md"]["rows_unsupported_unit"] == 1
    json.dumps(r, allow_nan=False)


def test_a_refused_row_never_yields_a_clean_complete_verdict_in_either_order():
    for a, b in (("m^2^2", "m"), ("m", "m^2^2")):
        r = audit_documents({"a.md": f"X-1 | length | 1 {a}\nX-2 | length | 3 m\n", "b.md": f"X-1 | length | 1 {b}\nX-2 | length | 3 m\n"})
        assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False


def test_recognizer_does_not_call_pint_for_an_exponent_shaped_token(monkeypatch):
    calls = []
    real = core.UNITS.Quantity

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(core.UNITS, "Quantity", spy)
    core._unit_like.cache_clear()
    bad = "m^2^2"
    assert core._unit_like(bad) is True          # exponent-shaped: still a unit column for the unparsed-row warning
    assert calls == []
    r = audit_documents({"a.md": f"| A-1 | B | {bad} | 1 |\n", "b.md": "x\n"})
    assert [w["code"] for w in r["warnings"]] == ["UNPARSED_ROW"] and r["coverage_complete"] is False
    assert calls == []


def test_hyphenated_prose_in_a_unit_column_is_not_mistaken_for_a_unit():
    core._unit_like.cache_clear()
    assert core._unit_like("Isolation-valve") is False
    assert core._unit_like("Grundfos") is False


# ------------------------------------------------------------------ chained exponents (child process only)
CHAIN_SCENARIOS = ["md", "md_swapped", "md_stars", "md_negative", "md_short_chain", "txt", "csv_three_cells", "csv_four_cells"]


@pytest.mark.parametrize("name", CHAIN_SCENARIOS)
def test_fr01_chained_exponents_fail_closed_quickly(battery, name):
    r = battery["core"][name]
    assert r["seconds"] < 5
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    refused = [w for w in r["warnings"] if w["code"] == "UNSUPPORTED_UNIT"]
    assert len(refused) == 1 and refused[0]["reason"] == "UnitSyntaxError" and refused[0]["detail"] == "CHAINED_EXPONENT"
    assert refused[0]["line"] == 1 and refused[0]["excerpt"].startswith("X-1")


def test_fr01_recognizer_path_reports_an_unparsed_row(battery):
    r = battery["core"]["recognizer_table"]
    assert r["seconds"] < 5 and r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False
    assert [w["code"] for w in r["warnings"]] == ["UNPARSED_ROW"] and r["warnings"][0]["line"] == 1


def test_fr01_recognizer_row_beside_a_valid_row_stays_incomplete(battery):
    r = battery["core"]["recognizer_with_valid_row"]
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and r["comparisons"] == 1


def test_fr01_valid_rows_next_to_a_refused_row_are_still_compared(battery):
    r = battery["core"]["valid_row_after_bad_row"]
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False and r["comparisons"] == 1


def test_fr01_api_mcp_and_ui_agree(battery):
    assert battery["api"] == {"status": 200, "verdict": "NOTHING_COMPARED", "coverage_complete": False}
    assert battery["mcp"] == {"verdict": "NOTHING_COMPARED", "coverage_complete": False}
    ui = battery["ui"]
    assert ui["exception"] is False and ui["success_banners"] == 0
    assert (ui["verdict"], ui["coverage_complete"]) == ("NOTHING_COMPARED", False)
    assert ui["markdown_says_incomplete"] and ui["markdown_names_row"]
