"""Result matching: a substrate's result rows against a question's gold answer.

scalar: the result has exactly one row and some cell equals gold (|a-g| <= max(0.01, 1e-6|g|)).
set   : some column of the result, as a set of strings, equals the gold set (row order and extra columns ignored).
"""
from decimal import Decimal


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float, Decimal)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.replace(",", ""))
        except ValueError:
            return None
    return None


def match(rows, q):
    if rows is None:
        return False
    rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows]
    g = q["gold"]
    if q["gold_type"] == "scalar":
        if len(rows) != 1:
            return False
        for x in rows[0]:
            v = _num(x)
            if v is not None and abs(v - g) <= max(0.01, 1e-6 * abs(g)):
                return True
        return False
    gs = set(g)
    if not rows:
        return not gs
    for j in range(len(rows[0])):
        col = [r[j] for r in rows if j < len(r)]
        if all(isinstance(x, str) for x in col) and set(col) == gs and len(col) == len(gs):
            return True
        # a single row holding a list of codes
        if len(rows) == 1 and isinstance(col[0], list) and set(map(str, col[0])) == gs:
            return True
    return False
