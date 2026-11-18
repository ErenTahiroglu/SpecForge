"""Child-process battery for FR-01 (#45). Run only by tests/test_p1_fr01_unit_syntax.py, with a hard timeout.

It audits rows whose unit text chains exponent operators. On a build without the unit-syntax guard such a row can
keep Pint busy for a very long time, so this file must never be imported into the pytest process.
The pathological unit texts are built from small parts here, never written out as one literal.
Usage: python tests/_fr01_battery.py <output.json>
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import audit_documents  # noqa: E402

CHAIN = "m" + "^9" * 3
CHAIN_STARS = "m" + "**9" * 3
CHAIN_NEG = "m^-9" + "^9" * 2
CHAIN_SHORT = "m" + "^2" * 3

SCENARIOS = {
    "md": {"a.md": f"X-1 | length | 1 {CHAIN}\n", "b.md": "X-1 | length | 1 m\n"},
    "md_swapped": {"a.md": "X-1 | length | 1 m\n", "b.md": f"X-1 | length | 1 {CHAIN}\n"},
    "md_stars": {"a.md": f"X-1 | length | 1 {CHAIN_STARS}\n", "b.md": "X-1 | length | 1 m\n"},
    "md_negative": {"a.md": f"X-1 | length | 1 {CHAIN_NEG}\n", "b.md": "X-1 | length | 1 m\n"},
    "md_short_chain": {"a.md": f"X-1 | length | 1 {CHAIN_SHORT}\n", "b.md": "X-1 | length | 1 m\n"},
    "txt": {"a.txt": f"X-1 | length | 1 {CHAIN}\n", "b.txt": "X-1 | length | 1 m\n"},
    "csv_three_cells": {"a.csv": f"X-1,length,1 {CHAIN}\n", "b.csv": "X-1,length,1 m\n"},
    "csv_four_cells": {"a.csv": f"X-1,length,1,{CHAIN}\n", "b.csv": "X-1,length,1,m\n"},
    "recognizer_table": {"a.md": f"| A-1 | B | {CHAIN} | 1 |\n", "b.md": "x\n"},
    "recognizer_with_valid_row": {"a.md": f"| A-1 | B | {CHAIN} | 1 |\nX-2 | length | 3 m\n", "b.md": "X-2 | length | 3 m\n"},
    "valid_row_after_bad_row": {"a.md": f"X-1 | length | 1 {CHAIN}\nX-2 | length | 3 m\n",
                                "b.md": "X-1 | length | 1 m\nX-2 | length | 3 m\n"},
}


def brief(result):
    return {
        "verdict": result["verdict"], "coverage_complete": result["coverage_complete"],
        "comparisons": result["comparisons"],
        "warnings": [{key: w.get(key) for key in ("code", "reason", "detail", "document", "line", "excerpt")}
                     for w in result["warnings"]],
    }


def main(out_path):
    out = {"core": {}}
    for name, docs in SCENARIOS.items():
        started = time.perf_counter()
        out["core"][name] = {**brief(audit_documents(docs)), "seconds": round(time.perf_counter() - started, 3)}
    out["core_json_finite"] = True
    json.dumps(out["core"], allow_nan=False)

    from fastapi.testclient import TestClient
    from app.api import app as api_app
    client = TestClient(api_app, raise_server_exceptions=False)
    response = client.post("/audit", json={"documents": SCENARIOS["md"]})
    body = response.json()
    out["api"] = {"status": response.status_code, "verdict": body.get("verdict"),
                  "coverage_complete": body.get("coverage_complete")}

    from app.mcp_server import compare_engineering_rows
    mcp = compare_engineering_rows(SCENARIOS["md"]["a.md"], SCENARIOS["md"]["b.md"])
    out["mcp"] = {"verdict": mcp["verdict"], "coverage_complete": mcp["coverage_complete"]}

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(ROOT, "app", "ui.py"), default_timeout=60)
    at.run()
    at.get("file_uploader")[0].upload("a.md", SCENARIOS["md"]["a.md"].encode(), "text/markdown")
    at.get("file_uploader")[1].upload("b.md", SCENARIOS["md"]["b.md"].encode(), "text/markdown")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    audit = at.session_state["audit"]
    exported = json.loads(audit["json"])
    out["ui"] = {"exception": bool(at.exception), "success_banners": len(at.success),
                 "verdict": exported["verdict"], "coverage_complete": exported["coverage_complete"],
                 "markdown_says_incomplete": "Coverage complete: no" in audit["markdown"],
                 "markdown_names_row": "UNSUPPORTED_UNIT" in audit["markdown"]}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, allow_nan=False)


if __name__ == "__main__":
    main(sys.argv[1])
