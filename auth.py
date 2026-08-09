"""
Last.fm authentication.

Used by both the CLI and the GUI:

    1. save_credentials(key, secret)   -> write API key/secret to .env
    2. get_auth_url()                  -> URL to open in a browser
    3. complete_auth(url)              -> session key (after the user authorizes)
    4. save_session_key(key)           -> write the session key into .env

All functions read the current values from lastfm_config so that new
credentials are picked up without restarting the app.

A single SessionKeyGenerator is reused across get_auth_url() ->
complete_auth(url): pylast stores the auth token in the generator
instance, and get_web_auth_session_key() looks it back up from that
same instance. Creating a fresh generator for the second call would
lose the token and Last.fm would reject the request.
"""

import socket

import pylast

import lastfm_config
from paths import ENV_FILE

# Fail fast instead of hanging forever when Last.fm is unreachable.
NETWORK_TIMEOUT = 20

# Shared across one authorize flow (see module docstring).
_generator = None


def _with_timeout(call):
    previous = socket.getdefaulttimeout()

    try:
        socket.setdefaulttimeout(NETWORK_TIMEOUT)
        return call()
    finally:
        socket.setdefaulttimeout(previous)


def has_credentials():
    return bool(
        lastfm_config.LASTFM_API_KEY
        and lastfm_config.LASTFM_API_SECRET
    )


def has_session():
    return bool(lastfm_config.LASTFM_SESSION_KEY)


def _network():
    if not has_credentials():
        raise RuntimeError(
            "API key / secret missing. "
            "Create them at https://www.last.fm/api/account/create "
            "and save them under Settings."
        )

    return pylast.LastFMNetwork(
        api_key=lastfm_config.LASTFM_API_KEY,
        api_secret=lastfm_config.LASTFM_API_SECRET,
    )


def _new_generator():
    global _generator

    _generator = pylast.SessionKeyGenerator(
        _network()
    )

    return _generator


def get_auth_url():
    return _with_timeout(
        lambda: _new_generator().get_web_auth_url()
    )


def complete_auth(url):
    if _generator is None:
        raise RuntimeError(
            "No authorization in progress. "
            "Click 'Authorize Last.fm' first."
        )

    return _with_timeout(
        lambda: _generator.get_web_auth_session_key(url)
    )


def _write_env(updates):
    """
    Update .env, replacing any existing lines for the given keys,
    then reload the in-memory settings.
    """

    lines = []

    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(
            encoding="utf-8"
        ).splitlines()

    keys = set(updates)

    kept = [
        line
        for line in lines
        if not any(
            line.lstrip().startswith(key)
            for key in keys
        )
    ]

    for key, value in updates.items():
        kept.append(f'{key} = "{value}"')

    ENV_FILE.write_text(
        "\n".join(kept) + "\n",
        encoding="utf-8",
    )

    lastfm_config.reload()

    return ENV_FILE


def save_credentials(api_key, api_secret):
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()

    if not api_key or not api_secret:
        raise ValueError(
            "API key and API secret are required."
        )

    return _write_env({
        "LASTFM_API_KEY": api_key,
        "LASTFM_API_SECRET": api_secret,
    })


def save_session_key(session_key):
    return _write_env({
        "LASTFM_SESSION_KEY": session_key,
    })
