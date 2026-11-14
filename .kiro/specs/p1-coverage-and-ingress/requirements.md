# P1 coverage safety and ingress guard: requirements and decision log

Issues: #12 (shared-parser false-complete), #16 (G5 hosting). Extends `p1-evidence-first-audit`. The steering rules in
`.kiro/steering/engineering_rules.md` apply: no value is parsed or compared that the existing grammar does not accept.

**Provenance.** Written by Kiro CLI on 2026-11-14 during the implementation round, after the RED tests were drafted and
before independent review. It records decisions and acceptance criteria; it is not a pre-implementation artifact and it is
not an independent acceptance. Owner decisions are quoted from the owner's hand-off of the same date.

## Owner decisions (not re-asked)
- D1. Recognition is separate from parsing. CSV *parsing* stays gated on a `.csv` file name. A clearly measurement-like row in
  another layout, including comma rows in `.txt`, `.md` and the MCP tool, is *recognized* and reported, never compared.
  This deliberately changes `test_csv_grammar_still_applies_only_to_csv_named_documents`.
- D2. A tag does not need a digit to be recognized (`PUMP`, `INLET`, `FLOW` are legal tags).
- D3. `Rev | Date | 2026-11-13` keeps its existing conservative warning. No date exemption in this patch.
- D4. Add an early linear-time guard for blank, delimiter-free and over-long lines, and align Streamlit's upload limit with
  the exact 2,000,000-byte application guard.

## Requirements
R1. WHEN a line matches no supported row pattern AND has three or more cells (split by `|`, or by `,`) AND a cell from the
third on is a number with an optional qualifier (`max`, `min`, `nom`, `typ`, `approx`, `about`, `ca`, a sign or approximation
symbol) and an optional unit word, THE SYSTEM SHALL report it as `UNPARSED_ROW` with document, line and unchanged text, and
SHALL NOT parse or compare it.
R2. For comma rows outside `.csv`, the first cell SHALL be an identifier-like token. For a bare number in the fourth cell or
later, the cell before it SHALL be a unit the engine knows, refuses as ambiguous, or a gauge/absolute pressure spelling.
R3. A conflict among compared rows SHALL stay authoritative (`CONFLICTS_FOUND`). `coverage_complete` SHALL be false WHEN any coverage gap exists, and
SHALL stay true when a conflict is found and every recognized row was compared.
R4. A blank line, or a line with neither `|` nor `,`, SHALL NOT reach any regular expression. A line with a delimiter and more
than `MAX_LINE_CHARS` (2,000) characters SHALL NOT be inspected; it SHALL be reported as `LINE_TOO_LONG` (excerpt: the first 200
characters of raw.strip() plus an optional ellipsis, so at most 201 characters; `length` is the raw line's character count) and SHALL
count as a coverage gap. The 200,000-character and 5,000-line limits are unchanged.
R5. `.streamlit/config.toml` SHALL set `server.maxUploadSize = 2` and keep `browser.gatherUsageStats = false`. The exact
application guard `MAX_FILE_BYTES = 2_000_000` SHALL stay.
R6. README, the UI text and the MCP docstring SHALL state: recognizable unsupported measurement-like rows are listed and stay
un-compared; other unrecognized formats may be missed; no-conflict results apply only to compared rows. They SHALL NOT claim
that all invalid measurement lines are detected.

R7. (Second round, review finding F1.) A line SHALL be skipped as a table heading only WHEN the next line is pipe-delimited, every
cell of it is a delimiter cell (`:?-{3,}:?`) and it has the same number of columns as the line. A bare `---` SHALL NOT suppress
the line above it. A delimiter row longer than 2,000 characters SHALL NOT suppress it either, and is reported as `LINE_TOO_LONG`.
R8. (Review finding F2, narrowed in round three.) The unit glyphs `Ω` (U+03A9 and U+2126), `℃` (U+2103) and `℉` (U+2109) SHALL count as
unit words for recognition only. The raw cell is always matched first; an additive mapping of exactly three characters (U+2103 to
`°C`, U+2109 to `°F`, U+2126 to U+03A9) is tried second. No other character is rewritten: whole-cell Unicode compatibility
normalization (NFKC) SHALL NOT be used, because it changed `m²` to `m2`, circled revision labels to digits and compatibility letters
to ASCII. The reported row text is unchanged and nothing is converted.
R9. (Review finding F4.) The banned-claim check SHALL run on rendered user-visible text as well as on source text, for the
surfaces that the tests render. It does not prove that no future wording can overclaim.
R10. (Round three, review findings N1 to N3; clarified in the fourth commit, review finding L1.) Standing rule, in two parts.
(a) A row that the first commit (c721a6d1) warned about, or reported as a conflict, SHALL NOT become a complete, green result.
(b) A control that the first commit kept quiet SHALL stay quiet, except for the R7 exception below and unless this spec lists another
exception and its reason. Listed exceptions: none for warnings lost; the first commit's own conservative warnings
(`Rev | Date | 2026-11-13`, `pc`, `pcs`, `A`, a full-width digit label) are unchanged.
R10 exception (cross-reference R7). The first commit was quiet for a row that is followed directly by a bare `---` line only because
it wrongly treated that `---` as a table delimiter row and skipped the row above it; that is the defect R7 removes. From the second
commit on, such a row is inspected. These three controls, each followed by a bare `---`, therefore produce the same two conservative
`UNPARSED_ROW` warnings that the first commit already produced for the identical row without the rule:
1. `| DOC-619 | year | 2028 |`
2. `| BX-619 | fastener | pc | 12 |`
3. `Rev | Date | 2026-11-13` (the owner-approved conservative warning; no date exemption)
These warnings are accepted and documented. The old skip SHALL NOT be restored, and part (a) is not relaxed. Recognition remains a shape
heuristic: this exception does not claim that these three rows are the only quiet controls that can warn once a bare `---` follows them.
R11. (Review finding N2.) Heading structure counts UNESCAPED pipes only: `\|` stays inside its cell and `\\|` is an escaped backslash
followed by a structural pipe. This applies to the table-heading check only; row recognition and row parsing are unchanged.

## Known limits (locked by tests, stated in the README)
False positives: `Rev | Date | 2026-11-13`; list rows such as `Item,count,3 pieces`; unit-named columns that the unit library accepts
(`pc`, `pcs`, `A`); comma metadata rows in a `.csv` whose third cell starts with a digit; delimiter-bearing prose lines over
2,000 characters.
Missed without warning: description-first comma rows outside `.csv`; a value inside a sentence; two-cell rows; a unit followed
by extra words; semicolon or tab delimiters; ranges, scientific notation and decimal commas in a late cell; a zero-width space
inside a qualifier; rows that mix `|` and `,` inside one line; unit glyphs other than the four named in R8 (for example `Å` and `‰`); full-width
delimiters; a bare number after an unknown unit word. `Nm` in a unit column is
recognized on Python 3.11 (Pint 0.25.x) and not on 3.12 (Pint 0.26.x); no round changes that path.
Deliberate loss in round three: the square-millimetre and square-kilogram signs and similar compatibility characters were recognized in
round two only as a side effect of NFKC. They are not recognized now, exactly as in the first commit; this is recorded, not overlooked.
Precision and recall of the recognizer have not been measured.

## Evidence that is not a rate
- The author's design sample (46 hand-written lines) is design evidence only.
- The independent reviewer's 70-case sample (45 measurement-like rows, 25 controls), observed on commit c721a6d1: 44/45 measurement-like
  cases were false-clean on the base and 16/45 (Python 3.12) or 15/45 (3.11) on the patch; controls that raised a warning rose
  from 4/25 to 10/25. These are hand-picked adversarial-sample counts, not recall or precision, and not a population estimate.
  Round two's counts on the same unchanged sample are recorded in the private round-two report.
- CPU: a serial run of 99 lines of exactly 2,000 characters took 1.916 s on the reviewer's hardware. Matching is bounded by the
  line limit; it is not linear time for every supported line.

## Interface changes introduced by this work (documented, not hidden)
New warning code `LINE_TOO_LONG` (fields `length`, `limit`, `excerpt`, `reason`); new key `coverage[document]["rows_too_long"]`
(a zero is present for every document); a third `UNPARSED_ROW.reason` text for comma rows outside `.csv`; new constant
`MAX_LINE_CHARS = 2000`. Clients that compare the whole coverage object for equality need updating.

## Input limits, stated at the strength the evidence supports
- Observed once, Brave on macOS, Streamlit 1.67.0, `maxUploadSize = 2`: a file of 2,000,000 bytes was accepted and 2,000,001 bytes was refused by the page.
- The installed Streamlit frontend chooses its megabyte base from a platform predicate; an offline run of its own functions gave
  2,097,152 bytes for a Windows platform string and 2,000,000 for macOS or Linux. A real Windows browser run was not verified.
- Streamlit's server-side check uses 2 MiB plus multipart framing; Community Cloud behaviour is unknown.
- The application guard for uploaded files is `MAX_FILE_BYTES = 2_000_000` (`FILE_TOO_LARGE`). `POST /audit` is a different path: it takes JSON
  text and stops at the 200,000-character / 5,000-line core guard with HTTP 422; it never uses `FILE_TOO_LARGE`.
