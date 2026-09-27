"""Экспорт OpenAPI из FastAPI-приложений без запуска серверов, моделей и БД.

Запуск: ``python -m scripts.export_openapi``; проверка: ``--check``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def export(output: Path, check: bool = False) -> bool:
    """Сохранить схемы backend и ML; в режиме check проверить совпадение файлов."""
    from backend.api import app as backend_app
    from ml.service import app as ml_app

    matched = True
    for name, app in (("backend_openapi.json", backend_app), ("ml_openapi.json", ml_app)):
        content = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
        path = output / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                print(f"Устаревшая схема: {path}; выполните python -m scripts.export_openapi")
                matched = False
        else:
            output.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(path)
    return matched


def main():
    """CLI для обновления схем и проверки в CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not export(args.output, args.check):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
