from scripts.inspect_historical_route import epoch, match_trip, occurrences, supported_intervals, unambiguous_runs

import json
import sqlite3
from pathlib import Path

import pytest

from analytics.routes import import_route


def row(time, geom, ident):
    return dict(time_begin=time, geom=geom, tt_action_item_id=ident)


def test_tied_different_positions_break_sequence():
    rows = [row('01', 'POINT (37 55)', 1), row('02', 'POINT (38 55)', 2),
            row('02', 'POINT (39 55)', 3), row('03', 'POINT (37 56)', 4)]
    assert unambiguous_runs(rows) == [[rows[0]], [rows[3]]]


def test_duplicate_observation_does_not_invent_extra_stop():
    rows = [row('01', 'POINT (37 55)', 1), row('01', 'POINT (37.0 55.0)', 2),
            row('02', 'POINT (38 55)', 3), row('03', 'POINT (37 55)', 4),
            row('04', 'POINT (38 55)', 5)]
    matches = occurrences(unambiguous_runs(rows), [rows[0], rows[2]])
    assert len(matches) == 2
    assert [[r['tt_action_item_id'] for r in match] for match in matches] == [[1, 3], [4, 5]]


def test_terminal_repetition_breaks_sequence():
    rows = [row('01', 'POINT (37 55)', 1), row('02', 'POINT (38 55)', 2),
            row('03', 'POINT (38 55)', 3), row('04', 'POINT (39 55)', 4)]
    assert unambiguous_runs(rows) == [rows[:2], rows[2:]]


def test_matching_rejects_wrong_time_and_reverse_order():
    plan = [row('2026-01-06T06:00:00', 'POINT (37 55)', 1),
            row('2026-01-06T06:01:00', 'POINT (37.01 55)', 2)]
    start = epoch(plan[0]['time_begin'])
    assert match_trip(plan, [dict(t=start+3600, xy=(37, 55))]) == []
    reverse = [dict(t=start, xy=(37.01, 55)), dict(t=start+60, xy=(37, 55))]
    assert len(match_trip(plan, reverse)) == 1


def test_one_fix_cannot_confirm_two_stops():
    plan = [row('2026-01-06T06:00:00', 'POINT (37 55)', 1),
            row('2026-01-06T06:01:00', 'POINT (37.001 55)', 2)]
    assert len(match_trip(plan, [dict(t=epoch(plan[0]['time_begin']), xy=(37, 55))])) == 1


def test_assignment_requires_consecutive_stops_and_continuous_gps():
    plan = [row(f'2026-01-06T06:0{i}:00', f'POINT ({37+i*.01} 55)', i) for i in range(5)]
    gps = [dict(t=epoch(r['time_begin']), xy=(37+i*.01, 55)) for i, r in enumerate(plan)]
    matched = match_trip(plan, gps)
    assert len(supported_intervals(matched, gps)) == 1
    sparse_plan = [row(f'2026-01-06T06:{i*3:02d}:00', f'POINT ({37+i*.01} 55)', i) for i in range(5)]
    sparse_gps = [dict(t=epoch(r['time_begin']), xy=(37+i*.01, 55)) for i, r in enumerate(sparse_plan)]
    assert supported_intervals(match_trip(sparse_plan, sparse_gps), sparse_gps) == []
    assert supported_intervals(matched[:2]+matched[3:], gps) == []


def test_historical_fixture_import_and_duplicate_protection(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'analytics/examples/historical-122658.json'
    document = json.loads(fixture.read_text())
    path = tmp_path / 'input.db'
    url = f'sqlite:///{path}'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE telemetry (tr_id INTEGER)')
        c.execute('INSERT INTO telemetry VALUES (122658)')
    import_route(document, url)
    with pytest.raises(ValueError, match='already exists'):
        import_route(document, url)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT COUNT(*) FROM routes').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM route_segments').fetchone()[0] == 30
        assert c.execute('SELECT COUNT(*) FROM vehicle_route_assignments').fetchone()[0] == 12
        assert c.execute('SELECT * FROM telemetry').fetchall() == [(122658,)]
        assert c.execute('SELECT DISTINCT tr_id FROM vehicle_route_assignments').fetchall() == [(122658,)]
