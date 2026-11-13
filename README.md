# SpecForge — Engineering Specification and SI-Unit Auditor

SpecForge compares measurements in two engineering documents. It reads structured rows such as `P-101 | pressure | 10 bar`, converts the values to SI units with [Pint](https://pint.readthedocs.io/), and reports differences with the exact document, line and original text of both sides. It runs locally, uses no network and no language model, and decides every comparison deterministically.

It is a Kiroween 2026 **Frankenstein** candidate, developed with Kiro (specifications, steering, hooks and an MCP tool are in [`.kiro/`](.kiro/)).

**Status:** a working local application. It is **not hosted** (there is no public URL yet), it is **not a certification** of any document, and a result without conflicts covers only the rows that were actually compared.

## What it checks

- Rows are matched by tag and property (case-insensitive) across the two documents.
- Values are converted with Pint and compared with a relative tolerance (default 1 %, allowed range 0–20 %). The relative difference is `|a − b| / max(|a|, |b|)`.
- Unsupported or ambiguous rows are reported as warnings or incomplete coverage. They are never counted as a pass.
- Equal dimensions are **not** proof of equal meaning: for example torque and energy share a dimension. The engine does not know what a property means, and it does not check gauge versus absolute pressure.

## Quick start

Requires Git and Python 3.11 or 3.12. These commands were run on macOS; Windows and Python 3.13 have not been tested.

```bash
git clone https://github.com/ErenTahiroglu/SpecForge.git
cd SpecForge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Dependencies are version ranges, not pins (see [`requirements.txt`](requirements.txt)).

### Web interface

```bash
python -m streamlit run app/ui.py
```

Tick **Use the example documents** or upload two `.md`, `.txt`, `.csv` or text-based `.pdf` files.

### JSON API

```bash
python -m uvicorn app.api:app --port 8000
```

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/audit -H 'content-type: application/json' \
  -d '{"documents":{"spec.md":"P-101 | pressure | 10 bar","bom.md":"P-101 | pressure | 900 kPa"}}'
```

`POST /audit` takes 1–12 named documents and an optional `tolerance`. Each document is limited to 200,000 characters and 5,000 lines.

#### Result fields: `status`, `verdict`, `coverage_complete`

Every result (core, `POST /audit`, the MCP tool and the JSON export) carries three top-level outcome fields. Consumers should branch on `verdict` and `coverage_complete`.

- `status`: the original comparison outcome, kept for backward compatibility: `DIFFERENCES`, `NO_COMPARABLE_ROWS` or `NO_DIFFERENCES_FOUND`. **`NO_DIFFERENCES_FOUND` alone never means the audit was complete**; it can appear together with warnings and unmatched rows.
- `verdict`: the authoritative outcome, shared with the web interface and the Markdown report. Precedence, first match wins: `CONFLICTS_FOUND` (any finding; this wins even when coverage is incomplete), `NOTHING_COMPARED` (no comparison was made), `NO_CONFLICTS_PARTIAL_COVERAGE` (compared rows agree, but at least one coverage gap exists), `NO_CONFLICTS_IN_COMPARED_ROWS` (compared rows agree and there is no gap).
- `coverage_complete`: `true` only if at least one comparison was made and there is no coverage gap *within the supported structured-row scope*. It is not a certification of an entire document. Gaps are unparsed or unsupported rows (`UNPARSED_ROW`, `UNSUPPORTED_UNIT`, `AMBIGUOUS_UNIT`), unmatched rows, dimension or temperature mismatches, ambiguous comparisons and inconsistent repeats. A consistent repeat of the same value in one file is an informational note, not a gap. A conflict with complete coverage gives `CONFLICTS_FOUND` with `coverage_complete: true`; a conflict with a gap gives `CONFLICTS_FOUND` with `coverage_complete: false`.

The web interface's JSON download adds the same two fields at its top level; there they also count input problems such as an unreadable file, which the audit engine never sees.

### Demonstration

The files in [`examples/`](examples/) are synthetic fixtures. Comparing `examples/spec.md` with `examples/bom.md` must give exactly one conflict, `P-101 / pressure` (`10 bar` against `900 kPa`, a 10 % difference), and two equivalent pairs: `12 L/min = 0.0002 m^3/s` and `25 mm = 0.025 m`.

## Kiro MCP tool

[`.kiro/settings/mcp.json`](.kiro/settings/mcp.json) registers a read-only stdio server with one tool, `compare_engineering_rows`:

```json
{ "command": "python", "args": ["-m", "app.mcp_server"] }
```

Kiro's MCP configuration has no working-directory field, so this entry works only if both conditions hold:

1. `python` on `PATH` is the interpreter that has the dependencies installed (activate the virtual environment before starting Kiro from that shell).
2. The server process starts in the repository root so that `app.mcp_server` can be imported.

The tool accepts pipe-delimited rows only. It receives no filenames, so it **does not enable CSV grammar** (that is chosen from a `.csv` filename): comma-separated input is not safely audited by this interface, and a CSV row can be missed without a warning. Use the web interface or `POST /audit` with `.csv` document names for CSV. Its result carries `verdict` and `coverage_complete` as described above.

If either is not true, replace `command` with the absolute path of the project's Python. Do not add the tool to `autoApprove`. The stdio handshake and tool call were verified with this configuration from the repository root; a call from inside the Kiro IDE has **not** been verified.

## Source format and limits

One measurement per line: `P-101 | pressure | 10 bar`. Markdown table rows with outer pipes also work. In `.csv` files, `P-101,pressure,10 bar` and `P-101,pressure,10,bar` also work (quoted CSV fields are not supported). PDFs are read as text, best effort, up to 10 pages; evidence for a PDF is page-level.

Units with more than one meaning are refused, not guessed: `ton`, `gallon`, `quart`, `pint`, `cal`, `hp`, `Btu`, `bbl`/`barrel` and their short or plural forms. Use an explicit unit such as `short_ton`, `tonne`, `US_liquid_gallon`, `imperial_gallon`, `international_calorie`, `metric_horsepower` or `oil_barrel`. Temperature differences (`delta_degC`, or a property named difference, rise, drop or delta T) are never compared with absolute temperatures.

Not supported: scanned PDFs, OCR, drawings, P&ID images, free text, decimal commas (`10,5`), scientific notation (`1e3`), and gauge/absolute pressure units (`barg`, `bara`). Rows with a decimal comma, an exponent or an unsupported unit are reported as `UNPARSED_ROW` or `UNSUPPORTED_UNIT` warnings and are not compared. Quoted fields (`"P-101","pressure","10 bar"`, also in pipe rows) are not supported: a measurement-like row that uses them is **not compared** and is reported as an `UNPARSED_ROW` warning with its document, line and text, so the audit shows as incomplete (never as a clean pass). This applies where the quoted row is recognised: pipe rows anywhere, and comma rows in `.csv` documents, not in the MCP tool (see above). A non-measurement quoted line, such as a header, is not reported.

The optional field `dimension_key` in results is informational (sorted `[dimension]^exponent` terms, or `dimensionless`). The engine compares Pint dimensionality directly.

## P2 foundation (not a writing review)

[`app/ste_checker.py`](app/ste_checker.py) is a list-free foundation for a future technical-writing review: it protects numbers, identifiers, quoted text, explicit math and caller-supplied project terms with exact source offsets, marks `WARNING`/`CAUTION` paragraphs, and abstains when a boundary is ambiguous. It returns **no findings, no suggestions and no rewritten text**, it consults no word list, and it is not connected to the UI, API or MCP tool. The writing rules themselves are blocked pending owner decisions. See [`.kiro/specs/p2-ste-writing-review/`](.kiro/specs/p2-ste-writing-review/). This is not an ASD-STE100 checker and makes no conformance claim.

## Working with Kiro

- [`.kiro/specs/`](.kiro/specs/): requirements, design and tasks (`specforge/` is the bootstrap MVP; `engineering-auditor/`, `p1-evidence-first-audit/` and `p2-ste-writing-review/` are the implementation specifications).
- [`.kiro/steering/`](.kiro/steering/): product and engineering rules.
- [`.kiro/hooks/test-python-on-save.json`](.kiro/hooks/test-python-on-save.json): runs `tests/test_core.py` and `tests/test_api.py` when a Python file in `app/` or `tests/` is saved. The hook command itself was run and passes; a live trigger inside the Kiro IDE has **not** been observed.
- [`docs/KIRO_CLI_SESSION_01.md`](docs/KIRO_CLI_SESSION_01.md) holds the bootstrap instructions; [`docs/KIRO_CLI_SESSION_01_EXECUTION_LOG.md`](docs/KIRO_CLI_SESSION_01_EXECUTION_LOG.md) is the execution log of the first implementation session, with its provenance note.
- [`scripts/checkpoint_refresh.sh`](scripts/checkpoint_refresh.sh) and `scripts/checkpoint_state.py` are optional development tooling that refresh local code indexes. They need the external tools `codebase-memory-mcp` and `graphify` and are not needed to run or test the application. After each refresh the script checks that the code index cannot reach a private directory: every non-git directory next to the repository (plus `CHECKPOINT_PRIVATE_PATHS`, minus `CHECKPOINT_ALLOW_PATHS`; both are `:`-separated paths) is protected, and an indexed project inside, equal to or above one, or a symlink in the repository leading to one, marks the index stale. If the check cannot run, the index is also marked stale.

## Kiroween submission status

The contest requires meaningful use of Kiro, a public repository with an open-source license and a tracked `.kiro/` directory, a working application URL, and a public video under three minutes. This repository covers the source, license and `.kiro/` parts. A hosted application, the demo video and the written Kiro narrative are **not done**. Official rules: <https://kiroween.devpost.com/rules>.

## License

MIT © 2026 Eren Tahiroğlu. See [LICENSE](LICENSE). Third-party packages keep their own licenses; none is copied into this repository.
