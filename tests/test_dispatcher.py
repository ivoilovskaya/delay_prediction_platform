"""Dispatcher replay remains available alongside the current ML API."""

import pytest
from fastapi.testclient import TestClient

from backend.api import app


@pytest.mark.parametrize('path', ['/dispatcher', '/dispatcher/'])
def test_dispatcher_page(path):
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert 'text/html' in response.headers['content-type']
        assert 'ЗОНА ОТВЕТСТВЕННОСТИ' in response.text


@pytest.mark.parametrize('name', ['app.js', 'style.css', 'moscow-transport-logo.png'])
def test_dispatcher_assets(name):
    with TestClient(app) as client:
        response = client.get(f'/dispatcher/assets/{name}')
        assert response.status_code == 200
        assert response.content


def test_replay_is_available_without_prediction_database(tmp_path, monkeypatch):
    path = tmp_path / 'absent.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        assert client.get('/').status_code == 200
        assert client.get('/predictions/latest').json() == {
            'status': 'unavailable', 'predictions': [],
        }
        response = client.get('/dispatcher/replay')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/json')
        replay = response.json()
        assert replay['frames'][0]['vehicles']
        vehicle = replay['frames'][0]['vehicles'][0]
        assert vehicle['id']
        assert len(vehicle['position']) == 2
        assert client.get('/dispatcher/replay').json() == replay
    assert not path.exists()
