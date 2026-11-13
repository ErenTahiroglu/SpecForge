"""R3.1 P1-C1: a repeated project identity must not count as a distinct unseen project.

Project `name` is the stable identity (codebase-memory-mcp 0.11.0). A listing whose rows add up to `total` only because
one name repeats - within a page or across pages - is unverifiable and raises. Distinct names that share one root are
legitimate aliases and stay valid. Bounded P2 strictness in the same validator: `limit` must be a positive integer and
`has_more` must agree with offset + returned < total, exactly as the real CLI reports it."""
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

spec = importlib.util.spec_from_file_location("checkpoint_state_r31c", Path(__file__).parents[1] / "scripts/checkpoint_state.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def body(projects, total, offset=0, limit=100, more=False, nxt=None, **over):
    out = {"projects": projects, "total": total, "offset": offset, "limit": limit, "returned": len(projects), "has_more": more}
    if more:
        out["next_offset"] = offset + len(projects) if nxt is None else nxt
    out.update(over)
    return out


def p(name, root=None):
    return {"name": name, "root_path": root or f"/x/{name}"}


def listing(pages):
    it = iter(pages)

    def fake(cmd, **kw):
        b = next(it)
        return subprocess.CompletedProcess(cmd, 0, b if isinstance(b, str) else json.dumps(b), "")
    return mock.patch("subprocess.run", side_effect=fake)


def run_list(pages):
    with listing(pages):
        return cs._list_projects()


def reject(pages, match=None):
    with listing(pages):
        with pytest.raises(RuntimeError, match=match):
            cs._list_projects()


# ---------------------------------------------------------------- duplicate identities
def test_duplicate_identity_within_one_page_is_rejected():
    reject([body([p("self"), p("self")], 2)], "duplicate")


def test_duplicate_identity_across_advancing_pages_is_rejected():
    """Codex's reproducer: offsets advance and the total is stable, but only one distinct project was seen."""
    row = p("self", "/x/repo")
    reject([body([row], 2, more=True), body([row], 2, offset=1)], "duplicate")


def test_duplicate_identity_with_a_changed_root_is_rejected():
    reject([body([p("self", "/x/one")], 2, more=True), body([p("self", "/x/two")], 2, offset=1)], "duplicate")


def test_a_protected_project_replaced_by_a_duplicate_public_one_is_rejected():
    reject([body([p("self"), p("public")], 3, more=True), body([p("public")], 3, offset=2)], "duplicate")


def test_rows_are_not_silently_deduplicated():
    with listing([body([p("a"), p("a")], 2)]):
        with pytest.raises(RuntimeError):
            cs._list_projects()


@pytest.mark.parametrize("bad", [{"root_path": "/x"}, {"name": None, "root_path": "/x"}, {"name": "", "root_path": "/x"},
                                 {"name": 7, "root_path": "/x"}, {"name": ["a"], "root_path": "/x"}])
def test_a_project_without_a_usable_name_has_no_identity_and_is_rejected(bad):
    reject([body([bad], 1)], "name")


def test_distinct_names_sharing_one_root_are_legitimate_aliases():
    rows = run_list([body([p("alias-1", "/x/shared"), p("alias-2", "/x/shared")], 2)])
    assert [r["name"] for r in rows] == ["alias-1", "alias-2"]


def test_valid_multipage_and_empty_listings_still_pass():
    rows = run_list([body([p("a"), p("b")], 5, more=True), body([p("c"), p("d")], 5, offset=2, more=True),
                     body([p("e")], 5, offset=4)])
    assert [r["name"] for r in rows] == ["a", "b", "c", "d", "e"]
    assert run_list([body([], 0)]) == []


# ---------------------------------------------------------------- bounded P2 strictness: limit
@pytest.mark.parametrize("limit", [0, -1, 1.5, 100.0, "100", True, False, None, [100], {"n": 1}])
def test_limit_must_be_a_positive_integer(limit):
    reject([body([p("a")], 1, limit=limit)], "limit")


def test_missing_limit_is_rejected_even_for_an_empty_listing():
    b = body([], 0)
    del b["limit"]
    reject([b], "limit")
    reject([body([], 0, limit=True)], "limit")


def test_returned_may_not_exceed_limit():
    reject([body([p("a"), p("b")], 2, limit=1)], "limit")


@pytest.mark.parametrize("limit", [1, 5, 50, 100, 500])
def test_any_positive_integer_limit_the_cli_may_echo_is_accepted(limit):
    assert len(run_list([body([p("a")], 1, limit=limit)])) == 1


# ---------------------------------------------------------------- bounded P2 strictness: has_more
def test_has_more_must_agree_with_the_counters():
    # page already reaches the total yet claims more, then an empty final page (report 34)
    reject([body([p("a"), p("b")], 2, more=True), body([], 2, offset=2)], "has_more")
    # has_more false although rows are still missing
    reject([body([p("a")], 3, more=False)], "has_more")
    # intermediate page claims more but the counters say it is the last
    reject([body([p("a")], 1, more=True)], "has_more")


def test_has_more_is_checked_on_the_real_cli_shapes():
    full = [p(f"n{i}") for i in range(5)]
    assert len(run_list([body(full, 5, limit=5)])) == 5                                   # exactly one full page: has_more false
    assert run_list([body([], 0, limit=5)]) == []                                          # empty listing


# ---------------------------------------------------------------- recorded real 0.11.0 responses still pass
def test_recorded_real_cli_response_shapes_still_pass():
    rows = [p(f"proj{i}") for i in range(13)]
    pages = [body(rows[:5], 13, 0, 5, True), body(rows[5:10], 13, 5, 5, True), body(rows[10:], 13, 10, 5)]
    assert len(run_list(pages)) == 13
    assert len(run_list([body(rows, 13, limit=50)])) == 13
    assert len(run_list([body(rows, 13, limit=500)])) == 13


# ---------------------------------------------------------------- end to end: the second page really is requested
LIST_STUB = r'''#!%(py)s
import json, os, sys
a = sys.argv[1:]
if "list_projects" not in a:
    sys.exit(0)
rows = json.load(open(%(rows)r))
mode = open(%(mode)r).read().strip()
limit, offset = int(a[a.index("--limit") + 1]), int(a[a.index("--offset") + 1])
open(%(log)r, "a").write("%%d\n" %% offset)
page = rows[offset:offset + limit]
if mode == "substitute" and offset >= limit:
    page = page[:-1] + [dict(rows[1])]          # the protected last row is replaced by a repeat of a public project
more = offset + len(page) < len(rows)
out = {"projects": page, "total": len(rows), "offset": offset, "limit": limit, "returned": len(page), "has_more": more}
if more:
    out["next_offset"] = offset + len(page)
print(json.dumps(out))
'''


@pytest.fixture
def cli(tmp_path):
    root = tmp_path / "container"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    priv = root / "private-a"
    priv.mkdir()
    fillers = []
    for i in range(110):                                  # more than one 100-row page
        d = tmp_path / "external" / f"f{i}"
        d.mkdir(parents=True)
        fillers.append({"name": f"filler-{i}", "root_path": str(d)})
    rows = [{"name": "self", "root_path": str(repo)}] + fillers + [{"name": "private-project", "root_path": str(priv)}]
    assert rows.index(rows[-1]) == 111 and len(rows) > cs.LIST_PAGE      # the protected project is behind page one
    d = tmp_path / "fakebin"
    d.mkdir()
    (tmp_path / "rows.json").write_text(json.dumps(rows))
    (tmp_path / "mode").write_text("honest")
    stub = d / "codebase-memory-mcp"
    stub.write_text(LIST_STUB % {"py": sys.executable, "rows": str(tmp_path / "rows.json"), "mode": str(tmp_path / "mode"),
                                 "log": str(tmp_path / "offsets.log")})
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def run(mode):
        (tmp_path / "mode").write_text(mode)
        (tmp_path / "offsets.log").write_text("")
        env = dict(os.environ, PATH=f"{d}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
        pr = subprocess.run([sys.executable, str(Path(cs.__file__)), "verify-scope", "--project-dir", str(repo)],
                            capture_output=True, text=True, env=env, timeout=60)
        offsets = [int(x) for x in (tmp_path / "offsets.log").read_text().split()]
        return pr.returncode, json.loads(pr.stdout), offsets
    return SimpleNamespace(run=run)


def test_honest_multipage_listing_finds_the_protected_project_on_page_two(cli):
    code, out, offsets = cli.run("honest")
    assert offsets == [0, 100] and code == 1
    assert [v["kind"] for v in out["violations"]] == ["indexed_project"]


def test_a_duplicate_substituting_for_the_protected_project_is_unverified(cli):
    code, out, offsets = cli.run("substitute")
    assert offsets == [0, 100]                                   # page two was genuinely requested
    assert code == 2 and out["ok"] is False and not out["violations"]
    assert any("duplicate" in u for u in out["unverified"])
