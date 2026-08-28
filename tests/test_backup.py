"""Backup and restore — P7.

The tests that matter here are not "does it write a file". They are the four ways a
backup system fails a business that trusted it:

* it wrote something that cannot be read back, and nobody found out until the restore;
* retention deleted the last good copy;
* the archive is a plain copy of every transcript, readable by whoever gets the file;
* a restore ran while the operator still believed nothing had happened yet.
"""

from __future__ import annotations

import datetime as dt
import json
import tarfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.backup import archive, service, targets
from api.config import Settings
from api.main import create_app
from api.models import Backup, Membership, User, Workspace
from api.security.password import hash_password
from api.settings import store

PASSWORD = "a sentence i can actually remember"  # noqa: S105


# --- The target ---------------------------------------------------------------


def test_no_target_is_a_state_not_an_error(tmp_path: Path) -> None:
    """ "Nowhere" is a screen of its own, not a path to be defaulted in."""
    assert targets.resolve(None) is None
    assert targets.resolve("   ") is None

    probe = targets.probe(None)
    assert probe.ok is False
    assert probe.detail == "no target configured"


def test_the_target_is_proved_by_writing_to_it(tmp_path: Path) -> None:
    probe = targets.probe(str(tmp_path / "share"))

    assert probe.ok is True
    # And it cleans up after itself: an operator opening the share should not find a
    # scattering of probe files.
    assert list((tmp_path / "share").iterdir()) == []


def test_an_unwritable_target_says_why(tmp_path: Path) -> None:
    """The failure the screen's stale state describes: the path still looks fine."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file")

    probe = targets.probe(str(blocker / "backups"))

    assert probe.ok is False
    assert probe.detail != "writable"


def test_the_filename_survives_windows_and_smb() -> None:
    """Colons are legal on Linux and are not on an SMB share exported from Windows -
    which is exactly where these files are written."""
    name = targets.usable_name("2026-08-27T03:00:00+00:00", "nightly")

    assert ":" not in name
    assert name.endswith("-nightly.tar.gz")


def test_deleting_an_archive_that_is_already_gone_is_not_an_error(tmp_path: Path) -> None:
    """The USB disk went home in somebody's bag. Pruning must not fail on it."""
    targets.remove(tmp_path / "never-existed.tar.gz")


# --- The archive --------------------------------------------------------------


@pytest.fixture
async def seeded(migrated: AsyncSession) -> AsyncSession:
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    await migrated.commit()
    return migrated


async def test_an_archive_contains_every_table_and_says_so(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    """Read from `Base.metadata`, never a hand-written list.

    A table added by a later migration and forgotten in a list here would be missing
    from every backup taken afterwards, and nothing would say so until a restore came
    back short.
    """
    destination = tmp_path / "one.tar.gz"

    manifest = await archive.write_archive(
        seeded, destination, kind="manual", key_id=None, schema_revision="abc", version="0.1.0"
    )

    expected = {table.name for table in archive.ordered_tables()}
    assert set(manifest["tables"]) == expected
    assert manifest["tables"]["workspaces"] == 1
    assert manifest["tables"]["users"] == 1


async def test_a_half_written_archive_is_never_left_looking_complete(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    """Written under `.partial` and renamed. During an outage somebody reaches for the
    newest file in that folder, and it must not be one that was interrupted."""
    destination = tmp_path / "two.tar.gz"

    await archive.write_archive(
        seeded, destination, kind="manual", key_id=None, schema_revision=None, version="0.1.0"
    )

    assert destination.is_file()
    assert list(tmp_path.glob("*.partial")) == []


async def test_verification_reads_the_whole_archive_back(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    destination = tmp_path / "three.tar.gz"
    await archive.write_archive(
        seeded, destination, kind="manual", key_id=None, schema_revision=None, version="0.1.0"
    )

    manifest = archive.verify(destination, archive.checksum(destination))

    assert manifest["product"] == "tel-agent"


async def test_a_corrupted_archive_is_caught_by_verification(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    """The whole promise of the screen's "known to restore" sits on this test."""
    destination = tmp_path / "four.tar.gz"
    await archive.write_archive(
        seeded, destination, kind="manual", key_id=None, schema_revision=None, version="0.1.0"
    )
    good = archive.checksum(destination)

    # A disk that rotted, or a share that truncated the write.
    data = bytearray(destination.read_bytes())
    data[len(data) // 2] ^= 0xFF
    destination.write_bytes(bytes(data))

    with pytest.raises(ValueError):
        archive.verify(destination, good)


async def test_a_foreign_tar_is_refused(tmp_path: Path) -> None:
    """Somebody points the restore at the wrong file. It must not be opened hopefully."""
    stranger = tmp_path / "holiday-photos.tar.gz"
    with tarfile.open(stranger, "w:gz") as tar:
        payload = tmp_path / "note.txt"
        payload.write_text("not a backup")
        tar.add(payload, arcname="note.txt")

    with pytest.raises(ValueError):
        archive.read_manifest(stranger)


async def test_rows_survive_the_round_trip_with_their_types(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    """A naive timestamp restored on a machine in another timezone silently moves
    every call by hours, so datetimes go in marked and come back aware."""
    destination = tmp_path / "five.tar.gz"
    await archive.write_archive(
        seeded, destination, kind="manual", key_id=None, schema_revision=None, version="0.1.0"
    )

    rows = list(archive.read_table(destination, "users"))

    assert len(rows) == 1
    assert rows[0]["username"] == "mohamed"
    assert isinstance(rows[0]["created_at"], dt.datetime)
    assert rows[0]["created_at"].tzinfo is not None


def test_the_key_fingerprint_identifies_without_revealing() -> None:
    """It travels in the manifest, and the manifest travels to a network share."""
    key = bytes(range(32))

    fingerprint = archive.key_fingerprint(key)

    assert fingerprint == archive.key_fingerprint(key)
    assert fingerprint != archive.key_fingerprint(bytes(range(1, 33)))
    assert key.hex() not in fingerprint
    assert len(fingerprint) == 16


async def test_an_archive_from_a_newer_format_is_refused(
    seeded: AsyncSession, tmp_path: Path
) -> None:
    """Guessing at a layout this code has never seen is how a restore lands half-done."""
    destination = tmp_path / "future.tar.gz"
    manifest = {"product": "tel-agent", "format_version": archive.FORMAT_VERSION + 1}
    with tarfile.open(destination, "w:gz") as tar:
        payload = tmp_path / archive.MANIFEST_NAME
        payload.write_text(json.dumps(manifest))
        tar.add(payload, arcname=archive.MANIFEST_NAME)

    with pytest.raises(ValueError, match="newer"):
        archive.read_manifest(destination)


# --- Retention ----------------------------------------------------------------


def _row(days_ago: float, *, status: str = "ok", kind: str = "nightly", id: int = 0) -> Backup:
    row = Backup(kind=kind, status=status, contents={})
    row.id = id
    row.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)
    return row


def test_retention_keeps_fourteen_days_then_one_a_week() -> None:
    """The rule the screen states in its own copy."""
    rows = [_row(day, id=day + 1) for day in range(120)]

    keep = service._keep(rows)

    assert {row.id for row in rows[:14]} <= keep
    # Somewhere past the daily window it thins out rather than keeping everything.
    assert len(keep) < 40


def test_retention_never_deletes_the_last_good_backup() -> None:
    """A policy that can leave an installation with zero copies is not a policy.

    The dangerous case is exactly the one in the screen's stale state: the only good
    backup is old, because every night since has failed.
    """
    rows = [_row(200, id=1), _row(0, status="failed", id=2), _row(1, status="failed", id=3)]

    keep = service._keep(rows)

    assert 1 in keep


def test_a_pre_update_snapshot_is_never_pruned_on_age() -> None:
    """It exists because an upgrade went wrong. Tidying it away has no recovery."""
    rows = [_row(300, kind="before_update", id=1)] + [_row(d, id=d + 2) for d in range(20)]

    keep = service._keep(rows)

    assert 1 in keep


def test_an_unverified_snapshot_still_counts_as_a_copy() -> None:
    """Written but unread-back is not a failure - the bytes are there. It is not
    trusted, and it is not thrown away either."""
    rows = [_row(0, status="unverified", id=1)]

    assert 1 in service._keep(rows)


# --- Taking one, end to end ---------------------------------------------------


async def test_a_backup_runs_verifies_and_records_itself(
    seeded: AsyncSession, settings: Settings, tmp_path: Path
) -> None:
    await store.set_value(seeded, service.TARGET_KEY, str(tmp_path / "share"))
    await seeded.commit()

    row = await service.run_backup(seeded, kind="manual", settings=settings)

    assert row.status == "ok"
    assert row.verified_at is not None
    assert row.checksum and row.size_bytes
    assert Path(row.path).is_file()
    assert row.contents["tables"]["users"] == 1


async def test_a_backup_with_no_target_fails_loudly_and_leaves_a_row(
    seeded: AsyncSession, settings: Settings
) -> None:
    """The row is the evidence. Without it, a nightly job that never ran and one that
    failed look identical, and the screen reports last night's success either way."""
    row = await service.run_backup(seeded, kind="nightly", settings=settings)

    assert row.status == "failed"
    assert "no backup target" in (row.error or "")


async def test_pruning_deletes_the_file_as_well_as_the_row(
    seeded: AsyncSession, settings: Settings, tmp_path: Path
) -> None:
    """A row deleted while the archive stays behind fills the share with files the
    product no longer knows about."""
    await store.set_value(seeded, service.TARGET_KEY, str(tmp_path / "share"))
    await seeded.commit()
    kept = await service.run_backup(seeded, kind="manual", settings=settings)

    stale = Backup(kind="nightly", status="ok", contents={}, path=str(tmp_path / "old.tar.gz"))
    stale.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=400)
    Path(stale.path).write_bytes(b"old")
    seeded.add(stale)
    await seeded.commit()

    removed = await service.prune(seeded)

    assert removed == 1
    assert not Path(stale.path).exists()
    assert Path(kept.path).is_file()


async def test_the_verdict_goes_stale_before_it_goes_quiet(
    seeded: AsyncSession, settings: Settings
) -> None:
    """Nine days of failures with a green screen is the failure being prevented."""
    old = Backup(kind="nightly", status="ok", contents={}, path="/tmp/x")  # noqa: S108
    old.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=9)
    old.verified_at = old.started_at
    seeded.add(old)
    await seeded.commit()

    verdict = await service.verdict(seeded)

    assert verdict["state"] == "stale"
    # The age comes from the server, not from the browser. Two clocks disagree, and a
    # laptop whose time is a day out would otherwise report a nine-day-old backup as
    # fresh - on the one screen where that mistake costs the transcripts.
    assert verdict["last_good_age_days"] == 9


async def test_the_verdict_says_none_when_there_has_never_been_one(
    seeded: AsyncSession,
) -> None:
    assert (await service.verdict(seeded))["state"] == "none"


# --- The endpoints ------------------------------------------------------------


@pytest.fixture
async def clients(seeded: AsyncSession, settings: Settings, database_url: str):
    """An owner and an admin, so the line between them can be tested."""
    workspace = (await seeded.get(Workspace, 1)) or Workspace(name="w")
    admin = User(username="lukas", password_hash=hash_password(PASSWORD))
    seeded.add(admin)
    await seeded.flush()
    seeded.add(Membership(user_id=admin.id, workspace_id=workspace.id, role="admin"))
    await seeded.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    opened: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "lukas"):
            http = AsyncClient(transport=transport, base_url="http://localhost")
            response = await http.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200
            opened[username] = http
        try:
            yield opened
        finally:
            for http in opened.values():
                await http.aclose()


async def test_an_admin_sees_the_state_of_the_backups(clients) -> None:
    response = await clients["lukas"].get("/api/backup")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "none"
    assert body["retention"] == {"daily": 14, "weekly": 13}


async def test_an_admin_cannot_download_an_archive(clients, seeded: AsyncSession) -> None:
    """One file is every transcript on the installation. That is an export, and it is
    the owner's to make - not every operations account's."""
    row = Backup(kind="manual", status="ok", contents={}, path="/tmp/x.tar.gz")  # noqa: S108
    seeded.add(row)
    await seeded.commit()

    response = await clients["lukas"].get(f"/api/backup/{row.id}/download")

    assert response.status_code == 403


async def test_signed_out_sees_nothing(clients) -> None:
    clients["mohamed"].cookies.clear()

    assert (await clients["mohamed"].get("/api/backup")).status_code == 401


async def test_run_now_refuses_before_it_queues_when_the_target_is_broken(clients) -> None:
    """Told immediately, rather than by a failed row thirty seconds later."""
    response = await clients["lukas"].post("/api/backup/run")

    assert response.status_code == 409
    assert "not usable" in response.json()["error"]["message"]


async def test_a_restore_needs_the_date_typed(clients, seeded: AsyncSession, tmp_path) -> None:
    """The one control standing between a mis-click and every call since being deleted."""
    path = tmp_path / "archive.tar.gz"
    path.write_bytes(b"not really an archive")
    row = Backup(kind="manual", status="ok", contents={}, path=str(path))
    seeded.add(row)
    await seeded.commit()

    response = await clients["mohamed"].post(
        f"/api/backup/{row.id}/restore", json={"confirm_date": "1999-01-01"}
    )

    assert response.status_code == 400
    assert "Type the date" in response.json()["error"]["message"]


async def test_a_restore_is_refused_when_the_archive_cannot_be_read(
    clients, seeded: AsyncSession, tmp_path
) -> None:
    """Caught while the current data is still there. The failure that must never happen
    is an installation wiped and then found to have an unreadable archive."""
    path = tmp_path / "broken.tar.gz"
    path.write_bytes(b"this is not a gzip stream")
    row = Backup(kind="manual", status="ok", contents={}, path=str(path))
    seeded.add(row)
    await seeded.commit()
    await seeded.refresh(row)

    response = await clients["mohamed"].post(
        f"/api/backup/{row.id}/restore",
        json={"confirm_date": row.started_at.date().isoformat()},
    )

    assert response.status_code == 409
    assert "cannot be read" in response.json()["error"]["message"]


async def test_staging_a_restore_changes_nothing_yet(
    clients, seeded: AsyncSession, settings: Settings, tmp_path, monkeypatch
) -> None:
    """It writes a request and asks for a restart. Nothing is destroyed by the call
    that the operator makes from a browser."""
    from api.routes import backup as backup_routes

    marker = tmp_path / "restore-request.json"
    monkeypatch.setattr(backup_routes, "RESTORE_REQUEST", marker)

    await store.set_value(seeded, service.TARGET_KEY, str(tmp_path / "share"))
    await seeded.commit()
    row = await service.run_backup(seeded, kind="manual", settings=settings)

    response = await clients["mohamed"].post(
        f"/api/backup/{row.id}/restore",
        json={"confirm_date": row.started_at.date().isoformat()},
    )

    assert response.status_code == 200
    assert response.json()["staged"] is True
    assert marker.is_file()
    # The users are still there. A staged restore is not a restore.
    assert (await clients["mohamed"].get("/api/backup")).status_code == 200


# --- The restore itself -------------------------------------------------------


def _restore_module():
    """`scripts/restore.py` loaded by path.

    It is a script and not a package on purpose — a restore must never be importable
    into the running application — so the test reaches it the same way an operator
    does, by its path.
    """
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "telagent_restore", root / "scripts" / "restore.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_restore_puts_back_what_was_deleted_and_removes_what_came_after(
    seeded: AsyncSession,
    settings: Settings,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole product, in one test: back up, lose the data, get it back.

    Both halves are asserted. A restore that returns the old rows but leaves the new
    ones behind is not a restore to a point in time — it is a merge, and an operator
    who restored to undo a mistake would still be looking at the mistake.
    """
    from sqlalchemy import delete, select

    from api.config import get_settings

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    await store.set_value(seeded, service.TARGET_KEY, str(tmp_path / "share"))
    await seeded.commit()
    row = await service.run_backup(seeded, kind="manual", settings=settings)
    assert row.status == "ok", row.error

    await seeded.execute(delete(Membership))
    await seeded.execute(delete(User).where(User.username == "mohamed"))
    seeded.add(User(username="added-after-the-backup", password_hash=hash_password(PASSWORD)))
    await seeded.commit()

    await _restore_module()._load(Path(row.path))
    seeded.expire_all()

    names = set((await seeded.execute(select(User.username))).scalars().all())
    memberships = len((await seeded.execute(select(Membership))).scalars().all())

    assert "mohamed" in names
    assert "added-after-the-backup" not in names
    assert memberships == 1
