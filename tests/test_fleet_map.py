"""Координаты, треки, остановки и прогнозы для реальной карты из БД."""
import time

import pytest
from fastapi.testclient import TestClient
from backend.api import app
from ndtp_ingestion.db_init import init_db, connect
from ndtp_ingestion.server import stamp


@pytest.fixture
def fleet_db(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "fleet.db"}')
    monkeypatch.setenv('ML_TIME_MODE', 'wall')
    init_db()
    return time.time()


def point(tr, timestamp, *, receive=None, valid=True, lat=55.75, lon=37.5, speed=20):
    with connect() as conn:
        conn.execute('INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (tr, tr+100, stamp(timestamp), stamp(timestamp if receive is None else receive), lon, lat, speed, valid))


def snapshot():
    with TestClient(app) as client:
        response = client.get('/vehicles/active')
        assert response.status_code == 200, response.text
        return response.json()


def test_positions_segments_and_missing_predictions(fleet_db):
    now = fleet_db
    point(1, now-300)
    point(1, now-290, valid=False)
    point(1, now-280)
    point(1, now-10, lat=55.76, lon=37.51)
    point(1, now-5, lat=55.77, lon=37.52)
    point(2, now-5, valid=False)
    point(3, now-5, receive=now+100)
    point(4, now-5, lat=99)
    point(5, now-121)
    row = snapshot()['vehicles']
    assert len(row) == 1
    row = row[0]
    assert row['position'] == [55.77, 37.52]  # Leaflet: latitude, longitude
    assert [len(segment) for segment in row['track']] == [1, 1, 2]
    assert row['prediction'] is None and row['stops'] == []
    assert row['current_delay_s'] is None and not row['has_schedule']


def test_stops_stored_prediction_and_read_only(fleet_db, monkeypatch):
    from ml.db import DBConfig, make_engine, write_predictions
    from ml.predictor import Prediction
    import ml.predictor
    now = fleet_db
    point(1, now-10)
    with connect() as conn:
        for stop, offset in [(1,-100),(2,700),(3,800),(4,1600)]:
            conn.execute('INSERT INTO schedule_plan VALUES (?, ?, ?, ?, ?, ?)',
                         (stop, 1, stamp(now+offset), False, 'POINT (37.5 55.75)', f'Остановка {stop}'))
    cfg = DBConfig(); engine = make_engine(cfg)
    try:
        prediction = Prediction(tr_id=1, T=now, predicted_at=now, target_stop_id=2,
                                target_time_plan=now+700, predicted_delay_s=87, interval_s=(60,100),
                                model_used='ensemble')
        write_predictions(engine,cfg,[prediction])
    finally:
        engine.dispose()
    def forbid(*args, **kwargs):
        raise AssertionError('HTTP не должен загружать модель')
    monkeypatch.setattr(ml.predictor, 'DelayPredictor', forbid)
    with connect() as conn:
        before = {table:conn.execute(f'SELECT * FROM {table}').fetchall() for table in ['telemetry','schedule_plan','predictions']}
    row = snapshot()['vehicles'][0]
    assert [s['id'] for s in row['stops']] == [2,3]
    assert row['stops'][0]['position'] == [55.75,37.5]
    assert row['prediction']['predicted_delay_s'] == 87
    assert row['prediction']['target_position'] == [55.75,37.5]
    assert row['prediction']['target_stop_name'] == 'Остановка 2'
    assert row['prediction']['status'] == 'ready'
    with connect() as conn:
        after = {table:conn.execute(f'SELECT * FROM {table}').fetchall() for table in before}
    assert before == after


def test_stale_prediction_remains_explicit(fleet_db):
    from ml.db import DBConfig, make_engine, write_predictions
    from ml.predictor import Prediction
    now = fleet_db
    point(1, now-5)
    engine = make_engine(DBConfig())
    try:
        write_predictions(engine, DBConfig(), [Prediction(tr_id=1, T=now-300, predicted_at=now-300,
            target_stop_id=2, target_time_plan=now+400, predicted_delay_s=500, interval_s=(300,600))])
    finally:
        engine.dispose()
    assert snapshot()['vehicles'][0]['prediction']['status'] == 'stale'


def test_historical_reference_is_explicit(fleet_db, monkeypatch):
    monkeypatch.setenv('ML_TIME_MODE', 'stream')
    point(1, 1767700800)
    result = snapshot()
    assert result['mode'] == 'historical'
    assert result['reference_time'].startswith('2026-01-06')
    assert len(result['vehicles']) == 1


def test_database_error_is_503(tmp_path, monkeypatch):
    path = tmp_path / 'invalid.db'
    path.write_bytes(b'not a sqlite database')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    with TestClient(app) as client:
        assert client.get('/vehicles/active').status_code == 503
