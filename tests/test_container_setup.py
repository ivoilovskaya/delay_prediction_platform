"""Container bootstrap survives repeat runs and failed first imports."""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.container_setup import prepare


@pytest.fixture
def paths(tmp_path, monkeypatch):
    source, results, runtime = tmp_path / 'input.db', tmp_path / 'results.db', tmp_path / 'runtime'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{source}')
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}')
    return source, results, runtime


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return '\n'.join(conn.iterdump())


def test_live_bootstrap_and_restart_preserve_plan_and_predictions(paths, monkeypatch):
    source, results, runtime = paths
    prepare('emulator', runtime, Path('/not-used'))
    config = json.loads((runtime / 'emulator.json').read_text())
    assert config['targetHost'] == 'ingestion'
    assert config['targetPort'] == 9000
    assert len(config['units']) == 3
    from ml.db import DBConfig, write_predictions
    from ml.predictor import Prediction
    from ml.results_db import existing_engine
    engine = existing_engine(f'sqlite:///{results}')
    try:
        write_predictions(engine, DBConfig(), [Prediction(tr_id=42, T=1000, target_stop_id=1,
            target_time_plan=1700, predicted_delay_s=10, interval_s=(0, 20))])
    finally:
        engine.dispose()
    before = snapshot(source), snapshot(results), (runtime / 'schedule.csv').read_bytes()
    monkeypatch.setattr('scripts.container_setup.demo_plan', lambda _: pytest.fail('plan regenerated'))
    prepare('emulator', runtime, Path('/not-used'))
    assert (snapshot(source), snapshot(results), (runtime / 'schedule.csv').read_bytes()) == before
    with sqlite3.connect(source) as conn:
        assert conn.execute('SELECT COUNT(*) FROM schedule_plan').fetchone()[0] == 303
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='predictions'").fetchall()


def test_failed_import_does_not_publish_partial_database(paths, monkeypatch):
    source, results, runtime = paths
    def fail(_):
        raise ValueError('bad CSV')
    monkeypatch.setattr('scripts.container_setup.load_schedule', fail)
    with pytest.raises(ValueError, match='bad CSV'):
        prepare('emulator', runtime, Path('/not-used'))
    assert not source.exists()
    assert not results.exists()


def test_existing_incomplete_file_is_not_overwritten(paths):
    source, results, runtime = paths
    source.touch()
    with pytest.raises(sqlite3.OperationalError):
        prepare('historical', runtime, Path('/not-used'))
    assert source.stat().st_size == 0


def test_historical_import_runs_only_once(paths, monkeypatch):
    source, results, runtime = paths
    calls = []
    def fake_import(command, **kwargs):
        calls.append(command)
        from ndtp_ingestion.db_init import init_db
        init_db()
    monkeypatch.setattr('scripts.container_setup.subprocess.run', fake_import)
    prepare('historical', runtime, Path('/dataset'))
    prepare('historical', runtime, Path('/dataset'))
    assert len(calls) == 1
    assert calls[0][2] == 'ml.csv_to_db'


def test_emulator_configuration_retries_transient_failure(tmp_path, monkeypatch):
    from scripts.configure_emulator import configure
    config = tmp_path / 'emulator.json'
    config.write_text('{"targetHost":"ingestion","targetPort":9000,"units":[{}]}')
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b'{}'
    def urlopen(request, **_):
        requests.append(request)
        if len(requests) == 1:
            raise ConnectionRefusedError('not ready')
        return Response()
    monkeypatch.setattr('scripts.configure_emulator.urllib.request.urlopen', urlopen)
    monkeypatch.setattr('scripts.configure_emulator.time.sleep', lambda _: None)
    configure('http://emulator:18080', config)
    assert len(requests) == 2
    assert json.loads(requests[-1].data)['targetHost'] == 'ingestion'


def test_emulator_invalid_config_fails_without_retry(tmp_path, monkeypatch):
    from scripts.configure_emulator import configure
    from urllib.error import HTTPError
    config = tmp_path / 'emulator.json'
    config.write_text('{}')
    def fail(*_, **kwargs):
        raise HTTPError('http://emulator:18080', 400, 'invalid config', {}, None)
    monkeypatch.setattr('scripts.configure_emulator.urllib.request.urlopen', fail)
    with pytest.raises(HTTPError):
        configure('http://emulator:18080', config)
