"""P2 Gate F: list-free protection foundation for technical-text review.

Scope (see .kiro/specs/p2-ste-writing-review, requirement 17 and design section 14):
deterministic, offline, standard-library-only protected-span detection with exact source
offsets, explicit abstention, WARNING/CAUTION region marking and the future-compatible
``review_technical_text`` result shell.

This module is NOT a writing review.  It consults no closed lexical list, produces no
finding, suggestion, rewrite or revised text, and does not understand negation, limits,
units or obligations.  A protected span is literal text that a later stage must not alter;
it is not a claim that the text is correct or meaningful.

Offset convention: Python ``str`` indices (code points), ``end`` exclusive, relative to the
unmodified input, so ``text[start:end]`` is always the reported excerpt.
"""
from __future__ import annotations

import hashlib
import re
from array import array
from bisect import bisect_right
from dataclasses import dataclass

LABELS = ["STE-inspired writing suggestions", "ASD-STE100 conformance not verified"]
SCHEMA_VERSION = 1
STAGE = "GATE_F_LIST_FREE_FOUNDATION"
OFFSET_CONVENTION = "python_str_index_code_points_end_exclusive"

_BLOCKED_RULES = ("A01", "A02", "A03", "A04", "A05", "A06")
_KNOWN_RULES = _BLOCKED_RULES + ("A07",)


# --------------------------------------------------------------------------- options
@dataclass(frozen=True)
class ReviewOptions:
    """Options.  ``enabled_rules`` never causes a rule to run in Gate F (it is only reported)."""

    enabled_rules: tuple = ()
    project_terms: tuple = ()
    a02_rewrite: bool = False

    def __post_init__(self):
        if not isinstance(self.enabled_rules, tuple):
            raise ValueError("enabled_rules must be a tuple")
        for rule in self.enabled_rules:
            if rule not in _KNOWN_RULES:
                raise ValueError(f"unknown rule id: {rule!r}")
        if not isinstance(self.project_terms, tuple):
            raise ValueError("project_terms must be a tuple of strings")
        for term in self.project_terms:
            if not isinstance(term, str) or not term or term != term.strip():
                raise ValueError("each project term must be a non-empty string without outer whitespace")
        if self.a02_rewrite is not False:
            raise ValueError("a02_rewrite is not available in Gate F (no rewrite of any kind)")


# --------------------------------------------------------------------------- patterns
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_TOKEN = re.compile(r"\S+")
_LEAD = frozenset("([{\"\u201c'`")
_TRAIL = frozenset(".,;:!?)]}\"\u201d'`")
_SENTENCE_PUNCT = frozenset(".,;:!?")

_SIGN = r"[+\-\u2212]"
_UNS = r"(?:[0-9]+(?:[.,][0-9]+)*|[.,][0-9]+)"
_EXP = r"(?:[eE][+\-\u2212]?[0-9]+)?"
_NUM_CORE = rf"{_SIGN}?{_UNS}{_EXP}"
_NUMBER_FULL = re.compile(
    rf"(?:{_NUM_CORE}(?:[-\u2013]{_UNS}{_EXP})?|{_NUM_CORE}\u00d710\^{_SIGN}?[0-9]+)"
)
_NUMBER_PREFIX = re.compile(_NUM_CORE)
_ALN = r"[^\W_]"
_LET = r"[^\W\d_]"
_IDENT_I1 = re.compile(rf"{_LET}+[-_]?[0-9]{_ALN}*(?:[-_./:]{_ALN}+)*")
_IDENT_I2 = re.compile(rf"(?=[0-9]){_ALN}+[-_/]{_ALN}+(?:[-_./:]{_ALN}+)*")
_VARIABLE = re.compile(r"(?:[A-Za-z]|[\u0391-\u03a9\u03b1-\u03c9])(?:_[^\W_]+)?")
_ANCHOR_TOKEN = re.compile(r"(?:<=|>=|==|!=|[=<>\u2264\u2265\u2248\u2260\u00b1\u00d7\u00f7^])+")
_ANCHOR_INTOKEN = re.compile(rf"{_ALN}(?:<=|>=|==|!=|[=<>\u2264\u2265\u2248\u2260]){_ALN}")
_OPERATORS = frozenset(["+", "-", "\u2212", "\u2013", "*", "/"])
_GLUE_START = frozenset("\u00b0%")
_BACKTICKS = re.compile(r"`+")
_SAFETY_MARK = re.compile(r"[\s*_#>\-]*(WARNING|CAUTION|DANGER)(?![^\W_])", re.IGNORECASE)

_PRIORITY = {"PT7_QUOTE": 1, "PT3_TERM": 2, "PT6_MATH": 3, "PT1_NUMBER": 4, "PT1_IDENT": 4}

_REASONS = {
    "unit_boundary_deferred": "a token directly after a number may or may not be a unit; unit detection is not available in Gate F",
    "quote_char_ambiguous": "a double quote directly after a digit may be an inch mark or a closing quote; no span was guessed",
    "unclosed_quote": "a quotation delimiter has no safe partner in its paragraph; no span was guessed",
    "math_boundary_ambiguous": "the mathematical expression is missing an operand at one end; the literal run was protected",
    "overlap_conflict": "a candidate partially overlaps an already protected span and was discarded",
    "safety_marker_undecided": "this safety marker is not decided for Gate F; the paragraph is not marked and no claim is made",
    "unclassified_numeric_token": "a digit-bearing token matches no number or identifier shape; it was left unclassified",
}

_CAPABILITIES = [
    ("source_offsets", "supported", "exact code-point offsets; text[start:end] equals the excerpt"),
    ("numeric_literals", "supported", "literal number text only; no locale, unit or physical interpretation"),
    ("engineering_identifiers", "supported", "documented syntactic shapes only; not semantic identification"),
    ("quoted_spans", "supported", "double, curly and backtick delimiters within one paragraph"),
    ("math_expressions", "partial", "explicit operator and operand runs only; not natural-language limits"),
    ("project_terms", "supported", "only terms passed explicitly in options; no built-in vocabulary"),
    ("safety_labels_warning_caution", "supported", "paragraph regions only; no suggestion exists for any text"),
    ("safety_label_danger", "unsupported", "owner decision U-04 is open; the marker yields an abstention"),
    ("a07_suggestion_guard", "unsupported", "the preservation guard over suggestions is not implemented; no suggestion exists to guard"),
    ("unit_symbols", "unsupported", "needs an unreviewed list; tokens after numbers yield abstentions"),
    ("negation_detection", "unsupported", "needs an unreviewed list"),
    ("limit_phrases", "unsupported", "needs an unreviewed list; comparison symbols are protected only as literal text"),
    ("modal_obligation_classes", "unsupported", "not implemented in Gate F"),
    ("sentence_segmentation", "unsupported", "needs an unreviewed list"),
    ("writing_rules", "unsupported", "rules A01 to A06 are blocked until Gate W"),
    ("suggestions", "unsupported", "no replacement text is produced"),
    ("apply_decisions", "unsupported", "fails closed; text is never modified"),
]

_LIMITATIONS = [
    "Foundation only: protected-span primitives and an empty-findings review contract; this is not a writing review.",
    "Protected spans are literal text; they do not show that a number, identifier or expression is correct.",
    "Identifier and number shapes are syntactic and intentionally over-protective; they are not semantic classification.",
    "Unit symbols, negations, limit phrases, modals and sentence boundaries are not detected; uncertain text is left untouched and may yield abstentions.",
    "Only paragraph-level WARNING and CAUTION markers are recognized; other safety wording is not detected.",
    "ASD-STE100 conformance is not verified and is not claimed.",
]


# --------------------------------------------------------------------------- helpers
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


class _Lines:
    """Line table using the same break set as ``app.core.split_lines``."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0]
        self.contents = []          # (start, end) of line content, without the break
        pos = 0
        for m in _LINE_BREAK.finditer(text):
            self.contents.append((pos, m.start()))
            pos = m.end()
            self.starts.append(pos)
        if pos < len(text):
            self.contents.append((pos, len(text)))
        self.count = len(self.contents)

    def line_of(self, offset: int) -> int:
        return bisect_right(self.starts, offset)

    def paragraphs(self):
        start = end = None
        for s, e in self.contents:
            if self.text[s:e].strip():
                if start is None:
                    start = s
                end = e
            elif start is not None:
                yield start, end
                start = None
        if start is not None:
            yield start, end


def _core(text: str, s: int, e: int):
    while s < e and text[s] in _LEAD:
        s += 1
    while e > s and text[e - 1] in _TRAIL:
        e -= 1
    return s, e


def _classify(core: str):
    """Return ('num'|'ident'|'glued'|'plain', prefix_len)."""
    if _NUMBER_FULL.fullmatch(core):
        return "num", len(core)
    if _IDENT_I1.fullmatch(core) or _IDENT_I2.fullmatch(core):
        return "ident", len(core)
    m = _NUMBER_PREFIX.match(core)
    if m and m.end() < len(core):
        ch = core[m.end()]
        if ch.isalpha() or ch in _GLUE_START:
            return "glued", m.end()
    return "plain", 0


class _Token:
    def __init__(self, text, m):
        self.ts, self.te = m.start(), m.end()
        self.cs, self.ce = _core(text, self.ts, self.te)
        self.core = text[self.cs:self.ce]
        self.ends = any(ch in _SENTENCE_PUNCT for ch in text[self.ce:self.te])
        self.shape, self.prefix = _classify(self.core) if self.core else ("plain", 0)
        c = self.core
        if not c:
            self.kind = "OTHER"
        elif self.shape in ("num", "ident", "glued") or _VARIABLE.fullmatch(c):
            self.kind = "OPND"
        elif _ANCHOR_TOKEN.fullmatch(c):
            self.kind = "ANCHOR"
        elif _ANCHOR_INTOKEN.search(c):
            self.kind = "SELF"
        elif c in _OPERATORS:
            self.kind = "OPER"
        else:
            self.kind = "OTHER"


class _Builder:
    def __init__(self, text: str):
        self.text = text
        self.lines = _Lines(text)
        self.cands = []             # (priority, start, end, cls, detector, basis)
        self.abst = {}              # (kind, start, end) -> reason
        self.regions = []

    def abstain(self, kind, start, end):
        if start < end:
            self.abst.setdefault((kind, start, end), _REASONS[kind])

    def cand(self, cls, start, end, detector, basis):
        if start < end:
            self.cands.append((_PRIORITY[cls], start, end, cls, detector, basis))

    # ----- quotes
    def quotes(self, ps, pe):
        text = self.text
        open_at = None
        for i in range(ps, pe):
            if text[i] != '"':
                continue
            if i > ps and "0" <= text[i - 1] <= "9":
                # Possible inch mark: never an opener or a closer.  An open quote cannot be closed
                # safely across it, so it is left unresolved instead of guessing a span.
                self.abstain("quote_char_ambiguous", i, i + 1)
                if open_at is not None:
                    self.abstain("unclosed_quote", open_at, open_at + 1)
                    open_at = None
            elif open_at is None:
                open_at = i
            else:
                self.cand("PT7_QUOTE", open_at, i + 1, "quote_pairing", "delimiter_pairing")
                open_at = None
        if open_at is not None:
            self.abstain("unclosed_quote", open_at, open_at + 1)
        open_at = None
        for i in range(ps, pe):
            ch = text[i]
            if ch == "\u201c":
                if open_at is not None:
                    self.abstain("unclosed_quote", open_at, open_at + 1)
                open_at = i
            elif ch == "\u201d":
                if open_at is None:
                    self.abstain("unclosed_quote", i, i + 1)
                else:
                    self.cand("PT7_QUOTE", open_at, i + 1, "quote_pairing", "delimiter_pairing")
                    open_at = None
        if open_at is not None:
            self.abstain("unclosed_quote", open_at, open_at + 1)
        runs = [(m.start(), m.end()) for m in _BACKTICKS.finditer(text, ps, pe)]
        by_len = {}
        for idx, (s, e) in enumerate(runs):
            by_len.setdefault(e - s, []).append(idx)
        i = 0
        while i < len(runs):
            same = by_len[runs[i][1] - runs[i][0]]
            k = bisect_right(same, i)
            if k < len(same):
                j = same[k]
                self.cand("PT7_QUOTE", runs[i][0], runs[j][1], "quote_pairing", "delimiter_pairing")
                i = j + 1
            else:
                self.abstain("unclosed_quote", runs[i][0], runs[i][1])
                i += 1

    # ----- explicit project terms
    def terms(self, terms):
        for term in sorted(set(terms), key=lambda t: (-len(t), t)):
            pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")
            for m in pattern.finditer(self.text):
                self.cand("PT3_TERM", m.start(), m.end(), "project_term_option", "explicit_option")

    # ----- safety regions
    def safety(self, ps, pe):
        text = self.text
        m = _SAFETY_MARK.match(text, ps, pe)
        if not m:
            return
        start = ps
        while start < pe and text[start].isspace():
            start += 1
        end = pe
        while end > start and text[end - 1].isspace():
            end -= 1
        label = m.group(1).upper()
        if label == "DANGER":
            self.abstain("safety_marker_undecided", start, end)
        else:
            self.regions.append((start, end, label))

    # ----- tokens, math runs, numbers, identifiers
    def tokens(self):
        text = self.text
        toks = [_Token(text, m) for m in _TOKEN.finditer(text)]
        self._math(toks)
        for t in toks:
            if t.shape == "num":
                self.cand("PT1_NUMBER", t.cs, t.ce, "number_literal", "syntactic_pattern")
            elif t.shape == "ident":
                self.cand("PT1_IDENT", t.cs, t.ce, "identifier_shape", "syntactic_pattern")
            elif t.shape == "glued":
                self.cand("PT1_NUMBER", t.cs, t.cs + t.prefix, "number_literal", "syntactic_pattern")
        return toks

    def _same_line(self, a, b):
        gap = self.text[a:b]
        return "\n" not in gap and "\r" not in gap

    def _math(self, toks):
        run = []

        def flush():
            if any(t.kind in ("ANCHOR", "SELF") for t in run):
                self._emit_math(run)
            run.clear()

        for t in toks:
            if t.kind == "OTHER":
                flush()
                continue
            if run and (run[-1].ends or not self._same_line(run[-1].te, t.ts)):
                flush()
            run.append(t)
        flush()

    def _emit_math(self, run):
        text = self.text
        first, last = run[0], run[-1]
        s = first.ts
        while s < first.te and text[s] in _LEAD and text[s] != "(":
            s += 1
        e = last.te
        while e > s and text[e - 1] in _TRAIL and text[e - 1] != ")":
            e -= 1
        while s < e and text[s] == "(" and text[s:e].count("(") > text[s:e].count(")"):
            s += 1
        while e > s and text[e - 1] == ")" and text[s:e].count(")") > text[s:e].count("("):
            e -= 1
        if s >= e:
            return
        self.cand("PT6_MATH", s, e, "math_run", "operator_operand_run")
        if first.kind in ("ANCHOR", "OPER") or last.kind in ("ANCHOR", "OPER"):
            self.abstain("math_boundary_ambiguous", s, e)

    # ----- overlap resolution
    def resolve(self):
        n = len(self.text)
        owner = array("i", [-1]) * n
        spans, absorbed = [], 0
        for prio in (1, 2, 3, 4):
            for _, s, e, cls, det, basis in sorted((c for c in self.cands if c[0] == prio),
                                                   key=lambda c: (c[1], -c[2], c[3])):
                seen = set(owner[s:e])
                if seen == {-1}:
                    owner[s:e] = array("i", [len(spans)]) * (e - s)
                    spans.append((s, e, cls, det, basis))
                elif -1 not in seen and len(seen) == 1:
                    absorbed += 1
                else:
                    self.abstain("overlap_conflict", s, e)
        return owner, spans, absorbed

    @staticmethod
    def _boundary_eligible(owner, spans, tok, classes):
        holder = owner[tok.cs]
        return holder != -1 and spans[holder][2] in classes

    def deferred(self, toks, owner, spans):
        text = self.text
        for i, t in enumerate(toks):
            if not t.core:
                continue
            # The boundary depends on the lexical number token, not on which class owns its
            # characters (an explicit project term may own the number or the whole phrase).
            # Only quoted text is excluded: it is opaque and never a boundary candidate.
            if t.shape == "glued":
                if self._boundary_eligible(owner, spans, t, ("PT1_NUMBER", "PT3_TERM")):
                    self.abstain("unit_boundary_deferred", t.cs + t.prefix, t.ce)
            elif t.shape == "num":
                if not self._boundary_eligible(owner, spans, t, ("PT1_NUMBER", "PT6_MATH", "PT3_TERM")):
                    continue
                if t.ce != t.te or i + 1 >= len(toks):
                    continue
                nxt = toks[i + 1]
                if not (nxt.core and self._same_line(t.te, nxt.ts)
                        and (text[nxt.cs].isalpha() or text[nxt.cs] in _GLUE_START)):
                    continue
                # Unowned, or owned by an explicit term / identifier shape (ownership is unchanged
                # and does not decide whether the token is a unit).  Quoted and math-run text is
                # opaque and never a boundary candidate.
                holder = owner[nxt.cs]
                if holder == -1 or spans[holder][2] in ("PT3_TERM", "PT1_IDENT"):
                    self.abstain("unit_boundary_deferred", nxt.cs, nxt.ce)
            elif t.shape == "plain" and owner[t.cs] == -1 and any("0" <= ch <= "9" for ch in t.core):
                self.abstain("unclassified_numeric_token", t.cs, t.ce)


# --------------------------------------------------------------------------- public API
def review_technical_text(text, options=None) -> dict:
    """Return the Gate F review: protected spans, abstentions, safety regions, zero findings."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if options is None:
        options = ReviewOptions()
    elif not isinstance(options, ReviewOptions):
        raise TypeError("options must be a ReviewOptions or None")

    b = _Builder(text)
    for ps, pe in b.lines.paragraphs():
        b.quotes(ps, pe)
        b.safety(ps, pe)
    b.terms(options.project_terms)
    toks = b.tokens()
    owner, spans, absorbed = b.resolve()
    b.deferred(toks, owner, spans)

    lines = b.lines

    def where(s, e):
        return {"start": s, "end": e, "line_start": lines.line_of(s), "line_end": lines.line_of(e - 1)}

    spans.sort(key=lambda x: (x[0], x[1]))
    protected = [
        {"id": f"S-{i:04d}", "cls": cls, **where(s, e), "text": text[s:e],
         "provenance": {"detector": det, "basis": basis}}
        for i, (s, e, cls, det, basis) in enumerate(spans, 1)
    ]
    abstentions = [
        {"id": f"ABS-{i:04d}", "kind": kind, "reason": reason, **where(s, e), "excerpt": text[s:e]}
        for i, ((kind, s, e), reason) in enumerate(sorted(b.abst.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])), 1)
    ]
    regions = [
        {"label": label, **where(s, e), "excerpt": text[s:e], "rewrite_allowed": False}
        for s, e, label in sorted(b.regions)
    ]
    requested = [r for r in _BLOCKED_RULES if r in options.enabled_rules]
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "labels": list(LABELS),
        "input": {"chars": len(text), "lines": lines.count, "sha256": _sha256(text)},
        "options": {"enabled_rules": list(options.enabled_rules),
                    "project_terms": list(options.project_terms),
                    "a02_rewrite": options.a02_rewrite},
        "offset_convention": OFFSET_CONVENTION,
        "protected_spans": protected,
        "abstentions": abstentions,
        "safety_regions": regions,
        "findings": [],
        "rules_executed": [],
        "rules_not_executed": [{"rule_id": r, "reason": "GATE_W_BLOCKED"} for r in requested],
        "lexical_lists_consulted": [],
        "capabilities": [{"id": i, "status": s, "note": n} for i, s, n in _CAPABILITIES],
        "summary": {"findings": 0, "with_suggestion": 0, "protected_spans": len(protected),
                    "abstentions": len(abstentions), "safety_regions": len(regions),
                    "absorbed_nested": absorbed},
        "limitations": list(_LIMITATIONS),
    }


def protected_sequence(text, options=None) -> list:
    """Ordered ``(class, exact text)`` pairs of the protected spans of ``text``."""
    return [(s["cls"], s["text"]) for s in review_technical_text(text, options)["protected_spans"]]


def apply_decisions(text, result=None, decisions=None) -> dict:
    """Gate F: refuses every request and never modifies text (fail closed).

    ``result`` and ``decisions`` are accepted for signature compatibility only.  They are never
    read, iterated or sorted, so any value (including mappings with incomparable keys) is refused.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    sha = _sha256(text)
    return {
        "status": "REFUSED_FOUNDATION_ONLY",
        "reason": "Gate F has no suggestions and no apply path; the text is returned unchanged.",
        "original_sha256": sha,
        "revised_text": text,
        "revised_sha256": sha,
        "applied": [],
        "skipped": [],
        "conflicts": [],
    }
