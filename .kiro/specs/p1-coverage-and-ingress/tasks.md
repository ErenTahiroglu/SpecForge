# P1 coverage safety and ingress guard: tasks

- [x] T1 Archive the preflight probe evidence privately with a SHA-256 manifest (outside the repository).
- [x] T2 RED tests: `tests/test_p1_shared_parser_coverage.py`, `tests/test_p1_ingress_guard.py`; revise the `.txt` contract test and the
      config test with a written reason. 67 failures (final set) on an untouched copy of the frozen baseline source.
- [x] T3 GREEN: `_looks_like_unsupported_layout` and the per-line guard in `app/core.py`; `LINE_TOO_LONG` in `app/report.py`.
- [x] T4 Config and copy: `.streamlit/config.toml`, README, UI text, MCP docstring.
- [x] T5 Local real-browser smoke (upload boundaries, hidden row, pump station, over-long line).
- [x] T6 Independent adversarial review of commit c721a6d1 (Codex, read-only): BLOCK (F1 HIGH, F2 MEDIUM, F3 and F4 LOW).
- [x] T7 Round two, written test-first: F1 structural table-heading check; F2 recognition-only unit glyphs; F3 input-limit wording and the
      API/upload distinction; F4 rendered-copy check with a split-literal mutation; spec corrections (R3, R4, R7 to R9).
- [x] T8 Second independent review of commit 9f84f0c5 (Codex, read-only): BLOCK (N1 HIGH regression, N2 and N3 MEDIUM, N4 and N5 LOW).
- [x] T9 Round three, written test-first: N1 raw cell first with an additive three-glyph mapping instead of NFKC; N2 unescaped-pipe heading
      structure; N3 no label conversion; N4 comments; N5 excerpt wording; standing differential rule (R10).
- [ ] T10 Third independent review (not self-approved). Owner checkpoint before any push, PR, merge or deployment.
