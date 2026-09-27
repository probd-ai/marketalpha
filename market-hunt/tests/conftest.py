"""Shared test fixtures.

The critical one here is `fake_vault`, which is applied to EVERY test whether it
asks for it or not: without it, `brokers.config` would read and write the real
Windows Credential Manager, and a test could overwrite or delete the user's live
broker credentials. Tests must never touch the real vault.
"""
import pytest


class _FakeKeyring:
    """In-memory stand-in for the Windows Credential Manager."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch):
    """Redirect all credential reads/writes to memory.

    Autouse and unconditional — a test that forgets to request it still cannot
    reach the user's real credentials. Yields a dict-like view keyed by name so
    a test can seed values: `fake_vault.update({"KITE_API_KEY": "k"})`.
    """
    import brokers.config as bc

    fake = _FakeKeyring()
    monkeypatch.setattr(bc, "_kr", lambda: fake)

    class _View(dict):
        def update(self, mapping, **kw):          # type: ignore[override]
            for k, v in {**mapping, **kw}.items():
                bc.set_secret(k, v)
            super().update(mapping, **kw)

    yield _View()
