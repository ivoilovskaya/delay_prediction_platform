"""Retention across the real results schema, cadence, failures and independence."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine

from analytics import results
from maintenance.results import POLICIES, Scheduler, cleanup, policies_from_env
from ml.db import DBConfig, predictions_table
from ml.results_db import initialize
from storage.database import DatabaseConfig

NOW = 1800000000.0


def dt(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None)


def row_for(table, number, timestamp, seconds):
    values = {}
    for column in table.columns:
        if isinstance(column.type, Boolean): value = False
        elif isinstance(column.type, DateTime): value = dt(NOW)
        elif isinstance(column.type, (Integer, Float)): value = number
        elif isinstance(column.type, String): value = f'row-{number}'
        else: raise AssertionError(column)
        values[column.name] = value
    values[timestamp] = dt(seconds)
    return values


@pytest.fixture
def databases(tmp_path, monkeypatch):
    source, target = tmp_path / 'input.db', tmp_path / 'results.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{source}')
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{target}?timeout=0.01')
    monkeypatch.setenv('DATA_TIME_MODE', 'wall')
    for i, (table, _, _) in enumerate(POLICIES, 1):
        monkeypatch.setenv(f'{table.upper()}_RETENTION_S', str(100 * i))
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE telemetry (event_time TEXT)')
        conn.execute("INSERT INTO telemetry VALUES ('2000-01-01')")
        conn.execute('CREATE TABLE schedule_plan (time_begin TEXT)')
    initialize()
    results.initialize()
    engine = create_engine(f'sqlite:///{target}')
    tables = {**results.metadata.tables, 'predictions': predictions_table(DBConfig())}
    with engine.begin() as conn:
        for i, (name, timestamp, _) in enumerate(POLICIES, 1):
            # Expired, exactly on boundary, fresh: boundary must survive.
            for number, seconds in enumerate([NOW - 100*i - 1, NOW - 100*i, NOW - 1], 1):
                conn.execute(tables[name].insert().values(**row_for(tables[name], number, timestamp, seconds)))
    engine.dispose()
    return source, target, tables


def counts(target):
    with sqlite3.connect(target) as conn:
        return {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name, _, _ in POLICIES}


def test_all_tables_use_their_own_retention_and_leave_input_untouched(databases):
    source, target, _ = databases
    before = source.read_bytes()
    assert cleanup(NOW) == {name: 1 for name, _, _ in POLICIES}
    assert counts(target) == {name: 2 for name, _, _ in POLICIES}
    assert cleanup(NOW) == {name: 0 for name, _, _ in POLICIES}
    assert source.read_bytes() == before


def test_updated_alert_survives_even_with_old_creation_time(databases):
    _, target, _ = databases
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE alerts SET created_at='2000-01-01', status='ACTIVE' WHERE alert_id=3")
    cleanup(NOW)
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT status FROM alerts WHERE alert_id=3').fetchone() == ('ACTIVE',)


def test_historical_mode_never_connects_or_deletes(databases, monkeypatch):
    _, target, _ = databases
    before = counts(target)
    monkeypatch.setenv('DATA_TIME_MODE', 'stream')
    monkeypatch.setenv('ML_TIME_MODE', 'wall')
    assert cleanup(NOW) == {}
    scheduler = Scheduler(DatabaseConfig())
    assert scheduler.tick(now=NOW) == {}
    assert scheduler.engine is None
    assert counts(target) == before
    monkeypatch.setenv('RESULTS_DATABASE_URL', 'sqlite:////no/such/file.db')
    assert cleanup(NOW) == {}


@pytest.mark.parametrize('invalid', ['0', '-1', 'nan', 'inf'])
def test_invalid_settings_fail_before_any_deletion(databases, monkeypatch, invalid):
    _, target, _ = databases
    monkeypatch.setenv('ALERTS_RETENTION_S', invalid)
    with pytest.raises(ValueError, match='ALERTS_RETENTION_S'):
        cleanup(NOW)
    assert all(value == 3 for value in counts(target).values())


def test_independent_table_intervals(databases, monkeypatch):
    _, target, tables = databases
    monkeypatch.setenv('PREDICTIONS_CLEANUP_INTERVAL_S', '10')
    monkeypatch.setenv('VEHICLE_STATE_CLEANUP_INTERVAL_S', '20')
    scheduler = Scheduler(DatabaseConfig())
    try:
        assert len(scheduler.tick(monotonic=10, now=NOW)) == 6
        engine = create_engine(f'sqlite:///{target}')
        with engine.begin() as conn:
            conn.execute(tables['predictions'].insert().values(**row_for(tables['predictions'], 4, 'predicted_at', NOW-1000)))
            conn.execute(tables['vehicle_state'].insert().values(**row_for(tables['vehicle_state'], 4, 'event_time', NOW-1000)))
        engine.dispose()
        assert scheduler.tick(monotonic=19, now=NOW) == {}
        assert scheduler.tick(monotonic=20, now=NOW) == {'predictions': 1}
        assert scheduler.tick(monotonic=30, now=NOW) == {'predictions': 0, 'vehicle_state': 1}
    finally:
        scheduler.close()


def test_missing_database_retries_without_creating_it(tmp_path, monkeypatch, caplog):
    path = tmp_path / 'missing.db'
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{path}')
    monkeypatch.setenv('DATA_TIME_MODE', 'wall')
    scheduler = Scheduler(DatabaseConfig())
    try:
        assert scheduler.tick(monotonic=1, now=NOW) == {}
        assert not path.exists()
        assert 'Cleanup failed' in caplog.text
        initialize()
        results.initialize()
        assert len(scheduler.tick(monotonic=32, now=NOW)) == 6
    finally:
        scheduler.close()


def test_lock_is_logged_and_retried(databases, caplog):
    _, target, _ = databases
    scheduler = Scheduler(DatabaseConfig())
    try:
        with sqlite3.connect(target) as lock:
            lock.execute('BEGIN EXCLUSIVE')
            assert scheduler.tick(monotonic=1, now=NOW) == {}
            lock.rollback()
        assert 'locked' in caplog.text
        assert scheduler.tick(monotonic=32, now=NOW) == {name: 1 for name, _, _ in POLICIES}
    finally:
        scheduler.close()


def test_one_broken_table_does_not_block_other_tables(databases, caplog):
    _, target, _ = databases
    with sqlite3.connect(target) as conn:
        conn.execute('ALTER TABLE predictions RENAME COLUMN predicted_at TO wrong_column')
    scheduler = Scheduler(DatabaseConfig())
    try:
        result = scheduler.tick(monotonic=1, now=NOW)
        assert 'predictions' not in result and len(result) == 5
        assert 'Cleanup failed for predictions' in caplog.text
    finally:
        scheduler.close()


def test_unknown_tables_are_reported_and_preserved(databases, caplog):
    _, target, _ = databases
    with sqlite3.connect(target) as conn:
        conn.execute('CREATE TABLE future_analytics (updated_at TEXT)')
        conn.execute("INSERT INTO future_analytics VALUES ('2000-01-01')")
    scheduler = Scheduler(DatabaseConfig())
    try:
        scheduler.tick(monotonic=1, now=NOW)
        assert 'No retention policy' in caplog.text
        with sqlite3.connect(target) as conn:
            assert conn.execute('SELECT count(*) FROM future_analytics').fetchone()[0] == 1
    finally:
        scheduler.close()


def test_maintenance_imports_no_model_modules():
    code = 'import maintenance.results, sys; assert not any(n == p or n.startswith(p+".") for n in sys.modules for p in ("ml", "analytics", "numpy", "pandas", "torch"))'
    subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], check=True)


def test_time_mode_legacy_fallback_and_override(monkeypatch):
    monkeypatch.delenv('DATA_TIME_MODE', raising=False)
    monkeypatch.setenv('ML_TIME_MODE', 'wall')
    assert DatabaseConfig().time_mode == DBConfig().time_mode == 'wall'
    monkeypatch.setenv('DATA_TIME_MODE', 'stream')
    assert DatabaseConfig().time_mode == DBConfig().time_mode == 'stream'


def test_resolved_alert_does_not_refresh_forever(databases):
    from analytics.worker import _state_for_segment, Settings
    _, target, _ = databases
    engine = create_engine(f'sqlite:///{target}')
    segment = dict(segment_id='empty', route_id='route', route_name='Route', is_demo=True,
                   direction_id='out', from_name='A', to_name='B', from_lat=55.7, from_lon=37.5,
                   to_lat=55.7, to_lon=37.51, planned_travel_sec=100)
    with engine.begin() as conn:
        conn.execute(results.alerts.insert().values(alert_id=10, route_id='route', direction_id='out',
            segment_id='empty', alert_type='SEGMENT_SLOWDOWN', severity='WARNING',
            confidence_level='HIGH', vehicles_confirming=3, created_at=dt(NOW-1000),
            updated_at=dt(NOW-900), status='ACTIVE', calculation_version='segment-v1'))
        _state_for_segment(conn, segment, NOW, Settings())
        _state_for_segment(conn, segment, NOW+60, Settings())
        row = conn.execute(results.alerts.select().where(results.alerts.c.alert_id==10)).mappings().one()
        assert row['status']=='RESOLVED' and row['updated_at']==dt(NOW)
    engine.dispose()


def test_single_database_compatibility_preserves_input_tables(databases, monkeypatch):
    _, target, _ = databases
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{target}')
    monkeypatch.delenv('RESULTS_DATABASE_URL')
    with sqlite3.connect(target) as conn:
        conn.execute('CREATE TABLE telemetry (event_time TEXT)')
        conn.execute("INSERT INTO telemetry VALUES ('2000-01-01')")
    assert cleanup(NOW) == {name: 1 for name, _, _ in POLICIES}
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT count(*) FROM telemetry').fetchone()[0] == 1


def test_analytics_stops_without_waiting_for_the_next_interval(monkeypatch):
    from analytics import worker
    handlers = {}
    monkeypatch.setattr(worker.signal, 'signal', lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(sys, 'argv', ['analytics.worker'])
    monkeypatch.setenv('ANALYTICS_INTERVAL_S', '600')
    def cycle(_):
        handlers[worker.signal.SIGTERM]()
        return {}
    monkeypatch.setattr(worker, 'run_cycle', cycle)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: pytest.fail('shutdown blocked by sleep'))
    worker.main()
