"""Explicit results initialization: python -m ml.results_db."""
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url


# Compatibility import for existing local callers.
from storage.database import existing_engine


def initialize(cfg=None):
    from ml.db import DBConfig, predictions_table, _q
    cfg = cfg or DBConfig()
    url = cfg.results_url or cfg.url
    parsed = make_url(url)
    if parsed.get_backend_name() == 'sqlite' and parsed.database != ':memory:':
        Path(parsed.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    try:
        table = predictions_table(cfg)
        with engine.begin() as conn:
            table.metadata.create_all(conn)
            conn.execute(select(table).limit(0))  # fail early on an incompatible existing schema
            for index in table.indexes:
                index.create(conn, checkfirst=True)
            conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS {_q("uq_" + cfg.pred_table + "_vehicle_time_stop")} '
                              f'ON {_q(cfg.pred_table)} (tr_id, t_forecast, target_stop_id)'))
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS {_q("ix_" + cfg.pred_table + "_predicted_at")} '
                              f'ON {_q(cfg.pred_table)} (predicted_at)'))
    finally:
        engine.dispose()


if __name__ == '__main__':
    initialize()
    print('Results database initialized; existing predictions preserved.')
