"""Deterministic engineering quantity checker. No external AI or network calls.

Accepted rows (exact format only, no free-form inference):
  TAG | property | number unit          (any text file; optional leading/trailing pipe)
  TAG,property,number unit              (files named *.csv only)
  TAG,property,number,unit              (files named *.csv only)

Quoted fields are not part of the grammar: a measurement-like row that uses them is not compared and is
reported as UNPARSED_ROW. The same holds for other recognizable unsupported layouts (four-cell tables, `max 10 bar`, comma
rows outside .csv): recognized and listed, never parsed or compared. A line over MAX_LINE_CHARS is reported as LINE_TOO_LONG.
Everything the checker recognizes but cannot use is reported (UNSUPPORTED_UNIT, UNPARSED_ROW,
DUPLICATE_IDENTIFIER, DIMENSION_MISMATCH, unmatched records), so a clean status is not claimed for rows it recognized
but could not compare. Rows in layouts it cannot recognize may be missed (see README, "What is recognized").
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict, dataclass
import functools
import math
import re
from typing import Mapping
import pint

UNITS = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)

_NUM = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_UNIT = r"[A-Za-z°µμ][\w°µμ/%^.*-]*"
ENTRY = re.compile(
    r"^\s*\|?\s*(?P<tag>[A-Za-z][\w-]{1,31})\s*\|\s*"
    r"(?P<field>[A-Za-z][\w -]{1,47})\s*\|\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*"
    r"(?P<unit>[A-Za-z°µμ][\w°µμ/%^.*-]*)\s*\|?\s*$"
)
CSV_ENTRY = re.compile(
    r"^\s*(?P<tag>[A-Za-z][\w-]{1,31})\s*,\s*(?P<field>[A-Za-z][\w -]{1,47})\s*,\s*"
    r"(?P<value>%s)\s*,?\s*(?P<unit>%s)\s*$" % (_NUM, _UNIT)
)
# Only \n, \r\n and \r end a line, as in a text editor. str.splitlines() would also split on
# form feed, U+0085, U+2028 and others, which shifts reported line numbers.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
ALIASES = {"°C": "degC", "°F": "degF", "μm": "micrometer", "µm": "micrometer"}

# --- D04: unit names whose meaning depends on the country or the field. They are refused, not guessed. ---
# Matching is on the text the author wrote (lower-case letter groups), because Pint maps `ton` and `short_ton`
# to the same unit and cannot tell them apart. Explicit alternatives are always accepted.
AMBIGUOUS_UNITS = {
    "ton": ("short_ton", "long_ton", "metric_ton"),
    "gallon": ("US_liquid_gallon", "imperial_gallon"),
    "quart": ("US_liquid_quart", "imperial_quart"),
    "pint": ("imperial_pint", "liter"),
    "cal": ("thermochemical_calorie", "international_calorie"),
    "calorie": ("thermochemical_calorie", "international_calorie"),
    "hp": ("metric_horsepower", "electrical_horsepower", "boiler_horsepower"),
    "horsepower": ("metric_horsepower", "electrical_horsepower", "boiler_horsepower"),
    "btu": ("international_british_thermal_unit", "thermochemical_british_thermal_unit"),
    # H01: Pint reads `bbl`/`barrel` as the US fluid barrel (31.5 US gal = 119.24 L). Process engineering usually means
    # the oil barrel (42 US gal = 158.99 L). The two differ by 25 %, so the bare name is refused.
    "barrel": ("oil_barrel", "imperial_barrel", "beer_barrel", "dry_barrel", "liter"),
}
# Token grammar: Unicode letters/underscore; digits and expression operators delimit tokens.
# Registry prefix/suffix parsing preserves qualified names. Legacy industry MM/uppercase
# spellings remain refused; unknown prefixes on an otherwise barrel-shaped name fail closed.
_BARREL_WORD = re.compile(r"[a-zµμ]*(?:bbl|barrel)s?", re.IGNORECASE)
_AMBIGUOUS_ALIASES = {"tons": "ton", "gal": "gallon", "gallons": "gallon", "quarts": "quart", "qt": "quart",
                      "pints": "pint", "pt": "pint", "calories": "calorie", "btus": "btu"}

# --- D05: temperature differences are not absolute temperatures ---
_INTERVAL_WORDS = {"delta", "difference", "differential", "diff", "rise", "drop", "interval", "dt"}
_ABSOLUTE_WORDS = {"temperature", "temp"}
_OFFSET_TO_DELTA = {"degree_Celsius": "delta_degC", "degree_Fahrenheit": "delta_degF"}
MAX_CHARS = 200_000
MAX_LINES = 5_000
# Ingress guard (public hosting). The row regular expressions below are not linear-time on long runs of whitespace
# (measured on the frozen baseline: one line of ~100,000 spaces took ~42 s, growth ~quadratic). Blank and delimiter-free
# lines therefore skip every regular expression, and a delimiter-bearing line longer than this is not inspected: it is
# reported as LINE_TOO_LONG (a visible coverage gap), never dropped. Real engineering rows are far shorter; the limit is
# applied per line, so the worst case for one document stays bounded (about MAX_CHARS * MAX_LINE_CHARS steps).
MAX_LINE_CHARS = 2_000
_EXCERPT_CHARS = 200
SCOPE = "Structured rows only; not a general PDF/P&ID semantic audit"


@dataclass(frozen=True)
class Observation:
    document: str
    line: int
    tag: str
    field: str
    value: float
    unit: str
    si_value: float
    si_unit: str
    dimension: str
    excerpt: str
    quantity_kind: str = ""   # "absolute" | "interval" | "unknown" for temperatures; "" otherwise
    dimension_key: str = ""   # optional informational serialization, never an equality oracle

    @property
    def dimensionality(self):
        """Actual Pint structure; property stays out of dataclass/JSON serialization."""
        return UNITS.Quantity(1, ALIASES.get(self.unit, self.unit)).dimensionality


def dimension_key(dimensionality) -> str:
    """Informational sorted [dimension]^exponent terms, or 'dimensionless'.

    Integer exponents retain the prior format; fractional floats use round-trip repr.
    Scientific equality must use Pint dimensionality objects, never this string.
    """
    items = sorted((str(name), float(power)) for name, power in dimensionality.items())
    return " ".join(f"{name}^{int(power) if power.is_integer() else repr(power)}"
                    for name, power in items) or "dimensionless"


def split_lines(text: str) -> list[str]:
    """Split on editor line breaks. A final line break does not create an extra line."""
    parts = _LINE_BREAK.split(text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _is_csv(name: str) -> bool:
    return name.lower().split(":page-")[0].endswith(".csv")


_NUMBER_START = re.compile(r"^\s*(?:approx\.?|about|ca\.?|[~≈<>≤≥±+\-−=])*\s*[.,]?\d")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


# Quote characters (ASCII/typographic). Quoting is NOT part of the supported row grammar: a row that uses it is
# never compared, but it must be reported (issue #10) instead of vanishing from the audit.
_QUOTE_CHARS = "\"\u201c\u201d\u201e'\u2018\u2019"
_DOUBLE_QUOTES = re.compile("[\"\u201c\u201d\u201e]")
_TAG_LIKE = re.compile(r"[A-Za-z][\w-]{1,31}")


def _unquote(cell: str) -> str:
    return cell.strip().strip(_QUOTE_CHARS).strip()


def _looks_like_three_cell_row(raw: str, csv_file: bool) -> bool:
    """A line with 3+ cells whose third cell STARTS like a number, but which no row pattern accepted.

    A heading such as `value (2026 rev)` holds a digit but does not start with one, so it is not a measurement.
    A quoted row (`"P-101","flow","99 L/min"`) is recognised structurally only: the quote characters are removed
    to see whether the line is shaped like a measurement. Nothing is parsed or compared from it.
    """
    if csv_file:
        cells = raw.split(",")
    else:
        body = raw.strip()
        body = body[1:] if body.startswith("|") else body
        body = body[:-1] if body.endswith("|") else body
        cells = body.split("|")
    if len(cells) >= 3 and _NUMBER_START.match(cells[2]):
        return True
    if not any(q in raw for q in _QUOTE_CHARS):
        return False                                    # unquoted lines keep the rule above, exactly
    if not csv_file:
        return len(cells) >= 3 and bool(_NUMBER_START.match(_unquote(cells[2])))
    # CSV: ignore quoting and embedded commas (a quoted cell may hold a comma or be unterminated).
    flat = [_unquote(c) for c in _DOUBLE_QUOTES.sub("", raw).split(",")]
    return (len(flat) >= 3 and bool(_TAG_LIKE.fullmatch(flat[0]))
            and any(_NUMBER_START.match(c) for c in flat[2:]))


# --- Issue #12: recognition of UNSUPPORTED measurement-like layouts (recognition only; nothing here is parsed or compared) ---
# A "measurement cell" is a whole cell holding a number, optionally led by a qualifier word or symbol and optionally
# followed by one unit-like word: `10`, `10 bar`, `max 10 bar`, `~10 bar`, `0.5 m^3/s`. A cell such as `10 bar design` or
# `value is 10 bar` is NOT one (see the known-undetected tests). A tag is not required to contain a digit: PUMP is legal.
_QUALIFIER = r"(?:(?:max|min|nom|nominal|typ|typical|approx|approximately|about|ca)\.?\s*|[~≈<>≤≥±+\-−=]\s*)*"
_MEASURE_CELL = re.compile(_QUALIFIER + r"(?:\d+(?:\.\d+)?|\.\d+)\s*(?:[A-Za-z°µμ%][\w°µμ/%^.*-]*)?", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][\w.-]{0,31}")


_NUMBER_ONLY = re.compile(_QUALIFIER + r"(?:\d+(?:\.\d+)?|\.\d+)", re.IGNORECASE)
_UNIT_TOKEN = re.compile(r"[A-Za-z°µμ%][\w°µμ/%^.*-]{0,23}")
# Gauge/absolute pressure spellings are not supported units (README), but a column that holds them is still a unit column.
_PRESSURE_BASIS_UNIT = re.compile(r"(?:mbar|bar|psi|kpa|mpa|pa)[ga]", re.IGNORECASE)


@functools.lru_cache(maxsize=512)
def _unit_like(cell: str) -> bool:
    """True when a whole cell is a unit the engine knows, refuses as ambiguous, or a gauge/absolute pressure spelling."""
    if not _UNIT_TOKEN.fullmatch(cell):
        return False
    if _PRESSURE_BASIS_UNIT.fullmatch(cell) or ambiguous_unit(cell):
        return True
    try:
        UNITS.Quantity(1, ALIASES.get(cell, cell))
        return True
    except Exception:  # unknown unit word: not a unit column
        return False


def _has_measurement_cell(cells: list, first_cell=None) -> bool:
    """3+ cells and a measurement cell somewhere from the third cell on. `first_cell` optionally constrains cell 1.

    A BARE number (no unit word) in the fourth cell or later counts only when the cell before it is a unit. Without that
    rule, quantity, page and year columns of ordinary tables (`| P-101 | Pump | Grundfos | 2 |`) would be reported."""
    if len(cells) < 3:
        return False
    if first_cell is not None and not first_cell.fullmatch(cells[0]):
        return False
    for index in range(2, len(cells)):
        cell = cells[index]
        if not _MEASURE_CELL.fullmatch(cell):
            continue
        if index >= 3 and _NUMBER_ONLY.fullmatch(cell) and not _unit_like(cells[index - 1]):
            continue
        return True
    return False


def _looks_like_unsupported_layout(raw: str, csv_file: bool) -> bool:
    """Four-cell tables, `max 10 bar`, quoted pipe rows in a .csv, comma rows outside .csv (txt, md, MCP).

    Pipe rows: no constraint on the first cell (the baseline three-cell rule has none either).
    Comma rows: in a .csv file no constraint (comma rows are that file type's own layout); elsewhere the first cell must
    be a bare identifier, so prose such as `Note: pumps, valves, and 3 spares` or `Prepared by Acme, Ltd, 2026` stays quiet.
    This is a shape heuristic. Its precision and recall have NOT been measured."""
    text = raw.strip()
    if "|" in text:
        body = text[1:] if text.startswith("|") else text
        body = body[:-1] if body.endswith("|") else body
        if _has_measurement_cell([_unquote(c) for c in body.split("|")]):
            return True
    if "," in text:
        cells = [_unquote(c) for c in _DOUBLE_QUOTES.sub("", text).split(",")]
        if _has_measurement_cell(cells, None if csv_file else _IDENTIFIER):
            return True
    return False


def _looks_like_row(raw: str, csv_file: bool) -> bool:
    """True when a line that no supported pattern accepted is still recognizably a measurement row. Never parses it."""
    return _looks_like_three_cell_row(raw, csv_file) or _looks_like_unsupported_layout(raw, csv_file)


def ambiguous_unit(unit: str):
    """Return (ambiguous_name, alternatives) when the unit text uses a regional or field-dependent name, else None."""
    for word in re.findall(r"[^\W\d]+", unit):             # `short_ton` is one name; it is not the word `ton`
        key = word.lower()
        # Ask Pint about every whole token, including all installed SI/binary prefixes.
        candidates = UNITS.parse_unit_name(word, case_sensitive=False)
        barrel = any(base == "barrel" for _, base, _ in candidates)
        # Unknown barrel-shaped spellings are refused only if no unrelated named unit
        # resolves. Qualified names (underscore) cannot match this fallback grammar.
        barrel = barrel or (not candidates and bool(_BARREL_WORD.fullmatch(word)))
        key = "barrel" if barrel else _AMBIGUOUS_ALIASES.get(key, key)
        if key in AMBIGUOUS_UNITS:
            return word, AMBIGUOUS_UNITS[key]
    return None


def _words(text: str) -> set:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # dT -> d T ; deltaT -> delta T
    return {w.lower() for w in re.findall(r"[A-Za-z]+", spaced)} | {w.lower() for w in re.findall(r"[A-Za-z]+", text)}


def _temperature_kind(base_names: list, field: str) -> str:
    """Classify a pure-temperature quantity from its unit and its property name."""
    words = _words(field)
    interval_property = bool(words & _INTERVAL_WORDS)
    if any(n.startswith("delta_") for n in base_names):
        return "interval"
    if any(n in _OFFSET_TO_DELTA for n in base_names):
        return "interval" if interval_property else "absolute"
    if interval_property:
        return "interval"
    return "absolute" if words & _ABSOLUTE_WORDS else "unknown"


def _parse(name: str, text: str):
    if len(text) > MAX_CHARS:
        raise ValueError(f"{name}: maximum {MAX_CHARS} characters / {MAX_LINES} lines")
    lines = split_lines(text)
    if len(lines) > MAX_LINES:
        raise ValueError(f"{name}: maximum {MAX_CHARS} characters / {MAX_LINES} lines")
    csv_file = _is_csv(name)
    records: list[Observation] = []
    warnings: list[dict] = []
    coverage = {"lines": len(lines), "rows_parsed": 0, "rows_unsupported_unit": 0, "rows_ambiguous_unit": 0, "rows_unparsed": 0,
                "rows_too_long": 0}
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue                       # ingress: a blank line is never a row (linear time, no regular expression)
        if "|" not in raw and "," not in raw:
            continue                       # ingress: ENTRY needs a pipe and CSV_ENTRY a comma, so this cannot be a row
        if len(raw) > MAX_LINE_CHARS:
            text = raw.strip()
            coverage["rows_too_long"] += 1
            warnings.append({"code": "LINE_TOO_LONG", "document": name, "line": line_number,
                             "excerpt": text[:_EXCERPT_CHARS] + ("\u2026" if len(text) > _EXCERPT_CHARS else ""),
                             "length": len(raw), "limit": MAX_LINE_CHARS,
                             "reason": f"the line is longer than {MAX_LINE_CHARS:,} characters; it was not read and not compared"})
            continue
        next_line = lines[line_number] if line_number < len(lines) else ""
        if ("---" in next_line and raw.count("|") >= 2 and len(next_line) <= MAX_LINE_CHARS
                and _TABLE_SEPARATOR.match(next_line)):
            continue                       # D06: the line above a Markdown table separator is the table heading, never a row
        match = ENTRY.fullmatch(raw) or (CSV_ENTRY.fullmatch(raw) if csv_file else None)
        if not match:
            if _looks_like_row(raw, csv_file):
                coverage["rows_unparsed"] += 1
                quoted = any(q in raw for q in _QUOTE_CHARS)
                comma_outside_csv = not csv_file and "," in raw and "|" not in raw
                warnings.append({"code": "UNPARSED_ROW", "document": name, "line": line_number, "excerpt": raw.strip(),
                                 "reason": ("row uses quoted fields, which are not supported; it was not compared" if quoted
                                            else "comma-separated rows are read only from .csv files; it was not compared" if comma_outside_csv
                                            else "row does not match the supported format")})
            continue
        tag, field = match["tag"], match["field"].strip()
        value, unit = float(match["value"]), match["unit"]
        ambiguous = ambiguous_unit(unit)
        if ambiguous:
            word, alternatives = ambiguous
            coverage["rows_ambiguous_unit"] += 1
            warnings.append({
                "code": "AMBIGUOUS_UNIT", "document": name, "line": line_number, "excerpt": raw.strip(),
                "unit": unit, "ambiguous_name": word, "alternatives": list(alternatives),
                "reason": "the unit name has more than one meaning",
            })
            continue
        try:
            quantity = UNITS.Quantity(value, ALIASES.get(unit, unit))
            kind = ""
            if str(quantity.dimensionality) == "[temperature]":
                names = [n for n, _ in quantity.unit_items()]
                kind = _temperature_kind(names, field)
                if kind == "interval" and any(n in _OFFSET_TO_DELTA for n in names):
                    quantity = UNITS.Quantity(value, _OFFSET_TO_DELTA[names[0]])     # a rise of 5 degC is 5 K, not 278.15 K
            base = quantity.to_base_units()
            numeric = float(base.magnitude)
            if not math.isfinite(numeric):
                raise ValueError("non-finite converted value")
            dimension, base_units = str(quantity.dimensionality), str(base.units)
            key = dimension_key(quantity.dimensionality)
        except Exception as exc:  # any parser/conversion failure means: unit not usable, never a pass
            coverage["rows_unsupported_unit"] += 1
            warnings.append({
                "code": "UNSUPPORTED_UNIT", "document": name, "line": line_number,
                "excerpt": raw.strip(), "reason": type(exc).__name__,
            })
            continue
        coverage["rows_parsed"] += 1
        records.append(Observation(name, line_number, tag, field, value, unit, numeric,
                                   base_units, dimension, raw.strip(), kind, key))
    return records, warnings, coverage


def parse_document(name: str, text: str) -> tuple[list[Observation], list[dict]]:
    """Parse supported rows. Returns (records, warnings)."""
    records, warnings, _ = _parse(name, text)
    return records, warnings


def source_name(document: str) -> str:
    """The uploaded file a document name belongs to: `plan.pdf:page-3` -> `plan.pdf`; `spec.md` -> `spec.md`."""
    base, sep, page = document.rpartition(":page-")
    return base if sep and page.isdigit() else document


def _temperature_pair(ref: Observation, other: Observation):
    """D05. Returns None when the pair may be compared, else (detail, message)."""
    if ref.dimension != "[temperature]":
        return None
    kinds = {ref.quantity_kind, other.quantity_kind}
    if kinds == {"interval", "absolute"}:
        return ("interval_vs_absolute", "One value is a temperature difference and the other is an absolute temperature. They are not comparable.")
    if "interval" in kinds and "unknown" in kinds:
        return ("uncertain", "One value is a temperature difference. The other (for example a plain K) could be a temperature or a difference. "
                             "Name the property 'temperature difference' or use delta_degC to make the meaning explicit.")
    return None


# Authoritative audit verdicts. `status` below keeps the original comparison outcome; `verdict` is what consumers
# should branch on. The presentation layer (app/report.py) re-exports these names and uses `audit_verdict`.
VERDICT_CONFLICTS = "CONFLICTS_FOUND"
VERDICT_PARTIAL = "NO_CONFLICTS_PARTIAL_COVERAGE"
VERDICT_MATCH = "NO_CONFLICTS_IN_COMPARED_ROWS"
VERDICT_NONE = "NOTHING_COMPARED"


def count_coverage_gaps(result: Mapping) -> int:
    """Items that reduce coverage: every warning except a consistent repeat (informational), plus unmatched rows."""
    gaps = sum(1 for w in result["warnings"]
               if not (w["code"] == "DUPLICATE_IDENTIFIER" and w.get("consistent", False)))
    return gaps + len(result["unmatched"])


def audit_verdict(result: Mapping, extra_gaps: int = 0) -> tuple[str, bool]:
    """Single source of truth for (verdict, coverage_complete).

    `coverage_complete` is True only when rows were compared and nothing in the supported structured-row scope was left
    out (no unparsed, unmatched, unusable or ambiguous rows). It is not a certification of the documents.
    Precedence: conflicts first (coverage_complete still reports gaps), then nothing compared, then partial, then match.
    `extra_gaps` lets a caller add gaps core cannot see (for example files that could not be read).
    """
    gaps = count_coverage_gaps(result) + extra_gaps
    complete = gaps == 0 and result["comparisons"] > 0
    if result["findings"]:
        return VERDICT_CONFLICTS, complete
    if result["comparisons"] == 0:
        return VERDICT_NONE, False
    return (VERDICT_PARTIAL if gaps else VERDICT_MATCH), complete


def audit_documents(documents: Mapping[str, str], tolerance: float = 0.01) -> dict:
    """Compare equal TAG+field measurements across uploaded files, reporting evidence from both rows.

    A *source* is one uploaded file; the pages of a PDF are separate documents of the same source. Repeated
    records inside one source are never presented as a comparison between two sources:
    - consistent repeats count once (the first record represents the source) and are noted;
    - inconsistent repeats are an INTERNAL_CONTRADICTION and make the cross-source comparison ambiguous.
    """
    if not (0 <= tolerance <= 0.2) or not math.isfinite(tolerance):
        raise ValueError("tolerance must be between 0 and 0.2")
    if not documents or len(documents) > 12:
        raise ValueError("submit 1..12 documents")
    all_records: list[Observation] = []
    warnings: list[dict] = []
    coverage: dict[str, dict] = {}
    for name, text in documents.items():
        if not isinstance(text, str):
            raise ValueError(f"document {name} must contain UTF-8 text")
        parsed, doc_warnings, doc_coverage = _parse(name, text)
        all_records.extend(parsed)
        warnings.extend(doc_warnings)
        coverage[name] = doc_coverage
    groups: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for record in all_records:
        groups[(record.tag.casefold(), record.field.casefold())].append(record)
    findings: list[dict] = []
    matches: list[dict] = []
    unmatched: list[dict] = []
    group_warnings: list[dict] = []
    comparisons = 0

    def relative_error(a: Observation, b: Observation) -> float:
        denominator = max(abs(a.si_value), abs(b.si_value))
        return abs(a.si_value - b.si_value) / denominator if denominator else 0.0

    for (tag, field), records in sorted(groups.items()):
        by_source: dict[str, list[Observation]] = defaultdict(list)
        for record in records:
            by_source[source_name(record.document)].append(record)
        ambiguous = False
        representatives: list[Observation] = []
        for source, same in by_source.items():
            representatives.append(same[0])
            if len(same) == 1:
                continue
            first, consistent = same[0], True
            for other in same[1:]:
                if first.dimensionality != other.dimensionality:
                    consistent = False
                    group_warnings.append({"code": "DIMENSION_MISMATCH", "tag": tag, "field": field,
                                           "reference": asdict(first), "other": asdict(other)})
                    continue
                error = relative_error(first, other)
                if error > tolerance:
                    consistent = False
                    findings.append({"code": "INTERNAL_CONTRADICTION", "scope": "same_source", "tag": tag, "field": field,
                                     "relative_difference": round(error, 10),
                                     "reference": asdict(first), "other": asdict(other)})
            group_warnings.append({
                "code": "DUPLICATE_IDENTIFIER", "document": source, "tag": same[0].tag, "field": same[0].field,
                "lines": [r.line for r in same], "excerpts": [r.excerpt for r in same],
                "documents": [r.document for r in same], "consistent": consistent,
            })
            if not consistent:
                ambiguous = True
        if len(by_source) < 2:
            unmatched.extend({
                "code": "UNMATCHED_RECORD", "tag": r.tag, "field": r.field, "document": r.document,
                "line": r.line, "excerpt": r.excerpt, "value": r.value, "unit": r.unit,
            } for r in records)
            continue
        if ambiguous:
            group_warnings.append({
                "code": "AMBIGUOUS_COMPARISON", "tag": tag, "field": field,
                "sources": list(by_source), "excerpts": [r.excerpt for r in records],
                "documents": [r.document for r in records], "lines": [r.line for r in records],
                "reason": "a source contradicts itself, so no value can represent it",
            })
            continue
        reference = representatives[0]
        for other in representatives[1:]:
            if reference.dimensionality != other.dimensionality:
                group_warnings.append({"code": "DIMENSION_MISMATCH", "tag": tag, "field": field,
                                       "reference": asdict(reference), "other": asdict(other)})
                continue
            problem = _temperature_pair(reference, other)
            if problem:
                group_warnings.append({"code": "TEMPERATURE_SEMANTICS", "detail": problem[0], "message": problem[1],
                                       "tag": tag, "field": field, "reference": asdict(reference), "other": asdict(other)})
                continue
            comparisons += 1
            error = relative_error(reference, other)
            item = {"scope": "cross_source", "tag": tag, "field": field, "relative_difference": round(error, 10),
                    "reference": asdict(reference), "other": asdict(other)}
            if error > tolerance:
                findings.append({"code": "VALUE_MISMATCH", **item})
            else:
                matches.append({"code": "WITHIN_TOLERANCE", **item})
    warnings.extend(group_warnings)
    result = {
        "status": "DIFFERENCES" if findings else ("NO_COMPARABLE_ROWS" if not comparisons else "NO_DIFFERENCES_FOUND"),
        "records": len(all_records), "comparisons": comparisons,
        "tolerance": tolerance, "findings": findings, "matches": matches,
        "unmatched": unmatched, "warnings": warnings, "coverage": coverage,
        "scope": SCOPE,
    }
    result["verdict"], result["coverage_complete"] = audit_verdict(result)
    return result
