"""R6-A (issue #14): evidence-first presentation. The authoritative result decides what the screen, the Markdown report and the
JSON export say. A success presentation is never shown while coverage is incomplete or an input issue exists, even when
conflicts are present (the old screen then said nothing about coverage in its headline)."""
import json
from pathlib import Path

import pytest

from app import report
from app.core import audit_documents, audit_verdict

ROOT = Path(__file__).resolve().parent.parent
CASE = ROOT / "examples" / "pump_station"
PUMP_LABEL = "Pump station"


def pump_result():
    return audit_documents({"spec.md": (CASE / "spec.md").read_text(), "purchasing.csv": (CASE / "purchasing.csv").read_text()})


COMPLETE = {"a.md": "P-1 | pressure | 10 bar", "b.md": "P-1 | pressure | 1000 kPa"}
CONFLICT_COMPLETE = {"a.md": "P-1 | pressure | 10 bar", "b.md": "P-1 | pressure | 12 bar"}
PARTIAL = {"a.md": "P-1 | pressure | 10 bar\nP-2 | flow | 5 L/min", "b.md": "P-1 | pressure | 1000 kPa"}
NOTHING = {"a.md": "nothing", "b.md": "nothing"}


# ---------------------------------------------------------------- report model
@pytest.mark.parametrize("docs,issues,verdict,complete", [
    (COMPLETE, [], report.VERDICT_MATCH, True),
    (COMPLETE, [{"code": "X", "message": "file could not be read"}], report.VERDICT_PARTIAL, False),
    (CONFLICT_COMPLETE, [], report.VERDICT_CONFLICTS, True),
    (CONFLICT_COMPLETE, [{"code": "X", "message": "m"}], report.VERDICT_CONFLICTS, False),
    (PARTIAL, [], report.VERDICT_PARTIAL, False),
    (NOTHING, [], report.VERDICT_NONE, False),
])
def test_view_carries_the_authoritative_coverage_state(docs, issues, verdict, complete):
    r = audit_documents(docs)
    v = report.build_view(r, input_issues=issues)
    assert v.verdict == verdict and v.coverage_complete is complete
    assert (v.verdict, v.coverage_complete) == audit_verdict(r, extra_gaps=len(issues))


def test_a_conflict_with_incomplete_coverage_says_so_in_its_detail():
    v = report.build_view(pump_result())
    assert v.verdict == report.VERDICT_CONFLICTS and v.coverage_complete is False
    assert "incomplete" in v.detail.lower() and "4" in v.detail
    v2 = report.build_view(audit_documents(CONFLICT_COMPLETE))
    assert "incomplete" not in v2.detail.lower()                      # a complete conflict result is not called incomplete


@pytest.mark.parametrize("docs", [PARTIAL, NOTHING, CONFLICT_COMPLETE, COMPLETE, dict(CONFLICT_COMPLETE)])
def test_markdown_states_verdict_and_coverage_in_words(docs):
    r = audit_documents(docs)
    v = report.build_view(r)
    md = report.render_markdown(v)
    assert f"Verdict: {v.verdict}" in md
    assert ("Coverage complete: yes" if v.coverage_complete else "Coverage complete: no") in md


def test_evidence_carries_original_and_si_quantities_for_cards():
    v = report.build_view(pump_result(), {"spec.md": "Document A", "purchasing.csv": "Document B"})
    a, b = v.conflicts[0].original
    assert (a.quantity, a.si) == ("10 bar", "1,000,000 Pa")
    assert (b.quantity, b.si) == ("900 kPa", "900,000 Pa")
    eq = {c.title: c for c in v.equivalents}
    assert eq["P-101 · flow"].original[0].quantity == "12 L/min" and eq["P-101 · flow"].original[1].quantity == "0.0002 m^3/s"


# ---------------------------------------------------------------- Streamlit AppTest
def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60)


def _run(choice):
    at = _app()
    at.run()
    at.checkbox(key="use_example").check().run()
    if choice:
        at.radio(key="example_choice").set_value(choice).run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    return at


def _example_choice_pump(at):
    return next(o for o in at.radio(key="example_choice").options if PUMP_LABEL in o)


def test_pump_station_example_is_selectable_and_never_shows_success():
    at = _app()
    at.run()
    at.checkbox(key="use_example").check().run()
    pump = _example_choice_pump(at)
    at.radio(key="example_choice").set_value(pump).run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    assert not at.success                                             # no green banner
    assert any("conflict" in e.value for e in at.error)
    texts = " ".join(m.value for m in at.markdown)
    assert "Coverage incomplete" in texts and "4 items" in texts
    assert "Conflicts found" in texts
    audit = at.session_state["audit"]
    exported = json.loads(audit["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == ("CONFLICTS_FOUND", False)
    assert "Verdict: CONFLICTS_FOUND" in audit["markdown"] and "Coverage complete: no" in audit["markdown"]


def test_pump_station_cards_show_actual_values_lines_and_rows():
    at = _app()
    at.run()
    at.checkbox(key="use_example").check().run()
    at.radio(key="example_choice").set_value(_example_choice_pump(at)).run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    blob = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.code)
    for needle in ("10 bar", "900 kPa", "1,000,000 Pa", "900,000 Pa", "line 5", "line 3", "spec.md", "purchasing.csv",
                   "P-101 | pressure | 10 bar", "P-101,pressure,900 kPa", "Conflict", "Verified equivalent"):
        assert needle in blob, needle
    gaps = " ".join(c.value for c in at.code)
    assert "P-101 | motor_power | 5 hp" in gaps and '"T-301","design_temperature","95 degC"' in gaps


def test_basic_example_remains_the_default_and_is_complete_with_a_conflict():
    at = _run(None)
    audit = at.session_state["audit"]
    assert audit["view"].verdict == report.VERDICT_CONFLICTS and audit["view"].coverage_complete is True
    texts = " ".join(m.value for m in at.markdown)
    assert "Coverage complete" in texts and "Coverage incomplete" not in texts
    assert [a.value for a in at.error] and not at.success


def test_no_conflict_complete_audit_is_the_only_green_state():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("a.md", b"P-1 | pressure | 10 bar", "text/markdown")
    at.get("file_uploader")[1].upload("b.md", b"P-1 | pressure | 1000 kPa", "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert at.success and "Coverage complete" in " ".join(m.value for m in at.markdown)
    at.get("file_uploader")[1].upload("b.md", b"P-1 | pressure | 1000 kPa\nP-9 | flow | 1 L/min", "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.success and at.warning
