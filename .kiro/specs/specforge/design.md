# SpecForge MVP — Architecture

## Proposed modules
- `app/engine.py`: pure parser, unit registry, normalization, comparison, verdict and evidence; no AI or network.
- `app/ui.py`: Streamlit upload and result display; reports limited input coverage accurately.
- `app/mcp_server.py`: local FastMCP stdio wrapper exposing read-only `compare_engineering_rows`.
- `tests/test_engine.py`: deterministic regressions and Hypothesis properties.
- `examples/spec.md` and `examples/bom.md`: reproducible expected mismatch + equivalent rows.

## Suggested environment
Python 3.12, venv, Pint, pytest, hypothesis, Streamlit, MCP SDK. Keep dependency set small and document the actual installed versions in test evidence; don't pin guessed versions without checking. No database, external LLM or FastAPI is necessary for the first vertical slice.

## Data contract
Input: mapping of human-readable source names to UTF-8 document content, optional decimal relative tolerance (default 0.01).
Output: JSON-safe `status`, `records`, `comparisons`, `findings[]`, `warnings[]`, `coverage`.
Each finding must contain both source names, line numbers, exact excerpts, original values/units and normalized values.

## Decision rules
- Unsupported dimension, invalid units and parsing ambiguity must never be presented as a clean pass.
- “No comparable rows” has a different status from “compared rows with no detected difference.”
- Compare only same tag and property, never silently infer equivalence from natural language.
- Normalize temperature offsets correctly if temperature is supported; otherwise explicitly exclude temperatures in the first slice.
- Numerical result cannot depend on Kiro/LLM output.

## Evidence plan
Record Kiro CLI version, git baseline SHA, Kiro session actions, changed files, test commands/results, actual UI screenshot or observed run, MCP tool call/result, hook trigger logs and next decision. No fabricated test or demo claims.
