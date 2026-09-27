"""A small GPS replay through the separate input and results databases."""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from analytics import results
from analytics.routes import import_route
from analytics.worker import run_cycle
from backend.api import app
from ml.db import DBConfig
from ml.results_db import initialize as initialize_ml_results
from ndtp_ingestion.db_init import init_db


BASE = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def test_passage_and_api_keep_input_read_only(tmp_path, monkeypatch):
    input_file, output_file = tmp_path / "input.db", tmp_path / "results.db"
    source_url, results_url = f"sqlite:///{input_file}", f"sqlite:///{output_file}"
    monkeypatch.setenv("DATABASE_URL", source_url)
    monkeypatch.setenv("RESULTS_DATABASE_URL", results_url)
    init_db()
    initialize_ml_results(DBConfig())
    results.initialize(DBConfig())
    import_route({
        "route_id": "m1", "route_name": "Тестовый маршрут", "is_demo": True,
        "directions": [{"direction_id": "outbound", "stops": [
            {"stop_id": "a", "name": "А", "lat": 55.75, "lon": 37.5},
            {"stop_id": "b", "name": "Б", "lat": 55.75, "lon": 37.51},
            {"stop_id": "c", "name": "В", "lat": 55.75, "lon": 37.52},
        ], "planned_travel_sec": [100, 100]}],
        "assignments": [{"tr_id": 42, "direction_id": "outbound",
                         "valid_from": BASE.isoformat(),
                         "valid_to": (BASE + timedelta(hours=1)).isoformat()}],
    }, source_url)
    with sqlite3.connect(input_file) as db:
        for i, progress in enumerate((.05, .15, .3, .5, .7, .9, 1.05, 1.2, 1.4, 1.6)):
            db.execute("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (42, 142, stamp(i * 30), stamp(i * 30 + 1),
                        37.5 + progress * .01, 55.75, 16, 1))
        db.commit()
        before = db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    cfg = DBConfig()
    early = (BASE + timedelta(seconds=150)).timestamp()
    end = (BASE + timedelta(seconds=280)).timestamp()
    assert run_cycle(early, cfg)["segments"] == 2
    assert run_cycle(end, cfg)["segments"] == 2
    with sqlite3.connect(input_file) as db:
        assert db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == before
        assert db.execute("SELECT name FROM sqlite_master WHERE name='segment_state'").fetchone() is None
    engine = create_engine(results_url)
    with engine.connect() as db:
        passages = db.execute(select(results.segment_passages)).mappings().all()
        assert len(passages) == 1
        assert passages[0]["segment_id"] == "m1:outbound:0"
        assert passages[0]["travel_time_sec"] > 0
        state = db.execute(select(results.segment_state).where(
            results.segment_state.c.segment_id == "m1:outbound:0").order_by(
            results.segment_state.c.calculated_at.desc())).mappings().first()
        assert state["baseline_source"] == "planned"
        assert state["risk_index"] is not None
    with TestClient(app) as client:
        response = client.get("/analytics/segments")
        assert response.status_code == 200
        assert {row["segment_id"] for row in response.json()["segments"]} == {
            "m1:outbound:0", "m1:outbound:1"}
        assert client.get("/analytics/alerts").status_code == 200
    # The current ten-minute window is never used to establish its own norm.
    with sqlite3.connect(output_file) as db:
        for tr_id, travel in ((43, 110), (44, 130)):
            db.execute("""INSERT INTO segment_passages
                (tr_id, route_id, direction_id, segment_id, entered_at, exited_at,
                 travel_time_sec, data_quality, calculation_version)
                VALUES (?, 'm1', 'outbound', 'm1:outbound:0', ?, ?, ?, 0.9, 'segment-v1')""",
                       (tr_id, stamp(120), stamp(120 + travel), travel))
        db.commit()
    run_cycle((BASE + timedelta(seconds=900)).timestamp(), cfg)
    with sqlite3.connect(output_file) as db:
        assert db.execute("SELECT source, median_travel_time_sec FROM segment_baseline WHERE segment_id='m1:outbound:0'").fetchone() == ('observed', 110.0)


def test_unassigned_vehicle_stays_unknown(tmp_path, monkeypatch):
    input_file, output_file = tmp_path / "input.db", tmp_path / "results.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{input_file}")
    monkeypatch.setenv("RESULTS_DATABASE_URL", f"sqlite:///{output_file}")
    init_db()
    initialize_ml_results(DBConfig())
    results.initialize(DBConfig())
    with sqlite3.connect(input_file) as db:
        for i in range(3):
            db.execute("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (99, 199, stamp(i * 30), stamp(i * 30 + 1),
                        37.5 + i * .001, 55.75, 12, 1))
        db.commit()
    run_cycle((BASE + timedelta(seconds=90)).timestamp(), DBConfig())
    with sqlite3.connect(output_file) as db:
        assert db.execute("SELECT match_status FROM vehicle_state WHERE tr_id=99").fetchone()[0] == "UNKNOWN"
        assert db.execute("SELECT COUNT(*) FROM segment_passages").fetchone()[0] == 0
