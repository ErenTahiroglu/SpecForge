"""P2 Gate F (List-Free Foundation) tests.

Derived from Requirement 17 (.kiro/specs/p2-ste-writing-review/requirements.md) and design
section 14.  Test ids ``F17.n`` name the acceptance criterion exercised.  These tests cover only
deterministic protection primitives and the review-result contract.  They deliberately assert
that NO writing finding, suggestion, rewrite or revised text exists (Gate W is blocked).
"""
import ast
import hashlib
import json
import pathlib
import re
import sys
import time

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORBIDDEN_CLAIM = re.compile(r"\b(compliant|conforms?|certified|approved|endorsed)\b", re.I)


def checker():
    from app import ste_checker  # imported lazily so every test fails individually in RED
    return ste_checker


def review(text, **kw):
    c = checker()
    opts = c.ReviewOptions(**kw) if kw else None
    return c.review_technical_text(text, opts)


def spans(res, cls=None):
    return [(s["cls"], s["text"]) for s in res["protected_spans"] if cls is None or s["cls"] == cls]


def texts(res, cls=None):
    return [t for _, t in spans(res, cls)]


def abst(res, kind=None):
    return [a["excerpt"] for a in res["abstentions"] if kind is None or a["kind"] == kind]


def assert_offsets(text, res):
    """Requirement 17.1: text[start:end] == excerpt for every reported item."""
    for key, field in (("protected_spans", "text"), ("abstentions", "excerpt"),
                       ("safety_regions", "excerpt")):
        for item in res[key]:
            assert 0 <= item["start"] < item["end"] <= len(text), item
            assert text[item["start"]:item["end"]] == item[field], item
            assert item["line_start"] <= item["line_end"]


# ------------------------------------------------------------------ F17.1 / 17.2 / 17.3 source integrity
def test_f17_1_offset_convention_is_code_points_end_exclusive():
    text = "😀 é 1,000 end"          # astral char + non-ASCII before the number
    res = review(text)
    (s,) = res["protected_spans"]
    assert s["start"] == text.index("1,000") == 4
    assert text[s["start"]:s["end"]] == "1,000" == s["text"]
    assert res["offset_convention"] == "python_str_index_code_points_end_exclusive"


def test_f17_1_offsets_with_crlf_cr_lf_and_line_numbers():
    text = "alpha\r\nV-201 here\rx 10,5\n\nWARNING keep\r\n"
    res = review(text)
    assert_offsets(text, res)
    by = {s["text"]: s for s in res["protected_spans"]}
    assert by["V-201"]["line_start"] == 2 and by["V-201"]["line_end"] == 2
    assert by["10,5"]["line_start"] == 3
    assert res["safety_regions"][0]["line_start"] == 5


@pytest.mark.parametrize("text", ["", "\n", "a", "a\n", "a\n\nb\r", "a\r\nb\r\n", "\r\r\r", "x\ny"])
def test_f17_1_line_count_matches_core_split_lines(text):
    from app.core import split_lines
    assert review(text)["input"]["lines"] == len(split_lines(text))


def test_f17_2_input_identity_and_no_revised_text():
    text = "Set 1,000 \u00a0 and “quote” — é\r\n"
    before = text
    res = review(text)
    assert text == before
    assert res["input"]["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert res["input"]["chars"] == len(text)
    keys = set()

    def walk(o):
        if isinstance(o, dict):
            keys.update(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(res)
    assert not keys & {"revised_text", "revised_sha256", "suggestion", "replacement", "edits"}


def test_f17_3_determinism_order_ids_and_no_overlap():
    text = 'Open V-201 and P-101 to 0.5 < x <= 1.5 with "q 5" and 1,000.\n\nWARNING 2 bar'
    a, b = review(text), review(text)
    assert a == b
    sp = a["protected_spans"]
    assert [s["id"] for s in sp] == [f"S-{i:04d}" for i in range(1, len(sp) + 1)]
    assert [(s["start"], s["end"]) for s in sp] == sorted((s["start"], s["end"]) for s in sp)
    for left, right in zip(sp, sp[1:]):
        assert left["end"] <= right["start"]
    ab = a["abstentions"]
    assert [x["id"] for x in ab] == [f"ABS-{i:04d}" for i in range(1, len(ab) + 1)]
    assert [(x["start"], x["end"], x["kind"]) for x in ab] == sorted(
        (x["start"], x["end"], x["kind"]) for x in ab)


# ------------------------------------------------------------------ F17.5 numbers
@pytest.mark.parametrize("literal", ["1,000", "10,5", "1e3", "1.0E5", "-20", "−5", "+12.5",
                                     "0.5-1.0", "0.5–1.0", "2.5×10^-3", "0.5", ".5", "3"])
def test_f17_5_numeric_literals_are_exact_spans(literal):
    text = f"Set the value to {literal} now."
    res = review(text)
    assert_offsets(text, res)
    assert spans(res) == [("PT1_NUMBER", literal)]
    assert "locale" not in json.dumps(res["protected_spans"]).lower()


def test_f17_5_trailing_punctuation_excluded_and_no_conversion():
    text = "Values: 1,000, 10,5; and 1.0E5."
    res = review(text)
    assert texts(res, "PT1_NUMBER") == ["1,000", "10,5", "1.0E5"]
    assert text == "Values: 1,000, 10,5; and 1.0E5."  # nothing normalized


def test_f17_5_unicode_minus_is_not_normalized():
    res = review("Set to −5 now")
    assert spans(res) == [("PT1_NUMBER", "−5")]


# ------------------------------------------------------------------ F17.6 identifiers
@pytest.mark.parametrize("ident", ["P-101", "V-201", "ISO-9001", "M8-1.25", "6205-2RS",
                                   "ISO-9001:2015", "H2O"])
def test_f17_6_identifiers(ident):
    text = f"Check {ident} today."
    res = review(text)
    assert_offsets(text, res)
    assert spans(res) == [("PT1_IDENT", ident)]


@pytest.mark.parametrize("wrapped,expected", [("(V-201)", "V-201"), ("V-201.", "V-201"),
                                              ("V-201,", "V-201"), ('"V-201 ok"', None)])
def test_f17_6_identifier_boundaries(wrapped, expected):
    res = review(f"Close {wrapped} now")
    if expected is None:      # inside quotes: absorbed by the quote span
        assert spans(res) == [("PT7_QUOTE", '"V-201 ok"')]
    else:
        assert spans(res) == [("PT1_IDENT", expected)]


def test_f17_6_digit_ended_quote_is_ambiguous_not_guessed():
    # Changed 2026-11-10 (F1): '"V-201"' ends in digit+quote, which R17.7 / design 14.4 treat as an
    # inch-mark candidate.  It was previously asserted as one quote span; that contradicted R17.7.
    text = 'Close "V-201" now'
    res = review(text)
    assert spans(res) == [("PT1_IDENT", "V-201")]
    assert abst(res, "quote_char_ambiguous") == ['"'] and abst(res, "unclosed_quote") == ['"']
    assert_offsets(text, res)


def test_f17_6_identifier_is_not_split_into_number_pieces():
    res = review("Valves V-201 and P-101/P-102 plus 6205-2RS 1,000")
    assert spans(res) == [("PT1_IDENT", "V-201"), ("PT1_IDENT", "P-101/P-102"),
                          ("PT1_IDENT", "6205-2RS"), ("PT1_NUMBER", "1,000")]


@pytest.mark.parametrize("word", ["pump", "Close", "valve-body", "Pump-Station", "well-known", "ISO"])
def test_f17_6_plain_words_are_never_identifiers(word):
    assert review(f"Use the {word} now")["protected_spans"] == []


# ------------------------------------------------------------------ F17.7 quotes
def test_f17_7_straight_curly_and_code_quotes():
    text = 'Press "OPEN 5 bar" then “STOP 7” or `run -x 3` now'
    res = review(text)
    assert_offsets(text, res)
    assert spans(res) == [("PT7_QUOTE", '"OPEN 5 bar"'), ("PT7_QUOTE", "“STOP 7”"),
                          ("PT7_QUOTE", "`run -x 3`")]
    assert res["abstentions"] == []            # numbers inside quotes stay opaque
    assert res["summary"]["absorbed_nested"] >= 3


def test_f17_7_quote_does_not_cross_blank_line_and_unclosed_abstains():
    text = 'He said "unclosed\n\nand later 10,5 here'
    res = review(text)
    assert spans(res) == [("PT1_NUMBER", "10,5")]
    assert abst(res, "unclosed_quote") == ['"']


def test_f17_7_inch_mark_is_ambiguous_not_a_delimiter():
    text = 'Use 5" pipe and 6" flange'
    res = review(text)
    assert not texts(res, "PT7_QUOTE")
    assert abst(res, "quote_char_ambiguous") == ['"', '"']


def test_f17_7_nested_or_stray_curly_quotes_abstain():
    res = review("a “b “c” d")
    assert abst(res, "unclosed_quote")            # first opener is not safely paired
    assert review("stray ” closer")["abstentions"][0]["kind"] == "unclosed_quote"


def test_f17_7_backtick_runs_match_by_length():
    text = "code ``a ` b`` and `c`"
    assert texts(review(text), "PT7_QUOTE") == ["``a ` b``", "`c`"]
    assert abst(review("only ``open"), "unclosed_quote") == ["``"]


# ------------------------------------------------------------------ F17.8 math
@pytest.mark.parametrize("expr", ["0.5 < x <= 1.5", "P = F / A", "x=5", "a ≤ b", "T ≈ 20",
                                  "1 + 2 = 3", "0.5 < x ≤ 1.5"])
def test_f17_8_math_expression_spans(expr):
    text = f"See {expr} end"
    res = review(text)
    assert_offsets(text, res)
    assert [s for s in spans(res) if s[0] == "PT6_MATH"] == [("PT6_MATH", expr)]
    assert not abst(res, "math_boundary_ambiguous")


def test_f17_8_math_inside_sentence_stops_at_prose():
    res = review("The pressure P = F / A. Check it.")
    assert ("PT6_MATH", "P = F / A") in spans(res)
    res = review("Keep 0.5 < x ≤ 1.5 at all times.")
    assert texts(res, "PT6_MATH") == ["0.5 < x ≤ 1.5"]


def test_f17_8_math_absorbs_inner_numbers():
    res = review("0.5 < x <= 1.5")
    assert spans(res) == [("PT6_MATH", "0.5 < x <= 1.5")]


def test_f17_8_missing_operand_is_protected_with_abstention():
    text = "Pressure > 10 bar"
    res = review(text)
    assert ("PT6_MATH", "> 10") in spans(res)
    assert abst(res, "math_boundary_ambiguous") == ["> 10"]


def test_f17_8_parentheses_trimmed_when_unbalanced():
    assert texts(review("(x < 5)"), "PT6_MATH") == ["(x < 5)"]
    assert texts(review("if (x < 5), do"), "PT6_MATH") == ["(x < 5)"]
    assert texts(review("(x < 5 end"), "PT6_MATH") == ["x < 5"]
    assert texts(review("P = (F + A) / B"), "PT6_MATH") == ["P = (F + A) / B"]


def test_f17_8_no_natural_language_limit_understanding():
    res = review("Pressure must not exceed 10 bar and at least 5 bar")
    assert texts(res, "PT6_MATH") == []
    assert texts(res) == ["10", "5"]            # only the literal numbers; no limit/negation semantics


# ------------------------------------------------------------------ F17.9 project terms
def test_f17_9_no_builtin_vocabulary_and_default_empty():
    words = "Close the pressure control valve and remove the cover panel before you start."
    res = review(words)
    assert res["protected_spans"] == [] and res["abstentions"] == []
    assert res["options"]["project_terms"] == []


def test_f17_9_explicit_terms_are_whole_word_longest_first_exact_case():
    text = "pressure control valve; Pressure valve; control valve; controlvalves"
    res = review(text, project_terms=("control valve", "pressure control valve"))
    assert texts(res, "PT3_TERM") == ["pressure control valve", "control valve"]
    assert res["protected_spans"][1]["start"] == text.index("; control valve") + 2


def test_f17_9_terms_inside_longer_words_do_not_match():
    text = "xcontrol valve; control valves; control valve"
    res = review(text, project_terms=("control valve",))
    assert [s["start"] for s in res["protected_spans"]] == [text.rindex("control valve")]
    assert_offsets(text, res)


def test_f17_9_terms_with_digits_prefer_term_over_identifier():
    res = review("Use pump P-101 here", project_terms=("pump P-101",))
    assert spans(res) == [("PT3_TERM", "pump P-101")]


def test_f17_9_term_partially_overlapping_quote_is_discarded_with_conflict():
    text = 'x "alpha beta" y'
    res = review(text, project_terms=('x "alpha',))   # straddles the opening quote
    assert spans(res) == [("PT7_QUOTE", '"alpha beta"')]
    assert abst(res, "overlap_conflict") == ['x "alpha']


def test_f17_9_term_inside_quote_is_absorbed():
    res = review('see "alpha beta" gamma', project_terms=("alpha",))
    assert spans(res) == [("PT7_QUOTE", '"alpha beta"')] and res["abstentions"] == []


@pytest.mark.parametrize("bad", ["abc", ("",), (" x",), ("x ",), (1,), ["a"], (None,)])
def test_f17_9_invalid_project_terms_raise(bad):
    with pytest.raises(ValueError):
        checker().ReviewOptions(project_terms=bad)


# ------------------------------------------------------------------ F17.10 unit boundary deferral
def test_f17_10_following_letter_token_is_deferred_not_classified():
    text = "Set 10 bar and 20 foo."
    res = review(text)
    assert abst(res, "unit_boundary_deferred") == ["bar", "foo"]
    assert texts(res) == ["10", "20"]                  # no PT2 span exists
    assert not any(s["cls"] == "PT2_UNIT" for s in res["protected_spans"])


def test_f17_10_glued_unit_splits_numeric_prefix():
    for glued, num, rest in (("20°C", "20", "°C"), ("10kPa", "10", "kPa"), ("5%", "5", "%")):
        res = review(f"at {glued} now")
        assert texts(res) == [num] and abst(res, "unit_boundary_deferred") == [rest]


def test_f17_10_no_deferral_for_list_markers_or_line_breaks():
    assert review("1. Close the valve")["abstentions"] == []
    assert review("Set 10\nbar")["abstentions"] == []
    assert review("Set 10, bar")["abstentions"] == []


# ------------------------------------------------------------------ F17.11 safety regions
def test_f17_11_warning_and_caution_regions_cover_paragraph():
    text = "Intro line.\n\nWARNING: Do not open V-201\nwhile at 10 bar.\n\ncaution check it\n\nOther."
    res = review(text)
    assert_offsets(text, res)
    regs = res["safety_regions"]
    assert [r["label"] for r in regs] == ["WARNING", "CAUTION"]
    assert regs[0]["excerpt"] == "WARNING: Do not open V-201\nwhile at 10 bar."
    assert regs[1]["excerpt"] == "caution check it"
    assert all(r["rewrite_allowed"] is False for r in regs)
    assert (regs[0]["line_start"], regs[0]["line_end"]) == (3, 4)
    assert (regs[1]["line_start"], regs[1]["line_end"]) == (6, 6)
    assert "V-201" in texts(res)                         # spans inside safety text still reported


@pytest.mark.parametrize("text", ["**WARNING** hot", "> # _CAUTION_: hot", "  WARNING.", "WARNING"])
def test_f17_11_marker_prefix_characters_are_ignored(text):
    assert len(review(text)["safety_regions"]) == 1


@pytest.mark.parametrize("text", ["WARNINGS apply", "Be careful: WARNING", "CAUTIONARY tale", "Warn user"])
def test_f17_11_non_markers_are_not_regions(text):
    assert review(text)["safety_regions"] == []


def test_f17_11_danger_is_abstention_not_region():
    res = review("DANGER: high voltage")
    assert res["safety_regions"] == []
    assert abst(res, "safety_marker_undecided") == ["DANGER: high voltage"]


# ------------------------------------------------------------------ F17.12 / 17.13 no writing behavior
MODAL_TEXT = ("You should close the valve; it may leak, and the pump must not exceed 10 bar. "
              "Do not open it and drain the tank, which could be long, long, long, long, long, long, long, "
              "long, long, long, long, long, long, long, long, long, long, long, long, long, long words.\n"
              "WARNING: you should never remove the cover.")


def test_f17_12_zero_findings_zero_rules_zero_suggestions():
    res = review(MODAL_TEXT)
    assert res["findings"] == [] and res["rules_executed"] == []
    s = res["summary"]
    assert s["findings"] == 0 and s["with_suggestion"] == 0
    assert res["stage"] == "GATE_F_LIST_FREE_FOUNDATION"


def test_f17_12_requested_rules_are_reported_not_executed():
    res = review(MODAL_TEXT, enabled_rules=("A01", "A03", "A07"))
    assert res["findings"] == [] and res["rules_executed"] == []
    assert [r["rule_id"] for r in res["rules_not_executed"]] == ["A01", "A03"]
    assert all(r["reason"] == "GATE_W_BLOCKED" for r in res["rules_not_executed"])


def test_f17_12_defaults_and_invalid_options():
    c = checker()
    assert c.ReviewOptions().enabled_rules == () and c.ReviewOptions().a02_rewrite is False
    for bad in ({"a02_rewrite": True}, {"enabled_rules": ("A99",)}, {"enabled_rules": ["A01"]},
                {"enabled_rules": "A01"}):
        with pytest.raises(ValueError):
            c.ReviewOptions(**bad)


def test_f17_13_apply_decisions_fails_closed():
    c = checker()
    text = "Set 10 bar. You should close V-201."
    res = c.review_technical_text(text)
    out = c.apply_decisions(text, res, {"W-0001": {"state": "accepted", "note": None}})
    assert out["revised_text"] == text and out["applied"] == []
    assert out["status"] == "REFUSED_FOUNDATION_ONLY"
    assert out["original_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert c.apply_decisions(text, res, {})["revised_text"] == text


# ------------------------------------------------------------------ F17.14 honest capabilities, labels
def test_f17_14_capabilities_are_honest():
    res = review("x")
    cap = {c["id"]: c["status"] for c in res["capabilities"]}
    for supported in ("source_offsets", "numeric_literals", "engineering_identifiers", "quoted_spans",
                      "project_terms", "safety_labels_warning_caution"):
        assert cap[supported] == "supported", supported
    assert cap["math_expressions"] == "partial"
    for unsupported in ("unit_symbols", "negation_detection", "limit_phrases", "modal_obligation_classes",
                        "sentence_segmentation", "writing_rules", "suggestions", "apply_decisions",
                        "safety_label_danger"):
        assert cap[unsupported] == "unsupported", unsupported
    assert res["lexical_lists_consulted"] == []


def test_f17_14_labels_and_no_forbidden_claims():
    res = review(MODAL_TEXT, enabled_rules=("A01",))
    assert res["labels"] == ["STE-inspired writing suggestions", "ASD-STE100 conformance not verified"]
    assert not FORBIDDEN_CLAIM.search(json.dumps(res["capabilities"] + res["limitations"] + res["labels"]))
    assert not FORBIDDEN_CLAIM.search(json.dumps({k: v for k, v in res.items()
                                                   if k not in ("protected_spans", "abstentions",
                                                                "safety_regions", "options")}))


# ------------------------------------------------------------------ F17.15 robustness
def test_f17_15_types_and_empty_inputs():
    c = checker()
    for bad in (None, b"x", 12, ["a"]):
        with pytest.raises(TypeError):
            c.review_technical_text(bad)
    with pytest.raises(TypeError):
        c.review_technical_text("x", "not options")
    for t in ("", "   ", "\n\n", "\u2028", "\x00", "\ud800 1,000 \udfff"):
        assert review(t)["findings"] == []


def test_f17_15_large_and_fast():
    unit = 'Set 10,5 bar at V-201 "q" 0.5 < x <= 1.5 WARNING é😀 ' * 4
    text = (unit * 2000)[:200_000]
    res = review(text)
    assert res["input"]["chars"] == 200_000
    assert_offsets(text, res)
    small = (unit * 40)[:5000]
    t0 = time.perf_counter()
    review(small)
    assert time.perf_counter() - t0 < 1.0


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(alphabet=st.sampled_from(list('012 ,.-+−e×^=<>"“”`()xPV:/\n\r°%AWNIGO😀é_')), max_size=300))
def test_f17_property_invariants(text):
    """17.1, 17.3, 17.12, 17.15 over adversarial punctuation/digit soup."""
    res = review(text)
    assert res == review(text)
    assert_offsets(text, res)
    sp = res["protected_spans"]
    for left, right in zip(sp, sp[1:]):
        assert left["end"] <= right["start"]
    assert res["findings"] == [] and res["summary"]["with_suggestion"] == 0
    assert res["input"]["sha256"] == hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@settings(max_examples=100, deadline=None)
@given(st.text(max_size=400))
def test_f17_property_arbitrary_unicode_no_raise(text):
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return
    res = review(text)
    assert_offsets(text, res)
    assert res["findings"] == []


# ------------------------------------------------------------------ F17.16 separation / no lists
APP = ROOT / "app"


def test_f17_16_stdlib_only_and_not_imported_by_p1_modules():
    tree = ast.parse((APP / "ste_checker.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)
    for p in APP.glob("*.py"):
        if p.name != "ste_checker.py":
            assert "ste_checker" not in p.read_text(encoding="utf-8"), p.name


# Words from design Appendix A lists (A.1, A.2, A.6, A.7, A.8) and modals; none may appear as a literal.
LIST_WORDS = {"remove", "disconnect", "close", "open", "wait", "replace", "clean", "check", "test", "set",
              "before", "after", "until", "while", "unless", "once", "not", "no", "never", "without",
              "except", "neither", "nor", "none", "nothing", "must", "shall", "should", "may", "might",
              "could", "can", "cannot", "only", "all", "any", "each", "every", "both", "within", "above",
              "below", "min", "max", "exceed", "exceeds", "minimum", "maximum", "the", "and", "or", "of",
              "to", "at", "in", "on", "as", "by", "for", "is", "are", "be", "if", "then", "than", "from",
              "with", "per", "a", "an", "it", "they", "them", "these", "those", "approx", "fig", "ref"}


def test_f17_9_module_embeds_no_lexical_lists():
    tree = ast.parse((APP / "ste_checker.py").read_text(encoding="utf-8"))
    strings = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for n in strings:
        assert n.value.strip().lower() not in LIST_WORDS, (n.value, n.lineno)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Set, ast.List, ast.Tuple)):
            words = [e.value for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                     and re.fullmatch(r"[a-z]{2,}", e.value)]
            assert len(words) < 4, f"word-list literal at line {n.lineno}"


def test_f17_10_unclassified_digit_tokens_abstain():
    res = review("at 10:30 and 5+3 now")
    assert abst(res, "unclassified_numeric_token") == ["10:30", "5+3"]
    assert res["protected_spans"] == []


# ====================================================================== F1-F4 correction (2026-11-10)
# Regression tests for the four contract violations in private report 24.  Names start with
# ``test_corr_`` so they can be selected with ``-k test_corr_``.

def _ab(res, kind):
    return [(a["start"], a["excerpt"]) for a in res["abstentions"] if a["kind"] == kind]


# ---- F1 / R17.7: a double quote directly after a digit is never a definite closer
def test_corr_f1_opener_then_digit_quote_is_not_paired():
    text = '"Fit 5" pipe'
    res = review(text)
    assert_offsets(text, res)
    assert not texts(res, "PT7_QUOTE")                         # no guessed span across the mark
    assert _ab(res, "quote_char_ambiguous") == [(6, '"')]       # the digit-following mark
    assert _ab(res, "unclosed_quote") == [(0, '"')]             # the opener stays unresolved
    assert texts(res, "PT1_NUMBER") == ["5"]                    # numeric protection is kept


def test_corr_f1_neighbouring_valid_quote_is_still_protected():
    text = 'Use "Fit 5" pipe and "ok" now'
    res = review(text)
    assert_offsets(text, res)
    assert spans(res) == [("PT1_NUMBER", "5"), ("PT7_QUOTE", '"ok"')]
    assert _ab(res, "quote_char_ambiguous") == [(text.index('5"') + 1, '"')]
    assert _ab(res, "unclosed_quote") == [(text.index('"Fit'), '"')]


def test_corr_f1_ambiguous_mark_between_two_valid_quotes():
    text = '"one" then 5" pipe then "two"'
    res = review(text)
    assert_offsets(text, res)
    assert texts(res, "PT7_QUOTE") == ['"one"', '"two"']
    assert len(_ab(res, "quote_char_ambiguous")) == 1 and not _ab(res, "unclosed_quote")


def test_corr_f1_no_pt7_span_may_end_with_a_digit_quote():
    for text in ('"x 5" y', '"a 5" b "c 6" d', 'say "n=5" ok', '"A" 5" "B"'):
        res = review(text)
        assert_offsets(text, res)
        for s in res["protected_spans"]:
            if s["cls"] == "PT7_QUOTE":
                assert not (s["text"].startswith('"') and s["text"][-2:-1].isdigit()), (text, s)


def test_corr_f1_unambiguous_quotes_and_backticks_unchanged():
    text = 'Press "OPEN 5 bar" then `run 5` and “STOP 7” at "A 5 b"'
    res = review(text)
    assert texts(res, "PT7_QUOTE") == ['"OPEN 5 bar"', "`run 5`", "“STOP 7”", '"A 5 b"']
    assert not _ab(res, "quote_char_ambiguous")


# ---- F2 / R17.10: ownership by another class must not hide the unit-boundary abstention
def test_corr_f2_project_term_after_number_still_abstains():
    text = "10 bar"
    configured = review(text, project_terms=("bar",))
    assert_offsets(text, configured)
    assert spans(configured) == [("PT1_NUMBER", "10"), ("PT3_TERM", "bar")]   # ownership unchanged
    assert _ab(configured, "unit_boundary_deferred") == [(3, "bar")]
    plain = review(text)
    assert spans(plain) == [("PT1_NUMBER", "10")]
    assert _ab(plain, "unit_boundary_deferred") == [(3, "bar")]
    assert not any(s["cls"] == "PT2_UNIT" for s in configured["protected_spans"])


def test_corr_f2_other_owner_classes_and_nearby_boundaries():
    # identifier-owned letter-initial token directly after a bare number
    assert _ab(review("Set 10 P-101"), "unit_boundary_deferred") == [(7, "P-101")]
    # multi-word project term that starts at the token
    t = "Set 5 bar valve now"
    res = review(t, project_terms=("bar valve",))
    assert texts(res, "PT3_TERM") == ["bar valve"]
    assert _ab(res, "unit_boundary_deferred") == [(6, "bar")]
    # still no deferral across a comma, a line break, or when the number is not bare
    for text in ("10, bar", "10\nbar", "10; bar"):
        assert _ab(review(text, project_terms=("bar",)), "unit_boundary_deferred") == [], text
    # quoted material stays opaque; a token inside the same math span is not a unit candidate
    assert _ab(review('10 "bar"', project_terms=("bar",)), "unit_boundary_deferred") == []
    assert _ab(review("10 x = 5"), "unit_boundary_deferred") == []


def test_corr_f2_deterministic_unique_and_non_overlapping():
    text = "10 bar and 20 bar"
    a = review(text, project_terms=("bar",))
    assert a == review(text, project_terms=("bar",))
    assert [e for _, e in _ab(a, "unit_boundary_deferred")] == ["bar", "bar"]
    keys = [(x["kind"], x["start"], x["end"]) for x in a["abstentions"]]
    assert len(keys) == len(set(keys))
    sp = a["protected_spans"]
    for left, right in zip(sp, sp[1:]):
        assert left["end"] <= right["start"]
    assert_offsets(text, a)


# ---- F3 / R17.14: the A07 guard over suggestions is listed unsupported
def test_corr_f3_a07_guard_capability_is_unsupported():
    res = review("x")
    guard = [c for c in res["capabilities"] if c["id"] == "a07_suggestion_guard"]
    assert len(guard) == 1 and guard[0]["status"] == "unsupported"
    assert guard[0]["note"]
    ids = [c["id"] for c in res["capabilities"]]
    assert len(ids) == len(set(ids))
    assert {c["status"] for c in res["capabilities"]} <= {"supported", "partial", "unsupported"}
    assert res["lexical_lists_consulted"] == [] and res["findings"] == []
    assert not FORBIDDEN_CLAIM.search(json.dumps(res["capabilities"]))


# ---- F4 / R17.13: refusal must not depend on comparable decision keys
@pytest.mark.parametrize("decisions", [
    {1: "accepted", "a": "accepted"},                     # the reported reproducer
    {"W-0001": {"state": "accepted", "note": None}},      # ordinary string keys
    {}, None, [], ("a", 1), "abc", 5,
    {(1, 2): "x", None: "y", 3.5: "z"},                   # other incomparable key types
])
def test_corr_f4_apply_decisions_refuses_for_any_decisions(decisions):
    c = checker()
    text = "Set 10 bar. You should close V-201."
    out = c.apply_decisions(text, None, decisions)
    assert out["status"] == "REFUSED_FOUNDATION_ONLY"
    assert out["revised_text"] == text and out["applied"] == [] and out["conflicts"] == []
    assert out["original_sha256"] == out["revised_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert set(out) == {"status", "reason", "original_sha256", "revised_text", "revised_sha256",
                        "applied", "skipped", "conflicts"}


def test_corr_f4_decisions_are_not_interpreted():
    class Boom(dict):
        def __iter__(self):
            raise AssertionError("decisions must not be iterated")
    out = checker().apply_decisions("x", None, Boom({1: "a", "b": "c"}))
    assert out["revised_text"] == "x" and out["applied"] == []


# ====================================================================== F2 residual (2026-11-11)
# R17.10 must hold whoever owns the bare number: the boundary depends on the lexical number token,
# not on which class owns its characters in the final span map.

def _deferred(res):
    return [(a["start"], a["end"], a["excerpt"]) for a in res["abstentions"]
            if a["kind"] == "unit_boundary_deferred"]


@pytest.mark.parametrize("terms,expected_spans", [
    ((), [("PT1_NUMBER", "10")]),
    (("bar",), [("PT1_NUMBER", "10"), ("PT3_TERM", "bar")]),
    (("10",), [("PT3_TERM", "10")]),
    (("10 bar",), [("PT3_TERM", "10 bar")]),
    (("10", "bar"), [("PT3_TERM", "10"), ("PT3_TERM", "bar")]),
])
def test_corr_f2r_ten_bar_abstains_whoever_owns_the_number(terms, expected_spans):
    text = "10 bar"
    res = review(text, project_terms=terms)
    assert spans(res) == expected_spans                   # ownership and priority unchanged
    assert _deferred(res) == [(3, 6, "bar")]               # exactly one, exact offsets
    assert_offsets(text, res)
    assert not any(s["cls"] == "PT2_UNIT" for s in res["protected_spans"])
    assert res == review(text, project_terms=terms)       # deterministic


@pytest.mark.parametrize("text,term,start,token", [
    ("Use 1,000 psi now", "1,000", 11, "psi"),            # decimal-separator form owned by a term
    ("Set -20 degC", "-20", 8, "degC"),                    # signed number owned by a term
    ("Set 1.0E5 Pa", "1.0E5", 11 - 4, "Pa"),               # exponent form owned by a term
    ("x 5 m", "5", 4, "m"),
])
def test_corr_f2r_alternate_numeric_forms_owned_by_a_term(text, term, start, token):
    res = review(text, project_terms=(term,))
    assert texts(res, "PT3_TERM") == [term]
    assert _deferred(res) == [(text.index(token, text.index(term) + len(term)),
                               text.index(token, text.index(term) + len(term)) + len(token), token)]
    assert text[_deferred(res)[0][0]:_deferred(res)[0][1]] == token
    assert_offsets(text, res)


def test_corr_f2r_glued_number_owned_by_a_term_still_defers_the_remainder():
    text = "at 20\u00b0C now"
    for terms in ((), ("20",), ("20\u00b0C",)):
        res = review(text, project_terms=terms)
        assert _deferred(res) == [(5, 7, "\u00b0C")], terms
        assert_offsets(text, res)


@pytest.mark.parametrize("text,terms", [
    ("10, bar", ("10",)), ("10; bar", ("10", "bar")), ("10\nbar", ("10", "bar")),
    ("10. bar", ("10",)),                                   # token ends with sentence punctuation
    ('"10" bar', ("10",)),                                  # number inside a quote is opaque
    ('"10 bar"', ("10",)),
    ("10 5", ("10",)),                                      # next token does not start with a letter
    ("10 -x", ("10",)),
    ("P-10 bar", ("10",)),                                  # digits inside an identifier are not a bare number
    ("10", ("10",)),                                        # nothing follows
])
def test_corr_f2r_exclusions_still_hold_with_owned_numbers(text, terms):
    res = review(text, project_terms=terms)
    assert _deferred(res) == [], (text, terms, _deferred(res))
    assert_offsets(text, res)


def test_corr_f2r_no_duplicates_no_overlap_and_stable_order():
    text = "10 bar and 20 bar, 10 bar"
    res = review(text, project_terms=("10", "20", "bar"))
    d = _deferred(res)
    assert [e for _, _, e in d] == ["bar", "bar", "bar"] and d == sorted(d)
    keys = [(a["kind"], a["start"], a["end"]) for a in res["abstentions"]]
    assert len(keys) == len(set(keys))
    sp = res["protected_spans"]
    for left, right in zip(sp, sp[1:]):
        assert left["end"] <= right["start"]
    assert [a["id"] for a in res["abstentions"]] == [f"ABS-{i:04d}" for i in range(1, len(keys) + 1)]
    assert_offsets(text, res)


def test_corr_f2r_still_no_findings_rules_or_lists():
    res = review("10 bar", project_terms=("10 bar",), enabled_rules=("A01",))
    assert res["findings"] == [] and res["rules_executed"] == [] and res["lexical_lists_consulted"] == []
    assert res["summary"]["with_suggestion"] == 0
