# Документация кода — Sphinx

Справочник строится из docstring и сигнатур Python-кода: backend, ML,
аналитика, хранение, очистка, приём NDTP и команды разработчика.
В HTML доступны поиск, индекс классов/функций и ссылки на исходный код.

Из корня проекта, в окружении Python 3.12+:

```bash
python -m pip install -r requirements.txt -r requirements-docs.txt
python -m sphinx -W --keep-going -b html docs/source docs/_build/html
python -m http.server 8080 --bind 127.0.0.1 --directory docs/_build/html
```

Открыть [справочник Sphinx](http://127.0.0.1:8080/).
Можно открыть `docs/_build/html/index.html` напрямую без HTTP-сервера.
Сборка импортирует модули, но не запускает приложения, обучение и воркеры;
работающие базы, эмулятор и загруженная модель не требуются.

Исходная точка справочника: [index.rst](source/index.rst).
При добавлении модуля добавьте `automodule` в соответствующий файл `docs/source/*.rst`.
Описания функций и классов редактируются в docstring кода (reStructuredText,
Google или NumPy style). CI собирает HTML с проверкой предупреждений и сохраняет
артефакт `documentation`, доступный на странице запуска GitHub Actions.

[Документация API — OpenAPI / Swagger](API.md).

Основа справочника — [Sphinx autodoc](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html).
