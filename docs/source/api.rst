OpenAPI и Swagger
=================

Основной backend: `Swagger UI <http://127.0.0.1:8000/docs>`_ и
`OpenAPI JSON <http://127.0.0.1:8000/openapi.json>`_.
Historical-режим использует порт 8002, если запущен по README.
Отдельный ML-сервис: `Swagger ML <http://127.0.0.1:8001/docs>`_.

Сохранённые схемы:

* :download:`Backend OpenAPI <../backend_openapi.json>`
* :download:`ML OpenAPI <../ml_openapi.json>`

Обновление схем из корня проекта::

    python -m scripts.export_openapi
    python -m scripts.export_openapi --check

POST /replay/start меняет состояние воспроизведения.
POST /predict/from-db по умолчанию записывает прогнозы в БД.
