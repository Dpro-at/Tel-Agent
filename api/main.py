"""The FastAPI application: the first runnable thing, and what every later task attaches to.

This service never touches audio. It reads what the agent wrote and serves it, and it
does not call the agent — the database is the boundary between them
(docs/ARCHITECTURE.md, enforced by `.importlinter`).

Run it:

    uvicorn api.main:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.db import check_database, create_engine, create_sessionmaker
from api.errors import ErrorResponse, install_error_handlers
from api.jobs import builtin as builtin_jobs
from api.jobs.runner import ensure_schedule
from api.jobs.runner import loop as job_loop
from api.logging import configure_logging
from api.middleware.auth import AuthenticationMiddleware
from api.middleware.csrf import CsrfMiddleware
from api.middleware.request_id import RequestIdMiddleware
from api.middleware.ws_auth import WebSocketAuthMiddleware
from api.routes import auth as auth_routes
from api.routes import recovery as recovery_routes

TAGS_METADATA = [
    {
        "name": "system",
        "description": (
            "Liveness and version. `/health` is the endpoint a reverse proxy, a "
            "monitor or an operator hits first."
        ),
    },
]

DESCRIPTION = """
The REST and WebSocket API for Tel-Agent — an AI agent that answers the phone and the
messaging channels a customer reaches a business on.

The dashboard consumes this same API, so it exists anyway; it is simply public and
documented. Authorisation is enforced here rather than in the browser: anything the
browser can bypass is not a rule.
""".strip()


class Checks(BaseModel):
    """Each dependency, reported separately.

    Separately rather than as one boolean because "something is wrong" sends an
    operator looking, and "the database is unreachable" sends them somewhere. Provider
    reachability and SIP registration join this model at their own milestones; each is
    a field here rather than another endpoint to remember.
    """

    database: bool
    # What the scheduler last saw, per task. A silently dead service is worse than an
    # obviously dead one (SPEC B8), and a clock that stopped ticking an hour ago is
    # exactly that kind of quiet failure - `/health` returning ok while the housekeeping
    # has not run since Tuesday would be a lie of omission.
    scheduler: dict[str, dict[str, object]] | None = None


class Health(BaseModel):
    """What `/health` answers.

    A deep check, per §B8 of the specification: it reports what it has actually
    verified. `degraded` means the process is serving requests while something it
    depends on is not answering — which is the state worth alerting on, and the one a
    plain liveness probe hides.
    """

    status: Literal["ok", "degraded"]
    version: str
    environment: Literal["development", "production"]
    checks: Checks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the engine on startup, dispose of it on shutdown.

    The engine is created here rather than at import so that building an application
    does not open sockets — a test that never issues a query should not need a
    reachable database, and `alembic` importing this module should not either.

    `dispose()` on the way out returns every pooled connection. Without it a reload in
    development leaks the whole pool on each restart, and the server runs out of
    connections long before anyone suspects the dashboard.
    """
    settings: Settings = app.state.settings
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)

    # The installation's own clock. One loop in this process rather than a worker to
    # deploy: the smallest installation is one machine and somebody who does not run a
    # process manager. Without it the housekeeping tasks have no caller at all, which
    # is the state this codebase was in until P2.
    worker: asyncio.Task | None = None
    if settings.jobs_enabled:
        try:
            async with app.state.sessionmaker() as db:
                for name, interval in builtin_jobs.CORE_SCHEDULE.items():
                    await ensure_schedule(db, name, interval)
        except Exception:
            # A database that is not migrated yet must not stop the app from starting
            # and answering /health with the reason.
            logging.getLogger("api.jobs").exception("could not seed the core schedule")
        worker = asyncio.create_task(job_loop(app.state.sessionmaker))

    try:
        yield
    finally:
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await engine.dispose()


def get_session(request: Request) -> AsyncSession:
    """The request-scoped session dependency.

    Every route that touches the database takes this.

    Opened by `AuthenticationMiddleware`, which needs it before any route runs. Handing
    the same object out here keeps a request to one connection whatever it touches.
    """
    return request.state.db


# The annotation every route that touches the database uses:
#
#     async def handler(session: SessionDep) -> ...
#
# `Annotated` rather than `= Depends(get_session)` in the signature. A call in an
# argument default is evaluated once at import, which is a real bug for anything but
# FastAPI's markers - so the linter refuses the whole pattern, and this is the form
# that does not need an exception carved out for it.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    A factory rather than a module-level app so tests can build an application against
    a different configuration without mutating a global.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Tel-Agent",
        description=DESCRIPTION,
        version=settings.version,
        license_info={
            "name": "AGPL-3.0-or-later",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        contact={"name": "Tel-Agent", "url": "https://github.com/Dpro-at/Tel-Agent"},
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        # Documented once, on every route, rather than repeated per endpoint. Screens
        # branch on `error.code`; the message is prose and gets translated.
        responses={
            422: {"model": ErrorResponse, "description": "Validation error"},
            500: {"model": ErrorResponse, "description": "Unhandled server error"},
        },
    )

    # Read back by `lifespan`, which is what actually opens the engine.
    app.state.settings = settings

    install_error_handlers(app)

    # Middleware, added innermost-first (the last one added is the outermost).
    # The stack a request passes through, outside in:
    #
    #     request id -> CORS -> CSRF -> authentication -> trusted host -> route
    #
    # Two orderings here are load-bearing:
    #
    # * **CORS sits outside CSRF and authentication.** Both gates produce refusals
    #   (403, 401), and a response born outside the CORS layer carries no
    #   `Access-Control-Allow-Origin`. A browser then reports it as a network failure
    #   rather than a status - so the dashboard could never read the 401 that should
    #   send it to the sign-in screen. Found live, from the browser: curl does not
    #   enforce CORS, so no curl test could ever catch it.
    # * **The request id sits outside everything**, so even a refusal from the
    #   outermost gate is logged under an id.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(AuthenticationMiddleware)
    # The websocket twin of the gate above: BaseHTTPMiddleware never sees websocket
    # scopes, so without this every websocket route would bypass authentication.
    app.add_middleware(WebSocketAuthMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # So a browser can read the id off a failed response and show it to the user.
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(auth_routes.router)
    app.include_router(recovery_routes.router)

    @app.get(
        "/health",
        tags=["system"],
        summary="Deep health check",
        response_model=Health,
        responses={503: {"model": Health, "description": "A dependency is unreachable"}},
    )
    async def health(request: Request) -> JSONResponse:
        """Report what this process can actually reach.

        Answers 503 when a dependency is down, so a load balancer or an uptime monitor
        acts on it without having to parse the body. The body is still the full report
        rather than an empty error: whoever is looking needs to know *which* dependency.
        """
        engine = getattr(request.app.state, "engine", None)
        database_ok = await check_database(engine) if engine is not None else False

        scheduler: dict[str, dict[str, object]] | None = None
        if database_ok:
            try:
                from api.jobs.builtin import last_task_status

                async with request.app.state.sessionmaker() as db:
                    scheduler = await last_task_status(db)
            except Exception:
                # A database that is reachable but not yet migrated. The database check
                # already said what matters; an unreadable schedule is not a second
                # failure to report.
                scheduler = None

        report = Health(
            status="ok" if database_ok else "degraded",
            version=settings.version,
            environment=settings.environment,
            checks=Checks(database=database_ok, scheduler=scheduler),
        )
        return JSONResponse(
            status_code=200 if database_ok else 503,
            content=report.model_dump(),
        )

    return app


app = create_app()
