# SpecForge MVP — Requirements (2026-11-03)

## Problem
Engineers often find contradictory component specifications across documents. The first slice intentionally supports **structured measurement rows only**, using `TAG | property | numeric value unit`.

## User stories and acceptance criteria

### R1. Parse structured records
**As a reviewer**, I want to provide two UTF-8 .md or .txt documents with structured quantity rows.
- Parse tag, property, signed finite number, unit, source document name, 1-based source line and original line.
- Ignore Markdown table headers and blank lines without treating them as records; unsupported measurement-looking rows must produce a warning or explicit coverage limitation.
- Bounded input: reject overly large files and too many documents; reject NaN/Infinity and malformed numeric values.

### R2. Compare quantities with dimensional correctness
**As a reviewer**, I want equivalent SI quantities to compare correctly regardless of display unit.
- Use Pint with explicitly documented conventions. 1 bar = 100 kPa, 12 L/min = 0.0002 m^3/s, 25 mm = 0.025 m.
- Group by case-insensitive tag + property; compare matching records only.
- Default relative tolerance is 1%; define handling for zero values, missing matches, incompatible dimensions and duplicate keys.
- Dimensional mismatch must not be counted as a pass.

### R3. Explain findings
**As a reviewer**, I want discrepancies with traceable evidence.
- For `10 bar` vs `900 kPa` under `P-101 | pressure`, report **VALUE_MISMATCH** (~10% relative difference using max-absolute denominator) with both original values and source lines.
- Return separate `findings`, `warnings`, `comparisons`, and `coverage`.
- Do not display “all specifications consistent” if nothing comparable was parsed.

### R4. Test the engineering rules
- Pytest tests for mismatch, equivalence, dimensional mismatch, unsupported unit and empty/no-comparable input.
- Hypothesis property-based tests for bar/kPa and mm/m equivalence, within formatting tolerance.
- A reproducible local test command must exit nonzero on test failures.

### R5. First UI and read-only MCP integration
- A minimal Streamlit interface accepts two text files and shows findings and evidence pairs. No accounts or cloud required.
- Expose one read-only MCP tool, `compare_engineering_rows`, for the same deterministic engine.
- Observe a real MCP invocation from Kiro CLI before calling the integration verified.
- Add a `.kiro/hooks/` JSON hook consistent with the **installed** Kiro version; verify activation rather than counting a file as proof.

## MVP stop/go
GO only if: (1) deterministic fixtures pass, (2) property tests pass, (3) real Streamlit run succeeds, (4) real Kiro MCP tool call is observed and matches the engine, and (5) Kiro use is documented. Otherwise BLOCKED, with the exact failure.
