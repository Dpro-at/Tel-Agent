"""Start the API — the entry point that honours where it is told to listen.

    python -m api          # or `tel-agent`, once installed

**Why this exists at all: a default only counts if something applies it.** The README
has said "do not expose the port to the internet" since before there was a port, and
nothing enforced it — the host came from whoever typed the `uvicorn` command, so a
setting saying `127.0.0.1` would have been decoration. This module is the place the
setting becomes the behaviour.

§B9's supported paths are a private network, a VPN, or a reverse proxy terminating TLS.
All three reach a process listening on loopback; none of them needs it listening on
every interface. So loopback is the default and widening it is a decision somebody
makes on purpose and sees a warning about.

**What this cannot do, said plainly.** Running `uvicorn api.main:app --host 0.0.0.0` by
hand still binds to every interface, and nothing here will know. That is why this is
the documented way to start the server rather than one option among several — a
default is only a default where it is the path of least effort. The application itself
never logs which address it is on, because it cannot find out, and a line claiming
loopback while the socket says otherwise would be worse than silence.
"""

from __future__ import annotations

import ipaddress
import logging

from api.config import get_settings
from api.logging import configure_logging

logger = logging.getLogger("api")

# Names that resolve to this machine and nowhere else. Checked before the numeric
# parse, because `localhost` is not an address and would otherwise look like a
# hostname somebody meant to expose.
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})

EXPOSED_WARNING = (
    "Listening on %s, which is not loopback: this port is now reachable from the "
    "network. The supported ways to reach an installation from elsewhere are a private "
    "network, a VPN, or a reverse proxy terminating TLS - each of which talks to a "
    "server on 127.0.0.1. Set BIND_HOST back unless you meant this."
)


def is_loopback(host: str) -> bool:
    """Whether this address keeps the port on this machine."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname that is not one of the two above. It is somebody's own name for a
        # machine on a network, which is exactly the case worth warning about.
        return False


def main() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)

    if not is_loopback(settings.bind_host):
        logger.warning(EXPOSED_WARNING, settings.bind_host)

    uvicorn.run(
        "api.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        # Off here on purpose. Reloading is a development convenience and it doubles
        # the process count; `--reload` belongs on a command somebody types while
        # working, not on the one an installation starts with.
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
