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
R3. A conflict among compared rows SHALL stay authoritative (`CONFLICTS_FOUND`); `coverage_complete` SHALL then be false.
R4. A blank line, or a line with neither `|` nor `,`, SHALL NOT reach any regular expression. A line with a delimiter and more
than `MAX_LINE_CHARS` (2,000) characters SHALL NOT be inspected; it SHALL be reported as `LINE_TOO_LONG` (excerpt at most
200 characters) and SHALL count as a coverage gap. The 200,000-character and 5,000-line limits are unchanged.
R5. `.streamlit/config.toml` SHALL set `server.maxUploadSize = 2` and keep `browser.gatherUsageStats = false`. The exact
application guard `MAX_FILE_BYTES = 2_000_000` SHALL stay.
R6. README, the UI text and the MCP docstring SHALL state: recognizable unsupported measurement-like rows are listed and stay
un-compared; other unrecognized formats may be missed; no-conflict results apply only to compared rows. They SHALL NOT claim
that all invalid measurement lines are detected.

## Known limits (locked by tests, stated in the README)
False positives: `Rev | Date | 2026-11-13`; list rows such as `Item,count,3 pieces`; comma metadata rows in a `.csv` whose
third cell starts with a digit; delimiter-bearing prose lines over 2,000 characters.
Missed without warning: description-first comma rows outside `.csv`; a value inside a sentence; two-cell rows; a unit followed
by extra words; semicolon or tab delimiters; ranges in the fourth cell; a bare number after an unknown unit word.
Precision and recall of the recognizer have not been measured; the probe sample (46 hand-written lines) is design evidence only.

## Measured while implementing
Streamlit 1.67.0 in a real browser with `maxUploadSize = 2`: 2,000,000 bytes accepted, 2,000,001 refused (decimal megabytes).
The server-side check uses 2 MiB. The brief's assumption that the browser limit is 2,097,152 bytes was wrong.
