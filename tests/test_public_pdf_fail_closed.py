"""Public web UI: PDF upload is off by default and the refusal is enforced on the server side.

Without SPECFORGE_ENABLE_LOCAL_PDF_UI=1 the Streamlit app must never pass an uploaded PDF to the PDF reader. Layers:
1. the browser file filter (the uploader's accepted types; not testable here);
2. Streamlit 1.67.0's own server-side extension check when the widget value is read (it raises
   "Invalid file extension" before any app code runs);
3. the app gate in app/ui.py (_pdf_gate), which runs before ingest_pair and the PDF reader.
The AppTest harness does not apply layer 1. Layer 3 is tested with layer 2 switched off (`no_streamlit_check`), so the
app gate is proven on its own. Every PDF entry point is instrumented, so any call is a failure. All PDFs are synthetic.

These tests prove the gate in this source tree. They do not prove what a hosted deployment runs: that needs a separate
main promotion and a hosted check (the hosted runtime SHA is unknown).
"""
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pdf_factory import build_pdf, simple_pdf

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui.py"
FLAG = "SPECFORGE_ENABLE_LOCAL_PDF_UI"
CODE = "PDF_DISABLED_IN_WEB_UI"
MESSAGE = ("PDF upload is disabled in the public demo; use Markdown, TXT or CSV. The PDF engine remains available only in "
           "explicitly opted-in local operation.")
MD_A, MD_B = b"P-1 | pressure | 10 bar\n", b"P-1 | pressure | 1000 kPa\n"
PDF = simple_pdf([["P-1 | pressure | 10 bar"]])


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)


@pytest.fixture
def pdf_tripwire(monkeypatch):
    """Record (and refuse) every call into the PDF path: ingest_pair, ingest_upload, pdf_page_count, the PDF reader."""
    import pypdf
    from app import ingest
    calls = []

    def trip(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"PDF path entered: {name}")
        return fail

    for name in ("ingest_pair", "ingest_upload", "pdf_page_count", "_read_pdf", "_extract_pdf", "_open_pdf"):
        monkeypatch.setattr(ingest, name, trip(name))
    monkeypatch.setattr(pypdf, "PdfReader", trip("PdfReader"))
    return calls


@pytest.fixture
def no_streamlit_check(monkeypatch):
    """Switch off Streamlit's server-side extension check (layer 2) to test the app gate (layer 3) on its own."""
    from streamlit.elements.widgets import file_uploader
    monkeypatch.setattr(file_uploader, "enforce_filename_restriction", lambda *a, **k: None)


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(UI), default_timeout=90)


def _upload(at, name_a, data_a, name_b, data_b):
    at.get("file_uploader")[0].upload(name_a, data_a, "application/octet-stream")
    at.get("file_uploader")[1].upload(name_b, data_b, "application/octet-stream")
    at.run()
    return at


def _run(at):
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    return at


def _audit(name_a, data_a, name_b, data_b):
    at = _app()
    at.run()
    return _run(_upload(at, name_a, data_a, name_b, data_b))


def _text(at):
    parts = itertools.chain(at.markdown, at.warning, at.error, at.info, at.success, at.caption)
    return "\n".join(str(getattr(e, "value", "")) for e in parts)


def _downloads(at):
    return [e for e in at.get("download_button")]


def _assert_refused(at, slots):
    audit = at.session_state["audit"]
    assert audit["blocked"] is True
    assert [i["code"] for i in audit["issues"]] == [CODE] * len(slots)
    assert [i["message"].split(" (")[0] for i in audit["issues"]] == slots
    assert "result" not in audit and "json" not in audit and "markdown" not in audit
    assert not _downloads(at)                                            # no export of any kind
    shown = _text(at)
    assert MESSAGE in shown and "The audit did not run" in shown
    for word in ("No conflicts in the compared rows", "Coverage complete", "Traceback", "AUDIT_ERROR", "site-packages"):
        assert word not in shown


# ------------------------------------------------------------------ the policy module itself

@pytest.mark.parametrize("value,enabled", [("1", True), (None, False), ("", False), ("0", False), ("true", False),
                                           ("TRUE", False), ("yes", False), (" 1", False), ("1 ", False), ("01", False)])
def test_flag_is_on_only_for_exactly_one(value, enabled):
    from app.upload_policy import pdf_ui_enabled
    env = {} if value is None else {FLAG: value}
    assert pdf_ui_enabled(env) is enabled


def test_policy_lists_are_derived_from_one_place():
    from app import upload_policy as p
    assert p.ui_extensions(False) == (".md", ".txt", ".csv")
    assert p.ui_extensions(True) == (".md", ".txt", ".csv", ".pdf")
    assert ".pdf" not in p.ui_formats_text(False).replace("PDF upload is off", "")
    assert "PDF upload is off" in p.ui_formats_text(False)
    for name in ("a.pdf", "A.PDF", "a.Pdf", "x.tar.pdf", "spec (A).pdf"):
        assert p.is_pdf_name(name) and p.refused_pdf_names([name], False) == [name] and p.refused_pdf_names([name], True) == []
    for name in ("a.md", "a.pdf.md", "a.pdfx", "pdf", "a.txt"):
        assert not p.is_pdf_name(name)


def test_policy_module_does_not_import_the_pdf_reader():
    code = "import sys; import app.upload_policy; print('pypdf' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "False"


# ------------------------------------------------------------------ default UI: advertised types and copy

def test_default_ui_advertises_only_text_formats():
    at = _app()
    at.run()
    assert not at.exception
    for uploader in at.get("file_uploader"):
        assert list(uploader.proto.type) == [".md", ".txt", ".csv"]
    popover_copy = _text(at)
    assert "PDF upload is off in this web interface" in popover_copy
    assert "text-based PDF" not in popover_copy


def test_opt_in_ui_advertises_pdf(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    at = _app()
    at.run()
    for uploader in at.get("file_uploader"):
        assert list(uploader.proto.type) == [".md", ".txt", ".csv", ".pdf"]
    assert "text-based PDF" in _text(at)


@pytest.mark.parametrize("value", ["true", "yes", "0", ""])
def test_other_flag_values_keep_pdf_off_in_the_ui(monkeypatch, value, pdf_tripwire, no_streamlit_check):
    monkeypatch.setenv(FLAG, value)
    at = _audit("a.pdf", PDF, "b.md", MD_B)
    _assert_refused(at, ["Document A"])
    assert pdf_tripwire == []


# ------------------------------------------------------------------ default UI: server-side refusal (filter bypassed)

NAMES = ["a.pdf", "A.PDF", "a.Pdf"]
SLOTS = [("Document A",), ("Document B",), ("Document A", "Document B")]


def _pair(name, slots):
    a = (name, PDF) if "Document A" in slots else ("a.md", MD_A)
    b = (name.replace("a", "b").replace("A", "B"), PDF) if "Document B" in slots else ("b.md", MD_B)
    return (*a, *b)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("slots", SLOTS)
def test_streamlit_layer_rejects_a_bypassed_pdf_before_app_code(name, slots, pdf_tripwire):
    """Layer 2. Streamlit stops the run with its own error. Nothing is audited, no export is shown."""
    at = _app()
    at.run()
    _upload(at, *_pair(name, slots))
    extension = name[name.rindex("."):]
    assert [e.message for e in at.exception] == [f"Invalid file extension: `{extension}`. Allowed: ['.md', '.txt', '.csv']"]
    assert not _downloads(at) and "audit" not in at.session_state and "audit_count" not in at.session_state
    assert pdf_tripwire == []


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("slots", SLOTS)
def test_app_gate_refuses_a_pdf_in_any_slot_without_entering_the_pdf_path(name, slots, pdf_tripwire, no_streamlit_check):
    """Layer 3 alone. A visible refusal of the whole pair, zero calls into ingest or pypdf."""
    at = _audit(*_pair(name, slots))
    _assert_refused(at, list(slots))
    assert pdf_tripwire == []
    assert "audit_count" not in at.session_state                         # the engine never ran


def test_same_pdf_name_in_both_slots_is_refused(pdf_tripwire, no_streamlit_check):
    at = _audit("same.pdf", PDF, "same.pdf", PDF)
    _assert_refused(at, ["Document A", "Document B"])
    assert pdf_tripwire == []


def test_default_ui_states_that_pdf_is_off_next_to_the_upload_boxes():
    at = _app()
    at.run()
    assert MESSAGE in [c.value for c in at.caption]


def test_selected_pdf_shows_the_refusal_before_run(pdf_tripwire, no_streamlit_check):
    at = _app()
    at.run()
    _upload(at, "a.pdf", PDF, "b.md", MD_B)
    assert not at.exception and "audit" not in at.session_state
    shown = _text(at)
    assert MESSAGE in shown and "off in this web interface" in shown
    assert pdf_tripwire == []


def test_a_fresh_interpreter_never_imports_the_pdf_reader_for_a_refused_upload():
    """No tripwire: a separate Python process runs the app with a PDF and reports whether pypdf was ever imported.

    Cases: default (Streamlit layer on), default with the Streamlit layer off (app gate alone), and the local opt-in as
    the positive control that shows the probe can see pypdf load."""
    script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(ROOT / 'tests')!r})
if sys.argv[1] == "gate-only":
    from streamlit.elements.widgets import file_uploader
    file_uploader.enforce_filename_restriction = lambda *a, **k: None
from pdf_factory import simple_pdf
from streamlit.testing.v1 import AppTest
at = AppTest.from_file({str(UI)!r}, default_timeout=90); at.run()
at.get("file_uploader")[0].upload("a.PDF", simple_pdf([["P-1 | pressure | 10 bar"]]), "application/pdf")
at.get("file_uploader")[1].upload("b.md", b"P-1 | pressure | 10 bar", "text/markdown"); at.run()
if not at.exception:
    next(b for b in at.button if b.label == "Run audit").click().run()
audit = at.session_state["audit"] if "audit" in at.session_state else None
print("EXCEPTION", [e.message.lower().startswith("invalid file extension: `.pdf`") for e in at.exception])
print("BLOCKED", None if audit is None else (audit["blocked"], [i["code"] for i in audit.get("issues", [])]))
print("PYPDF", "pypdf" in sys.modules)
"""
    seen = {}
    for case in ("default", "gate-only", "opt-in"):
        env = {k: v for k, v in os.environ.items() if k != FLAG}
        if case == "opt-in":
            env[FLAG] = "1"
        out = subprocess.run([sys.executable, "-c", script, case], cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
        assert out.returncode == 0, out.stderr[-2000:]
        seen[case] = dict(line.split(" ", 1) for line in out.stdout.strip().splitlines()
                          if line.startswith(("EXCEPTION", "BLOCKED", "PYPDF")))
    assert seen["default"] == {"EXCEPTION": "[True]", "BLOCKED": "None", "PYPDF": "False"}
    assert seen["gate-only"] == {"EXCEPTION": "[]", "BLOCKED": f"(True, ['{CODE}'])", "PYPDF": "False"}
    assert seen["opt-in"]["PYPDF"] == "True" and seen["opt-in"]["BLOCKED"].startswith("(False")


# ------------------------------------------------------------------ stale results never survive a refused PDF

def test_a_clean_result_is_never_shown_after_a_bypassed_pdf_reaches_streamlit():
    """Layer 2 path: Streamlit's error stops the run, so the stored clean result and its downloads are not shown."""
    at = _audit("a.md", MD_A, "b.md", MD_B)
    assert _downloads(at)
    at.get("file_uploader")[0].upload("a.pdf", PDF, "application/pdf")
    at.run()
    assert at.exception and not _downloads(at)
    assert "No conflicts in the compared rows" not in _text(at)


def test_a_clean_result_is_removed_when_a_pdf_replaces_a_file_and_never_comes_back(no_streamlit_check):
    at = _audit("a.md", MD_A, "b.md", MD_B)
    assert at.session_state["audit"]["blocked"] is False and _downloads(at)
    assert at.session_state["audit_count"] == 1
    at.get("file_uploader")[0].upload("a.pdf", PDF, "application/pdf")
    at.run()
    assert "audit" not in at.session_state and not _downloads(at)        # removed as soon as the input changed
    assert at.session_state["audit_outdated"] is True
    _run(at)
    _assert_refused(at, ["Document A"])
    assert at.session_state["audit_count"] == 1                          # the refused run never reached the engine
    at.get("file_uploader")[0].upload("a.md", MD_A, "text/markdown")     # back to the original, already audited input
    at.run()
    assert "audit" not in at.session_state and not _downloads(at)        # the old clean result is not shown again
    _run(at)
    assert at.session_state["audit"]["blocked"] is False and at.session_state["audit_count"] == 2


def test_a_refused_pair_stays_refused_across_view_and_tolerance_changes(pdf_tripwire, no_streamlit_check):
    at = _audit("a.pdf", PDF, "b.md", MD_B)
    _assert_refused(at, ["Document A"])
    at.slider(key="tol").set_value(2.0).run()
    assert "audit" not in at.session_state and not _downloads(at)
    _run(at)
    _assert_refused(at, ["Document A"])
    assert pdf_tripwire == []


def test_switching_source_to_an_example_and_back_does_not_reuse_a_result(no_streamlit_check):
    at = _app()
    at.run()
    _upload(at, "a.pdf", PDF, "b.md", MD_B)
    assert at.session_state["source"] == "My files"
    at.segmented_control(key="source").set_value("Basic").run()
    _run(at)
    assert at.session_state["audit"]["blocked"] is False                 # the example runs; the kept PDF is not used
    at.segmented_control(key="source").set_value("My files").run()
    assert "audit" not in at.session_state and not _downloads(at)
    _run(at)
    _assert_refused(at, ["Document A"])
    assert at.session_state["audit_count"] == 1                          # only the example run reached the engine


def test_view_switch_does_not_run_an_audit():
    at = _audit("a.md", MD_A, "b.md", MD_B)
    count = at.session_state["audit_count"]
    exports = (at.session_state["audit"]["json"], at.session_state["audit"]["markdown"])
    at.segmented_control(key="view_mode").set_value("Detailed evidence").run()
    at.segmented_control(key="view_mode").set_value("Overview").run()
    assert at.session_state["audit_count"] == count
    assert (at.session_state["audit"]["json"], at.session_state["audit"]["markdown"]) == exports


# ------------------------------------------------------------------ other invalid input is still refused, not crashed

@pytest.mark.parametrize("name,data,code", [("a.md", b"\x00\x01binary", "NOT_TEXT"), ("a.txt", b"\xff\xfe\xfa", "NOT_UTF8"),
                                            ("a.csv", b"\x00", "NOT_TEXT")])
def test_invalid_text_input_is_refused_without_exception(name, data, code):
    at = _audit(name, data, "b.md", MD_B)
    audit = at.session_state["audit"]
    assert audit["blocked"] is True and [i["code"] for i in audit["issues"]] == [code]
    assert not _downloads(at) and "Traceback" not in _text(at)


@pytest.mark.parametrize("name", ["a.docx", "a.png"])
def test_other_types_are_refused_by_streamlit_or_by_ingest(name, no_streamlit_check):
    """With the Streamlit layer on, these never reach the app (same as before this change). Without it, ingest refuses."""
    at = _audit(name, b"PK\x03\x04", "b.md", MD_B)
    audit = at.session_state["audit"]
    assert audit["blocked"] is True and [i["code"] for i in audit["issues"]] == ["UNSUPPORTED_FORMAT"] and not _downloads(at)


def test_pdf_bytes_under_a_text_name_are_read_as_text_never_by_the_pdf_reader(monkeypatch):
    import pypdf
    calls = []
    monkeypatch.setattr(pypdf, "PdfReader", lambda *a, **k: calls.append(1))
    at = _audit("looks-like.md", build_pdf([[b"BT (P-1 | pressure | 10 bar) Tj ET"]]), "b.md", MD_B)
    assert calls == []
    audit = at.session_state["audit"]
    if not audit["blocked"]:
        assert json.loads(audit["json"])["coverage_complete"] is False   # never a clean, complete result


# ------------------------------------------------------------------ positive controls

@pytest.mark.parametrize("names", [("a.md", "b.md"), ("a.txt", "b.csv"), ("a.csv", "b.txt")])
def test_text_formats_still_work_by_default(names):
    data = {".md": MD_A, ".txt": MD_A, ".csv": b"P-1,pressure,10 bar\n"}
    other = {".md": MD_B, ".txt": MD_B, ".csv": b"P-1,pressure,1000 kPa\n"}
    at = _audit(names[0], data[Path(names[0]).suffix], names[1], other[Path(names[1]).suffix])
    audit = at.session_state["audit"]
    assert audit["blocked"] is False and audit["result"]["comparisons"] == 1 and audit["result"]["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"


@pytest.mark.parametrize("source", ["Basic", "Pump station"])
def test_examples_still_run_by_default(source):
    at = _app()
    at.run()
    at.segmented_control(key="source").set_value(source).run()
    _run(at)
    audit = at.session_state["audit"]
    assert audit["blocked"] is False and audit["result"]["comparisons"] > 0 and _downloads(at)


def test_local_opt_in_reads_an_ordinary_text_pdf_with_page_level_evidence(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    two_pages = simple_pdf([["intro"], ["P-1 | pressure | 10 bar"]])
    at = _audit("plan.pdf", two_pages, "b.md", MD_B)
    audit = at.session_state["audit"]
    assert audit["blocked"] is False
    match = audit["result"]["matches"][0]
    assert match["reference"]["document"] == "plan.pdf:page-2" and audit["result"]["comparisons"] == 1


@pytest.mark.parametrize("data,code", [(simple_pdf([["x"]] * 11), "PDF_TOO_MANY_PAGES"),
                                       (build_pdf([[b" " * 5_000_000]], compress=True), "PDF_LIMIT_EXCEEDED"),
                                       (b"%PDF-1.4\n" + b"\x00" * 200, "PDF_UNREADABLE")])
def test_local_opt_in_keeps_the_structural_refusals(monkeypatch, data, code):
    monkeypatch.setenv(FLAG, "1")
    at = _audit("x.pdf", data, "b.md", MD_B)
    audit = at.session_state["audit"]
    assert audit["blocked"] is True and [i["code"] for i in audit["issues"]] == [code] and not _downloads(at)


def test_local_opt_in_keeps_the_combined_document_limit(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    seven = simple_pdf([[f"P-{i} | pressure | {i} bar"] for i in range(7)])
    at = _audit("a.pdf", seven, "b.pdf", seven)
    assert [i["code"] for i in at.session_state["audit"]["issues"]] == ["DOCUMENT_LIMIT_EXCEEDED"]


# ------------------------------------------------------------------ documentation matches the implementation

def test_readme_describes_the_default_and_the_opt_in():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"{FLAG}=1" in readme and MESSAGE in readme
    assert "`.md`, `.txt` or `.csv`" in readme
    assert "text-based `.pdf`) in the upload boxes" not in readme       # the old default statement is gone
    assert "this has **not** been done" not in readme
    for kept in ("RUNTIME_SHA_UNKNOWN", "Do not upload confidential documents", "gatherUsageStats = true",
                 "Dev Container (development only, issue #39, not fixed)", "not a CPU-time or memory guarantee",
                 "pypdf==6.21.4", "not exact equality"):
        assert kept in readme, kept
