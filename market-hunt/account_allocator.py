"""Assign each target position to exactly ONE broker account. Pure, no I/O.

Why disjoint rather than proportional: splitting every position across accounts
doubles the order count, halves each order (worse against a 0.1% limit band and the
ADV participation cap), doubles brokerage, and fragments every future sell. The only
thing it buys is tidier per-account weights, which nobody looks at.

Two passes, in this order:

  1. STICKY  — a target already held in an account stays there. Re-homing a
     correctly-held position means a sell in one account and a buy in the other,
     paying full spread and brokerage to move stock that was already right.
  2. GREEDY  — remaining targets, largest first, into the account with the most
     remaining budget. Descending order matters: the big positions get first pick
     of capacity, so the awkward remainder is small names that fit anywhere.

Determinism is a requirement, not a nicety: an allocator that reshuffles between runs
would churn the book every rebalance. Ties break on account name, never on dict order.
"""
from __future__ import annotations


def allocate(targets: dict[str, float], accounts: dict[str, float],
             held: dict[str, dict[str, float]] | None = None
             ) -> tuple[dict[str, str], dict]:
    """`targets` {symbol: target value} · `accounts` {name: capital} ·
    `held` {account: {symbol: qty}}.

    Returns ({symbol: account}, report). `report` carries per-account allocated
    value and any target too large for a single account's budget — reported rather
    than dropped, because dropping silently shrinks the book.
    """
    if not accounts:
        raise ValueError("cannot allocate with no accounts configured")

    usable = {a: float(c) for a, c in accounts.items() if float(c) > 0}
    if not usable:
        raise ValueError("every configured account has zero capital")

    total_cap = sum(usable.values())
    total_tgt = sum(float(v) for v in targets.values())
    budget = {a: (c / total_cap) * total_tgt for a, c in usable.items()}

    held = held or {}
    where_held: dict[str, str] = {}
    for acct in sorted(held):                      # sorted: stable on collisions
        if acct not in usable:
            continue
        for sym, qty in held[acct].items():
            if qty and sym not in where_held:
                where_held[sym] = acct

    assign: dict[str, str] = {}
    allocated = {a: 0.0 for a in usable}
    over_budget: list[str] = []

    def place(sym: str, value: float, acct: str) -> None:
        assign[sym] = acct
        allocated[acct] += value
        budget[acct] -= value

    # 1 - sticky
    for sym in sorted(targets):
        acct = where_held.get(sym)
        if acct:
            place(sym, float(targets[sym]), acct)

    # 2 - greedy, largest first; (-value, symbol) keeps ties deterministic
    rest = sorted(((float(v), s) for s, v in targets.items() if s not in assign),
                  key=lambda t: (-t[0], t[1]))
    for value, sym in rest:
        # max remaining budget; account name breaks ties so dict order never leaks in
        acct = max(sorted(usable), key=lambda a: budget[a])
        if value > (total_tgt * (usable[acct] / total_cap)):
            over_budget.append(sym)
        place(sym, value, acct)

    return assign, {
        "allocated_value": allocated,
        "remaining_budget": budget,
        "over_budget": over_budget,
        "sticky": sorted(s for s in assign if s in where_held),
    }
