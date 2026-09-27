"""Основное FastAPI-приложение: готовые прогнозы, аналитика и dispatcher UI."""

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.endpoints import router
from backend.segment_analytics import router as analytics_router
from backend.replay import router as replay_router
from storage import replay
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Delay Prediction API",
    version="1.0.0",
    description="Готовые прогнозы задержек, активный транспорт и аналитика сегментов. "
                "Расчёты выполняют фоновые воркеры; HTTP backend читает результаты. "
                "В historical-режиме доступны состояние и запуск воспроизведения.",
    openapi_tags=[
        {"name": "health", "description": "Доступность HTTP-процесса, без проверки БД."},
        {"name": "vehicles", "description": "Активный транспорт, GPS и состояние на карте."},
        {"name": "predictions", "description": "Последние прогнозы и их свежесть."},
        {"name": "segment analytics", "description": "Состояние сегментов и активные предупреждения."},
        {"name": "historical replay", "description": "Состояние и управление историческим воспроизведением."},
    ],
)
app.include_router(router)
app.include_router(analytics_router)
app.include_router(replay_router)


@app.middleware('http')
async def replay_snapshot(request, call_next):
    if not replay.enabled() or not request.url.path.startswith(('/vehicles/', '/predictions/', '/analytics/', '/replay/')):
        return await call_next(request)
    try:
        state = replay.read_json('state.json')
        if not state and not request.url.path.startswith('/replay/'):
            return JSONResponse({'detail': 'Историческое воспроизведение подготавливается'}, status_code=503)
        with replay.scope(state):
            response = await call_next(request)
            if state:
                response.headers['X-Replay-Session'] = state['session']
            return response
    except (OSError, ValueError, KeyError):
        return JSONResponse({'detail': 'Состояние исторического воспроизведения недоступно'}, status_code=503)
DISPATCHER = Path(__file__).resolve().parents[1] / "frontend" / "dispatcher"
app.mount("/dispatcher/assets", StaticFiles(directory=DISPATCHER / "assets"), name="dispatcher-assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(DISPATCHER / "index.html")


@app.get("/dispatcher", include_in_schema=False)
@app.get("/dispatcher/", include_in_schema=False)
def dispatcher():
    return RedirectResponse(url="/", status_code=307)
