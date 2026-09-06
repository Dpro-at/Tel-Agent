"""Which model this installation answers with, and where that was decided.

§B9.2 puts a user-entered provider key in an encrypted column and the key that encrypts
it in `.env`. This module is the reader that turns those rows back into the thing the
agent understands, and it is on the `api/` side of the boundary because `agent/` may not
import `api/` and the store lives here.

**The store first, the environment second — per value.** That order is the point of P3:
`.env` is what an installer wrote once, the store is what the owner changed from the
settings screen afterwards, and a value set on a screen that lost to a stale environment
variable would be a setting that appears to save and does nothing. Per *value* rather
than per source, so an installation already running on `.env` can move one field at a
time: type a new model name, save, and the key that is already working keeps working.

**Read on every turn, and never cached.** §B9.2's table says a credential entered from
the UI takes effect immediately, and a cache is how that becomes "after the next
restart". The cost is one indexed read per reply, against a model call that is four
orders of magnitude slower.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from agent.config import ConfigurationError, LlmSettings, environment_values, settings_from
from agent.providers.llm import LLMProvider, provider_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession as DbSession

logger = logging.getLogger("api.llm")

# The keys, and what to call them when one of them is missing. A person who typed a key
# into a form cannot act on the words `LLM_API_KEY`, so the refusal names the field they
# are looking at instead - `agent.config.ENVIRONMENT_NAMES` is the same idea for the
# other source.
KEYS = {
    "provider": "llm.provider",
    "model": "llm.model",
    "api_key": "llm.api_key",
    "base_url": "llm.base_url",
}

# Said the same way wherever it is reported: the health row and the test button are
# both diagnosis surfaces, and two wordings for one fault read as two faults.
UNREADABLE_KEY = (
    "The stored key cannot be decrypted. ENCRYPTION_KEY has changed since it was "
    "saved - restore the old one, or save the key again."
)

SCREEN_NAMES = {
    "provider": "the provider",
    "model": "the model name",
    "api_key": "the API key",
}


async def stored_values(db: DbSession) -> dict[str, str]:
    """The four values as the store holds them, unmasked, empty string for unset.

    Unmasked on purpose and only here: `store.all_for` is what a screen reads and it
    masks, `store.get` is what a caller about to *use* a credential reads. This is that
    caller.
    """
    from api.settings import store

    return {field: str(await store.get(db, key) or "") for field, key in KEYS.items()}


async def resolve(db: DbSession) -> LlmSettings | None:
    """The model this installation will actually use, or `None` when it has none.

    Raises `ConfigurationError` for half a configuration - a provider named with
    nothing behind it. The caller decides what to do with that: the health screen
    shows it as the reason a row is red, and a conversation lets it raise, because an
    installation that thinks it has a model and does not must not answer as though it
    never had one.
    """
    stored = await stored_values(db)
    environment = environment_values()
    merged = {
        field: stored[field] or environment[field]
        for field in ("provider", "model", "api_key", "base_url")
    }
    return settings_from(**merged, names=SCREEN_NAMES)


async def resolve_provider(db: DbSession) -> LLMProvider | None:
    """`resolve`, as the object that streams. `None` when no model is configured."""
    settings = await resolve(db)
    return None if settings is None else provider_for(settings)


async def describe(db: DbSession) -> tuple[str, str | None]:
    """A state and a detail for the health screen, without spending a model request.

    A health screen that calls the model every time it is opened is a health screen
    with a bill, and what an owner needs from this row - whether this installation has
    a model at all - is answerable without one. A model that is configured and refusing
    shows up where it matters, in a conversation, and lands in the tray.
    """
    from api.security.crypto import DecryptionFailed

    try:
        settings = await resolve(db)
    except ConfigurationError as broken:
        # Half a configuration is worse than none: the agent refuses to answer and the
        # owner has no way to see why from the outside. This row is that way.
        return "down", str(broken)
    except DecryptionFailed:
        # The stored key cannot be opened - ENCRYPTION_KEY was rotated or lost, and
        # every credential on this installation is in the same state. Answered rather
        # than raised, because the health screen is exactly where somebody goes when
        # credentials stop working, and a 500 there is the least useful moment for one.
        # The reply path still raises: a turn that cannot read its key must not answer
        # as though no model were configured.
        logger.exception("the stored model key could not be decrypted")
        return "down", UNREADABLE_KEY
    if settings is None:
        return "not_configured", None
    # The model and where it lives. Never the key - this endpoint is admin-only, and
    # that is not a reason to hand one back.
    return "ok", f"{settings.model} at {settings.base_url}"


class ModelListUnavailable(Exception):
    """The endpoint answered, but not with a list of models.

    Some OpenAI-format endpoints do not serve ``/models`` at all, and a few answer
    with a shape that is not the OpenAI one. Neither is the operator's fault and neither
    means the key is wrong - the screen falls back to a typed model name.
    """


async def list_models(base_url: str, api_key: str) -> list[str]:
    """Ask an OpenAI-format endpoint which models this key may use.

    ``GET {base_url}/models`` with the key as a bearer token; the two extra headers are
    what the Anthropic compatibility layer wants and every other endpoint ignores.
    Errors are httpx's own - the route turns them into designed answers - except the
    two shapes of "answered, but not with models", which become
    :class:`ModelListUnavailable`.

    The key is used and forgotten: nothing here stores or logs it.
    """
    import httpx

    address = f"{base_url.strip().rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(address, headers=headers)
    if response.status_code in (404, 405, 501):
        raise ModelListUnavailable(f"{response.status_code} from /models")
    response.raise_for_status()
    try:
        rows = response.json()["data"]
        ids = [str(row["id"]) for row in rows]
    except (ValueError, KeyError, TypeError) as odd:
        raise ModelListUnavailable("not an OpenAI-shaped model list") from odd
    # The Gemini compatibility layer prefixes every id with "models/"; the chat route
    # wants the bare name.
    return sorted({model_id.removeprefix("models/") for model_id in ids if model_id})


# --- Models on this machine ----------------------------------------------------
#
# A runtime that serves models locally speaks the same OpenAI wire format as the
# cloud, so nothing in `agent/` changes: the difference is that it lives on a loopback
# port, needs no key ("local" is stored - the endpoint ignores it), and can be found
# by asking. The list below is what is asked, in the order a home machine is likely
# to have them. Only the first can also download a model on request.

KNOWN_LOCAL_RUNTIMES: tuple[tuple[str, str, int, str | None], ...] = (
    # id, name, port, native listing path (None: the OpenAI /models route)
    ("ollama", "Ollama", 11434, "/api/tags"),
    ("lmstudio", "LM Studio", 1234, None),
    ("jan", "Jan", 1337, None),
    ("gpt4all", "GPT4All", 4891, None),
    ("koboldcpp", "KoboldCpp", 5001, None),
    ("llamacpp", "llama.cpp server", 8080, None),
    ("vllm", "vLLM", 8000, None),
)

PROBE_TIMEOUT = 1.5


@dataclass(frozen=True)
class LocalModel:
    id: str
    size_bytes: int | None


@dataclass(frozen=True)
class LocalRuntime:
    id: str
    name: str
    base_url: str
    native_url: str
    models: tuple[LocalModel, ...]
    can_pull: bool


def local_hosts() -> list[str]:
    """Where "this machine" is from the API's point of view.

    Inside a container the loopback address is the container's own, and the runtime
    the operator installed sits on the host - which Docker exposes under one name.
    """
    hosts = ["127.0.0.1"]
    if os.path.exists("/.dockerenv"):
        hosts.append("host.docker.internal")
    return hosts


async def _probe(
    client: httpx.AsyncClient,
    host: str,
    runtime_id: str,
    name: str,
    port: int,
    native: str | None,
) -> LocalRuntime | None:
    origin = f"http://{host}:{port}"
    try:
        if native:
            answer = await client.get(f"{origin}{native}")
            answer.raise_for_status()
            rows = answer.json().get("models", [])
            models = tuple(
                LocalModel(
                    id=str(row.get("name") or row.get("model")), size_bytes=row.get("size")
                )
                for row in rows
                if row.get("name") or row.get("model")
            )
        else:
            answer = await client.get(f"{origin}/v1/models")
            answer.raise_for_status()
            models = tuple(
                LocalModel(id=str(row["id"]), size_bytes=None)
                for row in answer.json().get("data", [])
                if row.get("id")
            )
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return None
    return LocalRuntime(
        id=runtime_id,
        name=name,
        base_url=f"{origin}/v1",
        native_url=origin,
        models=models,
        can_pull=runtime_id == "ollama",
    )


async def discover_local_runtimes() -> list[LocalRuntime]:
    """Every known runtime that answers on this machine, with the models it holds.

    All probes run at once and each gives up after `PROBE_TIMEOUT`, so a machine with
    nothing installed answers in under two seconds rather than in seven times that.
    """
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        found = await asyncio.gather(
            *(
                _probe(client, host, runtime_id, name, port, native)
                for host in local_hosts()
                for runtime_id, name, port, native in KNOWN_LOCAL_RUNTIMES
            )
        )
    return [runtime for runtime in found if runtime is not None]


def total_memory_gb() -> float | None:
    """How much memory this machine has - the one number that decides which local
    model is sensible. `None` where it cannot be read; the screen then recommends
    nothing rather than guessing."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return round(status.ullTotalPhys / 1024**3, 1)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / 1024**3, 1)
    except (AttributeError, ValueError, OSError):
        return None


def is_local_origin(url: str) -> bool:
    """Only this machine's runtimes may be asked to download: the route is a proxy,
    and a proxy that reaches anywhere is a hole."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


async def pull_local_model(native_url: str, model: str) -> AsyncIterator[bytes]:
    """Ask the runtime to download a model, relaying its progress line by line.

    The runtime streams newline-delimited JSON (`status`, `total`, `completed`); each
    line is passed on unchanged, so the screen can draw a bar from the same numbers
    the runtime's own tools show. A failure to reach the runtime becomes one last
    line of the same shape rather than a broken stream.
    """
    address = f"{native_url.rstrip('/')}/api/pull"
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client,
            client.stream("POST", address, json={"model": model, "stream": True}) as response,
        ):
            if response.status_code >= 400:
                yield (
                    json.dumps(
                        {"status": "error", "error": f"runtime answered {response.status_code}"}
                    ).encode()
                    + b"\n"
                )
                return
            async for line in response.aiter_lines():
                if line.strip():
                    yield line.encode() + b"\n"
    except httpx.HTTPError as unreachable:
        logger.warning(
            "the local runtime stopped answering during a download",
            extra={"error": type(unreachable).__name__},
        )
        yield json.dumps({"status": "error", "error": "unreachable"}).encode() + b"\n"
