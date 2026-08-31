"""The document this API is published as — §B6.

"The dashboard consumes this same API, so it exists anyway; just make it public and
documented." The generator writes it from the code, so it cannot describe a route that
does not exist. What it *can* do is stay silent about the two things a reader needs
first — how to authenticate, and what a group of routes is for — because neither is
visible in a signature. These tests are about that silence.

They deliberately assert on the generated document rather than on `api/docs.py`. A test
that read the constants back would pass while the override was never installed.
"""

from __future__ import annotations

import pytest

from api.config import Settings
from api.main import TAGS_METADATA, create_app


@pytest.fixture(scope="module")
def document() -> dict:
    app = create_app(Settings(_env_file=None, jobs_enabled=False))
    return app.openapi()


def _tags_in_use(document: dict) -> set[str]:
    return {tag for _, _, op in _operations(document) for tag in op.get("tags", [])}


def _operations(document: dict) -> list[tuple[str, str, dict]]:
    return [
        (path, method, operation)
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if isinstance(operation, dict)
    ]


def test_every_tag_a_route_uses_is_explained(document: dict) -> None:
    """A tag with no description is a heading; a tag with one is a map.

    This is the test that keeps the sentence being written. A new router arrives with a
    new tag, and without this the group appears in `/docs` as a bare word that reads
    like a category and explains nothing.
    """
    described = {entry["name"] for entry in TAGS_METADATA}
    used = _tags_in_use(document)

    assert used <= described, (
        f"these tags are on a route and described nowhere: {sorted(used - described)}. "
        "Add a sentence to TAGS_METADATA in api/main.py."
    )


def test_no_tag_is_described_that_nothing_uses(document: dict) -> None:
    """The other direction, which is how a description outlives the routes it described."""
    described = {entry["name"] for entry in TAGS_METADATA}
    used = _tags_in_use(document)

    assert described <= used, (
        f"these tags are described and used by nothing: {sorted(described - used)}. "
        "A group that no longer exists should not still have a heading."
    )


def test_every_tag_description_is_a_sentence(document: dict) -> None:
    for entry in TAGS_METADATA:
        assert entry.get("description", "").strip(), f"{entry['name']} has an empty description"


def test_the_three_credentials_are_declared_separately(document: dict) -> None:
    """§B9.1's whole point, in the document a reader is handed.

    One combined "Authorization" scheme would describe a product where a leak of one
    credential opens the others, which is the arrangement the specification exists to
    prevent.
    """
    schemes = document["components"]["securitySchemes"]
    assert set(schemes) == {"session", "hooksToken", "mcpToken"}
    assert schemes["session"]["in"] == "cookie"
    assert schemes["hooksToken"]["scheme"] == "bearer"
    assert schemes["mcpToken"]["scheme"] == "bearer"


def test_every_operation_says_what_opens_it(document: dict) -> None:
    """Including the open ones.

    An empty `security` list is not the same as no `security` key: the first says "no
    credential is needed" and the second says nothing at all. A reader cannot tell the
    difference between an open route and an undocumented one from silence.
    """
    missing = [
        f"{method.upper()} {path}"
        for path, method, operation in _operations(document)
        if "security" not in operation
    ]
    assert not missing, f"no security stated for: {missing}"


def test_a_dashboard_route_is_opened_by_the_session_and_a_machine_path_is_not(
    document: dict,
) -> None:
    by_path = {path: operation["security"] for path, _, operation in _operations(document)}
    assert by_path["/api/conversations"] == [{"session": []}]
    assert by_path["/api/tokens"] == [{"session": []}]
    # Public by design: the widget's address travels in a stranger's HTML.
    assert by_path["/public/chat/{path}/messages"] == []
    assert by_path["/health"] == []
    # And sign-in, which cannot need the thing it produces.
    assert by_path["/api/auth/login"] == []


def test_documenting_the_api_did_not_change_it(document: dict) -> None:
    """The override adds description and must add nothing else.

    `api/docs.py` rewrites the generated document. The failure worth guarding is the one
    where it also, quietly, drops half of it — a schema that lost operations would still
    render a plausible-looking page.
    """
    operations = _operations(document)
    assert len(operations) > 80, f"only {len(operations)} operations survived the override"
    assert "/api/conversations/{conversation_id}/whisper" in document["paths"]
    assert document["info"]["title"] == "Tel-Agent"
