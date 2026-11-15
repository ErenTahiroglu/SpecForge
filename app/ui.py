"""SpecForge: compare measurements in two engineering documents.

The audit engine (app.core) is deterministic and makes no network calls and no AI/API calls. This file is a Streamlit
app: Streamlit serves it over HTTP, so on a hosted website uploaded documents travel to that server. Streamlit's own
usage statistics are disabled for local runs in .streamlit/config.toml (a hosting platform may override this).

Start:  .venv/bin/python -m streamlit run app/ui.py

One audit, two views. "Overview" and "Detailed evidence" show the same stored audit. The view, the finding selected for
inspection and the list filter are presentation state only: they never run the audit again and never change the exports.
"""
import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.core import audit_documents, audit_verdict  # noqa: E402
from app.ingest import (  # noqa: E402
    SUPPORTED_EXTENSIONS, SUPPORTED_FORMATS_TEXT, ingest_upload, input_fingerprint, split_document_name, unique_names,
)
from app.report import VERDICT_CONFLICTS, VERDICT_MATCH, VERDICT_NONE, build_view, escape_md, render_markdown  # noqa: E402

# Two built-in synthetic examples. The example fingerprints are unchanged (same file names and bytes as before).
EXAMPLES = {
    "Pump station": (ROOT / "examples" / "pump_station" / "spec.md", ROOT / "examples" / "pump_station" / "purchasing.csv"),
    "Basic": (ROOT / "examples" / "spec.md", ROOT / "examples" / "bom.md"),
}
MY_FILES = "My files"
SOURCES = [*EXAMPLES, MY_FILES]
VIEWS = ["Overview", "Detailed evidence"]
FILTERS = {"All": None, "Conflicts": {"conflict"}, "Not compared": {"gap", "input"}, "Equivalent": {"equivalent"}}
PRIVACY_NOTICE = ("Privacy: When SpecForge is used on a hosted website, documents you upload are transmitted to the "
                  "hosting server for processing. Do not upload confidential documents. To process documents on your own "
                  "machine, run SpecForge locally.")
VERDICT_WORDS = {
    "CONFLICTS_FOUND": "Conflicts found",
    "NO_CONFLICTS_PARTIAL_COVERAGE": "No conflicts, but incomplete",
    "NO_CONFLICTS_IN_COMPARED_ROWS": "No conflicts in the compared rows",
    "NOTHING_COMPARED": "Nothing compared",
}
UNSEEN = ("Rows in a layout SpecForge does not recognize may not be detected at all, so they never appear in this list. "
          "Not listed does not mean checked.")
REASON = {
    "AMBIGUOUS_UNIT": "Unit has more than one meaning", "UNPARSED_ROW": "Row is not in a supported format",
    "UNMATCHED_RECORD": "Appears in only one document", "UNSUPPORTED_UNIT": "Unit is not supported",
    "LINE_TOO_LONG": "Line is too long to read", "DIMENSION_MISMATCH": "Different physical dimensions",
    "AMBIGUOUS_COMPARISON": "One file gives several values", "DUPLICATE_IDENTIFIER": "Repeated with different values",
    "TEMPERATURE_SEMANTICS": "Temperature difference against an absolute temperature",
}
READ_AS = {".md": "Markdown", ".txt": "plain text", ".csv": "CSV", ".pdf": "text-based PDF"}

st.set_page_config(page_title="SpecForge", page_icon="🔎", layout="wide")
# Owned class names only. No Streamlit-internal element is restyled.
st.markdown(
    """
<style>
.block-container {max-width: 1150px; padding-top: 2rem;}
.sf-sub {opacity: .8; font-size: 1.05rem; margin-top: -0.6rem;}
.sf-ev {border-left: 4px solid rgba(128,128,128,.6); background: rgba(128,128,128,.12); padding: .5rem .8rem; margin: .3rem 0; border-radius: 3px; overflow-wrap: anywhere;}
.sf-loc {opacity: .9; font-size: .9rem;}
.sf-val {font-size: 1.15rem; font-weight: 600; margin: .1rem 0;}
.sf-si {font-size: .95rem; opacity: .9; margin-bottom: .3rem;}
.sf-line {overflow-wrap: anywhere;}
.sf-file {font-size: .9rem; overflow-wrap: anywhere; margin: -.4rem 0 .4rem 0;}
.sf-tbl {border-collapse: collapse; width: 100%; font-size: .9rem;}
.sf-tbl th {font-weight: 600; text-align: left; padding: .45rem .6rem; border-bottom: 1px solid rgba(128,128,128,.5);}
.sf-tbl td {padding: .45rem .6rem; border-bottom: 1px solid rgba(128,128,128,.22); vertical-align: top; overflow-wrap: anywhere;}
.sf-tbl td.m {font-family: ui-monospace, Menlo, Consolas, monospace; font-variant-numeric: tabular-nums;}
.sf-tbl tr.sel td {background: rgba(128,128,128,.16);}
@media (max-width: 640px) {
  .sf-tbl thead {display: none;}
  .sf-tbl tr {display: block; padding: .4rem 0; border-bottom: 1px solid rgba(128,128,128,.35);}
  .sf-tbl td {display: block; border: none; padding: .1rem 0;}
  .sf-tbl td::before {content: attr(data-l) ": "; opacity: .75; font-family: inherit;}
}
</style>
""",
    unsafe_allow_html=True,
)

ss = st.session_state

# ---------------------------------------------------------------- display-only safety for user strings
# Bidirectional controls in a file name or a row can make text display in a misleading order (for example an extension
# that looks like ".pdf"). On screen they are revealed as visible code-point labels. The engine, the fingerprint and both
# exports keep the original characters.
BIDI_CONTROLS = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_REVEAL = {c: f"⟨U+{ord(c):04X}⟩" for c in BIDI_CONTROLS}


def shown(text) -> str:
    """Display form: every bidirectional control character becomes a visible label such as ⟨U+202E⟩."""
    return "".join(_REVEAL.get(ch, ch) for ch in str(text))


def esc(text) -> str:
    """For owned HTML: revealed, one line (a blank line would end the HTML block), HTML-escaped."""
    return html.escape(" ".join(shown(text).replace("\r", " ").replace("\t", " ").split("\n")), quote=True)


def md(text) -> str:
    """For Markdown: revealed and Markdown-escaped (no markup, no links, one line)."""
    return escape_md(shown(text))


def iso(text) -> str:
    """For plain-text widget labels: revealed and wrapped in a first-strong isolate so right-to-left text in a name
    cannot reorder the surrounding label."""
    return "\u2068" + " ".join(shown(text).split()) + "\u2069"


def owned_html(inner: str) -> None:
    """One owned HTML block. Callers pass only fixed markup plus values that went through esc()."""
    st.markdown(f'<div class="sf-line">{inner}</div>', unsafe_allow_html=True)


def bidi_in(text) -> list:
    return sorted({f"U+{ord(c):04X}" for c in str(text) if c in BIDI_CONTROLS})


# ---------------------------------------------------------------- page header and inputs
# Streamlit drops the state of a widget that is not rendered in a run (the view switch is hidden while there is no
# result, the filter while Overview is shown). The last choice is kept in a plain key and restored.
ss.setdefault("source", SOURCES[0])
ss.setdefault("view_pref", VIEWS[0])
if ss.get("view_mode") != ss["view_pref"]:
    ss["view_mode"] = ss["view_pref"]


def _remember(widget_key, pref_key):
    ss[pref_key] = ss[widget_key]

st.title("SpecForge", anchor=False)                      # no self-link icon (a 16 px target, issue #32)
st.markdown('<p class="sf-sub">Compare the measurements in two engineering documents. '
            "Every result shows the original text from both documents.</p>", unsafe_allow_html=True)


def _use_my_files(slot):
    """Choosing a file means the user wants to audit their own files, so the source follows the upload."""
    if ss.get(slot) is not None:
        ss["source"] = MY_FILES


with st.container(horizontal=True, vertical_alignment="bottom", gap="small", key="toolbar"):
    source = st.segmented_control("Source · both examples are synthetic", SOURCES, key="source", required=True)
    with st.popover(f"Tolerance {float(ss.get('tol', 1.0)):g} %", key="tol_pop"):
        tol_pct = st.slider("Allowed difference between two values (%)", 0.0, 20.0, 1.0, 0.1, key="tol",
                            help="Two converted values differ if |A − B| ÷ the larger of |A| and |B| is above this limit.")
    with st.popover("Formats & limits", key="limits_pop"):
        st.markdown(
            f"**Supported files:** {SUPPORTED_FORMATS_TEXT}\n\n"
            "**Supported rows:** one measurement per line, in this form:\n\n"
            "`P-101 | pressure | 10 bar`  (Markdown table rows with outer pipes also work)\n\n"
            "In `.csv` files, rows like `P-101,pressure,10 bar` or `P-101,pressure,10,bar` also work.\n\n"
            "**Not supported:** scanned documents, drawings, P&ID images, OCR, free text such as "
            "“design pressure is ten bar”, decimal commas (`10,5`), scientific notation (`1e3`). "
            "Recognizable unsupported measurement-like rows are listed and remain un-compared; other unrecognized formats may be missed. "
            "No-conflict results apply only to compared rows.\n\n"
            "**Units with more than one meaning are not guessed.** `ton`, `gallon`, `quart`, `pint`, `cal`, `hp`, `Btu` and `bbl` (barrel) "
            "are refused. Write an explicit unit, for example `short_ton`, `long_ton`, `tonne`, `US_liquid_gallon`, "
            "`imperial_gallon`, `thermochemical_calorie`, `metric_horsepower`, `oil_barrel`.\n\n"
            "**Temperature differences are not absolute temperatures.** `delta_degC` is a difference. A property named "
            "“temperature difference”, “rise”, “drop” or “delta T” makes `K` and `degC` a difference too. "
            "A difference and an absolute temperature are never compared.\n\n"
            "SpecForge shows where two documents disagree. It does not decide which one is right "
            "and it does not certify a document."
        )
    use_example = source in EXAMPLES
    _ready_now = use_example or (ss.get("up_a") is not None and ss.get("up_b") is not None)
    run = st.button("Run audit", type="primary", disabled=not _ready_now, key="run")

if use_example:
    _ea, _eb = EXAMPLES[source]
    st.caption(f"Document A: {md(_ea.relative_to(ROOT).as_posix())} · Document B: {md(_eb.relative_to(ROOT).as_posix())} · "
               "synthetic example files. Press Run audit to audit them.")
elif not _ready_now:
    st.caption("Choose Document A and Document B under My files below, then press Run audit.")

result_slot = st.container(key="result_slot")

with st.container(key="inputs"):
    st.info(PRIVACY_NOTICE)
    # The uploaders are always rendered (inside one keyed expander), so chosen files survive a switch to an example.
    with st.expander("My files" + (" (kept while an example is selected)" if use_example else ""),
                     expanded=not use_example, key="files_exp"):
        _types = [e.lstrip(".") for e in SUPPORTED_EXTENSIONS]
        _col_a, _col_b = st.columns(2)
        for _col, _slot, _label in ((_col_a, "up_a", "Document A"), (_col_b, "up_b", "Document B")):
            with _col:
                _up = st.file_uploader(_label, type=_types, key=_slot, on_change=_use_my_files, args=(_slot,))
                if _up is not None:
                    _data = _up.getvalue()
                    _ext = next((e for e in SUPPORTED_EXTENSIONS if _up.name.lower().endswith(e)), None)
                    _kind = READ_AS[_ext] if _ext else "not a supported type (the audit will stop)"
                    # #31: a source-adjacent safe label. The upload box itself shows the raw name.
                    owned_html(f'<div class="sf-file">Selected for {_label}: <code><bdi>{esc(_up.name)}</bdi></code> · '
                               f'{len(_data):,} bytes · SHA-256 {hashlib.sha256(_data).hexdigest()[:12]} · read as {esc(_kind)}</div>')
                    _found = bidi_in(_up.name)
                    if _found:
                        st.warning(f"**Check this file name.** It contains invisible text-direction characters "
                                   f"({', '.join(_found)}). The upload box above can show the name in a misleading order, "
                                   f"for example with a different extension. With those characters made visible the name is: "
                                   f"{md(_up.name)}. It is read as {md(_kind)}.")

up_a, up_b = ss.get("up_a"), ss.get("up_b")
example_a, example_b = EXAMPLES[source] if use_example else (None, None)
ready = use_example or (up_a is not None and up_b is not None)


def _current_key():
    """Identity of what is selected right now (file CONTENT, slots, mode, settings). None when nothing is ready."""
    if not ready:
        return None
    if use_example:
        return input_fingerprint(True, example_a.name, example_a.read_bytes(), example_b.name, example_b.read_bytes(), tol_pct)
    return input_fingerprint(False, up_a.name, up_a.getvalue(), up_b.name, up_b.getvalue(), tol_pct)


if not run and "audit" in ss and ss["audit"].get("input_key") != _current_key():   # read only when a result exists
    del ss["audit"]                                     # D02: the inputs or settings changed since the audit
    ss["audit_outdated"] = True                         # also when the selection is momentarily empty


def _load_inputs():
    if use_example:
        return [("A", example_a.name, example_a.read_bytes()), ("B", example_b.name, example_b.read_bytes())]
    return [("A", up_a.name, up_a.getvalue()), ("B", up_b.name, up_b.getvalue())]


def _run_audit():
    ss["audit_count"] = ss.get("audit_count", 0) + 1    # how many times the engine ran in this session (tests read it)
    (_, name_a, data_a), (_, name_b, data_b) = _load_inputs()
    key_a, key_b = unique_names(name_a, name_b)
    res_a, res_b = ingest_upload(key_a, data_a), ingest_upload(key_b, data_b)
    issues = res_a.issues + res_b.issues
    files = [f"Document A: {key_a}", f"Document B: {key_b}"]
    if not res_a.documents or not res_b.documents:
        ss["audit"] = {"blocked": True, "issues": issues, "files": files, "input_key": _current_key()}
        return
    docs = {**res_a.documents, **res_b.documents}
    labels = {k: "Document A" for k in res_a.documents}
    labels.update({k: "Document B" for k in res_b.documents})
    labels[key_a], labels[key_b] = "Document A", "Document B"
    result = audit_documents(docs, tol_pct / 100)
    view = build_view(result, labels, issues, files)
    # Export-level verdict: the core verdict plus input problems (for example an unreadable file) the core never saw.
    verdict, coverage_complete = audit_verdict(result, extra_gaps=len(issues))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ss["audit"] = {
        "blocked": False, "view": view, "result": result, "names": (key_a, key_b),
        "markdown": render_markdown(view, stamp),
        "json": json.dumps({"verdict": verdict, "coverage_complete": coverage_complete,
                            "result": result, "input_issues": issues}, indent=2, ensure_ascii=False),
        "input_key": _current_key(),
    }


if run:
    ss.pop("audit_outdated", None)
    try:
        _run_audit()
    except Exception as exc:  # shown to the user; never a silent failure
        ss["audit"] = {"blocked": True, "files": [], "input_key": _current_key(), "issues": [
            {"code": "AUDIT_ERROR", "message": f"The audit stopped: {type(exc).__name__}: {exc}"}]}
    ss["sel"] = None                                    # a new result starts at its first finding, unfiltered
    ss["filt"] = ss["filt_pref"] = "All"


# ---------------------------------------------------------------- presentation-only finding identity
def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:10]


def _doc_names(audit):
    """(name of Document A, name of Document B) as given to the engine, or (None, None) when unknown."""
    if audit.get("names"):
        return tuple(audit["names"])
    found = {}
    for line in audit["view"].files:
        for slot in ("A", "B"):
            prefix = f"Document {slot}: "
            if line.startswith(prefix):
                found[slot] = line[len(prefix):]
    return found.get("A"), found.get("B")


def _side(document, names):
    base, _ = split_document_name(document)
    return "A" if base == names[0] else "B" if base == names[1] else None


def _gap_side(location, names):
    """Which document a single-document gap belongs to. Longest name first, so 'a.md' never claims 'aa.md'."""
    for slot, name in sorted((("A", names[0]), ("B", names[1])), key=lambda p: -len(p[1] or "")):
        if name and (location.startswith(name + ", ") or location.startswith(name + ":page-")):
            return slot
    return None


def finding_rows(audit):
    """Every finding once, ordered Conflicts, Not compared, Equivalent. Keys are stable for one result: they come from
    the documents, locations and excerpts, never from list positions, so a filter cannot shift a selection."""
    view = audit["view"]
    names = _doc_names(audit)
    rows, seen = [], {}

    def add(base, row):
        n = seen.get(base, 0)
        seen[base] = n + 1
        row["key"] = f"{base}#{n}"
        rows.append(row)

    for kind, cards in (("conflict", view.conflicts), ("equivalent", view.equivalents)):
        for card in cards:
            cells = {"A": [], "B": []}
            for ev in card.original:
                cells[_side(ev.document, names) or "A"].append(ev)
            evidence_id = "|".join(f"{ev.document}|{ev.location}" for ev in card.original)
            add(f"{kind}|{evidence_id}|{_digest(chr(0).join(ev.excerpt for ev in card.original))}",
                {"kind": kind, "status": "Conflict" if kind == "conflict" else "Equivalent", "item": card.title,
                 "cells": cells, "diff": card.difference or "", "card": card})
    for gap in view.coverage:
        add(f"gap|{gap.code}|{gap.location}|{_digest(gap.excerpt)}",
            {"kind": "gap", "status": "Not compared", "item": gap.code, "side": _gap_side(gap.location, names),
             "diff": "", "gap": gap})
    for issue in view.input_issues:
        add(f"input|{issue.get('code')}|{_digest(str(issue.get('message', '')))}",
            {"kind": "input", "status": "Not compared", "item": str(issue.get("code", "INPUT")), "side": None,
             "diff": "", "issue": issue})
    order = {"conflict": 0, "gap": 1, "input": 1, "equivalent": 2}
    rows.sort(key=lambda r: order[r["kind"]])            # stable sort: the engine's order inside each group
    for number, row in enumerate(rows, 1):
        row["n"] = number                                # one number per finding, the same in every filter
    return rows


def _where(row) -> str:
    if row["kind"] == "gap":
        return row["gap"].location
    if row["kind"] == "input":
        return str(row["issue"].get("filename", ""))
    return " / ".join(f"{ev.document} {ev.location}" for ev in row["card"].original)


def _pick_label(row) -> str:
    """Plain text for the native selector (its options are not Markdown). User parts are revealed and isolated.
    The label depends only on the finding, never on the filter: Streamlit keeps a selectbox value as its label."""
    item = REASON.get(row["item"], row["item"]) if row["kind"] == "gap" else row["item"]
    parts = [f"{row['n']}. {row['status']}", iso(item)]
    if row["diff"]:
        parts.append(row["diff"])
    if _where(row):
        parts.append(iso(_where(row)))
    return " · ".join(parts)


# ---------------------------------------------------------------- shared pieces
def _goto_detailed(sel=None, filt="All"):
    ss["view_mode"] = ss["view_pref"] = VIEWS[1]
    ss["filt"] = ss["filt_pref"] = filt
    ss["sel"] = sel


def _evidence(ev):
    """One side of a numeric finding: label, document, location, value as written, SI value, verbatim row."""
    owned_html(f'<div class="sf-ev"><b>{esc(ev.label)}</b> · <bdi>{esc(ev.document)}</bdi>'
               f'<br><span class="sf-loc">{esc(ev.location)}</span></div>')
    value = f'<div class="sf-val">{esc(ev.quantity)}</div>' if ev.quantity else ""
    if ev.si:                                           # only when the engine computed an SI value
        value += f'<div class="sf-si">= {esc(ev.si)} (SI)</div>'
    if value:
        owned_html(value)
    st.code(shown(ev.excerpt), language=None, wrap_lines=True)


def _difference_line(card, view):
    st.markdown(f"Difference **{md(card.difference or '')}** (difference ÷ the larger value) · allowed "
                f"**{md(format(view.tolerance * 100, '.10g') + ' %')}**")


def _gap_line(row):
    if row["kind"] == "gap":
        gap = row["gap"]
        owned_html(f'<b>{esc(REASON.get(gap.code, gap.code))}</b> · <bdi>{esc(gap.location)}</bdi> · <code>{esc(gap.code)}</code>')
        if gap.excerpt:
            st.code(shown(gap.excerpt), language=None, wrap_lines=True)
    else:
        issue = row["issue"]
        owned_html(f'<b>Input processing issue</b> · {esc(issue.get("message", ""))} · <code>{esc(issue.get("code", ""))}</code>')


def _status_strip(view, total_gaps):
    """Verdict and coverage are two independent statuses. Green only for a complete audit without conflicts."""
    m = view.metrics
    n_comp = m["compared"]
    rows_word = f"{n_comp} compared measurement{'s' if n_comp != 1 else ''}"
    clean_complete = view.verdict == VERDICT_MATCH and view.coverage_complete
    if view.verdict == VERDICT_NONE:
        st.warning("**Nothing was compared.** No tag and property pair with a usable value was found in both documents. "
                   "This is not a successful audit.")
        return
    left, right = st.columns(2)
    with left:
        if view.verdict == VERDICT_CONFLICTS:
            st.error(f"**{m['conflicts']} conflict{'s' if m['conflicts'] != 1 else ''}** in {rows_word}"
                     + (f" ({view.internal} inside one document)" if view.internal else "")
                     + ". SpecForge does not decide which document is right.")
        elif clean_complete:
            st.success(f"**No conflicts in the {rows_word}.** Supported rows only. Not a certification of the documents.")
        else:
            with st.container(border=True):
                st.markdown(f"**No conflicts in the {rows_word}**, but the audit is incomplete, so this is not a clean result.")
    with right:
        if view.coverage_complete:
            with st.container(border=True):
                st.markdown("**Coverage complete.** Every supported row that was found was compared.")
        else:
            st.warning(f"**Coverage incomplete.** {total_gaps} item{'s were' if total_gaps != 1 else ' was'} not compared. "
                       "A result without conflicts does not cover them. Nothing was guessed.")


# ---------------------------------------------------------------- Overview
def overview(audit, rows_all):
    view = audit["view"]
    conflicts = [r for r in rows_all if r["kind"] == "conflict"]
    gaps = [r for r in rows_all if r["kind"] in ("gap", "input")]
    equivalents = [r for r in rows_all if r["kind"] == "equivalent"]
    if conflicts:
        first = conflicts[0]
        card = first["card"]
        with st.container(border=True, key="ov_conflict"):
            st.markdown(f"### Conflict 1 of {len(conflicts)} · {md(card.title)}", anchors=False)
            st.caption("The first conflict in the engine's order. Conflicts are not ranked by severity.")
            for col, ev in zip(st.columns(len(card.original)), card.original):
                with col:
                    _evidence(ev)
            _difference_line(card, view)
            st.button("Open detailed evidence", key="ov_open", on_click=_goto_detailed, kwargs={"sel": first["key"]})
    elif view.metrics["compared"] == 0:
        st.markdown("**No measurements were compared; no no-conflict conclusion can be drawn.**")
    else:
        st.markdown(f"No conflicts among the {view.metrics['compared']} compared measurement"
                    f"{'s' if view.metrics['compared'] != 1 else ''}.")
    if gaps:
        with st.container(border=True, key="ov_gaps"):
            st.markdown(f"### Not compared · {len(gaps)}", anchors=False)
            for row in gaps[:5]:
                _gap_line(row)
            if len(gaps) > 5:
                st.caption(f"{len(gaps) - 5} more in Detailed evidence.")
            st.button("Review not compared", key="ov_gaps_btn", on_click=_goto_detailed, kwargs={"filt": "Not compared"})
    if equivalents:
        st.markdown(f"**Verified equivalent after unit conversion ({len(equivalents)}):** "
                    + "; ".join(f"{md(r['card'].title)} ({md(r['card'].summary or '')})" for r in equivalents))
    if not conflicts:
        st.button("Open detailed evidence", key="ov_open", on_click=_goto_detailed)
    if view.notes:
        st.caption(f"{len(view.notes)} note{'s' if len(view.notes) != 1 else ''} in Detailed evidence. Notes do not reduce coverage.")
    st.caption(UNSEEN)


# ---------------------------------------------------------------- Detailed evidence
def _cell(row, slot):
    if row["kind"] in ("conflict", "equivalent"):
        evs = row["cells"][slot]
        return " ; ".join(f"{ev.quantity} · {ev.location}" for ev in evs) if evs else "—"
    if row["kind"] == "gap":
        if row["side"] == slot:
            return f"{row['gap'].location} · {row['gap'].excerpt}"
        return "— not paired" if row["side"] else ("see the evidence panel" if slot == "A" else "")
    return "see the evidence panel" if slot == "A" else ""


def table_html(rows, names, selected):
    head_a = "A · " + (names[0] or "Document A")
    head_b = "B · " + (names[1] or "Document B")
    heads = ("#", "Status", "Item", head_a, head_b, "Difference")
    head = "".join(f'<th scope="col">{esc(h)}</th>' for h in heads)
    mono = ' class="m"'
    body = []
    for r in rows:
        cells = (str(r["n"]), r["status"], r["item"], _cell(r, "A"), _cell(r, "B"), r["diff"] or "—")
        tds = []
        for j, text in enumerate(cells):
            inner = f"<b>{esc(text)}</b>" if j == 1 and r["kind"] == "conflict" else f"<bdi>{esc(text)}</bdi>"
            tds.append(f'<td{mono if j in (0, 3, 4, 5) else ""} data-l="{esc(heads[j])}">{inner}</td>')
        current = ' class="sel" aria-current="true"' if r["key"] == selected else ""
        body.append(f'<tr{current}>{"".join(tds)}</tr>')
    return (f'<table class="sf-tbl" aria-label="Findings in this filter, read-only"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def inspector(row, view):
    if row["kind"] in ("conflict", "equivalent"):
        card = row["card"]
        st.markdown(f"#### {'Conflict' if row['kind'] == 'conflict' else 'Verified equivalent'} · {md(card.title)}",
                    anchors=False)
        st.markdown(md(card.issue))
        for col, ev in zip(st.columns(len(card.original)), card.original):
            with col:
                _evidence(ev)
        _difference_line(card, view)
        st.caption("Method: both values are converted to SI units and compared. The difference is |A − B| ÷ the larger "
                   "of |A| and |B|.")
        st.markdown(md(card.explanation))
        if card.suggestion:                                       # reserved for the later writing review (P2)
            st.markdown("**Suggested wording**")
            st.code(card.suggestion, language=None)
            if card.decision:
                st.caption(f"Decision: {md(card.decision)}")
    elif row["kind"] == "gap":
        gap = row["gap"]
        st.markdown(f"#### Not compared · {md(REASON.get(gap.code, gap.code))}", anchors=False)
        owned_html(f'Where: <bdi>{esc(gap.location)}</bdi> · code <code>{esc(gap.code)}</code>')
        if gap.excerpt:
            st.code(shown(gap.excerpt), language=None, wrap_lines=True)
        st.markdown(md(gap.summary))
        st.caption("No counterpart is paired with this item, and no value, SI form or difference is shown for it, "
                   "because it was not compared.")
    else:
        issue = row["issue"]
        st.markdown(f"#### Not compared · input processing issue · {md(issue.get('code', ''))}", anchors=False)
        owned_html(esc(issue.get("message", "")))
        st.caption("The affected input or page was not processed. Other readable content from the same file may still "
                   "have been compared. The issue above says what was left out.")


def _on_pick():
    ss["sel"] = ss.get("pick")


def _step(keys, delta):
    i = keys.index(ss["sel"]) if ss.get("sel") in keys else 0
    ss["sel"] = keys[max(0, min(len(keys) - 1, i + delta))]


def detailed(audit, rows_all):
    view = audit["view"]
    counts = {name: sum(1 for r in rows_all if kinds is None or r["kind"] in kinds) for name, kinds in FILTERS.items()}
    if ss.get("filt") != ss.get("filt_pref", "All"):
        ss["filt"] = ss.get("filt_pref", "All")
    filt = st.segmented_control("Show", list(FILTERS), key="filt", required=True,
                                format_func=lambda name: f"{name} ({counts[name]})",
                                on_change=_remember, args=("filt", "filt_pref"))
    st.caption("The filter changes this list only. The counts and the exports do not change.")
    kinds = FILTERS[filt]
    rows = [r for r in rows_all if kinds is None or r["kind"] in kinds]
    if not rows:
        st.markdown({"All": "This result has no findings.", "Conflicts": "This result has no conflicts.",
                     "Not compared": "No items are listed as not compared.",
                     "Equivalent": "This result has no verified equivalents."}[filt])
    else:
        keys = [r["key"] for r in rows]
        if ss.get("sel") not in keys:
            ss["sel"] = keys[0]
        if ss.get("pick") != ss["sel"]:
            ss["pick"] = ss["sel"]                       # set before the selector is created in this run
        labels = {r["key"]: _pick_label(r) for r in rows}
        pos = keys.index(ss["sel"])
        c_pick, c_prev, c_next = st.columns([6, 1, 1], vertical_alignment="bottom")
        with c_pick:
            st.selectbox(f"Finding to inspect ({len(rows)} in this list)", keys, key="pick", format_func=labels.get,
                         on_change=_on_pick)
        with c_prev:
            st.button("Previous", key="prev", on_click=_step, args=(keys, -1), disabled=pos == 0, width="stretch")
        with c_next:
            st.button("Next", key="next", on_click=_step, args=(keys, 1), disabled=pos == len(keys) - 1, width="stretch")
        row = rows[pos]
        with st.container(border=True, key="inspector"):
            inspector(row, view)
        st.markdown("#### All findings in this list", anchors=False)
        st.html(table_html(rows, _doc_names(audit), ss["sel"]))
    if view.notes:
        with st.expander(f"Notes ({len(view.notes)}). These do not reduce coverage", key="notes_exp"):
            for item in view.notes:
                st.markdown(md(item.summary))
                st.caption(md(item.location))
                if item.excerpt:
                    st.code(shown(item.excerpt), language=None, wrap_lines=True)
    st.caption(UNSEEN)


# ---------------------------------------------------------------- result (above the inputs)
with result_slot:
    audit = ss.get("audit")
    if not audit and ss.get("audit_outdated"):
        st.info("The documents or the settings changed since the last audit, so the old result was removed. "
                + ("Press Run audit to audit them again." if ready else "Select both documents, then press Run audit."))
    if audit and audit["blocked"]:
        st.error("The audit did not run. Fix the problem below and try again.")
        for issue in audit["issues"]:
            st.warning(md(issue["message"]))
    elif audit:
        view = audit["view"]
        total_gaps = len(view.coverage) + len(view.input_issues)
        with st.container(horizontal=True, vertical_alignment="bottom", gap="small", key="result_bar"):
            view_mode = st.segmented_control("View", VIEWS, key="view_mode", required=True,
                                             on_change=_remember, args=("view_mode", "view_pref"))
            st.space("stretch")
            st.download_button("Download report (Markdown)", audit["markdown"], file_name="specforge-report.md",
                               mime="text/markdown", on_click="ignore", key="dl_md")
            st.download_button("Download data (JSON)", audit["json"], file_name="specforge-audit.json",
                               mime="application/json", on_click="ignore", key="dl_json")
        _status_strip(view, total_gaps)
        coverage_text = ("Coverage complete for the supported rows found" if view.coverage_complete
                         else f"Coverage incomplete: {total_gaps} item{'s' if total_gaps != 1 else ''} not compared"
                         if total_gaps else "Coverage incomplete: nothing was compared")
        m = view.metrics
        owned_html(f"<b>Verdict:</b> {esc(VERDICT_WORDS.get(view.verdict, view.verdict))} &nbsp;·&nbsp; "
                   f"<b>{esc(coverage_text)}</b>")
        st.caption(f"{m['rows_read']} rows read · {m['compared']} compared · {m['conflicts']} "
                   f"conflict{'s' if m['conflicts'] != 1 else ''} · "
                   f"{m['equivalent']} equivalent · {m['not_compared']} not compared · machine-readable: "
                   f"verdict={view.verdict}, coverage_complete={str(view.coverage_complete).lower()} "
                   "(the same values are in both exports)")
        rows_all = finding_rows(audit)
        if view_mode == VIEWS[0]:
            overview(audit, rows_all)
        else:
            detailed(audit, rows_all)
        st.caption(md(view.scope))
