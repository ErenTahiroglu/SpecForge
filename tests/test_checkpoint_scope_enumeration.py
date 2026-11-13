"""Issue #11 (P1-B): the scope check must fail CLOSED. An unknown protected set (the sibling directories or an
identity could not be read) is not an empty set; it is unverifiable and verify-scope exits 2.

Reference - the project-listing schema observed from codebase-memory-mcp 0.11.0 (see test_checkpoint_listing_contract.py):
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
def test_failed_container_enumeration_is_unverified_not_an_empty_set(ws):
    with mock.patch.object(cs.os, "scandir", side_effect=PermissionError("denied")):
        r = cs.check_scope(str(ws.repo), [row(ws.priv)])
    assert r["ok"] is False and r["unverified"]
    assert "enumerat" in " ".join(r["unverified"]).lower()


def test_report32_reproducer_no_longer_reports_ok(ws):
    with mock.patch.object(cs.os, "scandir", side_effect=PermissionError("denied")):
        r = cs.check_scope(str(ws.repo), [{"root_path": str(ws.priv)}])
    assert not (r["ok"] and r["protected_count"] == 0 and not r["violations"] and not r["unverified"])


def test_protected_paths_raises_instead_of_returning_an_empty_list(ws):
    with mock.patch.object(cs.os, "scandir", side_effect=OSError("boom")):
        with pytest.raises(cs.ScopeUnverifiable):
            cs.protected_paths(str(ws.repo))


def test_a_sibling_entry_that_cannot_be_inspected_is_unverified(ws, monkeypatch):
    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path

        def is_dir(self, follow_symlinks=True):
            if self.name == "private-a":
                raise PermissionError("denied")
            return self._e.is_dir(follow_symlinks=follow_symlinks)

    real_scandir = cs.os.scandir
    monkeypatch.setattr(cs.os, "scandir", lambda p: [Entry(e) for e in real_scandir(p)])
    r = cs.check_scope(str(ws.repo), [row(ws.repo)])
    assert r["ok"] is False and r["unverified"]


def test_an_unreadable_git_marker_check_is_not_treated_as_not_a_repository(ws, monkeypatch):
    """os.path.exists() swallows errors; a sibling whose '.git' status cannot be determined is unverified."""
    real_stat = os.stat

    def stat_denied(path, *a, **k):
        if str(path).endswith(os.path.join("private-a", ".git")):
            raise PermissionError("denied")
        return real_stat(path, *a, **k)
    monkeypatch.setattr(cs.os, "stat", stat_denied)
    r = cs.check_scope(str(ws.repo), [row(ws.repo)])
    assert r["ok"] is False and r["unverified"]


def test_an_unreadable_directory_inside_the_repository_is_unverified(ws, monkeypatch):
    (ws.repo / "app" / "sub").mkdir()
    real_scandir = cs.os.scandir

    def selective(path):
        if str(path).endswith(os.path.join("app", "sub")):
            raise PermissionError("denied")
        return real_scandir(path)
    monkeypatch.setattr(cs.os, "scandir", selective)
    r = cs.check_scope(str(ws.repo), [row(ws.repo)])
    assert r["ok"] is False and any("repository directory" in u for u in r["unverified"])


def test_a_really_unreadable_directory_inside_the_repository_names_the_directory(ws):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = ws.repo / "app" / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        r = cs.check_scope(str(ws.repo), [row(ws.repo)])
    finally:
        locked.chmod(0o700)
    assert r["ok"] is False and any("app/locked" in u for u in r["unverified"])


def test_a_symlink_whose_target_cannot_be_resolved_for_identity_is_unverified(ws, monkeypatch):
    (ws.repo / "lnk").symlink_to(ws.priv)
    real_stat = os.stat

    def flaky(path, *a, **k):
        if str(path).rstrip("/").endswith("private-a") and not a and not k:
            raise PermissionError("denied")
        return real_stat(path, *a, **k)
    monkeypatch.setattr(cs.os, "stat", flaky)
    r = cs.check_scope(str(ws.repo), [row(ws.repo)])
    assert r["ok"] is False and (r["unverified"] or r["violations"])


def test_verified_empty_protected_set_still_passes(tmp_path):
    root = tmp_path / "container"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (root / "other-repo" / ".git").mkdir(parents=True)                   # only git siblings -> nothing protected
    r = cs.check_scope(str(repo), [row(repo), row(root / "other-repo")])
    assert r["ok"] is True and r["protected_count"] == 0 and not r["unverified"]


def test_ordinary_repository_with_no_siblings_passes(tmp_path):
    repo = tmp_path / "only"
    (repo / ".git").mkdir(parents=True)
    r = cs.check_scope(str(repo), [row(repo)])
    assert r["ok"] is True and r["protected_count"] == 0


def test_existing_protections_are_not_weakened(ws):
    (ws.repo / "link").symlink_to(ws.priv)
    assert cs.check_scope(str(ws.repo), [row(ws.priv)])["violations"]                      # project root
    assert cs.check_scope(str(ws.repo), [row(ws.root)])["violations"]                      # ancestor
    assert any(v["kind"] == "repository_symlink" for v in cs.check_scope(str(ws.repo), [row(ws.repo)])["violations"])
    assert cs.check_scope(str(ws.repo), [row(ws.repo / "x")], extra=[str(ws.repo / "x")])["violations"]   # explicit path


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




def test_cli_exits_2_when_sibling_enumeration_fails(tmp_path, ws):
    """A real permission error (the container cannot be listed) -> exit 2, never an empty protected set."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    control, broken = tmp_path / "control", tmp_path / "broken"
    control.mkdir()
    broken.mkdir()
    assert run_cli(control, ws, [good_page([row(ws.repo)], 1)])[0] == 0          # same setup, readable: passes
    ws.root.chmod(0o300)                                                          # write+execute, no read: scandir fails
    try:
        code, out = run_cli(broken, ws, [good_page([row(ws.repo)], 1)])
    finally:
        ws.root.chmod(0o700)
    assert code == 2 and json.loads(out)["ok"] is False and json.loads(out)["unverified"]




def test_the_shell_stops_the_baseline_on_helper_exit_2():
    text = (Path(__file__).parents[1] / "scripts/checkpoint_refresh.sh").read_text()
    block = text.split("verify-scope", 1)[1].split("Reject a moving input set", 1)[0]
    assert 'SCOPE_RC" -eq 0' in block and 'CBM_RESULT="stale"' in block
    assert block.count('CBM_RESULT="stale"') >= 2                    # both the violation (1) and the unverifiable (other) branches
