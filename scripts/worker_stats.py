"""Сводка ML_CYCLE / REPLAY_CYCLE из файла или stdin: python -m scripts.worker_stats --last 100."""
from __future__ import annotations

import argparse
from collections import deque
import json
import sys


def summarize(lines, last=100):
    if last < 1:
        raise ValueError("last должен быть положительным")
    cycles = deque(maxlen=last)
    for line in lines:
        _, marker, payload = line.partition("ML_CYCLE ")
        if not marker:
            _, marker, payload = line.partition("REPLAY_CYCLE ")
        if not marker:
            continue
        try:
            row = json.loads(payload)
        except json.JSONDecodeError:
            continue  # например, оборванная последняя строка
        if isinstance(row, dict) and row.get("status") in ("ok", "error"):
            cycles.append(row)
    ok = [r for r in cycles if r["status"] == "ok"]
    def mean(values):
        return round(sum(values) / len(values), 3) if values else None
    def per_vehicle(key):
        measured = [r for r in ok if r.get(key) is not None and r["vehicles"] > 0]
        count = sum(r["vehicles"] for r in measured)
        return round(sum(r[key] for r in measured) / count, 3) if count else None
    durations = [r["cycle_ms"] for r in cycles]
    result = {
        "cycles": len(cycles), "failed_cycles": len(cycles) - len(ok),
        "vehicles_per_cycle_avg": mean([r["vehicles"] for r in ok]),
        "vehicles_per_cycle_max": max((r["vehicles"] for r in ok), default=0),
        "cycle_ms_avg": mean(durations),
        "cycle_ms_max": round(max(durations), 3) if durations else None,
        "cycles_over_interval": sum(r["cycle_ms"] > r["interval_ms"] for r in cycles if "interval_ms" in r),
        "predict_ms_per_vehicle": per_vehicle("predict_ms"),
        "features_ms_per_vehicle": per_vehicle("features_ms"),
        "inference_ms_per_vehicle": per_vehicle("inference_ms"),
        "model_timed_cycles": sum(r.get("inference_ms") is not None for r in ok),
        "baseline_predictions": sum(r["baseline"] for r in ok),
        "skipped_vehicles": sum(r["skipped"] for r in ok),
    }
    historical = [r for r in cycles if "step_ms" in r]
    if historical:
        if len(historical) == len(cycles):
            result.pop("cycles_over_interval")
        result.update(
            ml_ms_avg=mean([r["ml_ms"] for r in ok if "ml_ms" in r]),
            analytics_ms_avg=mean([r["analytics_ms"] for r in ok if "analytics_ms" in r]),
            cycles_over_step=sum(r["cycle_ms"] > r["step_ms"] for r in historical),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", help="лог; по умолчанию stdin")
    parser.add_argument("--last", type=int, default=100, help="последние N попыток цикла")
    args = parser.parse_args()
    if args.last < 1:
        parser.error("--last должен быть положительным")
    if args.file:
        with open(args.file, encoding="utf-8") as stream:
            result = summarize(stream, args.last)
    else:
        result = summarize(sys.stdin, args.last)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
