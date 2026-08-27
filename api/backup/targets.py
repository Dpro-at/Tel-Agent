"""Where a backup goes — P7.

**One target kind in the core: a directory path.** That covers both of the targets the
backup screen calls local — a mounted network share and a USB disk are, to this code,
a path that either is writable or is not. What it deliberately does *not* cover is
S3, and that is a decision rather than a gap: object storage means credentials, a
client library, multipart uploads, retries and a bill, none of which the smallest
installation needs and all of which the extension contract (P5) exists to carry.

**The check that matters is not "is the path set", it is "can this process write there
right now".** A network share that stopped accepting the connection is the exact
failure the screen's own stale state describes — nine days of nightly jobs failing
against a path that still looks perfectly valid in the settings. So the target is
probed by writing a file and reading it back, before a backup is attempted and
whenever the operator asks.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("api.backup")

# Written and deleted by the probe. Named so an operator who finds one left behind
# after a crash knows what it was.
PROBE_NAME = ".telagent-write-test"


@dataclass(frozen=True)
class Probe:
    """What was actually true about the target at the moment it was asked."""

    ok: bool
    path: str
    detail: str
    free_bytes: int | None = None


def resolve(configured: str | None) -> Path | None:
    """The configured directory, or None when no target has been chosen.

    None is a real state with its own screen — "There is no backup of this
    installation" — not an error to be defaulted away. Silently falling back to a
    folder next to the database would be the worst possible default: it puts the only
    copy on the disk whose failure is the thing being insured against.
    """
    if not configured or not configured.strip():
        return None
    return Path(configured).expanduser()


def probe(configured: str | None) -> Probe:
    """Prove the target is writable by writing to it, not by inspecting it.

    `os.access` and a permissions check both lie on a network share: they answer from
    the mount, and the mount can be stale. Only a write finds that out.
    """
    target = resolve(configured)
    if target is None:
        return Probe(False, "", "no target configured")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return Probe(
            False, str(target), f"cannot create the directory: {error.strerror or error}"
        )

    probe_file = target / PROBE_NAME
    try:
        probe_file.write_bytes(b"telagent")
        # Read back, because a share can accept a write and drop it.
        if probe_file.read_bytes() != b"telagent":
            return Probe(False, str(target), "the target accepted a write and returned nothing")
    except OSError as error:
        return Probe(False, str(target), f"cannot write there: {error.strerror or error}")
    finally:
        try:
            probe_file.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - the write already failed
            pass

    free = None
    try:
        free = shutil.disk_usage(target).free
    except OSError:  # pragma: no cover - some network mounts do not report
        pass

    return Probe(True, str(target), "writable", free)


def has_room(configured: str | None, needed_bytes: int) -> bool:
    """Whether the target plausibly has space for one more archive.

    Asked before writing rather than discovered at 94% of the way through: a target
    that fills up mid-write leaves a partial file and, on a share holding the only
    copy, can leave *no* usable backup at all.
    """
    result = probe(configured)
    if not result.ok or result.free_bytes is None:
        return result.ok
    return result.free_bytes >= needed_bytes


def default_directory(root: Path) -> Path:
    """Where archives go when nothing is configured but one must be written anyway.

    Used by the pre-update snapshot, which cannot refuse: taking a backup onto the
    same disk is a poor backup and is still better than upgrading without one. Every
    caller of this must say so to the operator rather than letting it look configured.
    """
    return root / "var" / "backups"


def usable_name(taken_at: str, kind: str) -> str:
    """A filename that sorts by date and survives every filesystem this may land on.

    Colons are legal on Linux and are not on Windows or on an SMB share exported from
    one, which is exactly where these files go.
    """
    stamp = taken_at.replace(":", "").replace("-", "").replace("+0000", "Z")
    return f"telagent-{stamp}-{kind}.tar.gz".replace(" ", "T")


def remove(path: str | os.PathLike[str]) -> None:
    """Delete an archive, tolerating one that is already gone.

    Already-gone is the normal case for a USB disk that was swapped: the row is in the
    database and the file went home in somebody's bag. Pruning must not fail on it.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as error:
        logger.warning(
            "could not delete archive", extra={"path": str(path), "reason": str(error)}
        )
