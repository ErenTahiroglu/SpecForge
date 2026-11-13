"""R6-A (issue #13): the synthetic pump-station case gives one fixed, independently checked outcome.

Expected values are computed here with plain arithmetic, NOT with Pint, so the unit conversions are verified independently.
The case is synthetic; no customer, incident or field measurement is represented."""
import json
from pathlib import Path

import pytest

from app.core import audit_documents
from app.ingest import ingest_upload
from app.report import build_view, render_markdown

CASE = Path(__file__).resolve().parent.parent / "examples" / "pump_station"
SPEC, BOM = CASE / "spec.md", CASE / "purchasing.csv"


@pytest.fixture(scope="module")
def audit():
    a = ingest_upload(SPEC.name, SPEC.read_bytes())
    b = ingest_upload(BOM.name, BOM.read_bytes())
    assert a.issues == [] and b.issues == []
    return audit_documents({**a.documents, **b.documents})


def test_files_are_labelled_synthetic():
    for f in (SPEC, BOM):
        assert "synthetic" in f.read_text(encoding="utf-8").lower()


# ---- independent oracle (no Pint)
BAR, KPA = 1e5, 1e3
L_PER_MIN = 1e-3 / 60
MM = 1e-3


def test_independent_conversions_and_relative_differences(audit):
    ref_p, oth_p = 10 * BAR, 900 * KPA
    assert (ref_p, oth_p) == (1_000_000, 900_000)
    f = audit["findings"][0]
    assert f["reference"]["si_value"] == pytest.approx(ref_p) and f["other"]["si_value"] == pytest.approx(oth_p)
    assert f["relative_difference"] == pytest.approx(abs(ref_p - oth_p) / max(ref_p, oth_p)) == pytest.approx(0.1)
    by = {m["field"]: m for m in audit["matches"]}
    assert by["flow"]["reference"]["si_value"] == pytest.approx(12 * L_PER_MIN) == pytest.approx(0.0002)
    assert by["flow"]["other"]["si_value"] == pytest.approx(0.0002)
    assert by["inner_diameter"]["reference"]["si_value"] == pytest.approx(25 * MM) == pytest.approx(0.025)
    assert by["inner_diameter"]["other"]["si_value"] == pytest.approx(0.025)
    assert all(m["relative_difference"] < 1e-9 for m in audit["matches"])


# ---- fixed outcome
def test_outcome_is_conflict_with_incomplete_coverage(audit):
    assert audit["status"] == "DIFFERENCES"
    assert audit["verdict"] == "CONFLICTS_FOUND" and audit["coverage_complete"] is False
    assert (audit["records"], audit["comparisons"]) == (8, 3)
    assert [(f["tag"], f["field"]) for f in audit["findings"]] == [("p-101", "pressure")]
    assert sorted((m["tag"], m["field"]) for m in audit["matches"]) == [("p-101", "flow"), ("v-201", "inner_diameter")]


def test_conflict_cites_both_documents_lines_and_verbatim_rows(audit):
    f = audit["findings"][0]
    assert (f["reference"]["document"], f["reference"]["line"], f["reference"]["excerpt"]) == ("spec.md", 5, "P-101 | pressure | 10 bar")
    assert (f["other"]["document"], f["other"]["line"], f["other"]["excerpt"]) == ("purchasing.csv", 3, "P-101,pressure,900 kPa")
    assert SPEC.read_text().splitlines()[4] == f["reference"]["excerpt"]
    assert BOM.read_text().splitlines()[2] == f["other"]["excerpt"]


def test_ambiguous_and_quoted_rows_are_not_compared_and_reduce_coverage(audit):
    codes = sorted((w["code"], w["document"], w["line"]) for w in audit["warnings"])
    assert codes == [("AMBIGUOUS_UNIT", "spec.md", 7), ("UNPARSED_ROW", "purchasing.csv", 7)]
    assert sorted((u["tag"], u["field"]) for u in audit["unmatched"]) == [("P-101", "motor_power"), ("T-301", "design_temperature")]
    compared = {(x["tag"], x["field"]) for x in audit["findings"] + audit["matches"]}
    assert ("p-101", "motor_power") not in compared and ("t-301", "design_temperature") not in compared
    assert audit["coverage"]["purchasing.csv"]["rows_unparsed"] == 1 and audit["coverage"]["spec.md"]["rows_ambiguous_unit"] == 1


def test_report_is_deterministic_and_keeps_the_not_compared_rows(audit):
    labels = {"spec.md": "Document A", "purchasing.csv": "Document B"}
    v1, v2 = build_view(audit, labels), build_view(audit, labels)
    assert render_markdown(v1) == render_markdown(v2)
    assert len(v1.coverage) == 4 and v1.metrics["not_compared"] == 4
    md = render_markdown(v1)
    for needle in ("P-101 | pressure | 10 bar", "P-101,pressure,900 kPa", "5 hp", "95 degC"):
        assert needle in md
