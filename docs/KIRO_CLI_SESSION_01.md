# Kiro CLI — First Development Session

## Starting point
Repository: `ErenTahiroglu/SpecForge`. Branch: `development`.
This bootstrap **contains no claimed working implementation**; the first meaningful code should be implemented and exercised through Kiro CLI.

Official Kiro CLI quick start: https://kiro.dev/docs/cli/quick-start/
Official contest rules: https://kiroween.devpost.com/rules

### macOS terminal
```bash
git clone https://github.com/ErenTahiroglu/SpecForge.git
cd SpecForge
git switch development
git status --short
kiro-cli --version
kiro-cli doctor
kiro-cli login
kiro-cli
```

If `kiro-cli` is not installed, use the official install instructions at https://kiro.dev/downloads/. Inspect installers before running them.

### Paste this prompt into Kiro CLI

You are working in ErenTahiroglu/SpecForge on development for the Kiroween 2026 Frankenstein category.

Before coding, read .kiro/steering/product.md, .kiro/specs/specforge/requirements.md, design.md, tasks.md and both sample files. Verify the Git branch and installed Kiro CLI version. This repo was initialized after the contest submission period opened; it is not yet a functional app.

Implement the smallest new deterministic vertical slice needed to detect P-101 pressure 10 bar vs 900 kPa and report the two original lines. Prove that 12 L/min equals 0.0002 m^3/s and 25 mm equals 0.025 m. Use Pint for all conversions, never the LLM. Treat bad units, incompatible dimensions and no-comparison cases as incomplete, never as a pass.

Build real pytest/Hypothesis tests and a minimal Streamlit UI. After the core tests actually pass, build one local read-only MCP tool, wire it through workspace .kiro/settings/mcp.json and make one real Kiro MCP call. Then create one PostFileSave hook in the documented schema for this installed CLI and observe that it triggered.

Do not install dependencies, alter other repositories, spend money, deploy, commit or push until I explicitly approve the specific action. Ask approval for any package installation, then execute only the approved command. Do not claim successful tests, hooks, MCP, or app functionality without observed evidence. Avoid unnecessary hardening and unrelated features.

End with a report: CLI version; baseline SHA; precise files changed; tests and outputs; exact MCP call and result (or BLOCKED); hook evidence (or UNVERIFIED); remaining scope; factual GO/BLOCKED decision.

### Why not copy the previous ZIP wholesale?

The earlier 23-file prototype is useful as a reference, but its actual Pint/Hypothesis/MCP execution was **not validated**. For clear competition provenance, Kiro should substantively create and run the implementation in this repository, with recorded evidence.
