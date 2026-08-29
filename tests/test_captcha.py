"""reCAPTCHA, and the distinction the whole design turns on.

"We could not ask" allows; "we asked and it said no" refuses. Getting that backwards
either takes a business's chat offline because a firewall blocks Google, or lets every
bot through by unplugging a cable - so both directions are tested, not just the happy
one.

Nothing here reaches the network. `httpx.MockTransport` answers as Google would.
"""

from __future__ import annotations

import httpx
import pytest

from api.security import captcha

SECRET = "a-real-looking-secret"  # noqa: S105
TOKEN = "a-token-from-the-widget"  # noqa: S105


def _answers(body: dict | None = None, *, status: int = 200, raises: Exception | None = None):
    """Patch the client so `verify` talks to this instead of Google."""

    def handler(request: httpx.Request) -> httpx.Response:
        if raises is not None:
            raise raises
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


@pytest.fixture
def google(monkeypatch):
    """Hand `verify` a transport, and record what it sent."""
    sent: list[httpx.Request] = []

    def install(body=None, *, status=200, raises=None):
        transport = _answers(body, status=status, raises=raises)

        def recording(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return transport.handler(request)

        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            return original(*args, **kwargs)

        monkeypatch.setattr(captcha.httpx, "AsyncClient", factory)
        return sent

    return install


async def test_no_secret_means_no_request_at_all(google) -> None:
    """Switched off must be free, not merely permissive."""
    sent = google({"success": True, "score": 0.9, "action": captcha.ACTION})
    assert (await captcha.verify(None, TOKEN)).ok is True
    assert sent == []


async def test_a_good_score_passes(google) -> None:
    google({"success": True, "score": 0.9, "action": captcha.ACTION})
    verdict = await captcha.verify(SECRET, TOKEN)
    assert verdict.ok is True
    assert verdict.score == 0.9


async def test_a_low_score_is_refused(google) -> None:
    google({"success": True, "score": 0.1, "action": captcha.ACTION})
    verdict = await captcha.verify(SECRET, TOKEN)
    assert verdict.ok is False
    assert "below" in verdict.reason


async def test_the_threshold_is_per_channel(google) -> None:
    google({"success": True, "score": 0.6, "action": captcha.ACTION})
    # Passes the default, refused by a business that wants to be stricter.
    assert (await captcha.verify(SECRET, TOKEN)).ok is True
    assert (await captcha.verify(SECRET, TOKEN, threshold=0.8)).ok is False


async def test_a_token_minted_for_another_action_is_refused(google) -> None:
    """A token is issued per action. Without this check, one from the same site's
    sign-up form would pass here."""
    google({"success": True, "score": 0.9, "action": "signup"})
    verdict = await captcha.verify(SECRET, TOKEN)
    assert verdict.ok is False
    assert "signup" in verdict.reason


async def test_google_saying_no_is_refused(google) -> None:
    google({"success": False, "error-codes": ["timeout-or-duplicate"]})
    verdict = await captcha.verify(SECRET, TOKEN)
    assert verdict.ok is False
    assert "timeout-or-duplicate" in verdict.reason


async def test_a_v2_key_pair_is_refused_rather_than_silently_passing(google) -> None:
    """v2 answers without a score, so the threshold would compare against nothing."""
    google({"success": True, "action": captcha.ACTION})
    verdict = await captcha.verify(SECRET, TOKEN)
    assert verdict.ok is False
    assert "v3" in verdict.reason


async def test_a_missing_token_is_refused_when_it_is_switched_on(google) -> None:
    sent = google({"success": True, "score": 0.9, "action": captcha.ACTION})
    verdict = await captcha.verify(SECRET, None)
    assert verdict.ok is False
    # Refused without asking: there is nothing to ask about.
    assert sent == []


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("no route to host"),
        httpx.ReadTimeout("too slow"),
    ],
)
async def test_being_unable_to_ask_allows(google, failure) -> None:
    """The half that would take a business offline if it were the other way round.

    A firewall that never opened outbound HTTPS is common on a self-hosted box, and
    refusing every visitor for it would be a chat that stops working for a reason
    nothing about the visitor caused.
    """
    google(raises=failure)
    assert (await captcha.verify(SECRET, TOKEN)).ok is True


async def test_a_broken_answer_allows_rather_than_refusing(google) -> None:
    """A 500 from Google, or HTML from a captive portal. Still 'could not ask'."""
    google({"success": True}, status=500)
    assert (await captcha.verify(SECRET, TOKEN)).ok is True


async def test_the_secret_is_sent_and_never_logged(google, caplog) -> None:
    sent = google(raises=httpx.ConnectError("no route to host"))
    with caplog.at_level("WARNING"):
        await captcha.verify(SECRET, TOKEN)

    # It goes to Google in the body, which is the only place it belongs...
    assert sent, "no request was made"
    assert SECRET in sent[0].content.decode()
    # ...and nowhere near the log line that says the call failed.
    assert SECRET not in caplog.text
