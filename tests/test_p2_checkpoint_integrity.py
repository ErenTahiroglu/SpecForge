"""#57: developer checkpoint tooling must not report success without verified identity and persistence.

All tests use disposable temp directories and stubs. Nothing here starts a real index, graph refresh, MCP server or child CLI, and
nothing reads or writes a real cache or another project. The shell script is run against a throwaway project whose helper is a
stub that records calls, so the script's own control flow and exit codes are what is tested (the real helper is tested in
process, below). A real end-to-end refresh with the real indexers is NOT_RUN by design.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checkpoint_state", ROOT / "scripts" / "checkpoint_state.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")


# ============================================ verify-cbm: exact identities, child status, metadata ==========================
PATHS = ["README.md", "app/core.py", "app/ui.py", "tests/test_core.py"]


def coverage_stdout(paths, *, status="no_recorded_issue", freshness="metadata_match", generation=True, more=False, extra_meta=True):
    rows = [{"path": p, "status": status, "freshness": freshness} for p in paths]
    body = {"paths": rows, "path_has_more": more}
    if extra_meta:
        body["metadata"] = {} if generation is None else {"generation_matches": generation}
    return json.dumps({"result": {"content": [{"text": json.dumps(body)}]}})


@pytest.fixture
def verify(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    project.mkdir()
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"manifest": {p: "x" for p in PATHS}}))
    calls = []

    def run(stdout, returncode=0, status_text=None, raises=None):
        text = status_text if status_text is not None else f"status: ready\nroot_path: {project}\nnodes: 3\nedges: 2\n"

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        monkeypatch.setattr(cs, "sys", SimpleNamespace(stdin=SimpleNamespace(read=lambda: text), argv=sys.argv, exit=sys.exit))
        with mock.patch("subprocess.run", side_effect=fake_run):
            rc = cs.cmd_verify_cbm(SimpleNamespace(project_dir=str(project), project="proj", scan_file=str(scan)))
        report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        return rc, report

    run.calls = calls
    return run


def test_exactly_the_requested_paths_is_fresh(verify):
    rc, report = verify(coverage_stdout(PATHS))
    assert rc == 0 and report["ok"] is True and report["issues"] == [] and report["files_checked"] == 4


def test_the_child_is_a_list_command_with_no_shell(verify):
    verify(coverage_stdout(PATHS))
    cmd, kwargs = verify.calls[0]
    assert isinstance(cmd, list) and not kwargs.get("shell") and kwargs.get("timeout")


def test_a_failed_child_with_plausible_stdout_is_not_fresh(verify):
    rc, report = verify(coverage_stdout(PATHS), returncode=3)
    assert rc == 1 and report["ok"] is False and any("exit" in i or "return" in i for i in report["issues"])


@pytest.mark.parametrize("label,paths", [
    ("same count, one path replaced", PATHS[:3] + ["app/other.py"]),
    ("missing path, extra path", PATHS[:3] + ["extra/file.py"]),
    ("duplicate path hides a missing one", PATHS[:3] + [PATHS[0]]),
    ("extra path", PATHS + ["extra.py"]),
    ("missing path", PATHS[:3]),
    ("different spelling of a path", PATHS[:3] + ["./tests/test_core.py"]),
    ("different case of a path", PATHS[:3] + ["Tests/test_core.py"]),
    ("absolute path", PATHS[:3] + ["/tests/test_core.py"]),
    ("parent traversal", PATHS[:3] + ["../tests/test_core.py"]),
])
def test_any_difference_between_requested_and_returned_paths_fails_closed(verify, label, paths):
    rc, report = verify(coverage_stdout(paths))
    assert rc == 1 and report["ok"] is False, label


def test_rows_with_bad_shapes_fail_closed(verify):
    bad = json.dumps({"result": {"content": [{"text": json.dumps({"paths": [{"path": 5, "status": "no_recorded_issue", "freshness": "metadata_match"}] * 4,
                                                                  "metadata": {"generation_matches": True}})}]}})
    assert verify(bad)[0] == 1
    not_dicts = json.dumps({"result": {"content": [{"text": json.dumps({"paths": ["a", "b", "c", "d"], "metadata": {"generation_matches": True}})}]}})
    assert verify(not_dicts)[0] == 1


@pytest.mark.parametrize("kwargs", [dict(generation=None), dict(generation=False), dict(extra_meta=False), dict(more=True)])
def test_missing_or_bad_generation_metadata_and_truncation_fail_closed(verify, kwargs):
    assert verify(coverage_stdout(PATHS, **kwargs))[0] == 1


@pytest.mark.parametrize("freshness,status", [("stale", "no_recorded_issue"), ("metadata_match", "partial"), ("unknown", "no_recorded_issue")])
def test_a_row_that_is_not_fresh_fails_closed(verify, freshness, status):
    assert verify(coverage_stdout(PATHS, freshness=freshness, status=status))[0] == 1


@pytest.mark.parametrize("stdout", ["", "not json", "{", json.dumps({"result": {"content": []}}), json.dumps(["x"])])
def test_malformed_child_output_fails_closed(verify, stdout):
    assert verify(stdout)[0] == 1


@pytest.mark.parametrize("exc", [OSError("no such program"), subprocess.TimeoutExpired("x", 1)])
def test_a_child_that_cannot_run_fails_closed(verify, exc):
    assert verify("", raises=exc)[0] == 1


def test_wrong_root_or_status_fails_closed(verify, tmp_path):
    assert verify(coverage_stdout(PATHS), status_text=f"status: ready\nroot_path: {tmp_path / 'elsewhere'}\n")[0] == 1
    assert verify(coverage_stdout(PATHS), status_text=f"status: indexing\nroot_path: {tmp_path / 'proj'}\n")[0] == 1


# ============================================ verify-graphify: token provenance ===================================================
@pytest.fixture
def graphify(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    (project / "graphify-out").mkdir(parents=True)
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"graphify_manifest": {}}))
    monkeypatch.setattr(cs, "graphify_inputs", lambda *a, **k: {"issues": []})

    def run(graph):
        path = project / "graphify-out" / "graph.json"
        path.write_text(json.dumps(graph))
        rc = cs.cmd_verify_graphify(SimpleNamespace(project_dir=str(project), graph=str(path), scan_file=str(scan), since=0))
        return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return run


def test_token_counters_that_are_present_and_zero_are_reported_as_zero(graphify):
    rc, report = graphify({"nodes": [], "links": [], "input_tokens": 0, "output_tokens": 0})
    assert rc == 0 and report["llm_tokens"] == "0"


@pytest.mark.parametrize("graph", [{"nodes": [], "links": []}, {"nodes": [], "links": [], "input_tokens": 0},
                                   {"nodes": [], "links": [], "input_tokens": "0", "output_tokens": "0"},
                                   {"nodes": [], "links": [], "input_tokens": None, "output_tokens": None},
                                   {"nodes": [], "links": [], "input_tokens": True, "output_tokens": 0}])
def test_missing_or_malformed_token_counters_are_unknown_not_zero(graphify, graph):
    rc, report = graphify(graph)
    assert report["llm_tokens"] == "UNKNOWN"


def test_nonzero_tokens_still_fail(graphify):
    rc, report = graphify({"nodes": [], "links": [], "input_tokens": 5, "output_tokens": 0})
    assert rc == 1 and any("LLM tokens" in i for i in report["issues"])


# ============================================ record / verify-recorded ===========================================================
def scan_file(tmp_path, fp="fp-1"):
    p = tmp_path / "scan.json"
    p.write_text(json.dumps({"fingerprint": fp, "manifest": {"a.py": "1"}, "graphify_manifest": {}, "index_fps": {"cbm": "c-1", "graphify": "g-1"}}))
    return str(p)


def record(tmp_path, index, result="ok"):
    return cs.cmd_record(SimpleNamespace(state_dir=str(tmp_path / "state"), index=index, scan_file=scan_file(tmp_path), result=result,
                                         detail="t", checkpoint="T", nodes="1", edges="1"))


def verify_recorded(tmp_path, fp="fp-1"):
    return cs.cmd_verify_recorded(SimpleNamespace(state_dir=str(tmp_path / "state"), scan_file=scan_file(tmp_path, fp)))


def test_a_complete_recorded_baseline_verifies(tmp_path, capsys):
    assert record(tmp_path, "cbm") == 0 and record(tmp_path, "graphify") == 0
    capsys.readouterr()
    assert verify_recorded(tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_one_index_recorded_is_not_both_fresh(tmp_path, capsys):
    assert record(tmp_path, "cbm") == 0
    capsys.readouterr()
    assert verify_recorded(tmp_path) == 1


def test_a_failed_record_is_not_verified_and_no_state_means_failure(tmp_path, capsys):
    assert verify_recorded(tmp_path) == 1
    assert record(tmp_path, "cbm") == 0 and record(tmp_path, "graphify", "failed") == 0
    capsys.readouterr()
    assert verify_recorded(tmp_path) == 1


def test_a_different_scan_fingerprint_is_not_verified(tmp_path, capsys):
    record(tmp_path, "cbm"), record(tmp_path, "graphify")
    capsys.readouterr()
    other = tmp_path / "scan2.json"
    other.write_text(json.dumps({"fingerprint": "fp-2", "manifest": {}, "graphify_manifest": {}, "index_fps": {"cbm": "c-2", "graphify": "g-1"}}))
    assert cs.cmd_verify_recorded(SimpleNamespace(state_dir=str(tmp_path / "state"), scan_file=str(other))) == 1


def test_a_corrupt_state_file_is_not_verified(tmp_path, capsys):
    record(tmp_path, "cbm"), record(tmp_path, "graphify")
    (tmp_path / "state" / "state.json").write_text("{not json")
    capsys.readouterr()
    assert verify_recorded(tmp_path) == 1


def test_a_writer_that_fails_returns_a_nonzero_status_and_keeps_the_old_state(tmp_path, capsys):
    assert record(tmp_path, "cbm") == 0
    before = (tmp_path / "state" / "state.json").read_text()
    with mock.patch.object(cs, "atomic_write", side_effect=OSError("disk full")):
        rc = record(tmp_path, "graphify")
    out = capsys.readouterr().out
    assert rc != 0 and '"ok": false' in out
    assert (tmp_path / "state" / "state.json").read_text() == before


# ============================================ the shell script's own control flow =================================================
FAKE_HELPER = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    cmd, args = sys.argv[1], sys.argv[2:]
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    log = os.environ["FAKE_CALL_LOG"]
    def note(text):
        open(log, "a").write(text + "\\n")
    if cmd == "get":
        data = json.load(open(args[0]))
        for part in args[1].split("."):
            data = data[part]
        print(json.dumps(data) if isinstance(data, (dict, list)) else data)
    elif cmd == "plan":
        need = os.environ.get("FAKE_NEED", "True")
        scan = {"fingerprint": "fp1", "manifest": {}, "graphify_manifest": {}, "index_fps": {"cbm": "c", "graphify": "g"}}
        json.dump(scan, open(opt("--scan-out"), "w"))
        print(json.dumps({"need_cbm": need == "True", "need_graphify": need == "True", "baseline": "X", "file_count": 1,
                          "fingerprint": "fp1", "indexes": {"cbm": {"reason": "r"}, "graphify": {"reason": "r"}},
                          "changes": {"removed": [], "renamed": [], "added": [], "modified": []}}))
    elif cmd == "verify-cbm":
        sys.stdin.read()
        print(json.dumps({"ok": True, "issues": [], "nodes": "3", "edges": "2"}))
    elif cmd == "verify-graphify":
        print(json.dumps({"ok": True, "issues": [], "nodes": 3, "edges": 2, "llm_tokens": os.environ.get("FAKE_LLM_TOKENS", "0")}))
    elif cmd == "verify-scope":
        print(json.dumps({"ok": True, "projects_checked": 1}))
    elif cmd == "record":
        index, result = opt("--index"), opt("--result")
        note("record %s %s" % (index, result))
        if result == "ok" and os.environ.get("FAKE_RECORD_FAIL") == index:
            print(json.dumps({"ok": False, "error": "write failed"}))
            sys.exit(1)
        print(json.dumps({"index": index, "status": result, "both_fresh": False}))
    elif cmd == "verify-recorded":
        note("verify-recorded")
        if os.environ.get("FAKE_VERIFY_RECORDED_FAIL"):
            print(json.dumps({"ok": False, "issues": ["not persisted"]}))
            sys.exit(1)
        print(json.dumps({"ok": True, "issues": []}))
    else:
        sys.exit(9)
''')


@pytest.fixture
def project(tmp_path):
    """A throwaway project: the real script, a stub helper, stub external tools, and no access to any real cache."""
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".venv" / "bin").mkdir(parents=True)
    wrapper = proj / ".venv" / "bin" / "python"          # a wrapper, not a symlink: a symlinked venv python would lose its packages
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    (proj / "graphify-out").mkdir()
    (proj / "graphify-out" / "graph.json").write_text(json.dumps({"nodes": [{"id": "audit_documents"}], "links": []}))
    for name in ("test_core.py", "test_api.py"):
        (proj / "tests" / name).write_text("def test_ok():\n    assert True\n")
    shutil.copy(ROOT / "scripts" / "checkpoint_refresh.sh", proj / "scripts" / "checkpoint_refresh.sh")
    (proj / "scripts" / "checkpoint_state.py").write_text(FAKE_HELPER)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "codebase-memory-mcp").write_text('#!/bin/sh\ncase "$*" in\n  *--version*) echo "cbm 0.0-test";;\n  *index_repository*) echo \'{"status":"indexed"}\';;\n'
                                               '  *index_status*) printf "status: ready\\nroot_path: x\\n";;\n  *search_graph*) echo audit_documents;;\n  *) echo ok;;\nesac\n')
    (stubs / "graphify").write_text('#!/bin/sh\ncase "$1" in --version) echo "graphify 0.0-test";; *) echo "updated";; esac\n')
    for stub in stubs.iterdir():
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    log = tmp_path / "calls.log"
    base = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "CHECKPOINT_STATE_DIR": str(state), "FAKE_CALL_LOG": str(log),
            "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}

    def run(**extra):
        env = {**base, **{k: v for k, v in extra.items()}}
        done = subprocess.run([BASH, str(proj / "scripts" / "checkpoint_refresh.sh"), "T-ISOLATED"], cwd=proj, env=env, capture_output=True, text=True, timeout=120)
        calls = log.read_text().splitlines() if log.exists() else []
        return done.returncode, done.stdout + done.stderr, calls
    return run


@needs_bash
def test_script_success_path_records_both_then_verifies_persistence(project):
    rc, out, calls = project()
    assert rc == 0 and "RESULT: INDEX_REFRESHED" in out, out[-800:]
    assert calls == ["record cbm ok", "record graphify ok", "verify-recorded"]


@needs_bash
def test_script_no_change_path_is_also_verified(project):
    rc, out, calls = project(FAKE_NEED="False")
    assert rc == 0 and "RESULT: NO_CHANGE" in out and calls[-1] == "verify-recorded"


@needs_bash
@pytest.mark.parametrize("failing,expected_calls", [("cbm", ["record cbm ok"]), ("graphify", ["record cbm ok", "record graphify ok"])])
def test_script_record_write_failure_is_not_success(project, failing, expected_calls):
    rc, out, calls = project(FAKE_RECORD_FAIL=failing)
    assert rc == 4, out[-800:]
    assert "RESULT: INDEX_REFRESHED" not in out and "RESULT: NO_CHANGE" not in out and "RESULT: BASELINE_NOT_PERSISTED" in out
    assert calls == expected_calls                        # nothing is attempted after the first failed write
    if failing == "graphify":
        assert "partially" in out.lower()


@needs_bash
def test_script_unverified_persistence_is_not_success(project):
    rc, out, calls = project(FAKE_VERIFY_RECORDED_FAIL="1")
    assert rc == 4 and "RESULT: INDEX_REFRESHED" not in out and "RESULT: BASELINE_NOT_PERSISTED" in out


@needs_bash
def test_script_failure_in_no_change_mode_is_also_nonzero(project):
    rc, out, calls = project(FAKE_NEED="False", FAKE_RECORD_FAIL="cbm")
    assert rc == 4 and "RESULT: NO_CHANGE" not in out


@needs_bash
def test_script_states_tokens_only_from_a_checked_value(project):
    rc, out, _ = project(FAKE_LLM_TOKENS="0")
    assert "llm_tokens=0 (counters in graph.json)" in [l for l in out.splitlines() if "llm_tokens=" in l][-1]
    rc, out, _ = project(FAKE_LLM_TOKENS="UNKNOWN")
    summary = [l for l in out.splitlines() if "llm_tokens=" in l][-1]
    assert "llm_tokens=UNKNOWN" in summary and "llm_tokens=0" not in summary and "verified" not in summary


def test_the_script_text_has_no_unconditional_zero_token_claim_and_documents_exit_4():
    text = (ROOT / "scripts" / "checkpoint_refresh.sh").read_text()
    assert "llm_tokens=0(verified" not in text
    assert "4 BASELINE_NOT_PERSISTED" in text or "4 baseline" in text.lower()
