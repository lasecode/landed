from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings, validate_production_settings
from .db import init_db
from .routers import admin, auth, customers, dev, invoices, payments, public, webhooks, whatsapp
from .routers import settings as settings_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.env == "development":
        init_db()  # production uses `alembic upgrade head` instead
    sched = None
    if settings.embed_scheduler:
        from .scheduler import build

        sched = build(background=True)
        sched.start()
    yield
    if sched:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    validate_production_settings()
    app = FastAPI(title=f"{settings.app_name} API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.is_production else "/docs", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])
    for r in (auth, invoices, customers, payments, settings_router, webhooks, whatsapp, public, admin, dev):
        app.include_router(r.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built React dashboard from the same service (SPA fallback to index.html)."""
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist = Path(settings.web_dist) if settings.web_dist else None
    if not dist or not (dist / "index.html").is_file():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "admin/", "webhooks/", "r/")):
            from fastapi import HTTPException

            raise HTTPException(404, "Not found")
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(index)


app = create_app()
