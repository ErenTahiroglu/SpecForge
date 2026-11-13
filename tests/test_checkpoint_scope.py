"""Scope protection of scripts/checkpoint_state.py (verify-scope): private directories must never be
reachable by an indexed project or by a symlink inside the repository.

A workspace container holds the repository, a sibling git repository, two non-git directories that count
as private, and a symlink alias to a private directory. No real index, daemon or network is used."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("checkpoint_state", Path(__file__).parents[1] / "scripts/checkpoint_state.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "container"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "app" / "core.py").write_text("x = 1\n")
    priv = root / "private-a"
    priv.mkdir()
    (priv / "secret.md").write_text("private\n")
    other = root / "private-b"
    (other / "deeper").mkdir(parents=True)
    sib = root / "sibling"
    (sib / ".git").mkdir(parents=True)
    ext = tmp_path / "elsewhere" / "project"
    ext.mkdir(parents=True)
    (root / "alias").symlink_to(priv)
    return SimpleNamespace(root=root, repo=repo, priv=priv, other=other, sib=sib, ext=ext)


def row(path, name=None):
    # Project name is the listing identity and must be unique per listing: default to one derived from the root.
    return {"name": str(path) if name is None else name, "root_path": str(path)}


def check(ws, rows, **kw):
    return cs.check_scope(str(ws.repo), rows, **kw)


# ---- what is protected
def test_non_git_siblings_are_protected_and_git_siblings_are_not(ws):
    protected = {os.path.realpath(p) for p in cs.protected_paths(str(ws.repo))}
    assert protected == {os.path.realpath(ws.priv), os.path.realpath(ws.other)}      # alias resolves to private-a


def test_repository_and_symlinks_leaving_the_container_are_not_discovered(ws, tmp_path):
    (ws.root / "away").symlink_to(ws.ext.parent)            # points outside the container
    protected = {os.path.realpath(p) for p in cs.protected_paths(str(ws.repo))}
    assert os.path.realpath(ws.ext.parent) not in protected and os.path.realpath(ws.repo) not in protected


def test_extra_and_allowed_paths(ws):
    extra = ws.ext
    assert os.path.realpath(extra) in {os.path.realpath(p) for p in cs.protected_paths(str(ws.repo), extra=[str(extra)])}
    allowed = {os.path.realpath(p) for p in cs.protected_paths(str(ws.repo), allow=[str(ws.other)])}
    assert os.path.realpath(ws.other) not in allowed and os.path.realpath(ws.priv) in allowed


# ---- indexed project roots
def test_clean_listing_is_accepted(ws):
    r = check(ws, [row(ws.repo), row(ws.ext), row(ws.sib)])
    assert r["violations"] == [] and r["projects_checked"] == 3


@pytest.mark.parametrize("which", ["priv", "other"])
def test_private_directory_as_project_root_is_rejected(ws, which):
    assert check(ws, [row(ws.repo), row(getattr(ws, which))])["violations"]


def test_symlink_alias_of_a_private_directory_is_rejected(ws):
    assert check(ws, [row(ws.root / "alias")])["violations"]


def test_directory_inside_a_private_directory_is_rejected(ws):
    assert check(ws, [row(ws.other / "deeper")])["violations"]
    assert check(ws, [row(ws.priv / "not-created-yet")])["violations"]          # stale/nonexistent roots too


@pytest.mark.parametrize("ancestor", ["container", "grandparent", "filesystem_root"])
def test_ancestor_of_a_private_directory_is_rejected(ws, ancestor):
    path = {"container": ws.root, "grandparent": ws.root.parent, "filesystem_root": Path("/")}[ancestor]
    assert check(ws, [row(path)])["violations"]


def test_case_variant_of_a_private_path_is_rejected_on_case_insensitive_volumes(ws):
    variant = Path(str(ws.priv).replace("private-a", "PRIVATE-A"))
    if not variant.exists():
        pytest.skip("case-sensitive filesystem: the variant names a different directory")
    assert check(ws, [row(variant)])["violations"]


def test_other_private_directory_not_named_anywhere_is_still_protected(ws):
    newdir = ws.root / "anything-else"
    newdir.mkdir()
    assert check(ws, [row(newdir)])["violations"]


def test_allow_and_extra_paths_change_the_decision(ws):
    assert check(ws, [row(ws.other)], allow=[str(ws.other)])["violations"] == []
    assert check(ws, [row(ws.sib)], extra=[str(ws.sib)])["violations"]


def test_project_without_a_root_cannot_be_verified(ws):
    r = check(ws, [{"name": "x"}])
    assert r["unverified"] and not r["violations"]


# ---- symlinks inside the repository
def test_symlink_in_repository_to_private_directory_is_reported(ws):
    (ws.repo / "link").symlink_to(ws.priv)
    (ws.repo / "app" / "filelink").symlink_to(ws.priv / "secret.md")
    r = check(ws, [row(ws.repo)])
    assert len(r["violations"]) == 2


def test_symlink_to_an_ancestor_or_via_a_chain_is_reported(ws):
    (ws.repo / "up").symlink_to("..")
    (ws.repo / "hop1").symlink_to(ws.root / "alias")
    assert len(check(ws, [row(ws.repo)])["violations"]) == 2


def test_harmless_and_excluded_symlinks_are_ignored(ws):
    (ws.repo / "harmless").symlink_to(ws.ext)
    (ws.repo / "inside").symlink_to(ws.repo / "app")
    (ws.repo / ".venv").mkdir()
    (ws.repo / ".venv" / "bin_link").symlink_to(ws.priv)             # .venv is outside the indexed input set
    (ws.repo / "dangling").symlink_to(ws.root / "gone")
    assert check(ws, [row(ws.repo)])["violations"] == []


# ---- command line, pagination and failure behaviour
_fake_counter = iter(range(10_000))


def fake_cbm(tmp_path, pages, fail=False):
    """A stand-in `codebase-memory-mcp` that serves list_projects pages from JSON (own directory per call)."""
    home = tmp_path / f"fake{next(_fake_counter)}"
    home.mkdir()
    data = home / "pages.json"
    data.write_text(json.dumps(pages))
    binary = home / "bin" / "codebase-memory-mcp"
    binary.parent.mkdir()
    binary.write_text(
        "#!%s\nimport json, sys\na = sys.argv[1:]\n"
        "%s"
        "off = int(a[a.index('--offset') + 1]) if '--offset' in a else 0\n"
        "print(json.dumps(json.load(open(%r))[off // 2 if off else 0]))\n" % (sys.executable, "sys.exit(3)\n" if fail else "", str(data)))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary.parent


def run_cli(tmp_path, ws, bin_dir, extra_env=None):
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, str(Path(cs.__file__)), "verify-scope", "--project-dir", str(ws.repo)],
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip().startswith("{") else p.stdout + p.stderr)


def page(rows, total, more, nxt=None, offset=0):
    """A page shaped like the real `list_projects --format json` response (codebase-memory-mcp 0.11.0)."""
    out = {"projects": rows, "total": total, "offset": offset, "limit": 100, "returned": len(rows), "has_more": more}
    if nxt is not None:
        out["next_offset"] = nxt
    return out


def test_cli_accepts_a_clean_listing_and_reads_every_page(ws, tmp_path):
    pages = [page([row(ws.repo), row(ws.ext)], 4, True, 2), page([row(ws.sib), row(ws.ext.parent / "x")], 4, False, offset=2)]
    code, out = run_cli(tmp_path, ws, fake_cbm(tmp_path, pages))
    assert code == 0 and out["ok"] and out["projects_checked"] == 4


def test_cli_finds_a_private_directory_that_is_only_on_a_later_page(ws, tmp_path):
    pages = [page([row(ws.repo), row(ws.ext)], 4, True, 2), page([row(ws.sib), row(ws.priv)], 4, False, offset=2)]
    code, out = run_cli(tmp_path, ws, fake_cbm(tmp_path, pages))
    assert code == 1 and out["violations"]


def test_cli_fails_closed_when_the_listing_cannot_be_read_or_is_incomplete(ws, tmp_path):
    code, _ = run_cli(tmp_path, ws, fake_cbm(tmp_path, [page([row(ws.repo)], 1, False)], fail=True))
    assert code == 2
    code, out = run_cli(tmp_path, ws, fake_cbm(tmp_path, [page([row(ws.repo)], 5, False)]))      # total says 5, one returned
    assert code == 2 and out["unverified"]
    code, out = run_cli(tmp_path, ws, fake_cbm(tmp_path, [page([row(ws.repo)], 3, True, 0)]))    # pagination does not advance
    assert code == 2


def test_cli_honours_environment_lists(ws, tmp_path):
    pages = [page([row(ws.repo), row(ws.sib)], 2, False)]
    assert run_cli(tmp_path, ws, fake_cbm(tmp_path, pages), {"CHECKPOINT_PRIVATE_PATHS": str(ws.sib)})[0] == 1
    pages = [page([row(ws.repo), row(ws.other)], 2, False)]
    bin_dir = fake_cbm(tmp_path, pages)
    assert run_cli(tmp_path, ws, bin_dir)[0] == 1
    assert run_cli(tmp_path, ws, bin_dir, {"CHECKPOINT_ALLOW_PATHS": str(ws.other)})[0] == 0


def test_the_script_delegates_the_scope_check_and_has_no_inline_name_match():
    text = (Path(__file__).parents[1] / "scripts/checkpoint_refresh.sh").read_text()
    assert "verify-scope" in text
    assert "list_projects" not in text and "PROJECTS" not in text      # the old inline listing + grep is gone
    assert "grep -q \"" not in text.split("verify-scope", 1)[1].split("Reject a moving input set", 1)[0]
