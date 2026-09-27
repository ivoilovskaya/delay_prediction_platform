"""One-shot Compose preparation; existing input data is never reimported."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from ml.db import DBConfig
from ml.results_db import initialize
from ndtp_ingestion.db_init import db_path, init_db
from ndtp_ingestion.load_schedule import load_schedule
from scripts.setup import demo_plan


def check_input(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('SELECT tr_id, event_time, receive_time, lon, lat, speed, location_valid FROM telemetry LIMIT 0')
        conn.execute('SELECT tt_action_item_id, tr_id, time_begin, geom, manual_fill FROM schedule_plan LIMIT 0')


def prepare(mode, runtime, dataset):
    path = db_path()
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    # Do not fill an existing file, even if empty: fail visibly on an incomplete schema.
    if path.exists():
        check_input(path)
        if mode == 'emulator':
            for name in ('units.json', 'emulator.json'):
                if not (runtime / name).is_file():
                    raise RuntimeError(f'Existing input database has no {runtime / name}; restore its runtime-config volume')
        print('Existing input database preserved; CSV import skipped.', flush=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Build next to the final file; publish only after successful import.
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=path.parent) as temporary:
            staging = Path(temporary) / 'input.db'
            original_url = os.environ['DATABASE_URL']
            try:
                os.environ['DATABASE_URL'] = 'sqlite:///' + str(staging)
                if mode == 'emulator':
                    plan, _, config_path = demo_plan(runtime)
                    config = json.loads(config_path.read_text())
                    config.update(targetHost='ingestion', targetPort=9000)
                    config_path.write_text(json.dumps(config), encoding='utf-8')
                    init_db()
                    load_schedule(plan)
                else:
                    subprocess.run([sys.executable, '-m', 'ml.csv_to_db', '--data', str(dataset),
                                    '--url', os.environ['DATABASE_URL']], check=True)
                check_input(staging)
                # SQLite backup includes committed WAL data; copying the .db alone does not.
                completed = Path(temporary) / 'complete.db'
                with closing(sqlite3.connect(staging)) as source:
                    with closing(sqlite3.connect(completed)) as target:
                        source.backup(target)
                        target.execute('PRAGMA journal_mode=DELETE')
                completed.replace(path)
            finally:
                os.environ['DATABASE_URL'] = original_url
        print(f'Input database prepared: {path}', flush=True)
    cfg = DBConfig()
    initialize(cfg)
    from analytics.results import initialize as initialize_analytics
    initialize_analytics(cfg)
    print('Results schema ready; existing predictions preserved.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['emulator', 'historical'])
    parser.add_argument('--runtime', type=Path, default=Path('/data/runtime'))
    parser.add_argument('--dataset', type=Path, default=Path('/dataset'))
    args = parser.parse_args()
    prepare(args.mode, args.runtime, args.dataset)


if __name__ == '__main__':
    main()
