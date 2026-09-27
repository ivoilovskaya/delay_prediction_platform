# Документация API — OpenAPI / Swagger

После запуска проекта по основному [README](../README.md):

| Приложение | Swagger UI | OpenAPI JSON |
| --- | --- | --- |
| Backend, live | [localhost:8000/docs](http://127.0.0.1:8000/docs) | [localhost:8000/openapi.json](http://127.0.0.1:8000/openapi.json) |
| Backend, historical (`PORT=8002`) | [localhost:8002/docs](http://127.0.0.1:8002/docs) | [localhost:8002/openapi.json](http://127.0.0.1:8002/openapi.json) |
| Отдельный ML-сервис | [localhost:8001/docs](http://127.0.0.1:8001/docs) | [localhost:8001/openapi.json](http://127.0.0.1:8001/openapi.json) |

При другом `PORT` замените порт в ссылках. ReDoc доступен у каждого приложения
по `/redoc`. Swagger позволяет просматривать параметры и выполнять запросы.
`POST /replay/start` меняет состояние воспроизведения, а ML-метод
`POST /predict/from-db` по умолчанию записывает прогнозы в БД.

Основной backend предоставляет здоровье процесса (`/health`), активный транспорт
(`/vehicles/active`), готовые прогнозы (`/predictions/*`), аналитику сегментов
(`/analytics/*`) и управление historical-режимом (`/replay/*`).
`/health` не проверяет БД и готовность модели. Отсутствующий прогноз возвращает
404; недоступность данных — 503. Ответы прогнозов различают состояния `ready`,
`stale`, а пустая общая выборка — `unavailable`.

Отдельный ML-сервис не входит в стандартный Compose. Для ручной работы:

```bash
python -m uvicorn ml.service:app --host 127.0.0.1 --port 8001
```

Его методы `/predict`, `/predict/batch`, `/predict/from-db` описывают входные
схемы и результаты модели. Пример запроса: [ml_predict_example.json](ml_predict_example.json).

## Схемы без запуска приложения

Сохранённые схемы: [backend_openapi.json](backend_openapi.json),
[ml_openapi.json](ml_openapi.json). Они генерируются из маршрутов FastAPI:

```bash
python -m scripts.export_openapi
python -m scripts.export_openapi --check
```

Экспорт не запускает lifespan, не загружает модель и не подключается к БД.
После изменения маршрутов или схем обновите JSON; CI проверяет соответствие коду.
При работающем приложении `/openapi.json` всегда отражает загруженную версию кода.

Механизм генерации описан в [документации FastAPI](https://fastapi.tiangolo.com/tutorial/metadata/).
