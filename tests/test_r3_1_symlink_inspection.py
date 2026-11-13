"""R3.1 P1-B1: a repository entry whose symlink status cannot be inspected is UNVERIFIED, never "not a symlink".

os.path.islink() turns every lstat failure into False, so a symlink inside a readable-but-not-searchable directory
(mode 0400) was skipped and the scope check passed. The walk now uses os.lstat + stat.S_ISLNK and records the failure."""
import errno
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

spec = importlib.util.spec_from_file_location("checkpoint_state_r31b", Path(__file__).parents[1] / "scripts/checkpoint_state.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions; real-filesystem test not run")


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "container"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "app").mkdir()
    priv = root / "private-a"                    # a non-git sibling: protected
    priv.mkdir()
    (root / "sibling" / ".git").mkdir(parents=True)
    return SimpleNamespace(root=root, repo=repo, priv=priv)


def projects(ws):
    return [{"name": "self", "root_path": str(ws.repo)}]


def fail_lstat_for(suffix, exc):
    real = os.lstat

    def lstat(path, *a, **kw):
        if str(path).endswith(suffix):
            raise exc
        return real(path, *a, **kw)
    return mock.patch.object(cs.os, "lstat", side_effect=lstat)


# ---- Codex's real non-root reproducer (report 34, P1-B1)
@needs_non_root
def test_readable_but_unsearchable_directory_is_unverified(ws):
    locked = ws.repo / "locked"
    locked.mkdir()
    (locked / "escape").symlink_to(ws.priv)
    control = cs.check_scope(str(ws.repo), projects(ws))                      # searchable: the protected link is found
    assert control["ok"] is False and control["protected_count"] == 1
    assert [v["kind"] for v in control["violations"]] == ["repository_symlink"]
    locked.chmod(0o400)                                                       # readable, but no search permission
    try:
        r = cs.check_scope(str(ws.repo), projects(ws))
    finally:
        locked.chmod(0o700)
    assert r["ok"] is False
    assert r["unverified"] and any("locked/escape" in u for u in r["unverified"])
    assert r["protected_count"] == 1


@needs_non_root
def test_cli_exits_2_for_the_unsearchable_directory(ws, tmp_path):
    locked = ws.repo / "locked"
    locked.mkdir()
    (locked / "escape").symlink_to(ws.priv)
    body = {"projects": projects(ws), "total": 1, "offset": 0, "limit": 100, "returned": 1, "has_more": False}
    fake = tmp_path / "fakebin"
    fake.mkdir()
    b = fake / "codebase-memory-mcp"
    b.write_text("#!%s\nimport json\nprint(json.dumps(%r))\n" % (sys.executable, body))
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, str(Path(cs.__file__)), "verify-scope", "--project-dir", str(ws.repo)]
    assert subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60).returncode == 1   # control: violation
    locked.chmod(0o400)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    finally:
        locked.chmod(0o700)
    assert p.returncode == 2 and json.loads(p.stdout)["ok"] is False and json.loads(p.stdout)["unverified"]


# ---- injected lstat failures confined to the symlink's own path
@pytest.mark.parametrize("exc", [PermissionError(errno.EACCES, "denied"), OSError(errno.EIO, "i/o error"),
                                 OSError(errno.ELOOP, "loop")])
def test_injected_lstat_failure_on_a_symlink_is_unverified(ws, exc):
    (ws.repo / "escape").symlink_to(ws.priv)
    with fail_lstat_for(os.sep + "escape", exc):
        r = cs.check_scope(str(ws.repo), projects(ws))
    assert r["ok"] is False and not r["violations"]
    assert any("escape" in u for u in r["unverified"])


def test_injected_lstat_failure_on_an_ordinary_file_is_also_unverified(ws):
    (ws.repo / "app" / "core.py").write_text("x = 1\n")
    with fail_lstat_for("core.py", PermissionError(errno.EACCES, "denied")):
        r = cs.check_scope(str(ws.repo), projects(ws))
    assert r["ok"] is False and any("core.py" in u for u in r["unverified"])


@pytest.mark.parametrize("exc", [FileNotFoundError(errno.ENOENT, "gone"), NotADirectoryError(errno.ENOTDIR, "not a dir")])
def test_a_genuinely_vanished_entry_is_skipped(ws, exc):
    (ws.repo / "app" / "core.py").write_text("x = 1\n")
    with fail_lstat_for("core.py", exc):
        r = cs.check_scope(str(ws.repo), projects(ws))
    assert r["ok"] is True and r["unverified"] == [] and r["violations"] == []


# ---- existing protections are unchanged
def test_readable_control_still_detects_every_kind_of_protected_link(ws):
    (ws.repo / "link").symlink_to(ws.priv)
    (ws.repo / "up").symlink_to("..")
    (ws.repo / "app" / "filelink").symlink_to(ws.priv / "secret.md")
    r = cs.check_scope(str(ws.repo), projects(ws))
    assert r["ok"] is False and len(r["violations"]) == 3 and r["unverified"] == []


def test_harmless_and_dangling_links_and_plain_files_still_pass(ws):
    (ws.repo / "harmless").symlink_to(ws.root / "sibling")
    (ws.repo / "dangling").symlink_to(ws.root / "gone")
    (ws.repo / "app" / "core.py").write_text("x = 1\n")
    r = cs.check_scope(str(ws.repo), projects(ws))
    assert r["ok"] is True and r["unverified"] == [] and r["violations"] == []


def test_the_shell_does_not_advance_the_baseline_after_a_nonzero_scope_result():
    """The shell marks the index stale on any non-zero verify-scope exit, and only fresh/fresh results are recorded."""
    text = (Path(__file__).parents[1] / "scripts/checkpoint_refresh.sh").read_text()
    block = text.split("verify-scope", 1)[1].split("Reject a moving input set", 1)[0]
    assert 'SCOPE_RC" -eq 0' in block and block.count('CBM_RESULT="stale"') >= 2
    tail = text.split("STEP E: report", 1)[1]
    assert "fresh*/fresh*)" in tail and tail.count("record cbm ok") == 1
    assert "baseline NOT advanced" in tail
