import copy
import json
import pytest
from app import core, report
from app.api import app
from app.mcp_server import compare_engineering_rows
from fastapi.testclient import TestClient

def pair(a, b):
    return core.audit_documents({'a': f'AA | value | 1 {a}', 'b': f'AA | value | 1 {b}'})

@pytest.mark.parametrize('unit', ['Tbbl','hbbl','microbbl','Kibbl','ubbl','μbbl','µbbl','hectobarrels','Mibbl','MMbbl','KBBL','Tbbl/day','m^3/microbbl','hbbl*m','Kibbl^2','bbl2'])
def test_barrel_tokens_refused(unit):
    text = f'AA | value | 1 {unit}'
    r = core.audit_documents({'a':text,'b':text})
    assert not r['matches'] and not r['findings']
    assert len(r['warnings']) == 2
    assert all(w['code']=='AMBIGUOUS_UNIT' and w['excerpt']==text and w['line']==1 for w in r['warnings'])

def test_registry_prefixes():
    # Finite installed-registry set, not a claim about arbitrary spelling.
    for prefix in core.UNITS._prefixes:
        for stem in ('bbl','barrel','bbls','barrels'):
            token=prefix+stem
            if core.UNITS.parse_unit_name(token):
                assert core.ambiguous_unit(token), token

@pytest.mark.parametrize('unit',['oil_barrel','oil_bbl','UK_bbl','imperial_barrel','beer_bbl','dry_barrel','kilooil_barrel','bar','barn','mbar','kbar'])
def test_explicit_or_unrelated_units(unit):
    assert core.ambiguous_unit(unit) is None
    assert pair(unit,unit)['matches']

@pytest.mark.parametrize('a,b',[('m**1.0000001','m'),('m**0.33333331','m**0.33333332'),('m**-1.0000001','m**-1')])
def test_fractional_dimensions_distinct(a,b):
    assert core.UNITS.get_dimensionality(a) != core.UNITS.get_dimensionality(b)
    r=pair(a,b)
    assert not r['matches'] and r['warnings'][0]['code']=='DIMENSION_MISMATCH'
    r=core.audit_documents({'a':f'AA | value | 1 {a}\nAA | value | 1 {b}','b':f'AA | value | 1 {b}'})
    assert not r['matches'] and any(w['code']=='AMBIGUOUS_COMPARISON' for w in r['warnings'])

def test_equality_does_not_depend_on_key(monkeypatch):
    monkeypatch.setattr(core,'dimension_key',lambda d:'collision')
    assert not pair('m','s')['matches']

def test_legacy_report_and_exports():
    r=core.audit_documents({'a':'AA | pressure | 10 bar','b':'AA | pressure | 900 kPa'})
    legacy=copy.deepcopy(r)
    for f in legacy['findings']:
        for side in ('reference','other'): f[side].pop('dimension_key')
    assert report.build_view(legacy)==report.build_view(r)
    assert 'Pressure basis' in report.render_markdown(report.build_view(legacy))
    assert json.loads(json.dumps(r))==r

def test_api_mcp_key_contract():
    docs={'spec':'AA | force | 1 lbf','bom':'AA | force | 4.4482216152605 N'}
    engine=core.audit_documents(docs)
    api=TestClient(app).post('/audit',json={'documents':docs}).json()
    mcp=compare_engineering_rows(docs['spec'],docs['bom'])
    assert engine==api==mcp
    assert engine['matches'][0]['reference']['dimension_key']=='[length]^1 [mass]^1 [time]^-2'
