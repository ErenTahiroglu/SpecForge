#!/usr/bin/env python3
"""Content-based change detection and freshness state for checkpoint_refresh.sh.

Stdlib only, Python 3.9 compatible. Never starts, stops or signals any process.
State lives outside the repository (default ~/.cache/specforge-checkpoint/<project>/).

Subcommands:
  plan    scan sources, compare with stored per-index state, print a JSON plan
  record  store the result of one index refresh (atomic write)
  verify-cbm / verify-graphify   freshness checks after a refresh
  get     print one key from a JSON file
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
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
    s = sub.add_parser("get")
    s.add_argument("file")
    s.add_argument("key")
    s.set_defaults(fn=cmd_get)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
