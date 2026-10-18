"""FastAPI application factory, lifespan, token-exchange root route, and static UI."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import stat
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from noxai.agent.resume import mark_interrupted_runs
from noxai.api.auth import (
    COOKIE_NAME,
    check_gate_authenticated,
    generate_session_token,
    load_session_token,
)
from noxai.api.routes import active_runs, api_router
from noxai.api.websocket import ws_router
from noxai.config import SECRETS_FILE, Settings, get_config
from noxai.db import get_db, init_db
from noxai.lock import DaemonLock
from noxai.scheduler import JobScheduler, seed_database_if_needed

log = logging.getLogger(__name__)

# Project root: src/noxai/api/app.py -> 3 levels up to repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
UI_DIST = REPO_ROOT / "ui" / "dist"


async def _poll_ollama_readiness(base_url: str, app: FastAPI) -> None:
    """Background polling task that detects when Ollama becomes available."""
    import httpx

    while True:
        try:
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    if not getattr(app.state, "ollama_reachable", False):
                        app.state.ollama_reachable = True
                        log.info("Ollama is reachable and ready at %s", base_url)
        except asyncio.CancelledError:
            break
        except Exception:
            app.state.ollama_reachable = False


async def _approval_sweeper_task() -> None:
    """Periodically sweep and expire pending approvals past their expiration time."""
    while True:
        try:
            await asyncio.sleep(10)
            async with get_db() as db:
                cursor = await db.execute(
                    "UPDATE approvals SET status = 'expired' "
                    "WHERE status = 'pending' AND expires_at IS NOT NULL "
                    "AND expires_at < datetime('now')"
                )
                if cursor.rowcount > 0:
                    await db.commit()
                    log.info(
                        "Approval timeout sweeper marked %d expired approval(s).",
                        cursor.rowcount,
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.debug("Approval sweeper check error: %s", exc)


async def _gate_recheck_task(cfg: Settings, app: FastAPI) -> None:
    """Periodically re-evaluate authentication gate when in command mode."""
    interval = getattr(cfg.gate, "check_interval_seconds", 30)
    while True:
        try:
            await asyncio.sleep(interval)
            auth_ok = await check_gate_authenticated(cfg)
            app.state.gate_authenticated = auth_ok
            if not auth_ok:
                log.warning("Gate re-check failed; authentication gate locked.")
            else:
                log.debug("Gate re-check passed.")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("Gate re-check error: %s", exc)


def create_app(
    config: Settings | None = None,
    start_scheduler: bool = True,
    manage_lock: bool = False,
) -> FastAPI:
    """Create and configure the main FastAPI application for noxd."""
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup sequence per Spec M7.1 / Phase E4.1
        log.info("NoX daemon startup sequence initiated...")

        # 1. Config and secrets verification (refuse if looser than 0600)
        log.info("1. Verifying config and secrets permissions...")
        if SECRETS_FILE.exists():
            file_mode = stat.S_IMODE(SECRETS_FILE.stat().st_mode)
            if file_mode & 0o177:
                raise PermissionError(
                    f"Secrets file {SECRETS_FILE} has unsafe permissions ({oct(file_mode)}). "
                    f"Expected 0600. Refusing to start daemon."
                )
        log.info("Config and secrets permissions verified.")

        # 2. Single-instance process lock
        lock: DaemonLock | None = None
        if manage_lock:
            log.info("2. Acquiring single-instance daemon lock...")
            lock = DaemonLock()
            lock.acquire()
            log.info("Single-instance lock acquired.")
        else:
            log.info("2. Single-instance lock managed externally.")

        # 3. Database initialization & schema migrations
        log.info("3. Applying database migrations...")
        await init_db()
        log.info("Database migrations verified.")

        async with get_db() as db:
            # 4. Database seeds (once)
            log.info("4. Applying database seeds...")
            await seed_database_if_needed(db)
            log.info("Database seeds verified.")

            # 5. Mark interrupted runs and job runs from previous shutdown
            log.info("5. Recovering interrupted runs and job runs from previous session...")
            interrupted_count = await mark_interrupted_runs(db)
            log.info("Recovered %d interrupted runs and jobs.", interrupted_count)

        # 6. Build model router
        log.info("6. Building ModelRouter...")
        from noxai.models.router import ModelRouter

        router = ModelRouter(config=cfg)
        app.state.model_router = router
        log.info("ModelRouter built successfully.")

        # 7. Check Ollama readiness (background probe, non-blocking)
        log.info("7. Checking Ollama readiness at %s...", cfg.models.ollama.base_url)
        ollama_task: asyncio.Task[None] | None = None
        try:
            import httpx

            resp = httpx.get(f"{cfg.models.ollama.base_url}/api/tags", timeout=0.8)
            if resp.status_code == 200:
                app.state.ollama_reachable = True
                log.info("Ollama is reachable and ready.")
            else:
                app.state.ollama_reachable = False
                log.warning(
                    "Ollama returned HTTP %d. Operating in degraded local mode.",
                    resp.status_code,
                )
        except Exception:
            app.state.ollama_reachable = False
            log.warning(
                "Ollama is unreachable at %s. Operating in degraded local mode.",
                cfg.models.ollama.base_url,
            )

        ollama_task = asyncio.create_task(
            _poll_ollama_readiness(cfg.models.ollama.base_url, app)
        )

        # 8. Start background JobScheduler
        scheduler: JobScheduler | None = None
        if start_scheduler:
            log.info("8. Starting JobScheduler...")
            scheduler = JobScheduler(config=cfg)
            app.state.scheduler = scheduler
            await scheduler.start()
            log.info("JobScheduler started in daemon.")
        else:
            log.info("8. JobScheduler startup skipped.")

        # 9. Start approval timeout sweeper
        log.info("9. Starting approval timeout sweeper...")
        sweeper_task = asyncio.create_task(_approval_sweeper_task())
        log.info("Approval timeout sweeper active.")

        # 10. Start gate re-check loop if gate mode is command
        gate_task: asyncio.Task[None] | None = None
        if cfg.gate.mode == "command":
            log.info("10. Starting gate re-check loop (command mode)...")
            gate_task = asyncio.create_task(_gate_recheck_task(cfg, app))
            log.info("Gate re-check loop active.")
        else:
            log.info(
                "10. Gate mode '%s' (no periodic command re-check needed).",
                cfg.gate.mode,
            )

        # 11. Generate session token
        log.info("11. Generating daemon session token...")
        token = generate_session_token()
        app.state.session_token = token
        log.info("Session token initialized: %s...", token[:8])

        # 12. Serve
        log.info(
            "12. NoX daemon startup complete. Serving on %s:%s",
            cfg.server.host,
            cfg.server.port,
        )

        yield

        # Graceful shutdown sequence on SIGTERM / SIGINT / lifespan exit
        log.info("NoX daemon shutdown initiated...")

        # 1. Cancel background tasks
        if ollama_task is not None:
            ollama_task.cancel()
        sweeper_task.cancel()
        if gate_task is not None:
            gate_task.cancel()

        # 2. Stop scheduler
        if scheduler is not None:
            log.info("Stopping JobScheduler...")
            await scheduler.stop()

        # 3. Cancel active running chat / DAG tasks
        for run_id, task in list(active_runs.items()):
            if not task.done():
                log.info("Cancelling active run %s on shutdown.", run_id)
                task.cancel()

        # 4. Finish DB writes & WAL checkpoint
        log.info("Checkpointing SQLite WAL...")
        try:
            async with get_db() as db:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await db.commit()
            log.info("SQLite WAL checkpoint complete.")
        except Exception as exc:
            log.warning("WAL checkpoint during shutdown encountered: %s", exc)

        # 5. Release single-instance lock
        if lock is not None:
            lock.release()

        log.info("NoX daemon shutdown complete.")

    app = FastAPI(
        title="NoX Daemon",
        description="Autonomous AI Agent Daemon for Arch Linux & Hyprland",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )

    # Correlation ID, body size limit (1 MB), and response headers middleware
    @app.middleware("http")
    async def correlation_and_security_middleware(request: Request, call_next: Any) -> Response:
        corr_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.correlation_id = corr_id

        # 1 MB body size cap per spec §6.1 / E3
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 1_048_576:
                    resp = JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={
                            "error": {
                                "code": "payload_too_large",
                                "message": "Request body exceeds 1 MB limit",
                                "correlation_id": corr_id,
                            }
                        },
                    )
                    resp.headers["X-Request-Id"] = corr_id
                    return resp
            except ValueError:
                pass

        try:
            response: Response = await call_next(request)
        except Exception as exc:
            log.error(
                "Unhandled server error correlation_id=%s: %s",
                corr_id,
                exc,
                exc_info=True,
            )
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "Something went wrong",
                        "correlation_id": corr_id,
                    }
                },
            )

        response.headers["X-Request-Id"] = corr_id
        # Security headers per Spec §6.1 & Phase E4.4
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Exception handlers for standard JSON envelope
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        corr_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        code = (
            "not_found"
            if exc.status_code == 404
            else "unauthorized"
            if exc.status_code == 401
            else "forbidden"
            if exc.status_code == 403
            else f"http_{exc.status_code}"
        )
        resp = JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "correlation_id": corr_id,
                }
            },
        )
        resp.headers["X-Request-Id"] = corr_id
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        corr_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        resp = JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": exc.errors(),
                    "correlation_id": corr_id,
                }
            },
        )
        resp.headers["X-Request-Id"] = corr_id
        return resp

    # CORS locked down: only enabled in dev mode NOX_DEV=1 per Spec D4 / E4.3
    if os.environ.get("NOX_DEV") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Mount REST and WebSocket routers
    app.include_router(api_router)
    app.include_router(ws_router)

    # 308 permanent redirect from /v1/* to /api/* per E4
    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def redirect_v1(request: Request, path: str) -> Response:
        target = f"/api/{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(
            url=target,
            status_code=status.HTTP_308_PERMANENT_REDIRECT,
        )

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
            if hmac.compare_digest(t, valid_token):
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
        if cookie_val and hmac.compare_digest(cookie_val, valid_token):
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
        if valid_token and cookie_val and hmac.compare_digest(cookie_val, valid_token):
            if UI_DIST.exists():
                try:
                    resolved = (UI_DIST / full_path).resolve()
                    if resolved.is_relative_to(UI_DIST.resolve()) and resolved.is_file():
                        return FileResponse(str(resolved))
                except (ValueError, RuntimeError):
                    pass
                if (UI_DIST / "index.html").exists():
                    return FileResponse(str(UI_DIST / "index.html"))

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    return app
