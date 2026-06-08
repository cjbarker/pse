"""FastAPI application entry point: wires routers, static files, and templates."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import admin, api, search

app = FastAPI(title="PSE — Personalized Search Engine", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(search.router)
app.include_router(admin.router)
app.include_router(api.router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}
