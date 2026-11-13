import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('checkpoint_state',Path(__file__).parents[1]/'scripts/checkpoint_state.py')
cs=importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)

# These three tests drive the real external `graphify` executable (cs.graphify_inputs uses the installed updater). They
# are skipped ONLY when that executable is not on PATH. An installed but broken graphify still fails them. The fail-closed
# behaviour without graphify is covered below by tests that need no executable.
needs_graphify = pytest.mark.skipif(shutil.which("graphify") is None,
                                    reason="the external graphify CLI is not installed; fail-closed behaviour is tested separately")

def setup(tmp_path):
    root=tmp_path/'repo'; root.mkdir()
    (root/'a.py').write_text('def example():\n    return 1\n')
    (root/'notes.txt').write_text('documentation')
    state=tmp_path/'state'
    artifact=tmp_path/'artifact'; artifact.write_text('{}')
    args=SimpleNamespace(project_dir=str(root),state_dir=str(state),versions='installed',flags='AST',
                         cbm_artifact=str(artifact),graphify_artifact=str(artifact),scan_out=str(tmp_path/'scan.json'))
    return root,args

def plan(a,capsys):
    assert cs.cmd_plan(a)==0
    return json.loads(capsys.readouterr().out)

def record(a,index,result='ok'):
    cs.cmd_record(SimpleNamespace(state_dir=a.state_dir,index=index,scan_file=a.scan_out,
                                 result=result,checkpoint='controlled',detail='test',nodes=1,edges=0))

def baseline(a,capsys):
    plan(a,capsys)
    for i in ('cbm','graphify'): record(a,i)
    capsys.readouterr()

@needs_graphify
def test_controlled_changes(tmp_path,capsys):
    root,a=setup(tmp_path)
    baseline(a,capsys)
    p=plan(a,capsys); assert not p['need_cbm'] and not p['need_graphify']  # NO_CHANGE
    (root/'a.py').write_text('def example():\n    return 2\n')
    p=plan(a,capsys); assert p['need_cbm'] and p['need_graphify']
    baseline(a,capsys)
    (root/'.graphifyignore').write_text('excluded/')
    p=plan(a,capsys); assert p['need_graphify'] and p['need_cbm']
    baseline(a,capsys)
    (root/'notes.txt').write_text('excluded from AST document extraction')
    p=plan(a,capsys); assert not p['need_graphify']
    assert p['indexes']['graphify']['reason']=='EXCLUDED_INPUT_ONLY_NO_REBUILD'
    for i in ('cbm','graphify'): record(a,i)
    capsys.readouterr()
    p=plan(a,capsys); assert not p['need_cbm'] and not p['need_graphify'] # subsequent NO_CHANGE
    (root/'a.py').write_text('def changed():\n    return 3\n')
    plan(a,capsys)
    before=cs.load_state(a.state_dir)[0]['last_complete']
    record(a,'graphify','failed'); capsys.readouterr()
    p=plan(a,capsys); assert p['need_graphify']
    state=cs.load_state(a.state_dir)[0]
    assert state['last_complete']==before and state['graphify']['status']=='failed'

@needs_graphify
def test_markdown_is_relevant_and_corrupt_state_fails_closed(tmp_path,capsys):
    root,a=setup(tmp_path); baseline(a,capsys)
    (root/'guide.md').write_text('# Guide\nText')
    assert plan(a,capsys)['need_graphify']
    Path(a.state_dir,'state.json').write_text('{}')
    assert plan(a,capsys)['baseline']=='CORRUPT_BASELINE'

@needs_graphify
def test_stale_manifest_cannot_be_fresh(tmp_path):
    root,a=setup(tmp_path)
    out=root/'graphify-out'; out.mkdir()
    (out/'manifest.json').write_text(json.dumps({'a.py':{'ast_hash':'wrong'}}))
    assert cs.graphify_inputs(str(root),verify=True)['issues']


# ---------------------------------------------------------------------------------------------------------------------
# Dependency-free: graphify genuinely unavailable (simulated). These run everywhere, with or without graphify installed.
SEED = {"schema": cs.SCHEMA, "last_complete": {"fingerprint": "SEED-FP", "checkpoint": "SEED", "at": "t0"},
        "cbm": {"status": "fresh", "fingerprint": "x", "manifest": {}, "checkpoint": "SEED"},
        "graphify": {"status": "fresh", "fingerprint": "y", "manifest": {}, "checkpoint": "SEED"}}


@pytest.fixture
def no_graphify(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    assert shutil.which("graphify") is None


def seeded(tmp_path):
    root, a = setup(tmp_path)
    Path(a.state_dir).mkdir()
    state_file = Path(a.state_dir, "state.json")
    state_file.write_text(json.dumps(SEED, sort_keys=True))
    return root, a, state_file, state_file.read_bytes()


def test_missing_graphify_is_detected_by_graphify_inputs(tmp_path, no_graphify):
    root, _ = setup(tmp_path)
    for verify in (False, True):
        with pytest.raises(RuntimeError, match="graphify unavailable"):
            cs.graphify_inputs(str(root), verify=verify)


def test_missing_graphify_makes_plan_fail_without_a_plan_or_state_change(tmp_path, no_graphify, capsys):
    root, a, state_file, before = seeded(tmp_path)
    with pytest.raises(RuntimeError, match="graphify unavailable"):
        cs.cmd_plan(a)
    assert capsys.readouterr().out == ""                       # no plan JSON was printed
    assert not Path(a.scan_out).exists()                       # no scan/fingerprint file was written
    assert state_file.read_bytes() == before                   # last_complete and both index records untouched
    assert cs.load_state(a.state_dir)[0]["last_complete"] == SEED["last_complete"]


def test_missing_graphify_helper_exits_nonzero_without_plan_or_state_change(tmp_path):
    """The real command line, in a process whose PATH has no graphify (the shell treats a non-zero plan as PLAN_FAILED)."""
    root, a, state_file, before = seeded(tmp_path)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    env = {"PATH": str(empty), "PYTHONDONTWRITEBYTECODE": "1"}
    probe = subprocess.run([sys.executable, "-c", "import shutil,sys;sys.exit(1 if shutil.which('graphify') else 0)"],
                           env=env, capture_output=True)
    assert probe.returncode == 0                               # graphify really is absent from this environment
    p = subprocess.run([sys.executable, str(Path(cs.__file__)), "plan", "--project-dir", str(root), "--state-dir", a.state_dir,
                        "--versions", "v", "--flags", "f", "--cbm-artifact", a.cbm_artifact,
                        "--graphify-artifact", a.graphify_artifact, "--scan-out", a.scan_out],
                       env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "graphify unavailable" in p.stderr
    assert p.stdout.strip() == "" and not Path(a.scan_out).exists()
    assert state_file.read_bytes() == before


def test_missing_graphify_never_reports_graphify_fresh(tmp_path, no_graphify, capsys):
    root, a, state_file, before = seeded(tmp_path)
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"nodes": [{"source_file": "a.py"}], "links": []}))
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"graphify_manifest": {"a.py": "h"}}))
    rc = cs.cmd_verify_graphify(SimpleNamespace(project_dir=str(root), graph=str(graph), scan_file=str(scan), since=0))
    out = json.loads(capsys.readouterr().out)
    assert rc != 0 and out["ok"] is False
    assert any("graphify unavailable" in i for i in out["issues"])
    assert state_file.read_bytes() == before                   # verification never writes or advances state


def test_missing_graphify_does_not_create_a_baseline_from_nothing(tmp_path, no_graphify, capsys):
    root, a = setup(tmp_path)                                  # no state at all
    with pytest.raises(RuntimeError):
        cs.cmd_plan(a)
    assert not Path(a.state_dir, "state.json").exists() and not Path(a.scan_out).exists()
