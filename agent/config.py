"""What a configured model is, and how the environment describes one.

`agent/` never imports from `api/` (docs/ARCHITECTURE.md rule 3), so it does not share
`api.config.Settings`. That separation is not an inconvenience to work around: at
Milestone 11 the agent is a process joining a room, started by whoever runs the media
path, and it has to be configurable without an API server existing at all.

**Since §B9.2 the environment is no longer the only source.** The key a user types into
the settings screen lives encrypted in the database, and the database is on the far
side of the boundary this package may not cross. So the rule that decides whether a
configuration is *whole* lives here, in `settings_from`, and whoever can read a source
calls it: `llm_settings()` for the environment, `api.llm.resolve` for the store. The
agent never learns which one answered, which is exactly what keeps it free of `api/`.

The environment path is read once, because a value that can change under a running call
is a value that will change under a running call. The store path is read per turn -
§B9.2 says a credential entered from the screen takes effect immediately - and a turn
holds the settings it started with.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

# The default for an OpenAI-compatible endpoint. Named `openai` because that is the
# shape of the API - `POST /chat/completions` with server-sent events - and not the
# name of one company's service: the same code reaches a local model, a hosted
# gateway, or anything else that speaks it, by pointing `LLM_BASE_URL` elsewhere.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

SUPPORTED_PROVIDERS = ("openai",)


class ConfigurationError(RuntimeError):
    """Half a configuration. Louder than a default, because a default would be wrong."""


@dataclass(frozen=True)
class LlmSettings:
    """Which model answers, and where it lives."""

    provider: str
    model: str
    api_key: str
    base_url: str


def _clean(name: str) -> str:
    return os.environ.get(name, "").strip()


def _sentence(text: str) -> str:
    """Capitalise the first letter only.

    The message opens with whatever the source calls the field, and one of those is the
    screen's `the provider` - a refusal that begins in lower case reads as a string
    somebody forgot to finish. `str.capitalize` is wrong here: it would lower-case
    `LLM_PROVIDER` in the other source's version of the same sentence.
    """
    return text[:1].upper() + text[1:]


# What each of the four values is called, when the environment is the source. The
# message a half-configured installation gets has to name the thing the person can
# actually go and fix, and that name differs per source - `LLM_API_KEY` means nothing
# to somebody who typed the key into a form.
ENVIRONMENT_NAMES: Mapping[str, str] = {
    "provider": "LLM_PROVIDER",
    "model": "LLM_MODEL",
    "api_key": "LLM_API_KEY",
}


def settings_from(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    names: Mapping[str, str],
) -> LlmSettings | None:
    """The configured model these four values describe, or `None` for no model.

    `None` is a supported state, not a failure: an installation that has not connected
    a model still takes messages, and `agent.reply` says so in words rather than
    failing a request the visitor cannot do anything about.

    A *half*-configured model is a different thing and raises. A provider named with no
    key behind it is somebody who meant to connect a model and has not finished; a
    silent fallback there would look exactly like the model answering badly.

    `names` is what to call each value in that refusal - see `ENVIRONMENT_NAMES`.
    """
    provider, model, api_key = provider.strip(), model.strip(), api_key.strip()
    if not provider:
        return None

    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(
            f"{provider!r} is not a provider this build implements. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    missing = [
        names[field] for field, value in (("model", model), ("api_key", api_key)) if not value
    ]
    if missing:
        raise ConfigurationError(
            _sentence(
                f"{names['provider']} is set to {provider!r} but {' and '.join(missing)} "
                f"is empty. Set it, or clear {names['provider']} to run without a model."
            )
        )

    return LlmSettings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url.strip().rstrip("/") or DEFAULT_BASE_URL,
    )


def environment_values() -> dict[str, str]:
    """The four values as the environment gives them, unvalidated.

    Separate from `llm_settings` because a dashboard merges these with what the owner
    saved, and a merge cannot start from a function that refuses half a configuration:
    an installer's `LLM_PROVIDER` with the key now living in the store is *not* half a
    configuration, it is the migration §B9.2 describes. Validation happens once, over
    the merged four, in `settings_from`.
    """
    return {
        "provider": _clean("LLM_PROVIDER"),
        "model": _clean("LLM_MODEL"),
        "api_key": _clean("LLM_API_KEY"),
        "base_url": _clean("LLM_BASE_URL"),
    }


@lru_cache(maxsize=1)
def llm_settings() -> LlmSettings | None:
    """The model the *environment* describes, or `None` when it names none.

    This is the installer's path and the standalone one: at Milestone 11 the agent runs
    as its own process with no API server and no settings screen behind it, and this is
    how it is configured there. Where a dashboard exists, `api.llm.resolve` prefers what
    the owner saved over what an installer wrote once - see §B9.2.
    """
    return settings_from(**environment_values(), names=ENVIRONMENT_NAMES)


# --- The voice providers - Milestone 11, §B3 -------------------------------------
#
# STT and TTS join the model at the phone, and they are configured the same way and for
# the same reason: the standalone agent process has no settings screen, so the
# environment describes them, and `None` means "not configured" rather than a failure.
# The real key lives encrypted in the database once a dashboard exists (§B9.2); these
# read the installer's/standalone path.

# Deepgram's streaming endpoint speaks over WebSocket, so the base is `wss://`. The
# model and language are the two knobs Rule 4 cares about - `nova-2` is the current
# streaming model, and German is the target the accuracy bar is set against.
DEFAULT_STT_BASE_URL = "wss://api.deepgram.com"
DEFAULT_STT_MODEL = "nova-2"

# ElevenLabs, and the two choices a phone forces. `eleven_turbo_v2_5` is the low-latency
# model - Rule 3's budget does not survive the quality-first one - and `ulaw_8000` is
# G.711 μ-law at 8 kHz, which is what a SIP call carries (SIP_CODEC=PCMU); asking for
# mp3 would mean transcoding on the media thread, which Rule 3 forbids.
DEFAULT_TTS_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_TTS_OUTPUT_FORMAT = "ulaw_8000"


@dataclass(frozen=True)
class SttSettings:
    """Which speech-to-text service listens, in what language, and over which codec.

    `encoding` and `sample_rate` are the transport's business, not the installer's: a
    direct SIP call is μ-law 8 kHz, but a LiveKit room hands the agent 16-bit PCM at
    the room's rate, and Deepgram must be told which. They default to the SIP case and
    the LiveKit transport overrides them - so the same provider serves both media paths
    without a transcode on either.
    """

    api_key: str
    model: str
    language: str
    base_url: str
    encoding: str = "mulaw"
    sample_rate: int = 8000


@dataclass(frozen=True)
class TtsSettings:
    """Which text-to-speech service speaks, in which voice, at which codec."""

    api_key: str
    voice_id: str
    model: str
    output_format: str
    base_url: str


@lru_cache(maxsize=1)
def stt_settings() -> SttSettings | None:
    """The speech-to-text the environment describes, or `None`.

    A key alone is enough: model and language have working defaults, so an installer
    who set `DEEPGRAM_API_KEY` gets German nova-2 without four more variables. No key is
    the honest "no STT", which the phone reports rather than failing a call.
    """
    key = _clean("DEEPGRAM_API_KEY")
    if not key:
        return None
    return SttSettings(
        api_key=key,
        model=_clean("DEEPGRAM_MODEL") or DEFAULT_STT_MODEL,
        language=_clean("STT_LANGUAGE") or "de",
        base_url=_clean("DEEPGRAM_BASE_URL").rstrip("/") or DEFAULT_STT_BASE_URL,
    )


def _replace_codec(settings: SttSettings, *, encoding: str, sample_rate: int) -> SttSettings:
    """The same STT configuration, re-coded for a transport's media format.

    A transport knows its audio format and the installer does not, so the transport
    calls this rather than asking the operator to set a codec they cannot know. Kept
    here beside `SttSettings` so the field names have one home.
    """
    import dataclasses

    return dataclasses.replace(settings, encoding=encoding, sample_rate=sample_rate)


@lru_cache(maxsize=1)
def tts_settings() -> TtsSettings | None:
    """The text-to-speech the environment describes, or `None`.

    Both the key and a voice are required, because ElevenLabs has no default voice and a
    call with no voice cannot speak - unlike the language, which STT can default. A key
    without a voice is the half-configuration `settings_from` refuses for the model, and
    it raises here for the same reason: a silent fallback would be found on a live call.
    """
    key = _clean("ELEVENLABS_API_KEY")
    if not key:
        return None
    voice = _clean("ELEVENLABS_VOICE_ID")
    if not voice:
        raise ConfigurationError(
            "ELEVENLABS_API_KEY is set but ELEVENLABS_VOICE_ID is empty. Set a voice, "
            "or clear the key to run without a voice."
        )
    return TtsSettings(
        api_key=key,
        voice_id=voice,
        model=_clean("ELEVENLABS_MODEL_ID") or DEFAULT_TTS_MODEL,
        output_format=_clean("ELEVENLABS_OUTPUT_FORMAT") or DEFAULT_TTS_OUTPUT_FORMAT,
        base_url=_clean("ELEVENLABS_BASE_URL").rstrip("/") or DEFAULT_TTS_BASE_URL,
    )
