"""#56: a measurement written with non-ASCII decimal digits must never become a clean zero.

Policy under test (README, "Source format and limits"): the accepted numeric syntax is ASCII only. A row whose number uses other
decimal digits (Arabic-Indic, Devanagari, full-width, mathematical, ...) is recognized and reported as UNPARSED_ROW with its
source, line and text; it is never parsed, never normalized and never compared. Python's float() accepts such digits, so the
old parser could turn a nonzero number below the smallest float into 0.0, and the underflow guard (ASCII digits only) missed it.
"""
import json
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from app import core
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows
from app.report import build_view, render_markdown

UI = Path(__file__).resolve().parent.parent / "app" / "ui.py"
ARABIC_ZERO, ARABIC_ONE = "\u0660", "\u0661"
TINY_ARABIC = "0." .replace("0", ARABIC_ZERO) + ARABIC_ZERO * 400 + ARABIC_ONE        # 1e-401, nonzero, below the smallest float
TINY_ASCII = "0." + "0" * 400 + "1"
SCRIPTS = {"arabic_indic": "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "devanagari": "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f",
           "fullwidth": "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "math_bold": "\U0001d7ce\U0001d7cf\U0001d7d0\U0001d7d1\U0001d7d2\U0001d7d3\U0001d7d4\U0001d7d5\U0001d7d6\U0001d7d7",
           "thai": "\u0e50\u0e51\u0e52\u0e53\u0e54\u0e55\u0e56\u0e57\u0e58\u0e59"}


def unparsed(r):
    return [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"]


def test_the_hazard_is_real_float_accepts_the_digits_and_underflows_to_zero():
    assert float(TINY_ARABIC) == 0.0 and float(TINY_ASCII) == 0.0
    assert any(unicodedata.category(c) == "Nd" and c not in "0123456789" for c in TINY_ARABIC)


def test_the_row_patterns_accept_ascii_digits_only():
    assert core.ENTRY.fullmatch(f"X-1 | length | {TINY_ASCII} m")
    assert not core.ENTRY.fullmatch(f"X-1 | length | {TINY_ARABIC} m")
    assert not core.CSV_ENTRY.fullmatch(f"X-1,length,{TINY_ARABIC} m")
    assert not core.CSV_ENTRY.fullmatch(f"X-1,length,{TINY_ARABIC},m")


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_each_script_is_recognized_and_reported_not_compared(name):
    digits = SCRIPTS[name]
    value = digits[1] + digits[0]                                              # "10" in that script
    r = audit_documents({"a.md": f"X-1 | length | {value} m\n", "b.md": "X-1 | length | 10 m\n"})
    assert r["records"] == 1 and r["comparisons"] == 0
    assert r["coverage_complete"] is False and r["verdict"] == "NOTHING_COMPARED"
    (w,) = unparsed(r)
    assert (w["document"], w["line"], w["excerpt"]) == ("a.md", 1, f"X-1 | length | {value} m")
    assert w["detail"] == "NON_ASCII_DIGITS" and "non-ASCII" in w["reason"]
    assert [u["document"] for u in r["unmatched"]] == ["b.md"]
    json.dumps(r, allow_nan=False)


@pytest.mark.parametrize("value", [TINY_ARABIC, "-" + TINY_ARABIC, "+" + TINY_ARABIC, ARABIC_ZERO + "." + ARABIC_ZERO * 5 + ARABIC_ONE,
                                   "1" + ARABIC_ZERO, ARABIC_ONE + "0", "\uff11\uff10.\uff15", ARABIC_ZERO, "00" + ARABIC_ONE])
def test_near_zero_mixed_signed_leading_zero_and_zero_values_never_parse(value):
    r = audit_documents({"a.md": f"X-1 | length | {value} m\n", "b.md": "X-1 | length | 0 m\n"})
    assert r["records"] == 1                              # only the ASCII zero of b.md
    assert r["coverage_complete"] is False and r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert len(unparsed(r)) == 1 and not r["matches"] and not r["findings"]


def test_the_exact_false_clean_case_the_reviewer_reported():
    r = audit_documents({"a.md": f"X-1 | length | {TINY_ARABIC} m\n", "b.md": "X-1 | length | 0 m\n"})
    assert r["verdict"] == "NOTHING_COMPARED" and r["coverage_complete"] is False


# ---- an unsupported row beside valid rows is never a green complete result, in every layout and order ------------------------
@pytest.mark.parametrize("ext,sep", [(".md", " | "), (".txt", " | "), (".csv", ",")])
@pytest.mark.parametrize("swap", [False, True])
def test_valid_rows_plus_one_unicode_row_are_partial_not_complete(ext, sep, swap):
    good = f"P-1{sep}pressure{sep}10 bar\nP-2{sep}length{sep}5 m\n"
    bad = f"X-9{sep}length{sep}{SCRIPTS['arabic_indic'][1] + SCRIPTS['arabic_indic'][0]} m\n"
    a, b = ("a" + ext, good + bad), ("b" + ext, good + "X-9" + sep + "length" + sep + "10 m\n")
    docs = dict([b, a]) if swap else dict([a, b])
    r = audit_documents(docs)
    assert r["comparisons"] == 2 and r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    assert len(unparsed(r)) == 1 and unparsed(r)[0]["document"] == "a" + ext


def test_conflict_elsewhere_is_still_reported_beside_the_unsupported_row():
    r = audit_documents({"a.md": f"P-1 | pressure | 10 bar\nX-1 | length | {TINY_ARABIC} m\n", "b.md": "P-1 | pressure | 900 kPa\nX-1 | length | 0 m\n"})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False


# ---- ASCII behaviour is unchanged ----------------------------------------------------------------------------------------
def test_ascii_underflow_is_still_refused_and_small_values_still_compare():
    r = audit_documents({"a.md": f"X-1 | length | {TINY_ASCII} m\n", "b.md": "X-1 | length | 0 m\n"})
    assert [w["code"] for w in r["warnings"]] == ["UNSUPPORTED_UNIT"] and r["warnings"][0]["reason"] == "NumericRangeError"
    ok = audit_documents({"a.md": "X-1 | length | 0.000001 m\n", "b.md": "X-1 | length | 0.000001 m\n"})
    assert ok["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and ok["coverage_complete"] is True
    zero = audit_documents({"a.md": "X-1 | length | 0 m\n", "b.md": "X-1 | length | -0.0 m\n"})
    assert zero["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    for text in ("+5", "-5", "007", "0.5", ".5"):
        assert audit_documents({"a.md": f"X-1 | length | {text} m\n", "b.md": f"X-1 | length | {text} m\n"})["comparisons"] == 1


def test_tag_and_unit_text_are_not_rewritten():
    r = audit_documents({"a.md": "X\u0661-1 | length | 10 m\n", "b.md": "X\u0661-1 | length | 10 m\n"})     # a non-ASCII digit inside a tag
    assert r["comparisons"] == 1 and r["matches"][0]["reference"]["tag"] == "X\u0661-1"


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.sampled_from(list("0123456789") + list(SCRIPTS["arabic_indic"]) + list(SCRIPTS["fullwidth"]) + list(".+-")), min_size=1, max_size=12))
def test_property_a_row_with_any_non_ascii_digit_is_never_parsed(value):
    row = f"X-1 | length | {value} m"
    r = audit_documents({"a.md": row + "\n", "b.md": "X-1 | length | 1 m\n"})
    has_non_ascii = any(unicodedata.category(c) == "Nd" and c not in "0123456789" for c in value)
    if has_non_ascii:
        assert r["records"] == 1 and r["coverage_complete"] is False
    if r["records"] == 2:
        assert not has_non_ascii


# ---- every surface and the human-readable export -----------------------------------------------------------------------------
FIXTURE = (f"X-1 | length | {TINY_ARABIC} m\n", "X-1 | length | 0 m\n")


def test_mcp_and_api_agree():
    m = compare_engineering_rows(*FIXTURE)
    assert (m["verdict"], m["coverage_complete"]) == ("NOTHING_COMPARED", False)
    from fastapi.testclient import TestClient
    from app.api import app
    body = TestClient(app).post("/audit", json={"documents": {"a.md": FIXTURE[0], "b.md": FIXTURE[1]}}).json()
    assert (body["verdict"], body["coverage_complete"]) == ("NOTHING_COMPARED", False)


def test_markdown_lists_the_row_and_says_coverage_is_incomplete():
    r = audit_documents({"a.md": FIXTURE[0], "b.md": FIXTURE[1]})
    md = render_markdown(build_view(r, {"a.md": "Document A", "b.md": "Document B"}), "2026-11-19 00:00 UTC")
    assert "Coverage complete: no" in md and "UNPARSED_ROW" in md and ARABIC_ONE in md
    assert "Every supported row found was compared." not in md


def test_streamlit_shows_no_green_result():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(UI), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", FIXTURE[0].encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", FIXTURE[1].encode(), "text/markdown")
    at.run()
    next(x for x in at.button if x.label == "Run audit").click().run()
    assert not at.exception and len(at.success) == 0
    exported = json.loads(at.session_state["audit"]["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("NOTHING_COMPARED", False)
