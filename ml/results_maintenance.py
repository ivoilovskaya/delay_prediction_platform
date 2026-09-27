"""Independent retention process: python -m ml.results_maintenance [--once]."""
import argparse
import logging
import math
import os
import signal
import threading
import time

from sqlalchemy import text
from ml.db import DBConfig, _param, _q
from ml.results_db import existing_engine

log = logging.getLogger('results.maintenance')


def cleanup(now=None, cfg=None):
    cfg = cfg or DBConfig()
    if cfg.time_mode == 'stream':
        return 0
    if cfg.time_mode != 'wall':
        raise ValueError('ML_TIME_MODE must be stream or wall')
    retention = float(os.getenv('PREDICTIONS_RETENTION_S', '86400'))
    if not math.isfinite(retention) or retention <= 0:
        raise ValueError('PREDICTIONS_RETENTION_S must be positive and finite')
    engine = existing_engine(cfg.results_url or cfg.url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(f'DELETE FROM {_q(cfg.pred_table)} WHERE predicted_at < :cutoff'),
                                {'cutoff': _param(engine, (time.time() if now is None else now) - retention)}).rowcount
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    cfg = DBConfig()
    if cfg.time_mode == 'stream':
        log.info('Historical mode: results retention disabled')
        return
    interval = float(os.getenv('RESULTS_CLEANUP_INTERVAL_S', '600'))
    if not math.isfinite(interval) or interval <= 0:
        parser.error('RESULTS_CLEANUP_INTERVAL_S must be positive and finite')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            log.info('Deleted predictions: %s', cleanup(cfg=cfg))
        except Exception:
            log.exception('Results cleanup failed; will retry')
            if args.once:
                raise
        if args.once:
            return
        stop.wait(interval)


if __name__ == '__main__':
    main()
