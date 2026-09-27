"""Make the frozen research harness deterministic for golden-master comparisons — without editing it.

The harness iterates Python sets (index members, `set(tr) | set(picks)`, pending exits). Set order of strings changes
with each process's hash seed, and that order decides sort tie-breaks and which holding receives leftover cash first,
so the harness result varies run to run. The engine visits symbols in sorted order. This module shadows `set` and
`members_as_of` inside the harness modules with sorted equivalents, so the oracle follows the same canonical order.
"""
from __future__ import annotations


class SortedSet(list):
    def __init__(self, it=()):
        super().__init__(sorted(set(it)))

    def __or__(self, other):
        return SortedSet(list(self) + list(other))

    __ror__ = __or__

    def __ior__(self, other):
        return SortedSet(list(self) + list(other))

    def add(self, x):
        if x not in self:
            self[:] = sorted(list(self) + [x])

    def discard(self, x):
        if x in self:
            self.remove(x)


def canonicalize(module) -> None:
    """Patch a harness module in place (call once per test session)."""
    module.set = SortedSet
    if hasattr(module, "members_as_of"):
        orig = module.members_as_of
        module.members_as_of = lambda iv, d: SortedSet(orig(iv, d))
