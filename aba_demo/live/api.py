"""Loopback-only HTTP + WebSocket API for the SIMULATED live-session shell.

Run with ``python -m aba_demo.live``. This is a single-operator local tool, not
an authenticated multi-user service; never bind it to a public interface.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .runtime import (COMMANDS, CapacityExceeded, InvalidTransition, SessionManager,
                      SessionNotFound, SubscriberOverflow)

UI_DIST = Path(__file__).resolve().parents[2] / "live-ui" / "dist"
DEFAULT_PORT = 8767
DEV_UI_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
CSP = ("default-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str = Field(min_length=1, max_length=64)


class SessionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal[COMMANDS]
    activity: Literal["table", "movement", "break"] | None = None


def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code)


def create_app(manager: SessionManager | None = None, port: int = DEFAULT_PORT,
               dev_origins=(), ui_dist: Path = UI_DIST) -> FastAPI:
    manager = manager or SessionManager()
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", *dev_origins}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await manager.shutdown()

    app = FastAPI(title="ABA Live Session Shell — simulation", version="0.1.0",
                  lifespan=lifespan, docs_url="/api/live/docs", redoc_url=None,
                  openapi_url="/api/live/openapi.json")
    app.state.manager = manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed_origins:
                return _error(403, "Cross-origin write rejected")
            if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
                if origin not in allowed_origins:
                    return _error(403, "Cross-site write rejected")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/assets/"):
            # Vite content-hashes asset names, so they can be cached forever.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # The HTML shell must be revalidated so an update never runs a stale bundle.
            response.headers["Cache-Control"] = "no-cache"
        if not request.url.path.startswith("/api/live/docs"):
            response.headers["Content-Security-Policy"] = CSP
        return response

    @app.exception_handler(SessionNotFound)
    async def not_found(_request, _exc):
        return _error(404, "Session not found")

    @app.exception_handler(InvalidTransition)
    async def invalid_transition(_request, exc):
        return _error(409, str(exc))

    @app.exception_handler(CapacityExceeded)
    async def capacity(_request, exc):
        return _error(429, str(exc))

    @app.exception_handler(ValueError)
    async def bad_value(_request, exc):
        return _error(400, str(exc))

    @app.get("/api/live/health")
    async def health():
        return {"status": "ok", "mode": "simulation", "camera": False, "inference": False}

    @app.get("/api/live/scenarios")
    async def scenarios():
        return {"scenarios": manager.scenarios()}

    @app.get("/api/live/sessions")
    async def list_sessions():
        return {"sessions": manager.list()}

    @app.post("/api/live/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(body: CreateSession):
        return manager.create(body.scenario).snapshot()

    @app.get("/api/live/sessions/{session_id}")
    async def get_session(session_id: str):
        return manager.get(session_id).snapshot()

    @app.delete("/api/live/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def discard_session(session_id: str):
        await manager.discard(session_id)

    @app.post("/api/live/sessions/{session_id}/commands")
    async def command(session_id: str, body: SessionCommand):
        if (body.command == "set_activity") != (body.activity is not None):
            raise ValueError("activity is required for set_activity and only for set_activity")
        return await manager.get(session_id).command(body.command, body.activity)

    @app.websocket("/api/live/sessions/{session_id}/events")
    async def events(websocket: WebSocket, session_id: str, after: int = Query(0, ge=0)):
        if websocket.headers.get("origin") not in allowed_origins:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        try:
            runner = manager.get(session_id)
        except SessionNotFound:
            # Accept first so the client receives a definitive close code instead of retrying.
            await websocket.close(code=4404, reason="session not found")
            return
        backlog, subscription = runner.subscribe(after)
        try:
            await websocket.send_json({"event_type": "stream_ready", "snapshot": runner.snapshot(),
                                       "history_start": runner.history_start(), "after": after})
            for event in backlog:
                await websocket.send_json(event)

            async def pump():
                while True:
                    await websocket.send_json(await subscription.next())

            async def drain_client():
                while True:
                    await websocket.receive_text()

            tasks = {asyncio.create_task(pump()), asyncio.create_task(drain_client())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                exc = task.exception()
                if isinstance(exc, SubscriberOverflow):
                    await websocket.close(code=4408, reason="lagging; resume with after")
                elif exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            pass
        finally:
            subscription.close()

    if (ui_dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = (ui_dist / path).resolve()
            if path and candidate.is_file() and ui_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(ui_dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def not_built():
            return HTMLResponse("<h1>Live UI not built</h1><p>Run <code>npm ci &amp;&amp; npm run build"
                                "</code> in <code>live-ui/</code>, or use the Vite dev server.</p>")

    return app
