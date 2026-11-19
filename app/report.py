"""Presentation model for an audit result: verdict, review cards, tables and a Markdown report.

Pure functions. No Streamlit import, so everything here is unit-testable.
Only facts from the audit result are used. Nothing is inferred about engineering intent.

ReviewCard is deliberately generic. A later STE-inspired writing check can fill `suggestion`
and `decision` and reuse the same screen and report code for "original text, issue,
suggested wording, explanation, accept/reject".
"""
from __future__ import annotations
import html
import re
from dataclasses import dataclass, field
from typing import Optional

from .ingest import split_document_name
from .core import (UNITS, VERDICT_CONFLICTS, VERDICT_MATCH, VERDICT_NONE, VERDICT_PARTIAL, ZERO_TOLERANCE_FLOOR,
                   audit_verdict)

PRESSURE_DIMENSION = UNITS.pascal.dimensionality

# VERDICT_* and the decision itself live in app.core (audit_verdict) so every interface shares one rule.

REASONS = {
    "UNSUPPORTED_UNIT": "Unit is not supported. The row was not compared.",
    "UNPARSED_ROW": "The row looks like a measurement but does not match the supported format. It was not compared.",
    "DUPLICATE_IDENTIFIER": "The same tag and property appear more than once in one document.",
    "DIMENSION_MISMATCH": "The two values have different physical dimensions. They cannot be compared.",
    "AMBIGUOUS_COMPARISON": "One file gives different values for this item, so no single value can be compared with the other file.",
    "TEMPERATURE_SEMANTICS": "The kind of temperature (absolute or difference) is not clear or not the same, so the values were not compared.",
}


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~&$:@^-])")


def escape_md(text) -> str:
    """Make untrusted text (file names, row text) safe to show in Markdown: no markup, no links, one line."""
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    return _MD_SPECIAL.sub(r"\\\1", text)


def evidence_html(ev) -> str:
    """The small evidence header. Everything that comes from a user file is HTML-escaped."""
    return ('<div class="sf-ev"><b>' + html.escape(ev.label) + "</b> · " + html.escape(ev.document)
            + '<br><span class="sf-loc">' + html.escape(ev.location) + "</span></div>")


@dataclass(frozen=True)
class Evidence:
    label: str      # "Document A" ...
    document: str   # name as given to the engine
    location: str   # human-readable location, honest for PDF pages
    excerpt: str    # verbatim source text
    quantity: str = ""   # the value and unit as written, e.g. "10 bar"
    si: str = ""         # the same value in SI, e.g. "1,000,000 Pa"


@dataclass
class ReviewCard:
    kind: str                        # "conflict" | "equivalent"
    title: str
    original: list                   # list[Evidence]
    issue: str
    explanation: str
    suggestion: Optional[str] = None  # reserved for P2 writing review
    decision: Optional[str] = None    # reserved for P2: None | "accepted" | "rejected"
    difference: Optional[str] = None  # formatted relative difference, numeric findings only
    summary: Optional[str] = None     # short one-line form, e.g. "12 L/min = 0.0002 m^3/s"


@dataclass
class CoverageItem:
    code: str
    summary: str
    location: str
    excerpt: str = ""
    informational: bool = False   # a note that does not reduce coverage (for example consistent repeats)
    evidence: list = field(default_factory=list)   # one dict per original row: document, location, excerpt (two-sided and repeat items)


@dataclass
class AuditView:
    verdict: str
    headline: str
    detail: str
    metrics: dict
    conflicts: list = field(default_factory=list)      # ReviewCard
    equivalents: list = field(default_factory=list)    # ReviewCard
    coverage: list = field(default_factory=list)       # CoverageItem that reduce coverage
    notes: list = field(default_factory=list)          # CoverageItem, informational
    internal: int = 0                                  # conflicts inside one file
    input_issues: list = field(default_factory=list)   # dict from ingest
    files: list = field(default_factory=list)          # str descriptions
    scope: str = ""
    tolerance: float = 0.0
    coverage_complete: bool = False   # the authoritative flag (core.audit_verdict), including input issues
    effective_tolerance: float = 0.0  # enforced allowed difference: max(requested tolerance, 1e-12); `tolerance` is the request


# Pint's names for SI base-unit combinations -> the symbols engineers use.
SI_SYMBOLS = {
    "kilogram / meter / second ** 2": "Pa", "meter ** 3 / second": "m³/s", "meter": "m", "second": "s",
    "kilogram": "kg", "kelvin": "K", "meter / second": "m/s", "meter ** 2": "m²", "meter ** 3": "m³",
    "kilogram / meter ** 3": "kg/m³", "kilogram * meter / second ** 2": "N",
    "kilogram * meter ** 2 / second ** 2": "J", "kilogram * meter ** 2 / second ** 3": "W",
    "dimensionless": "(dimensionless)", "ampere": "A", "1 / second": "Hz",
}


def si_symbol(si_unit: str) -> str:
    return SI_SYMBOLS.get(si_unit, si_unit)


def fmt_number(x: float) -> str:
    return format(x, ",.10g")


def fmt_pct(fraction: float) -> str:
    return format(fraction * 100, ".10g") + " %"


def allowed_text(requested: float, effective: float) -> str:
    """The allowed difference as enforced. When the numeric floor (1e-12 relative) raised the request, both are shown."""
    if effective == requested:
        return fmt_pct(effective)
    return f"{fmt_pct(effective)} (requested {fmt_pct(requested)}; values are never compared more finely than 1e-12)"


def describe_location(name: str, line: int) -> str:
    base, page = split_document_name(name)
    if page is None:
        return f"line {line}"
    return f"page {page}, line {line} of the text extracted from that page (not a visual line)"


def _evidence(label: str, rec: dict) -> Evidence:
    return Evidence(label, rec["document"], describe_location(rec["document"], rec["line"]), rec["excerpt"],
                    _quantity(rec), _si(rec))


def _doc_label(name: str, labels: dict) -> str:
    base, _ = split_document_name(name)
    return labels.get(name) or labels.get(base) or base


def _quantity(rec: dict) -> str:
    return f"{fmt_number(rec['value'])} {rec['unit']}"


def _si(rec: dict) -> str:
    return f"{fmt_number(rec['si_value'])} {si_symbol(rec['si_unit'])}"


def _title(item: dict) -> str:
    return f"{item['reference']['tag']} · {item['reference']['field']}"


def _label_of(rec: dict, labels: dict) -> str:
    return _doc_label(rec["document"], labels)


def _conflict_card(item: dict, labels: dict, tolerance: float, requested: float) -> ReviewCard:
    ref, oth = item["reference"], item["other"]
    internal = item["code"] == "INTERNAL_CONTRADICTION"
    la, lb = _label_of(ref, labels), _label_of(oth, labels)
    lower = "lower" if oth["si_value"] < ref["si_value"] else "higher"
    if internal:
        where = f"Both rows are in {la}."
        first, second, issue = "First row", "Second row", "One document contradicts itself"
    else:
        where = f"{la} and {lb} disagree." if la != lb else f"{ref['document']} and {oth['document']} disagree."
        first, second, issue = la, lb, "Same item, different values"
    explanation = (
        f"{where} The first row states {_quantity(ref)}. The second row states {_quantity(oth)}. "
        f"Converted to SI ({si_symbol(ref['si_unit'])}): {_si(ref)} and {_si(oth)}. "
        f"The second value is {lower} by {fmt_pct(item['relative_difference'])} "
        f"(difference divided by the larger value). The allowed difference is {allowed_text(requested, tolerance)}. "
        "SpecForge cannot tell which value is correct."
    )
    if not internal:
        explanation += " If a document repeats this item, the pair shown is the one with the largest difference between the two documents."
    if internal:
        explanation += " Because of this, nothing from this file was compared with the other file for this item."
    if UNITS.get_dimensionality(ref["dimension"]) == PRESSURE_DIMENSION:
        explanation += " Pressure basis (gauge or absolute) is not checked."
    return ReviewCard(
        "conflict", _title(item), [_evidence(first, ref), _evidence(second, oth)],
        issue, explanation, difference=fmt_pct(item["relative_difference"]))


def _equivalent_card(item: dict, labels: dict, tolerance: float, requested: float) -> ReviewCard:
    ref, oth = item["reference"], item["other"]
    explanation = (
        f"{_quantity(ref)} and {_quantity(oth)} convert to {_si(ref)} and {_si(oth)}. "
        f"The difference is {fmt_pct(item['relative_difference'])}, within the allowed {allowed_text(requested, tolerance)}. "
        "If a document repeats this item, the pair shown is the one with the largest difference, and every other pair is within the allowed difference."
    )
    same = f"{_quantity(ref)} = {_quantity(oth)}"
    if item["relative_difference"] > ZERO_TOLERANCE_FLOOR:      # more than rounding noise: not the same value, only within tolerance
        same = f"{_quantity(ref)} \u2248 {_quantity(oth)} (within tolerance, difference {fmt_pct(item['relative_difference'])})"
    return ReviewCard("equivalent", _title(item), [_evidence(_label_of(ref, labels), ref), _evidence(_label_of(oth, labels), oth)],
                      "No difference beyond the tolerance", explanation, difference=fmt_pct(item["relative_difference"]),
                      summary=same)


def _plural_lines(w: dict) -> str:
    docs = w.get("documents") or [w["document"]] * len(w["lines"])
    return "; ".join(f"{d}, {describe_location(d, n)}" for d, n in zip(docs, w["lines"]))


def _record(document: str, line: int, excerpt: str) -> dict:
    return {"document": document, "location": describe_location(document, line), "excerpt": excerpt}


def _two_rows(a: dict, b: dict) -> tuple:
    """Location text and one evidence record per original row, in the order the engine gives them."""
    records = [_record(a["document"], a["line"], a["excerpt"]), _record(b["document"], b["line"], b["excerpt"])]
    return " / ".join(f"{r['document']}, {r['location']}" for r in records), records


def _many_rows(w: dict) -> list:
    docs = w.get("documents") or [w["document"]] * len(w["lines"])
    return [_record(d, n, x) for d, n, x in zip(docs, w["lines"], w["excerpts"])]


def _coverage_items(result: dict) -> tuple:
    """Returns (gaps, notes). Gaps reduce coverage; notes only inform."""
    gaps, notes = [], []
    for w in result["warnings"]:
        code = w["code"]
        if code == "DIMENSION_MISMATCH":
            a, b = w["reference"], w["other"]
            loc, records = _two_rows(a, b)
            gaps.append(CoverageItem(code, f"{REASONS[code]} ({a['tag']} {a['field']}: {a['unit']} vs {b['unit']})", loc,
                                     a["excerpt"] + "\n" + b["excerpt"], evidence=records))
        elif code == "DUPLICATE_IDENTIFIER":
            kind = ("Repeated with consistent values: all repeats agree within the allowed difference, so the file counts as consistent. "
                    "The comparison with the other file uses its lowest and highest values."
                    if w.get("consistent", False) else "Repeated with different values.")
            item = CoverageItem(code, f"{REASONS[code]} {kind} ({w['tag']} {w['field']})", _plural_lines(w),
                                "\n".join(w["excerpts"]), informational=bool(w.get("consistent", False)), evidence=_many_rows(w))
            (notes if item.informational else gaps).append(item)
        elif code == "AMBIGUOUS_COMPARISON":
            gaps.append(CoverageItem(code, f"{REASONS[code]} ({w['tag']} {w['field']})", _plural_lines(w), "\n".join(w["excerpts"]),
                                     evidence=_many_rows(w)))
        elif code == "AMBIGUOUS_UNIT":
            alts = ", ".join(w["alternatives"])
            gaps.append(CoverageItem(code, f"Unit '{w['ambiguous_name']}' has more than one meaning. Write an explicit unit instead: {alts}. The row was not compared.",
                                     f"{w['document']}, {describe_location(w['document'], w['line'])}", w["excerpt"]))
        elif code == "TEMPERATURE_SEMANTICS":
            a, b = w["reference"], w["other"]
            loc, records = _two_rows(a, b)
            gaps.append(CoverageItem(code, f"{w['message']} ({a['tag']} {a['field']}: {a['unit']} vs {b['unit']})", loc,
                                     a["excerpt"] + "\n" + b["excerpt"], evidence=records))
        elif code == "LINE_TOO_LONG":
            gaps.append(CoverageItem(code, f"The line has {w['length']} characters. Lines over {w['limit']} characters are not read, so this row was not compared.",
                                     f"{w['document']}, {describe_location(w['document'], w['line'])}", w["excerpt"]))
        else:
            where = (f"{w['document']}, lines {w['line']}-{w['end_line']}" if w.get("end_line")
                     else f"{w['document']}, {describe_location(w['document'], w['line'])}")
            quoted = w.get("detail") in ("MULTILINE_QUOTED_FIELD", "UNCLOSED_QUOTE")   # the engine's own sentence names the span
            gaps.append(CoverageItem(code, w["reason"] if quoted else REASONS.get(code, code), where, w["excerpt"]))
    for u in result["unmatched"]:
        gaps.append(CoverageItem(
            "UNMATCHED_RECORD",
            f"{u['tag']} · {u['field']} appears in only one file, so nothing was compared.",
            f"{u['document']}, {describe_location(u['document'], u['line'])}", u["excerpt"]))
    return gaps, notes


def build_view(result: dict, labels: Optional[dict] = None, input_issues: Optional[list] = None,
               files: Optional[list] = None) -> AuditView:
    labels = labels or {}
    input_issues = list(input_issues or [])
    requested = result["tolerance"]
    tol = result.get("effective_tolerance", max(requested, ZERO_TOLERANCE_FLOOR))
    coverage, notes = _coverage_items(result)
    conflicts = [_conflict_card(f, labels, tol, requested) for f in result["findings"]]
    equivalents = [_equivalent_card(m, labels, tol, requested) for m in result["matches"]]
    gaps = len(coverage) + len(input_issues)
    n_conf, n_comp = len(conflicts), result["comparisons"]
    metrics = {
        "rows_read": result["records"], "compared": n_comp, "conflicts": n_conf,
        "equivalent": len(equivalents), "not_compared": gaps,
    }
    internal = sum(1 for f in result["findings"] if f["code"] == "INTERNAL_CONTRADICTION")
    verdict, complete = audit_verdict(result, extra_gaps=len(input_issues))
    if verdict == VERDICT_CONFLICTS:
        headline = f"{n_conf} conflict{'s' if n_conf != 1 else ''} found"
        if n_comp:
            headline += f" in {n_comp} compared measurement{'s' if n_comp != 1 else ''}"
        if internal:
            headline += f" ({internal} inside one document)"
        detail = "Check the evidence below. SpecForge reports what the documents say. It does not decide which document is right."
        if gaps:
            detail += (f" The audit is also incomplete: {gaps} item{'s were' if gaps != 1 else ' was'} not compared."
                       " Read the coverage section.")
    elif verdict == VERDICT_NONE:
        headline = "Nothing could be compared"
        detail = "No tag and property pair with a usable value was found in two places. This is not a successful audit. See the coverage section."
    elif verdict == VERDICT_PARTIAL:
        headline = f"No conflicts in the {n_comp} compared measurement{'s' if n_comp != 1 else ''}, but the audit is incomplete"
        detail = f"{gaps} item{'s were' if gaps != 1 else ' was'} not compared. Read the coverage section before relying on this result."
    else:
        headline = f"No conflicts in the {n_comp} compared measurement{'s' if n_comp != 1 else ''}"
        detail = "This covers only rows in the supported format. It is not a certification of the documents."
    return AuditView(verdict, headline, detail, metrics, conflicts, equivalents, coverage, notes, internal, input_issues,
                     list(files or []), result["scope"], requested, complete, tol)


def _where(ev) -> str:
    place = ev.location.split(",")[0] if ev.location.startswith("page") else ev.location
    return f"{ev.document} · {place}"


def table_rows(cards: list) -> list:
    """Flat rows for a table. The first and second row are named by what they are, not by a document letter."""
    rows = []
    for c in cards:
        a, b = c.original[0], c.original[1]
        internal = c.issue == "One document contradicts itself"
        rows.append({
            "Item": c.title,
            "Kind": "Inside one document" if internal else "Between documents",
            "First: source": (f"{a.label} · " if not internal else "") + _where(a), "First: text": a.excerpt,
            "Second: source": (f"{b.label} · " if not internal else "") + _where(b), "Second: text": b.excerpt,
        })
    return rows


def _md_cell(text: str) -> str:
    return escape_md(text)


def render_markdown(view: AuditView, generated_at: Optional[str] = None) -> str:
    e = escape_md
    out = ["# SpecForge audit report", ""]
    if generated_at:
        out += [f"Generated: {generated_at}", ""]
    out += [f"**Result: {e(view.headline)}**", "", e(view.detail), ""]
    out += [f"Verdict: {view.verdict}", "", f"Coverage complete: {'yes' if view.coverage_complete else 'no'}"
            + ("" if view.coverage_complete else f" ({view.metrics['not_compared']} items not compared)")
            + " (within the supported structured rows; not a certification of the documents)", ""]
    out += ["## Input files", ""]
    out += [f"- {e(f)}" for f in view.files] or ["- (none)"]
    out += ["", "## Summary", "", "| Measure | Count |", "| --- | --- |"]
    for key, label in (("rows_read", "Measurement rows read"), ("compared", "Measurements compared"),
                       ("conflicts", "Conflicts"), ("equivalent", "Equivalent after conversion"),
                       ("not_compared", "Items not compared")):
        out.append(f"| {label} | {view.metrics[key]} |")
    if view.effective_tolerance == view.tolerance:
        out += ["", f"Allowed difference: {fmt_pct(view.tolerance)}.", ""]
    else:
        out += ["", f"Allowed difference: requested {fmt_pct(view.tolerance)}, enforced {fmt_pct(view.effective_tolerance)} "
                    "(values are never compared more finely than 1e-12 relative).", ""]
    out += ["## Conflicts", ""]
    if not view.conflicts:
        out += ["None found among the compared measurements.", ""]
    for i, c in enumerate(view.conflicts, 1):
        out += [f"### {i}. {e(c.title)}", "", f"*{e(c.issue)}*", "", e(c.explanation), ""]
        for ev in c.original:
            out += [f"**{e(ev.label)}** — {e(ev.document)}, {e(ev.location)}", "", _fence(ev.excerpt), ""]
    out += ["## Verified equivalent after unit conversion", ""]
    if not view.equivalents:
        out += ["None.", ""]
    else:
        out += ["| Item | Document A | Document B | Difference |", "| --- | --- | --- | --- |"]
        for c in view.equivalents:
            a, b = c.original
            out.append(f"| {_md_cell(c.title)} | {_md_cell(a.document)}: {_md_cell(a.excerpt)} | {_md_cell(b.document)}: {_md_cell(b.excerpt)} | {c.difference} |")
        out.append("")
    out += ["## Not compared (coverage)", ""]
    if not view.coverage and not view.input_issues:
        out += ["Every supported row found was compared.", ""]
    for issue in view.input_issues:
        out.append(f"- **Input** — {e(issue['message'])}")
    for item in view.coverage:
        if len(item.evidence) > 1:                          # one line per original row: file, location, the whole excerpt
            out.append(f"- **{item.code}** — {e(item.summary)}")
            out += _evidence_lines(item)
            continue
        line = f"- **{item.code}** — {e(item.summary)} ({e(item.location)})"
        out.append(line + (f": {_inline_code(_one_line(item.excerpt))}" if item.excerpt else ""))
    if view.notes:
        out += ["", "## Notes (do not reduce coverage)", ""]
        for item in view.notes:
            if len(item.evidence) > 1:                      # consistent repeats: every original row, as for a gap
                out.append(f"- **{item.code}** — {e(item.summary)}")
                out += _evidence_lines(item)
                continue
            out.append(f"- **{item.code}** — {e(item.summary)} ({e(item.location)})")
    out += ["", "## Limits of this report", "", f"- {e(view.scope)}.",
            "- Only rows in the supported format are read: `TAG | property | number unit`, or comma-separated rows in .csv files.",
            "- SpecForge reports differences between documents. It does not decide which value is correct and does not certify any document.",
            "- PDF locations are page-level. Line numbers for a PDF refer to the extracted text of that page.", ""]
    return "\n".join(out)


_LINE_BREAKS = re.compile(r"\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


def _one_line(text: str) -> str:
    """An excerpt on one Markdown line. Every line break is replaced by a visible marker, so nothing is cut off silently."""
    return _LINE_BREAKS.sub(" \u23ce ", text)


def _evidence_lines(item) -> list:
    """One indented list entry per original row: file, location, the whole excerpt in a code span (line breaks marked)."""
    return [f"  - {escape_md(r['document'])}, {escape_md(r['location'])}: {_inline_code(_one_line(r['excerpt']))}" for r in item.evidence]


def _fence(text: str) -> str:
    """A code block that the text itself cannot close."""
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def _inline_code(text: str) -> str:
    ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"
