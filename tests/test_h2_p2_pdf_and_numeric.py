"""H2 P2 regressions: H2-003 (aggregate document limit), H2-004 (numeric policy, finite-only output) and
H2-005 (fail-closed PDF extraction guard).

All PDFs are synthetic and generated here (tests/pdf_factory.py). Nothing is fetched and no hosted site is contacted.
The guard tests prove refusal and bounded memory for the generated inputs. They do not prove a hard CPU, time or memory
guarantee: that needs a worker with enforced resource limits (see the README, Limits)."""
import asyncio
import itertools
import json
import random
import re
import threading
import time
import tracemalloc
import zlib
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.core as core
from app import ingest
from app.api import app as api_app
from app.core import MAX_DOCUMENTS, NumericRangeError, ZERO_TOLERANCE_FLOOR, audit_documents, relative_difference
from app.ingest import (MAX_PDF_CONTENT_BYTES, MAX_PDF_CONTENT_STREAMS_PER_PAGE, MAX_PDF_PAGE_CONTENT_BYTES,
                        MAX_PDF_PAGES, MAX_PDF_TEXT_CHARS, ingest_pair, ingest_upload, pdf_page_count)
from app.report import build_view, render_markdown
from pdf_factory import build_pdf, simple_pdf, text_stream

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui.py"
OK_PAGE = ["P-1 | pressure | 5 bar"]


def codes(result):
    return [issue["code"] for issue in result.issues]


def pdf_pages(n, tag="P"):
    return simple_pdf([[f"{tag}-{i} | pressure | {i} bar"] for i in range(1, n + 1)], compress=True)


def padded(size, text=b"BT /F1 12 Tf 50 700 Td (P-1 | pressure | 5 bar) Tj ET\n"):
    """A content stream of exactly `size` decoded bytes."""
    assert size >= len(text)
    return text + b" " * (size - len(text))


# ------------------------------------------------------------------ H2-003: aggregate document limit

@pytest.mark.parametrize("pages_a,pages_b,accepted", [
    (6, 6, True), (7, 6, False), (7, 7, False), (10, 2, True), (2, 10, True), (10, 3, False), (10, 10, False), (1, 1, True)])
def test_pdf_pair_total_is_checked_before_the_engine(pages_a, pages_b, accepted):
    res_a, res_b = ingest_pair("a.pdf", pdf_pages(pages_a, "A"), "b.pdf", pdf_pages(pages_b, "B"))
    if accepted:
        assert not res_a.issues and not res_b.issues
        docs = {**res_a.documents, **res_b.documents}
        assert len(docs) == pages_a + pages_b <= MAX_DOCUMENTS
        assert audit_documents(docs)["records"] == pages_a + pages_b          # the engine accepts it
    else:
        assert codes(res_a) == ["DOCUMENT_LIMIT_EXCEEDED"] and not res_b.issues
        assert not res_a.documents and not res_b.documents                     # nothing is audited, no partial success
        message = res_a.issues[0]["message"]
        assert f"{MAX_DOCUMENTS} in total" in message and "a.pdf" in message and "b.pdf" in message
        assert "Nothing was audited" in message
        with pytest.raises(ValueError):                                         # the engine would have failed obscurely
            audit_documents({f"d{i}": "x" for i in range(pages_a + pages_b)})


def test_the_per_file_ten_page_rule_is_kept_and_reported_first():
    res_a, res_b = ingest_pair("a.pdf", pdf_pages(11), "b.pdf", pdf_pages(1))
    assert codes(res_a) == ["PDF_TOO_MANY_PAGES"] and not res_a.documents
    assert res_b.documents                                                      # the other file is read on its own terms
    assert MAX_PDF_PAGES == 10


def test_text_files_count_as_one_document_each():
    text = b"P-1 | pressure | 5 bar\n"
    res_a, res_b = ingest_pair("a.pdf", pdf_pages(10), "b.md", text)
    assert not res_a.issues and not res_b.issues and len(res_a.documents) + len(res_b.documents) == 11
    res_a, res_b = ingest_pair("a.md", text, "b.md", text)
    assert len(res_a.documents) + len(res_b.documents) == 2


@pytest.mark.parametrize("name,data", [("b.pdf", b"not a pdf"), ("b.pdf", b""), ("b.pdf", pdf_pages(3)[:150]),
                                       ("b.png", b"x"), ("b.md", b"\xff\xfe")])
def test_an_unreadable_or_unsupported_second_file_never_hides_behind_the_aggregate_check(name, data):
    res_a, res_b = ingest_pair("a.pdf", pdf_pages(10), name, data)
    assert "DOCUMENT_LIMIT_EXCEEDED" not in codes(res_a) + codes(res_b)
    assert res_b.issues and not res_b.documents                                 # the file's own reason is reported


def test_aggregate_check_counts_declared_pages_so_blank_pages_do_not_open_a_loophole():
    blank = build_pdf([[text_stream(OK_PAGE)]] * 3 + [[b""]] * 4)               # 7 pages, 4 without text
    res_a, res_b = ingest_pair("a.pdf", blank, "b.pdf", pdf_pages(6))
    assert codes(res_a) == ["DOCUMENT_LIMIT_EXCEEDED"] and not res_a.documents


def test_document_names_keep_the_original_page_evidence():
    res_a, res_b = ingest_pair("a.pdf", pdf_pages(6, "A"), "b.pdf", pdf_pages(6, "B"))
    assert sorted(res_a.documents) == [f"a.pdf:page-{i}" for i in range(1, 7)]
    assert "A-3 | pressure | 3 bar" in res_a.documents["a.pdf:page-3"]
    assert sorted(res_b.documents)[0] == "b.pdf:page-1"


def test_api_and_engine_keep_their_twelve_document_limit():
    client = TestClient(api_app)
    docs = {f"d{i}": "P-1 | pressure | 5 bar" for i in range(13)}
    assert client.post("/audit", json={"documents": docs}).status_code == 422
    with pytest.raises(ValueError, match="1..12"):
        audit_documents(docs)


# ------------------------------------------------------------------ H2-005: PDF extraction guard

def test_ordinary_small_text_pdfs_still_work():
    for compress in (False, True):
        res = ingest_upload("plan.pdf", simple_pdf([["P-1 | pressure | 5 bar", "P-2 | flow | 7 L/min"], ["P-3 | temp | 20 degC"]], compress))
        assert not res.issues and res.pages == 2
        assert res.documents["plan.pdf:page-1"].splitlines() == ["P-1 | pressure | 5 bar", "P-2 | flow | 7 L/min"]
        assert res.documents["plan.pdf:page-2"].strip() == "P-3 | temp | 20 degC"


@pytest.mark.parametrize("pages,expected", [(10, []), (11, ["PDF_TOO_MANY_PAGES"])])
def test_page_limit_boundary(pages, expected):
    res = ingest_upload("p.pdf", pdf_pages(pages))
    assert codes(res) == expected
    assert bool(res.documents) == (not expected)


def test_page_count_comes_from_the_page_tree_not_from_the_count_entry():
    data = build_pdf([[text_stream(OK_PAGE)]] * 11).replace(b"/Count 11", b"/Count 2")
    assert pdf_page_count(data) == 11
    assert codes(ingest_upload("p.pdf", data)) == ["PDF_TOO_MANY_PAGES"]


@pytest.mark.parametrize("kids", [None, 10])
def test_page_tree_flood_is_refused_before_pages_are_materialised(kids):
    data = build_pdf([[b"BT (a) Tj ET"]] * 3000, kids_per_node=kids)
    res = ingest_upload("p.pdf", data)
    assert codes(res) == ["PDF_LIMIT_EXCEEDED"] and not res.documents
    assert pdf_page_count(data) is None


def test_page_content_boundary_is_exact():
    ok = ingest_upload("p.pdf", build_pdf([[padded(MAX_PDF_PAGE_CONTENT_BYTES)]], compress=True))
    assert not ok.issues and "p.pdf:page-1" in ok.documents
    over = ingest_upload("p.pdf", build_pdf([[padded(MAX_PDF_PAGE_CONTENT_BYTES + 1)]], compress=True))
    assert codes(over) == ["PDF_LIMIT_EXCEEDED"] and not over.documents


def test_cumulative_content_boundary_is_exact_across_pages():
    third = MAX_PDF_CONTENT_BYTES // 3                                           # 333_333 per page
    ok = ingest_upload("p.pdf", build_pdf([[padded(third)]] * 3, compress=True))
    assert not ok.issues and len(ok.documents) == 3
    over = ingest_upload("p.pdf", build_pdf([[padded(third + 1)]] * 3, compress=True))   # 1_000_002 bytes, each page below its own limit
    assert codes(over) == ["PDF_LIMIT_EXCEEDED"] and not over.documents
    assert "in total" in over.issues[0]["message"]


def test_many_streams_on_one_page_cannot_get_around_the_per_stream_limit():
    ok = ingest_upload("p.pdf", build_pdf([[text_stream(OK_PAGE)] * MAX_PDF_CONTENT_STREAMS_PER_PAGE]))
    assert not [c for c in codes(ok) if c.startswith("PDF_LIMIT")]
    over = ingest_upload("p.pdf", build_pdf([[text_stream(OK_PAGE)] * (MAX_PDF_CONTENT_STREAMS_PER_PAGE + 1)]))
    assert codes(over) == ["PDF_LIMIT_EXCEEDED"] and not over.documents
    split = ingest_upload("p.pdf", build_pdf([[padded(MAX_PDF_PAGE_CONTENT_BYTES // 2 + 1000)] * 2], compress=True))
    assert codes(split) == ["PDF_LIMIT_EXCEEDED"]                                # two streams, each fine, together too large


def test_cumulative_extracted_text_limit():
    def page(chars):
        return [b"BT /F1 12 Tf 50 700 Td (" + b"a" * chars + b") Tj ET"]
    ok = ingest_upload("p.pdf", build_pdf([page(MAX_PDF_TEXT_CHARS // 3 - 100)] * 3, compress=True))
    assert not ok.issues and len(ok.documents) == 3
    over = ingest_upload("p.pdf", build_pdf([page(MAX_PDF_TEXT_CHARS // 3 + 100)] * 3, compress=True))
    assert "PDF_LIMIT_EXCEEDED" in codes(over) and not over.documents


@pytest.mark.parametrize("megabytes", [30, 100])
def test_decompression_bomb_is_refused_with_bounded_memory(megabytes):
    data = build_pdf([[b" " * (megabytes * 1_000_000)]], compress=True)
    assert len(data) < 200_000                                                   # a small file for a huge page
    tracemalloc.start()
    res = ingest_upload("bomb.pdf", data)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert codes(res) == ["PDF_LIMIT_EXCEEDED"] and not res.documents
    assert peak < 10_000_000                                                     # nowhere near the decoded size


def test_control_without_the_guard_the_same_bomb_is_decoded_in_full():
    """Negative control: pypdf with its defaults decodes the 30 MB page, so the guard above is what refuses it."""
    import io
    from pypdf import PdfReader
    data = build_pdf([[b" " * 30_000_000]], compress=True)
    tracemalloc.start()
    PdfReader(io.BytesIO(data)).pages[0].extract_text()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak > 25_000_000


# One page with one content stream: font = object 3, stream = 4, page = 5, so the form is object 6 and a nested form is 7.
def _form_pdf(form_body, invocations, compress=False, nested=None):
    payload = zlib.compress(form_body) if compress else form_body
    flt = b" /Filter /FlateDecode" if compress else b""
    resources = b"<< /Font << /F1 3 0 R >> " + (b"/XObject << /Y 7 0 R >> " if nested else b"") + b">>"
    form = (b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources " + resources + flt +
            b" /Length %d >>\nstream\n" % len(payload)) + payload + b"\nendstream"
    extra = [form] + ([nested] if nested else [])
    data = build_pdf([[b"/X Do\n" * invocations]], extra_objects=extra)
    return data.replace(b"/Font << /F1 3 0 R >> >>", b"/Font << /F1 3 0 R >> /XObject << /X 6 0 R >> >>", 1)


def test_form_object_amplification_is_refused_before_extraction():
    body = b"BT /F1 12 Tf 50 700 Td (P-1 | pressure | 5 bar) Tj ET\n" * (380_000 // 56)
    started = time.monotonic()
    res = ingest_upload("p.pdf", _form_pdf(body, invocations=50, compress=True))
    assert codes(res) == ["PDF_LIMIT_EXCEEDED"] and not res.documents
    assert time.monotonic() - started < 5                                        # refused up front, without 50 decodes


def test_small_form_object_is_still_read_and_over_invocation_is_refused():
    body = b"BT /F1 12 Tf 50 700 Td (P-9 | pressure | 9 bar) Tj ET"
    ok = ingest_upload("p.pdf", _form_pdf(body, invocations=1))
    assert not ok.issues and "P-9 | pressure | 9 bar" in ok.documents["p.pdf:page-1"]
    edge = ingest_upload("p.pdf", _form_pdf(body, invocations=ingest.MAX_PDF_FORM_INVOCATIONS))
    assert not edge.issues and edge.documents["p.pdf:page-1"].count("P-9") == ingest.MAX_PDF_FORM_INVOCATIONS
    over = ingest_upload("p.pdf", _form_pdf(body, invocations=ingest.MAX_PDF_FORM_INVOCATIONS + 1))
    assert codes(over) == ["PDF_LIMIT_EXCEEDED"] and not over.documents          # pypdf alone would skip the extra forms silently


@pytest.mark.parametrize("data", [
    b"", b"%PDF-1.4\n", b"%PDF-1.4\n" + b"\x00" * 1000, b"just text", pdf_pages(2)[:200], pdf_pages(2)[:-120],
    pdf_pages(2).replace(b"/Kids", b"/Kidz"), pdf_pages(2).replace(b"endstream", b"endstrea"), b"%PDF-1.7\n1 0 obj<<>>endobj" * 50])
def test_malformed_pdfs_fail_closed_without_traceback(data):
    res = ingest_upload("m.pdf", data)
    assert not res.documents or all(isinstance(v, str) for v in res.documents.values())
    text = json.dumps(res.issues)
    assert "Traceback" not in text and 'File "' not in text and "/Users/" not in text and "site-packages" not in text
    if not res.documents:
        assert res.issues                                                        # a refusal always carries a reason


def test_a_refused_pdf_never_leaves_partial_documents():
    good = text_stream(OK_PAGE)
    bomb = b" " * (MAX_PDF_PAGE_CONTENT_BYTES + 1)
    res = ingest_upload("m.pdf", build_pdf([[good], [good], [bomb]], compress=True))
    assert not res.documents and codes(res) == ["PDF_LIMIT_EXCEEDED"]


def test_encrypted_pdf_is_refused():
    from pypdf import PdfReader, PdfWriter
    import io
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_pages(1))))
    try:
        writer.encrypt("secret", algorithm="RC4-128")
    except Exception as exc:                                                     # pragma: no cover - missing crypto backend
        pytest.skip(f"cannot build an encrypted PDF here: {type(exc).__name__}")
    out = io.BytesIO()
    writer.write(out)
    res = ingest_upload("e.pdf", out.getvalue())
    assert not res.documents and codes(res) in (["PDF_ENCRYPTED"], ["PDF_UNREADABLE"])


def test_limits_are_per_context_and_never_change_the_process_default():
    from pypdf import get_configuration
    default = get_configuration()
    ingest_upload("p.pdf", pdf_pages(2))
    ingest_upload("b.pdf", build_pdf([[b" " * 5_000_000]], compress=True))
    assert get_configuration() == default and default.zlib_maximum_output_length == 75_000_000
    inside, outside = {}, {}
    entered, leave = threading.Event(), threading.Event()

    def holder():
        from pypdf import apply_configuration
        with apply_configuration(**ingest._pdf_limits()):
            inside["limit"] = get_configuration().zlib_maximum_output_length
            entered.set()
            leave.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(10)
    outside["limit"] = get_configuration().zlib_maximum_output_length           # another thread, while the guard is active
    leave.set()
    thread.join()
    assert inside["limit"] == MAX_PDF_PAGE_CONTENT_BYTES and outside["limit"] == 75_000_000


def test_concurrent_sessions_get_independent_results():
    ordinary, bomb = pdf_pages(3), build_pdf([[b" " * 20_000_000]], compress=True)
    outcomes, errors = [], []

    def work(index):
        try:
            for _ in range(5):
                data = bomb if index % 2 else ordinary
                res = ingest_upload("c.pdf", data)
                outcomes.append((index % 2, tuple(codes(res)), len(res.documents)))
        except Exception as exc:                                                 # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    assert not errors and len(outcomes) == 40
    assert {o for o in outcomes if o[0] == 1} == {(1, ("PDF_LIMIT_EXCEEDED",), 0)}
    assert {o for o in outcomes if o[0] == 0} == {(0, (), 3)}


# ------------------------------------------------------------------ H2-004: numeric policy and finite-only output

EXTREME = [("1.7" + "0" * 307, "-1.7" + "0" * 307), ("1" + "0" * 300, "1"), ("0", "0"), ("0", "1" + "0" * 300),
           ("0." + "0" * 300 + "1", "0.0000001"), ("1" + "0" * 307, "1" + "0" * 307), ("0." + "0" * 307 + "22251", "1")]


@pytest.mark.parametrize("a,b", [(1.7e308, -1.7e308), (1e308, 1e308), (5e-324, 1e-323), (2.2250738585072014e-308, -2.2250738585072014e-308),
                                 (0.0, 0.0), (0.0, 1e-300), (-0.0, 0.0), (1e-300, 1e300), (1.7976931348623157e308, 1.0),
                                 (-3.0, -3.0), (123456789.123, 123456789.124)])
def test_relative_difference_is_finite_bounded_and_symmetric(a, b):
    d = relative_difference(a, b)
    assert 0.0 <= d <= 2.0 and d == relative_difference(b, a)
    if a == b:
        assert d == 0.0


def test_relative_difference_known_values():
    assert relative_difference(1.7e308, -1.7e308) == 2.0                        # a - b would overflow to inf
    assert relative_difference(0.0, 5.0) == 1.0 and relative_difference(0.0, 0.0) == 0.0
    assert relative_difference(1.0, 1.5) == pytest.approx(1 / 3)
    assert relative_difference(1e-300, -1e-300) == 2.0
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(NumericRangeError):
            relative_difference(bad, 1.0)


def test_relative_difference_matches_exact_arithmetic_on_random_pairs():
    from fractions import Fraction
    rng = random.Random(20261118)
    for _ in range(2000):
        a, b = (rng.choice([-1, 1]) * rng.uniform(0.1, 9.9) * 10.0 ** rng.randint(-300, 300) for _ in range(2))
        exact = abs(Fraction(a) - Fraction(b)) / max(abs(Fraction(a)), abs(Fraction(b)))
        assert relative_difference(a, b) == pytest.approx(float(exact), rel=1e-12)


def _compare(a, b, tolerance=0.0, unit="m"):
    return audit_documents({"a.md": f"P-1 | length | {a} {unit}", "b.md": f"P-1 | length | {b} {unit}"}, tolerance)


def test_the_reported_defect_equivalent_flow_units_match_at_zero_tolerance():
    r = audit_documents({"a.md": "P-1 | flow | 12 L/min", "b.md": "P-1 | flow | 0.0002 m^3/s"}, 0.0)
    assert r["status"] == "NO_DIFFERENCES_FOUND" and not r["findings"] and len(r["matches"]) == 1
    assert r["tolerance"] == 0.0                                                 # the user's setting is reported unchanged


# Exactly equal quantities written in different units or prefixes. Each pair is exact in decimal arithmetic.
EQUIVALENT = [("12 L/min", "0.0002 m^3/s"), ("36 m^3/h", "0.01 m^3/s"), ("1 bar", "100 kPa"), ("1 bar", "100000 Pa"),
              ("10 bar", "1 MPa"), ("1 inch", "25.4 mm"), ("1 ft", "0.3048 m"), ("1 lb", "0.45359237 kg"),
              ("212 degF", "100 degC"), ("32 degF", "0 degC"), ("1 kWh", "3.6 MJ"), ("36 km/h", "10 m/s"),
              ("1 mbar", "100 Pa"), ("3 min", "180 s"), ("2 h", "7200 s"), ("1 L", "0.001 m^3"), ("1 mL", "1 cm^3"),
              ("0.5 MW", "500 kW"), ("-40 degF", "-40 degC"), ("1 kgf", "9.80665 N")]


@pytest.mark.parametrize("a,b", EQUIVALENT)
def test_equivalent_converted_values_are_equal_at_zero_tolerance(a, b):
    for x, y in ((a, b), (b, a)):
        r = audit_documents({"a.md": f"P-1 | v | {x}", "b.md": f"P-1 | v | {y}"}, 0.0)
        assert not r["findings"] and r["comparisons"] == 1, (x, y, r["findings"])


def test_rounding_rationale_measured_error_is_far_below_the_floor():
    """The floor is justified by measurement: the worst relative error over exact-equivalent conversions is tiny."""
    rng = random.Random(7)
    worst = 0.0
    scales = [("mm", "m", Decimal("0.001")), ("kPa", "Pa", Decimal(1000)), ("bar", "kPa", Decimal(100)), ("MPa", "bar", Decimal(10)),
              ("cm", "m", Decimal("0.01")), ("km", "m", Decimal(1000)), ("mbar", "Pa", Decimal(100)), ("MJ", "kJ", Decimal(1000)),
              ("kW", "W", Decimal(1000)), ("min", "s", Decimal(60)), ("h", "min", Decimal(60)), ("ms", "s", Decimal("0.001"))]
    for _ in range(300):
        for unit_a, unit_b, factor in scales:
            digits = Decimal(rng.randint(1, 10 ** 9)) / Decimal(10 ** rng.randint(0, 6))
            r = audit_documents({"a.md": f"P-1 | v | {digits} {unit_a}", "b.md": f"P-1 | v | {digits * factor:f} {unit_b}"}, 0.0)
            worst = max(worst, relative_difference(r["matches"][0]["reference"]["si_value"], r["matches"][0]["other"]["si_value"]))
    assert worst < 1e-14
    assert ZERO_TOLERANCE_FLOOR >= 100 * worst


@pytest.mark.parametrize("a,b,flagged", [("1", "1.000000001", True), ("1", "1.00000000001", True), ("1", "1.0000000000001", False),
                                         ("100", "100.0000001", True), ("5", "5", False), ("12 L/min", "12.0000001 L/min", True)])
def test_zero_tolerance_still_flags_meaningful_differences(a, b, flagged):
    unit_a = a.split(" ", 1)[1] if " " in a else "m"
    unit_b = b.split(" ", 1)[1] if " " in b else "m"
    r = audit_documents({"a.md": f"P-1 | v | {a.split()[0]} {unit_a}", "b.md": f"P-1 | v | {b.split()[0]} {unit_b}"}, 0.0)
    assert bool(r["findings"]) == flagged
    for finding in r["findings"]:
        assert finding["relative_difference"] > 0.0                              # a flagged difference is never shown as 0.0


def test_positive_tolerance_behaviour_is_unchanged():
    assert _compare("100", "100.5", 0.01)["findings"] == []
    assert _compare("100", "102", 0.01)["findings"][0]["relative_difference"] == pytest.approx(0.0196078431)
    assert _compare("100", "102", 0.02)["findings"] == []


def test_extreme_finite_values_compare_without_overflow():
    r = _compare("1.7" + "0" * 307, "-1.7" + "0" * 307)
    assert [f["relative_difference"] for f in r["findings"]] == [2.0] and r["status"] == "DIFFERENCES"
    r = _compare("1.7" + "0" * 307, "1.7" + "0" * 307)
    assert r["status"] == "NO_DIFFERENCES_FOUND"
    r = _compare("0", "1" + "0" * 300)
    assert r["findings"][0]["relative_difference"] == 1.0
    r = _compare("0", "0")
    assert r["status"] == "NO_DIFFERENCES_FOUND" and r["comparisons"] == 1


@pytest.mark.parametrize("a,b,unit", [("0." + "0" * 330 + "1", "0", "m"), ("0." + "0" * 320 + "1", "0." + "0" * 320 + "2", "m"),
                                      ("0." + "0" * 320 + "1", "0", "nm"), ("1" + "0" * 307, "1", "km"), ("1" + "0" * 400, "1", "m")])
def test_unrepresentable_values_become_a_visible_gap_never_a_clean_result(a, b, unit):
    r = _compare(a, b, 0.0, unit)
    unusable = [w for w in r["warnings"] if w["code"] == "UNSUPPORTED_UNIT"]
    assert unusable and all(w["reason"] in ("NumericRangeError", "ValueError", "OverflowError") for w in unusable)
    assert r["verdict"] != "NO_CONFLICTS_IN_COMPARED_ROWS" and r["coverage_complete"] is False
    assert r["status"] != "NO_DIFFERENCES_FOUND" or r["comparisons"] == 0
    json.dumps(r, allow_nan=False)


def test_a_legitimate_zero_from_an_offset_unit_is_not_mistaken_for_underflow():
    r = audit_documents({"a.md": "P-1 | temperature | -273.15 degC", "b.md": "P-1 | temperature | 0 K"}, 0.0)
    assert r["comparisons"] == 1 and not r["findings"] and not r["warnings"]


def test_same_source_contradiction_uses_the_same_arithmetic():
    r = audit_documents({"a.md": "P-1 | length | 1.7" + "0" * 307 + " m\nP-1 | length | -1.7" + "0" * 307 + " m",
                         "b.md": "P-1 | length | 5 m"}, 0.0)
    assert any(f["code"] == "INTERNAL_CONTRADICTION" and f["relative_difference"] == 2.0 for f in r["findings"])
    json.dumps(r, allow_nan=False)


def _strict_loads(text):
    def refuse(token):
        raise AssertionError(f"non-standard JSON constant {token}")
    return json.loads(text, parse_constant=refuse)


def test_http_extreme_values_give_standard_json():
    client = TestClient(api_app)
    big = "1.7" + "0" * 307
    for a, b, tol in ((big, "-" + big, 0.0), ("0", big, 0.0), ("12 L/min", "0.0002 m^3/s", 0.0)):
        unit = "" if " " in a else " m"
        r = client.post("/audit", json={"documents": {"a.md": f"P-1 | v | {a}{unit}", "b.md": f"P-1 | v | {b}{'' if ' ' in b else ' m'}"},
                                        "tolerance": tol})
        assert r.status_code == 200
        _strict_loads(r.text)
    assert not re.search(r"NaN|Infinity", r.text)


@pytest.mark.parametrize("tolerance", [-0.1, 0.21])
def test_http_tolerance_bounds_still_return_422(tolerance):
    r = TestClient(api_app).post("/audit", json={"documents": {"a.md": "P-1 | v | 1 m"}, "tolerance": tolerance})
    assert r.status_code == 422


def test_http_validation_errors_keep_the_default_body_shape():
    r = TestClient(api_app).post("/audit", json={"documents": {"a.md": "x"}, "tolerance": 0.5})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "tolerance"] and "input" in r.json()["detail"][0]


def test_http_nan_tolerance_is_rejected_not_500():
    r = TestClient(api_app).post("/audit", content='{"documents": {"a.md": "P-1 | v | 1 m"}, "tolerance": NaN}',
                                 headers={"content-type": "application/json"})
    assert r.status_code == 422
    _strict_loads(r.text)


def test_non_finite_result_is_a_controlled_error_not_a_500_or_nan_output(monkeypatch):
    monkeypatch.setattr(core, "relative_difference", lambda a, b: float("nan"))
    with pytest.raises(NumericRangeError, match="NUMERIC_RANGE"):
        core.audit_documents({"a.md": "P-1 | v | 1 m", "b.md": "P-1 | v | 2 m"}, 0.0)
    r = TestClient(api_app).post("/audit", json={"documents": {"a.md": "P-1 | v | 1 m", "b.md": "P-1 | v | 2 m"}, "tolerance": 0.0})
    assert r.status_code == 422 and "NUMERIC_RANGE" in r.json()["detail"] and "NaN" not in r.text


def test_mcp_tool_extremes_and_controlled_failure(monkeypatch):
    from app import mcp_server
    big = "1.7" + "0" * 307
    out = mcp_server.compare_engineering_rows(f"P-1 | v | {big} m", f"P-1 | v | -{big} m", 0.0)
    json.dumps(out, allow_nan=False)
    assert out["findings"][0]["relative_difference"] == 2.0
    same = mcp_server.compare_engineering_rows("P-1 | flow | 12 L/min", "P-1 | flow | 0.0002 m^3/s", 0.0)
    assert same["status"] == "NO_DIFFERENCES_FOUND"
    result = asyncio.run(mcp_server.mcp.call_tool("compare_engineering_rows", {"document_a": f"P-1 | v | {big} m",
                                                                              "document_b": f"P-1 | v | -{big} m", "tolerance": 0.0}))
    assert not re.search(r"NaN|Infinity", repr(result))
    monkeypatch.setattr(core, "relative_difference", lambda a, b: float("inf"))
    with pytest.raises(Exception) as caught:
        asyncio.run(mcp_server.mcp.call_tool("compare_engineering_rows", {"document_a": "P-1 | v | 1 m", "document_b": "P-1 | v | 2 m"}))
    assert "NUMERIC_RANGE" in str(caught.value) or "NumericRangeError" in str(caught.value)


def test_reports_have_no_nan_or_infinity_for_extreme_inputs():
    big = "1.7" + "0" * 307
    for a, b in ((big, "-" + big), ("0", big), ("0." + "0" * 330 + "1", "0")):
        r = _compare(a, b)
        md = render_markdown(build_view(r, {"a.md": "Document A", "b.md": "Document B"}, [], ["Document A: a.md", "Document B: b.md"]), "2026-11-18")
        assert not re.search(r"\b(nan|inf|infinity)\b", md, re.IGNORECASE)
        json.dumps(r, allow_nan=False)


# ------------------------------------------------------------------ UI (Streamlit AppTest)

def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(UI), default_timeout=90)


def _audit(name_a, data_a, name_b, data_b):
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload(name_a, data_a, "application/octet-stream")
    at.get("file_uploader")[1].upload(name_b, data_b, "application/octet-stream")
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    return at


def _page_text(at):
    parts = [e.value for e in itertools.chain(at.markdown, at.warning, at.error, at.info, at.success)]
    return "\n".join(str(p) for p in parts)


@pytest.mark.parametrize("pages_a,pages_b", [(7, 6), (7, 7), (10, 3)])
def test_ui_rejects_an_oversized_pdf_pair_with_a_clear_message(pages_a, pages_b, monkeypatch):
    monkeypatch.setenv("SPECFORGE_ENABLE_LOCAL_PDF_UI", "1")                    # local opt-in: PDF is off by default
    at = _audit("a.pdf", pdf_pages(pages_a, "A"), "b.pdf", pdf_pages(pages_b, "B"))
    audit = at.session_state["audit"]
    assert audit["blocked"] is True
    assert [i["code"] for i in audit["issues"]] == ["DOCUMENT_LIMIT_EXCEEDED"]
    shown = _page_text(at)
    assert "Nothing was audited" in shown and "AUDIT_ERROR" not in shown and "submit 1..12" not in shown
    assert "The audit did not run" in shown


@pytest.mark.parametrize("pages_a,pages_b", [(6, 6), (10, 2)])
def test_ui_accepts_a_pair_within_the_aggregate_limit(pages_a, pages_b, monkeypatch):
    monkeypatch.setenv("SPECFORGE_ENABLE_LOCAL_PDF_UI", "1")
    at = _audit("a.pdf", pdf_pages(pages_a, "A"), "b.pdf", pdf_pages(pages_b, "B"))
    audit = at.session_state["audit"]
    assert audit["blocked"] is False and audit["result"]["records"] == pages_a + pages_b
    _strict_loads(audit["json"])


def test_ui_refuses_a_hostile_pdf_without_a_traceback(monkeypatch):
    monkeypatch.setenv("SPECFORGE_ENABLE_LOCAL_PDF_UI", "1")
    bomb = build_pdf([[b" " * 50_000_000]], compress=True)
    at = _audit("bomb.pdf", bomb, "b.md", b"P-1 | pressure | 5 bar\n")
    audit = at.session_state["audit"]
    assert audit["blocked"] is True and [i["code"] for i in audit["issues"]] == ["PDF_LIMIT_EXCEEDED"]
    shown = _page_text(at)
    assert "Traceback" not in shown and "LimitReachedError" not in shown and "Not audited" in shown


def test_ui_extreme_values_export_standard_json_and_clean_markdown():
    big = "1.7" + "0" * 307
    at = _audit("a.md", f"P-1 | length | {big} m\n".encode(), "b.md", f"P-1 | length | -{big} m\n".encode())
    audit = at.session_state["audit"]
    assert audit["blocked"] is False
    data = _strict_loads(audit["json"])
    assert data["result"]["findings"][0]["relative_difference"] == 2.0
    assert not re.search(r"NaN|Infinity|\binf\b", audit["markdown"] + audit["json"])


def test_ui_zero_tolerance_equivalent_units_show_no_difference():
    at = _app()
    at.run()
    at.get("file_uploader")[0].upload("a.md", b"P-1 | flow | 12 L/min\n", "text/markdown")
    at.get("file_uploader")[1].upload("b.md", b"P-1 | flow | 0.0002 m^3/s\n", "text/markdown")
    at.run()
    at.slider(key="tol").set_value(0.0)
    at.run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception
    result = at.session_state["audit"]["result"]
    assert result["tolerance"] == 0.0 and result["status"] == "NO_DIFFERENCES_FOUND"


def test_ui_a_non_finite_result_becomes_a_controlled_error(monkeypatch):
    def broken(documents, tolerance=0.01):
        return {"status": "NO_DIFFERENCES_FOUND", "records": float("nan")}
    monkeypatch.setattr(core, "audit_documents", broken)
    at = _audit("a.md", b"P-1 | v | 1 m\n", "b.md", b"P-1 | v | 1 m\n")
    audit = at.session_state["audit"]
    assert audit["blocked"] is True and audit["issues"][0]["code"] == "AUDIT_ERROR"
    assert "NaN" not in _page_text(at)
