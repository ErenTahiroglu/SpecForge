"""O1 two-view workbench (Overview | Detailed evidence) on Streamlit's AppTest.

One audit, two views: the view, the selected finding and the filter are presentation state only. These tests lock that the
engine runs once per Run audit, that the exports do not change when the view, filter or selection changes, that the seven
result states keep their colours in both views, and that untrusted names are displayed safely (issue #31)."""
import hashlib
import json
from pathlib import Path

import pytest

from app import report

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui.py"
DETAILED = "Detailed evidence"

CONF_COMPLETE = ("P-1 | pressure | 10 bar", "P-1 | pressure | 12 bar")
CONF_INCOMPLETE = ("P-1 | pressure | 10 bar\nP-2 | flow | 5 L/min", "P-1 | pressure | 12 bar")
COMPLETE = ("P-1 | pressure | 10 bar", "P-1 | pressure | 1000 kPa")
PARTIAL = ("P-1 | pressure | 10 bar\nP-2 | flow | 5 L/min", "P-1 | pressure | 1000 kPa")
NOTHING = ("nothing", "nothing")


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(UI), default_timeout=60)


def _run_button(at):
    return next(b for b in at.button if b.label == "Run audit")


def _upload(at, a, b, name_a="a.md", name_b="b.md"):
    data_a = a if isinstance(a, bytes) else a.encode()
    data_b = b if isinstance(b, bytes) else b.encode()
    at.get("file_uploader")[0].upload(name_a, data_a, "text/markdown")
    at.get("file_uploader")[1].upload(name_b, data_b, "text/markdown")
    at.run()
    return at


def _audit_uploaded(a, b, name_a="a.md", name_b="b.md"):
    at = _app()
    at.run()
    _upload(at, a, b, name_a, name_b)
    _run_button(at).click().run()
    assert not at.exception
    return at


def _audit_example(source="Pump station"):
    at = _app()
    at.run()
    at.segmented_control(key="source").set_value(source).run()
    _run_button(at).click().run()
    assert not at.exception
    return at


def _set_view(at, view):
    at.segmented_control(key="view_mode").set_value(view).run()
    assert not at.exception
    return at


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _downloads(at):
    return [d for d in at.get("download_button")]


def _markdown(at):
    return " ".join(m.value for m in at.markdown)


def _state(at):
    """The colours on screen: (red, green, amber) callouts. Neutral states use a plain bordered container."""
    return (len(at.error) > 0, len(at.success) > 0, len(at.warning) > 0)


# ---------------------------------------------------------------- default and inputs
def test_pump_station_is_preselected_and_nothing_runs_before_run_audit():
    at = _app()
    at.run()
    assert not at.exception
    assert at.segmented_control(key="source").value == "Pump station"
    assert "audit" not in at.session_state and "audit_count" not in at.session_state
    assert _downloads(at) == [] and not at.error and not at.success
    _run_button(at).click().run()
    assert at.session_state["audit_count"] == 1
    assert at.session_state["view_mode"] == "Overview"


def test_choosing_a_file_switches_the_source_and_the_uploaders_stay_mounted():
    at = _app()
    at.run()
    _upload(at, *COMPLETE)
    assert at.segmented_control(key="source").value == "My files"
    at.segmented_control(key="source").set_value("Basic").run()
    assert len(at.get("file_uploader")) == 2                              # still rendered, so the files are kept
    at.segmented_control(key="source").set_value("My files").run()
    _run_button(at).click().run()
    assert at.session_state["audit"]["view"].verdict == report.VERDICT_MATCH


def test_privacy_notice_is_rendered_before_the_uploaders():
    src = UI.read_text(encoding="utf-8")
    assert src.index("st.info(PRIVACY_NOTICE)") < src.index("st.file_uploader(")
    assert "nothing is uploaded" not in src.lower() and "not uploaded" not in src.lower()


def test_no_interactive_grid_and_downloads_do_not_rerun():
    src = UI.read_text(encoding="utf-8")
    assert "st.dataframe" not in src and "st.data_editor" not in src
    assert src.count('on_click="ignore"') == 2
    at = _audit_example()
    _set_view(at, DETAILED)
    assert not at.dataframe


# ---------------------------------------------------------------- one audit, many views
def test_ten_view_switches_keep_one_audit_and_identical_export_bytes():
    at = _audit_example()
    audit = at.session_state["audit"]
    before = (_sha(audit["markdown"]), _sha(audit["json"]))
    for i in range(10):
        _set_view(at, DETAILED if i % 2 == 0 else "Overview")
        assert len(_downloads(at)) == 2
    after = at.session_state["audit"]
    assert at.session_state["audit_count"] == 1
    assert (_sha(after["markdown"]), _sha(after["json"])) == before
    assert after["input_key"] == audit["input_key"]


def test_filters_and_selection_never_change_counts_or_exports():
    at = _audit_example()
    _set_view(at, DETAILED)
    audit = at.session_state["audit"]
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    labels = None
    for name in ("Conflicts", "Not compared", "Equivalent", "All"):
        at.segmented_control(key="filt").set_value(name).run()
        assert not at.exception
        options = list(at.segmented_control(key="filt").options)          # AppTest gives the formatted labels
        labels = labels or options
        assert options == labels == ["All (7)", "Conflicts (1)", "Not compared (4)", "Equivalent (2)"]
        caption = " ".join(c.value for c in at.caption)
        assert "1 conflict ·" in caption and "4 not compared" in caption and "2 equivalent" in caption
    for label in ("Previous", "Previous", "Next"):                     # the Equivalent selection is kept in All (#6)
        next(b for b in at.button if b.label == label).click().run()
    assert not at.exception and at.session_state["audit_count"] == 1
    assert (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest


def _all_keys(at):
    """Walk the selector with Next and collect the raw finding keys (AppTest exposes only formatted option labels)."""
    # Previous/Next are never disabled (a disabled button drops keyboard focus, P1-F02); the ends clamp. So walk by count.
    count = len(at.selectbox(key="pick").options)
    for _ in range(count):
        next(b for b in at.button if b.label == "Previous").click().run()
    keys = [at.selectbox(key="pick").value]
    for _ in range(count - 1):
        next(b for b in at.button if b.label == "Next").click().run()
        keys.append(at.selectbox(key="pick").value)
    return keys


def test_selection_keys_are_stable_across_filters():
    at = _audit_example()
    _set_view(at, DETAILED)
    keys = _all_keys(at)
    assert len(keys) == len(set(keys)) == 7 == len(at.selectbox(key="pick").options)
    gap = keys[2]                                                    # a not-compared item
    at.selectbox(key="pick").set_value(gap).run()
    assert at.session_state["sel"] == gap
    at.segmented_control(key="filt").set_value("Not compared").run()
    assert at.session_state["sel"] == gap and at.selectbox(key="pick").value == gap      # the same finding stays selected
    at.segmented_control(key="filt").set_value("All").run()
    assert at.selectbox(key="pick").value == gap
    at.segmented_control(key="filt").set_value("Conflicts").run()
    assert at.selectbox(key="pick").value == keys[0]                                     # not in this list: first one
    order = [k.split("|")[0] for k in keys]
    assert order == ["conflict", "gap", "gap", "gap", "gap", "equivalent", "equivalent"]


def test_zero_row_filter_is_safe():
    at = _audit_uploaded(*COMPLETE)
    _set_view(at, DETAILED)
    at.segmented_control(key="filt").set_value("Conflicts").run()
    assert not at.exception
    assert "This result has no conflicts." in _markdown(at)
    assert len(at.selectbox) == 0 and len(_downloads(at)) == 2 and at.success          # status and exports unchanged
    at.segmented_control(key="filt").set_value("Not compared").run()
    assert not at.exception and "No items are listed as not compared." in _markdown(at)


def test_not_compared_items_get_no_counterpart_value():
    at = _audit_example()
    _set_view(at, DETAILED)
    conflict = " ".join(str(getattr(e, "value", "")) for e in at.get_by_key("inspector"))
    assert "(SI)" in conflict and "Difference" in conflict                         # positive control: a conflict has both
    at.segmented_control(key="filt").set_value("Not compared").run()
    keys = _all_keys(at)
    assert len(keys) == 4
    for key in keys:
        at.selectbox(key="pick").set_value(key).run()
        inspector = at.get_by_key("inspector")
        text = " ".join(str(getattr(e, "value", "")) for e in inspector)
        assert "(SI)" not in text and "Difference" not in text
        assert any("No counterpart is paired" in c.value for c in at.caption)
    table = " ".join(h.value for h in at.html)
    assert "not paired" in table and "Pa" not in table


def test_overview_has_no_self_removing_action_buttons_and_navigation_stays_on_the_persistent_view_control():
    """#32: the former Overview buttons removed themselves when pressed and stranded keyboard focus on the page body.
    Navigation is now the persistent View and Show controls, which keep focus. Same stored audit, same selection rules."""
    at = _audit_example()
    labels = [b.label for b in at.button]
    assert "Open detailed evidence" not in labels and "Review not compared" not in labels
    text = _markdown(at)
    assert "choose **Detailed evidence** under **View** above" in text and "choose **Not compared**" in text
    _set_view(at, DETAILED)
    assert at.selectbox(key="pick").value.startswith("conflict|")                      # a new audit starts at the first finding
    at.segmented_control(key="filt").set_value("Not compared").run()
    assert at.segmented_control(key="filt").value == "Not compared" and at.selectbox(key="pick").value.startswith("gap|")
    assert at.session_state["audit_count"] == 1


def test_overview_shows_the_first_conflict_with_values_and_the_not_compared_list():
    at = _audit_example()
    text = _markdown(at)
    for needle in ("Conflict 1 of 1", "10 bar", "900 kPa", "1,000,000 Pa", "900,000 Pa", "Difference **10 %**",
                   "allowed **1 %**", "Not compared · 4", "Verified equivalent after unit conversion (2)"):
        assert needle in text, needle
    assert any("engine's order" in c.value and "not ranked" in c.value for c in at.caption)
    assert any("may not be detected at all" in c.value for c in at.caption)


# ---------------------------------------------------------------- the seven states, in both views
GOLDEN = [
    ("conflicts_complete", CONF_COMPLETE, (True, False, False), report.VERDICT_CONFLICTS, True),
    ("conflicts_incomplete", CONF_INCOMPLETE, (True, False, True), report.VERDICT_CONFLICTS, False),
    ("complete_clean", COMPLETE, (False, True, False), report.VERDICT_MATCH, True),
    ("partial_clean", PARTIAL, (False, False, True), report.VERDICT_PARTIAL, False),
    ("nothing_compared", NOTHING, (False, False, True), report.VERDICT_NONE, False),
]


@pytest.mark.parametrize("case", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_result_states_are_identical_in_both_views(case):
    _, (a, b), colours, verdict, complete = case
    at = _audit_uploaded(a, b)
    audit = at.session_state["audit"]
    assert (audit["view"].verdict, audit["view"].coverage_complete) == (verdict, complete)
    exported = json.loads(audit["json"])
    assert (exported["verdict"], exported["coverage_complete"]) == (verdict, complete)
    for view in ("Overview", DETAILED, "Overview"):
        _set_view(at, view)
        assert _state(at) == colours, (view, _state(at))
        assert len(_downloads(at)) == 2
        assert any(f"verdict={verdict}" in c.value and f"coverage_complete={str(complete).lower()}" in c.value
                   for c in at.caption)
    assert at.session_state["audit_count"] == 1


def test_golden_blocked_state_has_no_verdict_and_no_exports():
    at = _audit_uploaded(b"\xff\xfe broken", COMPLETE[1])
    assert at.session_state["audit"]["blocked"] is True
    assert any("did not run" in e.value for e in at.error) and not at.success
    assert _downloads(at) == [] and "Verdict:" not in _markdown(at)
    assert len(at.segmented_control) == 1                                   # only the source selector, no view switch


def test_golden_stale_state_hides_the_old_verdict_and_exports():
    at = _audit_uploaded(*COMPLETE)
    assert at.success
    at.slider[0].set_value(5.0).run()
    assert "audit" not in at.session_state
    assert not at.success and not at.error and _downloads(at) == []
    assert any("changed" in i.value for i in at.info)


def test_same_name_new_bytes_removes_the_old_result():
    at = _audit_uploaded(*COMPLETE)
    first = at.session_state["audit"]["input_key"]
    labels_before = [m.value for m in at.markdown if "Selected for Document B" in m.value]
    at.get("file_uploader")[1].upload("b.md", b"P-1 | pressure | 2000 kPa", "text/markdown")
    at.run()
    assert "audit" not in at.session_state and any("changed" in i.value for i in at.info)
    labels_after = [m.value for m in at.markdown if "Selected for Document B" in m.value]
    assert labels_before and labels_after and labels_before != labels_after                # bytes and SHA-256 changed
    _run_button(at).click().run()
    assert at.session_state["audit"]["input_key"] != first and at.error
    assert at.session_state["audit_count"] == 2


# ---------------------------------------------------------------- #31 untrusted names
CONTROLS = {"U+202E": "\u202e", "U+202D": "\u202d", "U+200E": "\u200e", "U+200F": "\u200f", "U+2066": "\u2066",
            "U+2067": "\u2067", "U+2068": "\u2068", "U+2069": "\u2069", "U+061C": "\u061c"}


def _visible_texts(at):
    parts = []
    for kind in ("title", "markdown", "caption", "info", "warning", "error", "success", "code", "html"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [e.label for e in at.expander]
    for box in at.selectbox:
        parts += list(box.options)                                       # formatted labels
    return parts


ALL_BIDI = {f"U+{ord(c):04X}": c for c in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"}


@pytest.mark.parametrize("code", list(ALL_BIDI))
def test_bidi_control_in_a_file_name_is_revealed_warned_and_blocks_the_audit(code):
    """#31 (OWNER_POLICY_GO_REQUIRED): a directional control in a selected name disables Run audit and shows rename
    instructions. The name stays revealed on screen. Nothing is audited and no result or download exists."""
    name = f"invoice_{ALL_BIDI[code]}fdp.md"
    at = _app()
    at.run()
    _upload(at, COMPLETE[0], CONF_COMPLETE[1], name_a=name)
    assert not at.exception
    warnings = " ".join(w.value for w in at.warning)
    assert "Rename this file to continue" in warnings and code in warnings and "Run audit is off until you rename a file" in warnings
    label = next(m.value for m in at.markdown if "Selected for Document A" in m.value)
    assert f"⟨{code}⟩" in label and ALL_BIDI[code] not in label
    assert _run_button(at).disabled
    assert "audit" not in at.session_state and "audit_count" not in at.session_state and _downloads(at) == []
    for t in _visible_texts(at):
        assert not any(c in t for c in ALL_BIDI.values()), t                         # every control is revealed on screen


def test_legitimate_right_to_left_name_gets_no_warning():
    at = _app()
    at.run()
    _upload(at, *CONF_COMPLETE, name_a="\u05de\u05e4\u05e8\u05d8.md", name_b="\u0645\u0648\u0627\u0635\u0641\u0627\u062a.md")
    assert not at.warning
    _run_button(at).click().run()
    _set_view(at, DETAILED)
    assert any("\u05de\u05e4\u05e8\u05d8.md" in h.value for h in at.html)


@pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>.md", "[click](javascript:alert(1)).md", "**bold**.md",
                                  "`code`.md", "a|b.md", "<script>alert(1)</script>.md"])
def test_hostile_file_names_are_text_in_both_views(name):
    at = _app()
    at.run()
    _upload(at, CONF_INCOMPLETE[0], CONF_INCOMPLETE[1], name_a=name)
    _run_button(at).click().run()
    for view in ("Overview", DETAILED):
        _set_view(at, view)
        for value in [m.value for m in at.markdown] + [h.value for h in at.html]:
            assert "<img" not in value and "<script" not in value, value
        for value in [m.value for m in at.markdown]:
            # Raw link syntax may appear only inside an owned HTML block, where Markdown is not parsed.
            assert "](javascript" not in value.replace("\\]", "") or value.startswith('<div class="sf-line">'), value
        for text in [w.value for w in at.warning] + [c.value for c in at.caption] + [e.label for e in at.expander]:
            assert "](javascript" not in text.replace("\\]", "")
    assert name in at.session_state["audit"]["json"]                                    # exports keep the real name


def test_selector_labels_are_unique_and_do_not_depend_on_the_filter():
    """Streamlit stores a selectbox value as its formatted label, so a label must name one finding in every filter."""
    at = _audit_example()
    _set_view(at, DETAILED)
    every = list(at.selectbox(key="pick").options)
    assert len(every) == len(set(every)) == 7
    for name in ("Conflicts", "Not compared", "Equivalent"):
        at.segmented_control(key="filt").set_value(name).run()
        assert set(at.selectbox(key="pick").options) <= set(every), name
    table = " ".join(h.value for h in at.html)
    assert 'data-l="#"><bdi>7</bdi>' in table                                         # numbers follow the finding


def test_view_and_filter_survive_a_stale_result_and_a_view_switch():
    """Streamlit drops the state of widgets that are not rendered; the last view and filter must still come back."""
    at = _audit_example()
    _set_view(at, DETAILED)
    at.segmented_control(key="filt").set_value("Not compared").run()
    _set_view(at, "Overview")
    _set_view(at, DETAILED)
    assert at.segmented_control(key="filt").value == "Not compared"
    at.slider[0].set_value(2.0).run()                                     # stale: no view switch is rendered
    assert "audit" not in at.session_state and len(at.segmented_control) == 1
    _run_button(at).click().run()
    assert at.segmented_control(key="view_mode").value == DETAILED
    assert at.segmented_control(key="filt").value == "All"                 # a new result starts unfiltered
    assert any("allowed **2 %**" in m.value for m in at.markdown)


# ---------------------------------------------------------------- O1 correction gate: R-01, R-02, R-03
def _pdf(pages):
    """A minimal synthetic PDF built in memory (hand-written objects). A page is a list of text lines; [] is blank."""
    n = len(pages)
    objs = ["<</Type/Catalog/Pages 2 0 R>>", "<</Type/Pages/Kids[" + " ".join(f"{3 + i} 0 R" for i in range(n)) + f"]/Count {n}>>"]
    font = 3 + 2 * n
    for i, lines in enumerate(pages):
        objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {3 + n + i} 0 R"
                    f"/Resources<</Font<</F1 {font} 0 R>>>>>>")
    for lines in pages:
        text = "BT /F1 12 Tf 72 720 Td " + " 0 -18 Td ".join(f"({r}) Tj" for r in lines) + " ET" if lines else ""
        objs.append(f"<</Length {len(text)}>>\nstream\n{text}\nendstream")
    objs.append("<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()


WHOLE_FILE_CLAIMS = ("file not read", "nothing from this file")


def _screen_text(at):
    return " ".join(str(getattr(e, "value", "")) for kind in ("markdown", "caption", "warning", "error", "info", "html")
                    for e in getattr(at, kind)).lower()


def _mixed_pdf_audit():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("mixed-pages.pdf", _pdf([["P-1 | pressure | 10 bar"], []]), "application/pdf")
    at.get("file_uploader")[1].upload("b.md", b"P-1 | pressure | 1000 kPa", "text/markdown")
    at.run()
    _run_button(at).click().run()
    assert not at.exception
    return at


def test_r01_partial_pdf_issue_does_not_claim_the_whole_file_was_unread():
    at = _mixed_pdf_audit()
    audit = at.session_state["audit"]
    result = json.loads(audit["json"])
    # engine truth: page 1 was compared, page 2 is an input issue, coverage is partial, never green
    assert (result["verdict"], result["coverage_complete"]) == (report.VERDICT_PARTIAL, False)
    assert result["result"]["comparisons"] == 1 and len(result["result"]["matches"]) == 1
    assert result["result"]["matches"][0]["reference"]["document"] == "mixed-pages.pdf:page-1"
    assert [i["code"] for i in result["input_issues"]] == ["PDF_PAGE_NO_TEXT"]
    precise = "mixed-pages.pdf, page 2: no extractable text"
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    # Overview: the issue is listed, labelled as an input processing issue, with the precise message
    text = _screen_text(at)
    assert precise in text and "input processing issue" in text and not at.success
    assert not [c for c in WHOLE_FILE_CLAIMS if c in text], text
    # Detailed: walk every finding, including the input issue; the inspector must stay page scoped
    _set_view(at, DETAILED)
    at.segmented_control(key="filt").set_value("Not compared").run()
    keys = _all_keys(at)
    assert len(keys) == 1 and keys[0].startswith("input|PDF_PAGE_NO_TEXT|")
    inspector = " ".join(str(getattr(e, "value", "")) for e in at.get_by_key("inspector")).lower()
    assert precise in inspector and "input processing issue" in inspector
    assert "may still have been compared" in inspector
    assert not [c for c in WHOLE_FILE_CLAIMS if c in inspector] and not [c for c in WHOLE_FILE_CLAIMS if c in _screen_text(at)]
    # the comparison that did happen is still listed with its extracted-page citation, and the exports did not change
    at.segmented_control(key="filt").set_value("Equivalent").run()
    equivalent = " ".join(str(getattr(e, "value", "")) for e in at.get_by_key("inspector"))
    assert "mixed-pages.pdf:page-1" in equivalent and "page 1, line 1 of the text extracted from that page" in equivalent
    assert at.session_state["audit_count"] == 1
    assert (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest


def test_r01_a_truly_blocked_whole_file_input_is_still_reported_as_blocked():
    at = _audit_uploaded(b"\xff\xfe broken", COMPLETE[1])
    assert at.session_state["audit"]["blocked"] is True
    text = _screen_text(at)
    assert "the audit did not run" in text and "not valid utf" in text
    assert any("did not run" in e.value for e in at.error) and not at.success
    assert _downloads(at) == [] and "verdict:" not in text and "input processing issue" not in text
    assert "audit succeeded" not in text and "no conflicts" not in text


def test_r02_removing_a_document_after_a_successful_audit_removes_the_result_and_both_downloads():
    for view in ("Overview", DETAILED):
        at = _audit_uploaded(*COMPLETE)
        assert at.success and len(_downloads(at)) == 2
        _set_view(at, view)
        assert at.session_state["audit"]["input_key"]
        at.get("file_uploader")[1].set_value(None).run()                            # remove Document B only
        assert not at.exception
        assert at.get("file_uploader")[0].value is not None and at.get("file_uploader")[1].value is None
        assert "audit" not in at.session_state, view
        assert _downloads(at) == [] and not at.success and not at.error, view
        assert "verdict:" not in _screen_text(at) and any("changed" in i.value for i in at.info), view
        assert next(b for b in at.button if b.label == "Run audit").disabled, view
        assert len(at.segmented_control) == 1, view                                  # no view switch without a result
        at.get("file_uploader")[1].upload("b.md", COMPLETE[1].encode(), "text/markdown")
        at.run()
        assert "audit" not in at.session_state and _downloads(at) == []             # returning the file does not revive it


NOTHING_SENTENCE = "no measurements were compared; no no-conflict conclusion can be drawn."


@pytest.mark.parametrize("view", ["Overview", DETAILED])
def test_r03_zero_comparisons_never_say_no_conflicts_in_either_view(view):
    at = _audit_uploaded(*NOTHING)
    audit = at.session_state["audit"]
    assert audit["view"].metrics["compared"] == 0 and audit["view"].verdict == report.VERDICT_NONE
    digest = (_sha(audit["markdown"]), _sha(audit["json"]))
    _set_view(at, "Overview")
    assert NOTHING_SENTENCE in _screen_text(at)
    _set_view(at, view)
    text = _screen_text(at)
    assert "no conflicts among" not in text and "no conflicts in" not in text
    assert "nothing was compared" in text and "not a successful audit" in text         # the amber callout is still there
    assert _state(at) == (False, False, True) and not at.success
    assert any("verdict=NOTHING_COMPARED" in c.value and "coverage_complete=false" in c.value for c in at.caption)
    assert (_sha(at.session_state["audit"]["markdown"]), _sha(at.session_state["audit"]["json"])) == digest
    assert at.session_state["audit_count"] == 1


def test_r03_nonzero_comparisons_keep_scoped_wording():
    at = _audit_uploaded(*COMPLETE)
    _set_view(at, "Overview")
    # a conflict-free complete result: the green callout and the scoped sentence both name the compared count
    assert "no conflicts among the 1 compared measurement." in _screen_text(at)
    assert NOTHING_SENTENCE not in _screen_text(at) and at.success
    at2 = _audit_uploaded(*PARTIAL)
    assert "no conflicts among the 1 compared measurement." in _screen_text(at2) and not at2.success
