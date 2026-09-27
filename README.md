# Запуск проекта

## 1. Клонировать проект

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd delay_prediction_platform
```

## 2. Live-режим — первый запуск

Положить эмулятор сюда:

```text
data/dataset/ndtp-telemetry-emulator.tar
```

Один раз загрузить образ:

```bash
docker load -i data/dataset/ndtp-telemetry-emulator.tar
```

Запустить:

```bash
docker compose up -d --build
```

Открыть:

```text
http://127.0.0.1:8000/
```

Проверить:

```bash
docker compose ps -a
```

Остановить:

```bash
docker compose down
```

## 3. Historical-режим

Эмулятор не нужен.

Запустить:

```bash
PORT=8002 docker compose -f compose.historical.yaml up -d --build
```

Открыть:

```text
http://127.0.0.1:8002/
```

Проверить:

```bash
docker compose -f compose.historical.yaml ps -a
```

Посмотреть ход replay:

```bash
docker compose -f compose.historical.yaml logs -f replay
```

Остановить:

```bash
docker compose -f compose.historical.yaml down
```

После первого `docker load` для live-режима архив каждый раз загружать не нужно: Docker уже хранит образ `ndtp-telemetry-emulator:1.0` локально.

![Архитектура](schema.png)


# Производительность:

В лог добавлена статистика циклов:

средняя и максимальная длительность, превышения интервала запуска;
количество автобусов в батче;
средние затраты на автобус: прогнозирование, признаки и инференс;
ошибки циклов, baseline и пропуски.

Сводка по последним 100 попыткам цикла:

```bash
docker compose logs --no-color --no-log-prefix worker | python -m 
scripts.worker_stats --last 100
```

## Документация

- [Код — Sphinx](docs/README.md)
- [API — OpenAPI / Swagger](docs/API.md)
