# SpecForge — Engineering Specification and SI-Unit Auditor

**Status: Design / CLI implementation pending. Not a working application yet.**

SpecForge is a new Kiroween 2026 **Frankenstein** candidate. It aims to connect engineering document inspection, dimensional analysis via Pint, traceable evidence, and a local MCP tool into a practical quality-control workflow.

## First verified vertical slice

Two structured files with rows such as `P-101 | pressure | 10 bar` and `P-101 | pressure | 900 kPa` are compared by tag and property. The first observable result must identify the mismatch, preserve both document names and line numbers, and avoid interpreting unsupported input as a successful audit.

Non-goals for this first gate: scanned PDFs, interpretation of P&ID symbols, autonomous engineering design approvals, CFD/FEM analysis, paid APIs, public deployment.

## Development

The default branch `main` contains the contest-compatible MIT license. Work happens on `development`. The initial requirements, design, tasks, and CLI handoff are in `.kiro/` and `docs/`.

Read [the first Kiro CLI session instructions](docs/KIRO_CLI_SESSION_01.md), then run Kiro CLI from the repository root. Kiro should create and verify the first functional implementation, not merely claim success because configuration files are present.

## Kiroween submission requirements

A submission must be built using Kiro in the eligible period, be available as a working application, have an open-source public GitHub repository containing the root `.kiro/` directory, and include a public YouTube demo under three minutes. The student bonus requires student verification. Nothing here is yet a complete submission.

Official rules: https://kiroween.devpost.com/rules

## License

MIT © 2026 Eren Tahiroğlu. See [LICENSE](LICENSE).
