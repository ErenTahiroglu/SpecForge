"""P1.1 regression tests: six defects found by the P1 red-team review (D01-D06).

Written BEFORE the fixes. Each test names the defect. New helpers are referenced as module attributes
(report.escape_md, ingest.input_fingerprint, ...) so every test fails individually until its fix exists.
"""
from pathlib import Path

import pytest

from app import ingest, report
from app.core import audit_documents

PRESSURE_DIM = "[mass] / [length] / [time] ** 2"


def A(docs, tol=0.01):
    return audit_documents(docs, tol)


def view(result, labels=None):
    return report.build_view(result, labels or {}, [], [])


# =============================== D01 document identity ===============================

def test_d01_equal_duplicates_in_one_file_are_not_a_cross_document_comparison():
    r = A({"a.md": "P-1 | pressure | 5 bar\nP-1 | pressure | 5 bar", "b.md": "P-1 | pressure | 500 kPa"})
    assert r["comparisons"] == 1 and len(r["matches"]) == 1 and r["findings"] == []
    m = r["matches"][0]
    assert {m["reference"]["document"], m["other"]["document"]} == {"a.md", "b.md"}
    dup = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]
    assert len(dup) == 1 and dup[0]["consistent"] is True and dup[0]["lines"] == [1, 2]


def test_d01_contradiction_inside_one_file_is_internal_not_a_document_mismatch():
    r = A({"a.md": "P-1 | pressure | 5 bar\nP-1 | pressure | 6 bar", "b.md": "P-1 | pressure | 5 bar"})
    codes = [f["code"] for f in r["findings"]]
    assert codes == ["INTERNAL_CONTRADICTION"]
    f = r["findings"][0]
    assert f["scope"] == "same_source" and f["reference"]["document"] == f["other"]["document"] == "a.md"
    assert (f["reference"]["line"], f["other"]["line"]) == (1, 2)
    assert any(w["code"] == "AMBIGUOUS_COMPARISON" for w in r["warnings"])
    assert r["comparisons"] == 0 and r["matches"] == []          # no cross-document claim is made
    assert r["status"] == "DIFFERENCES"


def test_d01_same_tag_on_two_pdf_pages_with_different_values():
    r = A({"x.pdf:page-1": "P-1 | pressure | 10 bar", "x.pdf:page-2": "P-1 | pressure | 9 bar", "b.md": "P-1 | pressure | 10 bar"})
    assert [f["code"] for f in r["findings"]] == ["INTERNAL_CONTRADICTION"]
    f = r["findings"][0]
    assert {f["reference"]["document"], f["other"]["document"]} == {"x.pdf:page-1", "x.pdf:page-2"}
    dup = [w for w in r["warnings"] if w["code"] == "DUPLICATE_IDENTIFIER"]
    assert len(dup) == 1 and dup[0]["consistent"] is False and dup[0]["document"] == "x.pdf"
    assert any(w["code"] == "AMBIGUOUS_COMPARISON" for w in r["warnings"])
    assert r["matches"] == [] and r["comparisons"] == 0


def test_d01_consistent_duplicates_across_pages_use_one_representative_and_still_compare():
    r = A({"x.pdf:page-1": "P-1 | pressure | 10 bar", "x.pdf:page-2": "P-1 | pressure | 10 bar", "b.md": "P-1 | pressure | 9 bar"})
    assert [f["code"] for f in r["findings"]] == ["VALUE_MISMATCH"]
    f = r["findings"][0]
    assert f["scope"] == "cross_source" and f["reference"]["document"] == "x.pdf:page-1" and f["other"]["document"] == "b.md"
    assert any(w["code"] == "DUPLICATE_IDENTIFIER" and w["consistent"] for w in r["warnings"])


def test_d01_tag_only_on_pages_of_one_pdf_is_unmatched_not_compared():
    r = A({"x.pdf:page-1": "P-1 | pressure | 10 bar", "x.pdf:page-2": "P-1 | pressure | 10 bar", "b.md": "V-2 | length | 1 m"})
    assert r["comparisons"] == 0 and len(r["unmatched"]) == 3


def test_d01_normal_two_document_mismatch_is_unchanged_and_marked_cross_source():
    r = A({"spec.md": "P-101 | pressure | 10 bar", "bom.md": "P-101 | pressure | 900 kPa"})
    f = r["findings"][0]
    assert f["code"] == "VALUE_MISMATCH" and f["scope"] == "cross_source" and f["relative_difference"] == 0.1


def test_d01_report_never_says_a_document_disagrees_with_itself():
    docs = {"x.pdf:page-1": "P-1 | pressure | 10 bar", "x.pdf:page-2": "P-1 | pressure | 9 bar", "b.md": "P-1 | pressure | 10 bar"}
    labels = {"x.pdf:page-1": "Document A", "x.pdf:page-2": "Document A", "b.md": "Document B"}
    v = view(A(docs), labels)
    md = report.render_markdown(v)
    assert v.conflicts and all("Document A and Document A" not in c.explanation for c in v.conflicts)
    for c in v.conflicts:
        for ev in c.original:
            if ev.document.startswith("x.pdf"):
                assert ev.label != "Document B"
        assert c.issue == "One document contradicts itself"
    assert "Document A and Document A" not in md
    assert v.internal == 1 and "inside one document" in v.headline


def test_d01_evidence_label_follows_the_real_document_not_the_position():
    docs = {"b.md": "P-1 | pressure | 10 bar", "a.md": "P-1 | pressure | 9 bar"}      # B listed first on purpose
    v = view(A(docs), {"a.md": "Document A", "b.md": "Document B"})
    labels = {ev.document: ev.label for ev in v.conflicts[0].original}
    assert labels == {"b.md": "Document B", "a.md": "Document A"}


def test_d01_consistent_duplicates_are_a_note_not_a_coverage_gap():
    v = view(A({"a.md": "P-1 | pressure | 5 bar\nP-1 | pressure | 5 bar", "b.md": "P-1 | pressure | 500 kPa"}),
             {"a.md": "Document A", "b.md": "Document B"})
    assert v.verdict == report.VERDICT_MATCH and v.metrics["not_compared"] == 0
    assert [n.code for n in v.notes] == ["DUPLICATE_IDENTIFIER"] and v.equivalents[0].original[0].document != v.equivalents[0].original[1].document


def test_d01_ambiguous_comparison_is_a_visible_gap():
    v = view(A({"a.md": "P-1 | pressure | 5 bar\nP-1 | pressure | 6 bar", "b.md": "P-1 | pressure | 5 bar"}), {"a.md": "Document A", "b.md": "Document B"})
    assert "AMBIGUOUS_COMPARISON" in [i.code for i in v.coverage] and v.metrics["not_compared"] >= 1


def test_d01_table_rows_do_not_pretend_two_documents():
    v = view(A({"a.md": "P-1 | pressure | 5 bar\nP-1 | pressure | 6 bar", "b.md": ""}), {"a.md": "Document A", "b.md": "Document B"})
    row = report.table_rows(v.conflicts)[0]
    assert row["Kind"] == "Inside one document" and "Document B" not in row["First: source"] + row["Second: source"]


# =============================== D02 stale results ===============================

def test_d02_fingerprint_depends_on_content_not_only_the_name():
    k1 = ingest.input_fingerprint(False, "doc.md", b"P-1 | pressure | 10 bar", "doc.md", b"P-1 | pressure | 900 kPa", 1.0)
    k2 = ingest.input_fingerprint(False, "doc.md", b"P-1 | pressure | 10 bar", "doc.md", b"P-1 | pressure | 800 kPa", 1.0)
    assert k1 != k2 and k1 == ingest.input_fingerprint(False, "doc.md", b"P-1 | pressure | 10 bar", "doc.md", b"P-1 | pressure | 900 kPa", 1.0)


def test_d02_fingerprint_depends_on_settings_and_mode():
    base = (False, "a.md", b"x", "b.md", b"y", 1.0)
    assert ingest.input_fingerprint(*base) != ingest.input_fingerprint(False, "a.md", b"x", "b.md", b"y", 2.5)
    assert ingest.input_fingerprint(*base) != ingest.input_fingerprint(True, "a.md", b"x", "b.md", b"y", 1.0)
    assert ingest.input_fingerprint(*base) != ingest.input_fingerprint(False, "b.md", b"y", "a.md", b"x", 1.0)   # A/B swap


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "ui.py"), default_timeout=60)


def _example_bytes():
    root = Path(__file__).resolve().parent.parent
    return (root / "examples/spec.md").read_bytes(), (root / "examples/bom.md").read_bytes()


def test_d02_stale_result_is_not_shown_when_no_input_is_selected():
    at = _app()
    at.session_state["audit"] = {"blocked": True, "issues": [{"code": "X", "message": "old result"}], "files": [], "input_key": "OLD"}
    at.run()
    assert not at.exception
    assert [w.value for w in at.warning] == [] and "audit" not in at.session_state
    assert any("changed" in i.value for i in at.info)                                    # the user is told why nothing is shown


def test_d02_result_disappears_after_a_setting_changes_and_returns_after_rerun():
    at = _app()
    at.run()
    at.checkbox(key="use_example").check().run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception and any("conflict" in e.value for e in at.error)
    at.slider[0].set_value(5.0).run()
    assert not at.exception and not at.error and "audit" not in at.session_state       # stale result cleared
    assert any("changed" in i.value for i in at.info)
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert any("conflict" in e.value for e in at.error)
    assert "audit" in at.session_state and at.session_state["audit"]["input_key"]


def test_d02_result_stays_while_nothing_changes():
    at = _app()
    at.run(); at.checkbox(key="use_example").check().run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    at.run()                                                                           # unrelated rerun
    assert "audit" in at.session_state and any("conflict" in e.value for e in at.error)


# =============================== D03 untrusted file names ===============================

HOSTILE = ["<img src=x onerror=alert(1)>.md", "x<b>BOLD</b>.md", "[click](javascript:alert(1)).md", "**bold**.md", "`code`.md", "a|b.md", "<script>alert(1)</script>.md"]


@pytest.mark.parametrize("name", HOSTILE)
def test_d03_escape_md_neutralizes_markup(name):
    out = report.escape_md(name)
    for raw in ("<", ">", "[", "]", "*", "`", "|"):
        if raw in name:
            assert out.count("\\" + raw) == name.count(raw), (raw, out)     # every raw character is backslash-escaped
    assert "\n" not in report.escape_md("a\nb.md") and "\r" not in report.escape_md("a\rb.md")


@pytest.mark.parametrize("name", HOSTILE)
def test_d03_evidence_html_contains_only_our_own_tags(name):
    ev = report.Evidence("Document A", name, "line 1", "P-1 | pressure | 5 bar")
    html = report.evidence_html(ev)
    assert html.count("<b>") == 1 and html.count("<div") == 1 and html.count("<span") == 1
    assert "<img" not in html and "<script" not in html and "<b>BOLD" not in html
    if "<" in name:
        assert "&lt;" in html


@pytest.mark.parametrize("name", HOSTILE)
def test_d03_markdown_report_has_no_live_markup_from_file_names(name):
    r = A({name: "P-1 | pressure | 5 bar", "b.md": "P-1 | pressure | 6 bar\nP-2 | length | 1 m"})
    v = report.build_view(r, {name: "Document A", "b.md": "Document B"}, [{"code": "X", "filename": name, "message": f"{name}: not audited"}],
                          [f"Document A: {name}", "Document B: b.md"])
    md = report.render_markdown(v)
    unescaped = md.replace("\\<", "").replace("\\>", "")
    assert "<img" not in unescaped and "<script" not in unescaped and "<b>BOLD" not in unescaped and "](javascript" not in md.replace("\\]", "")
    if report.escape_md(name) != name:
        assert name not in md, "raw file name appears unescaped in the report"
    assert "\n- **Input**" in md


def test_d03_ui_renders_file_names_as_text():
    name = "[click](javascript:alert(1))<img src=x onerror=alert(1)>.md"
    docs = {name: "P-1 | pressure | 5 bar", "b.md": "P-1 | pressure | 6 bar"}
    r = A(docs); v = report.build_view(r, {name: "Document A", "b.md": "Document B"}, [{"code": "X", "filename": name, "message": f"{name}: not valid UTF-8. Not audited."}], [])
    ex = _example_bytes()
    key = ingest.input_fingerprint(True, "spec.md", ex[0], "bom.md", ex[1], 1.0)
    at = _app()
    at.session_state["use_example"] = True
    at.session_state["audit"] = {"blocked": False, "view": v, "result": r, "markdown": report.render_markdown(v), "json": "{}", "input_key": key}
    at.run()
    assert not at.exception
    values = [m.value for m in at.markdown]
    assert values and all("<img" not in v_ and "onerror=alert" not in v_.replace("&lt;img src=x onerror=alert(1)&gt;", "") for v_ in values)
    texts = [w.value for w in at.warning] + [e.label for e in at.expander] + [c.value for c in at.caption]
    assert texts and all("](javascript" not in t.replace("\\]", "") for t in texts)


# =============================== D04 ambiguous units ===============================

AMBIGUOUS = ["ton", "tons", "gallon", "gal", "quart", "qt", "pint", "pt", "cal", "calorie", "hp", "horsepower", "Btu", "BTU", "gallon/min"]


@pytest.mark.parametrize("unit", AMBIGUOUS)
def test_d04_ambiguous_unit_is_not_compared_and_is_explained(unit):
    r = A({"a.md": f"M-1 | q | 1 {unit}", "b.md": "M-1 | q | 1 kg"})
    w = [x for x in r["warnings"] if x["code"] == "AMBIGUOUS_UNIT"]
    assert len(w) == 1 and w[0]["document"] == "a.md" and w[0]["line"] == 1 and w[0]["alternatives"]
    assert r["comparisons"] == 0 and r["findings"] == [] and r["matches"] == []
    assert r["coverage"]["a.md"]["rows_ambiguous_unit"] == 1


def test_d04_the_reported_case_ton_vs_us_short_ton_kg():
    r = A({"a.md": "M-1 | q | 1 ton", "b.md": "M-1 | q | 907.18474 kg"}, 1e-6)
    assert r["status"] == "NO_COMPARABLE_ROWS" and r["warnings"][0]["code"] == "AMBIGUOUS_UNIT"
    assert {"short_ton", "long_ton", "metric_ton"} <= set(r["warnings"][0]["alternatives"])


@pytest.mark.parametrize("a,b", [
    ("1 short_ton", "907.18474 kg"), ("1 long_ton", "1016.0469088 kg"), ("1 tonne", "1000 kg"), ("1 metric_ton", "1000 kg"),
    ("1 imperial_gallon", "4.54609 L"), ("1 US_liquid_gallon", "3.785411784 L"),
    ("1 thermochemical_calorie", "4.184 J"), ("1 international_calorie", "4.1868 J"),
    ("1 international_british_thermal_unit", "1055.055853 J"),
])
def test_d04_explicit_units_convert_correctly(a, b):
    r = A({"a.md": "M-1 | q | " + a, "b.md": "M-1 | q | " + b}, 1e-6)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and not r["warnings"], r["warnings"]


@pytest.mark.parametrize("a,b", [("10 bar", "1000 kPa"), ("12 L/min", "0.0002 m^3/s"), ("25 mm", "0.025 m"), ("5 min", "300 s"),
                                 ("10 ft", "3.048 m"), ("14.5037738 psi", "100 kPa"), ("1 t", "1000 kg"), ("2 kN", "2000 N")])
def test_d04_unambiguous_conversions_are_preserved(a, b):
    r = A({"a.md": "X-1 | q | " + a, "b.md": "X-1 | q | " + b}, 1e-6)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and not r["warnings"]


def test_d04_report_lists_the_ambiguous_unit_with_alternatives():
    v = view(A({"a.md": "M-1 | q | 1 gallon", "b.md": "M-1 | q | 3.785 L"}), {"a.md": "Document A", "b.md": "Document B"})
    assert v.verdict == report.VERDICT_NONE
    item = [i for i in v.coverage if i.code == "AMBIGUOUS_UNIT"][0]
    assert "US_liquid_gallon" in item.summary and "imperial_gallon" in item.summary and item.excerpt == "M-1 | q | 1 gallon"


# =============================== D05 temperature semantics ===============================

def T(a, b, field="temperature", fa=None, fb=None, tol=1e-6):
    return A({"a.md": f"T-1 | {fa or field} | {a}", "b.md": f"T-1 | {fb or field} | {b}"}, tol)


@pytest.mark.parametrize("field", ["temperature difference", "delta T", "temperature rise", "temperature drop", "dT"])
def test_d05_interval_equivalence_is_preserved_when_the_property_says_difference(field):
    r = T("5 delta_degC", "5 K", field)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and len(r["matches"]) == 1 and not r["warnings"]
    assert r["matches"][0]["reference"]["quantity_kind"] == "interval" and r["matches"][0]["other"]["quantity_kind"] == "interval"


def test_d05_celsius_degrees_in_a_rise_are_an_interval_not_an_absolute_temperature():
    assert T("5 degC", "5 K", "temperature rise")["status"] == "NO_DIFFERENCES_FOUND"
    assert T("9 degF", "5 K", "temperature rise")["status"] == "NO_DIFFERENCES_FOUND"
    assert T("5 delta_degF", "2.7777777778 K", "delta T", tol=1e-9)["status"] == "NO_DIFFERENCES_FOUND"


def test_d05_interval_is_never_equated_with_an_absolute_temperature():
    r = T("5 delta_degC", "278.15 K", "temperature")
    assert r["status"] == "NO_COMPARABLE_ROWS" and r["matches"] == [] and r["findings"] == []
    assert [w["code"] for w in r["warnings"]] == ["TEMPERATURE_SEMANTICS"] and r["warnings"][0]["detail"] == "interval_vs_absolute"


def test_d05_unknown_semantics_is_reported_not_guessed():
    r = T("5 delta_degC", "5 K", "reading")
    assert r["matches"] == [] and r["findings"] == [] and r["warnings"][0]["code"] == "TEMPERATURE_SEMANTICS" and r["warnings"][0]["detail"] == "uncertain"


def test_d05_absolute_temperature_behaviour_is_unchanged():
    assert T("20 degC", "293.15 K")["status"] == "NO_DIFFERENCES_FOUND"
    assert T("20 degC", "20 K")["status"] == "DIFFERENCES"
    assert T("20 degC", "293.15 K", "reading")["status"] == "NO_DIFFERENCES_FOUND"          # degC is absolute, K unknown: compared as absolute
    assert T("68 degF", "20 degC")["status"] == "NO_DIFFERENCES_FOUND"


def test_d05_temperature_gradients_are_not_classified():
    r = T("5 K/m", "5 delta_degC/m", "gradient")
    assert r["status"] == "NO_DIFFERENCES_FOUND" and not r["warnings"]


def test_d05_report_explains_the_temperature_warning():
    v = view(T("5 delta_degC", "278.15 K"), {"a.md": "Document A", "b.md": "Document B"})
    item = [i for i in v.coverage if i.code == "TEMPERATURE_SEMANTICS"][0]
    assert "difference" in item.summary and "absolute" in item.summary and v.verdict == report.VERDICT_NONE


# =============================== D06 false unparsed-row warnings ===============================

@pytest.mark.parametrize("line,name", [
    ("| TAG | property | value (2026 rev) |", "h.md"),
    ("TAG | property | Rev 2 (2026)", "h.md"),
    ("Item,Qty,Order 66", "n.csv"),
    ("Notes, see, section 4", "n.csv"),
    ("TAG,property,value (2026)", "n.csv"),
    ("a | b | c3", "t.md"),
])
def test_d06_headings_and_prose_with_digits_are_not_reported(line, name):
    r = A({name: line, "z.md": ""})
    assert [w for w in r["warnings"] if w["code"] == "UNPARSED_ROW"] == []


def test_d06_markdown_table_header_before_separator_is_never_a_row():
    text = "| TAG | property | 2026 value |\n| --- | --- | --- |\n| P-1 | pressure | 10 bar |\n"
    r = A({"a.md": text, "b.md": "P-1 | pressure | 10 bar"})
    assert r["records"] == 2 and r["warnings"] == []


@pytest.mark.parametrize("row", ["P-1 | pressure | ~10 bar", "P-1 | pressure | approx. 10 bar", "P-1 | pressure | ≈10 bar", "P-1 | pressure | <= 10 bar",
                                 "P-1 | pressure | 10,5 bar", "P-1 | pressure | 1e3 Pa", "P-1 | pressure | 10", "P-1 | pressure | 10 bar | extra", "P-1 | pressure | 10 kPa(g)"])
def test_d06_real_malformed_measurement_rows_are_still_reported(row):
    r = A({"a.md": "title\n" + row, "b.md": "P-1 | pressure | 10 bar"})
    w = [x for x in r["warnings"] if x["code"] == "UNPARSED_ROW"]
    assert len(w) == 1 and w[0]["line"] == 2 and w[0]["excerpt"] == row and r["coverage"]["a.md"]["rows_unparsed"] == 1


def test_d06_malformed_csv_rows_are_still_reported():
    r = A({"a.csv": "TAG,property,value,unit\nP-1,pressure,10,5 bar\nP-2,length,3 m", "b.md": ""})
    assert [w["line"] for w in r["warnings"] if w["code"] == "UNPARSED_ROW"] == [2]
