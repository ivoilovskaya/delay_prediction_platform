from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.endpoints import router

app = FastAPI(title="Delay Prediction API")
app.include_router(router)
DISPATCHER = Path(__file__).resolve().parents[1] / "frontend" / "dispatcher"
app.mount("/dispatcher/assets", StaticFiles(directory=DISPATCHER / "assets"), name="dispatcher-assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(DISPATCHER / "index.html")


@app.get("/dispatcher", include_in_schema=False)
@app.get("/dispatcher/", include_in_schema=False)
def dispatcher():
    return RedirectResponse(url="/", status_code=307)
