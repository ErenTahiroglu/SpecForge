# Implementation Plan

> **Provenance.** Created 2026-11-07 (UTC+3) as the third file of the P2 spec, after `requirements.md` and `design.md` (timestamps in the private freeze record). No task below has been started. All checkboxes are unchecked. Nothing here was used to build P1.
> **Status:** FROZEN FOR OWNER REVIEW. **Implementation is blocked** until gates G-P1.1 and G-OWNER (task 0) are closed. A checkbox is ticked only when the evidence listed under the task exists and has been looked at.
> **Order rule:** tests before code. No file `app/ste_checker.py` and no `tests/test_ste*.py` exist at creation time.
> **AMENDMENT 2026-11-09 (Gate F / Gate W split).** Gate F (List-Free Foundation) may proceed under the owner's narrow authorization recorded below. **Everything not in the "Gate F task subset" stays blocked (Gate W)**. Original file SHA-256 before this amendment: `7aa715f4f25bc02cc259bdbd4097e5795c65892ac56efb8eb46c2987d00fcabe`. Original checkboxes are unchanged (50 unchecked at amendment time); Gate F tasks are tracked as `F-n` below.

## Gate F amendment record (2026-11-09)

**Owner authorization, quoted verbatim (Turkish, 2026-11-09):**
> P2'nin yalnızca kapalı sözcük listesine bağımlı olmayan Foundation kapsamını onaylıyorum. G-OWNER ve G-P1.1/U-11 koşullarının sadece Foundation için dar kapsamlı değiştirilmesine izin veriyorum. Önce eksik spesifikasyonlar doğrulanıp değişiklikler koddan önce belgelensin. Writing MVP, A01/A03, otomatik yeniden yazım ve incelenmemiş listeler için onay vermiyorum.

**Effect (and only this effect):**
- G-OWNER and G-P1.1/U-11 are **amended for Gate F only**, so that Gate F can proceed on the documented P1.3 baseline `SPECFORGE_P1_3_ACCEPTED_WITH_LIMITATIONS` (author-associated acceptance; this does not become independent acceptance).
- The original independent-assessment and owner-answer conditions **remain fully applicable to Gate W** and to every task not in the Gate F subset.
- No Appendix D "default if silent" is treated as owner approval. No lexical list (A.1–A.9) is approved, activated or consumed.

### Amended gates

| Gate | Original (preserved above) | Amendment 2026-11-09 |
|---|---|---|
| **G-P1.1** | blocks tasks 1–14 | does **not** block **Gate F tasks F-1…F-9**; still blocks tasks 1–14 outside the Gate F subset (Gate W) |
| **G-OWNER** | blocks tasks 1–14 | does **not** block Gate F tasks; U-01…U-11 and list review (U-03) still block Gate W |
| **G-F (new)** | – | Gate F exit criteria below |
| **G-W (new, = unchanged original blockers)** | – | see "Retained Gate W blockers" |

### Gate F task subset and traceability

| Task | Description | Requirements (17.x) | Derives from original task | Evidence |
|---|---|---|---|---|
| F-0 | Record amendment, hashes, consistency review before tests | process | 0.5 (tests-first rule), 16.2/16.3 | this section; private report |
| F-1 | Write Gate F tests; run them and save the genuine failing output **before** `app/ste_checker.py` exists | 17.1–17.16 | 1.1/1.2/1.3 partly, G1 | saved RED run |
| F-2 | Module skeleton, options, result shell, input identity (`sha256`), non-str/option errors | 17.2, 17.12, 17.14, 17.15 | 3.0 | tests green |
| F-3 | Offsets, lines, paragraphs, tokens, overlap resolution, ordering | 17.1, 17.3, 17.4 | 3.1 (without sentence boundaries) | tests green |
| F-4 | Numbers and identifiers | 17.5, 17.6 | 3.2 (PT1 only) | tests green |
| F-5 | Quotes and code spans | 17.7 | 3.2 (PT7 only) | tests green |
| F-6 | Math expressions | 17.8 | 3.2/3.3 (span form, not sentence flag) | tests green |
| F-7 | Explicit project terms | 17.9 | 3.2 (PT3 only) | tests green |
| F-8 | WARNING/CAUTION regions, DANGER abstention, unit-boundary deferral, abstention records | 17.10, 17.11 | 3.3 (safety only) | tests green |
| F-9 | Fail-closed `apply_decisions`, `rules_not_executed`, capabilities, separation | 17.12, 17.13, 17.14, 17.16 | 3.5 (refusal only), R10 | tests green |
| F-10 | Hypothesis properties; scoped mutation check; full regression; checkpoint | 17.1, 17.3, 17.15 | 6.1 (scoped), 13.1, 13.2 | logs in private report |

**Original tasks that remain blocked (Gate W):** 0.1–0.5 (not closed for Gate W), 1.x and 2.x except as re-scoped by F-1, 3.1 sentence boundaries, 3.3 obligation/instruction/math-sentence flags, 3.4 guard over suggestions, 3.5 `apply_decisions` behavior, 4 (A01), 5 (A03), 6 (full A07 mutation set), 7 (A02), 8 (A04, plus G-A04), 9, 10, 11, 12, 13.3–13.4, 14.

### Gate F exit criteria (G-F)
1. Requirement 17 criteria each have at least one passing automated test traceable by id.
2. Gate F tests were written and shown failing for a genuine reason before the module existed; GREEN follows.
3. Full existing Python suite passes with no previously passing test weakened or removed.
4. Scoped mutation check kills its mutants or explains survivors.
5. Zero findings, zero suggestions, zero revised text, zero lexical lists consulted — asserted by tests.
6. Dual-graph checkpoint refresh run or the checkpoint classified honestly incomplete.
7. Author-disjoint adversarial review (Codex) of the frozen implementation is **recommended but not performed by this implementation**. Exit criteria do not include any precision claim.

### Retained Gate W blockers (unchanged, not weakened)
G-OWNER for Writing (U-01…U-11 answers); independent assessment of P1 or owner non-blocking record for Gate W (U-11); review of lists A.1–A.9 (U-03); resolution of §5.3/U-02 and U-04 (DANGER) and U-06 (`shall`); G-A04; independent labeller and data (U-05, R14.2) with **at least 30** independently labelled positive findings per rule and **precision ≥ 0.90 per enabled rule**, the 95 % Wilson interval, and `INSUFFICIENT_SAMPLE`/experimental classification (R14.3–14.5); G1–G5 as originally written for rule groups; the release block on any known protection/obligation violation (R14.6). **Not authorized by the 2026-11-09 decision:** Writing MVP, A01/A03 detection, findings or suggestions, A02/A04/A05/A06, automatic rewriting or applying decisions, activation of unreviewed lists.

### Unresolved owner questions (not blocking Gate F)
1. U-04: should `DANGER` be a safety marker for Gate W? (Gate F abstains.)
2. U-02: does `must be X-ed → imperative` count as changing `must`? (Gate F implements nothing.)
3. U-03/U-06/U-01/U-05/U-07/U-08/U-10: unanswered.
4. Appendix C P1.1 defects D1–D6 were recorded on 2026-11-07 as NOT DONE; the P1.1/P1.2/P1.3 reports describe later remediation. Their independent assessment is still pending for Gate W.

### Concise consistency review (2026-11-09)
- R17 vs R1: R1.4 (no revised text) and R1.7 (order) are consistent; R1.7's `W-` ids apply to findings, none exist in Gate F.
- R17 vs R3: R3.6 lists negations, limit phrases and units as protected tokens — these need A.7a/A.7b/A.8a; Gate F protects only the pattern-based items of R3.6 and states the rest `unsupported` (17.14).
- R17 vs R8: R8.2/8.3 (suggestion-free, findings with severity) — Gate F has no findings; safety regions only. DANGER deferred.
- R17 vs R10/16.3: no import coupling; evidence rule retained.
- Design §14 vs §1.1 default `enabled_rules` and §5.3/U-02: documented in §14.8; no conflict affects Gate F.
- No unresolvable conflict found within the authorized scope.

## Gates

| Gate | Condition | Blocks |
|---|---|---|
| **G-P1.1** | P1.1 remediation (labels A/B, stale results, untrusted file names, ambiguous units, absolute vs interval temperature, false unparsed rows) completed and independently assessed, or the owner records it as non-blocking | tasks 1–14 *(AMENDED 2026-11-09: not blocking Gate F tasks F-1…F-10; see Gate F amendment record)* |
| **G-OWNER** | Owner answers U-01…U-11 (design Appendix D) or accepts the defaults; closed lists A.1–A.9 reviewed (U-03) | tasks 1–14 *(AMENDED 2026-11-09: not blocking Gate F tasks; still blocks Gate W)* |
| **G-A04** | Owner approves the A04 definition (U-01) | task 8 only |
| **G1** | Failing tests for the groups in task 1–2 have been run and the failing output saved | tasks 3–14 |
| **G2** | Tasks 3–6 green and mutation check green | task 7 |
| **G3** | Tasks 7–9 green; labelled-data report generated | task 10 |
| **G4** | Browser verification with screenshots that were viewed | task 12 |
| **G5** | Checkpoint fresh, full suite green, independent review requested | release |

## Tasks

- [ ] 0. Close preconditions *(no code)*
  - [ ] 0.1 Confirm P1.1 status; if not done, record "P2 implementation blocked"
    - _Requirements: 16.1_
    - Evidence: a dated line in the private freeze record (§P1.1) plus the probe output; **status on 2026-11-07 01:16: not done**
  - [ ] 0.2 Collect the owner's answers to U-01…U-11
    - _Requirements: 6 (status note), 7 (status note), 8.1, 14.2_
    - Evidence: owner answers recorded in the private freeze record or a new dated file
  - [ ] 0.3 Owner reviews the closed lists (design Appendix A) and marks each list accepted or changed
    - _Requirements: 13.2_
    - Evidence: owner-marked copy of Appendix A with date; edits applied to `design.md` with a dated change note
  - [ ] 0.5 Record that the rule "tests first" and "evidence before ticking" apply to every later task
    - _Requirements: 16.2, 16.3_
    - Evidence: gate G1 output (task 2) exists before the first line of module code; each ticked task links its evidence file
  - [ ] 0.4 Name the independent labeller and the delivery date of the evaluation set
    - _Requirements: 14.2, 14.3_
    - Evidence: written statement naming the labeller (not the implementer)

- [ ] 1. Write failing tests, foundation groups *(tests only; no module file)*
  - [ ] 1.1 Create `tests/test_ste_segmentation.py` for groups SEG (matrix rows T-SEG-01…10)
    - _Requirements: 1.5, 1.6, 2.1–2.9_
    - Evidence: pytest run showing every test failing with `ModuleNotFoundError: app.ste_checker`, output saved in the private development evidence folder
  - [ ] 1.2 Create `tests/test_ste_protection.py` for PRT, GRD, MOD, NEG, MTH, UNT (T-PRT-01…08, T-GRD-01…07, T-MOD-01…08 as amended, T-NEG-01…04, T-MTH-01…03, T-UNT-01…03)
    - _Requirements: 3.1–3.9, 5.6, 10.3_
    - Evidence: failing run saved; each test names the requirement id in its docstring
  - [ ] 1.3 Create the Hypothesis properties: protected sequence equality for every suggestion; offsets (`text[start:end] == original`); determinism; no-raise on arbitrary Unicode; `apply_decisions` idempotence
    - _Requirements: 1.1, 1.5, 2.8, 3.2, 9.9_
    - Evidence: failing run saved
  - [ ] 1.4 Create the label test (T-UI-03, T-UI-04 string checks) with the forbidden-claim expression of the requirements introduction
    - _Requirements: 13.1, 11.8_
    - Evidence: failing run saved

- [ ] 2. Write failing tests, rule groups
  - [ ] 2.1 `tests/test_ste_a01.py` (T-A01-01…08)
    - _Requirements: 4.1–4.7_
    - Evidence: failing run saved
  - [ ] 2.2 `tests/test_ste_a03.py` (T-A03-01…08 as amended; no option tests)
    - _Requirements: 5.1–5.8_
    - Evidence: failing run saved
  - [ ] 2.3 `tests/test_ste_a02.py` (T-A02-01…08)
    - _Requirements: 7.1–7.7_
    - Evidence: failing run saved
  - [ ] 2.4 `tests/test_ste_a04.py` (T-A04-01…08) — written but marked `skip` until G-A04
    - _Requirements: 6.1–6.6_
    - Evidence: file exists; skipped with the reason "G-A04"
  - [ ] 2.5 `tests/test_ste_decisions.py` (T-HUM-01…06, new AMEND-03…AMEND-07)
    - _Requirements: 9.1–9.9, 12.3_
    - Evidence: failing run saved
  - [ ] 2.6 `tests/test_ste_safety.py` (T-GRD-06, T-A03-08, new AMEND-02)
    - _Requirements: 8.1–8.4_
    - Evidence: failing run saved
  - [ ] 2.7 `tests/test_ste_separation.py` (T-UNT-03, new AMEND-08, AMEND-09)
    - _Requirements: 10.1–10.4_
    - Evidence: failing run saved
  - **Gate G1:** show the combined failing run; STOP if any test passes without code.

- [ ] 3. Foundation: text model, segmentation, protected spans, guard *(implementation begins here)*
  - [ ] 3.0 Module skeleton: standard-library-only imports (plus read-only `split_lines`, `ENTRY`, `CSV_ENTRY`), `input.sha256`, input never modified, no revised text in the review result
    - _Requirements: 1.2, 1.3, 1.4_
    - Evidence: import-list test (AMEND-08 variant for the module itself); AMEND-01 green; T-HUM-02 green
  - [ ] 3.1 `app/ste_checker.py`: options, `split` into units, skipped lines, sentence boundaries, word count
    - _Requirements: 2.1–2.9, 4.1_
    - Evidence: group SEG green; `text[start:end] == original` property green
  - [ ] 3.2 Protected-span detection (design §3) and `protected_sequence`
    - _Requirements: 3.6–3.8_
    - Evidence: PRT group green
  - [ ] 3.3 Obligation classes, instruction clauses, safety and math-bearing flags (design §4)
    - _Requirements: 3.4, 3.7, 8.1_
    - Evidence: MOD, MTH groups green
  - [ ] 3.4 Guard checks 1–6 with the A02 relaxation switch (design §5.3)
    - _Requirements: 3.1–3.5, 3.9, 5.6_
    - Evidence: GRD group green; guard cannot be disabled (T-GRD-05)
  - [ ] 3.5 `apply_decisions` (design §6.2)
    - _Requirements: 9.1–9.9_
    - Evidence: T-HUM group green; idempotence property green

- [ ] 4. A01 sentence length
  - _Requirements: 4.1–4.7_
  - Evidence: A01 tests green; word counts equal requirement 4.4

- [ ] 5. A03 ambiguous modals: flag and ask
  - _Requirements: 5.1–5.8, 8.2_
  - Evidence: A03 tests green; test proves `suggestion is None` for every A03 finding

- [ ] 6. A07 mutation check (foundation)
  - [ ] 6.1 Write the mutation script outside the repository (in the private tools folder) with ≥ 12 mutants: guard off, negation class off, protected sequence order off, outside-span check off, sentence-start check off, modal-word check off, safety withhold off, math-bearing check off, decimal rule off, abbreviation rule off, offsets shifted, "accept all" includes A03
    - _Requirements: 3.1–3.5, 8.2, 9.7_
    - Evidence: every mutant killed; output saved
  - **Gate G2.**

- [ ] 7. A02 passive voice
  - _Requirements: 7.1–7.7_
  - Evidence: A02 tests green; obligation sequence unchanged for every suggestion (T-A02-08, T-MOD-01…02, T-NEG-02)

- [ ] 8. A04 multiple instructions — **blocked by G-A04**
  - _Requirements: 6.1–6.6_
  - Evidence: A04 tests un-skipped and green; counts equal design §8.4; owner approval recorded

- [ ] 9. Review result assembly
  - [ ] 9.1 Findings ordering, ids, summary, limitations, explicit abstention catalogue (design §5.4)
    - _Requirements: 1.7, 3.9_
    - Evidence: T-DET-01 green; every `abstain_reason` is in the catalogue (property test)
  - [ ] 9.2 Performance and robustness
    - _Requirements: 1.5, 1.6_
    - Evidence: T-PERF-01, T-ROB-01 green

- [ ] 10. Evaluation tooling (D-07)
  - [ ] 10.1 `scripts/ste_eval.py` reads labelled JSON Lines and writes the JSON and Markdown precision report (design §11)
    - _Requirements: 14.1, 14.3, 14.4, 14.7_
    - Evidence: tests with a small synthetic labelled file; the report shows n, TP, FP, precision, Wilson interval, status
  - [ ] 10.2 Run on the independently labelled set when delivered
    - _Requirements: 14.2, 14.5, 14.6_
    - Evidence: report file; labeller id and data hash recorded; **if the set is missing, the status is `INSUFFICIENT_SAMPLE` and rules ship off by default**
  - **Gate G3.**

- [ ] 11. UI, card integration and export
  - [ ] 11.1 Additive `ReviewCard` fields and `writing_cards(result)` in `app/report.py`
    - _Requirements: 11.2, 11.3_
    - Evidence: T-UI-01 green; the 82 existing tests still pass
  - [ ] 11.2 "Writing review (STE-inspired)" section in `app/ui.py`: text area, findings, cards, Accept/Reject/Clarified, revised preview, labels
    - _Requirements: 11.1, 11.3–11.8, 13.1, 13.3, 13.4_
    - Evidence: T-UI-05, T-UI-06 string and state tests; manual list of controls
  - [ ] 11.2a No ASD or STEMG logo, name-as-claim or affiliation wording anywhere in the section
    - _Requirements: 13.3_
    - Evidence: string scan test over the UI source and exports; list of static assets (none added)
  - [ ] 11.3 Markdown and JSON export
    - _Requirements: 12.1–12.4_
    - Evidence: T-EXP-01, T-EXP-02 green

- [ ] 12. Browser verification
  - Extend the browser script (outside the module) with scenarios: paste text → findings → expand a card → accept → revised preview → reject → clarified (A03) → export download; the safety statement; a text with decimals and identifiers; changing the text clears old decisions
  - _Requirements: 11.1–11.8, 12.1–12.4, 8.2, 9.2, 9.7_
  - Evidence: script results saved; **screenshots viewed** (not only text assertions); downloaded files compared with the module output
  - **Gate G4.**

- [ ] 13. Quality gates and checkpoint
  - [ ] 13.1 Full suite green (82 existing + new)
    - _Requirements: 14.6_
    - Evidence: pytest output
  - [ ] 13.2 Dual-graph checkpoint `bash scripts/checkpoint_refresh.sh P2`
    - Evidence: `RESULT: INDEX_REFRESHED` then `NO_CHANGE`
  - [ ] 13.3 Record every rule's gate status (PASS / FAIL / INSUFFICIENT_SAMPLE) and decide which rules ship enabled
    - _Requirements: 14.3, 14.5_
    - Evidence: the report of task 10.2 and a short decision note
  - [ ] 13.4 Request independent review
    - Evidence: review report
  - **Gate G5.**

- [ ] 14. OPTIONAL after task 13 and only with the owner's go-ahead: A06 then A05, disabled by default
  - _Requirements: 15.1–15.4_
  - Evidence: tests; both rules absent from `enabled_rules` by default; evaluation per requirement 14 before any default-on change

## Traceability: requirements → tasks → planned evidence

The 108 rows of the matrix in the private rule-refinement notes (§5) are **planned evidence. None has been implemented or run.** The "Test groups" column names the planned rows; the "Amended" rows are described below.

| Requirement | Tasks | Test groups (planned rows) | Verification evidence |
|---|---|---|---|
| R1 deterministic, non-destructive | 3, 9 | T-DET-01, T-PERF-01, T-ROB-01, T-HUM-02, AMEND-01 | property run, timing |
| R2 segmentation | 3.1 | T-SEG-01…10 | SEG group green |
| R3 protected tokens, guard | 3.2–3.4, 6 | T-PRT, T-GRD, T-MTH, T-UNT-01…02, T-MUT-01 | group runs, mutation output |
| R4 A01 | 4 | T-A01-01…08 | A01 group |
| R5 A03 | 5 | T-A03-01…08 (amended), T-MOD-04…08, AMEND-02 | A03 group |
| R6 A04 (PROPOSED) | 8 | T-A04-01…08, T-NEG-01 | A04 group after G-A04 |
| R7 A02 | 7 | T-A02-01…08, T-MOD-01…03, T-NEG-02…04 | A02 group |
| R8 safety | 3.3, 5, 7 | T-GRD-06, T-A03-08, T-UI-06, AMEND-02 | safety group |
| R9 decisions | 3.5, 11 | T-HUM-01…06, T-CLN-03, AMEND-03…07 | decisions group |
| R10 separation | 3, 11 | T-UNT-03, AMEND-08, AMEND-09 | separation group |
| R11 UI | 11, 12 | T-UI-01…06, AMEND-10 | browser run with viewed screenshots |
| R12 export | 11.3, 12 | T-EXP-01…02 | export tests, downloaded files |
| R13 labels, legal | 11.2, 1.4 | T-UI-03, T-UI-04, AMEND-11 | string tests |
| R14 evaluation, release gate | 10, 13 | T-CLN-01…02 (amended), AMEND-12…14 | evaluation report |
| R15 optional rules | 14 | T-A05-01…05, T-A06-01…06 (amended) | later |
| R16 preconditions | 0 | – | process evidence |

### Matrix amendments caused by owner decisions (the 108 rows remain the baseline)
| Row | Change | Reason |
|---|---|---|
| T-A03-01, T-A03-02 | expect `clarification` set, `suggestion` and `options` null; no option text | D-03 |
| T-A03-03 | `might` also yields a clarification (not just "no options") | D-03 |
| T-A03-07 | "accept all" excludes A03 and findings without a suggestion | D-03, R9.7 |
| T-MOD-03 | replaced: no text anywhere contains an alternative modal for A03 | D-03 |
| T-MOD-05, T-MOD-07 | adjusted to the clarification behavior | D-03 |
| T-A06-01…06 | A06 default off; `candidates` informational; no replacement | D-05 |
| T-A05-04 | A05 default off | D-04 |
| T-CLN-01 | clean corpus is a development test; the gate uses independent data | D-07 |
| T-CLN-02 | gate data must be labelled by a non-implementer; scores with n; `INSUFFICIENT_SAMPLE` allowed | D-07 |
| T-UI-05 | replaced: A03 cards offer Clarified and Reject only | D-03 |
| T-GRD-02 | add `must→should`, `should→must`, `may→can`, `can→may` candidates, all suppressed | D-03 |
| `enabled_rules` default | A06 removed | D-05 |
| option `approved_terms` | renamed `project_terms` | forbidden-claim word |

### New planned rows (do not alter the 108)
| ID | Test | Requirement |
|---|---|---|
| AMEND-01 | `input.sha256` equals the SHA-256 of the input; input unchanged after review | 1.3 |
| AMEND-02 | WARNING/CAUTION/DANGER paragraph: findings present, no suggestion, `abstain_reason = "safety statement: rewrite manually"`, no option can change this | 8.1–8.4 |
| AMEND-03 | `apply_decisions` with a result for other text raises `ValueError` | 9.1 |
| AMEND-04 | `accepted` on a finding without suggestion → `skipped: no suggestion` | 9.5 |
| AMEND-05 | `clarified` leaves `revised_text == text` | 9.2 |
| AMEND-06 | `original_sha256` equals the input hash; the input string compares equal before and after | 9.1 |
| AMEND-07 | overlapping accepted edits (A01 and A04 on one sentence) → both in `conflicts` | 9.4 |
| AMEND-08 | `ste_checker` is not imported by `core`, `ingest`, audit parts of `report` (import graph test) | 10.1 |
| AMEND-09 | audit output identical with the writing review run before and after | 10.1–10.2 |
| AMEND-10 | changing input text clears or marks old decisions `outdated` | 11.6 |
| AMEND-11 | no suggestion text contains a different modal word than its original, except under the flagged A02 rule | 5.6 |
| AMEND-12 | evaluation report shows n, TP, FP, precision, interval, status for each rule | 14.3, 14.7 |
| AMEND-13 | n < 30 gives `INSUFFICIENT_SAMPLE`, never `PASS` | 14.3 |
| AMEND-14 | a data file whose `labeller` equals the implementer id is rejected by the gate command | 14.2 |

## Estimated effort (hours)
| Block | Tasks | Hours |
|---|---|---|
| Preconditions and owner reviews | 0 | owner time; 0.5 recording |
| Failing tests | 1–2 | 2.0 |
| Foundation | 3 | 2.0 |
| A01, A03 | 4, 5 | 1.2 |
| Mutation check | 6 | 0.7 |
| A02 | 7 | 1.0 |
| A04 (after G-A04) | 8 | 1.0 |
| Result assembly | 9 | 0.5 |
| Evaluation tooling | 10 | 1.0 |
| UI, cards, export | 11 | 1.5 |
| Browser verification | 12 | 1.0 |
| Gates, checkpoint, review request | 13 | 0.7 |
| **Initial release (0–13)** | | **≈ 12.6 h** (≈ 11.6 h without A04) |
| P1.1 (separate, blocking) | – | ≈ 1.5 h, not counted above |
| Optional A06, A05 | 14 | ≈ 1.3 h |
Owner time for labelling the evaluation set is outside these hours and decides whether any rule can be enabled by default.
