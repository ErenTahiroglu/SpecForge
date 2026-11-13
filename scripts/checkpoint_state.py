#!/usr/bin/env python3
"""Content-based change detection and freshness state for checkpoint_refresh.sh.

Stdlib only, Python 3.9 compatible. Never starts, stops or signals any process.
State lives outside the repository (default ~/.cache/specforge-checkpoint/<project>/).

Subcommands:
  plan    scan sources, compare with stored per-index state, print a JSON plan
  record  store the result of one index refresh (atomic write)
  verify-cbm / verify-graphify   freshness checks after a refresh
  verify-scope   fail if the code index (or a symlink in the repository) can reach a protected directory
  get     print one key from a JSON file
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone

SCHEMA = 2
EXCLUDE_DIRS = {
    ".git", ".venv", "graphify-out", ".checkpoint-logs", "__pycache__",
    ".pytest_cache", ".hypothesis", ".mypy_cache", ".ruff_cache",
    "node_modules", ".idea", ".vscode",
}
EXCLUDE_GLOBS = (
    ".DS_Store", ".env", ".env.*", "*.pyc", "*.pyo", "*.bak-*", "*.log",
    "*.tmp", "*.pem", "*.key", "id_rsa*", "*.p12",
)
CONFIG_FILES = (".gitignore", ".cbmignore", ".graphifyignore")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def excluded(name):
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def scan(root):
    """Return {relative_posix_path: sha256} for relevant files, deterministic."""
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if excluded(fn):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            manifest[rel] = sha256_file(full)
    return manifest


def graphify_inputs(root, verify=False):
    """Use the installed updater's detector/extractors; fail closed on adapter errors."""
    import shutil
    import subprocess
    executable = shutil.which("graphify")
    if not executable:
        raise RuntimeError("graphify unavailable")
    with open(executable, encoding="utf-8") as fh:
        interpreter = fh.readline().strip().removeprefix("#!")
    program = r"""
import json, sys
from pathlib import Path
from graphify.detect import detect, _md5_file, _nfc
from graphify.extract import _get_extractor
from graphify.watch import _read_build_excludes, _read_build_gitignore
root=Path(sys.argv[1]).resolve(); out=root/'graphify-out'
d=detect(root, extra_excludes=_read_build_excludes(out) or None,
         gitignore=_read_build_gitignore(out))
paths=list(d['files']['code'])
paths += [p for p in d['files'].get('document',[]) if _get_extractor(Path(p)) is not None]
paths=sorted(set(str(Path(p).relative_to(root)) for p in paths))
issues=[]
if sys.argv[2]=='True':
    manifest=json.loads((out/'manifest.json').read_text())
    for p in paths:
        if manifest.get(_nfc(p),{}).get('ast_hash') != _md5_file(root/p):
            issues.append('manifest missing/stale: '+p)
print(json.dumps({'paths':paths,'issues':issues}))
"""
    proc = subprocess.run([interpreter, "-c", program, root, str(verify)],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError(proc.stderr[-1000:])
    return json.loads(proc.stdout)


def fingerprint(root, versions, flags):
    manifest = scan(root)
    src = hashlib.sha256()
    for p in sorted(manifest):
        src.update(("%s\0%s\n" % (p, manifest[p])).encode("utf-8"))
    cfg = hashlib.sha256()
    cfg.update(("versions=%s\nflags=%s\n" % (versions, flags)).encode("utf-8"))
    for name in CONFIG_FILES:
        fp = os.path.join(root, name)
        cfg.update(("%s=%s\n" % (name, sha256_file(fp) if os.path.isfile(fp) else "absent")).encode())
    # Include nested ignore rules and persisted Graphify corpus options.
    for path in sorted(manifest):
        if os.path.basename(path) in CONFIG_FILES:
            cfg.update((path + manifest[path]).encode())
    build_cfg = os.path.join(root, "graphify-out", ".graphify_build.json")
    cfg.update((sha256_file(build_cfg) if os.path.isfile(build_cfg) else "absent").encode())
    graph_paths = graphify_inputs(root)["paths"]
    graph_manifest = {p: sha256_file(os.path.join(root, p)) for p in graph_paths}
    graph_fp = hashlib.sha256(json.dumps([graph_manifest, cfg.hexdigest()], sort_keys=True).encode()).hexdigest()
    total = hashlib.sha256((src.hexdigest() + cfg.hexdigest()).encode()).hexdigest()
    return {"fingerprint": total, "source_fp": src.hexdigest(),
            "config_fp": cfg.hexdigest(), "manifest": manifest,
            "index_fps": {"cbm": total, "graphify": graph_fp}, "graphify_manifest": graph_manifest}


def diff_manifests(old, new):
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(p for p in set(old) & set(new) if old[p] != new[p])
    renamed = []
    for r in list(removed):
        for a in list(added):
            if old[r] == new[a]:
                renamed.append({"from": r, "to": a})
                removed.remove(r)
                added.remove(a)
                break
    return {"added": added, "removed": removed, "modified": modified, "renamed": renamed}


def load_state(state_dir):
    """Return (state, reason). state is None when missing or corrupt."""
    path = os.path.join(state_dir, "state.json")
    if not os.path.exists(path):
        return None, "NO_BASELINE"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict) or state.get("schema") != SCHEMA:
            return None, "CORRUPT_BASELINE"
        for idx in ("cbm", "graphify"):
            rec = state.get(idx)
            if rec is not None and not isinstance(rec, dict):
                return None, "CORRUPT_BASELINE"
        return state, "OK"
    except (OSError, ValueError):
        return None, "CORRUPT_BASELINE"


def atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_plan(a):
    scan_result = fingerprint(a.project_dir, a.versions, a.flags)
    with open(a.scan_out, "w", encoding="utf-8") as fh:
        json.dump(scan_result, fh)
    state, why = load_state(a.state_dir)
    artifacts = {"cbm": a.cbm_artifact, "graphify": a.graphify_artifact}
    plan = {"fingerprint": scan_result["fingerprint"], "baseline": why, "indexes": {}}
    old_manifest = {}
    for idx in ("cbm", "graphify"):
        rec = (state or {}).get(idx)
        if state is None:
            need, reason = True, why
        elif not rec or rec.get("status") != "fresh":
            need, reason = True, "INDEX_NOT_FRESH:%s" % ((rec or {}).get("status", "none"))
        elif rec.get("fingerprint") != scan_result["index_fps"][idx]:
            need, reason = True, "SOURCE_OR_CONFIG_CHANGED"
        elif not os.path.exists(artifacts[idx]):
            need, reason = True, "ARTIFACT_MISSING"
        else:
            need, reason = False, "UNCHANGED"
        plan["indexes"][idx] = {"need": need, "reason": reason}
        if not need and rec.get("manifest") != scan_result["manifest"]:
            plan["indexes"][idx]["reason"] = "EXCLUDED_INPUT_ONLY_NO_REBUILD"
        if rec and isinstance(rec.get("manifest"), dict) and not old_manifest:
            old_manifest = rec["manifest"]
    plan["changes"] = diff_manifests(old_manifest, scan_result["manifest"])
    plan["need_cbm"] = plan["indexes"]["cbm"]["need"]
    plan["need_graphify"] = plan["indexes"]["graphify"]["need"]
    plan["file_count"] = len(scan_result["manifest"])
    print(json.dumps(plan))
    return 0


def cmd_record(a):
    with open(a.scan_file, "r", encoding="utf-8") as fh:
        scan_result = json.load(fh)
    state, _ = load_state(a.state_dir)
    if state is None:
        state = {"schema": SCHEMA}
    rec = state.get(a.index) or {}
    if a.result == "ok":
        state[a.index] = {
            "status": "fresh", "fingerprint": scan_result["index_fps"][a.index],
            "manifest": scan_result["manifest"], "completed_at": now_iso(),
            "checkpoint": a.checkpoint, "nodes": a.nodes, "edges": a.edges,
            "detail": a.detail,
        }
    else:
        state[a.index] = {
            "status": a.result, "fingerprint": None,
            "last_good_fingerprint": rec.get("fingerprint") or rec.get("last_good_fingerprint"),
            "failed_at": now_iso(), "checkpoint": a.checkpoint, "detail": a.detail,
        }
    both = all(state.get(i, {}).get("status") == "fresh"
               and state[i].get("fingerprint") == scan_result["index_fps"][i]
               for i in ("cbm", "graphify"))
    if both:
        state["last_complete"] = {"fingerprint": scan_result["fingerprint"],
                                  "checkpoint": a.checkpoint, "at": now_iso()}
    atomic_write(os.path.join(a.state_dir, "state.json"), state)
    print(json.dumps({"index": a.index, "status": state[a.index]["status"], "both_fresh": both}))
    return 0


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def cmd_verify_cbm(a):
    """Freshness = index_status ready for this root AND every relevant source file reports
    metadata_match (or not_tracked for non-code files) in check_index_coverage.
    indexed_at is reported but NOT used: it does not advance on incremental refreshes."""
    import subprocess
    kv = parse_kv(sys.stdin.read())
    issues = []
    if kv.get("status") != "ready":
        issues.append("status=%s" % kv.get("status"))
    if os.path.realpath(kv.get("root_path", "")) != os.path.realpath(a.project_dir):
        issues.append("root_path=%s" % kv.get("root_path"))
    with open(a.scan_file, "r", encoding="utf-8") as fh:
        paths = sorted(json.load(fh)["manifest"])
    args = {"project": a.project, "paths": paths, "path_limit": max(200, len(paths)), "format": "json"}
    try:
        proc = subprocess.run(["codebase-memory-mcp", "cli", "check_index_coverage", "--json"],
                              input=json.dumps(args), capture_output=True, text=True, timeout=120)
        out = proc.stdout
        env = json.loads(out[out.index("{"):])
        body = json.loads((env.get("result") or env)["content"][0]["text"])
        rows = body.get("paths", [])
        if body.get("path_has_more") or len(rows) != len(paths):
            issues.append("coverage rows %d != manifest %d" % (len(rows), len(paths)))
        bad = [(r.get("path"), r.get("status"), r.get("freshness")) for r in rows
               if r.get("freshness") not in ("metadata_match", "not_tracked")
               or r.get("status") != "no_recorded_issue"]
        if bad:
            issues.append("not fresh: %s" % bad[:5])
        if body.get("metadata", {}).get("generation_matches") is False:
            issues.append("generation mismatch")
    except Exception as exc:  # fail closed
        issues.append("coverage check failed: %s: %s" % (type(exc).__name__, exc))
    print(json.dumps({"ok": not issues, "issues": issues, "nodes": kv.get("nodes"),
                      "edges": kv.get("edges"), "indexed_at": kv.get("indexed_at"),
                      "files_checked": len(paths)}))
    return 0 if not issues else 1


# ---------------------------------------------------------------------------------------------
# Scope protection: private directories must never be indexed.
#
# Protected set = every directory next to the repository (same parent) that is NOT a git repository,
# plus the paths in CHECKPOINT_PRIVATE_PATHS, minus the paths in CHECKPOINT_ALLOW_PATHS (os.pathsep
# separated). Sibling git repositories are ordinary projects. No directory name is built in.
# A project root is rejected if it is a protected directory, lies inside one, or contains one (an
# ancestor would index it). Comparison uses file identity (device+inode), so symlink aliases and
# case variants on case-insensitive volumes cannot hide a path.
#
# FAIL CLOSED: a protected set that cannot be established (the sibling directory cannot be listed, an
# entry cannot be inspected, a stat fails) is UNKNOWN, never empty. It raises ScopeUnverifiable /
# is reported under "unverified", and verify-scope exits 2, which marks the index stale so the stored
# baseline does not advance. Only "file or directory does not exist" counts as a verified absence.
# ---------------------------------------------------------------------------------------------
LIST_PAGE = 100
MAX_PAGES = 1000


class ScopeUnverifiable(Exception):
    """The protected-directory set or an identity needed to judge a path could not be established."""


def _real(path):
    return os.path.realpath(os.path.expanduser(str(path)))


def _identity(path):
    """(device, inode) of an existing path; None only if it verifiably does not exist."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ScopeUnverifiable("cannot inspect %s (%s)" % (path, type(exc).__name__))
    return (st.st_dev, st.st_ino)


def _has_git(path):
    """True if `path` contains a .git entry; False only if it verifiably does not."""
    return _identity(os.path.join(path, ".git")) is not None


def _chain(path):
    """File identities of path and all its ancestors (path must exist; symlinks already resolved)."""
    out, cur = set(), path
    while True:
        ident = _identity(cur)
        if ident is not None:
            out.add(ident)
        parent = os.path.dirname(cur)
        if parent == cur:
            return out
        cur = parent


def _nearest_existing(path):
    cur = path
    while _identity(cur) is None:
        parent = os.path.dirname(cur)
        if parent == cur:
            return cur
        cur = parent
    return cur


def _split_paths(text):
    return [p for p in (text or "").split(os.pathsep) if p.strip()]


def protected_paths(project_dir, extra=(), allow=()):
    """Resolved protected directories for this repository (see block comment above).

    Raises ScopeUnverifiable if the set cannot be established; it never returns a partial set."""
    repo = _real(project_dir)
    container = os.path.dirname(repo)
    found = {}
    if container != repo:
        try:
            entries = sorted(os.scandir(container), key=lambda e: e.name)
        except OSError as exc:
            raise ScopeUnverifiable("cannot enumerate the directories next to the repository (%s)" % type(exc).__name__)
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError as exc:
                raise ScopeUnverifiable("cannot inspect the sibling entry %r (%s)" % (entry.name, type(exc).__name__))
            real = _real(entry.path)
            if real == repo or os.path.dirname(real) != container:
                continue            # the repository itself, or a link that leaves the container
            if _has_git(real):
                continue            # a sibling git repository is an ordinary project
            found[real] = True
    for p in extra:
        found[_real(p)] = True
    allowed_paths = {_real(p) for p in allow}
    allowed = {i for i in (_identity(p) for p in allowed_paths) if i is not None}
    return sorted(p for p in found if p not in allowed_paths and _identity(p) not in allowed)


def check_scope(project_dir, projects, extra=(), allow=()):
    """Return {ok, violations, unverified, projects_checked, protected_count}.

    ok is True only if the protected set was established AND every project root and repository symlink
    could be judged AND none reaches a protected directory."""
    try:
        protected = protected_paths(project_dir, extra, allow)
        pidents = {i for i in (_identity(p) for p in protected) if i is not None}
        ancestors = set()
        for p in protected:
            cur = os.path.dirname(p)
            while True:
                ident = _identity(cur)
                if ident is not None:
                    ancestors.add(ident)
                if os.path.dirname(cur) == cur:
                    break
                cur = os.path.dirname(cur)
    except ScopeUnverifiable as exc:
        return {"ok": False, "violations": [], "unverified": [str(exc)],
                "projects_checked": len(projects), "protected_count": None}

    def reach(path):
        real = _real(path)
        anchor = _nearest_existing(real)
        if _chain(anchor) & pidents:
            return "inside_or_equal"
        if anchor == real and _identity(real) in ancestors:
            return "contains"
        return None

    violations, unverified = [], []
    checked = 0
    for row in projects:
        checked += 1
        root = row.get("root_path") if isinstance(row, dict) else None
        if not isinstance(root, str) or not root.strip():
            unverified.append("project %r has no root_path" % (row.get("name") if isinstance(row, dict) else row))
            continue
        try:
            why = reach(root)
        except ScopeUnverifiable as exc:
            unverified.append("project root: %s" % exc)
            continue
        if why:
            violations.append({"kind": "indexed_project", "project": row.get("name"), "root_path": root, "reason": why})
    repo = _real(project_dir)
    walk_errors = []
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False, onerror=walk_errors.append):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(dirnames + filenames):
            full = os.path.join(dirpath, name)
            try:
                is_link = stat.S_ISLNK(os.lstat(full).st_mode)
            except (FileNotFoundError, NotADirectoryError):
                continue            # the entry vanished while walking: nothing there to judge
            except OSError as exc:
                # os.path.islink() would turn this into "not a symlink". An entry that cannot be inspected is unverified.
                unverified.append("cannot inspect repository entry %s (%s)" % (
                    os.path.relpath(full, repo).replace(os.sep, "/"), type(exc).__name__))
                continue
            if not is_link:
                continue
            try:
                why = reach(full)
            except ScopeUnverifiable as exc:
                unverified.append("repository symlink %s: %s" % (os.path.relpath(full, repo).replace(os.sep, "/"), exc))
                continue
            if why:
                violations.append({"kind": "repository_symlink", "path": os.path.relpath(full, repo).replace(os.sep, "/"),
                                   "reason": why})
    for exc in walk_errors:
        where = getattr(exc, "filename", None)
        rel = os.path.relpath(where, repo).replace(os.sep, "/") if isinstance(where, str) and where.startswith(repo) else "?"
        unverified.append("cannot read repository directory %s (%s)" % (rel, type(exc).__name__))
    return {"ok": not violations and not unverified, "violations": violations, "unverified": unverified,
            "projects_checked": checked, "protected_count": len(protected)}


def _is_int(value):
    """A real, non-negative integer. bool is a subclass of int and is deliberately NOT accepted."""
    return type(value) is int and value >= 0


def _list_projects():
    """All projects of the code index (every page), validated against the observed schema of
    codebase-memory-mcp 0.11.0:

        {"projects": [..], "total": int, "offset": int, "limit": int, "returned": int, "has_more": bool,
         "next_offset": int  (present exactly while has_more is true)}

    A page past the end is {"projects": [], "returned": 0, "has_more": false}. Completeness is accepted only
    when every page is well formed and consistent and the rows seen add up to `total`; anything else raises
    RuntimeError (nothing is ever inferred from a missing or malformed field)."""
    import subprocess
    rows, offset, total = [], 0, None
    for _ in range(MAX_PAGES):
        try:
            proc = subprocess.run(["codebase-memory-mcp", "cli", "list_projects", "--format", "json",
                                   "--limit", str(LIST_PAGE), "--offset", str(offset)],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("list_projects could not run: %s" % type(exc).__name__)
        if proc.returncode != 0:
            raise RuntimeError("list_projects failed rc=%d" % proc.returncode)
        try:
            body = json.loads(proc.stdout[proc.stdout.index("{"):])
        except ValueError:
            raise RuntimeError("list_projects returned unreadable output")
        if not isinstance(body, dict):
            raise RuntimeError("list_projects returned unreadable output")
        page = body.get("projects")
        if not isinstance(page, list) or not all(isinstance(p, dict) for p in page):
            raise RuntimeError("list_projects page has no valid project list")
        for key in ("total", "offset", "returned"):
            if not _is_int(body.get(key)):
                raise RuntimeError("list_projects metadata %r is missing or not an integer" % key)
        if type(body.get("has_more")) is not bool:
            raise RuntimeError("list_projects metadata 'has_more' is missing or not a boolean")
        if body["offset"] != offset:
            raise RuntimeError("list_projects answered offset %d for a request at %d" % (body["offset"], offset))
        if body["returned"] != len(page):
            raise RuntimeError("list_projects reported returned=%d for %d projects" % (body["returned"], len(page)))
        if total is not None and body["total"] != total:
            raise RuntimeError("list_projects total changed from %d to %d during the listing" % (total, body["total"]))
        total = body["total"]
        rows.extend(page)
        if len(rows) > total:
            raise RuntimeError("list_projects returned more projects than its total")
        if not body["has_more"]:
            if "next_offset" in body:
                raise RuntimeError("list_projects last page carries a next_offset")
            break
        nxt = body.get("next_offset")
        if not _is_int(nxt) or not page or nxt != offset + len(page):
            raise RuntimeError("list_projects pagination metadata is invalid or does not advance")
        offset = nxt
    else:
        raise RuntimeError("list_projects has too many pages")
    if total != len(rows):
        raise RuntimeError("list_projects returned %d of %d projects" % (len(rows), total))
    return rows


def cmd_verify_scope(a):
    extra = _split_paths(os.environ.get("CHECKPOINT_PRIVATE_PATHS"))
    allow = _split_paths(os.environ.get("CHECKPOINT_ALLOW_PATHS"))
    try:
        rows = _list_projects()
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "violations": [], "unverified": [str(exc)], "projects_checked": 0}))
        return 2
    result = check_scope(a.project_dir, rows, extra, allow)
    print(json.dumps(result))
    if result["violations"]:
        return 1
    return 0 if result["ok"] else 2


def cmd_verify_graphify(a):
    issues = []
    try:
        with open(a.graph, "r", encoding="utf-8") as fh:
            g = json.load(fh)
        nodes, links = g.get("nodes", []), g.get("links", [])
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "issues": ["graph unreadable: %s" % exc]}))
        return 1
    # A successful refresh may leave identical topology untouched. The updater's
    # per-input manifest, not graph mtime, proves that it processed current bytes.
    try:
        issues.extend(graphify_inputs(a.project_dir, verify=True)["issues"])
    except Exception as exc:
        issues.append("input verification failed: %s" % exc)
    if g.get("input_tokens", 0) != 0 or g.get("output_tokens", 0) != 0:
        issues.append("LLM tokens recorded: in=%s out=%s" % (g.get("input_tokens"), g.get("output_tokens")))
    sources = {n.get("source_file") for n in nodes if n.get("source_file")}
    stale = sorted(s for s in sources if not os.path.exists(os.path.join(a.project_dir, s)))
    if stale:
        issues.append("stale source refs: %s" % stale[:5])
    with open(a.scan_file, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)["graphify_manifest"]
    missing = sorted(p for p in manifest if p.endswith(".py") and p not in sources and os.path.getsize(os.path.join(a.project_dir, p)))
    if missing:
        issues.append("python files missing from graph: %s" % missing[:5])
    print(json.dumps({"ok": not issues, "issues": issues, "nodes": len(nodes), "edges": len(links)}))
    return 0 if not issues else 1


def cmd_get(a):
    with open(a.file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for part in a.key.split("."):
        data = data[part]
    print(json.dumps(data) if isinstance(data, (dict, list)) else data)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan")
    s.add_argument("--project-dir", required=True)
    s.add_argument("--state-dir", required=True)
    s.add_argument("--versions", default="")
    s.add_argument("--flags", default="")
    s.add_argument("--cbm-artifact", required=True)
    s.add_argument("--graphify-artifact", required=True)
    s.add_argument("--scan-out", required=True)
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("record")
    s.add_argument("--state-dir", required=True)
    s.add_argument("--index", choices=("cbm", "graphify"), required=True)
    s.add_argument("--scan-file", required=True)
    s.add_argument("--result", choices=("ok", "failed", "blocked"), required=True)
    s.add_argument("--detail", default="")
    s.add_argument("--checkpoint", default="")
    s.add_argument("--nodes", default=None)
    s.add_argument("--edges", default=None)
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("verify-cbm")
    s.add_argument("--project-dir", required=True)
    s.add_argument("--project", required=True)
    s.add_argument("--scan-file", required=True)
    s.set_defaults(fn=cmd_verify_cbm)
    s = sub.add_parser("verify-graphify")
    s.add_argument("--project-dir", required=True)
    s.add_argument("--graph", required=True)
    s.add_argument("--scan-file", required=True)
    s.add_argument("--since", type=float, required=True)
    s.set_defaults(fn=cmd_verify_graphify)
    s = sub.add_parser("verify-scope")
    s.add_argument("--project-dir", required=True)
    s.set_defaults(fn=cmd_verify_scope)
    s = sub.add_parser("get")
    s.add_argument("file")
    s.add_argument("key")
    s.set_defaults(fn=cmd_get)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
