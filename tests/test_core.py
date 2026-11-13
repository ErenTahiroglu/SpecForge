import math
import pytest
from hypothesis import given, settings, strategies as st
from app.core import audit_documents


def test_mismatch_has_two_evidence_sources():
    result = audit_documents({"spec.md": "P-101 | pressure | 10 bar", "bom.csv": "P-101 | pressure | 900 kPa"})
    assert result["status"] == "DIFFERENCES"
    assert result["comparisons"] == 1
    assert result["findings"][0]["reference"]["document"] == "spec.md"
    assert result["findings"][0]["other"]["line"] == 1
    assert math.isclose(result["findings"][0]["relative_difference"], 0.1)


def test_compatible_rows_and_other_tags():
    report = audit_documents({"a": "P-101 | flow | 12 L/min\nP-102 | pressure | 10 bar", "b": "P-101 | flow | 0.0002 m^3/s"})
    assert report["status"] == "NO_DIFFERENCES_FOUND"
    assert not report["findings"]


def test_unknown_unit_reports_warning_not_a_false_pass():
    report = audit_documents({"a": "P-101 | pressure | 12 lollipops", "b": "P-101 | pressure | 10 bar"})
    assert report["status"] == "NO_COMPARABLE_ROWS"
    assert report["warnings"][0]["code"] == "UNSUPPORTED_UNIT"


def test_dimension_mismatch_warning():
    report = audit_documents({"a": "P-101 | pressure | 2 bar", "b": "P-101 | pressure | 3 mm"})
    assert report["warnings"][0]["code"] == "DIMENSION_MISMATCH"
    assert report["comparisons"] == 0


def test_bounded_documents():
    with pytest.raises(ValueError):
        audit_documents({})


@given(st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False))
@settings(max_examples=80, deadline=None)
def test_property_bar_kpa_equivalence(value):
    doc1 = f"P-101 | pressure | {value:.8f} bar"
    doc2 = f"P-101 | pressure | {value*100:.8f} kPa"
    report = audit_documents({"a": doc1, "b": doc2})
    assert report["status"] == "NO_DIFFERENCES_FOUND"
    assert report["comparisons"] == 1
