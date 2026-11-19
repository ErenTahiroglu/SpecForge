"""FROZEN-RT-001 (#60): a field-name character that the label tokenizer cannot see must never turn an unclear label into a clear one.

Policy under test (README, "Temperature semantics"): the kind of a temperature is read from a label only when EVERY character of
the property name is an ASCII letter, a space, `_` or `-`. Any other character (Greek delta, accented or look-alike letters, digits)
makes the kind UNKNOWN. An unknown kind with an offset unit (degC, degF) is not compared with a different value. Nothing is
transliterated or deleted to make a word match. A delta_ unit still decides by itself.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui.py"
DELTA = "\u0394"


def T(field, a, b, tol=0.01, swap=False):
    docs = {"a.md": f"MR-27 | {field} | {a}\n", "b.md": f"MR-27 | {field} | {b}\n"}
    if swap:
        docs = {"a.md": docs["b.md"], "b.md": docs["a.md"]}
    return audit_documents(docs, tol)


def refused(r):
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False and r["comparisons"] == 0, r["verdict"]
    assert not r["matches"] and not r["findings"]
    hits = [w for w in r["warnings"] if w["code"] == "TEMPERATURE_SEMANTICS"]
    assert len(hits) == 1 and hits[0]["detail"] in ("unknown_kind", "uncertain")
    return hits[0]


# ---- the reported fixture ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pair", [("1 degC", "274.15 K"), ("1 degC", "1 K"), ("1 degF", "1 degC")])
@pytest.mark.parametrize("swap", [False, True])
def test_the_reported_field_is_never_a_match_or_a_conflict_by_guess(pair, swap):
    w = refused(T(f"temperature_{DELTA}", *pair, swap=swap))
    assert w["detail"] == "unknown_kind"
    assert w["reference"]["excerpt"].startswith(f"MR-27 | temperature_{DELTA} |") and w["reference"]["line"] == 1
    assert w["other"]["excerpt"].startswith(f"MR-27 | temperature_{DELTA} |") and w["other"]["line"] == 1
    assert {w["reference"]["document"], w["other"]["document"]} == {"a.md", "b.md"}


def test_the_kind_function_sees_the_whole_name():
    assert core._temperature_kind(["degree_Celsius"], f"temperature_{DELTA}") == "unknown"
    assert core._temperature_kind(["degree_Celsius"], "temperature") == "absolute"
    assert core._temperature_kind(["delta_degree_Celsius"], f"temperature_{DELTA}") == "interval"      # an explicit unit still decides


# ---- negative controls: every supported spelling with a character the tokenizer cannot see ------------------------------------
UNICODE_LABELS = [f"temperature_{DELTA}", f"temperature {DELTA}", f"temperature-{DELTA}", f"temperature  {DELTA}", f"temperature_{DELTA}_",
                  f"temp {DELTA}", f"temperature{DELTA}", f"Temperature_{DELTA}", f"temperature_{DELTA}T", "temperature_\u03b4", f"design temperature {DELTA}",
                  "temperatur\u00e9", "temp\u00e9rature rise", "t\u0435mperature", "temperature \u0440ise",                       # accented and Cyrillic look-alikes
                  "temperature\u00b2", "temperature_2", "temperature 1", "design temperature2", "temperature\u2212rise",
                  "temperature_\u0394_rise", "ASCII temperature \u0645"]


@pytest.mark.parametrize("label", UNICODE_LABELS)
@pytest.mark.parametrize("pair", [("1 degC", "274.15 K"), ("1 degC", "1 K")])
def test_a_label_with_any_character_outside_ascii_letters_is_unknown(label, pair):
    r = T(label, *pair)
    assert r["coverage_complete"] is False and not r["matches"] and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    if r["records"] == 2:                                     # the row was parsed: the label must have been judged unknown
        refused(r)


@pytest.mark.parametrize("label", [f"{DELTA}T", f"{DELTA}temperature", f"{DELTA}_temperature", "\u0394 temperature", "\u03a4emperature"])
def test_a_name_that_does_not_start_with_an_ascii_letter_is_reported_as_an_unparsed_row(label):
    r = T(label, "1 degC", "274.15 K")
    assert r["coverage_complete"] is False and r["records"] == 0 and r["verdict"] == "NOTHING_COMPARED"
    assert sorted(w["document"] for w in r["warnings"] if w["code"] == "UNPARSED_ROW") == ["a.md", "b.md"]


def test_invisible_direction_controls_in_a_name_are_not_read_as_a_row():
    r = T("temperature\u202e", "1 degC", "274.15 K")
    assert r["coverage_complete"] is False and not r["matches"]


def test_text_after_the_symbol_does_not_rescue_the_label():
    for label in (f"temperature rise {DELTA}", f"rise temperature {DELTA}", f"temperature difference_{DELTA}"):
        r = T(label, "1 degC", "274.15 K")
        assert r["coverage_complete"] is False and not r["matches"], label


# ---- identical text is the only comparable case under an unknown kind ------------------------------------------------------------
def test_identical_value_and_unit_stay_comparable_under_the_unclear_label():
    r = T(f"temperature_{DELTA}", "1 degC", "1 degC")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


def test_plain_kelvin_without_offset_units_is_unchanged():
    r = T(f"temperature_{DELTA}", "1 K", "1 K")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"


def test_same_source_repeats_under_the_unclear_label_are_a_gap():
    r = audit_documents({"a.md": f"MR-27 | temperature_{DELTA} | 1 degC\nMR-27 | temperature_{DELTA} | 274.15 K\n",
                         "b.md": f"MR-27 | temperature_{DELTA} | 1 degC\n"}, 0.01)
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert any(w["code"] == "TEMPERATURE_SEMANTICS" and w.get("scope") == "same_source" for w in r["warnings"])


# ---- positive controls ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("label", ["temperature", "design temperature", "operating temperature", "ambient temperature", "Temperature", "operating_temperature"])
def test_ascii_absolute_labels_still_convert_as_absolute(label):
    r = T(label, "100 degC", "212 degF")
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True
    assert T(label, "1 degC", "274.15 K")["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"


@pytest.mark.parametrize("label", ["temperature rise", "temperature difference", "delta T", "deltaT", "temperature drop", "max temperature rise"])
def test_ascii_interval_labels_still_convert_as_differences(label):
    assert T(label, "1 degC", "1 K")["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert T(label, "1 degC", "274.15 K")["verdict"] == "CONFLICTS_FOUND"


def test_temperature_uncertainty_is_still_not_absolute():
    refused(T("temperature uncertainty", "1 degC", "274.15 K"))


def test_explicit_delta_units_and_linear_scales_are_unchanged():
    assert T(f"temperature_{DELTA}", "1 delta_degC", "1 delta_degC")["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert T("temperature rise", "1 delta_degC", "1 K")["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert T("temperature rise", "9 degR", "5 K")["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    r = T("temperature rise", "0 degRe", "273.15 K")                                  # the earlier scale policy still refuses Réaumur
    assert r["coverage_complete"] is False and any(w.get("reason") == "UnsupportedTemperatureScale" for w in r["warnings"])


# ---- property: no character outside the ASCII label alphabet can pass -----------------------------------------------------------
WORDS = ["temperature", "temp", "design", "operating", "rise", "difference", "delta", "t", "uncertainty", "ambient"]
EXTRA = [DELTA, "\u03b4", "\u00e9", "\u0435", "\u00b2", "2", "\u2212", "\u00a0", "\u200b"]


@settings(max_examples=400, deadline=None)
@given(st.lists(st.sampled_from(WORDS), min_size=1, max_size=4), st.sampled_from(EXTRA), st.integers(0, 4), st.sampled_from([" ", "_", "-"]))
def test_property_one_foreign_character_anywhere_makes_the_kind_unknown(words, extra, position, glue):
    parts = list(words)
    parts.insert(min(position, len(parts)), extra)
    assert core._temperature_kind(["degree_Celsius"], glue.join(parts)) == "unknown"


# ---- every entry point ------------------------------------------------------------------------------------------------------------
ROWS = (f"MR-27 | temperature_{DELTA} | 1 degC\n", f"MR-27 | temperature_{DELTA} | 274.15 K\n")


def test_mcp_function_and_api_agree():
    m = compare_engineering_rows(*ROWS)
    assert (m["verdict"], m["coverage_complete"]) == ("NOTHING_COMPARED", False)
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": ROWS[0], "b.md": ROWS[1]}}).json()
    assert (body["verdict"], body["coverage_complete"]) == ("NOTHING_COMPARED", False)


def test_actual_mcp_stdio_tool_agrees():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def call():
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await asyncio.wait_for(session.call_tool("compare_engineering_rows", {"document_a": ROWS[0], "document_b": ROWS[1]}), 30)
                return result.structuredContent or json.loads(result.content[0].text)
    body = asyncio.run(asyncio.wait_for(call(), 60))
    body = body.get("result", body)
    assert (body["verdict"], body["coverage_complete"]) == ("NOTHING_COMPARED", False)


def test_markdown_shows_both_rows_and_incomplete_coverage():
    r = audit_documents({"a.md": ROWS[0], "b.md": ROWS[1]})
    md = render_markdown(build_view(r, {"a.md": "Document A", "b.md": "Document B"}), "2026-11-19 00:00 UTC")
    assert "Coverage complete: no" in md and "TEMPERATURE_SEMANTICS" in md
    assert f"temperature_{DELTA} | 1 degC" in md and f"temperature_{DELTA} | 274.15 K" in md


def test_streamlit_shows_no_green_result_and_exports_agree():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", ROWS[0].encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", ROWS[1].encode(), "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception and len(at.success) == 0
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NOTHING_COMPARED", False)
