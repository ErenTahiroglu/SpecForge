# SpecForge product and safety boundaries

- This is a new Kiroween 2026 project for the Frankenstein category, with a possible Best Student Project bonus.
- The actual software must be developed and substantively used through Kiro CLI/IDE; keep verifiable change and test evidence.
- Use local-first Python and free libraries. No paid APIs, cloud bill, automatic deployment, or API-key requirements.
- Compare engineering quantities with deterministic parsing and Pint unit conversions. Never delegate numerical conversions, equivalence decisions, or safety judgments to a language model.
- Report exact document/line/excerpt pairs for each finding. Keep unsupported rows and units visible as warnings or incomplete coverage, not clean passes.
- Do not claim the ability to interpret arbitrary technical prose, scanned PDFs, P&ID drawings, BOM semantics, or CFD/FEM results unless independently implemented and tested.
- Do not automatically run remote network commands, `git push`, install dependencies, or modify `main`. Require the user's approval for these actions.
- Do not add `.kiro/` to `.gitignore`; the contest requires that directory in the public repository.
- Keep the first validation gate small: demonstrate one known mismatch, two equivalent measurements, and explicit handling of unsupported input.
- Preserve upstream third-party license notices. Do not copy prior-repository code without documenting the origin and license.
