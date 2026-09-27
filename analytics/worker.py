"""Calculate route-segment conditions from past GPS, writing only to Base 2.

For a historical replay, call ``python -m analytics.worker --once --at ISO_TIME``
with increasing times. Each run sees only telemetry available by that time.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, inspect, select, text

from analytics import results, routes
from ml.db import DBConfig, _param, _q, current_T
from storage.database import existing_engine

log = logging.getLogger("analytics.worker")
VERSION = "segment-v1"


@dataclass(frozen=True)
class Settings:
    corridor_m: float = 150.0
    active_s: float = 120.0
    recent_s: float = 600.0
    slowdown_ratio: float = 1.4
    min_baseline_n: int = 3
    min_confirming: int = 2

    @classmethod
    def from_env(cls):
        values = cls(float(os.getenv("ANALYTICS_CORRIDOR_M", "150")),
                     float(os.getenv("ANALYTICS_ACTIVE_S", "120")),
                     float(os.getenv("ANALYTICS_RECENT_S", "600")),
                     float(os.getenv("ANALYTICS_SLOWDOWN_RATIO", "1.4")),
                     int(os.getenv("ANALYTICS_MIN_BASELINE_N", "3")),
                     int(os.getenv("ANALYTICS_MIN_CONFIRMING", "2")))
        if (not all(math.isfinite(x) and x > 0 for x in
                    (values.corridor_m, values.active_s, values.recent_s, values.slowdown_ratio))
                or values.min_baseline_n < 1 or values.min_confirming < 1):
            raise ValueError("Invalid analytics settings")
        return values


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)


def _project(point: tuple[float, float], segment: dict) -> tuple[float, float, float]:
    """Return distance in metres, progress and signed movement axis for a stop line."""
    lat, lon = point
    cos_lat = math.cos(math.radians((segment["from_lat"] + segment["to_lat"]) / 2))
    scale = 111_320.0
    vx = (segment["to_lon"] - segment["from_lon"]) * cos_lat * scale
    vy = (segment["to_lat"] - segment["from_lat"]) * scale
    px = (lon - segment["from_lon"]) * cos_lat * scale
    py = (lat - segment["from_lat"]) * scale
    length2 = vx * vx + vy * vy
    if length2 < 2500:  # stops closer than 50 m cannot form a reliable segment
        return float("inf"), 0.0, 0.0
    progress = (px * vx + py * vy) / length2
    clipped = min(1.0, max(0.0, progress))
    distance = math.hypot(px - clipped * vx, py - clipped * vy)
    return distance, clipped, progress


def match_segment(points: list[dict], segments: list[dict], settings: Settings) -> tuple[dict | None, float | None, float]:
    """Use three recent valid points; reject ambiguous stop-line matches."""
    if len(points) < 3 or points[-1]["t"] - points[-3]["t"] > settings.active_s:
        return None, None, 0.0
    votes = defaultdict(list)
    for point in points[-3:]:
        candidates = sorted(((_project((point["lat"], point["lon"]), segment)[0], segment)
                             for segment in segments), key=lambda item: item[0])
        if not candidates or candidates[0][0] > settings.corridor_m:
            return None, None, 0.0
        best_dist, best = candidates[0]
        if len(candidates) > 1 and candidates[1][0] - best_dist < 15:
            # A junction is ambiguous until the bus moves away from it.
            return None, None, 0.0
        votes[best["segment_id"]].append(best_dist)
    winner, distances = max(votes.items(), key=lambda item: len(item[1]))
    if len(distances) < 2:
        return None, None, 0.0
    segment = next(s for s in segments if s["segment_id"] == winner)
    last_distance, progress, _ = _project((points[-1]["lat"], points[-1]["lon"]), segment)
    if last_distance > settings.corridor_m:
        return None, None, 0.0
    start_progress = _project((points[-3]["lat"], points[-3]["lon"]), segment)[2]
    end_progress = _project((points[-1]["lat"], points[-1]["lon"]), segment)[2]
    if end_progress < start_progress - 0.1:
        return None, None, 0.0
    quality = max(0.0, 1.0 - statistics.median(distances) / settings.corridor_m)
    return segment, progress, round(quality, 3)


def _valid(row: dict, previous: dict | None, T: float) -> bool:
    try:
        lat, lon, speed = float(row["lat"]), float(row["lon"]), float(row["speed"])
    except (ValueError, TypeError):
        return False
    if (str(row["location_valid"]).lower() not in ("true", "1", "t")
            or not 55 <= lat <= 57 or not 36 <= lon <= 39
            or not 0 <= speed <= 120 or row["t"] > T
            or row["received"] > T):
        return False
    if previous:
        elapsed = row["t"] - previous["t"]
        if elapsed <= 0:
            return False
        north = (lat - previous["lat"]) * 111_320
        east = (lon - previous["lon"]) * 111_320 * math.cos(math.radians(lat))
        if math.hypot(north, east) / elapsed > 120 / 3.6:
            return False
    return True


def _assignment(assignments: list[dict], tr_id: int, t: float) -> dict | None:
    return next((a for a in assignments if a["tr_id"] == tr_id
                 and _epoch(a["valid_from"]) <= t < _epoch(a["valid_to"])), None)


def _save_state(conn, state: dict) -> None:
    existing = conn.execute(select(results.vehicle_state.c.tr_id).where(
        results.vehicle_state.c.tr_id == state["tr_id"])).first()
    if existing:
        conn.execute(results.vehicle_state.update().where(results.vehicle_state.c.tr_id == state["tr_id"]).values(**state))
    else:
        conn.execute(results.vehicle_state.insert().values(**state))


def _load_input(conn, engine, cfg: DBConfig, T: float):
    if inspect(conn).has_table(routes.route_segments.name):
        segment_rows = [dict(row) for row in conn.execute(
            select(routes.route_segments, routes.routes.c.route_name, routes.routes.c.is_demo)
            .join(routes.routes, routes.route_segments.c.route_id == routes.routes.c.route_id)
            .order_by(routes.route_segments.c.route_id, routes.route_segments.c.direction_id,
                      routes.route_segments.c.segment_order)).mappings()]
        assignment_rows = [dict(row) for row in conn.execute(select(routes.vehicle_route_assignments).where(
            routes.vehicle_route_assignments.c.valid_from <= _dt(T),
            routes.vehicle_route_assignments.c.valid_to > _dt(T - 600))).mappings()]
    else:
        segment_rows, assignment_rows = [], []
    cols = [cfg.tel_tr, cfg.tel_time, cfg.tel_lat, cfg.tel_lon, cfg.tel_speed, cfg.tel_valid]
    if cfg.tel_receive:
        cols.append(cfg.tel_receive)
    cols.append("unit_id")
    raw = conn.execute(text(
        f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(cfg.tel_table)} "
        f"WHERE {_q(cfg.tel_time)} > :start AND {_q(cfg.tel_time)} <= :end "
        f"ORDER BY {_q(cfg.tel_tr)}, {_q(cfg.tel_time)}"),
        {"start": _param(engine, T - 600), "end": _param(engine, T)}).mappings()
    telemetry_by_time = {}
    for point in raw:
        value = dict(point)
        value["tr_id"] = int(value[cfg.tel_tr])
        value["t"] = _epoch(value[cfg.tel_time])
        value["received"] = _epoch(value[cfg.tel_receive]) if cfg.tel_receive else value["t"]
        value["lat"] = value[cfg.tel_lat]
        value["lon"] = value[cfg.tel_lon]
        value["speed"] = value[cfg.tel_speed]
        value["location_valid"] = value[cfg.tel_valid]
        # A replay must not advance vehicle state using packets not yet received.
        if value["received"] > T:
            continue
        key = value["tr_id"], value["t"]
        previous = telemetry_by_time.get(key)
        if previous is None:
            telemetry_by_time[key] = value
            continue
        # Duplicate deliveries of one GPS fix must not reset the three-point
        # movement history. Conflicting fixes remain explicitly invalid.
        fields = ("lat", "lon", "speed", "location_valid", "unit_id")
        if any(previous[field] != value[field] for field in fields):
            previous["location_valid"] = False
        previous["received"] = min(previous["received"], value["received"])
    return segment_rows, assignment_rows, list(telemetry_by_time.values())


def _process_vehicle(conn, tr_id, points, assignments, segments_by_route, previous, T, settings):
    history = deque(maxlen=5)
    last_good = None
    state = previous
    passages = 0
    for point in points:
        valid = _valid(point, last_good, T)
        if valid:
            last_good = dict(t=point["t"], lat=float(point["lat"]), lon=float(point["lon"]))
            history.append({**last_good, "speed": float(point["speed"])})
        else:
            history.clear()
            last_good = None
        if state and point["t"] <= _epoch(state["event_time"]):
            continue  # delayed packets never move a vehicle backwards
        assignment = _assignment(assignments, tr_id, point["t"])
        possible = segments_by_route.get((assignment["route_id"], assignment["direction_id"]), []) if assignment else []
        segment, progress, confidence = match_segment(list(history), possible, settings) if valid else (None, None, 0.0)
        if segment and state and state["segment_id"] and state["route_id"] == segment["route_id"]:
            old_order = state["segment_order"]
            if (state["direction_id"] != segment["direction_id"]
                    or segment["segment_order"] < old_order
                    or segment["segment_order"] > old_order + 1
                    or (segment["segment_order"] == old_order
                        and progress < (state["segment_progress"] or 0) - .2)):
                segment, progress, confidence = None, None, 0.0
        if segment:
            same = bool(state and state["segment_id"] == segment["segment_id"] and state["match_status"] == "MATCHED")
            # An adjacent transition is observed only after enough GPS points
            # confirm the new segment. Keep its first confirmed event time.
            entered = state["entered_at"] if same else _dt(point["t"])
            speed_sum = (state["speed_sum_kmh"] if same else 0.0) + float(point["speed"])
            speed_samples = (state["speed_samples"] if same else 0) + 1
            if (state and state["segment_id"] and not same and state["entered_at"]
                    and state["route_id"] == segment["route_id"]
                    and state["direction_id"] == segment["direction_id"]
                    and point["t"] - _epoch(state["event_time"]) <= settings.active_s
                    and segment["segment_order"] == state["segment_order"] + 1):
                duration = point["t"] - _epoch(state["entered_at"])
                if 15 <= duration <= 3600 and state["match_confidence"] >= .4:
                    already = conn.execute(select(results.segment_passages.c.id).where(
                        results.segment_passages.c.tr_id == tr_id,
                        results.segment_passages.c.segment_id == state["segment_id"],
                        results.segment_passages.c.entered_at == state["entered_at"])).first()
                    if not already:
                        conn.execute(results.segment_passages.insert().values(
                            tr_id=tr_id, unit_id=state["unit_id"], route_id=state["route_id"],
                            direction_id=state["direction_id"], segment_id=state["segment_id"],
                            entered_at=state["entered_at"], exited_at=_dt(point["t"]),
                            travel_time_sec=duration,
                            mean_speed_kmh=(state["speed_sum_kmh"] / state["speed_samples"]
                                            if state["speed_samples"] else None),
                            data_quality=min(state["match_confidence"], confidence),
                            calculation_version=VERSION))
                        passages += 1
            route_id, direction_id, segment_id = segment["route_id"], segment["direction_id"], segment["segment_id"]
            order, status = segment["segment_order"], "MATCHED"
        else:
            route_id = assignment["route_id"] if assignment else None
            direction_id = assignment["direction_id"] if assignment else None
            # Preserve the last confident segment across a short ambiguous
            # junction, while marking this GPS point UNKNOWN for aggregates.
            carry = bool(valid and state and state["segment_id"] and
                         state["route_id"] == route_id and
                         state["direction_id"] == direction_id and
                         point["t"] - _epoch(state["event_time"]) <= settings.active_s)
            segment_id = state["segment_id"] if carry else None
            order = state["segment_order"] if carry else None
            entered = state["entered_at"] if carry else None
            progress = state["segment_progress"] if carry else None
            speed_sum = state["speed_sum_kmh"] if carry else 0.0
            speed_samples = state["speed_samples"] if carry else 0
            confidence = state["match_confidence"] if carry else 0.0
            status = "UNKNOWN"
        state = dict(tr_id=tr_id, unit_id=int(point["unit_id"]) if point["unit_id"] is not None else None,
                     event_time=_dt(point["t"]), route_id=route_id, direction_id=direction_id,
                     segment_id=segment_id, segment_order=order, entered_at=entered,
                     segment_progress=progress, raw_speed_kmh=float(point["speed"]) if valid else None,
                     smoothed_speed_kmh=(statistics.median(p["speed"] for p in history) if valid else None),
                     speed_sum_kmh=speed_sum, speed_samples=speed_samples,
                     gps_age_sec=max(0.0, T - point["t"]), match_confidence=confidence,
                     match_status=status, calculation_version=VERSION)
    if state and (not previous or _epoch(state["event_time"]) > _epoch(previous["event_time"])):
        _save_state(conn, state)
    return passages


def _upsert(conn, table, key: dict, values: dict) -> None:
    condition = and_(*(table.c[name] == value for name, value in key.items()))
    if conn.execute(select(table).where(condition).limit(1)).first():
        conn.execute(table.update().where(condition).values(**values))
    else:
        conn.execute(table.insert().values(**{**key, **values}))


def _state_for_segment(conn, segment, T, settings):
    seg_id = segment["segment_id"]
    passes = [dict(r) for r in conn.execute(select(results.segment_passages).where(
        results.segment_passages.c.segment_id == seg_id,
        results.segment_passages.c.exited_at <= _dt(T),
        results.segment_passages.c.data_quality >= .4)).mappings()]
    # Keep the current observation window out of its own normal value.
    historical = [p["travel_time_sec"] for p in passes
                  if _epoch(p["exited_at"]) < T - settings.recent_s]
    if len(historical) >= settings.min_baseline_n:
        baseline = statistics.median(historical)
        source = "observed"
    else:
        baseline = segment["planned_travel_sec"]
        source = "planned" if baseline else "unavailable"
    base_values = dict(sample_count=len(historical),
                       median_travel_time_sec=baseline,
                       p25_travel_time_sec=(sorted(historical)[max(0, int(.25 * (len(historical)-1)))] if historical else None),
                       p75_travel_time_sec=(sorted(historical)[int(.75 * (len(historical)-1))] if historical else None),
                       source=source, updated_at=_dt(T), calculation_version=VERSION)
    _upsert(conn, results.segment_baseline, {"segment_id": seg_id}, base_values)
    recent = [p for p in passes if _epoch(p["exited_at"]) >= T - settings.recent_s]
    active = [dict(r) for r in conn.execute(select(results.vehicle_state).where(
        results.vehicle_state.c.segment_id == seg_id,
        results.vehicle_state.c.match_status == "MATCHED",
        results.vehicle_state.c.event_time <= _dt(T),
        results.vehicle_state.c.event_time >= _dt(T - settings.active_s))).mappings()]
    values = {p["tr_id"]: p["travel_time_sec"] for p in recent}
    for v in active:
        if baseline and v["entered_at"] and v["segment_progress"] is not None:
            elapsed = T - _epoch(v["entered_at"])
            if 0 <= elapsed <= 3600:
                values[v["tr_id"]] = elapsed + (1 - v["segment_progress"]) * baseline
    median_current = statistics.median(values.values()) if values else None
    ratio = median_current / baseline if baseline and median_current is not None else None
    confirming = sum(value >= settings.slowdown_ratio * baseline for value in values.values()) if baseline else 0
    n = len(values)
    risk = (round(100 * min(1, max(0, (ratio - 1) / .5)) * confirming / n, 1)
            if ratio is not None and n else None)
    quality = statistics.mean(v["match_confidence"] for v in active) if active else (1.0 if recent else 0.0)
    base_quality = 1.0 if source == "observed" else .5 if source == "planned" else 0.0
    confidence = round(min(1.0, n / 3) * quality * base_quality, 3)
    level = "HIGH" if confidence >= .75 else "MEDIUM" if confidence >= .4 else "LOW"
    prior = conn.execute(select(results.segment_state.c.severity).where(
        results.segment_state.c.segment_id == seg_id,
        results.segment_state.c.calculated_at < _dt(T)).order_by(
        results.segment_state.c.calculated_at.desc()).limit(1)).scalar()
    if ratio is None or confirming == 0:
        severity = "NORMAL"
    elif (confirming >= settings.min_confirming and confidence >= .4
          and risk is not None and risk >= 60 and prior in ("WATCH", "WARNING", "CRITICAL")):
        severity = "CRITICAL" if ratio >= 2 and confirming >= 3 and level == "HIGH" else "WARNING"
    else:
        severity = "WATCH"
    speeds = [p["mean_speed_kmh"] for p in recent if p["mean_speed_kmh"] is not None]
    speeds += [v["smoothed_speed_kmh"] for v in active if v["smoothed_speed_kmh"] is not None]
    row = dict(route_id=segment["route_id"], route_name=segment["route_name"],
               is_demo=segment["is_demo"], direction_id=segment["direction_id"],
               segment_id=seg_id, from_name=segment["from_name"], to_name=segment["to_name"],
               from_lat=segment["from_lat"], from_lon=segment["from_lon"],
               to_lat=segment["to_lat"], to_lon=segment["to_lon"],
               vehicles_on_segment=len(active), recent_completed_vehicles=len(recent),
               vehicles_considered=n,
               vehicles_confirming_slowdown=confirming,
               median_current_travel_time_sec=median_current,
               median_smoothed_speed_kmh=statistics.median(speeds) if speeds else None,
               baseline_travel_time_sec=baseline, baseline_source=source,
               slowdown_ratio=ratio, risk_index=risk,
               confidence_score=confidence, confidence_level=level,
               severity=severity, calculation_version=VERSION)
    _upsert(conn, results.segment_state, {"segment_id": seg_id, "calculated_at": _dt(T)}, row)
    alert_key = {"segment_id": seg_id, "alert_type": "SEGMENT_SLOWDOWN"}
    existing = conn.execute(select(results.alerts).where(
        results.alerts.c.segment_id == seg_id,
        results.alerts.c.alert_type == "SEGMENT_SLOWDOWN")).mappings().first()
    # Do not refresh resolved alerts forever: maintenance ages them by updated_at.
    if severity != "NORMAL" or (existing and existing["status"] == "ACTIVE"):
        alert_values = dict(route_id=segment["route_id"], direction_id=segment["direction_id"],
                            severity=severity, confidence_level=level, current_value=median_current,
                            baseline_value=baseline, vehicles_confirming=confirming,
                            updated_at=_dt(T), status="ACTIVE" if severity != "NORMAL" else "RESOLVED",
                            calculation_version=VERSION)
        if existing:
            conn.execute(results.alerts.update().where(results.alerts.c.alert_id == existing["alert_id"]).values(**alert_values))
        else:
            conn.execute(results.alerts.insert().values(**alert_key, **alert_values, created_at=_dt(T)))
    return row


def run_cycle(T: float | None = None, cfg: DBConfig | None = None, settings: Settings | None = None) -> dict:
    cfg, settings = cfg or DBConfig(), settings or Settings.from_env()
    if not cfg.url or not cfg.results_url or cfg.url == cfg.results_url:
        raise ValueError("Analytics requires distinct DATABASE_URL and RESULTS_DATABASE_URL")
    source = existing_engine(cfg.url, readonly=True)
    target = existing_engine(cfg.results_url)
    try:
        if T is None:
            T = current_T(source, cfg)
        if T is None:
            return {"calculated_at": None, "passages": 0, "segments": 0}
        with source.connect() as conn:
            segments, assignments, telemetry = _load_input(conn, source, cfg, T)
        by_route = defaultdict(list)
        for segment in segments:
            by_route[(segment["route_id"], segment["direction_id"])].append(segment)
        by_vehicle = defaultdict(list)
        for point in telemetry:
            by_vehicle[point["tr_id"]].append(point)
        with target.begin() as conn:
            old = {row["tr_id"]: dict(row) for row in conn.execute(select(results.vehicle_state)).mappings()}
            written_passages = sum(_process_vehicle(conn, tr_id, points, assignments, by_route,
                                                    old.get(tr_id), T, settings)
                                   for tr_id, points in by_vehicle.items())
            for segment in segments:
                _state_for_segment(conn, segment, T, settings)
        return {"calculated_at": _dt(T).isoformat() + "Z", "passages": written_passages,
                "segments": len(segments)}
    finally:
        source.dispose()
        target.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--at", help="UTC time for one replay cycle, ISO 8601")
    args = parser.parse_args()
    if args.at and not args.once:
        parser.error("--at requires --once")
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()

    def shut_down(*_):
        stop.set()

    signal.signal(signal.SIGINT, shut_down)
    signal.signal(signal.SIGTERM, shut_down)
    backoff = 1.0
    while not stop.is_set():
        try:
            result = run_cycle(_epoch(args.at) if args.at else None)
            log.info("Analytics cycle: %s", result)
            backoff = 1.0
        except Exception:
            log.exception("Analytics cycle failed")
            if args.once:
                raise
            stop.wait(backoff)
            backoff = min(60.0, backoff * 2)
            continue
        if args.once:
            break
        interval = float(os.getenv("ANALYTICS_INTERVAL_S", "30"))
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("ANALYTICS_INTERVAL_S must be positive")
        stop.wait(interval)


if __name__ == "__main__":
    main()
