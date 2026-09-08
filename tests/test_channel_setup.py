"""The setup descriptor — what a channel declares once, and what the card may see.

The descriptor is the only thing that differs between one channel's card and the
next, so the property worth a test is the one a future channel could break without
noticing: `public()` is what crosses the wire to a browser, and a credential must
never be able to ride along inside it.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import types

import pytest

from api.channels import generic
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


# --- What `register` refuses ------------------------------------------------------


def _complete_module(name: str = "api.channels._probe") -> types.ModuleType:
    """A module that satisfies the whole contract, for a test to break one piece of."""
    module = types.ModuleType(name)
    module.KIND = "sms"
    module.INBOUND = "door"
    module.SETUP = Setup(
        kind="sms",
        title="SMS",
        note="A complete declaration.",
        guide_url="https://example.invalid/guide",
        fields=(Field("api_key", "API key"),),
    )

    async def nothing(*args: object, **kwargs: object) -> None:
        return None

    def plain(*args: object, **kwargs: object) -> None:
        return None

    module.make_client = plain
    module.probe = nothing
    module.send_text = nothing
    module.message_text = plain
    module.ingest = nothing
    module.respond = nothing
    module.schedule_reply = plain
    module.receive = nothing
    return module


def test_a_complete_module_registers(monkeypatch) -> None:
    monkeypatch.setattr(generic, "CHANNELS", {})
    generic.register(_complete_module())
    assert generic.module_for("sms") is not None


@pytest.mark.parametrize(
    ("missing", "says"),
    [
        ("SETUP", "SETUP"),
        ("receive", "receive"),
        ("message_text", "message_text"),
        ("schedule_reply", "schedule_reply"),
    ],
)
def test_a_module_missing_a_piece_of_the_contract_is_named(
    monkeypatch, missing: str, says: str
) -> None:
    """The failure is at registration, with the missing name in it.

    Not at the first request from a stranger, where a door with no `receive` is a 500
    and a card with no `SETUP` is a screen that cannot be drawn.
    """
    monkeypatch.setattr(generic, "CHANNELS", {})
    module = _complete_module()
    delattr(module, missing)
    with pytest.raises(TypeError, match=says):
        generic.register(module)


def test_a_dial_out_module_owes_a_loop_and_not_a_receive(monkeypatch) -> None:
    monkeypatch.setattr(generic, "CHANNELS", {})
    module = _complete_module()
    module.INBOUND = "dial_out"
    with pytest.raises(TypeError, match="loop"):
        generic.register(module)

    async def loop(sessionmaker: object) -> None:
        return None

    module.loop = loop
    generic.register(module)
    assert generic.dial_out_modules() == [module]


def test_a_module_whose_declaration_names_another_kind_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(generic, "CHANNELS", {})
    module = _complete_module()
    module.KIND = "telegram"
    with pytest.raises(TypeError, match=r"SETUP\.kind"):
        generic.register(module)


# --- The registry does not depend on who imported whom first ----------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _in_a_fresh_interpreter(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_transport_can_be_the_first_module_imported() -> None:
    """`import api.channels.sms` on its own must not raise.

    It is what a script, a migration or a test that only wants the transport does. The
    transport imports the registry for `ChannelRefused`, so a registry that registered
    at its own import time would reach back into a module Python has not finished
    building and refuse it for declaring nothing.
    """
    done = _in_a_fresh_interpreter("import api.channels.sms")
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    "first",
    ["api.channels.sms", "api.channels.generic"],
    ids=["transport first", "registry first"],
)
def test_the_registry_lists_the_channel_whichever_was_imported_first(first: str) -> None:
    """Same answer both ways round, in a process that imported nothing else."""
    done = _in_a_fresh_interpreter(
        f"import {first}\nfrom api.channels import generic\nprint(sorted(generic.channels()))\n"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "['sms']"
