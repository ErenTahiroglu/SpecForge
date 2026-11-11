"""Issue #11 (P1-C): a project listing is accepted only when its completeness is positively established,
against the schema observed from codebase-memory-mcp 0.11.0:
        {"projects": [...], "total": int, "offset": int, "limit": int, "returned": int, "has_more": bool,
         ["next_offset": int  -- present only while has_more is true]}
    A page past the end is {"projects": [], "returned": 0, "has_more": false}.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

spec = importlib.util.spec_from_file_location("checkpoint_state", Path(__file__).parents[1] / "scripts/checkpoint_state.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


# ---------------------------------------------------------------- workspace
@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "container"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "app" / "core.py").write_text("x = 1\n")
    priv = root / "private-a"
    priv.mkdir()
    (root / "sibling").mkdir()
    ((root / "sibling") / ".git").mkdir()
    return SimpleNamespace(root=root, repo=repo, priv=priv)


def row(path, name="p"):
    return {"name": name, "root_path": str(path)}


# ================================================================ (A) enumeration






















# ================================================================ (B) project listing contract
def good_page(projects, total, offset=0, limit=100, more=False, nxt=None):
    body = {"projects": projects, "total": total, "offset": offset, "limit": limit, "returned": len(projects), "has_more": more}
    if more:
        body["next_offset"] = offset + len(projects) if nxt is None else nxt
    return body


def proj(i):
    return {"name": f"n{i}", "root_path": f"/x/{i}"}


def listing(pages):
    """Patch subprocess.run so successive calls return the given bodies (dict/str) as a successful CLI."""
    it = iter(pages)

    def fake(cmd, **kw):
        body = next(it)
        out = body if isinstance(body, str) else json.dumps(body)
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return mock.patch("subprocess.run", side_effect=fake)


def run_list(pages):
    with listing(pages):
        return cs._list_projects()


def reject(pages):
    with listing(pages):
        with pytest.raises(RuntimeError):
            cs._list_projects()


def test_real_schema_single_complete_page_and_genuine_empty_index():
    assert run_list([good_page([proj(i) for i in range(3)], 3)]) == [proj(i) for i in range(3)]
    assert run_list([good_page([], 0)]) == []                                       # genuine empty index: total 0, returned 0


def test_real_schema_multi_page_listing():
    pages = [good_page([proj(0), proj(1)], 5, 0, 2, True), good_page([proj(2), proj(3)], 5, 2, 2, True),
             good_page([proj(4)], 5, 4, 2, False)]
    assert run_list(pages) == [proj(i) for i in range(5)]


def test_real_cli_responses_recorded_from_0_11_0_are_accepted():
    """Shapes copied from the installed CLI (paths replaced): first/middle/last page and an empty past-the-end page."""
    pages = [good_page([proj(i) for i in range(5)], 13, 0, 5, True), good_page([proj(i) for i in range(5, 10)], 13, 5, 5, True),
             good_page([proj(i) for i in range(10, 13)], 13, 10, 5, False)]
    assert len(run_list(pages)) == 13


@pytest.mark.parametrize("body", [
    {"projects": []},                                                              # report-32 reproducer 1
    {"projects": [], "has_more": False, "total": "999"},                           # report-32 reproducer 2
    {"projects": [], "total": 0, "returned": 0},                                   # has_more missing
    {"projects": [], "has_more": False, "returned": 0},                            # total missing
    {"projects": [], "has_more": False, "total": 0},                               # returned missing
    {"projects": [], "total": None, "returned": 0, "has_more": False},
    {"projects": [], "total": 0, "returned": None, "has_more": False},
    {"projects": [], "total": 0, "returned": 0, "has_more": None},
    {"projects": [], "total": 0, "returned": 0, "has_more": "false"},              # string, not boolean
    {"projects": [], "total": 0, "returned": 0, "has_more": 0},                    # int, not boolean
    {"projects": [], "total": False, "returned": 0, "has_more": False},            # bool is an int subclass
    {"projects": [], "total": 0, "returned": False, "has_more": False},
    {"projects": [], "total": 0.0, "returned": 0, "has_more": False},              # float
    {"projects": [], "total": "0", "returned": 0, "has_more": False},
    {"projects": [], "total": -1, "returned": 0, "has_more": False},
    {"projects": [], "total": 0, "returned": -1, "has_more": False},
    {"projects": [proj(1)], "total": 1, "returned": 2, "has_more": False},         # returned != len(projects)
    {"projects": [proj(1)], "total": 1, "returned": True, "has_more": False},      # True == 1 must not pass
    {"projects": None, "total": 0, "returned": 0, "has_more": False},
    {"projects": {}, "total": 0, "returned": 0, "has_more": False},
    {"projects": [proj(1)], "total": 5, "returned": 1, "has_more": False},         # last page claims 5 but only 1 seen
    {"projects": [proj(1), proj(2)], "total": 1, "returned": 2, "has_more": False},  # more rows than total
    {"projects": [], "total": 3, "returned": 0, "has_more": False},                # empty page but total says 3
    {"projects": [], "total": 0, "returned": 0, "has_more": True, "next_offset": 0},   # empty page cannot have more
    {"total": 0, "returned": 0, "has_more": False},                                # projects missing
    [], "[]", "null", "", "   ", "boom", "{", '{"projects": [', '{"projects": [], "total": 0, "returned": 0, "has_more": false',
])
def test_malformed_or_incomplete_metadata_is_rejected(body):
    reject([body])


@pytest.mark.parametrize("second", [
    good_page([proj(2)], 3, 2, 2, True, nxt=2),                    # nxt does not advance beyond offset
    good_page([proj(2)], 3, 2, 2, True, nxt=1),                    # goes backwards
    good_page([proj(2)], 3, 2, 2, True, nxt=True),                 # bool next_offset
    good_page([proj(2)], 3, 2, 2, True, nxt="3"),                  # string next_offset
])
def test_non_advancing_or_invalid_next_offset_is_rejected(second):
    reject([good_page([proj(0), proj(1)], 3, 0, 2, True), second])


def test_has_more_without_next_offset_is_rejected():
    body = good_page([proj(0)], 3, 0, 1, True)
    del body["next_offset"]
    reject([body])


def test_next_offset_not_equal_to_offset_plus_returned_is_rejected():
    reject([good_page([proj(0)], 3, 0, 1, True, nxt=2)])            # would skip a project


def test_offset_echo_must_match_the_requested_offset():
    reject([good_page([proj(0), proj(1)], 4, 0, 2, True), good_page([proj(2), proj(3)], 4, 0, 2, False)])


def test_total_changing_between_pages_is_rejected():
    reject([good_page([proj(0), proj(1)], 4, 0, 2, True), good_page([proj(2), proj(3)], 5, 2, 2, False)])


def test_last_page_that_ends_short_of_total_is_rejected():
    reject([good_page([proj(0), proj(1)], 5, 0, 2, True), good_page([proj(2)], 5, 2, 2, False)])


def test_later_page_that_is_malformed_is_rejected_and_not_truncated_to_the_good_part():
    reject([good_page([proj(0), proj(1)], 4, 0, 2, True), {"projects": [proj(2)]}])


def test_nonzero_exit_and_runtime_errors_are_rejected():
    with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 3, "", "err")):
        with pytest.raises(RuntimeError):
            cs._list_projects()
    with mock.patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError):
            cs._list_projects()
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
        with pytest.raises(RuntimeError):
            cs._list_projects()


def test_project_entries_must_be_objects():
    reject([good_page(["not-an-object"], 1)])                       # a list of strings is not a project listing


# ================================================================ end to end: the CLI and the baseline
def fake_cbm(tmp_path, bodies):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    data = tmp_path / "bodies.json"
    data.write_text(json.dumps(bodies))
    b = d / "codebase-memory-mcp"
    b.write_text("#!%s\nimport json, sys\nidx = int(open(%r + '.n').read()) if __import__('os').path.exists(%r + '.n') else 0\n"
                 "open(%r + '.n', 'w').write(str(idx + 1))\nb = json.load(open(%r))[idx]\nprint(b if isinstance(b, str) else json.dumps(b))\n"
                 % (sys.executable, str(data), str(data), str(data), str(data)))
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    return d


def run_cli(tmp_path, ws, bodies):
    env = dict(os.environ, PATH=f"{fake_cbm(tmp_path, bodies)}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, str(Path(cs.__file__)), "verify-scope", "--project-dir", str(ws.repo)],
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout


@pytest.mark.parametrize("bodies", [
    [{"projects": []}],
    [{"projects": [], "has_more": False, "total": "999"}],
    ["not json"],
    [good_page([row(Path("/x"))], 3, 0, 100, False)],
])
def test_cli_exits_2_for_an_unverifiable_listing(tmp_path, ws, bodies):
    code, out = run_cli(tmp_path, ws, bodies)
    assert code == 2 and json.loads(out)["ok"] is False




def test_cli_accepts_genuinely_empty_and_multipage_listings(tmp_path, ws):
    assert run_cli(tmp_path, ws, [good_page([], 0)])[0] == 0
    (tmp_path / "m").mkdir()
    code, out = run_cli(tmp_path / "m", ws, [good_page([row(ws.repo, "a")], 2, 0, 1, True), good_page([row(ws.repo, "b")], 2, 1, 1, False)])
    assert code == 0 and json.loads(out)["projects_checked"] == 2


