# R6-A Engineering demo case and evidence-first UX: requirements

Extends `p1-evidence-first-audit`. The steering rules in `.kiro/steering/engineering_rules.md` apply. The audit engine, the
`status` / `verdict` / `coverage_complete` contract and Gate F are not changed. Issues: #13 (case), #14 (UX).

## R1 Synthetic pump-station case (issue #13)
THE SYSTEM SHALL ship a fully synthetic two-document case in `examples/pump_station/`: a specification (`.md`, pipe rows) and a
purchasing sheet (`.csv`). The files SHALL state that they are synthetic. They SHALL contain: one genuine pressure conflict
(`10 bar` vs `900 kPa`), the flow equivalence (`12 L/min` vs `0.0002 m^3/s`), the length equivalence (`25 mm` vs `0.025 m`),
one ambiguous-unit row (`hp`) that SHALL NOT be compared, and one quoted CSV measurement that SHALL be reported as not compared.
Expected outcome: `CONFLICTS_FOUND`, `coverage_complete = false`, with counts fixed by tests and the SI numbers checked by an
oracle independent of Pint.

## R2 One prominent outcome (issue #14)
THE SYSTEM SHALL show one outcome panel with the verdict in words and, separately, the coverage state ("complete" or
"incomplete, N items not compared"). Meaning SHALL NOT depend on colour alone.
WHEN `coverage_complete` is false OR any input issue exists, THE SYSTEM SHALL NOT show a success presentation, including when
conflicts are present (a conflict result SHALL also say that coverage is incomplete).

## R3 Evidence cards
Each conflict card SHALL show tag and property, both original values and units, both SI values, the relative difference, the
source document and line (or page) and the verbatim source row. Equivalences SHALL be visually distinct cards. Items not compared
SHALL be in their own section with the reason, location and original text. No invented locations, no confidence scores.

## R4 Consistency
`status`, `verdict`, `coverage_complete`, the on-screen outcome, the Markdown report and the JSON export SHALL agree. The Markdown
report SHALL state the verdict and the coverage state in words.

## R5 Reproducibility
A new user SHALL be able to select the pump-station example (or upload the two files) and obtain the same verdict, counts, SI
values and exports. README SHALL describe the steps and the expected result.

## Out of scope
Gate W, new parsing grammar, OCR, charts, hosting, deployment, changes to the core engine.
