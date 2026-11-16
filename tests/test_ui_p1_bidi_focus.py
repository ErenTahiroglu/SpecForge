"""P1 local spike for #31 (directional controls in a selected file name) and #32 (keyboard focus and navigation).

#31 is an input-policy change (OWNER_POLICY_GO_REQUIRED): a selected file whose name contains one of the enumerated
directional controls cannot be audited until it is renamed. These tests pin the exact behaviour so the owner can approve
or reject it as written. They count REAL calls of app.core.audit_documents with a spy, never the app's own audit_count.
The native upload chip is not testable here and is not claimed to be fixed (see the real-browser probes)."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui.py"
DETAILED = "Detailed evidence"
CLEAN_A, CLEAN_B = b"P-1 | pressure | 10 bar", b"P-1 | pressure | 1000 kPa"
CONFLICT_B = b"P-1 | pressure | 12 bar"
BIDI = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"


@pytest.fixture
def engine(monkeypatch):
    """Spy on the real engine entry point the UI calls. Returns the list of calls."""
    import app.core as core
    calls = []
    original = core.audit_documents

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(core, "audit_documents", spy)
    return calls


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(UI), default_timeout=60)


def _run_button(at):
    return next(b for b in at.button if b.label == "Run audit")


def _upload(at, slot, name, data):
    at.get("file_uploader")[slot].upload(name, data, "text/markdown")
    at.run()


def _fresh(a=("a.md", CLEAN_A), b=("b.md", CLEAN_B)):
    at = _app()
    at.run()
    if a:
        _upload(at, 0, *a)
    if b:
        _upload(at, 1, *b)
    return at


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _downloads(at):
    return list(at.get("download_button"))


def _text(at):
    return " ".join(str(getattr(e, "value", "")) for kind in ("markdown", "caption", "warning", "error", "info", "success", "html")
                    for e in getattr(at, kind))


# ---------------------------------------------------------------- #31: the name gate
@pytest.mark.parametrize("slot,which", [(0, "Document A"), (1, "Document B")])
def test_suspect_name_in_either_slot_blocks_run_with_zero_engine_calls(engine, slot, which):
    at = _app()
    at.run()
    suspect = ("invoice_\u202efdp.md", CLEAN_A if slot == 0 else CLEAN_B)
    clean = ("clean.md", CLEAN_B if slot == 0 else CLEAN_A)
    _upload(at, slot, *suspect)
    _upload(at, 1 - slot, *clean)
    assert not at.exception
    assert _run_button(at).disabled
    warnings = " ".join(w.value for w in at.warning)
    assert "Run audit is off until you rename a file" in warnings and f"The name of {which} contains" in warnings
    assert "Rename the file on your computer, remove the current file" in warnings and "Nothing was audited" in warnings
    other = "Document B" if slot == 0 else "Document A"
    assert f"The name of {other} contains" not in warnings
    assert engine == [] and "audit" not in at.session_state and _downloads(at) == []


def test_both_names_suspect_are_both_named_and_block(engine):
    at = _fresh(("a_\u202e.md", CLEAN_A), ("b_\u2066.md", CLEAN_B))
    warnings = " ".join(w.value for w in at.warning)
    assert "The name of Document A contains" in warnings and "The name of Document B contains" in warnings
    assert "U+202E" in warnings and "U+2066" in warnings
    assert _run_button(at).disabled and engine == []


def test_a_name_is_gated_by_its_code_points_not_by_its_script(engine):
    """Arabic, Hebrew and mixed-script names without a directional control are accepted and audited normally. Other
    invisible characters (zero width space/joiner, BOM) are NOT in the enumerated set and are not blocked: documented scope."""
    names = ["\u05de\u05e4\u05e8\u05d8.md", "\u0645\u0648\u0627\u0635\u0641\u0627\u062a.md", "plan_\u05de\u05e4\u05e8\u05d8_v2.md",
             "a\u200bb.md", "a\u200db.md", "a\ufeffb.md"]
    for name in names:
        at = _fresh((name, CLEAN_A), ("b.md", CLEAN_B))
        assert not _run_button(at).disabled and "Run audit is off" not in " ".join(w.value for w in at.warning), repr(name)
        _run_button(at).click().run()
        assert not at.exception and "audit" in at.session_state and len(_downloads(at)) == 2, repr(name)
    assert len(engine) == len(names)


def test_hostile_markup_and_newline_names_without_controls_are_inert_and_audited(engine):
    name = "x\n<img src=x onerror=alert(1)>[a](javascript:alert(1))**b**`c`.md"
    at = _fresh((name, CLEAN_A), ("b.md", CLEAN_B))
    assert not _run_button(at).disabled
    _run_button(at).click().run()
    assert len(engine) == 1 and not at.exception
    for value in [m.value for m in at.markdown]:
        assert "<img" not in value.replace("&lt;img", "") or value.startswith('<div class="sf-line">') or "&lt;" in value
    assert name in json.loads(at.session_state["audit"]["json"])["result"]["matches"][0]["reference"]["document"]   # raw, canonical


def test_stale_result_and_downloads_vanish_when_a_suspect_name_replaces_a_file(engine):
    at = _fresh()
    _run_button(at).click().run()
    assert len(engine) == 1 and at.success and len(_downloads(at)) == 2
    _upload(at, 1, "b_\u202e.md", CLEAN_B)                                   # same bytes, suspect name
    assert "audit" not in at.session_state and _downloads(at) == [] and not at.success and not at.error
    assert any("changed" in i.value and "Rename the file named in the message above" in i.value for i in at.info)
    assert _run_button(at).disabled and len(at.segmented_control) == 1 and len(engine) == 1
    # recovery: choose a renamed file; Run is available again and performs exactly one more audit
    _upload(at, 1, "b_renamed.md", CLEAN_B)
    assert not _run_button(at).disabled and "Run audit is off" not in " ".join(w.value for w in at.warning)
    _run_button(at).click().run()
    assert len(engine) == 2 and at.success and len(_downloads(at)) == 2


def test_removing_the_suspect_file_restores_the_normal_prompt(engine):
    at = _fresh(("a_\u202e.md", CLEAN_A), ("b.md", CLEAN_B))
    assert _run_button(at).disabled
    at.get("file_uploader")[0].set_value(None).run()
    assert not at.exception and _run_button(at).disabled                       # A is missing now
    assert "Run audit is off until you rename" not in " ".join(w.value for w in at.warning)
    assert any("Choose Document A and Document B" in c.value for c in at.caption)
    assert engine == []


def test_same_name_new_bytes_and_duplicate_names_still_behave_after_the_gate(engine):
    at = _fresh(("same.md", CLEAN_A), ("same.md", CLEAN_B))                  # duplicate names are made unique by the engine path
    _run_button(at).click().run()
    first = at.session_state["audit"]["input_key"]
    _upload(at, 1, "same.md", CONFLICT_B)                                     # same name, different bytes
    assert "audit" not in at.session_state
    _run_button(at).click().run()
    assert at.session_state["audit"]["input_key"] != first and at.error and len(engine) == 2


def test_tolerance_change_does_not_lift_the_gate(engine):
    at = _fresh(("a_\u202e.md", CLEAN_A), ("b.md", CLEAN_B))
    at.slider[0].set_value(5.0).run()
    assert _run_button(at).disabled and engine == [] and "audit" not in at.session_state


def test_example_sources_ignore_a_suspect_upload_but_my_files_blocks_it(engine):
    at = _fresh(("a_\u202e.md", CLEAN_A), ("b.md", CLEAN_B))
    assert at.segmented_control(key="source").value == "My files" and _run_button(at).disabled
    at.segmented_control(key="source").set_value("Pump station").run()
    assert not _run_button(at).disabled                                        # the example is audited by its own fixed names
    kept = " ".join(w.value for w in at.warning)
    assert "kept but not used while an example is selected" in kept and "Rename this file to continue" not in kept
    assert "Run audit is off" not in kept
    _run_button(at).click().run()
    assert len(engine) == 1 and at.session_state["audit"]["view"].metrics["conflicts"] == 1
    assert at.session_state["audit"]["input_key"]
    at.segmented_control(key="source").set_value("My files").run()
    assert "audit" not in at.session_state and _downloads(at) == [] and _run_button(at).disabled
    assert len(engine) == 1                                                    # switching back never called the engine


def test_gate_changes_no_export_bytes_for_accepted_files(engine):
    """A fixed audited result produces the same Markdown and JSON before and after view changes, with clean names."""
    at = _fresh(("a.md", CLEAN_A), ("b.md", CONFLICT_B))
    _run_button(at).click().run()
    audit = at.session_state["audit"]
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    for i in range(10):
        at.segmented_control(key="view_mode").set_value(DETAILED if i % 2 == 0 else "Overview").run()
    at.selectbox(key="pick").set_value(at.selectbox(key="pick").value).run() if at.selectbox else None
    assert (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest
    assert len(engine) == 1 and len(_downloads(at)) == 2


def test_the_control_set_is_exactly_the_documented_twelve():
    src = UI.read_text(encoding="utf-8")
    import re
    block = re.search(r'BIDI_CONTROLS = "([^"]+)"', src).group(1)
    assert block.encode().decode("unicode_escape") == BIDI and len(BIDI) == 12


# ---------------------------------------------------------------- #32: navigation without a self-removing control
def test_no_overview_action_button_exists_to_strand_focus(engine):
    at = _app()
    at.run()
    _run_button(at).click().run()
    assert [b.label for b in at.button if b.label in ("Open detailed evidence", "Review not compared")] == []
    assert len(engine) == 1


def test_persistent_view_control_reaches_the_inspector_with_one_stored_audit(engine):
    at = _app()
    at.run()
    _run_button(at).click().run()
    audit = at.session_state["audit"]
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    for _ in range(6):                                                         # the same control is present in both views
        for view in (DETAILED, "Overview"):
            at.segmented_control(key="view_mode").set_value(view).run()
            assert [c.key for c in at.segmented_control if c.key == "view_mode"] == ["view_mode"]
    at.segmented_control(key="view_mode").set_value(DETAILED).run()
    assert at.get_by_key("inspector") is not None and at.selectbox(key="pick").value.startswith("conflict|")
    for name in ("Conflicts", "Not compared", "Equivalent", "All"):
        at.segmented_control(key="filt").set_value(name).run()
    assert len(engine) == 1 and (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest
    assert len(_downloads(at)) == 2 and not at.dataframe


# ---------------------------------------------------------------- #32 / P1-F02: Previous and Next at the ends of the list
# A native button that disables itself while it holds keyboard focus drops focus to the page body. Both buttons therefore stay
# enabled and the ends clamp (no wraparound), with a visible position line and a note. AppTest cannot prove focus: the real-browser
# probe (endpoint_probe.py) does. These tests pin the state the browser result depends on.
def _pump_detailed():
    at = _app()
    at.run()
    _run_button(at).click().run()
    at.segmented_control(key="view_mode").set_value(DETAILED).run()
    return at


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _status(at):
    return next((c.value for c in at.caption if c.value.startswith("Finding ")), None)


def _first_line_of_inspector(at):
    return " ".join(str(getattr(e, "value", "")) for e in at.get_by_key("inspector")).split("\n")[0]


def test_previous_and_next_are_never_disabled_at_any_position_and_never_wrap(engine):
    at = _pump_detailed()
    count = len(at.selectbox(key="pick").options)
    assert count == 7
    seen = []
    for step in range(count):
        assert not _btn(at, "Previous").disabled and not _btn(at, "Next").disabled, step
        seen.append(at.selectbox(key="pick").value)
        if step < count - 1:
            _btn(at, "Next").click().run()
    assert len(set(seen)) == 7 and seen == sorted(set(seen), key=seen.index)          # visited each finding once, in order
    last = at.selectbox(key="pick").value
    _btn(at, "Next").click().run()                                                      # at the end: clamp, no wraparound
    assert at.selectbox(key="pick").value == last and not _btn(at, "Next").disabled
    assert len(engine) == 1


def test_previous_from_second_reaches_first_then_clamps_with_a_visible_note(engine):
    at = _pump_detailed()
    _btn(at, "Next").click().run()                                                      # first -> second
    assert _status(at).startswith("Finding 2 of 7") and "already" not in _status(at)
    first_key = None
    _btn(at, "Previous").click().run()                                                  # second -> first
    first_key = at.selectbox(key="pick").value
    assert _status(at) == "Finding 1 of 7 (first finding in this list)"
    assert not _btn(at, "Previous").disabled and not at.exception
    assert _first_line_of_inspector(at).lstrip(" #").startswith("Conflict")
    _btn(at, "Previous").click().run()                                                  # at the first: no wraparound, honest note
    assert at.selectbox(key="pick").value == first_key and not _btn(at, "Previous").disabled
    assert _status(at) == "Finding 1 of 7 (first finding in this list) · This is already the first finding. Nothing changed."
    assert len(engine) == 1


def test_next_from_penultimate_reaches_last_then_clamps_with_a_visible_note(engine):
    at = _pump_detailed()
    for _ in range(5):
        _btn(at, "Next").click().run()                                                  # to the penultimate (6 of 7)
    assert _status(at).startswith("Finding 6 of 7")
    _btn(at, "Next").click().run()
    last_key = at.selectbox(key="pick").value
    assert _status(at) == "Finding 7 of 7 (last finding in this list)" and not _btn(at, "Next").disabled
    _btn(at, "Next").click().run()
    assert at.selectbox(key="pick").value == last_key and not _btn(at, "Next").disabled
    assert _status(at).endswith("This is already the last finding. Nothing changed.")
    _btn(at, "Previous").click().run()                                                  # leaving the end clears the note
    assert _status(at) == "Finding 6 of 7" and len(engine) == 1


def test_exactly_one_finding_keeps_both_buttons_enabled_and_says_so(engine):
    at = _pump_detailed()
    at.segmented_control(key="filt").set_value("Conflicts").run()
    assert len(at.selectbox(key="pick").options) == 1
    assert not _btn(at, "Previous").disabled and not _btn(at, "Next").disabled
    only = at.selectbox(key="pick").value
    for label in ("Previous", "Next"):
        _btn(at, label).click().run()
        assert at.selectbox(key="pick").value == only and not _btn(at, label).disabled
        assert _status(at) == "Finding 1 of 1 (only finding in this list) · This list has only one finding. Nothing changed."
    assert len(engine) == 1


def test_the_boundary_note_is_cleared_by_selection_filter_and_view_changes(engine):
    at = _pump_detailed()
    first = at.selectbox(key="pick").value
    _btn(at, "Next").click().run()
    second = at.selectbox(key="pick").value
    _btn(at, "Previous").click().run()
    _btn(at, "Previous").click().run()                                                  # clamped at the first
    assert at.selectbox(key="pick").value == first and "Nothing changed" in _status(at)
    at.selectbox(key="pick").set_value(second).run()                                    # a real pick clears it
    assert "Nothing changed" not in _status(at) and _status(at).startswith("Finding 2 of 7")
    at.selectbox(key="pick").set_value(first).run()                                     # back on the same finding: still no stale note
    assert _status(at) == "Finding 1 of 7 (first finding in this list)"
    _btn(at, "Previous").click().run()
    assert "Nothing changed" in _status(at)
    at.segmented_control(key="filt").set_value("Not compared").run()
    assert _status(at).startswith("Finding 1 of 4") and "Nothing changed" not in _status(at)
    _btn(at, "Previous").click().run()
    at.segmented_control(key="view_mode").set_value("Overview").run()
    at.segmented_control(key="view_mode").set_value(DETAILED).run()
    assert "Nothing changed" not in _status(at) and len(engine) == 1


def test_filtering_and_reselecting_after_a_boundary_keeps_stable_identity(engine):
    at = _pump_detailed()
    for _ in range(9):                                                                  # past the end on purpose
        _btn(at, "Next").click().run()
    last = at.selectbox(key="pick").value
    assert last.startswith("equivalent|")
    at.segmented_control(key="filt").set_value("Equivalent").run()
    assert at.selectbox(key="pick").value == last and _status(at).startswith("Finding 2 of 2")
    at.segmented_control(key="filt").set_value("Not compared").run()                    # not in this list: first of the list
    assert at.selectbox(key="pick").value.startswith("gap|") and _status(at).startswith("Finding 1 of 4")
    at.segmented_control(key="filt").set_value("All").run()
    assert not _btn(at, "Previous").disabled and not _btn(at, "Next").disabled and len(engine) == 1


def test_endpoint_navigation_changes_no_export_byte_and_makes_no_extra_engine_call(engine):
    at = _pump_detailed()
    audit = at.session_state["audit"]
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    for _ in range(8):
        _btn(at, "Next").click().run()
    for _ in range(8):
        _btn(at, "Previous").click().run()
    for name in ("Conflicts", "Not compared", "Equivalent", "All"):
        at.segmented_control(key="filt").set_value(name).run()
    assert (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest
    assert len(engine) == 1 and len(_downloads(at)) == 2 and not at.dataframe
    _run_button(at).click().run()
    assert len(engine) == 2                                                             # an explicit second Run adds exactly one


# ---------------------------------------------------------------- PR34-N01: a Previous/Next note never outlives its audit
STALE_NOTE_WORDS = ("Nothing changed", "only one finding", "already the first", "already the last")


def _all_captions(at):
    return " ".join(c.value for c in at.caption)


def _strip_stamp(markdown):
    import re
    return re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", "<stamp>", markdown)


@pytest.mark.parametrize("label", ["Previous", "Next"])
def test_a_new_run_clears_a_boundary_note_from_the_previous_audit(engine, label):
    at = _pump_detailed()
    first = at.session_state["audit"]
    first_json, first_md = first["json"], _strip_stamp(first["markdown"])
    at.segmented_control(key="filt").set_value("Conflicts").run()
    _btn(at, label).click().run()                                                      # one-finding list: a no-op note
    assert _status(at) == "Finding 1 of 1 (only finding in this list) · This list has only one finding. Nothing changed."
    _run_button(at).click().run()                                                       # a second explicit Run, same input
    assert not at.exception and len(engine) == 2
    assert at.segmented_control(key="filt").value == "All" and len(at.selectbox(key="pick").options) == 7
    assert _status(at) == "Finding 1 of 7 (first finding in this list)"
    assert not [w for w in STALE_NOTE_WORDS if w in _all_captions(at)]
    second = at.session_state["audit"]                                                  # same input: same canonical exports
    assert second["json"] == first_json and _strip_stamp(second["markdown"]) == first_md
    assert sorted(json.loads(second["json"])) == ["coverage_complete", "input_issues", "result", "verdict"]
    for view in ("Overview", DETAILED):                                                 # the note does not come back
        at.segmented_control(key="view_mode").set_value(view).run()
        assert not [w for w in STALE_NOTE_WORDS if w in _all_captions(at)]
    assert len(engine) == 2


def test_a_blocked_new_run_does_not_carry_an_old_note_into_the_next_result(engine):
    at = _fresh(("a.md", CLEAN_A + b"\nP-2 | flow | 5 L/min"), ("b.md", CONFLICT_B))        # 1 conflict + 1 not compared
    _run_button(at).click().run()
    at.segmented_control(key="view_mode").set_value(DETAILED).run()
    at.segmented_control(key="filt").set_value("Conflicts").run()
    _btn(at, "Next").click().run()
    assert "This list has only one finding. Nothing changed." in _status(at)
    _upload(at, 1, "b.md", b"\xff\xfe not utf-8")                                         # same name, unreadable bytes
    _run_button(at).click().run()                                                       # a new attempt that is blocked
    assert at.session_state["audit"]["blocked"] is True and any("did not run" in e.value for e in at.error)
    assert at.session_state.get("nav_note") is None and _downloads(at) == []
    assert not [w for w in STALE_NOTE_WORDS if w in _text(at)]
    assert len(engine) == 1                                                             # blocked at ingest: no engine call
    _upload(at, 1, "b.md", CONFLICT_B)                                                  # back to the readable file
    _run_button(at).click().run()
    assert len(engine) == 2 and at.segmented_control(key="view_mode").value == DETAILED
    assert _status(at) == "Finding 1 of 2 (first finding in this list)"
    assert not [w for w in STALE_NOTE_WORDS if w in _all_captions(at)]
