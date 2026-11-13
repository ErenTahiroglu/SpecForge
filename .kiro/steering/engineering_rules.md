---
inclusion: always
---
# Engineering invariants for Kiro

- Never fabricate engineering quantities, PDF citations, units, tolerances, equations, or source rows.
- Every mismatch report must contain BOTH actual document names, line numbers, and original text excerpts.
- Compare quantities only after Pint dimensionality validation and conversion to common SI units.
- Unknown units, ambiguous properties, and unreadable PDF drawings are unresolved; never treat them as a passing audit.
- Do not imply pressure gauge/absolute equivalence, or compare temperature differences as absolute temperatures.
- The sole accepted prototype format is `TAG | property | numeric value unit`; do not imply arbitrary P&ID/BOM semantics work.
- For every semantic change, update tests; Hypothesis property tests protect supported conversions.
- No LLM is authorized to override deterministic values. AI assistance is for proposing/coding/documenting only.
- Zero additional cloud/API expense. No background network requests, uploads to third parties, autonomous deployment, publishing, or modifications to other projects.
- Before expanding parsing scope, demand a synthetic fixture and acceptance criterion.
- Use Kiro's spec-driven process rather than silently coding. Preserve a human-readable decision log.
- New project only during the official Oct 30–Dec 4 2026 window; do not transplant prior copyrighted, noncommercial, or proprietary project source.
