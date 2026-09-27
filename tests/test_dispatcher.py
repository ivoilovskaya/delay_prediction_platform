"""Главная карта использует FastAPI, статический replay больше не подключён."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.api import app


@pytest.mark.parametrize('path', ['/', '/dispatcher', '/dispatcher/'])
def test_dispatcher_page_and_local_assets(path):
    class Resources(HTMLParser):
        urls = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            url = attrs.get('src') if tag == 'script' else attrs.get('href') if tag in ('link', 'image') else None
            if url:
                self.urls.append(url)
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert 'СИТУАЦИЯ НА СЕТИ' in response.text
        assert 'Инциденты' in response.text
        assert 'moscow-transport-logo.png' in response.text
        parser = Resources()
        parser.feed(response.text)
        for resource in parser.urls:
            if not urlsplit(resource).netloc:
                assert client.get(urljoin(str(response.url), resource)).status_code == 200
        assert 'replay.json' not in response.text
        assert 'data-scenario=' not in response.text
        assert 'time-slider' not in response.text


@pytest.mark.parametrize('name', ['app.js', 'data.js', 'style.css', 'moscow-transport-logo.png'])
def test_dispatcher_assets(name):
    with TestClient(app) as client:
        response = client.get(f'/dispatcher/assets/{name}')
        assert response.status_code == 200
        assert response.content


def test_empty_database_never_creates_data(tmp_path, monkeypatch):
    path = tmp_path / 'absent.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/vehicles/active').json()['vehicles'] == []
        assert client.get('/dispatcher/replay').status_code == 404
    assert not path.exists()
