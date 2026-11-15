"""Second round after the independent review of commit c721a6d1 (findings F1-F4). RED tests were written before the fix.

F1  A bare Markdown horizontal rule (`---`) is not a table separator. The inherited heading shortcut treated it as one and
    skipped the line above it, so a supported row (a real conflict) or a recognizable unsupported row vanished behind a green
    verdict. A heading is now skipped only when the next line is a pipe-delimited delimiter row with the same column count.
F2  Late-cell unit glyphs (ohm sign, Greek capital omega, degree-Celsius and degree-Fahrenheit signs) are RECOGNIZED and
    reported as UNPARSED_ROW. Recognition only: nothing is parsed, converted or compared.
F3  Input-limit wording and the API/upload distinction.
F4  The banned-claim check also runs on RENDERED user-visible text, not only on source text.

Counts of the reviewer's 70-case corpus are hand-picked adversarial-sample counts. They are neither recall nor precision."""
import json
import shutil
from pathlib import Path

import pint
import pytest
from fastapi.testclient import TestClient

from app import core, report
from app.api import app as api_app
from app.core import audit_documents
from app.ingest import MAX_FILE_BYTES, ingest_upload
from app.mcp_server import compare_engineering_rows

ROOT = Path(__file__).resolve().parent.parent
FLOW = "REF-90 | flow | 25 L/min"


def codes(r, code="UNPARSED_ROW"):
    return [w for w in r["warnings"] if w["code"] == code]


def names(ext):
    return f"a{ext}", f"b{ext}"


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


# ======================================================================================================== F1
# (id, rows in A, rows in B, expected verdict, expected complete, findings, comparisons, unparsed warnings)
SUPPORTED = ("supported-conflict", "P-102 | pressure | 10 bar", "P-102 | pressure | 16 bar")
QUALIFIER = ("unsupported-qualifier", "PUMP | pressure | max 10 bar", "PUMP | pressure | max 16 bar")
FOUR_CELL = ("unsupported-four-cell", "| P-102 | Feed pump | pressure | 10 bar |", "| P-102 | Feed pump | pressure | 16 bar |")
EXPECT = {
    "supported-conflict": ("CONFLICTS_FOUND", True, 1, 2, 0),
    "unsupported-qualifier": ("NO_CONFLICTS_PARTIAL_COVERAGE", False, 0, 1, 2),
    "unsupported-four-cell": ("NO_CONFLICTS_PARTIAL_COVERAGE", False, 0, 1, 2),
}
RULES = ["---", "   ---   ", "----------", "-----   ", "***", "___", "- - -"]


def rule_docs(case, rule):
    cid, a, b = case
    return f"{FLOW}\n{a}\n{rule}\n", f"{FLOW}\n{b}\n{rule}\n"


@pytest.mark.parametrize("rule", RULES, ids=[f"rule{i}" for i in range(len(RULES))])
@pytest.mark.parametrize("ext", [".md", ".txt", ".csv"])
@pytest.mark.parametrize("case", [SUPPORTED, QUALIFIER, FOUR_CELL], ids=lambda c: c[0])
def test_f1_horizontal_rule_never_hides_a_row(case, ext, rule):
    verdict, complete, findings, comparisons, unparsed = EXPECT[case[0]]
    a, b = rule_docs(case, rule)
    na, nb = names(ext)
    r = audit_documents({na: a, nb: b})
    assert r["verdict"] == verdict and r["coverage_complete"] is complete, r["verdict"]
    assert len(r["findings"]) == findings and r["comparisons"] == comparisons
    assert len(codes(r)) == unparsed
    if unparsed:                                                                  # recognized, never parsed or compared
        assert [(w["document"], w["line"]) for w in codes(r)] == [(na, 2), (nb, 2)]
        assert r["records"] == 2 and r["findings"] == []
    else:
        assert r["findings"][0]["relative_difference"] == pytest.approx(0.375)    # 10 bar vs 16 bar


@pytest.mark.parametrize("case", [SUPPORTED, QUALIFIER, FOUR_CELL], ids=lambda c: c[0])
def test_f1_api_and_mcp_agree_with_core(case):
    verdict, complete, findings, comparisons, unparsed = EXPECT[case[0]]
    a, b = rule_docs(case, "---")
    body = TestClient(api_app).post("/audit", json={"documents": {"a.md": a, "b.md": b}}).json()
    assert body["verdict"] == verdict and body["coverage_complete"] is complete and body["comparisons"] == comparisons
    mcp = compare_engineering_rows(a, b)
    assert mcp == audit_documents({"spec": a, "bom": b})
    assert mcp["verdict"] == verdict and mcp["coverage_complete"] is complete


@pytest.mark.parametrize("ext", [".md", ".txt"])
@pytest.mark.parametrize("case", [SUPPORTED, QUALIFIER, FOUR_CELL], ids=lambda c: c[0])
def test_f1_banner_json_and_markdown_exports(case, ext):
    verdict, complete, findings, comparisons, unparsed = EXPECT[case[0]]
    a, b = rule_docs(case, "---")
    na, nb = names(ext)
    at = ui_run(na, a, nb, b)
    audit = at.session_state["audit"]
    assert audit["view"].verdict == verdict and audit["view"].coverage_complete is complete
    assert not at.success                                                          # never the green banner
    assert (len(at.error) > 0) == (verdict == "CONFLICTS_FOUND")
    exported = json.loads(audit["json"])
    assert exported["verdict"] == verdict and exported["coverage_complete"] is complete
    md = audit["markdown"]
    assert f"Verdict: {verdict}" in md
    if findings:
        assert "10 bar" in md and "16 bar" in md
    else:
        assert "UNPARSED_ROW" in md and "incomplete" in md.lower()


# ---- genuine Markdown table headers are still suppressed, and the data below them is still read
HEADER_CASES = [
    ("| Item | Year | 2026 |", "| --- | --- | --- |"),
    ("| Item | Year | 2026 |", "|---|---|---|"),
    ("| Item | Year | 2026 |", "|:---|:---:|---:|"),
    ("| Item | Year | 2026 |", "|  ---  |  :---:  |  ---:  |"),
    ("| Item | Year | 2026 |", "| ------- | ------- | ------- |"),
    ("| Item | Year | 2026 |", "| --- | --- | --- |    "),
    ("Item | Year | 2026", "--- | --- | ---"),
    ("| Item | 2026 |", "| --- | --- |"),
    ("| Item | Year | Rev | 2026 |", "| --- | --- | --- | --- |"),
]


@pytest.mark.parametrize("heading,sep", HEADER_CASES, ids=[f"hdr{i}" for i in range(len(HEADER_CASES))])
@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
def test_f1_genuine_table_header_is_still_suppressed_and_data_is_read(heading, sep, ext):
    na, nb = names(ext)
    doc = f"{heading}\n{sep}\n| P-102 | pressure | 10 bar |\n"
    other = f"{heading}\n{sep}\n| P-102 | pressure | 16 bar |\n"
    r = audit_documents({na: doc, nb: other})
    assert codes(r) == [], heading                                                  # the heading is not a row
    assert r["records"] == 2 and r["comparisons"] == 1 and len(r["findings"]) == 1
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is True


def test_f1_existing_d06_behaviour_is_unchanged_for_a_measurement_above_a_real_separator():
    """Existing rule (D06): the line above a genuine pipe separator is a table heading, never a row. Locked, not changed."""
    a = "| P-102 | pressure | 10 bar |\n| --- | --- | --- |\n| P-103 | pressure | 10 bar |\n"
    b = "| P-102 | pressure | 16 bar |\n| --- | --- | --- |\n| P-103 | pressure | 16 bar |\n"
    r = audit_documents({"a.md": a, "b.md": b})
    assert r["comparisons"] == 1 and [f["tag"] for f in r["findings"]] == ["p-103"]


# ---- invalid, mismatched or short separators never suppress a measurement
NOT_SEPARATORS = [
    "| --- | --- |",                    # one column too few
    "| --- | --- | --- | --- |",        # one column too many
    "| -- | -- | -- |",                  # fewer than three dashes
    "| --- | abc | --- |",               # a non-separator cell
    "| --- | --- | --- | text |",
    "|---|---|",
    "| === | === | === |",
    "---",
    "   ---   ",
]


@pytest.mark.parametrize("sep", NOT_SEPARATORS, ids=[f"bad{i}" for i in range(len(NOT_SEPARATORS))])
def test_f1_a_non_separator_never_suppresses_the_row_above(sep):
    a = f"{FLOW}\n| P-102 | pressure | 10 bar |\n{sep}\n"
    b = f"{FLOW}\n| P-102 | pressure | 16 bar |\n{sep}\n"
    r = audit_documents({"a.md": a, "b.md": b})
    assert r["comparisons"] == 2 and len(r["findings"]) == 1, sep
    assert r["verdict"] == "CONFLICTS_FOUND"


def test_f1_mismatched_header_is_read_as_a_row_and_a_numeric_heading_is_reported():
    r = audit_documents({"a.md": f"{FLOW}\n| Item | Year | 2026 |\n| --- | --- |\n", "b.md": f"{FLOW}\n| Item | Year | 2026 |\n| --- | --- |\n"})
    assert [(w["document"], w["line"]) for w in codes(r)] == [("a.md", 2), ("b.md", 2)]


@pytest.mark.parametrize("pad,suppressed", [(2000, True), (2001, False)])
def test_f1_separator_length_boundary(pad, suppressed):
    sep = "| --- | --- | --- |"
    sep = sep + " " * (pad - len(sep))
    assert len(sep) == pad
    a = f"{FLOW}\n| P-102 | pressure | 10 bar |\n{sep}\n"
    b = f"{FLOW}\n| P-102 | pressure | 16 bar |\n{sep}\n"
    r = audit_documents({"a.md": a, "b.md": b})
    if suppressed:                                                                  # exactly at the limit: a genuine header
        assert r["comparisons"] == 1 and r["findings"] == [] and r["warnings"] == []
    else:                                                                           # over the limit: not a separator, and visible
        assert r["comparisons"] == 2 and len(r["findings"]) == 1
        assert [(w["document"], w["line"]) for w in codes(r, "LINE_TOO_LONG")] == [("a.md", 3), ("b.md", 3)]
        assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False


def test_f1_conflict_precedence_with_a_hidden_unsupported_row_and_a_rule():
    a = f"{FLOW}\nP-102 | pressure | 10 bar\n---\nPUMP | pressure | max 10 bar\n---\n"
    b = "REF-90 | flow | 25 L/min\nP-102 | pressure | 16 bar\n---\nPUMP | pressure | max 16 bar\n---\n"
    r = audit_documents({"a.md": a, "b.md": b})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False
    assert len(r["findings"]) == 1 and r["comparisons"] == 2
    assert [(w["document"], w["line"]) for w in codes(r)] == [("a.md", 4), ("b.md", 4)]


# ======================================================================================================== F2
OHM, OHM_SIGN, CELSIUS, FAHRENHEIT = "\u03a9", "\u2126", "\u2103", "\u2109"
GLYPH_ROWS = [
    ("ohm-4cell", "| RACK-3 | heater | resistance | {v} \u03a9 |"),
    ("ohm-sign-4cell", "| RACK-3 | heater | resistance | {v} \u2126 |"),
    ("celsius-4cell", "| HX-24 | shell | temperature | {v} \u2103 |"),
    ("fahrenheit-4cell", "| HX-24 | shell | temperature | {v} \u2109 |"),
    ("ohm-5cell", "| RACK-3 | Main heater | Acme | resistance | {v} \u03a9 |"),
    ("celsius-5cell", "| HX-24 | Shell side | Acme | temperature | {v} \u2103 |"),
    ("kilo-ohm", "| RACK-3 | heater | resistance | {v} k\u03a9 |"),
    ("milli-ohm-sign", "| RACK-3 | heater | resistance | {v} m\u2126 |"),
    ("no-space", "| RACK-3 | heater | resistance | {v}\u03a9 |"),
    ("nbsp", "| RACK-3 | heater | resistance | {v}\u00a0\u03a9 |"),
    ("thin-space", "| HX-24 | shell | temperature | {v}\u2009\u2103 |"),
    ("narrow-nbsp", "| HX-24 | shell | temperature | {v}\u202f\u2109 |"),
    ("qualifier", "| RACK-3 | heater | resistance | max {v} \u03a9 |"),
    ("unit-column-ohm", "| RACK-3 | heater | \u03a9 | {v} |"),
    ("unit-column-ohm-sign", "| RACK-3 | heater | \u2126 | {v} |"),
    ("unit-column-celsius", "| HX-24 | shell | \u2103 | {v} |"),
    ("unit-column-fahrenheit", "| HX-24 | shell | \u2109 | {v} |"),
    ("comma-txt", "RACK-3,resistance,{v} \u03a9"),
    ("comma-txt-celsius", "HX-24,temperature,{v} \u2103"),
]


@pytest.mark.parametrize("case", GLYPH_ROWS, ids=[c[0] for c in GLYPH_ROWS])
@pytest.mark.parametrize("ext", [".md", ".txt"])
def test_f2_unit_glyph_rows_are_recognized_not_parsed(case, ext):
    cid, template = case
    na, nb = names(ext)
    r = audit_documents({na: f"{FLOW}\n{template.format(v=10)}\n", nb: f"{FLOW}\n{template.format(v=16)}\n"})
    assert len(codes(r)) == 2, (cid, r["warnings"])
    assert [(w["document"], w["line"]) for w in codes(r)] == [(na, 2), (nb, 2)]
    assert r["coverage_complete"] is False and r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE"
    assert r["records"] == 2 and r["comparisons"] == 1 and r["findings"] == []     # never parsed, converted or compared


def test_f2_the_unit_glyph_reaches_the_ui_and_exports_as_incomplete():
    a = f"{FLOW}\n| RACK-3 | heater | resistance | 10 \u03a9 |\n"
    b = f"{FLOW}\n| RACK-3 | heater | resistance | 16 \u03a9 |\n"
    at = ui_run("a.md", a, "b.md", b)
    audit = at.session_state["audit"]
    assert not at.success and audit["view"].verdict == report.VERDICT_PARTIAL
    assert json.loads(audit["json"])["coverage_complete"] is False and "UNPARSED_ROW" in audit["markdown"]


@pytest.mark.parametrize("row", [
    "| Symbol | \u03a9 | ohm |",                      # glyph as a label
    "The unit of resistance is the ohm (\u03a9).",     # prose
    "| Item | \u03a9-ring | Acme | 4 |",              # a part name that contains the glyph, then a quantity
    "| Temperature | \u2103 | Celsius |",             # a legend row
    "Temperatures in \u2103, resistances in \u03a9, see annex",
    "| Symbol | Meaning |",
])
@pytest.mark.parametrize("ext", ["", ".md", ".txt", ".csv"])
def test_f2_glyph_negatives_stay_quiet(row, ext):
    na, nb = names(ext)
    r = audit_documents({na: f"{FLOW}\n{row}\n", nb: f"{FLOW}\n{row}\n"})
    assert codes(r) == [], row
    assert r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is True


@pytest.mark.parametrize("name,row", [
    ("angstrom sign", "| FILM-2 | coating | thickness | {v} \u212b |"),
    ("per-mille sign", "| BATCH-2 | assay | purity | {v} \u2030 |"),
    ("full-width comma delimiter", "PUMP\uff0cpressure\uff0c{v} bar"),
])
def test_f2_known_undetected_glyphs_and_delimiters_are_documented_limits(name, row):
    """KNOWN MISSES outside the four requested glyphs (owner scope). Locked so the limit is explicit and cannot change silently."""
    r = audit_documents({"a.txt": f"{FLOW}\n{row.format(v=10)}\n", "b.txt": f"{FLOW}\n{row.format(v=16)}\n"})
    assert codes(r) == [] and r["coverage_complete"] is True, name


def test_f2_the_pint_version_sensitive_nm_detector_path_is_unchanged():
    """`Nm` is a unit to Pint 0.25.x (python 3.11) and not to 0.26.x (python 3.12). Round two does not change that path."""
    pint_major_minor = tuple(int(x) for x in pint.__version__.split(".")[:2])
    row = "| AGITATOR-3 | shaft torque | Nm | {v} |"
    r = audit_documents({"a.md": f"{FLOW}\n{row.format(v=10)}\n", "b.md": f"{FLOW}\n{row.format(v=16)}\n"})
    assert (len(codes(r)) == 2) == (pint_major_minor < (0, 26)), (pint.__version__, r["warnings"])


@pytest.mark.parametrize("row", [
    "| BV-44 | Isolation valve | pc | 4 |",
    "| BV-44 | Isolation valve | pcs | 4 |",
    "| DOC-44 | Approved | A | 2026 |",
])
def test_documented_false_positives_from_unit_named_columns_are_unchanged(row):
    """KNOWN, DOCUMENTED CONSERVATIVE WARNINGS (Pint accepts pc, pcs and A as units). Locked so round two cannot widen them silently."""
    r = audit_documents({"a.md": f"{FLOW}\n{row}\n", "b.md": f"{FLOW}\n{row}\n"})
    assert len(codes(r)) == 2 and r["coverage_complete"] is False


def test_documented_false_positive_list_row_is_unchanged():
    r = audit_documents({"a.txt": f"{FLOW}\nSpares,count,4 pieces\n", "b.txt": f"{FLOW}\nSpares,count,4 pieces\n"})
    assert len(codes(r)) == 2


# ======================================================================================================== F3
def test_f3_the_json_api_is_not_the_upload_path():
    """POST /audit takes JSON text and stops at the 200,000-character / 5,000-line core guard (HTTP 422).
    It never reaches uploaded-file ingestion and never returns FILE_TOO_LARGE."""
    resp = TestClient(api_app).post("/audit", json={"documents": {"large.txt": "a" * 2_000_001, "b.txt": "x"}})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "maximum 200000 characters" in detail and "FILE_TOO_LARGE" not in detail
    upload = ingest_upload("large.txt", b"a" * 2_000_001)                           # the upload path has its own code
    assert [i["code"] for i in upload.issues] == ["FILE_TOO_LARGE"]
    assert MAX_FILE_BYTES == 2_000_000


def test_f3_wording_makes_no_universal_browser_claim():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cfg = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    spec = (ROOT / ".kiro" / "specs" / "p1-coverage-and-ingress" / "requirements.md").read_text(encoding="utf-8")
    for name, text in (("README", readme), ("config", cfg), ("spec", spec)):
        low = " ".join(text.lower().split())
        assert "the browser refuses a file above 2,000,000" not in low, name
        assert "browser enforces a decimal 2,000,000" not in low, name
    assert "brave" in readme.lower() and "macos" in readme.lower()                  # the observation is scoped to what was run
    assert "windows" in readme.lower() and "2,097,152" in readme                    # the platform-dependent frontend base
    assert "422" in readme and "FILE_TOO_LARGE" in readme                           # the API/upload distinction
    assert "not verified" in readme.lower() or "not tested" in readme.lower()      # no real Windows browser run


# ======================================================================================================== F4
BANNED = ("all invalid measurement", "cannot bluff", "verified physical equivalence", "compliance certification",
          "every unsupported row", "all unsupported rows")


def rendered_text(at):
    """All user-visible text that Streamlit rendered for the current run."""
    parts = []
    for kind in ("title", "header", "subheader", "markdown", "caption", "text", "info", "warning", "error", "success", "code"):
        parts += [str(el.value) for el in getattr(at, kind, [])]
    return "\n".join(parts)


def violations(text):
    flat = " ".join(text.lower().split())
    return [b for b in BANNED if b in flat]


def test_f4_the_rendered_page_contains_no_banned_claim_and_has_the_contract():
    at = _app()
    at.run()
    text = rendered_text(at)
    assert violations(text) == []
    assert "Recognizable unsupported measurement-like rows are listed" in text and "may be missed" in text


def test_f4_a_split_literal_is_invisible_to_the_source_check_but_caught_when_rendered(tmp_path):
    shutil.copytree(ROOT / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    ui = tmp_path / "app" / "ui.py"
    source = ui.read_text(encoding="utf-8")
    # Adapted for the O1 two-view workbench: the title call is now st.title("SpecForge", anchor=False).
    title = 'st.title("SpecForge", anchor=False)'
    mutated = source.replace(title, title + '\nst.markdown("All invalid " "measurement lines are detected.")', 1)
    assert mutated != source
    ui.write_text(mutated, encoding="utf-8")
    assert violations(mutated) == []                                                # the old source-level check cannot see it
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ui), default_timeout=60)
    at.run()
    assert not at.exception
    assert violations(rendered_text(at)) == ["all invalid measurement"]             # the rendered check does


def test_f4_source_level_checks_on_readme_and_mcp_remain():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (readme, compare_engineering_rows.__doc__):
        assert violations(text) == []
    assert "does not enable CSV" in readme


# ======================================================================================================== spec wording
def test_spec_wording_is_corrected():
    raw = (ROOT / ".kiro" / "specs" / "p1-coverage-and-ingress" / "requirements.md").read_text(encoding="utf-8")
    spec = " ".join(raw.replace("`", "").split())                                    # markdown backticks do not matter here
    assert "coverage_complete SHALL be false WHEN any coverage gap exists" in spec
    assert "201" in spec and "ellipsis" in spec
    assert "44/45" in spec and "10/25" in spec and "not recall or precision" in spec.lower()
    assert "1.916" in spec and "not linear" in spec.lower()
    assert "LINE_TOO_LONG" in spec and "rows_too_long" in spec
