"""Public-host ingress hardening (owner-approved 2026-11-14). This is input protection, not a product feature.

1. Regex CPU. On the frozen baseline one line of ~100,000 spaces took ~42 s and cost grows with the square of the line
   length (measured locally; NOT an observed Cloud outage). Fix: a linear-time fast path for blank and delimiter-free
   lines, and a per-line limit (MAX_LINE_CHARS) before any regular expression runs on a delimiter-bearing line.
   A line over the limit is never dropped silently: it becomes a visible LINE_TOO_LONG coverage gap.
2. Upload limit. Streamlit's own limit (server.maxUploadSize = 2) sits at the application's exact guard
   (MAX_FILE_BYTES = 2,000,000 bytes). Measured in a real browser with Streamlit 1.67.0: the page refuses files above
   2,000,000 bytes. Streamlit's SERVER-side check is 2 MiB = 2,097,152 bytes, so the numbers differ for clients that bypass
   the page and the application guard stays as defence in depth. The browser behaviour is checked by the local browser smoke,
   not by these unit tests."""
import time
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core, report
from app.api import app as api_app
from app.core import audit_documents
from app.ingest import MAX_FILE_BYTES, ingest_upload

ROOT = Path(__file__).resolve().parent.parent
SUP = "P-101 | flow | 100 L/min"


def padded(row, length):
    assert len(row) <= length
    return row + " " * (length - len(row))


def too_long(r):
    return [w for w in r["warnings"] if w["code"] == "LINE_TOO_LONG"]


# ---------- linear-time fast paths: no regular expression may run on these lines
class _Boom:
    def __getattr__(self, name):
        raise AssertionError("a regular expression ran on a line that cannot be a measurement row")


def test_blank_and_delimiter_free_lines_never_reach_a_regular_expression(monkeypatch):
    for attr in ("ENTRY", "CSV_ENTRY", "_TABLE_SEPARATOR"):
        monkeypatch.setattr(core, attr, _Boom())
    text = "\n".join([" " * 60_000, "\t \t", "x" * 80_000, "plain prose without any delimiter " * 400, ""])
    records, warnings, cov = core._parse("a.md", text)
    assert records == [] and warnings == [] and cov["rows_unparsed"] == 0


def test_a_long_blank_line_is_not_a_warning_and_does_not_hide_the_next_row():
    r = audit_documents({"a.md": " " * 150_000 + "\n" + SUP, "b.md": SUP})
    assert r["warnings"] == [] and r["comparisons"] == 1 and r["verdict"] == "NO_CONFLICTS_IN_COMPARED_ROWS"
    assert r["matches"][0]["reference"]["line"] == 2                         # line numbers still count the blank line


# ---------- the per-line limit
def test_limit_is_modest_and_documented():
    assert isinstance(core.MAX_LINE_CHARS, int) and 1_000 <= core.MAX_LINE_CHARS <= 5_000
    assert core.MAX_LINE_CHARS < core.MAX_CHARS                              # the 200,000-character document limit stays


def test_a_row_exactly_at_the_limit_is_still_compared():
    row = padded(SUP, core.MAX_LINE_CHARS)
    assert len(row) == core.MAX_LINE_CHARS
    r = audit_documents({"a.md": row, "b.md": SUP})
    assert too_long(r) == [] and r["comparisons"] == 1 and r["coverage_complete"] is True


def test_a_row_one_over_the_limit_is_a_visible_gap_not_a_green_audit():
    over = padded("P-102 | pressure | 10 bar", core.MAX_LINE_CHARS + 1)
    over_b = padded("P-102 | pressure | 16 bar", core.MAX_LINE_CHARS + 1)
    r = audit_documents({"a.md": SUP + "\n" + over, "b.md": SUP + "\n" + over_b})
    w = too_long(r)
    assert [(x["document"], x["line"]) for x in w] == [("a.md", 2), ("b.md", 2)]
    assert all(x["length"] == core.MAX_LINE_CHARS + 1 and x["limit"] == core.MAX_LINE_CHARS and x["reason"] for x in w)
    assert all(len(x["excerpt"]) <= 201 for x in w)                          # never a giant excerpt in results or logs
    assert w[0]["excerpt"].startswith("P-102 | pressure | 10 bar")
    assert r["comparisons"] == 1 and r["findings"] == []                      # the unread row is not guessed
    assert r["verdict"] == "NO_CONFLICTS_PARTIAL_COVERAGE" and r["coverage_complete"] is False
    assert r["coverage"]["a.md"]["rows_too_long"] == 1 and r["coverage"]["b.md"]["rows_too_long"] == 1


def test_a_conflict_elsewhere_stays_authoritative_next_to_a_long_line():
    over = padded("P-102,pressure,10 bar", core.MAX_LINE_CHARS + 50)
    r = audit_documents({"a.csv": "P-101,flow,100 L/min\n" + over, "b.csv": "P-101,flow,50 L/min"})
    assert r["verdict"] == "CONFLICTS_FOUND" and r["coverage_complete"] is False and len(too_long(r)) == 1


def test_long_line_with_a_delimiter_is_fast_enough():
    """Frozen baseline: 30,000 padded spaces took seconds (quadratic). Generous bound; a linear guard takes microseconds."""
    line = "| " + " " * 30_000 + "x |"
    t = time.perf_counter()
    r = audit_documents({"a.md": line, "b.md": line})
    assert time.perf_counter() - t < 1.5
    assert len(too_long(r)) == 2


def test_long_prose_without_any_delimiter_is_not_a_warning():
    prose = "A long paragraph with no table or list separators. " * 200            # ~10,000 characters, no '|' and no ','
    r = audit_documents({"a.md": SUP + "\n" + prose, "b.md": SUP})
    assert r["warnings"] == [] and r["coverage_complete"] is True


def test_long_prose_that_contains_commas_is_a_documented_visible_gap():
    """KNOWN, DOCUMENTED CONSERVATIVE BEHAVIOUR: a delimiter-bearing line over the limit cannot be inspected safely."""
    prose = "Pumps, valves, and fittings are listed in the annex. " * 80            # > MAX_LINE_CHARS, contains commas
    assert len(prose) > core.MAX_LINE_CHARS
    r = audit_documents({"a.md": SUP + "\n" + prose, "b.md": SUP})
    assert len(too_long(r)) == 1 and r["coverage_complete"] is False


# ---------- the new gap reaches every surface
def test_limit_gap_reaches_view_markdown_api_and_mcp():
    over = padded("P-102 | pressure | 10 bar", core.MAX_LINE_CHARS + 1)
    r = audit_documents({"a.md": SUP + "\n" + over, "b.md": SUP})
    v = report.build_view(r)
    item = next(i for i in v.coverage if i.code == "LINE_TOO_LONG")
    assert str(core.MAX_LINE_CHARS) in item.summary and "a.md" in item.location
    md = report.render_markdown(v, "t")
    assert "LINE_TOO_LONG" in md and "incomplete" in md.lower() and len(md) < 6_000
    body = TestClient(api_app).post("/audit", json={"documents": {"a.md": SUP + "\n" + over, "b.md": SUP}}).json()
    assert [w["code"] for w in body["warnings"]] == ["LINE_TOO_LONG"] and body["coverage_complete"] is False
    from app.mcp_server import compare_engineering_rows
    assert compare_engineering_rows(SUP + "\n" + over, SUP) == audit_documents({"spec": SUP + "\n" + over, "bom": SUP})


def test_streamlit_ui_shows_the_gap_not_a_success_banner():
    from streamlit.testing.v1 import AppTest
    over = padded("P-102 | pressure | 10 bar", core.MAX_LINE_CHARS + 1)
    at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", (SUP + "\n" + over).encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", SUP.encode(), "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception and not at.success
    assert at.session_state["audit"]["view"].verdict == report.VERDICT_PARTIAL


# ---------- upload limits
def test_streamlit_upload_limit_is_a_single_central_value_near_two_mib():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg == {"server": {"maxUploadSize": 2}, "browser": {"gatherUsageStats": False}}      # local telemetry opt-out kept


def test_the_application_guard_is_stricter_than_streamlits_server_side_limit():
    assert MAX_FILE_BYTES == 2_000_000
    assert MAX_FILE_BYTES < 2 * 1024 * 1024 == 2_097_152                      # Streamlit's server-side 2 MiB is looser than the exact guard


def test_threshold_paths_in_the_application_guard():
    ok = ingest_upload("big.md", b"a" * 1_999_999)                             # passes the byte guard ...
    assert not any(i["code"] == "FILE_TOO_LARGE" for i in ok.issues)
    assert [i["code"] for i in ok.issues] == ["TEXT_TOO_LONG"]               # ... then meets the 200,000-character text limit
    over = ingest_upload("big.md", b"a" * 2_000_001)
    assert [i["code"] for i in over.issues] == ["FILE_TOO_LARGE"] and over.documents == {}
    exactly = ingest_upload("big.md", b"a" * 2_000_000)
    assert not any(i["code"] == "FILE_TOO_LARGE" for i in exactly.issues)
    # In a browser, Streamlit refuses anything above 2,000,000 bytes before ingest_upload can run (measured; see the
    # docstring), so FILE_TOO_LARGE is reached only by direct uploads between 2,000,001 and 2,097,152 bytes and by the API.
    between = ingest_upload("big.md", b"a" * 2_050_000)
    assert [i["code"] for i in between.issues] == ["FILE_TOO_LARGE"]
