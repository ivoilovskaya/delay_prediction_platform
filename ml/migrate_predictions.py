"""Verified, repeatable copy; source is read-only and is never deleted.

Stop worker and results maintenance before running:
python -m ml.migrate_predictions --source sqlite:///artifacts/demo.db
"""
import argparse

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from ml.db import DBConfig, predictions_table
from storage.database import existing_engine


def migrate(source_url, cfg=None):
    cfg = cfg or DBConfig()
    if not cfg.results_url:
        raise ValueError('Set RESULTS_DATABASE_URL and initialize it first')
    source = existing_engine(source_url, readonly=True)
    target = existing_engine(cfg.results_url)
    if source.url.database == target.url.database:
        raise ValueError('Source and destination must differ')
    try:
        table = predictions_table(cfg)
        columns = [c.name for c in table.columns if c.name != 'id']
        keys = ('tr_id', 't_forecast', 'target_stop_id')
        # Types from the existing model normalize SQLite timestamps and booleans.
        old = predictions_table(cfg)
        with source.connect() as conn:
            rows = [dict(row) for row in conn.execute(select(*(old.c[c] for c in columns))).mappings()]
        with target.begin() as conn:
            inserted = 0
            for row in rows:
                inserted += conn.execute(insert(table).values(row).on_conflict_do_nothing(
                    index_elements=list(keys))).rowcount
                match = conn.execute(select(*(table.c[c] for c in columns)).where(
                    *(table.c[k] == row[k] for k in keys))).mappings().one()
                if dict(match) != row:
                    raise ValueError(f'Conflicting prediction {tuple(row[k] for k in keys)}; migration rolled back')
        return {'source_rows_verified': len(rows), 'inserted': inserted}
    finally:
        source.dispose()
        target.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    print(migrate(args.source))


if __name__ == '__main__':
    main()
