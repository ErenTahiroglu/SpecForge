# Engineering Spec & SI-Unit Auditor — Requirements (Kiroween 2026)

**Project creation window:** 2026-10-30 to 2026-12-04 (official hackathon interval). This is a NEW project, not a fork or resubmission of an older personal application.
**Category:** Frankenstein. **Primary bonus:** Best Student Project: Frankenstein.
**Status:** Three-hour feasibility prototype only, not submission-ready or validated engineering software.

## User story 1 — Evidence-based unit comparison
As an engineer, I want to compare component measurements in two engineering tables so that a pressure/flow/length discrepancy is visible without inventing facts.

### Acceptance criteria (EARS)
- WHEN a document includes `TAG | property | numeric value unit`, THE SYSTEM SHALL extract its source name, line, TAG, property, value and unit.
- WHEN records from different documents share TAG and property, THE SYSTEM SHALL convert their values to compatible SI base units before comparing.
- WHEN relative disagreement exceeds the configured tolerance (default 1%), THE SYSTEM SHALL report the discrepancy, relative difference and both verbatim source rows.
- WHEN values agree after unit conversion, THE SYSTEM SHALL not report a numeric discrepancy.
- WHEN units are unknown or dimensions differ, THE SYSTEM SHALL emit an explicit warning rather than claiming the documents agree.
- WHEN no pair can be compared, THE SYSTEM SHALL state `NO_COMPARABLE_ROWS` rather than `PASS`.

## User story 2 — No hallucination and visible limitations
- THE SYSTEM SHALL NOT claim general PDF, drawing, or P&ID image interpretation in the prototype.
- THE SYSTEM SHALL NOT use an LLM for numerical truth or unverified engineering inferences.
- THE SYSTEM SHALL bound the file count, document size, and acceptable tolerance.
- THE SYSTEM SHALL never read secrets, trigger paid APIs, or make background network calls.
- THE SYSTEM SHALL allow a local-only demonstration and machine-readable JSON output.

## User story 3 — Reproducible development evidence
- WHEN Python application/test files are saved in a supported Kiro session, THE HOOK SHALL run a focused test suite.
- PROPERTY-BASED tests SHALL verify bar-to-kPa conversion invariance over many generated finite inputs.
- THE PROJECT SHALL include `.kiro/specs`, `.kiro/steering`, and `.kiro/hooks` tracked in Git.
- THE APP SHALL provide a local read-only MCP tool; its connection and actual Kiro invocation must be verified manually.

## Explicit exclusions / follow-up work
- No OCR or image-based P&ID analysis.
- No PDF cross-page table reconstruction; PDF text extraction is best effort.
- No guaranteed conflict detection for narrative prose or spreadsheets with arbitrary schemas.
- No paid cloud, hidden LLM calls, automatic deployment, or promises of contest prizes.
- No claim that the template itself proves meaningful Kiro use; actual Kiro session evidence must be collected.

## Prototype stop/go conditions
1. Two synthetic example documents produce exactly one pressure discrepancy with two source citations, and no flow/diameter false positives.
2. Unsupported units and incompatible dimensions generate warnings, not a false clean result.
3. Deterministic tests + property-based suite pass in the user's local environment.
4. Kiro IDE recognizes the hook, MCP server is callable, and its outputs match the API/core on same fixtures.
5. If (1)–(3) fail within the personal 3-hour gate, stop before adding UI/deployment features.
