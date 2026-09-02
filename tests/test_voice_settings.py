"""The environment describing the voice providers — Milestone 11, §B3/§B9.2.

The same rules the model's settings hold: no key is the supported "not configured"
state, sensible defaults fill in the rest, and a half-configuration (a key with no
voice, where a voice cannot be defaulted) is refused rather than found on a live call.
"""

from __future__ import annotations

import pytest

from agent.config import (
    ConfigurationError,
    stt_settings,
    tts_settings,
)

_VOICE_ENV = (
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_MODEL",
    "DEEPGRAM_BASE_URL",
    "STT_LANGUAGE",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MODEL_ID",
    "ELEVENLABS_OUTPUT_FORMAT",
    "ELEVENLABS_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in _VOICE_ENV:
        monkeypatch.delenv(name, raising=False)
    stt_settings.cache_clear()
    tts_settings.cache_clear()
    yield
    stt_settings.cache_clear()
    tts_settings.cache_clear()


def test_no_key_is_not_configured_for_either() -> None:
    assert stt_settings() is None
    assert tts_settings() is None


def test_a_deepgram_key_alone_configures_german_nova(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    settings = stt_settings()
    assert settings is not None
    assert settings.model == "nova-2"
    assert settings.language == "de"
    assert settings.base_url.startswith("wss://")


def test_deepgram_env_overrides_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    monkeypatch.setenv("DEEPGRAM_MODEL", "nova-3")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "ws://127.0.0.1:9000/")
    settings = stt_settings()
    assert (settings.model, settings.language) == ("nova-3", "en")
    # The trailing slash is trimmed so the client's path join is clean.
    assert settings.base_url == "ws://127.0.0.1:9000"


def test_elevenlabs_needs_a_key_and_a_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-42")
    settings = tts_settings()
    assert settings is not None
    assert settings.voice_id == "voice-42"
    assert settings.model == "eleven_turbo_v2_5"
    assert settings.output_format == "ulaw_8000"


def test_a_key_with_no_voice_is_a_refused_half_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-key")
    with pytest.raises(ConfigurationError) as caught:
        tts_settings()
    assert "ELEVENLABS_VOICE_ID" in str(caught.value)


def test_a_transport_recodes_stt_for_its_own_media_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is the SIP case (μ-law 8 kHz); a LiveKit room is 16-bit PCM, and the
    transport re-codes the settings without touching the key or the language."""
    from agent.config import _replace_codec

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    base = stt_settings()
    assert (base.encoding, base.sample_rate) == ("mulaw", 8000)

    room = _replace_codec(base, encoding="linear16", sample_rate=48000)
    assert (room.encoding, room.sample_rate) == ("linear16", 48000)
    # Everything else is carried through unchanged.
    assert (room.api_key, room.model, room.language) == (
        base.api_key,
        base.model,
        base.language,
    )
