# Design Document

> **Provenance.** Created 2026-11-07 (UTC+3) as the second file of the P2 spec, after `requirements.md` (timestamps in the private freeze record). Written before any P2 test or code. It does not describe, and did not govern, any P1 code.
> **Status:** FROZEN FOR OWNER REVIEW. Items marked `OWNER_REVIEW_REQUIRED` or `PROPOSED` are not final. Numbers in examples marked **[computed]** were produced on 2026-11-07 by throwaway scripts that are not part of the repository; the planned tests recompute them.

## Overview

`app/ste_checker.py` (decision D-11) is a pure, stateless, standard-library module with two public functions: `review_technical_text` and `apply_decisions`. It finds writing issues, explains them, and, only when it can prove a suggestion keeps every protected engineering token and every obligation, proposes replacement text. A human decides. The original text is never changed.

```
text ──► segmentation ──► protected spans + flags ──► rule detectors (A01 A03 A04 A02 [A05 A06])
                                   │                          │
                                   └────────► eligibility ──► suggestion builder ──► A07 guard ──► Finding
Finding[] + human decisions ──► apply_decisions ──► guard again ──► revised_text (new string)
```

Boundaries:
- New: `app/ste_checker.py`; a "Writing review" section in `app/ui.py`; additive fields in `ReviewCard` (`app/report.py`).
- Unchanged: `app/core.py` semantics, `app/ingest.py`, `app/api.py`, `app/mcp_server.py`. The module may import only `split_lines`, `ENTRY`, `CSV_ENTRY` from `app/core.py`, read-only. Nothing in the audit imports the module (R10).
- No new package. No network. No LLM.

## 1. Components and interfaces

### 1.1 Options
```python
@dataclass(frozen=True)
class ReviewOptions:
    enabled_rules: tuple = ("A01", "A03")                 # A07 guard always runs and is not selectable (R3.1); A02 off until separately approved; A04 off until G-A04 (U-01); A05, A06 off (D-04, D-05, D-12)
    max_words: int = 20                                    # D-01
    project_terms: tuple = ()                              # multi-word terms treated as one protected token
    a02_rewrite: bool = False                              # U-02: no obligation-changing automatic rewrite; detect only unless separately approved
    cluster_notice: int = 4                                # A05 only
    cluster_warning: int = 6                               # A05 only
```
Invalid values (`max_words < 5`, unknown rule id, non-tuple) raise `ValueError`. `A07` is the mandatory preservation guard, not an option: it always runs, and including or omitting `"A07"` in `enabled_rules` changes nothing (R3.1). Unknown keys cannot be passed (frozen dataclass).

### 1.2 Functions
```python
review_technical_text(text: str, options: ReviewOptions | None = None) -> dict
apply_decisions(text: str, result: dict, decisions: dict) -> dict
```
`text` must be `str`, otherwise `TypeError`. For every `str` the function returns a result (R1.5).

### 1.3 Result (JSON-serializable dictionary)
```
{
 "schema_version": 1,
 "labels": ["STE-inspired writing suggestions", "ASD-STE100 conformance not verified"],
 "input": {"chars": int, "lines": int, "sha256": str, "review_units": int, "sentences": int},
 "options": { ...echo... },
 "findings": [Finding...],
 "skipped": [{"line": int, "kind": "blank|heading|code|table_row|measurement_row|identifier_only"}],
 "summary": {"findings": int, "with_suggestion": int, "abstained": int, "clarification_requests": int,
             "suppressed_by_guard": int, "by_rule": {"A01": int, ...}},
 "limitations": [str]
}
Finding = {
 "id": "W-0001",
 "rule_id": "A01|A02|A03|A04|A05|A06",
 "category": "length|voice|obligation|multi_instruction|term_string|reference",
 "severity": "notice|warning",
 "span": {"start": int, "end": int, "line_start": int, "line_end": int, "sentence_index": int},
 "original": str,                              # == text[start:end]
 "explanation": str,
 "confidence": "high|medium|low",
 "limitation": str,
 "safety_context": bool,
 "suggestion": {"text": str, "edits": [{"start": int, "end": int, "replacement": str}],
                "needs_human_review": true, "badges": [str]} | null,
 "clarification": {"question": str, "why": str} | null,       # A03 always; others null
 "candidates": [str] | null,                                   # A06 only, informational
 "abstain_reason": str | null,
 "guard": {"status": "passed|suppressed|not_applicable", "failed": str | null},
 "decision": {"state": "pending", "note": null}               # the module always returns "pending"
}
```
Exactly one of these holds for every finding (explicit abstention contract): (a) `suggestion` is set, `abstain_reason` is null; (b) `suggestion` is null and `abstain_reason` is a non-empty string from the catalogue in §7.4; (c) `clarification` is set (A03), `suggestion` is null and `abstain_reason` is the fixed string `"clarification required"`.

### 1.4 Decisions
```
decisions = { "W-0003": {"state": "accepted"|"rejected"|"clarified", "note": str|None}, ... }
```
`pending` is the default for any id not present. See §6 for effects.

## 2. Text model, segmentation and offsets

### 2.1 Offsets
All offsets are Python `str` indices (code points) into the unmodified input. `end` is exclusive. `line_start` and `line_end` are 1-based editor lines computed with `split_lines`. The module never normalizes whitespace, case, Unicode or line endings; spans may contain `\r\n`.

### 2.2 Units
1. Split into lines (`split_lines`), keeping each line's start offset.
2. Mark a line **skipped** (kind in brackets) if: blank [blank]; starts with `#` [heading]; inside a ``` fence (the fence lines too) [code]; contains two or more `|` [table_row]; matches `ENTRY` or `CSV_ENTRY` [measurement_row]; consists only of one token that contains a digit [identifier_only].
3. Group consecutive non-skipped, non-blank lines into a **paragraph**. A line that starts with `-`, `*`, `n.` or `n)` (n = digits) starts a new **unit**; a paragraph break (blank line) also ends a unit. Lines inside a unit are joined by a single space *for analysis only*; each analysis character keeps its original offset.

### 2.3 Sentence boundaries
Inside a unit, scan for `.`, `!`, `?`. A character is a **boundary** if all hold:
1. it is followed by whitespace or the end of the unit (so `0.5`, `e.g.` interior, `V-2.01`, `1.0E5` never split);
2. the next non-space character is an uppercase ASCII letter, a digit, a quote (`"` `“`), `(` or `[`, or there is none;
3. if it is `.`: the whitespace-delimited token before it, lower-cased and stripped of `(`, is **not** in the abbreviation list (Appendix A.4), **or** the token before that one contains a digit (so `5 min.` ends a sentence while `max. 10 bar` does not).
`:` and `;` are never boundaries. A boundary after an identifier (`V-201.`) is valid when 1 and 2 hold. **[computed]** 10 of 10 fixed cases pass (requirement 2.9 plus `Wait 5 min. Then open the valve.`=2, `See e.g. sec. 4.2 for details. Stop.`=2, `Use min. 5 bar at the inlet. Stop.`=2, `Apply 1.0E5 Pa. Read the gauge.`=2).
Known limit: `Set to 5. 10 minutes later …` is split after `5.` (digit after the period counts as a start); and an initial such as `Gauge A. Close it.` is split. Both are documented in `limitations`.

### 2.4 Word count
Tokens are maximal runs of non-whitespace characters. A word is a token containing at least one ASCII letter, a non-ASCII letter, or a digit. `10 bar` is two words; a hyphenated compound is one. **[computed]** values for the fixed examples are in requirement 4.4.

## 3. Protected spans

### 3.1 Representation
```python
@dataclass(frozen=True)
class ProtectedSpan:
    start: int; end: int            # offsets into the analyzed text
    cls: str                        # "PT1_NUMBER" | "PT1_IDENT" | "PT2_UNIT" | "PT3_TERM" | "PT4_NEG" | "PT5_LIMIT" | "PT7_QUOTE"
    text: str                       # exact characters
```
`protected_sequence(s) -> list[(cls, text)]` returns the spans of a string in order of position.

### 3.2 Tokens and core
A token's **core** removes leading `( [ " “ '` and trailing `. , ; : ) ] " ” ' ! ?`. Offsets of a span refer to the core.

### 3.3 Classes and detection (applied in this order; a character belongs to the first class that claims it)
1. **PT7_QUOTE**: text between `"…"`, `“…”`, or `` `…` `` in one unit. Opaque.
2. **PT3_TERM**: each phrase in `project_terms` (exact case, whole words, longest first). Atomic.
3. **PT1_NUMBER / PT1_IDENT**: a core containing a digit. It is a **number** if it contains no letters other than an `e`/`E` between digits, `×`, or `x` in `×`-style exponent forms; otherwise an **identifier**. Whole cores are protected, so `1,000`, `10,5`, `1.0E5`, `−5`, `-20`, `+3.2`, `0.5-1.0`, `0.5–1.0`, `2.5×10^-3`, `P-101`, `V-201`, `M8-1.25`, `6205-2RS`, `ISO-9001`, `20°C` are single spans. (A pattern that splits tokens was tried and failed on `V-201`, `-20`, `M8-1.25`; this token rule replaces it.)
4. **PT2_UNIT**: the core of the token directly after a PT1_NUMBER token, if it does not start with a digit and is not in the function-word list A.8a (`to and or of the at in on as by for is are be if then than from with per a an`). Over-inclusion is intended.
5. **PT4_NEG**: tokens in A.7a (case-insensitive) and any core ending in `n't`.
6. **PT5_LIMIT**: phrases in A.7b (case-insensitive, token sequences, longest first).

### 3.4 Math-bearing sentence
A sentence is **math-bearing** if it has a token whose core consists only of characters from `= < > ≤ ≥ ≈ ± × ÷ ^`, or contains a letter immediately before or after `=` inside one token (`x=5`). **[computed]** `The pressure P = F / A is constant.`, `Keep 0.5 < x ≤ 1.5 at all times.`, `Set T = 20 degC.` are math-bearing; `Close the valve and wait.` is not. Math-bearing sentences get no suggestion (R3.7). Span detection for formulas was tried and was unreliable, so the sentence-level flag is used instead.

## 4. Obligation, scope and safety flags

### 4.1 Obligation classes
Precedence: `PROHIBIT > REQUIRE > RECOMMEND > PERMIT > POSSIBLE > NONE`.
- PROHIBIT: `do not`, `must not`, `shall not`, `never`, `is not permitted`, `is prohibited`.
- REQUIRE: `must`, `shall`, `is required to`, `need to`, `always`, or the clause is an instruction clause (§4.2).
- RECOMMEND: `should`, `ought to`.
- PERMIT: `may`.
- POSSIBLE: `might`, `could`, `can`, `cannot`.
`obligation_sequence(s)` = flattened list of classes, one per clause, where clauses come from splitting each sentence of `s` at the coordinators of A.9 and `;`, ignoring a leading `then`. The guard compares the sequence of the original span with that of the replacement (§5.3).

### 4.2 Instruction clause
After optional `Do not`, `Never` or `Always`, the first word `w` of a clause (lower-case) makes it an instruction clause if `w` is in A.1, or `w` is in A.2 and the next word is in A.5.

### 4.3 Instruction-bearing sentence (scope of A03; also used by A02 badges)
True if (a) the sentence starts, after an optional list marker, `Note:` or `Then,`, with an instruction clause or with `Do not`/`Never`/`Always`; or (b) it contains one of `should, ought, may, might, could, must, shall` together with a word of A.9d (`you operator technician mechanic engineer user personnel`); or (c) it contains one of `must, shall, should, may, might, could` followed by `be` and a participle of A.3. **[computed]** this gives the expected A03/A02 flags for the 16 scope examples in the private rule-refinement notes and §7 (e.g. `Pressure may exceed 10 bar during start-up.` → no; `This might damage the seal.` → no).

### 4.4 Safety statement
A paragraph whose first three tokens (after removing leading `* _ # > -` marks) include `WARNING`, `CAUTION` or `DANGER` (case-insensitive, optional `:`) is a safety statement for its whole length. `DANGER` is a conservative addition to D-08 (`OWNER_REVIEW_REQUIRED`, U-04).

## 5. Suggestions, eligibility, guard

### 5.1 Suggestion object
`edits` are non-overlapping `{start, end, replacement}` against the original text; `text` is the replacement for `[span.start, span.end)`. `needs_human_review` is always `true`. `badges` are fixed strings (A02: `restructures the sentence`; A04: `removes 'and/then': confirm the order`).

### 5.2 Eligibility (all conditions must hold; otherwise abstain with the named reason)
| Rule | Conditions for a suggestion |
|---|---|
| A01 | sentence not math-bearing; not in a safety statement; has a `;` outside brackets/quotes; both parts ≥ 4 words; left part does not start with `If, When, Before, After, Until, Unless, While, Once`; the new second sentence does not start with `and, but, or, which, that, it, they, them`; guard passes |
| A02 | `a02_rewrite` is true; modal is `must`/`shall`; no `by` after the participle; participle is `rewrite_ok` (A.3); subject phrase before the modal has ≤ 6 tokens; not math-bearing; not safety; guard passes |
| A03 | never |
| A04 | (PROPOSED) count ≥ 2; no abstain cause of §8.3; guard passes |
| A05, A06 | never |

### 5.3 Guard (A07), run for every suggestion and again in `apply_decisions`
Let `O` be the original span text and `S` the replacement.
1. `protected_sequence(O) == protected_sequence(S)` as ordered lists of `(class, exact text)`; any difference (missing, added, changed character, different order) fails with `failed = "protected_tokens"`.
2. `obligation_sequence(O) == obligation_sequence(S)`; else `failed = "obligation"`.
3. After applying the edit, `text[:start] + S + text[end:]` has `text[:start]` and `text[end:]` byte-identical to the original; else `failed = "outside_span"`.
4. No sentence in `S` starts with `and, but, or, which, that, it, they, them` (case-insensitive); else `failed = "sentence_start"`.
5. Capitalization differences between `O` and `S` are only at the first letter of a sentence whose first token is not protected; else `failed = "capitalization"`.
6. `S` contains none of `must, shall, should, may, can` unless `O` contains the same number of the same word in the same order; else `failed = "modal_word"`. (This makes "never change `must`, `shall`, `should`, `may` or `can`" a literal check.)
On failure: `suggestion = null`, `guard = {status: "suppressed", failed}`, `abstain_reason = "guard: <failed> would change"`, and `summary.suppressed_by_guard` is incremented.
**A02 note:** converting `must be replaced` to `Replace` removes the word `must`; check 6 would fail. Resolution (`OWNER_REVIEW_REQUIRED`, U-02): check 6 is relaxed for A02 only to "the modal word is removed and the obligation sequence is unchanged (REQUIRE→REQUIRE, PROHIBIT→PROHIBIT)", the suggestion carries the badge `restructures the sentence`, and `a02_rewrite=False` turns it off. If the owner answers that this still counts as changing `must`, A02 becomes detection-only.

### 5.4 Abstention catalogue (the only allowed `abstain_reason` strings)
`"clarification required"`, `"no safe split; shorten manually"`, `"safety statement: rewrite manually"`, `"math-bearing sentence: rewrite manually"`, `"modal 'should' marks a recommendation; the author must decide"`, `"agent named: the author must decide who acts"`, `"verb form needs manual rewrite"`, `"subject phrase too long"`, `"subordinator: scope would change"`, `"negation: scope would change"`, `"bracketed or quoted text"`, `"new sentence would start with a pronoun or conjunction"`, `"mixed content: not every part is an instruction"`, `"restructuring changes meaning; edit manually"`, `"guard: protected_tokens would change"`, `"guard: obligation would change"`, `"guard: outside_span would change"`, `"guard: sentence_start would change"`, `"guard: capitalization would change"`, `"guard: modal_word would change"`.

## 6. Human decision contract and `apply_decisions`

### 6.1 States
| State | Who | Valid for | Effect on revised text |
|---|---|---|---|
| `pending` | default | all | none |
| `accepted` | human | findings with a `suggestion` | edit applied |
| `rejected` | human | all | none |
| `clarified` | human | A03 findings (and any finding without a suggestion) | none; the author edits the source text outside the tool and re-runs the review |
`accepted` on a finding without a suggestion, and `accepted` on A03, are refused with reason `no suggestion`.

### 6.2 `apply_decisions` algorithm
1. Raise `ValueError` if `sha256(text) != result.input.sha256` (the result belongs to other text).
2. Collect findings with state `accepted` and a non-null suggestion; others go to `skipped` with a reason.
3. Detect overlapping edits; both findings of an overlap go to `conflicts`; neither is applied. (A01 and A04 may propose overlapping edits on the same sentence; they are never combined.)
4. Apply remaining edits from the last offset to the first on a copy.
5. Run the guard checks 1–6 per applied edit on the final string (with the A02 relaxation); on failure return `revised_text == text` and list the failure.
6. Return `{original_sha256, revised_text, revised_sha256, applied, skipped, conflicts}`. The input string object is never changed; the module keeps no state.
Idempotence target: `review_technical_text(revised_text)` has no finding of the same rule at an edited span (R9.9).

## 7. Rules (algorithms; requirements in R4–R7 and R15)

### 7.1 A01
Segment (§2.3); count words (§2.4); report when `count > max_words`; severity `notice` for 21–30, `warning` ≥ 31 (for the default limit; with another `max_words` L, `notice` for L+1…L+10 and `warning` above). Suggestion per §5.2; capitalizing rule §5.3(5).

### 7.2 A03
Find instruction-bearing sentences (§4.3) containing `should`, `ought`, `may`, `might`, `could`. One finding per modal occurrence, span = the sentence. `suggestion = null`. `clarification.question` templates (exact text, with `{modal}` replaced):
- should/ought: `Is this action required, recommended, or optional? Write the word that matches your document standard. SpecForge will not choose it for you.`
- may: `Is this action permitted (the reader may choose) or only possible? State which. SpecForge will not choose it for you.`
- might/could: `Does this always happen, or only sometimes? State which. SpecForge will not choose it for you.`
`why` states the ambiguity in one sentence. `severity = notice`, `warning` in safety statements. `confidence = high` for detection (the scope test is a heuristic, so `limitation` says "The check cannot tell instructions from descriptions in every case").

### 7.3 A02
Find `must|shall|should` + optional `not` + `be` + optional `-ly` word + participle of A.3 (§4.3(c)). Suggestions only under §5.2. Base verb comes from the A.3 map; the subject phrase is kept verbatim; only its first letter is lower-cased and only when its first token is an ordinary word (not protected). Affirmative: `<Base> <subject> <rest>`; negative (`must not`/`shall not`): `Do not <base> <subject> <rest>`.

### 7.4 A04 (PROPOSED, OWNER_REVIEW_REQUIRED)
See §8 for the formal algorithm and the computed results.

### 7.5 A05, A06 (OPTIONAL, disabled)
A05: runs of tokens that are not function words (A.8b), not instruction verbs, not protected, with each `project_terms` phrase as one token; report ≥ 4 (`notice`), ≥ 6 (`warning`); never suggest. A06: pronouns of R15.3; `candidates` informational; never suggest; `confidence = low`.

## 8. A04 formal algorithm and verification (PROPOSED)

### 8.1 Definition of the unresolved point
The earlier proposal said "clauses joined by coordinators" but did not define splitting, the shared-object case, or when a clause counts. This section defines them. The owner has not approved A04 (condition of the freeze).

### 8.2 Steps
1. `body` = the sentence without trailing `. ! ?` and spaces.
2. Split `body` on the coordinator expression (longest alternative first, case-insensitive): `; then` | `, and then` | `and then` | `, then` | `;` | `, and` | `and` | `then`.
3. A segment of exactly one word that is an instruction verb and is **not the last segment** is merged into the next segment (shared object).
4. A segment is an **instruction clause** per §4.2. `count` = number of instruction clauses.
5. If `count ≥ 2`: report. If not every merged segment is an instruction clause, the suggestion abstains with `"mixed content: not every part is an instruction"`.

### 8.3 Abstain causes (checked in this order)
a subordinator of A.6 in any segment → `"subordinator: scope would change"`; a negation word of A.7a in any segment except the last → `"negation: scope would change"`; a segment inside brackets or quotes → `"bracketed or quoted text"`; math-bearing or safety → their reasons; a new sentence would start with a pronoun/conjunction → that reason.

### 8.4 Computed results (2026-11-07, throwaway script implementing §8.2–§8.3; the script is not kept)
| Sentence | count | abstain |
|---|---|---|
| `Turn off the power supply and remove the cover panel and disconnect the ground wire.` | 3 | none |
| `Close the isolation valve; wait until the gauge shows 0 bar; then open the drain valve and collect the fluid.` | **4** | subordinator (`until`) |
| `Press and hold the button.` | 1 | – |
| `Remove and discard the seal.` | 1 | – |
| `Check the oil level and the coolant level.` | 1 | – |
| `The pump and the motor are on the same skid and must be aligned.` | 0 | – |
| `Do not open the valve and drain the tank.` | 2 | negation |
| `Remove the cover and clean the seat before you install the seal.` | 2 | subordinator (`before`) |
| `Check the gauge, then close the valve.` | 2 | none |
| `Open the valve and wait.` | 2 | none |
| `Check valve V-3 and replace the seal.` | 1 | – (missed: `Check` is ambiguous and `valve` is not a determiner; precision is preferred to recall) |
| `Set the switch to ON and press the button.` | 2 | none |
| `Check the oil level, and replace the filter.` | 2 | none |
This closes the earlier `[expected]` flag for the semicolon example: it counts **4** under the formal algorithm. The definition itself still needs owner approval.

## 9. ReviewCard integration

Current `ReviewCard` (inspected via Codebase Memory, `app/report.py:40–49`): `kind, title, original: list[Evidence], issue, explanation, suggestion, decision, difference, summary`. `Evidence(label, document, location, excerpt)`. Consumers: `build_view`, `_conflict_card`, `_equivalent_card`, `ui._run_audit`.
Additive fields (defaults make existing callers unchanged): `rule_id=None, finding_id=None, confidence=None, limitation=None, abstain_reason=None, clarification=None, safety_context=False, badges=()`.
Mapping from a Finding: `kind="writing"`, `title=f"{rule_id} {rule_name}"`, `original=[Evidence("Original", source_name, f"line {line_start}", original)]`, `issue` = category label, `explanation`, `suggestion=suggestion.text if any`, `decision=state`. Builder function `writing_cards(result)` lives in `app/report.py`; it does not import `ste_checker` (it takes the result dictionary).

## 10. Correctness and safety invariants
I-1 The input string is never modified. I-2 `review_technical_text` returns no revised text. I-3 Every suggestion passed the guard. I-4 No suggestion exists for safety statements or math-bearing sentences. I-5 Every suggestion has `needs_human_review = true`. I-6 No suggestion alters `must/shall/should/may/can` (A02 relaxation §5.3 excepted and flagged). I-7 Protected token sequences are equal before and after. I-8 Text outside accepted spans is byte-identical. I-9 Same input and options give the same output. I-10 The module is stateless and offline. I-11 Doubt means abstain. I-12 `text[start:end] == original` for every finding. I-13 The audit never calls the module; the module never reads audit results. I-14 Every writing surface shows `L1` and `L2` and no forbidden-claim word.

## 11. Precision evaluation and reporting (D-07)

### 11.1 Data
JSON Lines, one record per text: `{"id", "text", "labeller", "labelled_at", "findings": [{"rule_id", "start", "end", "valid": true|false, "suggestion_ok": true|false|null, "violation": true|false}], "clean_sentences": [[start,end], ...]}`. `labeller` must differ from the implementer of the rule (R14.2). Development corpora written by the implementer are stored separately (`dev_*.jsonl`) and cannot be passed to the gate command.

### 11.2 Metrics (per enabled rule)
A reported finding is a **true positive** if the labeller recorded a finding of the same rule whose span overlaps the reported span by at least 50 % of the shorter span and marked it `valid`; otherwise **false positive**. `n = TP + FP`. `precision = TP / n`. Wilson 95 % interval: with `p = TP/n`, `z = 1.96`, `center = (p + z²/2n) / (1 + z²/n)`, `half = z·sqrt(p(1−p)/n + z²/4n²) / (1 + z²/n)`. Suggestion acceptability (`suggestion_ok`) and `violation` counts are reported separately. Recall is `TP / labelled valid findings`, reported, not gated. Findings on `clean_sentences` are counted as false positives in a separate "clean text" line.

### 11.3 Status
`PASS` n ≥ 30 and precision ≥ 0.90; `FAIL` n ≥ 30 and precision < 0.90; `INSUFFICIENT_SAMPLE` n < 30. Any `violation = true` blocks release regardless of precision (R14.6). The report (JSON and Markdown) lists every rule with TP, FP, n, precision, interval, status, and the labeller id and data hash.

## 12. Error handling
`TypeError` for non-`str` text; `ValueError` for invalid options, or a result that does not belong to the text in `apply_decisions`; no exception for any `str`. Unexpected internal errors inside one rule are caught per rule, the rule is skipped, and `limitations` receives `"rule <id> failed and was skipped"`; the result remains valid (R1.5).

## 13. Testing strategy
The planned acceptance matrix of 108 rows in the private rule-refinement notes (§5) is **planned evidence only**. No row has been implemented or run. `tasks.md` maps requirements to those rows, lists the rows that the owner decisions amend, and adds new rows. Tests are written and shown failing before any module code (gate G1). Hypothesis properties cover: protected sequence equality for every suggestion, offsets, determinism, no-raise on arbitrary Unicode, idempotence of `apply_decisions`. A mutation run (≥ 12 mutants) must kill every mutant.

## 14. Gate F — List-Free Foundation contract (AMENDMENT 2026-11-09)

> Added before any Gate F test or code, under the owner's narrow authorization of 2026-11-09 (see `tasks.md` "Gate F amendment record"). Original file SHA-256 before this amendment: `44a1fada388602f87ed68a420d85ba054c533ddb9b5060e1cfd0beb48c1dd5dd`. Sections 1–13 and the appendices are preserved; where this section is narrower it applies **to Gate F only**. Requirement 17 states the acceptance criteria.

### 14.1 Scope and non-scope
Gate F consults **no** list of Appendix A. It implements protected-span detection, offsets, abstention, WARNING/CAUTION region marking and the `review_technical_text` result shell. It deliberately does **not** implement: sentence segmentation (needs A.4), PT2 units (needs A.8a), PT4 negations (A.7a), PT5 limit phrases (A.7b), obligation classes, instruction clauses (A.1/A.2/A.5), subordinators (A.6), any rule A01–A06, the guard over suggestions, suggestions, `apply_decisions` behavior, UI, export or evaluation tooling. The only literal words the module may contain are the safety labels `WARNING`, `CAUTION` (recognized) and `DANGER` (abstention trigger only, U-04).

### 14.2 Offset convention
All offsets are Python `str` indices into the unmodified input (code points, not UTF-8 bytes, not grapheme clusters). `end` is exclusive. Invariant for every span, abstention and region: `text[start:end] == excerpt`. `line_start`/`line_end` are 1-based editor lines using the same line-break set as `split_lines` (`\n`, `\r\n`, `\r`); `line_end` is the line of `end - 1`. The module does not import `split_lines`; a test asserts equal line counts.

### 14.3 Paragraphs and tokens
A paragraph is a maximal run of lines that are not blank (blank = only whitespace). A token is a maximal run of non-whitespace characters (`\S+`). The **core** of a token removes leading `( [ { " “ ' ` and `` ` `` and trailing `. , ; : ! ? ) ] } " ” '` and `` ` ``; spans refer to the core offsets.

### 14.4 Classes and shapes
Fixed priority (a lower number claims text first): `PT7_QUOTE` (1), `PT3_TERM` (2), `PT6_MATH` (3), `PT1_NUMBER` / `PT1_IDENT` (4). `PT6_MATH` is a Gate F class name (the original numbering left 6 unassigned); PT2, PT4, PT5 are not produced by Gate F.

- **PT1_NUMBER**: the core of a token fully matches `SIGN? DIGITS ((.|,) DIGITS)* EXP?`, a leading-separator form `SIGN? (.|,) DIGITS EXP?`, a range `NUMBER (-|–) UNSIGNED_NUMBER`, or `NUMBER × 10 ^ SIGNED_INT`; `SIGN` ∈ `+ - −`; `EXP` = `[eE] SIGN? DIGITS`. Interpretation: literal characters only. When a token starts with a number and the remainder begins with a letter or one of `° % µ μ Ω` (glued unit, e.g. `20°C`, `10kPa`), only the numeric prefix is a PT1_NUMBER and a `unit_boundary_deferred` abstention covers the remainder.
- **PT1_IDENT** (syntactic; any token not matching these shapes is never an identifier; over-protection is intended, under-protection is not): (I1) `LETTERS (-|_)? DIGIT ALNUM* ((-|_|.|/|:) ALNUM+)*` (`P-101`, `V-2.01`, `ISO-9001`, `M8-1.25`, `H2O`, `ISO-9001:2015`); (I2) `DIGITS ALNUM* (-|_|/) ALNUM+ ((-|_|.|/|:) ALNUM+)*` (`6205-2RS`, `5/8`). Number shapes are tried first.
- **PT7_QUOTE**: `"…"` pairs (sequential, per paragraph), `“…”`, and Markdown backtick runs matched by equal run length. Delimiters are part of the span. A `"` directly after a digit is treated as an inch-mark candidate: not a delimiter, abstention `quote_char_ambiguous`. Unmatched openers/closers or a nested `“` before `”`: abstention `unclosed_quote`.
- **PT3_TERM**: each entry of `ReviewOptions.project_terms` (exact case, whole word: not preceded or followed by a word character; longest first; duplicates ignored). Entries must be non-empty `str` equal to their stripped form, else `ValueError`. There is no default term list.
- **PT6_MATH**: tokens are classified `ANCHOR` (core made only of `= < > ≤ ≥ ≈ ≠ ± × ÷ ^`, or `<= >= == !=`; or a token with an alphanumeric on both sides of a relational operator, such as `x=5`), `OPER` (`+ - − – * /` standing alone; `× ÷ ^` are anchors), `OPND` (a full number shape, an identifier shape, or a single letter `[A-Za-z]` / Greek letter, optionally with `_ALNUM+` subscript) or `OTHER`. A run of consecutive tokens on one line that are all `ANCHOR`/`OPER`/`OPND`, containing at least one `ANCHOR`, is a math expression; a token whose core lost trailing `. , ; : ! ?` ends the run. Unbalanced outer parentheses are trimmed from the span. If the run begins or ends with an anchor or operator token (a missing operand, e.g. `Pressure > 10`), record `math_boundary_ambiguous` and still protect the run.
- **Overlap**: see Requirement 17.4. Accepted spans are non-overlapping; absorbed candidates are counted in `summary.absorbed_nested`.

### 14.5 Abstentions
`{id, kind, reason, start, end, line_start, line_end, excerpt}`; kinds (closed set, no word list): `unit_boundary_deferred`, `quote_char_ambiguous`, `unclosed_quote`, `math_boundary_ambiguous`, `overlap_conflict`, `safety_marker_undecided`, `unclassified_numeric_token` (a digit-bearing token that matches no number/identifier shape, for example `10:30` or `5+3`, and is not claimed by any span).

> *Spec delta 2026-11-09 (recorded before GREEN, after RED):* `unclassified_numeric_token` added and the `× ÷ ^` operator/anchor inconsistency fixed while writing the implementation; corresponding test `test_f17_10_unclassified_digit_tokens_abstain` added. No other change. Abstention means: the module did not decide; the text is untouched and no claim is made.

### 14.6 Safety regions
Per Requirement 17.11. Output item: `{label, start, end, line_start, line_end, excerpt, rewrite_allowed: false}`. The region is metadata over original text; spans inside it are still reported. Gate F has no suggestions at all.

### 14.7 Result shell (extends §1.3; fields not listed there are Gate F-only)
```
{ "schema_version": 1, "stage": "GATE_F_LIST_FREE_FOUNDATION",
  "labels": [L1, L2], "input": {"chars","lines","sha256"},
  "options": {...echo...}, "offset_convention": "python_str_index_code_points_end_exclusive",
  "protected_spans": [{"id","cls","start","end","line_start","line_end","text","provenance":{"detector","basis"}}],
  "abstentions": [...], "safety_regions": [...],
  "findings": [], "rules_executed": [], "rules_not_executed": [{"rule_id","reason":"GATE_W_BLOCKED"}],
  "lexical_lists_consulted": [], "capabilities": [{"id","status","note"}],
  "summary": {"findings":0,"with_suggestion":0,"protected_spans":int,"abstentions":int,"safety_regions":int,"absorbed_nested":int},
  "limitations": [str] }
```
Also public: `protected_sequence(text, options=None) -> list[tuple[str, str]]` (§3.1) and `apply_decisions(text, result, decisions)`, which fails closed (Requirement 17.13).

### 14.8 Known conflicts documented and deferred (do not affect Gate F)
1. **§5.3 A02 note vs Appendix D U-02.** §5.3 says check 6 is relaxed for A02 and cites `a02_rewrite=False` as the off switch; Appendix D U-02's "default if silent" says "A02 rewrite on, flagged". This is not owner approval of rewrites. Gate F preserves `a02_rewrite=False` (True raises) and implements no obligation-changing rewrite. Resolution remains an owner decision for Gate W.
2. **R8.1/§4.4 include `DANGER`; U-04 undecided.** Gate F recognizes WARNING/CAUTION only and abstains on DANGER.
3. **R8.1 "begins" vs §4.4 "first three tokens include".** Gate F follows R8.1 ("begins"); §4.4 is not exercised.
4. **§1.1 default `enabled_rules=("A01","A03")`.** Gate F default is `()` so a default call cannot imply that a rule ran; Gate W restores the original default only after its gates.

## Appendix A. Closed lists — `OWNER_REVIEW_REQUIRED` (D-06)

**Provenance of the lists.** Drafted on 2026-11-07 from general English maintenance vocabulary, without consulting ASD-STE100 or any dictionary, and entered once. They contain only common English words. An AI model cannot prove that a common-word list shares nothing with any published list; the owner is asked to review and to confirm or change each entry. No entry carries a definition, example or wording from a standard.

**A.1 Instruction verbs, unambiguous (53):** remove, disconnect, connect, close, open, wait, replace, clean, wipe, inspect, drain, fill, collect, install, tighten, loosen, discard, adjust, lubricate, apply, secure, unlock, lift, lower, insert, extract, attach, detach, measure, compare, verify, rotate, align, unscrew, screw, bleed, switch, turn, push, pull, ensure, keep, torque, disassemble, assemble, dismantle, position, wash, rinse, dry, cover, protect, wear.

**A.2 Instruction verbs, ambiguous noun/verb (27):** check, test, set, fit, record, mark, label, read, hold, press, stop, start, seal, lock, release, store, flush, mount, repeat, report, use, mix, cut, drill, spray, clamp, plug. Count as instruction clauses only when the next word is in A.5.

**A.3 Action participles (64).** Regular, `rewrite_ok`, with base verb (51): inspected=inspect, replaced=replace, removed=remove, installed=install, tightened=tighten, loosened=loosen, torqued=torque, lubricated=lubricate, cleaned=clean, disconnected=disconnect, connected=connect, drained=drain, filled=fill, checked=check, adjusted=adjust, tested=test, measured=measure, recorded=record, secured=secure, sealed=seal, lifted=lift, lowered=lower, attached=attach, detached=detach, marked=mark, labelled=label, labeled=label, stored=store, discarded=discard, flushed=flush, bled=bleed, aligned=align, mounted=mount, rotated=rotate, repeated=repeat, verified=verify, washed=wash, rinsed=rinse, dried=dry, covered=cover, protected=protect, assembled=assemble, disassembled=disassemble, inserted=insert, extracted=extract, applied=apply, collected=collect, wiped=wipe, unlocked=unlock, locked=lock, released=release.
Irregular, **detect only** (13, abstain `"verb form needs manual rewrite"`): shown, done, made, kept, held, set, driven, given, taken, put, cut, left, written.
State words, never flagged: closed, open, required, specified, worn, damaged, full, empty, fitted.

**A.4 Abbreviations (16):** approx, max, min, no, fig, ref, para, sec, vs, dia, temp, nom, e.g, i.e, etc, incl.

**A.5 Words that may follow an ambiguous verb (determiners and pronouns):** the, a, an, each, all, any, this, these, those, it, them, both, off, on, out, up, down, away, until, back.

**A.6 Subordinators (10):** before, after, until, while, when, if, unless, once, because, whenever.

**A.7a Negations:** not, no, never, cannot, without, unless, except, neither, nor, none, nothing, and any token ending in n't.
**A.7b Limit and quantifier phrases:** at least, at most, no more than, no less than, not more than, not less than, more than, less than, up to, minimum, maximum, min, max, above, below, exceed, exceeds, within, only, all, any, each, every, both.

**A.8a Function words (units and A05):** to, and, or, of, the, at, in, on, as, by, for, is, are, be, if, then, than, from, with, per, a, an.
**A.8b Function words for A05:** A.8a plus: it, its, this, that, these, those, was, were, been, being, must, shall, should, may, might, can, could, will, not, no, into, onto, over, under, between, when, while, before, after, until, but.

**A.9 Others:** (a) coordinators as in §8.2; (b) safety markers: WARNING, CAUTION, DANGER; (c) dummy-subject adjectives (A06): necessary, possible, permitted, allowed, important, required, recommended, safe, likely; (d) person words: you, operator, technician, mechanic, engineer, user, personnel.

## Appendix B. Items resolved from the private rule-refinement notes while freezing (2026-11-07)
| # | Defect found | Resolution |
|---|---|---|
| 1 | A04 semicolon example was `[expected]` | computed 4 (§8.4), still PROPOSED |
| 2 | `Wait 5 min. 10 min later` not split | abbreviation rule refined (§2.3, item 3) |
| 3 | A02 scope required a person subject, contradicting its own examples | scope is modal + `be` + action participle (R7.1) |
| 4 | A03 offered `should→must` options | removed: clarification only (D-03, R5) |
| 5 | A06 default-on and offered a replacement | disabled by default, informational candidates (D-05, R15) |
| 6 | `approved_terms` contains a forbidden word | renamed `project_terms` |
| 7 | development corpora counted toward the precision gate | only independently labelled data counts (D-07, R14) |
| 8 | "V-201." boundary contradicted "digit-bearing token" rule | boundary rule rewritten (§2.3) |
| 9 | the guard could not express "never change must/shall/should/may/can" literally | check 6 added; A02 exception flagged (U-02) |
| 10 | the unit rule would protect `5 min.`-like function words | F-LIST exclusion (§3.3 item 4) |

## Appendix C. P1.1 dependency status (checked 2026-11-07 01:15–01:16, against the current code)
| Topic | Evidence | Status |
|---|---|---|
| A/B labels from position (D1) | probe: `Document A and Document A disagree` still produced; cross-page duplicate not flagged | NOT DONE |
| Stale results (D2) | no `outdated`/clear logic found in `app/ui.py` | NOT DONE |
| Untrusted file names (D6) | no `html.escape` in `app/ui.py` or `app/report.py` | NOT DONE |
| Ambiguous units (D4) | `1 ton` vs `907.18474 kg` accepted silently | NOT DONE |
| Interval vs absolute temperature (D3) | `5 delta_degC` vs `5 K` accepted as equivalent | NOT DONE |
| False unparsed rows (D5) | `value (2026 rev)` header flagged | NOT DONE |
No source or test file changed after the red-team report. No P1.1 report exists in the private folder. A separate remediation session may exist elsewhere; none was found in this workspace. **P2 implementation stays blocked** (R16.1).

## Appendix D. Open items for the owner
| ID | Item | Default if silent |
|---|---|---|
| U-01 | Approve or change A04 (definition, §8, lists A.1/A.2) | A04 stays unimplemented (gate G-A04) |
| U-02 | Does `must be X-ed` → imperative count as changing `must`? | A02 rewrite on, flagged; switch `a02_rewrite` |
| U-03 | Review all closed lists A.1–A.9 | lists unusable until reviewed |
| U-04 | Keep `DANGER` as a safety marker | kept |
| U-05 | Name the independent labeller and supply or approve the evaluation set (≥ 30 findings per rule) | gate status INSUFFICIENT_SAMPLE; rules ship off by default |
| U-06 | Treatment of `shall` in specification text | as `must` |
| U-07 | Manual-edit decision (author types a replacement in the tool) | not in the initial release |
| U-08 | Minimum sample 30 and the Wilson interval in the report | as written |
| U-09 | Rename `approved_terms` → `project_terms` | renamed |
| U-10 | Start A05/A06 only after tasks 1–12 | as written |
| U-11 | Treat P1.1 as blocking | blocking |

> **AMENDMENT 2026-11-09 — U-11 scoped for Gate F only.** For Gate F only, the owner permitted proceeding on the documented P1.3 baseline (`SPECFORGE_P1_3_ACCEPTED_WITH_LIMITATIONS`, author-associated). U-11 stays **blocking for Gate W**. Every other row U-01…U-10 is **unanswered**; none of the "default if silent" cells is treated as owner approval. Gate F touches only: U-03 (no list used → not approved), U-04 (DANGER not recognized), U-09 (the option name `project_terms` is used because the forbidden-claim rule excludes the alternative; U-09 itself is not claimed as confirmed), U-02 (`a02_rewrite=False` preserved).
