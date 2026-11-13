# P1 Evidence-first audit: requirements

Extends `engineering-auditor`. Steering in `.kiro/steering/engineering_rules.md` applies: Pint decides numbers, every finding cites both documents with line and verbatim text, unknown or incompatible input is never a pass.

## R1 Mismatch with evidence
WHEN two documents give the same TAG and property in different values beyond the tolerance, THE SYSTEM SHALL report a conflict with both document names, both locations, both verbatim rows, both SI values and the relative difference (difference divided by the larger absolute value).
Example: `10 bar` and `900 kPa` give 1,000,000 Pa and 900,000 Pa, 10 %.

## R2 Verified equivalence
WHEN values agree after conversion within the tolerance, THE SYSTEM SHALL list them as verified equivalent, with both rows (`12 L/min` and `0.0002 m^3/s`; `25 mm` and `0.025 m`).

## R3 Incomplete analysis is visible
WHEN a row has an unsupported unit, cannot be parsed, has no partner in the other document, is duplicated, or has an incompatible dimension, THE SYSTEM SHALL list it with its location and SHALL NOT show the audit as complete.
WHEN nothing could be compared, THE SYSTEM SHALL say so and SHALL NOT show success.

## R4 Honest locations
Line numbers follow editor line breaks (`\n`, `\r\n`, `\r`). For a PDF, THE SYSTEM SHALL report the page and the line of the extracted text of that page, and SHALL say it is not a visual line. A page without text SHALL be reported as not audited. No OCR.

## R5 Formats
Supported: `.md`, `.txt`, `.csv`, text-based `.pdf`. Everything else, unreadable encodings and oversized files SHALL be refused with a reason.

## R6 Report
THE SYSTEM SHALL show conflicts, equivalents and coverage on screen and offer a human-readable Markdown report and a JSON export.

## R7 Writing review readiness (P2 not implemented)
Review items SHALL have slots for original text, issue, explanation, suggested wording and a human decision.

## Out of scope
STE-inspired writing checks, OCR, drawings, deployment.
