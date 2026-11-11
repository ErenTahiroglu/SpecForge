# Technical design — prototype 0.1

## Purpose
A falsifiable demo of a novel, auditable tool-chain combination: extracted document rows -> Pint unit normalization -> deterministic contradiction detection -> same result exposed via Streamlit, FastAPI and a Kiro-accessible MCP tool. In the final hackathon, innovation and usability must be evidenced beyond the basic library combination.

## Architecture
```text
User uploads MD/TXT/CSV/PDF (<=2 MB per file)
  -> Streamlit read_file() + pypdf PDF text extraction (max 10 pages)
  -> core ENTRY parser (strict TAG | field | value unit rows)
  -> Pint conversion -> SI base units and dimensions
  -> grouped TAG+field comparisons (1% relative default)
  -> status / findings / warnings / two source rows / JSON export
                     ^
FastAPI POST /audit --+ (same pure core)
Kiro MCP stdio ------+ (same pure core, no external requests)
```

## Data contract
Input: `{ "documents": { "spec.md": "P-101 | pressure | 10 bar", "bom.md": "P-101 | pressure | 900 kPa" }, "tolerance": 0.01 }`.
Output: `{status, records, comparisons, tolerance, findings, warnings, scope}`. Each finding includes both `document`, `line`, `excerpt` and SI-normalized values. All data originates in submitted documents.

## Risk model
- Empty/unclear documents MUST NOT be silently called consistent; use `NO_COMPARABLE_ROWS`.
- Formulae, pressure gauge vs absolute pressure, nominal vs actual values, tolerances from standards, and symbol semantics are unknown unless explicitly standardized; do not claim domain correctness.
- Parsing strict tabular records avoids hallucination but reduces recall; evaluate with a known ground-truth fixture before considering broader parsing.
- PDF text extraction can reorder content, omit charts/drawings and drop line fidelity. The line number shown for PDF refers to extracted page text, not the visual PDF line.
- No executable file upload; only UTF-8 text and PDF text extraction. Public demo must use synthetic/unrestricted documents.
- Avoid data exfiltration: local-only until deployment choice is explicitly approved.

## Dependency/operation strategy
Python 3.11–3.13 target; FastAPI, Streamlit, Pint, pypdf, Hypothesis, pytest, MCP (Python package). Install dependencies with a Python virtual environment; never automatically install them in a Kiro hook.

## Proof before promotion
- `pytest` output and timestamped terminal logs on a supported local environment.
- Kiro session that shows real spec implementation work, a hook execution, and one MCP tool call.
- One 2-minute demo with exactly one genuine mismatch and one safe non-mismatch.
- A privacy-safe Git commit history starting in the permitted hackathon window.
