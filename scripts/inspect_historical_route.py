"""Reproduce the exploratory route candidate for vehicle 122658 on 2026-01-06.

Only reads CSV. Writes a candidate, GPS-supported assignment intervals and
evidence files; never changes a database. This is a case study, not a general
route reconstruction model.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def coordinates(geom):
    match = re.fullmatch(r"POINT\s*\(([-\d.]+)\s+([-\d.]+)\)", geom.strip())
    if not match:
        raise ValueError(f"Invalid geometry: {geom}")
    return tuple(map(float, match.groups()))


def unambiguous_runs(rows):
    """Collapse same-time/same-position copies; split at uncertain time ordering."""
    by_time = defaultdict(list)
    for row in rows:
        by_time[row['time_begin']].append(row)
    runs, current = [], []
    for _, group in sorted(by_time.items()):
        if len({coordinates(r['geom']) for r in group}) != 1:
            if current:
                runs.append(current)
            current = []
            continue
        row = group[0]
        if current and coordinates(row['geom']) == coordinates(current[-1]['geom']):
            runs.append(current)
            current = []
        current.append(row)
    if current:
        runs.append(current)
    return runs


def occurrences(runs, seed):
    key = [coordinates(r['geom']) for r in seed]
    return [run[i:i + len(key)] for run in runs for i in range(len(run) - len(key) + 1)
            if [coordinates(r['geom']) for r in run[i:i + len(key)]] == key]


def distance(a, b):
    lon, lat = a
    lon2, lat2 = b
    return 111320 * math.hypot(lat - lat2, (lon - lon2) * math.cos(math.radians((lat + lat2) / 2)))


def epoch(value):
    value = datetime.fromisoformat(value)
    return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()


def match_trip(plan, gps, radius_m=150, window_s=300):
    """Maximum ordered stop chain, with strictly increasing GPS timestamps.

    A fix cannot confirm multiple stops; missing stops may be skipped. Among
    equal-length chains prefer spatial and temporal proximity. No arrival labels
    or nearest-point lookup across the whole day are used.
    """
    states = []
    for index, row in enumerate(plan):
        target_t, target_xy = epoch(row['time_begin']), coordinates(row['geom'])
        added = []
        for fix in gps:
            delta = fix['t'] - target_t
            if abs(delta) > window_s:
                continue
            metres = distance(target_xy, fix['xy'])
            if metres > radius_m:
                continue
            predecessors = [s for s in states if s['t'] < fix['t']]
            best = min(predecessors, key=lambda s: (-len(s['chain']), s['cost'])) if predecessors else None
            observation = dict(stop_order=index, target_stop_id=row['tt_action_item_id'],
                               event_time=datetime.fromtimestamp(fix['t'], timezone.utc).isoformat(),
                               gps_lon=fix['xy'][0], gps_lat=fix['xy'][1],
                               distance_m=round(metres, 1), deviation_s=delta)
            added.append(dict(t=fix['t'], cost=(best['cost'] if best else 0) + metres/radius_m + abs(delta)/window_s,
                              chain=(best['chain'] if best else []) + [observation]))
        states.extend(added)
    return min(states, key=lambda s: (-len(s['chain']), s['cost']))['chain'] if states else []


def supported_intervals(matches, gps, min_stops=5, max_gap_s=120):
    """Only consecutive matched stops with continuous valid telemetry support.

    This confirms a partial traversal, not the unobserved rest of a trip.
    """
    groups, current = [], []
    for match in matches:
        start, end = (epoch(current[-1]['event_time']) if current else None), epoch(match['event_time'])
        continuous = True
        if current:
            times = [start] + sorted({p['t'] for p in gps if start < p['t'] < end}) + [end]
            continuous = all(b-a <= max_gap_s for a, b in zip(times, times[1:]))
        if current and (match['stop_order'] != current[-1]['stop_order'] + 1 or not continuous):
            if len(current) >= min_stops:
                groups.append(current)
            current = []
        current.append(match)
    if len(current) >= min_stops:
        groups.append(current)
    return groups


def fleet_inventory(rows):
    """Count repeated directed triples, without equating a vehicle to a route."""
    vehicles = defaultdict(list)
    for row in rows:
        vehicles[row['tr_id']].append(row)
    inventory = []
    for tr_id, plan in sorted(vehicles.items(), key=lambda item: int(item[0])):
        triples = defaultdict(set)
        for run in unambiguous_runs(plan):
            for i in range(len(run)-2):
                key = tuple(coordinates(r['geom']) for r in run[i:i+3])
                if len(set(key)) == 3:
                    triples[key].add(run[i]['time_begin'])
        inventory.append(dict(tr_id=int(tr_id), schedule_rows=len(plan),
                              distinct_coordinates=len({coordinates(r['geom']) for r in plan}),
                              repeated_directed_triples=sum(len(times) >= 3 for times in triples.values())))
    return inventory


def inspect(schedule, traffic, output):
    with schedule.open(encoding='utf-8-sig', newline='') as source:
        all_rows = list(csv.DictReader(source))
    rows = sorted((r for r in all_rows if r['tr_id'] == '122658'
                   and r['time_begin'].startswith('2026-01-06')),
                  key=lambda r: (r['time_begin'], r['tt_action_item_id']))
    runs = unambiguous_runs(rows)
    gps = []
    with traffic.open(encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            if (row['tr_id'] == '122658' and row['event_time'].startswith('2026-01-06')
                    and row['location_valid'].lower() in ('true', '1') and row['lon'] and row['lat']):
                point = float(row['lon']), float(row['lat'])
                if all(math.isfinite(v) for v in point) and 36 <= point[0] <= 39 and 55 <= point[1] <= 57:
                    try:
                        speed = float(row['speed'])
                    except (ValueError, TypeError):
                        continue
                    if math.isfinite(speed) and 0 <= speed <= 120:
                        gps.append(dict(t=epoch(row['event_time']), xy=point))
    gps.sort(key=lambda p: p['t'])
    # Same-time conflicting positions are unsuitable for ordered evidence.
    by_time = defaultdict(set)
    for fix in gps:
        by_time[fix['t']].add(fix['xy'])
    gps = [dict(t=t, xy=next(iter(points))) for t, points in sorted(by_time.items()) if len(points) == 1]
    filtered = []
    for fix in gps:
        if filtered and distance(filtered[-1]['xy'], fix['xy']) / (fix['t']-filtered[-1]['t']) > 120/3.6:
            continue
        filtered.append(fix)
    gps = filtered
    document = dict(route_id='historical-candidate-122658',
                    route_name='Кандидат по истории ТС 122658 (не номер маршрута)',
                    is_demo=True, directions=[], assignments=[])
    evidence = dict(schedule_sha256=hashlib.sha256(schedule.read_bytes()).hexdigest(),
                    traffic_sha256=hashlib.sha256(traffic.read_bytes()).hexdigest(),
                    date='2026-01-06', tr_id=122658, schedule_rows=len(rows),
                    valid_gps_points=len(gps), directions=[], mapping=[],
                    fleet_inventory=fleet_inventory(all_rows),
                    matching_policy=dict(radius_m=150, window_s=300, min_consecutive_stops=5,
                                         max_telemetry_gap_s=120, timezone_assumption='UTC'))
    lookup = defaultdict(list)
    for direction, start, end in [('a', '06:17:00', '06:41:00'), ('b', '06:47:00', '07:17:00')]:
        seed = [r for r in rows if f'2026-01-06 {start}' <= r['time_begin'] <= f'2026-01-06 {end}']
        if len(seed) < 2 or len({coordinates(r['geom']) for r in seed}) != len(seed):
            raise ValueError('Reference interval must contain distinct stops')
        found = occurrences(runs, seed)
        if len(found) < 2:
            raise ValueError('Reference sequence is not repeated')
        stops, coverage = [], []
        for row in seed:
            lon, lat = point = coordinates(row['geom'])
            stop_id = 'geo-' + hashlib.sha256(f'{lon:.8f},{lat:.8f}'.encode()).hexdigest()[:16]
            stops.append(dict(stop_id=stop_id, name=row['building_address'] or f'Точка {lat}, {lon}', lat=lat, lon=lon))
            lookup[point].append(dict(direction_id=direction, stop_id=stop_id))
            coverage.append(dict(stop_id=stop_id, nearest_gps_m=round(min(distance(point, p['xy']) for p in gps), 1) if gps else None))
        durations = [statistics.median((datetime.fromisoformat(match[i+1]['time_begin']) -
                                       datetime.fromisoformat(match[i]['time_begin'])).total_seconds()
                                      for match in found) for i in range(len(seed)-1)]
        document['directions'].append(dict(direction_id=direction, stops=stops, planned_travel_sec=durations))
        trips = []
        for plan in found:
            matched = match_trip(plan, gps)
            spans = supported_intervals(matched, gps)
            for span in spans:
                document['assignments'].append(dict(tr_id=122658, direction_id=direction,
                    valid_from=span[0]['event_time'], valid_to=span[-1]['event_time']))
            trips.append(dict(start=plan[0]['time_begin'], end=plan[-1]['time_begin'],
                              target_stop_ids=[r['tt_action_item_id'] for r in plan],
                              ordered_matches=matched, supported_spans=spans))
        evidence['directions'].append(dict(direction_id=direction, stops=len(stops),
                                          occurrences=trips, gps_coverage=coverage))
    for row in rows:
        candidates = lookup[coordinates(row['geom'])]
        evidence['mapping'].append(dict(target_stop_id=row['tt_action_item_id'], time_begin=row['time_begin'],
            candidates=candidates, status='unmatched' if not candidates else 'ambiguous_direction' if len(candidates)>1 else 'candidate'))
    assignments = sorted(document['assignments'], key=lambda a: a['valid_from'])
    for a, b in zip(assignments, assignments[1:]):
        if epoch(a['valid_to']) > epoch(b['valid_from']):
            raise ValueError('GPS-supported assignments overlap; manual review is needed')
    document['assignments'] = assignments
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('route.candidate.json', document), ('evidence.json', evidence)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(schedule_rows=len(rows), valid_gps_points=len(gps), assignments=len(assignments),
        directions=[dict(direction_id=d['direction_id'], stops=d['stops'], repeats=len(d['occurrences']),
                         stops_with_gps_within_100m=sum(p['nearest_gps_m'] is not None and p['nearest_gps_m']<=100 for p in d['gps_coverage']))
                    for d in evidence['directions']],
        mapping_counts={status:sum(m['status']==status for m in evidence['mapping'])
                        for status in ['candidate', 'ambiguous_direction', 'unmatched']}), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schedule', type=Path, default=Path('data/dataset/validate/schedule_plan.csv'))
    parser.add_argument('--traffic', type=Path, default=Path('data/dataset/validate/traffic.csv'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/historical-route-122658'))
    args = parser.parse_args()
    inspect(args.schedule, args.traffic, args.output)
