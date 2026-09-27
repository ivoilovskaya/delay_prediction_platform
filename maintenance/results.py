"""Expire all known results tables: python -m maintenance.results [--once].

Retention and cleanup cadence are separate; every table can override the global
RESULTS_CLEANUP_INTERVAL_S. Historical mode never deletes records.
"""
import argparse
from dataclasses import dataclass
import logging
import math
import os
import signal
import threading
import time

from sqlalchemy import inspect, text
from storage.database import DatabaseConfig, existing_engine, quote_identifier, timestamp_parameter

log = logging.getLogger('maintenance.results')
# Explicit timestamp semantics for every results table. New tables need a policy.
POLICIES = (
    ('predictions', 'predicted_at', 86400),
    ('vehicle_state', 'event_time', 86400),
    ('segment_passages', 'exited_at', 2592000),
    ('segment_baseline', 'updated_at', 2592000),
    ('segment_state', 'calculated_at', 86400),
    ('alerts', 'updated_at', 604800),
)


def _positive(name, default):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be positive and finite')
    return value


@dataclass(frozen=True)
class Policy:
    table: str
    timestamp: str
    retention_s: float
    interval_s: float


def policies_from_env(cfg):
    interval = _positive('RESULTS_CLEANUP_INTERVAL_S', 600)
    policies = tuple(Policy(cfg.pred_table if table == 'predictions' else table, column,
                            _positive(f'{table.upper()}_RETENTION_S', retention),
                            _positive(f'{table.upper()}_CLEANUP_INTERVAL_S', interval))
                     for table, column, retention in POLICIES)
    # Prevent a custom prediction table name from targeting input/analytics tables.
    if cfg.pred_table in {'telemetry', 'schedule_plan', 'routes', 'route_stops',
                         'route_segments', 'vehicle_route_assignments'} or len({p.table for p in policies}) != len(policies):
        raise ValueError('Prediction table name overlaps another table')
    return policies


def _historical(cfg):
    if cfg.time_mode not in ('wall', 'stream'):
        raise ValueError('DATA_TIME_MODE must be stream or wall (ML_TIME_MODE is the legacy fallback)')
    return cfg.time_mode == 'stream'


def cleanup_table(engine, policy, now):
    """One bounded-purpose transaction; never create tables or databases."""
    with engine.begin() as conn:
        if not inspect(conn).has_table(policy.table):
            log.info('Skipping absent table %s', policy.table)
            return None
        statement = text(f'DELETE FROM {quote_identifier(policy.table)} '
                         f'WHERE {quote_identifier(policy.table)}.{quote_identifier(policy.timestamp)} < :cutoff')
        deleted = conn.execute(statement, {'cutoff': timestamp_parameter(engine, now - policy.retention_s)}).rowcount
    log.info('table=%s deleted=%s retention_s=%s interval_s=%s',
             policy.table, deleted, policy.retention_s, policy.interval_s)
    return deleted


def cleanup(now=None, cfg=None):
    """Explicit single sweep (all policies); return deleted row counts by table."""
    cfg = cfg or DatabaseConfig()
    if _historical(cfg):
        return {}
    policies = policies_from_env(cfg)
    engine = existing_engine(cfg.results_url or cfg.url)
    try:
        counts = {}
        for policy in policies:
            deleted = cleanup_table(engine, policy, time.time() if now is None else now)
            if deleted is not None:
                counts[policy.table] = deleted
        return counts
    finally:
        engine.dispose()


class Scheduler:
    """Independent deadlines and retries; one table failure cannot starve others."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.disabled = _historical(cfg)
        self.policies = () if self.disabled else policies_from_env(cfg)
        self.engine = None
        self.next_due = {p.table: 0.0 for p in self.policies}
        self.reported_unknown = set()

    def tick(self, *, monotonic=None, now=None):
        if self.disabled:
            return {}
        monotonic = time.monotonic() if monotonic is None else monotonic
        now = time.time() if now is None else now
        counts = {}
        for policy in self.policies:
            if monotonic < self.next_due[policy.table]:
                continue
            try:
                self.engine = self.engine or existing_engine(self.cfg.results_url or self.cfg.url)
                deleted = cleanup_table(self.engine, policy, now)
                if deleted is not None:
                    counts[policy.table] = deleted
                self.next_due[policy.table] = monotonic + policy.interval_s
            except Exception:
                retry = min(30, policy.interval_s)
                log.exception('Cleanup failed for %s; retry in %ss', policy.table, retry)
                self.next_due[policy.table] = monotonic + retry
        # A new result table must not silently fall outside lifecycle management.
        if self.engine:
            try:
                tables = set(inspect(self.engine).get_table_names())
                known = {p.table for p in self.policies} | {'telemetry', 'schedule_plan', 'routes',
                        'route_stops', 'route_segments', 'vehicle_route_assignments'}
                unknown = tables - known - self.reported_unknown
                if unknown:
                    log.warning('No retention policy for tables: %s; left untouched', sorted(unknown))
                    self.reported_unknown.update(unknown)
            except Exception:
                log.debug('Cannot inspect results database', exc_info=True)
        return counts

    def close(self):
        if self.engine is not None:
            self.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Clean all known result tables once')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    cfg = DatabaseConfig()
    if _historical(cfg):
        log.info('Historical mode: all results retention disabled')
        return
    if args.once:
        cleanup(cfg=cfg)
        return
    scheduler = Scheduler(cfg)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            scheduler.tick()
            stop.wait(max(0, min(scheduler.next_due.values()) - time.monotonic()))
    finally:
        scheduler.close()


if __name__ == '__main__':
    main()
