import asyncio
import contextlib
import gc
import logging
import signal
import threading
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api.src.api import auth, avatars, dashboard, home, orgs, projects, runs, s3, wandb_protocol
from app.api.src.api.deps import APIError, JSONResponse
from app.api.src.core.errors import (
    BadGateway,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    TooManyAttempts,
)
from app.api.src.core.store import Store
from app.api.src.core.table import FilterError
from app.api.src.settings import Settings

log = logging.getLogger(__name__)


def create_app(store: Store) -> FastAPI:
    """Build the ASGI app serving the wandb protocol and the read API; it opens ``store``."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await store.open()
        if store.accounts.signup_open:
            # A fresh server: say where the first account comes from, since every request
            # (including the dashboard's) is refused until it exists.
            setup = store.settings.web_url
            log.warning(
                "no accounts yet: %s to create the first one, which becomes the super admin",
                f"open {setup.rstrip('/')}/setup" if setup else "open the dashboard",
            )
        # Move everything loaded at startup out of the cyclic GC's view so full collections
        # don't stall requests scanning millions of long-lived run-table objects.
        gc.collect()
        gc.freeze()

        async def watch_crashed_runs(interval: float) -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    for run in await store.mark_crashed_runs():
                        log.info("marked run %s as crashed", run.path)
                except Exception:
                    log.exception("crash detection failed")

        # Check often enough to notice a crash within ~1.25 timeouts, at least every 30s.
        interval = min(30.0, max(store.settings.crash_timeout / 4, 0.05))
        watcher = asyncio.create_task(watch_crashed_runs(interval))

        # The server waits for open responses to finish before shutting the app down, and
        # dashboard streams never finish on their own: end them as soon as a shutdown signal
        # arrives (clients reconnect and replay). Handlers can only be set on the main thread.
        loop = asyncio.get_running_loop()
        chained = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous = signal.getsignal(sig)
                if callable(previous):

                    def on_signal(signum, frame, previous=previous):
                        loop.call_soon_threadsafe(store.live.close)
                        previous(signum, frame)

                    chained[sig] = previous
                    signal.signal(sig, on_signal)
        # Per-request access logs cost latency and are noise for an API that dashboards poll.
        # (Set here: the server configures logging after importing this module.)
        logging.getLogger("uvicorn.access").disabled = True
        try:
            yield
        finally:
            for sig, previous in chained.items():
                signal.signal(sig, previous)
            store.live.close()
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await store.close()

    app = FastAPI(
        title="Flock",
        description="wandb-compatible experiment tracking with low-latency read APIs",
        lifespan=lifespan,
        default_response_class=JSONResponse,
    )
    app.state.store = store

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> Response:
        # ``errors`` keeps GraphQL clients (the wandb SDK) able to surface the message.
        body = {"error": exc.message, "errors": [{"message": exc.message}], **exc.extra}
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict) -> Response:
        return JSONResponse({"error": exc.message, "ids": exc.ids}, status_code=409)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden) -> Response:
        return JSONResponse({"error": exc.message, "ids": exc.ids}, status_code=403)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(TooManyAttempts)
    async def too_many_attempts(request: Request, exc: TooManyAttempts) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=429)

    @app.exception_handler(Invalid)
    async def invalid(request: Request, exc: Invalid) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.exception_handler(BadGateway)
    async def bad_gateway(request: Request, exc: BadGateway) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(FilterError)
    async def filter_error(request: Request, exc: FilterError) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if store.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=store.settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["*"],
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> Response:
        return JSONResponse({"ok": True})

    app.include_router(wandb_protocol.router)
    app.include_router(auth.router)
    app.include_router(orgs.router)
    app.include_router(projects.router)
    app.include_router(runs.router)
    app.include_router(dashboard.router)
    app.include_router(home.router)
    app.include_router(avatars.router)
    app.include_router(s3.router)
    return app


# The app `fastapi run app.py` serves, configured from FLOCK_* environment variables.
settings = Settings.load()
logging.basicConfig(
    level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
app = create_app(Store(settings))
