"""The origin guard, on its own.

§B14 makes this the thing that stands between a stranger and the database, so it is
tested apart from the route that calls it. Every case here is a way somebody gets in
if the check is written slightly wrong.
"""

from __future__ import annotations

import pytest

from api.security.embed import check_origin, normalise_origin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://shop.test", "https://shop.test"),
        # The default port is not part of the origin, and a browser omits it.
        ("https://shop.test:443", "https://shop.test"),
        ("http://shop.test:80", "http://shop.test"),
        # A non-default port is.
        ("http://localhost:3100", "http://localhost:3100"),
        # Case folds on the host. It does not fold on anything else.
        ("https://Shop.TEST", "https://shop.test"),
        ("  https://shop.test  ", "https://shop.test"),
    ],
)
def test_an_origin_is_scheme_host_and_a_port_that_is_not_the_default(raw, expected) -> None:
    assert normalise_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "shop.test",
        "//shop.test",
        "ftp://shop.test",
        "javascript:alert(1)",
        "https://",
        # Everything below is a string somebody hoped would be compared loosely.
        "https://shop.test/",
        "https://shop.test/path",
        "https://shop.test?a=1",
        "https://evil.test#https://shop.test",
        "https://user@shop.test",
        "https://shop.test:notaport",
    ],
)
def test_anything_that_is_not_an_origin_is_not_repaired_into_one(raw) -> None:
    assert normalise_origin(raw) is None


def test_a_page_on_the_list_may_post() -> None:
    assert check_origin("https://shop.test", ["https://shop.test"]) is None


def test_the_comparison_is_between_two_normalised_origins() -> None:
    # Stored one way, sent the other. The same origin either way.
    assert check_origin("https://shop.test:443", ["https://Shop.test"]) is None


@pytest.mark.parametrize(
    ("sent", "allowed", "because"),
    [
        # Not configured is refused, not open.
        ("https://shop.test", [], "no allowed origins"),
        ("https://shop.test", None, "no allowed origins"),
        # A browser always sends it on a cross-origin POST; absent means this did not
        # come from a page.
        (None, ["https://shop.test"], "no Origin header"),
        ("shop.test", ["https://shop.test"], "malformed"),
        ("https://other.test", ["https://shop.test"], "not in allowlist"),
        # A subdomain is a different origin, and so is a different scheme or port.
        ("https://evil.shop.test", ["https://shop.test"], "not in allowlist"),
        ("http://shop.test", ["https://shop.test"], "not in allowlist"),
        ("https://shop.test:8443", ["https://shop.test"], "not in allowlist"),
        # The classic near-miss: the allowed origin as a prefix of the sent one.
        ("https://shop.test.evil.test", ["https://shop.test"], "not in allowlist"),
    ],
)
def test_every_other_shape_is_refused(sent, allowed, because) -> None:
    refusal = check_origin(sent, allowed)
    assert refusal is not None, because
    assert because in refusal.reason
    # One code for all of them: a stranger probing an address must not learn from the
    # answer whether the channel exists or how it is configured.
    assert refusal.code == "origin_not_allowed"


def test_an_unparseable_entry_in_the_list_matches_nothing() -> None:
    """A bad row in the allowlist must not become a wildcard.

    It could have been stored before the validation existed, or by hand in psql.
    """
    assert check_origin("https://shop.test", ["not an origin"]) is not None
    # And it does not stop the good entries beside it from working.
    assert check_origin("https://shop.test", ["not an origin", "https://shop.test"]) is None
