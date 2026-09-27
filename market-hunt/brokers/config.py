"""Credential storage — Windows Credential Manager ONLY. No file fallback.

Broker credentials and the daily Kite access token live in the Windows Credential
Manager (DPAPI-backed, per Windows user). Nothing secret is written to `.env`,
to `.secrets/`, or to any file in this repo.

What this buys, honestly:
- A copied disk, a stolen backup, or a synced folder yields nothing: DPAPI keys
  are derived from your Windows login and do not leave the machine.
- Another local Windows account cannot read them.
- An agent or script reading the repo finds no secret, because none is there.

What it does NOT buy: protection from code running AS YOU. DPAPI decrypts for
your user, so malware in your session can read these too. That is why the
operational controls matter more than the storage — kill switch off by default,
tight value caps, and never exposing the order API beyond localhost.

Intended workflow (user's choice, 2026-09-06): store credentials on rebalance
day, trade, then wipe them:

    python scripts/credentials.py store     # interactive, never echoes
    python scripts/credentials.py status    # names only, never values
    python scripts/credentials.py clear     # wipe after trading

`.env` is still read for NON-secret configuration (value caps, feature flags).
It must never again hold a credential.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

#: Credential Manager service name. All entries live under this.
SERVICE = "market-hunt"

#: Internal key holding the JSON list of stored names, so entries can be
#: enumerated and wiped — the Credential Manager backend has no list API.
_MANIFEST = "__manifest__"

#: Names that MUST come from the Credential Manager and are never read from env.
#: These are the credentials that can move money-equivalent risk — they place
#: orders on a funded account.
SECRET_NAMES = (
    "KITE_API_KEY", "KITE_API_SECRET",
    "DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN",
)

# Deliberately NOT vaulted: MH_API_TOKEN. It is the local HTTP gate, read from
# the process environment by the auth guard, and it cannot place an order by
# itself. Wiping the broker credentials already disarms trading; and with no
# MH_API_TOKEN set the API is localhost-only, which is the safer default anyway.


class CredentialError(RuntimeError):
    """Credential storage failure. Messages never contain a secret value."""


def load_env() -> None:
    """Load .env for NON-secret configuration only (caps, flags)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        pass


def _kr():
    try:
        import keyring
    except ImportError:
        raise CredentialError(
            "keyring is not installed — credentials cannot be read. "
            "Run: pip install keyring")
    return keyring


# ---------------------------------------------------------------------------
# manifest (enumeration without touching the filesystem)
# ---------------------------------------------------------------------------

def _read_manifest() -> list[str]:
    import json
    raw = _kr().get_password(SERVICE, _MANIFEST)
    if not raw:
        return []
    try:
        return sorted(set(json.loads(raw)))
    except ValueError:
        return []


def _write_manifest(names: list[str]) -> None:
    import json
    _kr().set_password(SERVICE, _MANIFEST, json.dumps(sorted(set(names))))


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def get_secret(name: str) -> str | None:
    """Read a secret from the Credential Manager. Never falls back to env —
    a missing credential must fail loudly, not silently pick up a stale
    environment value that the user believed they had wiped."""
    v = _kr().get_password(SERVICE, name)
    return v.strip() if v else None


def set_secret(name: str, value: str) -> None:
    if not value or not value.strip():
        raise CredentialError(f"refusing to store an empty value for {name}")
    _kr().set_password(SERVICE, name, value.strip())
    _write_manifest(_read_manifest() + [name])


def delete_secret(name: str) -> bool:
    kr = _kr()
    existed = kr.get_password(SERVICE, name) is not None
    if existed:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass
    _write_manifest([n for n in _read_manifest() if n != name])
    return existed


def stored_names() -> list[str]:
    """Names currently stored — NEVER values. Safe to print or log."""
    return [n for n in _read_manifest() if get_secret(n)]


def clear_all() -> list[str]:
    """Wipe every stored credential. Returns the names removed."""
    removed = []
    for n in _read_manifest():
        if delete_secret(n):
            removed.append(n)
    try:
        _kr().delete_password(SERVICE, _MANIFEST)
    except Exception:
        pass
    return sorted(removed)


# ---------------------------------------------------------------------------
# non-secret config
# ---------------------------------------------------------------------------

def get(name: str) -> str | None:
    """NON-secret configuration from the process environment / .env.

    ⚠ Never use this for a credential — `SECRET_NAMES` are refused here so a
    credential can never be silently sourced from a file again.
    """
    if name in SECRET_NAMES:
        raise CredentialError(
            f"{name} is a credential — read it with get_secret(), not get(). "
            "Credentials live in the Windows Credential Manager, not .env.")
    v = os.environ.get(name)
    return v.strip() if v else None


# ---------------------------------------------------------------------------
# short-lived token cache (Kite's daily access token)
# ---------------------------------------------------------------------------
#
# Previously Fernet-encrypted under .secrets/ with the key stored in .env —
# i.e. the key sat beside the ciphertext, so anyone who could read one could
# read the other. Now the token goes straight into the Credential Manager and
# no file is written at all.

def _token_key(name: str) -> str:
    return f"__token__{name}"


def cache_token(name: str, token: str) -> None:
    set_secret(_token_key(name), token)


def read_cached_token(name: str) -> str | None:
    try:
        return get_secret(_token_key(name))
    except CredentialError:
        return None


def drop_cached_token(name: str) -> None:
    try:
        delete_secret(_token_key(name))
    except CredentialError:
        pass
