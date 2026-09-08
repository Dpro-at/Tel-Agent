"""The setup descriptor — what a channel declares once, and what the card may see.

The descriptor is the only thing that differs between one channel's card and the
next, so the property worth a test is the one a future channel could break without
noticing: `public()` is what crosses the wire to a browser, and a credential must
never be able to ride along inside it.
"""

from __future__ import annotations

import json

from api.channels.setup import Field, Setup

SETUP = Setup(
    kind="sms",
    title="SMS",
    note="Text messages arrive on the account the phone number already uses.",
    guide_url="https://example.invalid/guide",
    fields=(
        Field("api_key", "API key", secret=True, help="From the provider console."),
        Field("account", "Account", secret=False, placeholder="AC…"),
        Field("label", "Label", secret=False, required=False),
        Field("service_key", "Service key", multiline=True),
    ),
    verified_live=False,
)


def test_public_carries_the_declaration_and_never_a_value() -> None:
    """Exactly the keys the card draws from — so no value can travel with them."""
    payload = SETUP.public()
    assert json.loads(json.dumps(payload)) == payload

    assert payload["kind"] == "sms"
    assert payload["title"] == "SMS"
    assert payload["guide_url"] == "https://example.invalid/guide"
    assert payload["verified_live"] is False

    for field in payload["fields"]:
        assert set(field) == {
            "name",
            "label",
            "secret",
            "required",
            "help",
            "placeholder",
            "multiline",
        }

    by_name = {field["name"]: field for field in payload["fields"]}
    assert by_name["api_key"]["secret"] is True
    assert by_name["account"]["secret"] is False
    assert by_name["label"]["required"] is False
    assert by_name["service_key"]["multiline"] is True
    # A field declared without one is a field with no help text, not a missing key.
    assert by_name["account"]["help"] == ""


def test_a_field_is_a_secret_unless_it_says_otherwise() -> None:
    """The safe default, because the unsafe one is invisible until it leaks."""
    assert Field("token", "Token").secret is True
    assert Field("token", "Token").required is True
    assert Field("token", "Token").multiline is False


def test_the_names_the_route_needs_are_derived_from_the_declaration() -> None:
    assert SETUP.required_secrets() == ("api_key", "service_key")
    assert SETUP.required_names() == ("api_key", "account", "service_key")
    assert SETUP.secret_names() == ("api_key", "service_key")
    assert SETUP.field("account").label == "Account"
    assert SETUP.field("nothing") is None
