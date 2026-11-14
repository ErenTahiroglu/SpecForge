"""Third round (findings N1-N5 of the second independent review of commit 9f84f0c5). Written RED first.

Standing rule for this file: a row the FIRST commit (c721a6d1) warned about must still be warned, and a control the FIRST commit kept
quiet must stay quiet, unless a test below says otherwise and gives the reason. The tests encode FIRST's observed behaviour as static
fixtures; they never import another commit.

N1  Whole-cell Unicode NFKC (round two) turned `m²` into `m2`, so a bare value after a superscript unit column went from a warning to a
    complete, green result. Matching now keeps the RAW cell first; only three glyphs get an additive, recognition-only mapping.
N2  Escaped pipes (`\\|`) in a genuine table heading were counted as columns. Heading structure now counts unescaped pipes only.
N3  NFKC also turned circled revision labels (②, ③) into plain digits and added warnings to revision ledgers. No label is converted now.
N4  Test comments no longer make a universal browser claim.   N5  Spec R4 says the excerpt comes from the stripped line.

Deliberate, recorded loss: round two recognized compatibility characters such as the square-millimetre and square-kilogram signs only as a
side effect of NFKC. Those are not recognized now (the first commit did not recognize them either)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core, report
from app.api import app as api_app
from app.core import audit_documents
from app.mcp_server import compare_engineering_rows

ROOT = Path(__file__).resolve().parent.parent
ANCHOR = "ANCHOR-73 | flow | 31 L/min"


def codes(r, code="UNPARSED_ROW"):
    return [w for w in r["warnings"] if w["code"] == code]


def two(row_a, row_b=None, ext=".md", tail=""):
    row_b = row_a if row_b is None else row_b
    return {f"a{ext}": f"{ANCHOR}\n{row_a}\n{tail}", f"b{ext}": f"{ANCHOR}\n{row_b}\n{tail}"}


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60)


def ui_run(name_a, text_a, name_b, text_b):
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload(name_a, text_a.encode(), "text/plain")
    at.get("file_uploader")[1].upload(name_b, text_b.encode(), "text/plain")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    return at


# ================================================================================================ N1
SUPERSCRIPT_UNITS = ["m\u00b2", "m\u00b3", "m\u00b3/s", "mm\u00b2", "cm\u00b2", "kg/m\u00b3", "W/m\u00b2", "\u00b5m\u00b2"]


def n1_docs(unit, ext=".md"):
    a = f"{ANCHOR}\n| HX-83 | heat transfer area | {unit} | 23 |\n"
    b = f"{ANCHOR}\n| HX-83 | heat transfer area | {unit} | 47 |\n"
    return a, b, {f"a{ext}": a, f"b{ext}": b}


@pytest.mark.parametrize("unit", SUPERSCRIPT_UNITS)
@pytest.mark.parametrize("ext", [".md", ".txt", ".csv"])
def test_n1_a_value_after_a_superscript_unit_column_is_warned_not_green(unit, ext):
    a, b, docs = n1_docs(unit, ext)
    r = audit_documents(docs)
    assert [(w["document"], w["line"], w["excerpt"]) for w in codes(r)] == [
        (f"a{ext}", 2, f"| HX-83 | heat transfer area | {unit} | 23 |"), (f"b{ext}", 2, f"| HX-83 | heat transfer area | {unit} | 47 |")], unit
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    assert r["records"] == 2 and r["comparisons"] == 1 and r["findings"] == []        # the unsupported row is never compared


@pytest.mark.parametrize("unit", SUPERSCRIPT_UNITS)
def test_n1_api_and_mcp_agree(unit):
    a, b, docs = n1_docs(unit)
    body = TestClient(api_app).post("/audit", json={"documents": docs}).json()
    assert body["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and body["coverage_complete"] is False
    assert [w["code"] for w in body["warnings"]] == ["UNPARSED_ROW", "UNPARSED_ROW"]
    mcp = compare_engineering_rows(a, b)
    assert mcp == audit_documents({"spec": a, "bom": b})
    assert mcp["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and mcp["coverage_complete"] is False


@pytest.mark.parametrize("unit", ["m\u00b2", "kg/m\u00b3", "\u00b5m\u00b2"])
def test_n1_banner_json_and_markdown_exports(unit):
    a, b, _ = n1_docs(unit)
    at = ui_run("a.md", a, "b.md", b)
    audit = at.session_state["audit"]
    assert not at.success and audit["view"].verdict == report.VERDICT_PARTIAL and audit["view"].coverage_complete is False
    exported = json.loads(audit["json"])
    assert exported["verdict"] == report.VERDICT_PARTIAL and exported["coverage_complete"] is False
    assert "UNPARSED_ROW" in audit["markdown"] and unit in audit["markdown"]


# static record of what the FIRST commit did for ordinary unit columns: every one of them warned, and must still warn
FIRST_WARNED_UNIT_COLUMNS = ["m^2", "m**2", "bar", "kPa", "degC", "L/min", "hp", "barg", "psig", "m", "\u00b0C", "\u00b5m", "ton", "pc", "pcs", "A"]


@pytest.mark.parametrize("unit", FIRST_WARNED_UNIT_COLUMNS)
def test_n1_first_commit_unit_column_detections_survive_unchanged(unit):
    r = audit_documents(two(f"| HX-83 | rating | {unit} | 23 |", f"| HX-83 | rating | {unit} | 47 |"))
    assert len(codes(r)) == 2 and r["coverage_complete"] is False, unit


def test_n1_the_raw_cell_decides_unit_columns_and_only_three_glyphs_are_mapped():
    for cell in ["m\u00b2", "m\u00b3", "kg/m\u00b3", "bar", "\u03a9", "\u2126", "\u2103", "\u2109", "barg"]:
        assert core._unit_like(cell) is True, cell
    for cell in ["\u2461", "\u2462", "\u2163", "\u216b", "\u338f", "\u339c", "\uff21", "Acme", "Status", "Open", "x" * 40]:
        assert core._unit_like(cell) is False, cell


# ================================================================================================ N3
@pytest.mark.parametrize("row", [
    "| DOC-82 | revision | \u2461 |", "| DR-62 | page | \u247b |", "| DR-62 | release | \u2163 |", "| DR-62 | volume | \u216b |",
    "| PK-62 | gasket | \u338f | 8 |", "| PK-62 | locator | \u339c | 8 |", "| PK-62 | terminal | \uff21 | 8 |",
    "| BV-44 | Isolation valve | Acme | 4 |", "| PK-62 | packing set | sets | 8 |", "| DR-62 | sheet number | Open | 12 |",
])
@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
def test_n3_controls_that_the_first_commit_kept_quiet_stay_quiet(row, ext):
    r = audit_documents(two(row, ext=ext))
    assert codes(r) == [], (ext, row)
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


@pytest.mark.parametrize("row,ext", [("DR-62,revision,\u2462", ".txt"), ("DR-62,revision,\u2462", ".csv"), ("DR-62,revision,\u2462", ".md"),
                                      ("DR-62,appendix,\u2163", ".txt")])
def test_n3_circled_and_roman_labels_in_comma_rows_stay_quiet(row, ext):
    r = audit_documents(two(row, ext=ext))
    assert codes(r) == [] and r["coverage_complete"] is True


def test_n3_documented_existing_conservative_warnings_are_unchanged():
    """`Rev | Date | 2026-11-13` (owner decision), `pc`/`pcs`/`A` unit-named columns and a full-width digit were already warned by the first commit."""
    for row in ["Rev | Date | 2026-11-13", "| BV-44 | Isolation valve | pc | 4 |", "| DOC-44 | Approved | A | 2026 |", "| DR-62 | sheet number | \uff17 |"]:
        assert len(codes(audit_documents(two(row)))) == 2, row


# ================================================================================================ N2
ESCAPED_HEADINGS = [
    r"| Tag | Date | 2027 \| issue |",
    r"| Tag | 2027 \| schedule | Rev |",
    r"| Tag | Unit | 23 \| 47 |",
    r"| Tag | Date | 2027 \|",                 # an escaped pipe at the end is not a closing pipe
    r"Tag | Date | 2027 \| issue",              # no outer pipes
    r"| Tag | Date | 2027 \\\| issue |",        # escaped backslash followed by an escaped pipe: still one cell
]
DELIM3 = "| --- | --- | --- |"
DATA = "| VV-37 | mass | 31 kg |"


@pytest.mark.parametrize("heading", ESCAPED_HEADINGS, ids=[f"esc{i}" for i in range(len(ESCAPED_HEADINGS))])
@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_n2_an_escaped_pipe_does_not_add_a_column(heading, ext, eol):
    text = eol.join([ANCHOR, heading, DELIM3, DATA, ""])
    r = audit_documents({f"a{ext}": text, f"b{ext}": text})
    assert r["warnings"] == [], heading                                             # heading suppressed, no new warning
    assert r["records"] == 4 and r["comparisons"] == 2                              # the data row below the heading is still read
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


def test_n2_an_unescaped_pipe_after_an_escaped_backslash_is_a_real_column():
    four = r"| Tag | Date | 2027 \\| issue |"                                       # `\\|` = escaped backslash, then a structural pipe: 4 columns
    ok = "\n".join([ANCHOR, four, "| --- | --- | --- | --- |", "| VV-37 | mass | 31 kg |", ""])
    assert audit_documents({"a.md": ok, "b.md": ok})["warnings"] == []             # 4 = 4: genuine heading
    bad = "\n".join([ANCHOR, four, DELIM3, "| VV-37 | mass | 31 kg |", ""])
    r = audit_documents({"a.md": bad, "b.md": bad})
    assert [(w["document"], w["line"]) for w in codes(r)] == [("a.md", 2), ("b.md", 2)]   # 4 != 3: not a heading, read and reported


@pytest.mark.parametrize("delimiter", ["| --- | --- |", "| --- | --- | --- | --- |"])
def test_n2_a_real_column_mismatch_still_prevents_suppression(delimiter):
    heading = r"| Tag | Date | 2027 \| issue |"                                     # three columns
    text = "\n".join([ANCHOR, heading, delimiter, DATA, ""])
    r = audit_documents({"a.md": text, "b.md": text})
    assert [(w["document"], w["line"]) for w in codes(r)] == [("a.md", 2), ("b.md", 2)]


@pytest.mark.parametrize("pad,suppressed", [(2000, True), (2001, False)])
def test_n2_delimiter_length_boundary_with_an_escaped_pipe_heading(pad, suppressed):
    delimiter = DELIM3 + " " * (pad - len(DELIM3))
    assert len(delimiter) == pad
    text = "\n".join([ANCHOR, r"| Tag | Date | 2027 \| issue |", delimiter, DATA, ""])
    r = audit_documents({"a.md": text, "b.md": text})
    if suppressed:
        assert r["warnings"] == [] and r["comparisons"] == 2
    else:
        assert "LINE_TOO_LONG" in {w["code"] for w in r["warnings"]} and r["coverage_complete"] is False


def test_n2_the_f1_fix_is_preserved_next_to_escaped_pipes():
    """A bare horizontal rule still never hides the row above it, with or without escaped pipes in that row."""
    for row_a, row_b, verdict in [
        ("P-102 | pressure | 10 bar", "P-102 | pressure | 16 bar", "CONFLICTS_FOUND"),
        # four cells (a trailing note) is NOT a supported row: it is recognized, warned and never compared
        (r"P-102 | pressure | 10 bar | note \| x", r"P-102 | pressure | 16 bar | note \| x", "NO_CONFLICTS_PARTIAL_COVERAGE"),
        ("PUMP | pressure | max 10 bar", "PUMP | pressure | max 16 bar", "NO_CONFLICTS_PARTIAL_COVERAGE"),
        (r"| PUMP | note \| x | pressure | 10 bar |", r"| PUMP | note \| x | pressure | 16 bar |", "NO_CONFLICTS_PARTIAL_COVERAGE"),
    ]:
        r = audit_documents(two(row_a, row_b, tail="---"))
        assert r["verdict"] == verdict, row_a


@pytest.mark.parametrize("sep", ["| --- | --- | --- |", "|---|---|---|", "|:---|:---:|---:|", "| --- | --- | --- |    "])
def test_n2_genuine_headings_with_matched_delimiters_are_still_suppressed(sep):
    text = "\n".join([ANCHOR, "| Item | Year | 2026 |", sep, "| P-102 | pressure | 10 bar |", ""])
    other = "\n".join([ANCHOR, "| Item | Year | 2026 |", sep, "| P-102 | pressure | 16 bar |", ""])
    r = audit_documents({"a.md": text, "b.md": other})
    assert codes(r) == [] and r["verdict"] == "CONFLICTS_FOUND" and len(r["findings"]) == 1


def test_n2_the_escaped_heading_reaches_the_ui_without_a_warning():
    text = "\n".join([ANCHOR, r"| Tag | Date | 2027 \| issue |", DELIM3, DATA, ""])
    at = ui_run("a.md", text, "b.md", text)
    audit = at.session_state["audit"]
    assert audit["view"].verdict == report.VERDICT_MATCH and audit["view"].coverage_complete is True
    assert len(at.success) >= 1


# ================================================================================================ glyph-only recognition contract
@pytest.mark.parametrize("cell", ["10 \u2103", "10 \u2109", "10 \u2126", "10 \u03a9", "10 k\u03a9", "max 10 \u2103"])
def test_glyph_cells_are_recognized(cell):
    assert core._has_measurement_cell(["HX-24", "shell", cell]) is True


@pytest.mark.parametrize("cell", ["\u2461", "\u2462", "\u2163", "10 \u339c", "10 \u338f", "10 \u212b", "10 \u2030", "\uff2d\uff21\uff38 10 bar"])
def test_everything_else_is_not_normalized_into_a_match(cell):
    """No compatibility folding: circled and roman labels stay labels, and the square-millimetre and square-kilogram signs
    (recognized in round two only as a side effect of NFKC) are a deliberate, recorded loss."""
    assert core._has_measurement_cell(["HX-24", "shell", cell]) is False, cell


def test_the_reported_excerpt_and_record_counts_are_unchanged_for_glyph_rows():
    r = audit_documents(two("| RACK-3 | heater | resistance | 10 \u2126 |", "| RACK-3 | heater | resistance | 16 \u2126 |"))
    assert [w["excerpt"] for w in codes(r)] == ["| RACK-3 | heater | resistance | 10 \u2126 |", "| RACK-3 | heater | resistance | 16 \u2126 |"]
    assert r["records"] == 2 and r["comparisons"] == 1 and r["findings"] == []


# ================================================================================================ N4
def test_n4_the_ingress_test_comments_make_no_universal_claim():
    text = (ROOT / "tests" / "test_p1_ingress_guard.py").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "In a browser, Streamlit refuses anything above 2,000,000 bytes" not in flat
    assert "reached only by direct uploads between 2,000,001 and 2,097,152 bytes and by the API" not in flat
    assert "browser refuses files above 2,000,000 bytes" not in flat
    for needle in ("Brave", "macOS", "Win32", "2,097,152", "422", "not verified"):
        assert needle in text, needle
    assert "FILE_TOO_LARGE" in text and "200,000" in text


def test_n4_no_repository_text_claims_a_browser_limit_for_every_browser():
    for rel in ("README.md", ".streamlit/config.toml", ".kiro/specs/p1-coverage-and-ingress/requirements.md", "tests/test_p1_ingress_guard.py"):
        flat = " ".join((ROOT / rel).read_text(encoding="utf-8").lower().split())
        for banned in ("the browser refuses a file above 2,000,000", "browser enforces a decimal 2,000,000", "refuses anything above 2,000,000 bytes"):
            assert banned not in flat, (rel, banned)


# ================================================================================================ N5
def test_n5_the_excerpt_is_the_first_200_characters_of_the_stripped_line():
    raw = " " * 30 + "HX-83 | rating | " + "x" * 2000
    r = audit_documents({"a.md": raw + "\n"})
    (w,) = codes(r, "LINE_TOO_LONG")
    assert w["length"] == len(raw) == 2047                                          # the raw character count
    assert w["excerpt"] == raw.strip()[:200] + "\u2026" and len(w["excerpt"]) == 201
    assert w["excerpt"].startswith("HX-83")


def test_n5_the_spec_says_so():
    spec = " ".join((ROOT / ".kiro" / "specs" / "p1-coverage-and-ingress" / "requirements.md").read_text(encoding="utf-8").replace("`", "").split())
    assert "first 200 characters of raw.strip()" in spec and "at most 201" in spec
    assert "length is the raw line's character count" in spec
    assert "not a claim that every line is linear time" in spec.lower() or "not linear time for every" in spec.lower()
