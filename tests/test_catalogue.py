"""The catalogue's services.

The tests that earn their place are about the price. A price here is read out to a
customer by something that cannot check it against anything, so the two ways it can be
wrong both have to be impossible: an amount that survives a switch to "on request" and
gets quoted anyway, and a fixed price with no amount, which would be quoted as free.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, Service, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
EUR_120 = 120_000_000


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    people = [("mohamed", mine, "admin"), ("lukas", mine, "viewer"), ("wolf", theirs, "owner")]
    for username, workspace, role in people:
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username, _, _ in people:
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, migrated, mine, theirs
        finally:
            for http in clients.values():
                await http.aclose()


async def _add(http: AsyncClient, **fields) -> dict:
    body = {"name": "Consultation", "price_mode": "fixed", "price_micros": EUR_120} | fields
    answer = await http.post("/api/catalogue", json=body)
    assert answer.status_code == 201, answer.text
    return answer.json()


async def test_a_new_workspace_sells_nothing_and_says_so(stage) -> None:
    """Shipping a catalogue would be shipping somebody else's prices."""
    clients, *_ = stage
    body = (await clients["mohamed"].get("/api/catalogue")).json()
    assert body["services"] == []
    # And the currency is answered even when there is nothing to price.
    assert body["currency"] == "EUR"


async def test_a_service_survives_the_round_trip(stage) -> None:
    clients, *_ = stage
    created = await _add(
        clients["mohamed"],
        name="  First consultation  ",
        says="Short and free of charge for new customers.",
        minutes=30,
        price_micros=0,
        performed_by="  ",
        bookable=True,
    )
    # Trimmed, and whitespace is not a value: "any free" is null, not two spaces.
    assert created["name"] == "First consultation"
    assert created["performed_by"] is None
    # Zero is a price, not a missing one. A free first appointment is a real offer.
    assert created["price_micros"] == 0
    assert created["minutes"] == 30

    listed = (await clients["mohamed"].get("/api/catalogue")).json()["services"]
    assert [row["id"] for row in listed] == [created["id"]]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            {"price_mode": "on_request", "price_micros": EUR_120},
            "an amount that would be quoted despite the mode saying not to",
        ),
        ({"price_mode": "fixed", "price_micros": None}, "a fixed price with nothing in it"),
        ({"price_mode": "hourly", "price_micros": None}, "an hourly rate with no rate"),
        ({"price_mode": "per_kilo", "price_micros": EUR_120}, "a mode nothing can render"),
    ],
)
async def test_a_price_that_disagrees_with_its_mode_is_refused(stage, body, reason) -> None:
    clients, *_ = stage
    answer = await clients["mohamed"].post("/api/catalogue", json={"name": "Visit"} | body)
    assert answer.status_code == 422, reason


async def test_on_request_is_a_price_and_carries_no_amount(stage) -> None:
    clients, *_ = stage
    created = await _add(clients["mohamed"], price_mode="on_request", price_micros=None)
    assert created["price_mode"] == "on_request"
    assert created["price_micros"] is None


async def test_switching_to_on_request_clears_the_old_amount(stage) -> None:
    """Otherwise the number the business stopped quoting is still there to be read out."""
    clients, *_ = stage
    created = await _add(clients["mohamed"])
    assert created["price_micros"] == EUR_120

    answer = await clients["mohamed"].patch(
        f"/api/catalogue/{created['id']}", json={"price_mode": "on_request"}
    )
    assert answer.status_code == 200
    assert answer.json()["price_micros"] is None

    # And it is gone from the store, not just from this response.
    stored = (await clients["mohamed"].get("/api/catalogue")).json()["services"][0]
    assert stored["price_micros"] is None


async def test_a_mode_change_is_judged_against_the_row_not_the_request(stage) -> None:
    """Sending only a mode has to be checked against the amount already stored."""
    clients, *_ = stage
    created = await _add(clients["mohamed"], price_mode="on_request", price_micros=None)

    # No amount on the row, and none in the request: `fixed` would be quoted as free.
    refused = await clients["mohamed"].patch(
        f"/api/catalogue/{created['id']}", json={"price_mode": "fixed"}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "price_missing"

    # With an amount in the same request it is a complete instruction.
    allowed = await clients["mohamed"].patch(
        f"/api/catalogue/{created['id']}",
        json={"price_mode": "fixed", "price_micros": EUR_120},
    )
    assert allowed.status_code == 200
    assert allowed.json()["price_micros"] == EUR_120


async def test_an_unsent_field_is_left_alone(stage) -> None:
    clients, *_ = stage
    created = await _add(clients["mohamed"], says="The full first appointment.", minutes=60)

    answer = await clients["mohamed"].patch(
        f"/api/catalogue/{created['id']}", json={"bookable": False}
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["bookable"] is False
    # Switching a service off does not erase what it is or what it costs: the note in
    # the interface says the assistant still describes it and asks for a person.
    assert body["says"] == "The full first appointment."
    assert body["minutes"] == 60
    assert body["price_micros"] == EUR_120


async def test_a_field_sent_as_null_is_cleared(stage) -> None:
    """Absent means "leave it"; an explicit null means "it no longer has one"."""
    clients, *_ = stage
    created = await _add(clients["mohamed"], says="On site.", performed_by="Elisabeth")

    answer = await clients["mohamed"].patch(
        f"/api/catalogue/{created['id']}", json={"says": None, "performed_by": None}
    )
    assert answer.status_code == 200
    assert answer.json()["says"] is None
    assert answer.json()["performed_by"] is None


async def test_services_keep_the_order_the_business_arranged(stage) -> None:
    """Held, not sorted by name: "first consultation" belongs at the top."""
    clients, *_ = stage
    first = await _add(clients["mohamed"], name="Zebra service")
    second = await _add(clients["mohamed"], name="Alpha service")

    listed = (await clients["mohamed"].get("/api/catalogue")).json()["services"]
    assert [row["name"] for row in listed] == ["Zebra service", "Alpha service"]
    assert first["position"] < second["position"]


async def test_a_viewer_can_read_the_catalogue_and_not_change_it(stage) -> None:
    clients, *_ = stage
    created = await _add(clients["mohamed"])

    assert (await clients["lukas"].get("/api/catalogue")).status_code == 200
    assert (
        await clients["lukas"].post("/api/catalogue", json={"name": "X"})
    ).status_code == 403
    assert (
        await clients["lukas"].patch(
            f"/api/catalogue/{created['id']}", json={"bookable": False}
        )
    ).status_code == 403
    assert (await clients["lukas"].delete(f"/api/catalogue/{created['id']}")).status_code == 403


async def test_one_workspace_never_sees_or_touches_another(stage) -> None:
    """D-028: the scope is in the query, and this is what says so."""
    clients, *_ = stage
    mine = await _add(clients["mohamed"], name="Ours")

    assert (await clients["wolf"].get("/api/catalogue")).json()["services"] == []
    # Not 403: a foreign id must be indistinguishable from one that never existed,
    # or the answer itself confirms the row is there.
    assert (
        await clients["wolf"].patch(f"/api/catalogue/{mine['id']}", json={"bookable": False})
    ).status_code == 404
    assert (await clients["wolf"].delete(f"/api/catalogue/{mine['id']}")).status_code == 404


async def test_removing_a_service_removes_it(stage) -> None:
    clients, db, *_ = stage
    created = await _add(clients["mohamed"])

    assert (
        await clients["mohamed"].delete(f"/api/catalogue/{created['id']}")
    ).status_code == 204
    assert (await clients["mohamed"].get("/api/catalogue")).json()["services"] == []
    db.expire_all()
    assert await db.get(Service, created["id"]) is None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"name": "   "}, "a name that is only whitespace"),
        ({"minutes": 0}, "an appointment of no length"),
        ({"minutes": 60 * 24 + 1}, "an appointment longer than a day"),
        ({"price_micros": -1}, "a negative price"),
    ],
)
async def test_nonsense_is_refused_at_the_edge(stage, body, reason) -> None:
    clients, *_ = stage
    payload = {"name": "Visit", "price_mode": "fixed", "price_micros": EUR_120} | body
    answer = await clients["mohamed"].post("/api/catalogue", json=payload)
    assert answer.status_code in (400, 422), reason


async def test_it_is_closed_to_anybody_without_a_session(stage) -> None:
    clients, *_ = stage
    async with AsyncClient(
        transport=ASGITransport(
            app=clients["mohamed"]._transport.app, raise_app_exceptions=False
        ),
        base_url="http://localhost",
    ) as stranger:
        assert (await stranger.get("/api/catalogue")).status_code == 401
