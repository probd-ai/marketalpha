"""Store / inspect / wipe broker credentials in the Windows Credential Manager.

Run this yourself on the machine that trades. Values are typed at an interactive
prompt and never echoed, never written to a file, never passed as a command-line
argument (argv is visible to other processes and lands in shell history), and
never printed back.

    python scripts/credentials.py store             # all broker credentials
    python scripts/credentials.py store KITE        # just one broker
    python scripts/credentials.py store DHAN main   # a NAMED Dhan account
    python scripts/credentials.py status            # NAMES only, never values
    python scripts/credentials.py clear             # wipe everything after trading

Multiple Dhan accounts: the first can stay unnamed ("default"); give every other one
an alias. The alias is how a portfolio's broker mapping picks the account, so two
identically-configured portfolios in two accounts stay tellable apart — which
reconciliation cannot do from holdings alone.

Intended rhythm: store on rebalance morning, trade, `clear` when done. Between
rebalances the machine holds no broker credential at all, so there is nothing on
it to steal.

⚠ This protects against a copied disk, a stolen backup, another Windows account,
and anything reading the repo. It does NOT protect against code running as YOU —
DPAPI decrypts for your own login. Keep MH_TRADING_ENABLED=false unless you are
actively punching, keep the value caps tight, and never expose the API off
localhost.
"""
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers import config  # noqa: E402

GROUPS = {
    "KITE": ["KITE_API_KEY", "KITE_API_SECRET"],
    "DHAN": ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
}


def _store(names: list[str]) -> None:
    print("Values are hidden as you type. Press Enter to skip a field.\n")
    stored = 0
    for name in names:
        try:
            value = getpass(f"  {name}: ")
        except (EOFError, KeyboardInterrupt):
            print("\naborted — nothing further stored")
            return
        if not value.strip():
            print(f"    skipped {name}")
            continue
        config.set_secret(name, value)
        stored += 1
        print(f"    stored {name}")
    print(f"\n{stored} credential(s) stored in the Windows Credential Manager "
          f"under service '{config.SERVICE}'.")


def _alias_names(group: str, alias: str) -> list[str]:
    """Credential names for a named account: DHAN_CLIENT_ID__MAIN etc.
    'default' keeps the plain, unsuffixed names."""
    if alias.lower() in ("", "default"):
        return GROUPS[group]
    return [f"{n}__{alias.upper()}" for n in GROUPS[group]]


def cmd_store(args: list[str]) -> int:
    if args:
        group = args[0].upper()
        if group not in GROUPS:
            print(f"unknown broker '{args[0]}' — choose from: {', '.join(GROUPS)}")
            return 2
        alias = args[1] if len(args) > 1 else "default"
        if group == "KITE" and alias.lower() not in ("", "default"):
            print("Kite supports one account only (a single daily session token) — "
                  "named accounts are Dhan-only.")
            return 2
        if not alias.replace("_", "").isalnum():
            print(f"alias '{alias}' must be letters/digits/underscore only")
            return 2
        label = "default account" if alias.lower() in ("", "default") else f"account '{alias.lower()}'"
        print(f"Storing {group} credentials for the {label}.\n")
        _store(_alias_names(group, alias))
    else:
        _store([n for names in GROUPS.values() for n in names])
    return 0


def cmd_status(_args: list[str]) -> int:
    names = config.stored_names()
    if not names:
        print("No credentials stored. The machine currently holds nothing to steal.")
        return 0
    print(f"Stored in Credential Manager (service '{config.SERVICE}') — names only:")
    for n in sorted(names):
        label = " (daily session token)" if n.startswith("__token__") else ""
        print(f"  - {n}{label}")
    try:
        from brokers.dhan import list_accounts
        accounts = list_accounts()
        if accounts:
            print(f"\nDhan accounts usable now: {', '.join(accounts)}")
            print("  (a portfolio's broker mapping picks one by this name)")
    except Exception:
        pass
    print("\nWipe them with: python scripts/credentials.py clear")
    return 0


def cmd_clear(_args: list[str]) -> int:
    names = config.stored_names()
    if not names:
        print("Nothing stored — already clean.")
        return 0
    print("About to remove:")
    for n in sorted(names):
        print(f"  - {n}")
    try:
        if input("\nType 'wipe' to confirm: ").strip().lower() != "wipe":
            print("cancelled — nothing removed")
            return 1
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled — nothing removed")
        return 1
    removed = config.clear_all()
    print(f"\nRemoved {len(removed)} credential(s). Trading cannot proceed until "
          f"they are stored again.")
    return 0


COMMANDS = {"store": cmd_store, "status": cmd_status, "clear": cmd_clear}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
