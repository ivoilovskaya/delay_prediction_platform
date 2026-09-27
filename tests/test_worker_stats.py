import json

import pytest

from scripts.worker_stats import summarize


def record(**changes):
    row = dict(status="ok", cycle_ms=20, interval_ms=30, vehicles=1,
               predict_ms=10, features_ms=6, inference_ms=2, baseline=0, skipped=0)
    row.update(changes)
    return "worker | INFO ML_CYCLE " + json.dumps(row)


def test_window_weighting_and_errors():
    result = summarize([
        record(vehicles=1000), "ordinary log", "ML_CYCLE {broken",
        record(), record(vehicles=9, predict_ms=30, features_ms=14, inference_ms=8),
        record(status="error", cycle_ms=40),
    ], last=3)
    assert result["cycles"] == 3
    assert result["failed_cycles"] == 1
    assert result["vehicles_per_cycle_avg"] == 5
    assert result["predict_ms_per_vehicle"] == 4
    assert result["features_ms_per_vehicle"] == 2
    assert result["inference_ms_per_vehicle"] == 1
    assert result["cycles_over_interval"] == 1
    assert result["cycle_ms_max"] == 40


def test_unmeasured_baseline_does_not_dilute_model_timings():
    result = summarize([record(), record(vehicles=9, baseline=9, features_ms=None, inference_ms=None)])
    assert result["features_ms_per_vehicle"] == 6
    assert result["model_timed_cycles"] == 1
    assert result["baseline_predictions"] == 9
    assert summarize([])["cycle_ms_avg"] is None
    assert summarize([record(vehicles=0)])["inference_ms_per_vehicle"] is None
    with pytest.raises(ValueError):
        summarize([], last=0)
