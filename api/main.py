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

from api.channels import discord as discord_transport
from api.channels import email as email_transport
from api.channels import slack as slack_transport
from api.channels import telegram as telegram_transport
from api.config import Settings, get_settings
from api.db import check_database, create_engine, create_sessionmaker
from api.docs import describe
from api.errors import ErrorResponse, install_error_handlers
from api.extensions.builtin import BUILTIN
from api.extensions.registry import Registry, sync_catalogue
from api.jobs import builtin as builtin_jobs
from api.jobs.runner import ensure_schedule
from api.jobs.runner import loop as job_loop
from api.logging import configure_logging
from api.middleware.auth import AuthenticationMiddleware
from api.middleware.csrf import CsrfMiddleware
from api.middleware.limits import RequestLimitsMiddleware
from api.middleware.request_id import RequestIdMiddleware
from api.middleware.security_headers import SecurityHeadersMiddleware
from api.middleware.ws_auth import WebSocketAuthMiddleware
from api.routes import apps as apps_routes
from api.routes import assistants as assistant_routes
from api.routes import auth as auth_routes
from api.routes import backup as backup_routes
from api.routes import calendar as calendar_routes
from api.routes import catalogue as catalogue_routes
from api.routes import contacts as contact_routes
from api.routes import conversations as conversation_routes
from api.routes import discord_channel as discord_channel_routes
from api.routes import email_channel as email_channel_routes
from api.routes import home as home_routes
from api.routes import invites as invite_routes
from api.routes import knowledge as knowledge_routes
from api.routes import mcp as mcp_routes
from api.routes import meta_chat_channels as meta_chat_routes
from api.routes import notifications as notification_routes
from api.routes import numbers as number_routes
from api.routes import public_chat as public_chat_routes
from api.routes import recovery as recovery_routes
from api.routes import rules as rule_routes
from api.routes import settings as settings_routes
from api.routes import setup as setup_routes
from api.routes import slack_channel as slack_channel_routes
from api.routes import system as system_routes
from api.routes import telegram_channel as telegram_channel_routes
from api.routes import tokens as token_routes
from api.routes import web_channel as web_channel_routes
from api.routes import webhooks as webhook_routes
from api.routes import whatsapp_channel as whatsapp_channel_routes
from api.routes import widget as widget_routes
from api.routes import workspaces as workspace_routes

# One sentence per group, and each says what the group is *not* wherever that is the
# thing a reader gets wrong. A tag with no description is a heading; a tag with one is a
# map, and this document is the product's public surface (§B6).
#
# `tests/test_openapi.py` fails if a route uses a tag that is missing here, so a new
# router cannot quietly add a nineteenth unexplained word.
TAGS_METADATA = [
    {
        "name": "system",
        "description": (
            "Liveness and version. `/health` is the endpoint a reverse proxy, a "
            "monitor or an operator hits first, and it reports what it has actually "
            "verified rather than that the process is running."
        ),
    },
    {
        "name": "setup",
        "description": (
            "First run, and only first run. There is nobody to authenticate as before "
            "this, so it is public and defends itself by refusing to run twice."
        ),
    },
    {
        "name": "authentication",
        "description": (
            "Signing in, signing out, the six-digit code, SSH-key sign-in and changing "
            "a password. **No public signup** (D-030): the first account comes from "
            "setup and every later one is invited."
        ),
    },
    {
        "name": "workspaces",
        "description": (
            "The tenant boundary (D-028), its members and their roles. Every other "
            "route on this page is scoped by the active workspace, sent as "
            "`X-Workspace-Id`; omitted, the caller's first workspace is assumed."
        ),
    },
    {
        "name": "invitations",
        "description": (
            "A one-time link by which an invited person names themselves (D-034). "
            "Guarded by the token in the path rather than by a session, because the "
            "caller has no account worth the name yet."
        ),
    },
    {
        "name": "conversations",
        "description": (
            "The transcript archive, every channel in one place — a phone call is a "
            "conversation on a `phone` channel (D-017), not a separate kind of thing. "
            "Includes whispering into one that is still running (§A6.7)."
        ),
    },
    {
        "name": "contacts",
        "description": (
            "The phonebook. It is what turns an identity the channel knows — a number, "
            "a chat id — into a name on a screen, and nothing else reads it."
        ),
    },
    {
        "name": "rules",
        "description": (
            "Routing: who reaches the agent, who is refused, who goes straight "
            "through. Decided from a channel identity, not from a phone number, so one "
            "table serves every channel."
        ),
    },
    {
        "name": "numbers",
        "description": (
            "The registry of numbers this installation answers on. Adding, disabling "
            "and releasing one; SIP credentials arrive with the SIP milestone, because "
            "§B9.2 wants them encrypted and `sip_config` is plain JSON today."
        ),
    },
    {
        "name": "assistants",
        "description": (
            "Who the agent is: persona, instructions, and which tools it may use. This "
            "is what the business says to its customers, which is why every change is "
            "in the audit trail."
        ),
    },
    {
        "name": "knowledge",
        "description": (
            "What the agent is allowed to read before it answers. Adding a source "
            "changes the answers a customer gets, so it is recorded like a change to "
            "the assistant rather than like a file upload."
        ),
    },
    {
        "name": "calendar",
        "description": (
            "The free-busy week from the connected CalDAV source — busy periods with "
            "no names and no details, because RFC 4791's free-busy report carries "
            "none. Read only: the agent proposes and confirms, it never books (§B7)."
        ),
    },
    {
        "name": "catalogue",
        "description": (
            "Services and their prices. A price here is quoted to a customer by an "
            "agent that cannot check it against anything, so who changed one and when "
            "is the only record of why a caller was told a number."
        ),
    },
    {
        "name": "web chat",
        "description": (
            "The web chat channel and the widget it serves (§B14). The widget's own two "
            "routes are the only unauthenticated ones in the product, guarded by an "
            "origin allowlist, a captcha and a rate limit instead of by a session."
        ),
    },
    {
        "name": "telegram",
        "description": (
            "The Telegram channel (§B13) — a bot from the customer's own @BotFather, "
            "its token stored encrypted and masked on every read. The transport is "
            "long polling, so nothing has to be opened to the internet; these routes "
            "are only its settings card."
        ),
    },
    {
        "name": "email",
        "description": (
            "The email channel (§B13) — an IMAP/SMTP mailbox the customer already "
            "owns, its password stored encrypted and masked on every read. Not the "
            "installation's notification SMTP: that one talks to the operator, this "
            "one talks to customers. These routes are only its settings card."
        ),
    },
    {
        "name": "whatsapp",
        "description": (
            "The WhatsApp channel (§B13) — the customer's own Meta application: an "
            "access token, a phone number id, and the app secret that signs every "
            "webhook Meta delivers. Inbound arrives on a public webhook because the "
            "Cloud API offers nothing to poll; these routes are its settings card."
        ),
    },
    {
        "name": "messenger",
        "description": (
            "The Messenger channel (§B13) — a Facebook page answered through the "
            "customer's own Meta application, messaged with the page access token. "
            "Inbound shares Meta's webhook door with Instagram; these routes are its "
            "settings card."
        ),
    },
    {
        "name": "instagram",
        "description": (
            "The Instagram channel (§B13) — a Business account linked to a Facebook "
            "page, its DMs answered through that page's token from the same Meta "
            "application. Inbound shares Meta's webhook door with Messenger; these "
            "routes are its settings card."
        ),
    },
    {
        "name": "discord",
        "description": (
            "The Discord channel (§B13) — a bot from the customer's own developer "
            "portal, listening on the gateway WebSocket because Discord offers "
            "nothing else for conversational messages. DMs always answer; server "
            "channels only when mentioned. These routes are its settings card."
        ),
    },
    {
        "name": "slack",
        "description": (
            "The Slack channel (§B13) — Socket Mode, so nothing is exposed to the "
            "internet: two tokens from the customer's own Slack app, the app-level "
            "one opening the socket and the bot one speaking. DMs always answer; "
            "channels only as app mentions. These routes are its settings card."
        ),
    },
    {
        "name": "webhooks",
        "description": (
            "Where this installation posts what happened, and the secret that signs it. "
            "Rule 5 in `CLAUDE.md` is why this matters more than its size suggests: "
            "everything outside Tel-Agent's column is reached through here."
        ),
    },
    {
        "name": "tokens",
        "description": (
            "Credentials for the machine paths — `/hooks/…` and `/mcp` — each separate "
            "from the dashboard session and from each other (§B9.1). Shown once, stored "
            "as a hash, rotatable in place."
        ),
    },
    {
        "name": "apps",
        "description": (
            'Extensions (D-031). A channel is an extension, which is what keeps "add '
            'one more connector" from having no end.'
        ),
    },
    {
        "name": "settings",
        "description": (
            "Declared keys only, secrets encrypted and masked on read. Installation "
            "secrets live in `.env` and never here; credentials a person types live "
            "here and never in `.env` (§B9.2)."
        ),
    },
    {
        "name": "notifications",
        "description": (
            "What happened while nobody was watching, and what is waiting on a decision "
            "only a person can make. The two are kept apart on purpose."
        ),
    },
    {
        "name": "backup",
        "description": (
            "Archive, verify by reading back, retention, and a staged restore. One "
            "archive is every transcript on the installation, so downloading one is a "
            "data export and is recorded as one."
        ),
    },
    {
        "name": "home",
        "description": "The two counts the dashboard opens with (§A6.2). Nothing else.",
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
    # Extensions load after the database and before the first request, so no request
    # ever sees a half-loaded registry. A refusal is recorded and startup continues:
    # in an in-process design one broken extension must not stop the server answering.
    registry = Registry()
    for module_path in BUILTIN:
        registry.load(module_path)
    app.state.extensions = registry

    try:
        async with app.state.sessionmaker() as db:
            await sync_catalogue(db, registry)
    except Exception:
        # The catalogue mirrors what is running; it is not a precondition for running.
        # An unmigrated database must still start the server, or `alembic upgrade head`
        # could never be run against it.
        logging.getLogger("api.extensions").exception("could not sync the app catalogue")

    background: list[asyncio.Task] = []
    if settings.jobs_enabled:
        try:
            async with app.state.sessionmaker() as db:
                for name, interval in builtin_jobs.CORE_SCHEDULE.items():
                    await ensure_schedule(db, name, interval)
        except Exception:
            # A database that is not migrated yet must not stop the app from starting
            # and answering /health with the reason.
            logging.getLogger("api.jobs").exception("could not seed the core schedule")
        background.append(asyncio.create_task(job_loop(app.state.sessionmaker)))
        # The channel transports ride the same flag: background machinery with the
        # same "exactly one clock ticks" constraint, and tests drive each transport's
        # `poll_once` directly rather than racing a loop.
        background.append(asyncio.create_task(telegram_transport.loop(app.state.sessionmaker)))
        background.append(asyncio.create_task(email_transport.loop(app.state.sessionmaker)))
        background.append(asyncio.create_task(discord_transport.loop(app.state.sessionmaker)))
        background.append(asyncio.create_task(slack_transport.loop(app.state.sessionmaker)))

    try:
        yield
    finally:
        for task in background:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
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

    # What the generator cannot see: how to authenticate, and what each tag is for.
    describe(app, TAGS_METADATA)

    install_error_handlers(app)

    # Middleware, added innermost-first (the last one added is the outermost).
    # The stack a request passes through, outside in:
    #
    #     CORS -> request id -> CSRF -> authentication -> trusted host -> route
    #
    # One ordering rule, applied twice: **CORS is outermost, so every response the
    # browser must be able to read passes back out through it.**
    #
    # * The gates below produce refusals (401, 403). A response born outside the CORS
    #   layer carries no `Access-Control-Allow-Origin`, and a browser reports it as a
    #   network failure rather than a status - so the dashboard could never read the
    #   401 that should send it to the sign-in screen.
    # * `RequestIdMiddleware` catches unhandled exceptions and returns the 500 envelope
    #   *itself*. While it sat outside CORS, that envelope skipped the CORS layer on the
    #   way out, and every 500 reached the browser as `net::ERR_FAILED` with no status
    #   and no request id to quote. The one response class that most needs to be
    #   readable was the one that could not be read.
    #
    # Both were found from a browser. curl does not enforce CORS, so no curl test
    # catches either of them - `tests/test_errors.py` asserts the header instead.
    #
    # The cost of this order: a CORS preflight answered by the CORS layer never reaches
    # `RequestIdMiddleware`, so it is not logged under an id. A preflight carries no
    # application meaning, so that is the right thing to give up.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    # Innermost of the application's own middleware, so it sees the response every
    # route produced - including the ones that set a policy of their own, which it
    # then leaves alone. See the module docstring: overwriting the widget's
    # `frame-ancestors` would unembed every customer's chat bubble.
    app.add_middleware(SecurityHeadersMiddleware, hsts_seconds=settings.hsts_seconds)
    app.add_middleware(AuthenticationMiddleware)
    # The websocket twin of the gate above: BaseHTTPMiddleware never sees websocket
    # scopes, so without this every websocket route would bypass authentication.
    app.add_middleware(WebSocketAuthMiddleware)
    app.add_middleware(CsrfMiddleware)
    # Between CSRF and the request id on purpose: a refusal here still carries an id the
    # dashboard can quote, and an oversized body is dropped before authentication,
    # session lookup and routing have been paid for.
    app.add_middleware(
        RequestLimitsMiddleware,
        max_body_bytes=settings.max_body_bytes,
        timeout_seconds=settings.request_timeout_seconds,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # So a browser can read the id off a failed response and show it to the user.
        expose_headers=["X-Request-Id"],
    )

    app.include_router(apps_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(notification_routes.router)
    app.include_router(system_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(conversation_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(recovery_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(invite_routes.router)
    app.include_router(number_routes.router)
    app.include_router(assistant_routes.router)
    app.include_router(knowledge_routes.router)
    app.include_router(public_chat_routes.router)
    app.include_router(web_channel_routes.router)
    app.include_router(widget_routes.router)
    app.include_router(webhook_routes.router)
    app.include_router(rule_routes.router)
    app.include_router(contact_routes.router)
    app.include_router(home_routes.router)
    app.include_router(catalogue_routes.router)
    app.include_router(calendar_routes.router)
    app.include_router(setup_routes.router)
    app.include_router(token_routes.router)
    app.include_router(mcp_routes.router)
    app.include_router(telegram_channel_routes.router)
    app.include_router(email_channel_routes.router)
    app.include_router(whatsapp_channel_routes.router)
    app.include_router(whatsapp_channel_routes.card)
    app.include_router(meta_chat_routes.router)
    app.include_router(meta_chat_routes.messenger_card)
    app.include_router(meta_chat_routes.instagram_card)
    app.include_router(discord_channel_routes.router)
    app.include_router(slack_channel_routes.router)

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
