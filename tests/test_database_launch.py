"""Launch preparation preserves existing data and initializes results first."""
import os
import sqlite3
import sys

import pytest

from ml.results_db import initialize
from ndtp_ingestion.db_init import init_db


@pytest.mark.parametrize('mode', ['historical', 'emulator'])
def test_setup_restart_does_not_import_csv(tmp_path, monkeypatch, mode):
    from scripts import setup
    # setup intentionally mutates process environment before exec; isolate it in tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    source, results = tmp_path / 'input.db', tmp_path / 'results.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{source}')
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}')
    init_db()
    initialize()
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO schedule_plan VALUES (1, 42, '2026-09-27', 0, 'POINT (37.5 55.75)', 'sentinel')")
    commands = []
    monkeypatch.setattr(setup.subprocess, 'run', lambda command, **_: commands.append(command))
    monkeypatch.setattr(setup.importlib, 'import_module', lambda _: None)
    monkeypatch.setattr(setup.os, 'chdir', lambda _: None)
    launched = []
    monkeypatch.setattr(setup.os, 'execv', lambda *args: launched.append(args))
    args = ['setup', '--mode', mode, '--db', str(source), '--results-db', str(results)]
    if mode == 'emulator':
        mapping = tmp_path / 'units.json'
        mapping.write_text('{"123": 42}')
        args += ['--schedule', str(tmp_path / 'not-read.csv'), '--unit-map', str(mapping), '--no-emulator']
    monkeypatch.setattr(sys, 'argv', args)
    setup.main()
    assert launched
    assert not any('ml.csv_to_db' in c for c in commands)
    assert os.environ['RESULTS_DATABASE_URL'] == f'sqlite:///{results}'
    with sqlite3.connect(source) as conn:
        assert conn.execute('SELECT building_address FROM schedule_plan').fetchall() == [('sentinel',)]


@pytest.mark.parametrize('mode', ['stream', 'wall'])
def test_launcher_initializes_before_processes(tmp_path, monkeypatch, mode):
    from scripts import run_demo
    source, results = tmp_path / 'input.db', tmp_path / 'results.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{source}')
    monkeypatch.setenv('RESULTS_DATABASE_URL', f'sqlite:///{results}')
    monkeypatch.setenv('ML_TIME_MODE', mode)
    init_db()
    commands = []
    class Process:
        def poll(self): return 0
        def wait(self, **_): return 0
    def launch(command, **_):
        with sqlite3.connect(f'file:{results}?mode=ro', uri=True) as conn:
            assert conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0] == 0
        commands.append(command)
        return Process()
    monkeypatch.setattr(run_demo.subprocess, 'Popen', launch)
    monkeypatch.setattr(run_demo.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(sys, 'argv', ['run_demo'])
    assert run_demo.main() == 0
    assert any('ml.results_maintenance' in c for c in commands) == (mode == 'wall')
