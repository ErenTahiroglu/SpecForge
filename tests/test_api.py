from fastapi.testclient import TestClient
from app.api import app


def test_health_and_audit():
    client = TestClient(app)
    assert client.get('/health').status_code == 200
    r = client.post('/audit', json={'documents': {'a': 'P-101 | pressure | 10 bar', 'b': 'P-101 | pressure | 900 kPa'}})
    assert r.status_code == 200
    assert r.json()['status'] == 'DIFFERENCES'
