"""FastAPI application factory, lifespan, token-exchange root route, and static UI."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from noxai.agent.resume import mark_interrupted_runs
from noxai.api.auth import COOKIE_NAME, generate_session_token, load_session_token
from noxai.api.routes import active_runs, api_router
from noxai.api.websocket import ws_router
from noxai.config import Settings, get_config
from noxai.db import get_db, init_db
from noxai.scheduler import JobScheduler, seed_database_if_needed

log = logging.getLogger(__name__)

# Project root: src/noxai/api/app.py -> 3 levels up to repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
UI_DIST = REPO_ROOT / "ui" / "dist"


def create_app(
    config: Settings | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Create and configure the main FastAPI application for noxd."""
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup
        log.info("noxd daemon starting up...")

        # 1. Ensure / generate session token at daemon start per Section 6.1
        token = generate_session_token()
        app.state.session_token = token

        # 2. Initialize database schema
        await init_db()

        async with get_db() as db:
            # 3. Seed database with defaults if needed
            await seed_database_if_needed(db)

            # 4. Mark interrupted runs from previous shutdown
            await mark_interrupted_runs(db)

        # 5. Start background job scheduler
        scheduler: JobScheduler | None = None
        if start_scheduler:
            scheduler = JobScheduler(config=cfg)
            app.state.scheduler = scheduler
            await scheduler.start()
            log.info("JobScheduler started in daemon.")

        yield

        # Shutdown
        log.info("noxd daemon shutting down...")
        if scheduler is not None:
            await scheduler.stop()

        # Cancel any active running chat tasks
        for run_id, task in list(active_runs.items()):
            if not task.done():
                log.info("Cancelling active run %s on shutdown.", run_id)
                task.cancel()

    app = FastAPI(
        title="NoX Daemon",
        description="Autonomous AI Agent Daemon for Arch Linux & Hyprland",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # Allow local development and Web UI origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:7777",
            "http://localhost:7777",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount REST and WebSocket routers
    app.include_router(api_router)
    app.include_router(ws_router)

    # Mount static assets if UI has been built
    if UI_DIST.exists() and (UI_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")

    # Root landing & token exchange
    @app.get("/", response_class=Response)
    async def root_index(
        request: Request,
        t: str | None = Query(default=None),
    ) -> Response:
        """Root endpoint.
        If `?t=<token>` is supplied by `nx open`:
        Validates token, sets HttpOnly SameSite=Strict `nox_session` cookie, and redirects
        to `/` (cleaning URL query parameter).
        If already authenticated via cookie:
        Serves `ui/dist/index.html` if built, or daemon status HTML.
        """
        valid_token = load_session_token()
        if not valid_token:
            valid_token = generate_session_token()

        if t is not None:
            # Token exchange flow from `nx open`
            if secrets.compare_digest(t, valid_token):
                redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
                redirect.set_cookie(
                    key=COOKIE_NAME,
                    value=t,
                    httponly=True,
                    samesite="strict",
                    path="/",
                )
                return redirect
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid session token.",
            )

        # Check existing session cookie
        cookie_val = request.cookies.get(COOKIE_NAME)
        if cookie_val and secrets.compare_digest(cookie_val, valid_token):
            if UI_DIST.exists() and (UI_DIST / "index.html").exists():
                return FileResponse(str(UI_DIST / "index.html"))
            return HTMLResponse(
                "<!DOCTYPE html><html><head><title>NoX Daemon</title></head>"
                "<body style='font-family:sans-serif;padding:2rem;"
                "background:#0d1117;color:#c9d1d9;'>"
                "<h2>NoX Daemon Active</h2>"
                "<p>Authenticated session established. Web UI build pending in "
                "<code>ui/dist</code>.</p>"
                "</body></html>"
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: authentication required. Launch via 'nx open'.",
        )

    # SPA catch-all for frontend routes (e.g. /chat, /jobs, /briefings, etc.)
    @app.get("/{full_path:path}", response_class=Response)
    async def spa_fallback(request: Request, full_path: str) -> Response:
        # Do not catch /api or /ws
        if full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

        valid_token = load_session_token()
        cookie_val = request.cookies.get(COOKIE_NAME)
        if valid_token and cookie_val and secrets.compare_digest(cookie_val, valid_token):
            if UI_DIST.exists() and (UI_DIST / "index.html").exists():
                return FileResponse(str(UI_DIST / "index.html"))

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    return app
