"""SpecForge: compare measurements in two engineering documents. Runs locally. No network use.

Start:  .venv/bin/python -m streamlit run app/ui.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.core import audit_documents  # noqa: E402
from app.ingest import SUPPORTED_EXTENSIONS, SUPPORTED_FORMATS_TEXT, ingest_upload, input_fingerprint, unique_names  # noqa: E402
from app.report import (  # noqa: E402
    VERDICT_CONFLICTS, VERDICT_MATCH, build_view, escape_md, evidence_html, render_markdown, table_rows,
)

EXAMPLE_A = ROOT / "examples" / "spec.md"
EXAMPLE_B = ROOT / "examples" / "bom.md"

st.set_page_config(page_title="SpecForge", page_icon="🔎", layout="wide")
st.markdown(
    """
<style>
.block-container {max-width: 1150px; padding-top: 2rem;}
.sf-sub {opacity: .8; font-size: 1.05rem; margin-top: -0.6rem;}
.sf-ev {border-left: 4px solid #4c9; background: rgba(128,128,128,.14); padding: .5rem .8rem; margin: .3rem 0; border-radius: 3px;}
.sf-loc {opacity: .8; font-size: .85rem;}
</style>
""",
    unsafe_allow_html=True,
)

st.title("SpecForge")
st.markdown('<p class="sf-sub">Compare the measurements in two engineering documents. '
            "Every result shows the original text from both documents.</p>", unsafe_allow_html=True)

with st.expander("What SpecForge reads, and what it does not", expanded=False):
    st.markdown(
        f"**Supported files:** {SUPPORTED_FORMATS_TEXT}\n\n"
        "**Supported rows:** one measurement per line, in this form:\n\n"
        "`P-101 | pressure | 10 bar`  (Markdown table rows with outer pipes also work)\n\n"
        "In `.csv` files, rows like `P-101,pressure,10 bar` or `P-101,pressure,10,bar` also work.\n\n"
        "**Not supported:** scanned documents, drawings, P&ID images, OCR, free text such as "
        "“design pressure is ten bar”, decimal commas (`10,5`), scientific notation (`1e3`). "
        "Rows that look like measurements but cannot be read are listed, not ignored.\n\n"
        "**Units with more than one meaning are not guessed.** `ton`, `gallon`, `quart`, `pint`, `cal`, `hp`, `Btu` and `bbl` (barrel) "
        "are refused. Write an explicit unit, for example `short_ton`, `long_ton`, `tonne`, `US_liquid_gallon`, "
        "`imperial_gallon`, `thermochemical_calorie`, `metric_horsepower`, `oil_barrel`.\n\n"
        "**Temperature differences are not absolute temperatures.** `delta_degC` is a difference. A property named "
        "“temperature difference”, “rise”, “drop” or “delta T” makes `K` and `degC` a difference too. "
        "A difference and an absolute temperature are never compared.\n\n"
        "SpecForge shows where two documents disagree. It does not decide which one is right "
        "and it does not certify a document."
    )


left, right = st.columns(2)
with left:
    st.subheader("Document A")
    up_a = st.file_uploader("Upload document A", type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS], key="up_a",
                            label_visibility="collapsed")
with right:
    st.subheader("Document B")
    up_b = st.file_uploader("Upload document B", type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS], key="up_b",
                            label_visibility="collapsed")

use_example = st.checkbox("Use the example documents instead (examples/spec.md and examples/bom.md)", key="use_example")
with st.expander("Settings"):
    tol_pct = st.slider("Allowed difference between two values (%)", 0.0, 20.0, 1.0, 0.1,
                        help="Two converted values differ if |A − B| ÷ the larger of |A| and |B| is above this limit.")

ready = use_example or (up_a is not None and up_b is not None)
if not ready:
    st.info("Upload both documents, or tick the example box, then run the audit.")
run = st.button("Run audit", type="primary", disabled=not ready)


def _current_key():
    """Identity of what is selected right now (file CONTENT, slots, mode, settings). None when nothing is ready."""
    if not ready:
        return None
    if use_example:
        return input_fingerprint(True, EXAMPLE_A.name, EXAMPLE_A.read_bytes(), EXAMPLE_B.name, EXAMPLE_B.read_bytes(), tol_pct)
    return input_fingerprint(False, up_a.name, up_a.getvalue(), up_b.name, up_b.getvalue(), tol_pct)


_key = _current_key()
if not run and "audit" in st.session_state and st.session_state["audit"].get("input_key") != _key:
    del st.session_state["audit"]                       # D02: the inputs or settings changed since the audit
    st.session_state["audit_outdated"] = True            # also when the selection is momentarily empty


def _load_inputs():
    if use_example:
        return [("A", EXAMPLE_A.name, EXAMPLE_A.read_bytes()), ("B", EXAMPLE_B.name, EXAMPLE_B.read_bytes())]
    return [("A", up_a.name, up_a.getvalue()), ("B", up_b.name, up_b.getvalue())]


def _run_audit():
    (_, name_a, data_a), (_, name_b, data_b) = _load_inputs()
    key_a, key_b = unique_names(name_a, name_b)
    res_a, res_b = ingest_upload(key_a, data_a), ingest_upload(key_b, data_b)
    issues = res_a.issues + res_b.issues
    files = [f"Document A: {key_a}", f"Document B: {key_b}"]
    if not res_a.documents or not res_b.documents:
        st.session_state["audit"] = {"blocked": True, "issues": issues, "files": files, "input_key": _current_key()}
        return
    docs = {**res_a.documents, **res_b.documents}
    labels = {k: "Document A" for k in res_a.documents}
    labels.update({k: "Document B" for k in res_b.documents})
    labels[key_a], labels[key_b] = "Document A", "Document B"
    result = audit_documents(docs, tol_pct / 100)
    view = build_view(result, labels, issues, files)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.session_state["audit"] = {
        "blocked": False, "view": view, "result": result,
        "markdown": render_markdown(view, stamp),
        "json": json.dumps({"result": result, "input_issues": issues}, indent=2, ensure_ascii=False),
        "input_key": _current_key(),
    }


if run:
    st.session_state.pop("audit_outdated", None)
    try:
        _run_audit()
    except Exception as exc:  # shown to the user; never a silent failure
        st.session_state["audit"] = {"blocked": True, "files": [], "input_key": _current_key(), "issues": [
            {"code": "AUDIT_ERROR", "message": f"The audit stopped: {type(exc).__name__}: {exc}"}]}


def _card(card):
    """One review card: original text, issue, explanation. P2 can fill suggestion/decision."""
    st.markdown(f"**{escape_md(card.issue)}**")
    for ev in card.original:
        st.markdown(evidence_html(ev), unsafe_allow_html=True)      # every user-derived field is HTML-escaped
        st.code(ev.excerpt, language=None)
    st.markdown(escape_md(card.explanation))
    if card.suggestion:
        st.markdown("**Suggested wording**")
        st.code(card.suggestion, language=None)
        if card.decision:
            st.caption(f"Decision: {card.decision}")


audit = st.session_state.get("audit")
if not audit and st.session_state.get("audit_outdated"):
    st.divider()
    st.info("The documents or the settings changed since the last audit, so the old result was removed. "
            + ("Press Run audit to audit them again." if ready else "Select both documents, then press Run audit."))
if audit:
    st.divider()
    if audit["blocked"]:
        st.error("The audit did not run. Fix the problem below and try again.")
        for issue in audit["issues"]:
            st.warning(escape_md(issue["message"]))
    else:
        view = audit["view"]
        banner = {VERDICT_CONFLICTS: st.error, VERDICT_MATCH: st.success}.get(view.verdict, st.warning)
        banner(f"**{view.headline}**\n\n{view.detail}")
        m = view.metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Rows read", m["rows_read"])
        c2.metric("Compared", m["compared"])
        c3.metric("Conflicts", m["conflicts"])
        c4.metric("Equivalent", m["equivalent"])
        c5.metric("Not compared", m["not_compared"])

        st.subheader(f"Conflicts ({len(view.conflicts)})")
        if view.conflicts:
            st.dataframe(table_rows(view.conflicts), width="stretch", hide_index=True)
            for card in view.conflicts:
                with st.expander(f"Evidence: {escape_md(card.title)}", expanded=len(view.conflicts) == 1):
                    _card(card)
        else:
            st.write("No conflicts among the compared measurements.")

        st.subheader(f"Verified equivalent after unit conversion ({len(view.equivalents)})")
        if view.equivalents:
            st.caption("These values were converted to SI units and agree within the allowed difference.")
            for card in view.equivalents:
                with st.expander(f"{escape_md(card.title)}: {escape_md(card.summary)}"):
                    _card(card)
        else:
            st.write("None.")

        total_gaps = len(view.coverage) + len(view.input_issues)
        st.subheader(f"Not compared ({total_gaps})")
        if total_gaps:
            st.caption("These items were not part of the comparison. A result without conflicts does not cover them.")
        else:
            st.write("Every supported row that was found was compared.")
        for issue in view.input_issues:
            st.warning(escape_md(issue["message"]))
        for item in view.coverage:
            with st.expander(escape_md(item.summary)):
                st.caption(escape_md(item.location))
                if item.excerpt:
                    st.code(item.excerpt, language=None)
        if view.notes:
            st.subheader(f"Notes ({len(view.notes)})")
            st.caption("These do not reduce coverage. They are shown so that nothing is hidden.")
            for item in view.notes:
                with st.expander(escape_md(item.summary)):
                    st.caption(escape_md(item.location))
                    if item.excerpt:
                        st.code(item.excerpt, language=None)

        st.subheader("Export")
        d1, d2 = st.columns(2)
        d1.download_button("Download report (Markdown)", audit["markdown"], file_name="specforge-report.md",
                           mime="text/markdown")
        d2.download_button("Download data (JSON)", audit["json"], file_name="specforge-audit.json",
                           mime="application/json")
        st.caption(view.scope)
