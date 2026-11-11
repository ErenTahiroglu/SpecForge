# Requirements Document

> **Provenance.** Created 2026-11-07 (UTC+3); exact file timestamps are recorded in the private freeze record. Written **after** P1 implementation and **before** any P2 test or code. It does not govern P1, which was built without this spec. No P2 tests or module exist when this file is created.
> **Status:** FROZEN FOR OWNER REVIEW. Sources: the private rule-refinement notes (not published) plus owner decisions D-01…D-12 (conditional approval, 2026-11-07).
> **Open items** are marked `OWNER_REVIEW_REQUIRED` or `PROPOSED`. A requirement marked `PROPOSED` is not implemented until the owner approves it.
> **AMENDMENT 2026-11-09 (Gate F / Gate W split).** Requirement 17 (new) and a scoped note under 16.1 permit the list-free **Gate F Foundation** only. All other requirements stay **Gate W (blocked)**. Original file SHA-256 (before amendment): `1c5aa74b9a865e35638192172da0889a0b68df7990e2155b60f865ba49617af0`.

## Introduction

SpecForge compares measurements between engineering documents (the numeric audit). P2 adds a separate **writing review**: it finds wording problems in technical English, explains them, and lets a human accept or reject each suggestion. It is **STE-inspired**: independently written checks, not an implementation of ASD-STE100.

The writing review never decides whether a number is right and never changes engineering content on its own. Its main safety property is that **no suggestion can alter a number, a unit, an identifier, a negation, a limit, or an obligation** (`must`, `shall`, `should`, `may`, `can`).

Required labels on every writing-review surface (screen, export, JSON):
- `L1` = `STE-inspired writing suggestions`
- `L2` = `ASD-STE100 conformance not verified`

Forbidden as claims anywhere in module output or UI text (regular expression, case-insensitive): `\b(compliant|conforms?|certified|approved|endorsed)\b`. Option names therefore avoid these words (`project_terms`, not `approved_terms`).

### Glossary
- **Review unit**: a paragraph or list item that is reviewed (see 2.x).
- **Sentence**: text between sentence boundaries defined in design §3.
- **Finding**: one reported writing issue with an exact source span.
- **Suggestion**: a proposed replacement text for a finding's span.
- **Abstention**: a finding reported without a suggestion, with a stated reason.
- **Clarification request**: a finding that asks the author to resolve an ambiguity; contains no replacement text.
- **Protected token**: text that must survive any suggestion exactly (design §4).
- **Obligation class**: one of `PROHIBIT, REQUIRE, RECOMMEND, PERMIT, POSSIBLE, NONE` (design §5).
- **Safety statement**: a paragraph that begins with `WARNING`, `CAUTION` or `DANGER` (design §6).
- **Decision**: a human choice on a finding: `pending, accepted, rejected, clarified`.
- **Original text**: the exact input string. It is never modified.

### Owner decisions applied
| ID | Decision | Where |
|---|---|---|
| D-01 | sentence-length limit = 20 words | R4 |
| D-02 | `can` and `cannot` are not flagged | R5 |
| D-03 | no obligation-changing replacement text; flag ambiguity and require author clarification | R5, R3, R9 |
| D-04 | A05 optional and disabled by default | R15 |
| D-05 | A06 optional and disabled by default; one candidate is not proof of an antecedent | R15 |
| D-06 | closed lists drafted independently, marked `OWNER_REVIEW_REQUIRED` | design Appendix A |
| D-07 | precision ≥ 90 % on independently labelled data, per-rule scores and denominators, insufficient samples reported | R14 |
| D-08 | no automatic rewrite suggestions for WARNING/CAUTION material | R8 |
| D-09 | explicit `apply_decisions`, revised-text preview and export, immutable original, human acceptance | R9 |
| D-10 | English only | R13 |
| D-11 | core module `app/ste_checker.py` | design §1 |
| D-12 | priority A07, A01, A03, A04, A02; A05/A06 optional and disabled in the initial release | R15, tasks |

## Requirements

### Requirement 1: Deterministic, offline, non-destructive review
**User Story:** As a technical author, I want the same text to always give the same findings and never be changed behind my back, so that I can trust and repeat a review.

#### Acceptance Criteria
1.1 WHEN `review_technical_text` is called twice with equal `text` and equal `options` THEN THE SYSTEM SHALL return equal results (deep equality of the returned dictionaries).
1.2 THE SYSTEM SHALL perform no network access and no LLM call during a review. Verification: the module imports only the Python standard library and, read-only, `split_lines`, `ENTRY` and `CSV_ENTRY` from `app/core.py`.
1.3 WHEN `review_technical_text` returns THEN THE SYSTEM SHALL NOT have modified its `text` argument, and the result SHALL contain `input.sha256` equal to the SHA-256 of the UTF-8 bytes of `text`.
1.4 THE SYSTEM SHALL NOT include revised text in the result of `review_technical_text`.
1.5 WHEN `text` is empty, whitespace only, or arbitrary Unicode of any length up to 200,000 characters THEN THE SYSTEM SHALL return a result without raising an exception.
1.6 WHEN `text` has 5,000 characters THEN THE SYSTEM SHALL return within 1 second on the development machine.
1.7 THE SYSTEM SHALL order findings by `(span.start, rule_id)` and assign ids `W-0001`, `W-0002`, … in that order.

### Requirement 2: Review units and safe sentence segmentation
**User Story:** As a technical author, I want sentences found correctly around numbers, abbreviations, identifiers and formulas, so that findings point at the right text and numbers are never cut.

#### Acceptance Criteria
2.1 THE SYSTEM SHALL split text into lines with `split_lines` (`\n`, `\r\n`, `\r` only) and report `line_start` and `line_end` as 1-based editor lines.
2.2 WHEN a line is blank, a Markdown heading (`#`), inside a fenced code block, a table row (two or more `|`), accepted by `ENTRY` or `CSV_ENTRY`, or consists only of one identifier or number THEN THE SYSTEM SHALL NOT review it and SHALL list it in `skipped` with its kind.
2.3 WHEN lines of one paragraph are hard-wrapped THEN THE SYSTEM SHALL treat the line breaks as spaces.
2.4 WHEN a list item starts with `-`, `*`, `n.` or `n)` THEN THE SYSTEM SHALL review it as its own unit.
2.5 WHEN a `.` lies between two digits, or has no whitespace after it, THEN THE SYSTEM SHALL NOT treat it as a sentence boundary.
2.6 WHEN a `.` follows an abbreviation of the closed list (design Appendix A.4) and the token before the abbreviation contains no digit THEN THE SYSTEM SHALL NOT treat it as a boundary. WHEN that token contains a digit (`5 min.`) THEN the period SHALL be a boundary if the next text starts a sentence.
2.7 WHEN `.`, `!` or `?` is followed by whitespace and then an uppercase letter, a digit, a quote or an opening bracket (or the end of the unit) THEN THE SYSTEM SHALL treat it as a boundary, including after an identifier (`V-201.`).
2.8 For every finding THE SYSTEM SHALL satisfy `text[span.start:span.end] == finding.original`.
2.9 THE SYSTEM SHALL pass these fixed segmentation cases (sentence counts): `Set the relief valve to 0.5 bar. Check that the gauge reads 10.5 bar after 2.5 min.`=2; `Torque the bolt to 25 N·m (approx. 18 lbf·ft). Check it again.`=2; `Do not exceed max. 10 bar. Use gauge G-2.`=2; `Check valve V-201. Close it.`=2; `Wait 5 min. 10 min later, check again.`=2; `Use the value in Fig. 2 and ref. 4 as given.`=1; `Pump P-101 runs at 3.5 bar.`=1; `The pressure P = F / A. Check the gauge.`=2.

### Requirement 3: Protected tokens and the preservation guard (A07)
**User Story:** As an engineer, I want a guarantee that no writing suggestion changes any number, unit, identifier, negation, limit, or obligation, so that a suggestion can never change what a document requires.

#### Acceptance Criteria
3.1 THE SYSTEM SHALL always run the preservation guard. No option SHALL disable it, and removing `A07` from `enabled_rules` SHALL have no effect.
3.2 WHEN a suggestion (or an `apply_decisions` result) is produced THEN THE SYSTEM SHALL verify that the ordered sequence of protected tokens of classes PT-1, PT-2, PT-3, PT-4, PT-5 and PT-7 in the original span is character-for-character equal to that of the replacement.
3.3 WHEN the sequence differs in any token, any character, or the order THEN THE SYSTEM SHALL suppress the suggestion, set `guard.status = "suppressed"` with the failed class, and keep the finding with `suggestion = null`.
3.4 THE SYSTEM SHALL verify that the obligation class of every sentence touched by a suggestion is unchanged, and that `apply_decisions` leaves all text outside accepted spans byte-identical.
3.5 THE SYSTEM SHALL verify that no sentence created by a suggestion starts with `and, but, or, which, that, it, they, them`, and that capitalization changes affect only the first letter of a sentence whose first token is not protected.
3.6 THE SYSTEM SHALL protect these tokens exactly: `1,000`, `10,5`, `1.0E5`, `−5`, `-20`, `+3.2`, `0.5-1.0`, `0.5–1.0`, `2.5×10^-3`, `P-101`, `V-201`, `M8-1.25`, `6205-2RS`, `ISO-9001`, units directly after numbers (`bar`, `kPa`, `N·m`, `L/min`, `°C`, `degC`, `m^3/s`), negations, limit phrases, `project_terms` phrases, and text inside quotes or backticks (design §4).
3.7 WHEN a sentence is math-bearing (design §4.4) THEN THE SYSTEM SHALL produce no suggestion for it. It MAY still report findings.
3.8 THE SYSTEM SHALL NOT convert, rewrite or normalize any unit or number. WHEN a text contains `1000 kPa (10 bar)` THEN no suggestion SHALL alter either quantity.
3.9 THE SYSTEM SHALL report the count of guard suppressions in `summary.suppressed_by_guard`.

### Requirement 4: Sentence length (A01)
**User Story:** As a technical author, I want to know which sentences are too long, so that I can shorten them.

#### Acceptance Criteria
4.1 THE SYSTEM SHALL count a word as a whitespace-delimited token that contains at least one letter or digit.
4.2 WHEN a sentence has more than `max_words` words (default 20) THEN THE SYSTEM SHALL report an A01 finding. A sentence with exactly 20 words SHALL NOT be reported.
4.3 THE SYSTEM SHALL assign severity `notice` for 21–30 words and `warning` for 31 or more.
4.4 THE SYSTEM SHALL give these counts for the fixed cases (verified by computation on 2026-11-07): `Before you disconnect the hydraulic line from the actuator housing, make sure that the isolation valve is closed and the system pressure is below 0.5 bar.` = 26; `Close the isolation valve; wait until the gauge shows 0 bar; then open the drain valve and collect the fluid in the container.` = 23; `Before you disconnect the hydraulic line, make sure that the isolation valve is closed and the pressure is very low.` = 20; the same sentence ending `… very low again.` = 21; `Set the relief valve to 0.5 bar.` = 7; `Check that the gauge reads 10.5 bar after 2.5 min.` = 10.
4.5 WHEN a long sentence contains a `;` that is outside brackets and quotes, both parts have at least 4 words, the left part does not start with `If, When, Before, After, Until, Unless, While, Once`, and the guard passes THEN THE SYSTEM SHALL offer a suggestion that replaces each such `;` with `.` and capitalizes the next word under 3.5.
4.6 OTHERWISE THE SYSTEM SHALL abstain with `abstain_reason = "no safe split; shorten manually"`. THE SYSTEM SHALL NOT split at `,` or `and` in A01.
4.7 WHEN `max_words` is set THEN the threshold SHALL follow it (a 26-word sentence is reported at 25 and not at 26 or higher).

### Requirement 5: Ambiguous modals (A03) — flag and ask, never replace
**User Story:** As an author of procedures, I want to be told where `should`, `may`, `might` or `could` leave an action's status unclear, so that I decide myself whether it is required, recommended or optional.

#### Acceptance Criteria
5.1 THE SYSTEM SHALL report an A03 finding for `should`, `ought to`, `may`, `might` or `could` in an instruction-bearing sentence (design §5.3).
5.2 THE SYSTEM SHALL NOT report `can` or `cannot` (D-02).
5.3 THE SYSTEM SHALL NOT report descriptive sentences: `Pressure may exceed 10 bar during start-up.`, `The relief valve can open at 10 bar.`, `This might damage the seal.`
5.4 THE SYSTEM SHALL NOT produce replacement text, options, or any alternative wording for an A03 finding. `suggestion` and `options` SHALL be `null`.
5.5 THE SYSTEM SHALL provide a `clarification` object `{question, why}` for every A03 finding. The question for `should` SHALL ask whether the action is required, recommended or optional; for `may` whether it is permitted or only possible; for `might`/`could` whether it always or only sometimes happens.
5.6 THE SYSTEM SHALL NEVER change `must`, `shall`, `should`, `may` or `can` in any suggestion of any rule (guard 3.4).
5.7 WHEN a finding lies in a safety statement THEN THE SYSTEM SHALL report it with `safety_context = true` and severity `warning`.
5.8 The allowed human decisions for an A03 finding are `clarified` and `rejected`. `accepted` SHALL be refused because there is nothing to accept.

### Requirement 6: Multiple instructions in one sentence (A04) — PROPOSED, OWNER_REVIEW_REQUIRED
**User Story:** As an author of procedures, I want to see sentences that combine several steps, so that each step is stated once.

> **Status:** the A04 definition is **not yet owner-approved**. It is preserved from the proposal and its example counts are now computed under the formal algorithm (design Appendix B). Implementation of A04 is blocked until the owner approves this requirement (task gate G-A04).

#### Acceptance Criteria
6.1 THE SYSTEM SHALL split a sentence into segments at the coordinators `; then`, `, and then`, `and then`, `, then`, `then`, `;`, `, and`, `and` (longest match first) and count a segment as an **instruction clause** when it starts with a verb of the unambiguous list (Appendix A.1), or with a verb of the ambiguous list followed by a determiner or pronoun (Appendix A.2), optionally after `Do not`, `Never` or `Always`.
6.2 WHEN a segment consists of one instruction verb and is not the last segment THEN THE SYSTEM SHALL merge it into the next segment (shared object). `Press and hold the button.` and `Remove and discard the seal.` count 1.
6.3 WHEN the count is 2 or 3 THEN severity SHALL be `notice`; WHEN 4 or more THEN `warning`.
6.4 THE SYSTEM SHALL give these counts (computed 2026-11-07): `Turn off the power supply and remove the cover panel and disconnect the ground wire.` = 3; `Close the isolation valve; wait until the gauge shows 0 bar; then open the drain valve and collect the fluid.` = **4** (suggestion abstains, see 6.5); `Press and hold the button.` = 1; `Remove and discard the seal.` = 1; `Check the oil level and the coolant level.` = 1; `The pump and the motor are on the same skid and must be aligned.` = 0; `Do not open the valve and drain the tank.` = 2; `Remove the cover and clean the seat before you install the seal.` = 2.
6.5 THE SYSTEM SHALL abstain from a suggestion when any of: a subordinator (`before, after, until, while, when, if, unless, once, because, whenever`) occurs in any segment; a negation occurs in any segment except the last; any segment is inside brackets or quotes; the sentence is math-bearing or a safety statement; a new sentence would start with a pronoun or conjunction. The abstain reason SHALL name the cause.
6.6 OTHERWISE THE SYSTEM SHALL suggest separate sentences in the original order, with the coordinators removed, each starting with a capital letter, with protected tokens unchanged (3.2). The suggestion SHALL carry the badge `removes 'and/then': confirm the order`.

### Requirement 7: Passive voice in instructions (A02)
**User Story:** As an author of procedures, I want passive requirements shown as direct instructions where this is safe, so that instructions are clearer.

> **Status:** `OWNER_REVIEW_REQUIRED` for item 7.5: whether converting `must be X-ed` to an imperative counts as changing `must`. The class (REQUIRE) is preserved and a human accepts the change. Option `a02_rewrite` (default `False`) is detect-only; the rewrite stays off unless separately approved.

#### Acceptance Criteria
7.1 THE SYSTEM SHALL report an A02 finding when a sentence contains `must`, `shall` or `should`, then `be` (optionally with one `-ly` adverb in between), then a participle of the action list (Appendix A.3). No person subject is required.
7.2 THE SYSTEM SHALL NOT report state words (`closed, open, required, specified, worn, damaged, full, empty`) or sentences without a modal (`The pump is driven by a motor.`).
7.3 THE SYSTEM SHALL offer a rewrite only when all hold: the modal is `must` or `shall`; no `by <agent>` phrase; the participle is marked `rewrite_ok`; the subject phrase before the modal has at most 6 tokens; the sentence is not math-bearing and not a safety statement; the guard passes.
7.4 THE SYSTEM SHALL produce for `The filter element must be replaced every 500 operating hours.` the suggestion `Replace the filter element every 500 operating hours.` and for `The cover must not be removed while the engine runs.` the suggestion `Do not remove the cover while the engine runs.` The obligation class SHALL stay REQUIRE and PROHIBIT respectively.
7.5 WHEN the modal is `should` THEN THE SYSTEM SHALL abstain with `abstain_reason = "modal 'should' marks a recommendation; the author must decide"`.
7.6 WHEN an agent (`by …`) is present, or the participle is not `rewrite_ok`, or the unit is a safety statement THEN THE SYSTEM SHALL abstain with the cause.
7.7 Every A02 suggestion SHALL carry the badge `restructures the sentence`.

### Requirement 8: Safety statements (WARNING, CAUTION)
**User Story:** As a safety reviewer, I want warnings and cautions never to receive automatic rewrite suggestions, so that safety wording is only changed deliberately by a person.

#### Acceptance Criteria
8.1 WHEN a paragraph begins (ignoring Markdown `*` and `_`) with `WARNING`, `CAUTION` or `DANGER`, case-insensitive, THEN THE SYSTEM SHALL treat it as a safety statement. (`DANGER` is a conservative addition to D-08: `OWNER_REVIEW_REQUIRED`.)
8.2 THE SYSTEM SHALL NOT produce a suggestion for any finding in a safety statement and SHALL set `abstain_reason = "safety statement: rewrite manually"` and `safety_context = true`.
8.3 THE SYSTEM SHALL still report the findings with severity at least `warning`.
8.4 This behavior SHALL NOT depend on an option.

### Requirement 9: Human decisions, `apply_decisions`, immutable original
**User Story:** As a technical author, I want to accept or reject each suggestion and see the revised text beside the untouched original, so that nothing changes without my decision.

#### Acceptance Criteria
9.1 THE SYSTEM SHALL provide `apply_decisions(text, result, decisions)` that returns `{original_sha256, revised_text, revised_sha256, applied, skipped, conflicts}` and SHALL NOT modify `text`.
9.2 WHEN `decisions` is empty, or all findings are `pending` or `rejected` or `clarified`, THEN `revised_text` SHALL equal `text`.
9.3 THE SYSTEM SHALL apply only findings with state `accepted` and a non-null `suggestion`, from the end of the text to the start, so earlier offsets stay valid.
9.4 WHEN two accepted edits overlap THEN THE SYSTEM SHALL apply neither and SHALL list both ids in `conflicts`.
9.5 WHEN an accepted finding has no suggestion THEN THE SYSTEM SHALL list it in `skipped` with the reason `no suggestion`.
9.6 THE SYSTEM SHALL re-run the guard (3.2–3.5) on the final revised text and, WHEN it fails, SHALL return `revised_text == text` and list the failure.
9.7 THE SYSTEM SHALL NOT offer "accept all" for findings without a suggestion or for findings of rule A03. "Accept all" SHALL apply only suggestions that exist and have passed the guard.
9.8 THE SYSTEM SHALL record decisions only as data supplied by the caller; the module SHALL keep no state between calls.
9.9 Re-reviewing `revised_text` SHALL NOT produce a new finding of the same rule at an edited span.

### Requirement 10: Separation from the engineering audit
**User Story:** As an engineer, I want writing review and numeric verification kept apart, so that a writing suggestion can never influence a verified measurement.

#### Acceptance Criteria
10.1 THE SYSTEM SHALL NOT import `ste_checker` from `app/core.py`, `app/ingest.py` or `app/report.py`'s audit functions, and `audit_documents` output SHALL be identical with and without the writing review being run.
10.2 Writing findings SHALL NOT appear in the audit's `findings`, `matches`, `unmatched`, `warnings` or coverage lists, in the audit verdict, or in its metrics.
10.3 WHEN accepted edits are applied to a document and the audit is run on original and revised text THEN `records` (tag, field, value, unit, SI value) SHALL be identical.
10.4 THE SYSTEM SHALL skip measurement rows (2.2) so the two features cannot disagree about a number.

### Requirement 11: User interface and card integration
**User Story:** As a user, I want to paste text, see findings as cards with original text, reason and suggestion, and accept or reject them, so that review is quick and transparent.

#### Acceptance Criteria
11.1 THE SYSTEM SHALL add a section "Writing review (STE-inspired)" to `app/ui.py`, separate from the audit results, showing `L1` and `L2` permanently.
11.2 THE SYSTEM SHALL extend `ReviewCard` only with new optional fields with defaults (`rule_id, finding_id, confidence, limitation, abstain_reason, clarification, safety_context, badges`) so existing callers (`build_view`, `_conflict_card`, `_equivalent_card`, `ui._run_audit`) behave unchanged.
11.3 FOR each finding THE SYSTEM SHALL show: the original text with its line, the rule and issue, the explanation, the suggestion (or the abstention reason, or the clarification question), the confidence and limitation, and the decision controls.
11.4 THE SYSTEM SHALL show Accept and Reject for findings with a suggestion, and "Mark as clarified" and Reject for A03 findings. It SHALL show no Accept control when there is no suggestion.
11.5 THE SYSTEM SHALL show the original text and the revised-text preview side by side and SHALL label the preview `Revised text (original unchanged)`.
11.6 WHEN the input text or the options change THEN decisions and results from the previous text SHALL be cleared or labeled `outdated`.
11.7 File names and user text SHALL be shown as text, not interpreted as HTML.
11.8 THE SYSTEM SHALL NOT claim in any string that text is "STE" or "correct" or "compliant". Allowed phrases are `L1` and `L2`.

### Requirement 12: Export
**User Story:** As a reviewer, I want an exportable record of every finding and decision, so that I can keep or share the review.

#### Acceptance Criteria
12.1 THE SYSTEM SHALL export Markdown with `L1` and `L2` at the top, and for each finding: id, rule, line, severity, original text, suggestion or abstention reason or clarification question, explanation, confidence and limitation, and the decision (`pending, accepted, rejected, clarified`).
12.2 THE SYSTEM SHALL export JSON containing the result, the decisions, `original_sha256` and `revised_sha256`.
12.3 THE SYSTEM SHALL include both the original text and the revised text in the export when any edit was accepted, and only the original when none was.
12.4 Exporting SHALL NOT change the text or the decisions.

### Requirement 13: Labeling, language and legal boundaries
**User Story:** As the project owner, I want no claim of ASD-STE100 conformance and no use of protected material, so that the product is honest and lawful.

#### Acceptance Criteria
13.1 EVERY writing-review string shown to a user and every export SHALL contain `L1` and `L2` at least once per surface, and SHALL NOT match the forbidden-claim expression.
13.2 THE SYSTEM SHALL contain no text, dictionary entry, example or rule wording copied from ASD-STE100. All lists and examples SHALL be independently authored and recorded in design Appendix A as `OWNER_REVIEW_REQUIRED` until the owner confirms them.
13.3 THE SYSTEM SHALL use no ASD or STEMG logo and no claim of affiliation.
13.4 THE SYSTEM SHALL support English text only. WHEN text contains no ASCII letters THEN THE SYSTEM SHALL return no findings and `limitations` SHALL say that only English is supported.

### Requirement 14: Quality evaluation and release gate (D-07)
**User Story:** As the project owner, I want measured precision per rule on data labelled by someone other than the implementer, and a hard stop on any known protected-token or obligation violation, so that release decisions rest on evidence.

#### Acceptance Criteria
14.1 THE SYSTEM SHALL provide an evaluation procedure that reads a labelled data set (JSON Lines: `text`, `expected` findings per rule with spans, `labeller`, `labelled_at`) and writes a report.
14.2 THE evaluation data used for the release gate SHALL be labelled by a person other than the implementer of the rule (the owner or a designated independent labeller), without access to the rule implementation. Data written or labelled by the implementer MAY be used for development tests and SHALL NOT count toward the gate.
14.3 FOR each enabled rule THE report SHALL state: number of reported findings (n), true positives, false positives, precision, the 95 % Wilson interval, and a status: `PASS` (n ≥ 30 and precision ≥ 0.90), `FAIL` (n ≥ 30 and precision < 0.90), or `INSUFFICIENT_SAMPLE` (n < 30).
14.4 THE report SHALL also state, per rule, the number of labelled clean sentences and the number of findings on them, and recall as information (not gated).
14.5 A rule with status `FAIL` or `INSUFFICIENT_SAMPLE` SHALL NOT be enabled by default in a release. It MAY be shipped as `experimental` and off by default, and the report SHALL say so.
14.6 A release SHALL be blocked when any known violation exists: any failing test in the groups PRT, GRD, MOD, NEG, MTH, UNT of the planned matrix, or any labelled example where a suggestion changed a protected token or an obligation class.
14.7 THE report SHALL not hide denominators: every score SHALL appear with its n.

### Requirement 15: Optional rules A05 and A06 (disabled by default)
**User Story:** As the project owner, I want the two low-confidence rules to exist only as optional extras, so that the initial release stays precise.

> **Status:** OPTIONAL. Not part of the initial release acceptance. Implemented only after tasks 1–12 and only with the owner's go-ahead.

#### Acceptance Criteria
15.1 WHEN A05 or A06 is implemented THEN `enabled_rules` SHALL NOT contain them by default.
15.2 A05 SHALL report runs of at least `cluster_notice` (default 4) content words, `warning` at `cluster_warning` (default 6), never produce a suggestion, and treat each `project_terms` phrase as one token. Computed: `Check the hydraulic system pressure control valve adjustment procedure.` = 7 (no project term), 5 (with `pressure control valve`); `Check the pressure control valve.` = 3.
15.3 A06 SHALL report `it, they, them, these, those` at the start of a sentence or as the object right after a listed instruction verb, excluding dummy-subject frames, SHALL never produce replacement text, and MAY list `candidates` for information only. The explanation SHALL state that a candidate is not proof of the referent (D-05).
15.4 Both rules SHALL pass requirement 14 before being enabled by default.

### Requirement 16: Preconditions and dependencies
**User Story:** As the project owner, I want P2 implementation to wait for the known P1 defects to be handled, so that the writing review is not built on top of a misleading screen.

#### Acceptance Criteria
16.1 P2 implementation (tasks from 1 onward) SHALL NOT start until the P1.1 remediation (labels A/B, stale results, untrusted file names, ambiguous units, absolute versus interval temperature, false unparsed-row warnings) is completed and independently assessed, **or** the owner records that it is non-blocking. Status on 2026-11-07: **not done** (design Appendix C).
> **AMENDED 2026-11-09 (Gate F only; owner authorization, see Requirement 17 and `tasks.md` Gate F).** For **Gate F (List-Free Foundation) only**, the owner has permitted work to proceed on the documented `SPECFORGE_P1_3_ACCEPTED_WITH_LIMITATIONS` baseline (private acceptance report; author-associated acceptance, **not** independent assessment). The original 16.1 text above remains in force, unchanged, for Gate W and every task outside the Gate F subset.
16.2 P2 tests SHALL be written and shown failing before module code exists (test-first gate G1).
16.3 No acceptance criterion in this file is satisfied by writing it; each is satisfied only by the verification evidence named in `tasks.md`.

### Requirement 17: Gate F — List-Free Foundation (AMENDMENT 2026-11-09)
**Provenance.** Added 2026-11-09 under the owner's narrow authorization of the same date (quoted in `tasks.md`, "Gate F amendment record"). Added **before** any Gate F test or code. This amendment was written by Kiro CLI editing the existing frozen file; it is not a claim that the original P2 specifications were produced by a full Kiro specification-agent workflow.
**User Story:** As the project owner, I want the deterministic protection primitives and the review-result contract built first, without any closed word list, writing rule or rewrite, so that the later Writing MVP stands on tested, source-faithful primitives.

**Definition.** Gate F is the subset of P2 that (a) consults **no** closed lexical list of design Appendix A (A.1–A.9), (b) emits **no** writing-rule finding, suggestion, revised text or applied decision, and (c) is limited to protected-span detection, source-offset integrity, explicit abstention, WARNING/CAUTION region marking and the future-compatible `review_technical_text` result contract. Gate F is **not** a Writing MVP, **not** a complete or validated implementation of A07, and **not** a certification or semantic understanding of any kind.

#### Acceptance Criteria
17.1 (source integrity) FOR every protected span, abstention and safety region THE SYSTEM SHALL satisfy `text[start:end] == excerpt` with `0 <= start < end <= len(text)`, where offsets are Python `str` indices (Unicode code points), `end` exclusive, relative to the unmodified input document. THE SYSTEM SHALL NOT normalize, trim or re-encode the input.
17.2 (immutability and identity) `review_technical_text` SHALL NOT modify its input, SHALL return `input.sha256` equal to the SHA-256 of the UTF-8 bytes of `text`, and SHALL contain no revised text.
17.3 (determinism and order) Equal `text` and equal options SHALL give deeply equal results. Protected spans SHALL be reported in ascending `(start, end)` order, non-overlapping, with ids `S-0001`, `S-0002`, … in that order; abstentions in ascending `(start, end, kind)` with ids `ABS-0001`, … .
17.4 (overlap) WHEN candidate spans overlap THE SYSTEM SHALL apply the fixed class priority quote > project term > math expression > number/identifier; a candidate wholly inside an accepted span SHALL be absorbed (not reported separately); a candidate that partially overlaps an accepted span SHALL be discarded and recorded as an `overlap_conflict` abstention.
17.5 (numbers) THE SYSTEM SHALL protect as literal text spans, without locale interpretation, unit conversion or semantic claim, at least: `1,000`, `10,5`, `1e3`, `1.0E5`, `-20`, `−5` (U+2212), `+12.5`, `0.5-1.0`, `0.5–1.0`, `2.5×10^-3`. A protected number does not assert that its physical meaning is correct.
17.6 (identifiers) THE SYSTEM SHALL protect tokens that match the documented syntactic identifier shapes of design §14.4 (including `P-101`, `V-201`, `ISO-9001`, `M8-1.25`, `6205-2RS`) and SHALL NOT classify any other token as an identifier.
17.7 (quotes) THE SYSTEM SHALL protect clearly delimited quoted material (`"…"`, `“…”`, backtick code spans) within one paragraph. WHEN a delimiter is unmatched or ambiguous (for example an inch mark after a digit) THEN THE SYSTEM SHALL record an abstention and SHALL NOT guess a span.
17.8 (math) THE SYSTEM SHALL protect explicit mathematical expressions as defined in design §14.5 (including `0.5 < x <= 1.5`, `P = F / A`, `x=5`). WHEN an expression boundary is ambiguous THEN THE SYSTEM SHALL protect the literal operator/operand run found and record a `math_boundary_ambiguous` abstention. Protecting a literal comparison symbol SHALL NOT be described as understanding natural-language limits.
17.9 (project terms) Project terms SHALL be used **only** when passed explicitly in `ReviewOptions.project_terms`. THE SYSTEM SHALL contain no built-in technical vocabulary; with `project_terms=()` the outcome SHALL not depend on any word list.
17.10 (unit boundaries deferred) Because PT2 (unit) detection depends on the unreviewed list A.8a, THE SYSTEM SHALL NOT claim PT2 detection. WHEN a letter-initial token directly follows a bare number token on the same line THEN THE SYSTEM SHALL record a `unit_boundary_deferred` abstention covering that token and SHALL NOT classify it as a unit or as a non-unit.
17.10a (unclassified digit tokens) WHEN a token contains an ASCII digit, matches no number or identifier shape of design §14.4, and is claimed by no span THEN THE SYSTEM SHALL record an `unclassified_numeric_token` abstention over the token core. *(Delta 2026-11-09, added after the RED run.)*
17.11 (safety regions) THE SYSTEM SHALL mark a paragraph that begins (ignoring leading whitespace and the characters `* _ # > -`) with `WARNING` or `CAUTION` (case-insensitive, followed by a non-alphanumeric character or the end) as a safety statement region for the whole paragraph, with `rewrite_allowed = false`. `DANGER` (U-04, undecided) SHALL NOT be marked as a safety region; a paragraph beginning with `DANGER` SHALL yield a `safety_marker_undecided` abstention. No suggestion SHALL ever be produced for any text.
17.12 (no writing rules) THE SYSTEM SHALL return `findings == []`, `rules_executed == []`, `summary.findings == 0` and `summary.with_suggestion == 0` for every input. Requesting A01–A06 in `enabled_rules` SHALL NOT execute a rule and SHALL be reported under `rules_not_executed` with reason `GATE_W_BLOCKED`. `a02_rewrite=True` SHALL raise `ValueError`.
17.13 (no apply) `apply_decisions`, if present, SHALL fail closed: it SHALL return `revised_text == text`, no applied edits and `status == "REFUSED_FOUNDATION_ONLY"`, for any decisions.
17.14 (honest capabilities) THE result SHALL contain `capabilities` listing each primitive as `supported`, `partial` or `unsupported`; negation, limit phrases, modal/obligation classes, PT2 units, sentence segmentation, the A07 guard over suggestions, and all writing rules SHALL be listed `unsupported`. `lexical_lists_consulted` SHALL be `[]`. The result SHALL carry `L1` and `L2` and SHALL NOT match the forbidden-claim expression.
17.15 (robustness) FOR any `str` of length up to 200,000 THE SYSTEM SHALL return a result without raising; WHEN `text` is not a `str` it SHALL raise `TypeError`; WHEN options are invalid it SHALL raise `ValueError`. 5,000 characters SHALL be processed in under 1 second.
17.16 (separation) `app/ste_checker.py` SHALL import only the Python standard library and SHALL NOT be imported by `app/core.py`, `app/ingest.py`, `app/report.py`, `app/api.py`, `app/mcp_server.py` or `app/ui.py` in this checkpoint. No P1 behavior changes.

**Retained / deferred, not changed by Requirement 17:** R3.2–3.5 (guard over suggestions: there are no suggestions), R3.6 negation/limit/unit tokens, R4, R5, R6, R7, R9, R11, R12, R14, R15 and the DANGER part of R8.1 remain exactly as written and remain blocked (Gate W).

## Out of scope and future research (not requirements)
- LLM-generated rewrites; Level B conformance checking with a user-supplied standard; any claim of ASD-STE100 conformance.
- Turkish or other languages; spell checking; grammar parsing or part-of-speech tagging.
- A manual-edit decision (author types a replacement inside the tool, optionally marked as an authorized semantic change) — possible later; not in the initial release.
- Flagging `can`; reducing A03 false positives with a grammar parser; automatic detection of document type.
- An MCP tool for the writing review (the earlier idea in the private planning notes); not part of this release.
