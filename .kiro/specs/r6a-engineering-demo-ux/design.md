# R6-A design

- Data: `examples/pump_station/{spec.md,purchasing.csv}`. The CSV exercises filename-based CSV grammar and the quoted-field gap.
- Presentation model (`app/report.py`): `AuditView` gains `coverage_complete` (from the shared `audit_verdict`). The conflict
  detail text adds a coverage sentence when gaps exist. `render_markdown` prints the verdict and coverage state as words.
- UI (`app/ui.py`): an example selector (basic example kept as default), an outcome panel (verdict + coverage badge), bordered
  conflict cards with side-by-side Document A / Document B columns, bordered equivalence cards, a bordered "Not compared" panel.
  Native Streamlit components only.
- Tests: fixture/oracle tests, report-model tests (never success when incomplete), AppTest for the example flow, one real-browser run.
