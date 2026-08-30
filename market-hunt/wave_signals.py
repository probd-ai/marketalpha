import operator
from wave_models import Signal

_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge,
        "<=": operator.le, "==": operator.eq, "!=": operator.ne}

def crossed_up(prev, cur, level):
    return prev is not None and prev < level <= cur

def crossed_down(prev, cur, level):
    return prev is not None and cur < level <= prev

def eval_signal(sig: Signal, today: dict, prev: dict, last_dirs: dict) -> bool:
    if sig.kind == "compare":
        lhs = today.get(sig.lhs_metric)
        lhs = lhs if lhs is not None else 0.0
        if sig.rhs.number is not None:
            rhs = sig.rhs.number
        else:
            rhs = today.get(sig.rhs.metric)
            rhs = rhs if rhs is not None else 0.0
        r = _OPS[sig.op](lhs, rhs)
    elif sig.kind == "cross":
        prev_v = prev.get(sig.metric); cur_v = today.get(sig.metric)
        if cur_v is None:
            r = False
        else:
            r = crossed_up(prev_v, cur_v, sig.level) if sig.direction == "up" \
                else crossed_down(prev_v, cur_v, sig.level)
    elif sig.kind == "last_cross_dir":
        r = last_dirs.get((sig.metric, tuple(sig.grid))) == sig.direction
    else:
        r = False
    return (not r) if sig.negate else r

def group_fires(group, today, prev, last_dirs):
    return all(eval_signal(s, today, prev, last_dirs) for s in group)

def count_firing_groups(groups, today, prev, last_dirs):
    return sum(1 for g in groups if group_fires(g, today, prev, last_dirs))

def any_group_fires(groups, today, prev, last_dirs):
    return any(group_fires(g, today, prev, last_dirs) for g in groups)

def update_last_dir(prev, cur, grid, prior):
    if prev is None:
        return prior
    if cur < prev and any(cur < L <= prev for L in grid):
        return "down"
    if cur > prev and any(prev < L <= cur for L in grid):
        return "up"
    return prior

class CrossState:
    def __init__(self, grids):
        self.grids = list(grids)                 # [(metric, tuple(grid)), ...]
        self.prev = {}                           # metric -> last value
        self.last_dirs = {g: None for g in self.grids}

    def update(self, today):
        for metric, grid in self.grids:
            cur = today.get(metric)
            if cur is None:
                continue
            self.last_dirs[(metric, grid)] = update_last_dir(
                self.prev.get(metric), cur, list(grid), self.last_dirs[(metric, grid)])
        for metric, val in today.items():
            if val is not None:
                self.prev[metric] = val

    def prev_snapshot(self):
        return dict(self.prev)
