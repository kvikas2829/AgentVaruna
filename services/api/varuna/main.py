"""Agent Varuna API. Run with: uvicorn varuna.main:app"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, seed
from .bootstrap import bootstrap
from .config import settings
from .db import get_engine, session_scope
from .models import Session
from .routes import admin, demo, ingest, monitor, system

log = logging.getLogger("varuna")


def _seed_in_background():
    try:
        n = seed.seed()
        log.info("Seeded %s sample sessions", n)
    except Exception:  # noqa: BLE001
        log.exception("Seeding failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    with session_scope() as db:
        bootstrap(db)
        empty = db.query(Session).first() is None
    if empty and settings.seed_on_empty:
        threading.Thread(target=_seed_in_background, daemon=True).start()
    yield


app = FastAPI(title="Agent Varuna", version=__version__, lifespan=lifespan)
for r in (system.router, ingest.router, admin.router, monitor.router, demo.router):
    app.include_router(r)


@app.get("/healthz")
def healthz():
    return {"ok": True}


dist = settings.web_dist
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("v1/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = dist / path
        return FileResponse(f if path and f.is_file() else dist / "index.html")
