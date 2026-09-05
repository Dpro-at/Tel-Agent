"""Where the server listens — G3.

The README promised "do not expose the port" long before anything applied it. What is
tested here is that the promise is now the default, that widening it is loud, and that
`is_loopback` does not accidentally call somebody's own hostname safe.
"""

from __future__ import annotations

import logging

import pytest

from api.__main__ import EXPOSED_WARNING, is_loopback, main
from api.config import Settings


def test_the_default_keeps_the_port_on_this_machine() -> None:
    """§B9's three supported paths - a private network, a VPN, a reverse proxy
    terminating TLS - all reach a server on loopback. None needs one on every
    interface, so none is a reason to make this the other way round."""
    settings = Settings(_env_file=None)
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 38472


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST", "127.5.5.5"])
def test_the_addresses_that_stay_on_this_machine(host: str) -> None:
    assert is_loopback(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",  # noqa: S104 - the string being tested, not a bind
        "::",
        "192.168.1.10",
        "telagent.wagner-partner.local",
        "",
    ],
)
def test_the_addresses_that_do_not(host: str) -> None:
    """A hostname is the case worth being strict about: it is somebody's own name for a
    machine on a network, which is exactly the situation the warning is for."""
    assert is_loopback(host) is False


def test_widening_the_bind_is_said_out_loud(monkeypatch, caplog) -> None:
    """A default nobody is told they left is a default that stops meaning anything."""
    started: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        started.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("BIND_HOST", "0.0.0.0")  # noqa: S104 - the point of the test

    from api.config import get_settings

    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="api"):
            main()
    finally:
        get_settings.cache_clear()

    assert started["host"] == "0.0.0.0"  # noqa: S104 - asserting what was asked for
    # The message names what to do about it, not just that it happened.
    assert EXPOSED_WARNING[:40] in caplog.text or "not loopback" in caplog.text
    assert "reverse proxy" in caplog.text


def test_the_default_start_says_nothing(monkeypatch, caplog) -> None:
    """The warning has to be rare enough to read. An installation on loopback is the
    ordinary case and gets no line at all."""

    def fake_run(app: str, **kwargs: object) -> None:
        return None

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.delenv("BIND_HOST", raising=False)

    from api.config import get_settings

    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="api"):
            main()
    finally:
        get_settings.cache_clear()

    assert "not loopback" not in caplog.text
