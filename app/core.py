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
import json
import math
import re
import sys
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
# Pint canonical names that the policy keys resolve to. Used only to fail closed when a prefix cannot be located.
_AMBIGUOUS_CANONICAL = {"gallon": "gallon", "quart": "quart", "pint": "pint", "calorie": "calorie", "horsepower": "hp",
                        "british_thermal_unit": "btu", "ton": "ton", "barrel": "barrel"}

# --- D05: temperature differences are not absolute temperatures ---
# FR-03 (#47): only these words make a property name a clear label. Abbreviations (DT, diff, dTemp) and domain terms
# (superheat, approach, range ...) do not: their meaning depends on the document, so the kind stays "unknown".
_INTERVAL_WORDS = {"delta", "difference", "differential", "rise", "drop", "interval"}
_ABSOLUTE_WORDS = {"temperature", "temp"}
_UNCLEAR_TEMPERATURE_WORDS = {"dt", "dtemp", "dtemperature", "dtmp", "diff", "superheat", "superheating", "subcool",
                              "subcooling", "subcooled", "approach", "range", "span", "spread", "margin"}
_OFFSET_TO_DELTA = {"degree_Celsius": "delta_degC", "degree_Fahrenheit": "delta_degF"}
MAX_CHARS = 200_000
MAX_LINES = 5_000
MAX_DOCUMENTS = 12   # engine limit: audit_documents and the API accept 1..12 documents (a PDF page is one document)
# Ingress guard (public hosting). The row regular expressions below are not linear-time on long runs of whitespace
# (measured on the frozen baseline: one line of ~100,000 spaces took ~42 s, growth ~quadratic). Blank and delimiter-free
# lines therefore skip every regular expression, and a delimiter-bearing line longer than this is not inspected: it is
# reported as LINE_TOO_LONG (a visible coverage gap), never dropped. Real engineering rows are far shorter; the limit is
# applied per line, so the worst case for one document stays bounded (about MAX_CHARS * MAX_LINE_CHARS steps).
MAX_LINE_CHARS = 2_000
_EXCERPT_CHARS = 200
# H2-004 numeric policy (documented in the README). Two values are equal when their relative difference
# |a-b| / max(|a|,|b|) is not above max(tolerance, ZERO_TOLERANCE_FLOOR). The floor exists because converted values carry
# binary floating-point rounding: 12 L/min and 0.0002 m^3/s differ by about 2e-16 after conversion to SI. A conversion chain
# is a few float multiplications, each with a relative error of at most 2**-53 (1.1e-16), so real conversions stay below
# about 1e-14 (checked by tests over the supported unit pairs). 1e-12 leaves a margin of about 100. This is NOT exact
# equality at tolerance 0: relative differences up to 1e-12 are treated as equal. effective_tolerance = max(tolerance, 1e-12).
ZERO_TOLERANCE_FLOOR = 1e-12
_TINY = sys.float_info.min   # smallest normal double; a nonzero value below this has lost precision and is not compared
SCOPE = "Structured rows only; not a general PDF/P&ID semantic audit"

# --- FR-01 (#45): bounded unit-expression syntax. Checked BEFORE any text reaches Pint. ---
# Pint evaluates the unit text as an expression. A chained exponent such as `m^9^9^9` makes it compute a huge integer power
# and can keep one audit busy for a very long time (it holds the interpreter lock). This guard is a small lexical grammar,
# not a blacklist of one string:
#   unit  := term (("*" | "/") term)*                      at most MAX_UNIT_TERMS terms, at most MAX_UNIT_CHARS characters
#   term  := name [ ("^" | "**") exponent | digits ]       at most ONE exponent per term; digits = implicit exponent (m2, m\u00b2)
#   name  := letters, "_", "%", degree, micro and ohm signs (no ASCII or other digits, no spaces, no brackets)
#   exponent := [+-] 1-2 ASCII digits [ "." 1-9 ASCII digits ]   a literal, never an expression
# So no exponent can contain another exponent, and every exponent is a short literal. Anything else is refused with a stable
# code. The scan is linear in the length. This is bounded syntactic rejection; it is not a claim of immunity against every
# possible slow input.
MAX_UNIT_CHARS = 64
MAX_UNIT_TERMS = 8
_ASCII_DIGITS = "0123456789"
_SUPERSCRIPT_DIGITS = "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"   # m\u00b2, kg/m\u00b3
_SUPERSCRIPT_SIGNS = "\u207a\u207b"
_UNIT_NAME_SYMBOLS = "_\u00b0\u00b5\u03bc\u03a9%"


def _scan_unit_name(unit: str, pos: int) -> int:
    end = pos
    while end < len(unit) and (unit[end].isalpha() or unit[end] in _UNIT_NAME_SYMBOLS):
        end += 1
    return end


def _scan_exponent_literal(unit: str, pos: int):
    """End of an exponent literal that starts at pos, or None when there is none."""
    n, i = len(unit), pos
    if i < n and unit[i] in "+-":
        i += 1
    start = i
    while i < n and unit[i] in _ASCII_DIGITS and i - start < 2:
        i += 1
    if i == start:
        return None
    if i + 1 < n and unit[i] == "." and unit[i + 1] in _ASCII_DIGITS:
        j = i + 1
        while j < n and unit[j] in _ASCII_DIGITS and j - (i + 1) < 9:
            j += 1
        i = j
    return i


def unit_syntax_problem(unit):
    """None when the unit text is inside the bounded grammar above, else a stable code:
    EMPTY, TOO_LONG, TOO_MANY_TERMS, BAD_SYNTAX, BAD_EXPONENT, CHAINED_EXPONENT, EXPONENT_TOO_LARGE.
    Pure text scan. It never calls Pint."""
    if not isinstance(unit, str) or not unit:
        return "EMPTY"
    n = len(unit)
    if n > MAX_UNIT_CHARS:
        return "TOO_LONG"
    pos, terms = 0, 0
    while True:
        end = _scan_unit_name(unit, pos)
        if end == pos:
            return "BAD_SYNTAX"
        terms += 1
        if terms > MAX_UNIT_TERMS:
            return "TOO_MANY_TERMS"
        pos, has_exponent = end, False
        operator = 1 if unit.startswith("^", pos) else 2 if unit.startswith("**", pos) else 0
        if operator:
            end = _scan_exponent_literal(unit, pos + operator)
            if end is None:
                return "BAD_EXPONENT"
            pos, has_exponent = end, True
        elif pos < n and unit[pos] in _ASCII_DIGITS:        # implicit exponent: m2
            end = pos
            while end < n and unit[end] in _ASCII_DIGITS and end - pos < 2:
                end += 1
            pos, has_exponent = end, True
        elif pos < n and unit[pos] in _SUPERSCRIPT_DIGITS + _SUPERSCRIPT_SIGNS:       # superscript exponent: m\u00b2, m\u207b\u00b2
            end = pos + (1 if unit[pos] in _SUPERSCRIPT_SIGNS else 0)
            start = end
            while end < n and unit[end] in _SUPERSCRIPT_DIGITS and end - start < 2:
                end += 1
            if end == start:
                return "BAD_EXPONENT"
            pos, has_exponent = end, True
        if pos == n:
            return None
        if unit[pos] == "^" or unit.startswith("**", pos):
            return "CHAINED_EXPONENT" if has_exponent else "BAD_EXPONENT"
        if unit[pos] in _ASCII_DIGITS or unit[pos] in _SUPERSCRIPT_DIGITS:
            return "EXPONENT_TOO_LARGE"
        if unit[pos] in "*/":
            pos += 1
            continue
        return "BAD_SYNTAX"


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


class NumericRangeError(ValueError, ArithmeticError):
    """A value is too large, too small or too imprecise to compare safely. The row is reported, never compared."""


def relative_difference(a: float, b: float) -> float:
    """|a-b| / max(|a|,|b|) without overflow or underflow of the intermediate results. Inputs must be finite.

    Both values are first divided by the larger magnitude, so every intermediate is within [-1, 1]. The result is in
    [0, 2]: 0 for equal values (including 0 and 0), 1 for a zero against a nonzero value and 2 for opposite signs.
    """
    if not (math.isfinite(a) and math.isfinite(b)):
        raise NumericRangeError("non-finite value")
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return 0.0
    return abs(a / scale - b / scale)


def reported_difference(error: float) -> float:
    """The relative difference as reported: 10 decimals as before, but never 0.0 for a difference that was flagged."""
    shown = round(error, 10)
    return shown if shown > 0.0 or error == 0.0 else float(f"{error:.6g}")


def dimension_key(dimensionality) -> str:
    """Informational sorted [dimension]^exponent terms, or 'dimensionless'.

    Integer exponents retain the prior format; fractional floats use round-trip repr.
    Scientific equality must use Pint dimensionality objects, never this string.
    """
    items = sorted((str(name), float(power)) for name, power in dimensionality.items())
    return " ".join(f"{name}^{int(power) if power.is_integer() else repr(power)}"
                    for name, power in items) or "dimensionless"


def _split_unescaped_pipes(text: str) -> list:
    """Split on pipes that are not escaped. `\\|` is an escaped pipe (part of the cell); `\\\\|` is an escaped backslash followed
    by a real pipe. Linear time; used only for the table-heading check."""
    cells, start, backslashes = [], 0, 0
    for index, char in enumerate(text):
        if char == "\\":
            backslashes += 1
            continue
        if char == "|" and backslashes % 2 == 0:
            cells.append(text[start:index])
            start = index + 1
        backslashes = 0
    cells.append(text[start:])
    return cells


def _pipe_cells(line: str) -> list:
    """Cells of a pipe-delimited line for the heading check: optional outer pipes removed, escaped pipes kept inside their cell."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    before = body[:-1]
    if body.endswith("|") and (len(before) - len(before.rstrip("\\"))) % 2 == 0:      # a trailing pipe preceded by an escape is not a closing pipe
        body = before
    return _split_unescaped_pipes(body)


def _is_table_heading(line: str, next_line: str) -> bool:
    """D06: the line above a Markdown table delimiter row is the table heading, never a measurement row.

    The next line must itself be pipe-delimited, every one of its cells must be a delimiter cell, and it must have the same
    number of columns as this line. Only UNESCAPED pipes are structural, so `| Tag | Date | 2027 \\| issue |` has three columns
    (review finding N2). A bare `---` (a Markdown horizontal rule or a setext underline) never hides the line above it
    (review finding F1). Linear time: no regular expression runs on the whole line."""
    if "|" not in next_line or "---" not in next_line or len(next_line) > MAX_LINE_CHARS:
        return False
    if len(_split_unescaped_pipes(line)) - 1 < 2:
        return False
    delimiter = _pipe_cells(next_line)
    return all(_SEPARATOR_CELL.fullmatch(cell.strip()) for cell in delimiter) and len(delimiter) == len(_pipe_cells(line))


def split_lines(text: str) -> list[str]:
    """Split on editor line breaks. A final line break does not create an extra line."""
    parts = _LINE_BREAK.split(text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _is_csv(name: str) -> bool:
    return name.lower().split(":page-")[0].endswith(".csv")


_NUMBER_START = re.compile(r"^\s*(?:approx\.?|about|ca\.?|[~≈<>≤≥±+\-−=])*\s*[.,]?\d")
# One cell of a Markdown table delimiter row: three or more dashes, optional alignment colons (`---`, `:---`, `---:`, `:---:`).
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")


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
_MEASURE_CELL = re.compile(_QUALIFIER + r"(?:\d+(?:\.\d+)?|\.\d+)\s*(?:[A-Za-z°µμΩ%][\w°µμΩ/%^.*-]*)?", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][\w.-]{0,31}")


_NUMBER_ONLY = re.compile(_QUALIFIER + r"(?:\d+(?:\.\d+)?|\.\d+)", re.IGNORECASE)
_UNIT_TOKEN = re.compile(r"[A-Za-z°µμΩ%][\w°µμΩ/%^.*-]{0,23}")
# Gauge/absolute pressure spellings are not supported units (README), but a column that holds them is still a unit column.
_PRESSURE_BASIS_UNIT = re.compile(r"(?:mbar|bar|psi|kpa|mpa|pa)[ga]", re.IGNORECASE)


# Recognition-only glyph mapping, the ONLY compatibility mapping in this module: U+2103 (degree-Celsius sign) -> `°C`, U+2109
# (degree-Fahrenheit sign) -> `°F`, U+2126 (ohm sign) -> U+03A9 (Greek omega). It is ADDITIVE: the raw cell is always tried first, so
# everything the first commit recognized is recognized unchanged, and nothing else (superscripts, circled or roman labels, full-width
# or compatibility letters) is rewritten. The reported row text is never changed and nothing is parsed or converted.
_GLYPH_MAP = str.maketrans({"\u2103": "\u00b0C", "\u2109": "\u00b0F", "\u2126": "\u03a9"})


def _unit_like_raw(cell: str) -> bool:
    """True when the RAW cell is a unit the engine knows, refuses as ambiguous, a gauge/absolute pressure spelling, or the omega sign."""
    if not _UNIT_TOKEN.fullmatch(cell):
        return False
    if cell == "\u03a9" or _PRESSURE_BASIS_UNIT.fullmatch(cell):
        return True
    if unit_syntax_problem(cell):
        # FR-01: outside the grammar, Pint is never asked. An exponent-shaped token is still a unit column, so that the
        # row beside it is reported (UNPARSED_ROW) and not taken for prose; other refused shapes (for example a hyphenated
        # word) are not units.
        return "^" in cell or "**" in cell
    if ambiguous_unit(cell):
        return True
    try:
        UNITS.Quantity(1, ALIASES.get(cell, cell))
        return True
    except Exception:  # unknown unit word: not a unit column
        return False


@functools.lru_cache(maxsize=512)
def _unit_like(cell: str) -> bool:
    """Is this whole cell a unit column? Raw cell first (as in the first commit), then the three-glyph mapping as an additive fallback."""
    if _unit_like_raw(cell):
        return True
    mapped = cell.translate(_GLYPH_MAP)
    return mapped != cell and _unit_like_raw(mapped)


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
        mapped = cell.translate(_GLYPH_MAP)            # additive: the raw cell is tried first
        if not (_MEASURE_CELL.fullmatch(cell) or (mapped != cell and _MEASURE_CELL.fullmatch(mapped))):
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


def _written_stems(word: str, candidates):
    """H2-002. For every way Pint reads `word` (prefix, unit, suffix), yield the unit stem AS WRITTEN in the text.

    Pint folds explicit names into the same canonical unit as the bare ambiguous name (`US_liquid_gallon` -> `gallon`,
    `thermochemical_calorie` -> `calorie`, `short_ton` -> `ton`), so the canonical name cannot decide ambiguity. The
    written stem can: `kgal` -> `gal`, `kilocalorie` -> `calorie`, `US_liquid_gallon` -> `US_liquid_gallon`.
    Yields (canonical, stem); stem is None when the written prefix cannot be located, and the caller then fails closed."""
    low = word.lower()
    for prefix, canonical, suffix in candidates:
        end = len(word) - len(suffix) if suffix and low.endswith(suffix.lower()) else len(word)
        if not prefix:
            yield canonical, word[:end]
            continue
        definition = UNITS._prefixes.get(prefix)
        forms = {prefix, getattr(definition, "symbol", "") or "", *(getattr(definition, "aliases", ()) or ())}
        written = next((f for f in sorted(filter(None, forms), key=len, reverse=True) if low.startswith(f.lower())), None)
        yield canonical, (None if written is None else word[len(written):end])


def _policy_key(stem: str) -> str:
    """Policy name of a written stem; a plural `s` that Pint did not report as a suffix (`kgals`) is removed too."""
    key = stem.lower()
    key = _AMBIGUOUS_ALIASES.get(key, key)
    if key not in AMBIGUOUS_UNITS and key.endswith("s"):
        key = _AMBIGUOUS_ALIASES.get(key[:-1], key[:-1])
    return key


def ambiguous_unit(unit: str):
    """Return (ambiguous_name, alternatives) when the unit text uses a regional or field-dependent name, else None.

    A metric or binary prefix does not remove the ambiguity: `kgal`, `kcal`, `kBtu`, `kton` and `mhp` are refused like
    `gal`, `cal`, `Btu`, `ton` and `hp` (H2-002). Explicit names (`US_liquid_gallon`, `international_calorie`,
    `metric_horsepower`, `short_ton`, ...) stay usable."""
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
        for canonical, stem in _written_stems(word, candidates):
            # Prefix not located in the text: never guess. Refuse if the unit could be one of the ambiguous families.
            key = _AMBIGUOUS_CANONICAL.get(canonical) if stem is None else _policy_key(stem)
            if key in AMBIGUOUS_UNITS:
                return word, AMBIGUOUS_UNITS[key]
    return None


def _words(text: str) -> set:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)          # dT -> d T ; deltaT -> delta T
    return {w.lower() for w in re.findall(r"[A-Za-z]+", spaced)} | {w.lower() for w in re.findall(r"[A-Za-z]+", text)}


def _temperature_kind(base_names: list, field: str) -> str:
    """Classify a pure-temperature quantity from its unit and its property name."""
    words = _words(field)
    if any(n.startswith("delta_") for n in base_names):
        return "interval"                                   # explicit unit: delta_degC, delta_degF
    if words & _UNCLEAR_TEMPERATURE_WORDS:
        return "unknown"                                    # DT, diff, superheat ...: never guessed (FR-03)
    if words & _INTERVAL_WORDS:
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
        if _is_table_heading(raw, next_line):
            continue                       # D06: the line above a genuine pipe-delimited table delimiter row is the heading, never a row
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
        if value == 0.0 and any(ch in "123456789" for ch in match["value"]):
            value_error = NumericRangeError("a nonzero number is too small to represent")
        elif not math.isfinite(value):
            value_error = NumericRangeError("a number is too large to represent")
        else:
            value_error = None
        syntax = unit_syntax_problem(unit)
        if syntax:                         # FR-01: Pint is not called for unit text outside the bounded grammar
            coverage["rows_unsupported_unit"] += 1
            warnings.append({"code": "UNSUPPORTED_UNIT", "document": name, "line": line_number, "excerpt": raw.strip(),
                             "reason": "UnitSyntaxError", "detail": syntax})
            continue
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
            if value_error:
                raise value_error
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
                raise NumericRangeError("non-finite converted value")
            if 0.0 < abs(numeric) < _TINY:
                raise NumericRangeError("converted value loses precision (subnormal)")
            if numeric == 0.0 and value != 0.0 and float(UNITS.Quantity(value * 2, quantity.units).to_base_units().magnitude) == 0.0:
                raise NumericRangeError("converted value underflows to zero")   # not a legitimate zero such as -273.15 degC
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


_TEMPERATURE_MESSAGES = {
    "interval_vs_absolute": "One value is a temperature difference and the other is an absolute temperature. They are not comparable.",
    "uncertain": "One value is a temperature difference. The other (for example a plain K) could be a temperature or a difference. "
                 "Name the property 'temperature difference' or use delta_degC to make the meaning explicit.",
    "unknown_kind": "The kind of at least one temperature is not clear (absolute temperature or temperature difference), and a degC or "
                    "degF value converts differently in each case. Use delta_degC or delta_degF for a difference, or name the property "
                    "'temperature' for an absolute temperature. Abbreviations such as DT and terms such as superheat do not decide the kind.",
}


@functools.lru_cache(maxsize=256)
def _has_offset_unit(unit: str) -> bool:
    """True for degC and degF (offset scales). Fails closed (True) when the unit text cannot be read."""
    if unit_syntax_problem(unit):
        return True
    try:
        names = {name for name, _ in UNITS.Quantity(1, ALIASES.get(unit, unit)).unit_items()}
    except Exception:
        return True
    return bool(names & set(_OFFSET_TO_DELTA))


def _temperature_group_problem(records):
    """D05, H2-001, FR-03. For records of one tag+property that share the temperature dimension, return
    (detail, message, first, second) for a pair that must not be compared, else None. O(n), and equal to checking every pair:
      - an interval beside an absolute temperature, or beside an unknown kind, is never comparable;
      - an unknown kind is comparable only when no offset unit (degC, degF) is involved, or when value and unit are identical
        (identical text gives the same answer under every reading).
    The two returned records are listed in the order of `records`."""
    if not records or any(r.dimension != "[temperature]" for r in records):
        return None
    first_of = {}
    for record in records:
        first_of.setdefault(record.quantity_kind, record)
    position = {id(record): index for index, record in enumerate(records)}

    def found(detail, a, b):
        a, b = sorted((a, b), key=lambda r: position[id(r)])
        return detail, _TEMPERATURE_MESSAGES[detail], a, b

    interval = first_of.get("interval")
    if interval is not None:
        if "absolute" in first_of:
            return found("interval_vs_absolute", interval, first_of["absolute"])
        if "unknown" in first_of:
            return found("uncertain", interval, first_of["unknown"])
    unknowns = [r for r in records if r.quantity_kind == "unknown"]
    if unknowns:
        unknown_offset = next((u for u in unknowns if _has_offset_unit(u.unit)), None)
        if unknown_offset is not None:
            differing = next((r for r in records if r is not unknown_offset
                              and (r.value, r.unit) != (unknown_offset.value, unknown_offset.unit)), None)
            if differing is not None:
                return found("unknown_kind", unknown_offset, differing)
        unknown_linear = next((u for u in unknowns if not _has_offset_unit(u.unit)), None)
        offset_record = next((r for r in records if _has_offset_unit(r.unit)), None)
        if unknown_linear is not None and offset_record is not None:
            return found("unknown_kind", unknown_linear, offset_record)
    return None


def _temperature_pair(ref: Observation, other: Observation):
    """Returns None when the pair may be compared, else (detail, message)."""
    problem = _temperature_group_problem([ref, other])
    return problem[:2] if problem else None


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
    if not documents or len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"submit 1..{MAX_DOCUMENTS} documents")
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

    allowed = max(tolerance, ZERO_TOLERANCE_FLOOR)

    def relative_error(a: Observation, b: Observation) -> float:
        return relative_difference(a.si_value, b.si_value)

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
                # H2-001: a temperature difference and an absolute temperature are never "the same value", even when
                # their kelvin numbers agree. Check the kind BEFORE the numbers, so that no row can represent the source.
                problem = _temperature_pair(first, other)
                if problem:
                    consistent = False
                    group_warnings.append({"code": "TEMPERATURE_SEMANTICS", "scope": "same_source", "detail": problem[0],
                                           "message": problem[1], "tag": tag, "field": field,
                                           "reference": asdict(first), "other": asdict(other)})
                    continue
                error = relative_error(first, other)
                if error > allowed:
                    consistent = False
                    findings.append({"code": "INTERNAL_CONTRADICTION", "scope": "same_source", "tag": tag, "field": field,
                                     "relative_difference": reported_difference(error),
                                     "reference": asdict(first), "other": asdict(other)})
            # FR-02 (#46): a repeat that is close to the FIRST row can still contradict another repeat (approximate equality
            # is not transitive). Every pair must be compatible, not only each repeat with the first one.
            if consistent:
                problem = _temperature_group_problem(same)       # a pair that does not include the first row
                if problem:
                    consistent = False
                    group_warnings.append({"code": "TEMPERATURE_SEMANTICS", "scope": "same_source", "detail": problem[0],
                                           "message": problem[1], "tag": tag, "field": field,
                                           "reference": asdict(problem[2]), "other": asdict(problem[3])})
            if consistent:
                # Here allowed < 1 (tolerance <= 0.2). Two values of opposite sign, or zero and nonzero, differ by 1 or more,
                # so they are always inconsistent, and the minimum and maximum are such a pair whenever the group is not
                # strictly one-signed. In a one-signed group the relative difference 1 - (smaller / larger magnitude) is
                # largest for the smallest and largest magnitude, that is for the minimum and the maximum. So the group is
                # consistent exactly when the minimum and the maximum are within the allowed difference. O(n).
                low, high = min(same, key=lambda r: r.si_value), max(same, key=lambda r: r.si_value)
                error = relative_error(low, high)
                if error > allowed:
                    consistent = False
                    low, high = sorted((low, high), key=lambda r: same.index(r))
                    findings.append({"code": "INTERNAL_CONTRADICTION", "scope": "same_source", "tag": tag, "field": field,
                                     "relative_difference": reported_difference(error),
                                     "reference": asdict(low), "other": asdict(high)})
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
            item = {"scope": "cross_source", "tag": tag, "field": field, "relative_difference": reported_difference(error),
                    "reference": asdict(reference), "other": asdict(other)}
            if error > allowed:
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
    try:
        json.dumps(result, allow_nan=False)      # H2-004: a result that standard JSON cannot carry is an error, never output
    except ValueError as exc:
        raise NumericRangeError("NUMERIC_RANGE: the result contains a non-finite number and was not produced") from exc
    return result
