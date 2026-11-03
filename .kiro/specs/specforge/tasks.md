# SpecForge execution tasks

## Gate 0 — repository bootstrap
- [x] Public GitHub repository exists.
- [x] MIT license committed to main and visible as LICENSE.
- [x] Development branch created; minimal spec + steering prepared.
- [ ] Record locally installed Kiro CLI version and successful login.

## Gate 1 — deterministic vertical slice (first CLI session)
- [ ] From Kiro CLI implement parser and Pint-based comparison in app/engine.py.
- [ ] Write pytest + Hypothesis tests for the explicit fixtures.
- [ ] Run tests and report actual passing/failing results.
- [ ] Implement minimal Streamlit interface and observe a local run.
- [ ] Wire read-only FastMCP; call tool from Kiro and compare output with direct engine.
- [ ] Implement and verify one supported PostFileSave test hook.
- [ ] Record changed files, command outputs, remaining blockers and GO/BLOCKED.

## Gate 2 — later, only if Gate 1 GO
- [ ] Improve usability, reproducible README and demo flow.
- [ ] Decide hosting that has no unexpected cost and provides public judge access.
- [ ] Prepare <3-minute public YouTube demo and technically accurate Kiro write-up.
- [ ] Verify student identity information and Devpost submission fields.
- [ ] Re-evaluate bonus award requirements before spending effort on separate Crew app.

**Guard:** No push, paid subscription, installation or broad refactor without explicit user approval. Keep main stable; use development for changes.
