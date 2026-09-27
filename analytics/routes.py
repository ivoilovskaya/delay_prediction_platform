"""Explicitly import a manually described route into the input database.

The analytics worker only reads these tables. This command is the separate,
intentional administrative write path for route definitions and assignments.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select


metadata = MetaData()
routes = Table(
    "routes", metadata,
    Column("route_id", String(80), primary_key=True),
    Column("route_name", String(160), nullable=False),
    Column("is_demo", Boolean, nullable=False),
)
route_stops = Table(
    "route_stops", metadata,
    Column("route_id", String(80), ForeignKey("routes.route_id"), primary_key=True),
    Column("direction_id", String(40), primary_key=True),
    Column("stop_order", Integer, primary_key=True),
    Column("stop_id", String(80), nullable=False),
    Column("stop_name", String(160), nullable=False),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    UniqueConstraint("route_id", "direction_id", "stop_id"),
    CheckConstraint("stop_order >= 0"),
)
route_segments = Table(
    "route_segments", metadata,
    Column("segment_id", String(220), primary_key=True),
    Column("route_id", String(80), ForeignKey("routes.route_id"), nullable=False),
    Column("direction_id", String(40), nullable=False),
    Column("segment_order", Integer, nullable=False),
    Column("from_stop_id", String(80), nullable=False),
    Column("to_stop_id", String(80), nullable=False),
    Column("from_name", String(160), nullable=False),
    Column("to_name", String(160), nullable=False),
    Column("from_lat", Float, nullable=False),
    Column("from_lon", Float, nullable=False),
    Column("to_lat", Float, nullable=False),
    Column("to_lon", Float, nullable=False),
    Column("planned_travel_sec", Float),
    UniqueConstraint("route_id", "direction_id", "segment_order"),
)
vehicle_route_assignments = Table(
    "vehicle_route_assignments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tr_id", Integer, nullable=False, index=True),
    Column("route_id", String(80), ForeignKey("routes.route_id"), nullable=False),
    Column("direction_id", String(40), nullable=False),
    Column("valid_from", DateTime, nullable=False),
    Column("valid_to", DateTime, nullable=False),
    CheckConstraint("valid_to > valid_from"),
)


def _time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Assignment times must include a UTC offset")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _stop(value: dict, order: int) -> dict:
    lat, lon = float(value["lat"]), float(value["lon"])
    if not 55 <= lat <= 57 or not 36 <= lon <= 39:
        raise ValueError("Stop coordinates must be in the Moscow area")
    return dict(stop_order=order, stop_id=str(value["stop_id"]),
                stop_name=str(value["name"]), lat=lat, lon=lon)


def import_route(document: dict, input_url: str) -> None:
    """Add one route; reject replacement and overlapping assignments."""
    route_id = str(document["route_id"])
    if not route_id or ":" in route_id:
        raise ValueError("route_id must be nonempty and cannot contain ':'")
    directions = document["directions"]
    if not directions:
        raise ValueError("At least one direction is required")
    stop_rows, segment_rows = [], []
    seen = set()
    for direction in directions:
        direction_id = str(direction["direction_id"])
        if not direction_id or ":" in direction_id or direction_id in seen:
            raise ValueError("direction_id must be unique and cannot contain ':'")
        seen.add(direction_id)
        stops = [_stop(value, order) for order, value in enumerate(direction["stops"])]
        if len(stops) < 2 or len({s["stop_id"] for s in stops}) != len(stops):
            raise ValueError("A direction needs at least two distinct stable stop IDs")
        planned = direction.get("planned_travel_sec", [None] * (len(stops) - 1))
        if len(planned) != len(stops) - 1:
            raise ValueError("planned_travel_sec needs one value per segment")
        for stop in stops:
            stop_rows.append(dict(route_id=route_id, direction_id=direction_id, **stop))
        for order, (a, b, duration) in enumerate(zip(stops, stops[1:], planned)):
            if duration is not None and not 0 < float(duration) < 7200:
                raise ValueError("Planned travel time must be between 0 and 7200 seconds")
            segment_rows.append(dict(segment_id=f"{route_id}:{direction_id}:{order}",
                                     route_id=route_id, direction_id=direction_id,
                                     segment_order=order, from_stop_id=a["stop_id"],
                                     to_stop_id=b["stop_id"], from_name=a["stop_name"],
                                     to_name=b["stop_name"], from_lat=a["lat"], from_lon=a["lon"],
                                     to_lat=b["lat"], to_lon=b["lon"],
                                     planned_travel_sec=float(duration) if duration is not None else None))
    assignments = []
    for item in document.get("assignments", []):
        if item["direction_id"] not in seen:
            raise ValueError("Assignment references an unknown direction")
        start, end = _time(item["valid_from"]), _time(item["valid_to"])
        if end <= start:
            raise ValueError("Assignment valid_to must be later than valid_from")
        assignments.append(dict(tr_id=int(item["tr_id"]), route_id=route_id,
                                direction_id=item["direction_id"], valid_from=start, valid_to=end))
    for i, a in enumerate(assignments):
        if any(a["tr_id"] == b["tr_id"] and a["valid_from"] < b["valid_to"]
               and b["valid_from"] < a["valid_to"] for b in assignments[:i]):
            raise ValueError("Overlapping assignments for the same tr_id")
    engine = create_engine(input_url)
    try:
        with engine.begin() as conn:
            metadata.create_all(conn)
            if conn.execute(select(routes.c.route_id).where(routes.c.route_id == route_id)).first():
                raise ValueError(f"Route {route_id} already exists; no data was replaced")
            for item in assignments:
                overlap = conn.execute(select(vehicle_route_assignments.c.id).where(
                    vehicle_route_assignments.c.tr_id == item["tr_id"],
                    vehicle_route_assignments.c.valid_from < item["valid_to"],
                    vehicle_route_assignments.c.valid_to > item["valid_from"])).first()
                if overlap:
                    raise ValueError(f"Overlapping assignment for tr_id {item['tr_id']}")
            conn.execute(routes.insert().values(route_id=route_id,
                                                 route_name=str(document["route_name"]),
                                                 is_demo=bool(document.get("is_demo", False))))
            conn.execute(route_stops.insert(), stop_rows)
            conn.execute(route_segments.insert(), segment_rows)
            if assignments:
                conn.execute(vehicle_route_assignments.insert(), assignments)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")
    import_route(json.loads(args.file.read_text(encoding="utf-8")), url)
    print("Route imported into the input database")


if __name__ == "__main__":
    main()
