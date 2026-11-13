import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('checkpoint_state',Path(__file__).parents[1]/'scripts/checkpoint_state.py')
cs=importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)

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

def test_markdown_is_relevant_and_corrupt_state_fails_closed(tmp_path,capsys):
    root,a=setup(tmp_path); baseline(a,capsys)
    (root/'guide.md').write_text('# Guide\nText')
    assert plan(a,capsys)['need_graphify']
    Path(a.state_dir,'state.json').write_text('{}')
    assert plan(a,capsys)['baseline']=='CORRUPT_BASELINE'

def test_stale_manifest_cannot_be_fresh(tmp_path):
    root,a=setup(tmp_path)
    out=root/'graphify-out'; out.mkdir()
    (out/'manifest.json').write_text(json.dumps({'a.py':{'ast_hash':'wrong'}}))
    assert cs.graphify_inputs(str(root),verify=True)['issues']
