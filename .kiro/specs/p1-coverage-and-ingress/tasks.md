# P1 coverage safety and ingress guard: tasks

- [x] T1 Archive the preflight probe evidence privately with a SHA-256 manifest (outside the repository).
- [x] T2 RED tests: `tests/test_p1_shared_parser_coverage.py`, `tests/test_p1_ingress_guard.py`; revise the `.txt` contract test and the
      config test with a written reason. 67 failures (final set) on an untouched copy of the frozen baseline source.
- [x] T3 GREEN: `_looks_like_unsupported_layout` and the per-line guard in `app/core.py`; `LINE_TOO_LONG` in `app/report.py`.
- [x] T4 Config and copy: `.streamlit/config.toml`, README, UI text, MCP docstring.
- [x] T5 Local real-browser smoke (upload boundaries, hidden row, pump station, over-long line).
- [ ] T6 Independent adversarial review (not self-approved). Owner checkpoint before any push, PR, merge or deployment.
