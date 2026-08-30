"""Secrets loading + encrypted token cache.

.env (repo root, gitignored) holds:
  MH_API_TOKEN       - API key required by /api/broker/* when exposed beyond localhost
  MH_TOKEN_KEY       - Fernet key for the token cache (auto-generated on first use)
  KITE_API_KEY / KITE_API_SECRET
  DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
SECRETS_DIR = REPO_ROOT / ".secrets"


def load_env() -> None:
    """Load .env once, never overriding real process env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        pass


def get(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v else None


def _fernet():
    from cryptography.fernet import Fernet
    key = get("MH_TOKEN_KEY")
    if not key:
        # First use: generate and persist so the cache survives restarts.
        key = Fernet.generate_key().decode()
        with open(ENV_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n# auto-generated encryption key for .secrets token cache\nMH_TOKEN_KEY={key}\n")
        os.environ["MH_TOKEN_KEY"] = key
    return Fernet(key.encode())


def cache_token(name: str, token: str) -> None:
    """Encrypt-at-rest cache for short-lived access tokens (e.g. Kite daily)."""
    SECRETS_DIR.mkdir(exist_ok=True)
    (SECRETS_DIR / f"{name}.enc").write_bytes(_fernet().encrypt(token.encode()))


def read_cached_token(name: str) -> str | None:
    p = SECRETS_DIR / f"{name}.enc"
    if not p.exists():
        return None
    try:
        return _fernet().decrypt(p.read_bytes()).decode()
    except Exception:
        return None  # wrong key / corrupt cache -> treat as absent, never raise secrets


def drop_cached_token(name: str) -> None:
    p = SECRETS_DIR / f"{name}.enc"
    if p.exists():
        p.unlink()
