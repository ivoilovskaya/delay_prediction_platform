"""Integration checks with physically separate SQLite files."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.api import app
from ml.db import DBConfig, make_engine, run_cycle
from ml.results_db import initialize
from ml.results_maintenance import cleanup as results_cleanup
from ndtp_ingestion.db_init import init_db, connect
from ndtp_ingestion.server import cleanup, decode, save_point, stamp
from test_ingestion import frame


@pytest.fixture
def databases(tmp_path, monkeypatch):
    source, results = tmp_path / 'input.db', tmp_path / 'results.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{source}')
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}')
    monkeypatch.setenv('ML_TIME_MODE', 'stream')
    init_db()
    initialize()
    now = int(time.time())
    with connect() as conn:
        for offset in range(-900, 1000, 90):
            conn.execute('INSERT INTO schedule_plan VALUES (?, ?, ?, ?, ?, ?)',
                         (offset + 1000, 42, stamp(now + offset), False, 'POINT (37.5 55.75)', 'Stop'))
    return source, results, now


def snapshot(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
        return '\n'.join(conn.iterdump())


def cycle():
    cfg = DBConfig()
    engine = make_engine(cfg)
    try:
        return run_cycle(None, engine, cfg)
    finally:
        engine.dispose()


def test_packet_cycle_api_and_reinitialization(databases):
    source, results, now = databases
    before = snapshot(results)
    save_point(decode(*frame(now), {'123': 42}, now))
    assert snapshot(results) == before
    with sqlite3.connect(source) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='predictions'").fetchall()
    before = snapshot(source)
    assert cycle()['written'] == 1
    assert snapshot(source) == before
    initialize()
    assert cycle()['written'] == 0
    before = (snapshot(source), snapshot(results))
    with TestClient(app) as client:
        response = client.get('/vehicles/active')
        assert response.status_code == 200, response.text
        vehicle = response.json()['vehicles'][0]
        assert vehicle['tr_id'] == vehicle['prediction']['tr_id'] == 42
        assert client.get('/predictions/latest').json()['predictions'][0]['tr_id'] == 42
        assert client.get('/predictions/42/latest').status_code == 200
    assert (snapshot(source), snapshot(results)) == before


def test_cleanup_ownership_and_historical_mode(databases, monkeypatch):
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    cycle()
    with sqlite3.connect(results) as conn:
        conn.execute('UPDATE predictions SET predicted_at=?', (stamp(now - 90000),))
    before = snapshot(results)
    cleanup(now + 100000)
    assert snapshot(results) == before
    assert results_cleanup(now) == 0
    assert snapshot(results) == before
    before = snapshot(source)
    monkeypatch.setenv('ML_TIME_MODE', 'wall')
    assert results_cleanup(now) == 1
    assert snapshot(source) == before


def test_missing_results_and_recovery(databases):
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    results.unlink()
    with TestClient(app) as client:
        for endpoint in ['/vehicles/active', '/predictions/latest', '/predictions/42/latest']:
            assert client.get(endpoint).status_code == 503
    with pytest.raises(OperationalError):
        cycle()
    assert not results.exists()
    initialize()
    assert cycle()['written'] == 1


def test_locked_results_and_recovery(databases, monkeypatch):
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}?timeout=0.01')
    with sqlite3.connect(results) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with pytest.raises(OperationalError, match='locked'):
            cycle()
        lock.rollback()
    assert cycle()['written'] == 1
    assert cycle()['written'] == 0


def test_manual_service_uses_results(databases, monkeypatch):
    from ml import service
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    monkeypatch.setattr(service, 'get_predictor', lambda: None)
    with TestClient(service.app) as client:
        response = client.post('/predict/from-db')
        assert response.status_code == 200, response.text
        assert response.json()['written'] == 1
        assert client.post('/predict/from-db').json()['written'] == 0
    with sqlite3.connect(source) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='predictions'").fetchall()


def test_verified_migration(databases, monkeypatch, tmp_path):
    from ml.migrate_predictions import migrate
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    # Produce a legacy single-file prediction.
    monkeypatch.delenv('RESULTS_DATABASE_URL')
    cycle()
    before = snapshot(source)
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}')
    assert migrate(f'sqlite:///{source}') == {'source_rows_verified': 1, 'inserted': 1}
    assert migrate(f'sqlite:///{source}') == {'source_rows_verified': 1, 'inserted': 0}
    assert snapshot(source) == before
    with sqlite3.connect(results) as conn:
        conn.execute('UPDATE predictions SET predicted_delay_s=999')
    before_target = snapshot(results)
    with pytest.raises(ValueError, match='Conflicting'):
        migrate(f'sqlite:///{source}')
    assert snapshot(results) == before_target


def test_worker_logs_failure_and_recovers(databases, monkeypatch, caplog):
    from ml import worker
    source, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    results.unlink()
    monkeypatch.setattr(worker, '_stop', False)
    monkeypatch.setattr(worker, 'load_predictor', lambda: None)
    monkeypatch.setattr('sys.argv', ['worker', '--interval', '0'])
    class Wake:
        calls = 0
        def wait(self, _):
            self.calls += 1
            if self.calls == 1:
                initialize()
            else:
                worker._stop = True
    monkeypatch.setattr(worker, '_wake', Wake())
    # Do not replace the test runner's real signal handlers.
    monkeypatch.setattr(worker.signal, 'signal', lambda *_: None)
    worker.main()
    assert 'цикл не выполнен' in caplog.text
    with sqlite3.connect(results) as conn:
        assert conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0] == 1


def test_uninitialized_results_returns_503_without_writes(databases):
    _, results, _ = databases
    with sqlite3.connect(results) as conn:
        conn.execute('DROP TABLE predictions')
    before = snapshot(results)
    with TestClient(app) as client:
        for endpoint in ['/vehicles/active', '/predictions/latest', '/predictions/42/latest']:
            assert client.get(endpoint).status_code == 503
    assert snapshot(results) == before


def test_results_reads_do_not_depend_on_input(databases, monkeypatch):
    _, results, now = databases
    save_point(decode(*frame(now), {'123': 42}, now))
    cycle()
    monkeypatch.setenv('DATABASE_URL', 'sqlite:////nonexistent/input.db')
    with TestClient(app) as client:
        assert client.get('/predictions/latest').status_code == 200
        assert client.get('/predictions/42/latest').status_code == 200
