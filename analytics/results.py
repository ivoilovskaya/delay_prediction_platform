"""Derived analytics tables in RESULTS_DATABASE_URL.

Run ``python -m analytics.results`` once before starting the analytics worker.
No table here belongs in the input database.
"""

from __future__ import annotations

import os

from sqlalchemy import Index, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, UniqueConstraint

from storage.database import DatabaseConfig as DBConfig
from storage.database import existing_engine


metadata = MetaData()
vehicle_state = Table(
    "vehicle_state", metadata,
    Column("tr_id", Integer, primary_key=True),
    Column("unit_id", Integer),
    Column("event_time", DateTime, nullable=False),
    Column("route_id", String(80)),
    Column("direction_id", String(40)),
    Column("segment_id", String(220)),
    Column("segment_order", Integer),
    Column("entered_at", DateTime),
    Column("segment_progress", Float),
    Column("raw_speed_kmh", Float),
    Column("smoothed_speed_kmh", Float),
    Column("speed_sum_kmh", Float, nullable=False),
    Column("speed_samples", Integer, nullable=False),
    Column("gps_age_sec", Float),
    Column("match_confidence", Float, nullable=False),
    Column("match_status", String(12), nullable=False),
    Column("calculation_version", String(20), nullable=False),
)
segment_passages = Table(
    "segment_passages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tr_id", Integer, nullable=False),
    Column("unit_id", Integer),
    Column("route_id", String(80), nullable=False),
    Column("direction_id", String(40), nullable=False),
    Column("segment_id", String(220), nullable=False),
    Column("entered_at", DateTime, nullable=False),
    Column("exited_at", DateTime, nullable=False),
    Column("travel_time_sec", Float, nullable=False),
    Column("mean_speed_kmh", Float),
    Column("data_quality", Float, nullable=False),
    Column("calculation_version", String(20), nullable=False),
    UniqueConstraint("tr_id", "segment_id", "entered_at"),
)
segment_baseline = Table(
    "segment_baseline", metadata,
    Column("segment_id", String(220), primary_key=True),
    Column("sample_count", Integer, nullable=False),
    Column("median_travel_time_sec", Float),
    Column("p25_travel_time_sec", Float),
    Column("p75_travel_time_sec", Float),
    Column("source", String(20), nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("calculation_version", String(20), nullable=False),
)
segment_state = Table(
    "segment_state", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("calculated_at", DateTime, nullable=False),
    Column("route_id", String(80), nullable=False),
    Column("route_name", String(160), nullable=False),
    Column("is_demo", Boolean, nullable=False),
    Column("direction_id", String(40), nullable=False),
    Column("segment_id", String(220), nullable=False),
    Column("from_name", String(160), nullable=False),
    Column("to_name", String(160), nullable=False),
    Column("from_lat", Float, nullable=False),
    Column("from_lon", Float, nullable=False),
    Column("to_lat", Float, nullable=False),
    Column("to_lon", Float, nullable=False),
    Column("vehicles_on_segment", Integer, nullable=False),
    Column("recent_completed_vehicles", Integer, nullable=False),
    Column("vehicles_considered", Integer, nullable=False),
    Column("vehicles_confirming_slowdown", Integer, nullable=False),
    Column("median_current_travel_time_sec", Float),
    Column("median_smoothed_speed_kmh", Float),
    Column("baseline_travel_time_sec", Float),
    Column("baseline_source", String(20), nullable=False),
    Column("slowdown_ratio", Float),
    Column("risk_index", Float),
    Column("confidence_score", Float, nullable=False),
    Column("confidence_level", String(10), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("calculation_version", String(20), nullable=False),
    UniqueConstraint("segment_id", "calculated_at"),
)
alerts = Table(
    "alerts", metadata,
    Column("alert_id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String(80), nullable=False),
    Column("direction_id", String(40), nullable=False),
    Column("segment_id", String(220), nullable=False),
    Column("alert_type", String(30), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("confidence_level", String(10), nullable=False),
    Column("current_value", Float),
    Column("baseline_value", Float),
    Column("vehicles_confirming", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("status", String(10), nullable=False),
    Column("calculation_version", String(20), nullable=False),
    UniqueConstraint("segment_id", "alert_type"),
)


# Retention scans must remain bounded as the history grows.
for table, column in ((vehicle_state, 'event_time'), (segment_passages, 'exited_at'),
                      (segment_baseline, 'updated_at'), (segment_state, 'calculated_at'),
                      (alerts, 'updated_at')):
    Index(f'ix_{table.name}_{column}', table.c[column])
Index('ix_segment_passages_segment_exit', segment_passages.c.segment_id, segment_passages.c.exited_at)


def initialize(cfg: DBConfig | None = None) -> None:
    cfg = cfg or DBConfig()
    if not cfg.results_url or cfg.results_url == cfg.url:
        raise ValueError("Analytics requires a separate RESULTS_DATABASE_URL")
    engine = existing_engine(cfg.results_url)
    try:
        with engine.begin() as conn:
            metadata.create_all(conn)
            for table in metadata.tables.values():
                for index in table.indexes:
                    index.create(conn, checkfirst=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    if not os.getenv("RESULTS_DATABASE_URL"):
        raise SystemExit("RESULTS_DATABASE_URL is required")
    initialize()
    print("Analytics tables initialized in the results database")
