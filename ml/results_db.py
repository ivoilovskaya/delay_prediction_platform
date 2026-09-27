"""Explicit results initialization: python -m ml.results_db."""
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url


def existing_engine(url, *, readonly=False):
    """SQLite URI mode prevents accidental creation (including a check/open race)."""
    parsed = make_url(url)
    if parsed.get_backend_name() == 'sqlite':
        if not parsed.database or parsed.database == ':memory:':
            raise ValueError('Results database must be a persistent SQLite file')
        uri = Path(parsed.database).resolve().as_uri()
        parsed = parsed.set(database=uri, query={**parsed.query, 'mode': 'ro' if readonly else 'rw', 'uri': 'true'})
    return create_engine(parsed, pool_pre_ping=True)


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
