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
from .core import UNITS

PRESSURE_DIMENSION = UNITS.pascal.dimensionality

VERDICT_CONFLICTS = "CONFLICTS_FOUND"
VERDICT_PARTIAL = "NO_CONFLICTS_PARTIAL_COVERAGE"
VERDICT_MATCH = "NO_CONFLICTS_IN_COMPARED_ROWS"
VERDICT_NONE = "NOTHING_COMPARED"

REASONS = {
    "UNSUPPORTED_UNIT": "Unit is not supported. The row was not compared.",
    "UNPARSED_ROW": "The row looks like a measurement but does not match the supported format. It was not compared.",
    "DUPLICATE_IDENTIFIER": "The same tag and property appear more than once in one document.",
    "DIMENSION_MISMATCH": "The two values have different physical dimensions. They cannot be compared.",
    "AMBIGUOUS_COMPARISON": "One file gives different values for this item, so no single value can be compared with the other file.",
    "TEMPERATURE_SEMANTICS": "A temperature difference and an absolute temperature cannot be compared.",
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


def describe_location(name: str, line: int) -> str:
    base, page = split_document_name(name)
    if page is None:
        return f"line {line}"
    return f"page {page}, line {line} of the text extracted from that page (not a visual line)"


def _evidence(label: str, rec: dict) -> Evidence:
    return Evidence(label, rec["document"], describe_location(rec["document"], rec["line"]), rec["excerpt"])


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


def _conflict_card(item: dict, labels: dict, tolerance: float) -> ReviewCard:
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
        f"(difference divided by the larger value). The allowed difference is {fmt_pct(tolerance)}. "
        "SpecForge cannot tell which value is correct."
    )
    if internal:
        explanation += " Because of this, nothing from this file was compared with the other file for this item."
    if UNITS.get_dimensionality(ref["dimension"]) == PRESSURE_DIMENSION:
        explanation += " Pressure basis (gauge or absolute) is not checked."
    return ReviewCard(
        "conflict", _title(item), [_evidence(first, ref), _evidence(second, oth)],
        issue, explanation, difference=fmt_pct(item["relative_difference"]))


def _equivalent_card(item: dict, labels: dict, tolerance: float) -> ReviewCard:
    ref, oth = item["reference"], item["other"]
    explanation = (
        f"{_quantity(ref)} and {_quantity(oth)} convert to {_si(ref)} and {_si(oth)}. "
        f"The difference is {fmt_pct(item['relative_difference'])}, within the allowed {fmt_pct(tolerance)}."
    )
    return ReviewCard("equivalent", _title(item), [_evidence(_label_of(ref, labels), ref), _evidence(_label_of(oth, labels), oth)],
                      "No difference beyond the tolerance", explanation, difference=fmt_pct(item["relative_difference"]),
                      summary=f"{_quantity(ref)} = {_quantity(oth)}")


def _plural_lines(w: dict) -> str:
    docs = w.get("documents") or [w["document"]] * len(w["lines"])
    return "; ".join(f"{d}, {describe_location(d, n)}" for d, n in zip(docs, w["lines"]))


def _coverage_items(result: dict) -> tuple:
    """Returns (gaps, notes). Gaps reduce coverage; notes only inform."""
    gaps, notes = [], []
    for w in result["warnings"]:
        code = w["code"]
        if code == "DIMENSION_MISMATCH":
            a, b = w["reference"], w["other"]
            loc = f"{describe_location(a['document'], a['line'])} / {describe_location(b['document'], b['line'])}"
            gaps.append(CoverageItem(code, f"{REASONS[code]} ({a['tag']} {a['field']}: {a['unit']} vs {b['unit']})", loc,
                                     a["excerpt"] + "\n" + b["excerpt"]))
        elif code == "DUPLICATE_IDENTIFIER":
            kind = ("Repeated with consistent values: the first row represents the file."
                    if w.get("consistent", False) else "Repeated with different values.")
            item = CoverageItem(code, f"{REASONS[code]} {kind} ({w['tag']} {w['field']})", _plural_lines(w),
                                "\n".join(w["excerpts"]), informational=bool(w.get("consistent", False)))
            (notes if item.informational else gaps).append(item)
        elif code == "AMBIGUOUS_COMPARISON":
            gaps.append(CoverageItem(code, f"{REASONS[code]} ({w['tag']} {w['field']})", _plural_lines(w), "\n".join(w["excerpts"])))
        elif code == "AMBIGUOUS_UNIT":
            alts = ", ".join(w["alternatives"])
            gaps.append(CoverageItem(code, f"Unit '{w['ambiguous_name']}' has more than one meaning. Write an explicit unit instead: {alts}. The row was not compared.",
                                     f"{w['document']}, {describe_location(w['document'], w['line'])}", w["excerpt"]))
        elif code == "TEMPERATURE_SEMANTICS":
            a, b = w["reference"], w["other"]
            gaps.append(CoverageItem(code, f"{w['message']} ({a['tag']} {a['field']}: {a['unit']} vs {b['unit']})",
                                     f"{describe_location(a['document'], a['line'])} / {describe_location(b['document'], b['line'])}",
                                     a["excerpt"] + "\n" + b["excerpt"]))
        else:
            gaps.append(CoverageItem(code, REASONS.get(code, code), f"{w['document']}, {describe_location(w['document'], w['line'])}", w["excerpt"]))
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
    tol = result["tolerance"]
    coverage, notes = _coverage_items(result)
    conflicts = [_conflict_card(f, labels, tol) for f in result["findings"]]
    equivalents = [_equivalent_card(m, labels, tol) for m in result["matches"]]
    gaps = len(coverage) + len(input_issues)
    n_conf, n_comp = len(conflicts), result["comparisons"]
    metrics = {
        "rows_read": result["records"], "compared": n_comp, "conflicts": n_conf,
        "equivalent": len(equivalents), "not_compared": gaps,
    }
    internal = sum(1 for f in result["findings"] if f["code"] == "INTERNAL_CONTRADICTION")
    if n_conf:
        verdict = VERDICT_CONFLICTS
        headline = f"{n_conf} conflict{'s' if n_conf != 1 else ''} found"
        if n_comp:
            headline += f" in {n_comp} compared measurement{'s' if n_comp != 1 else ''}"
        if internal:
            headline += f" ({internal} inside one document)"
        detail = "Check the evidence below. SpecForge reports what the documents say. It does not decide which document is right."
    elif n_comp == 0:
        verdict = VERDICT_NONE
        headline = "Nothing could be compared"
        detail = "No tag and property pair with a usable value was found in two places. This is not a successful audit. See the coverage section."
    elif gaps:
        verdict = VERDICT_PARTIAL
        headline = f"No conflicts in the {n_comp} compared measurement{'s' if n_comp != 1 else ''}, but the audit is incomplete"
        detail = f"{gaps} item{'s were' if gaps != 1 else ' was'} not compared. Read the coverage section before relying on this result."
    else:
        verdict = VERDICT_MATCH
        headline = f"No conflicts in the {n_comp} compared measurement{'s' if n_comp != 1 else ''}"
        detail = "This covers only rows in the supported format. It is not a certification of the documents."
    return AuditView(verdict, headline, detail, metrics, conflicts, equivalents, coverage, notes, internal, input_issues,
                     list(files or []), result["scope"], tol)


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
    out += ["## Input files", ""]
    out += [f"- {e(f)}" for f in view.files] or ["- (none)"]
    out += ["", "## Summary", "", "| Measure | Count |", "| --- | --- |"]
    for key, label in (("rows_read", "Measurement rows read"), ("compared", "Measurements compared"),
                       ("conflicts", "Conflicts"), ("equivalent", "Equivalent after conversion"),
                       ("not_compared", "Items not compared")):
        out.append(f"| {label} | {view.metrics[key]} |")
    out += ["", f"Allowed difference: {fmt_pct(view.tolerance)}.", ""]
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
        line = f"- **{item.code}** — {e(item.summary)} ({e(item.location)})"
        out.append(line + (f": {_inline_code(item.excerpt.splitlines()[0])}" if item.excerpt else ""))
    if view.notes:
        out += ["", "## Notes (do not reduce coverage)", ""]
        for item in view.notes:
            out.append(f"- **{item.code}** — {e(item.summary)} ({e(item.location)})")
    out += ["", "## Limits of this report", "", f"- {e(view.scope)}.",
            "- Only rows in the supported format are read: `TAG | property | number unit`, or comma-separated rows in .csv files.",
            "- SpecForge reports differences between documents. It does not decide which value is correct and does not certify any document.",
            "- PDF locations are page-level. Line numbers for a PDF refer to the extracted text of that page.", ""]
    return "\n".join(out)


def _fence(text: str) -> str:
    """A code block that the text itself cannot close."""
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def _inline_code(text: str) -> str:
    ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"
