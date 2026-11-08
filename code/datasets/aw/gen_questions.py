"""Generate the AW question set (data/aw_questions.jsonl) with gold answers from gold.py.

Classes: L lookup, E multi-level explosion, W where-used, A aggregation through sales orders (incl. component demand
with the BOM valid on the order date), V as-of BOM (AW has real dated BOM changes in 2021: lines added, removed and
re-quantified). Instances are drawn with a fixed seed and kept only if they pass the per-template non-triviality
filter written next to each template (the answer must depend on the factor the template tests). Templates whose
tpl ends in 'x' are deliberately outside the semantic layer's single-query coverage (two dates, set differences,
intersections); they carry s3_expressible = false.

Record fields: qid ('AW-<cls><nn>'), cls, tpl, text, gold_type (scalar|set), gold, plus s3_expressible and args
(the instance parameters, used only by check_gold.py; never shown to a model).
"""
import json, os, random, sys, datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from __init__ import SQL_DB, QUESTIONS, MAIN_QUESTIONS  # noqa: E402
from gold import World, TODAY  # noqa: E402

W = World(SQL_DB)
rng = random.Random(20261105)

# Fresh replication set (N1_FRESH=1, review round): a new seed, instances of the main set excluded, qids prefixed F.
FRESH = os.environ.get("N1_FRESH") == "1"
if FRESH:
    rng = random.Random(20261108)
Q = []
used = set()
if FRESH:
    used |= {(q["tpl"], json.dumps(q["args"], sort_keys=True, default=str)) for q in map(json.loads, open(MAIN_QUESTIONS))}
EXAMPLE = "BK-R93R-44"   # used as the example code in the substrate docs; never asked about


def add(cls, tpl, text, gtype, gold, args, covered=True):
    key = (tpl, json.dumps(args, sort_keys=True, default=str))
    if key in used:
        return False
    used.add(key)
    n = len([q for q in Q if q["cls"] == cls]) + 1
    Q.append(dict(qid=f"AW-{cls}{n:02d}", cls=cls, tpl=tpl, text=text, gold_type=gtype, gold=gold,
                  s3_expressible=covered, args=args))
    return True


def r2(x):
    return round(float(x), 2)


def pick(n, gen):
    k = 0
    for _ in range(20000):
        if k == n:
            return
        if gen():
            k += 1
    if FRESH:  # the main set already used most instances of a small template: keep the rest
        print("fresh: short template", getattr(gen, "__name__", "?"), k, "of", n, flush=True); return
    raise RuntimeError(f"could not draw enough instances for {gen.__name__}")


P = W.prod
num = lambda p: P[p]["num"]
asm = [a for a in W.assemblies() if num(a) != EXAMPLE]
bikes = [a for a in asm if P[a]["cat"] == "Bikes"]
subasm = [a for a in asm if P[a]["cat"] != "Bikes"]           # frames, wheels, internal sub-assemblies
parts_all = sorted({c for a in W.bom for c, *_ in W.bom[a] if not P[c]["make"]})
made_kids = sorted({c for a in W.bom for c, *_ in W.bom[a] if P[c]["make"]})
DATE_LO, DATE_HI = dt.date(2021, 3, 3), dt.date(2021, 12, 21)
CHANGE_DATES = sorted({r[3] for r in W.rows} | {r[4] for r in W.rows if r[4]})


def rand_date(p=None):
    """A date inside the 2021 BOM-change window; 40% of the time a start/end date of one of p's own BOM lines
    (so that the inclusive validity bounds matter)."""
    if p is not None and rng.random() < 0.4:
        cand = sorted({x for c, q, a, b in W.bom[p] for x in (a, b) if x and DATE_LO <= x <= DATE_HI})
        if cand:
            return rng.choice(cand)
    return DATE_LO + dt.timedelta(days=rng.randrange((DATE_HI - DATE_LO).days))


def direct(p, d=TODAY):
    return W.explode(p, d, 1)


def path_count(p, c, d=TODAY):
    n = 0
    for ch, q, a, b in W.bom.get(p, []):
        if W.valid(a, b, d):
            n += (ch == c) + path_count(ch, c, d)
    return n


# ================= L: lookups (control) =================
priced = sorted(p for p in P if P[p]["cost"] > 0 and num(p) != EXAMPLE)


def L1():
    p = rng.choice(priced)
    return add("L", "L1", f"What is the standard cost of the product named '{P[p]['name']}'?", "scalar", P[p]["cost"],
               dict(name=P[p]["name"]))


def L2():
    p = rng.choice(sorted(x for x in P if P[x]["sub"]))
    return add("L", "L2", f"Which product subcategory does product {num(p)} belong to? Return the subcategory name.", "set",
               [P[p]["sub"]], dict(product=num(p)))


def L3():
    p = rng.choice(asm)
    ch = direct(p)
    if not 3 <= len(ch) <= 9:
        return False
    return add("L", "L3", f"List the product numbers of the components that product {num(p)} uses directly (first BOM level only) in its current BOM.",
               "set", sorted(num(c) for c in ch), dict(product=num(p)))


def L4():
    # filter: the BOM line has more than one dated version, so the current quantity differs from an older one
    a = rng.choice(asm)
    vers = defaultdict(set)
    for c, q, x, y in W.bom[a]:
        vers[c].add(q)
    cand = [c for c in vers if len(vers[c]) > 1 and c in direct(a)]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("L", "L4", f"In the current BOM, how many units of component {num(c)} does product {num(a)} use directly per unit (first level only)?",
               "scalar", direct(a)[c], dict(product=num(a), component=num(c)))


def L5():
    sub = rng.choice(sorted({P[p]["sub"] for p in P if P[p]["sub"]}))
    prices = sorted({P[p]["price"] for p in P if P[p]["sub"] == sub})
    if len(prices) < 3:
        return False
    thr = float(int(prices[len(prices) // 2]))
    n = sum(1 for p in P if P[p]["sub"] == sub and P[p]["price"] > thr)
    if n == 0:
        return False
    return add("L", "L5", f"How many products in subcategory '{sub}' have a list price above {thr:.0f}?", "scalar", n,
               dict(subcategory=sub, threshold=thr))


pick(4, L1); pick(4, L2); pick(5, L3); pick(4, L4); pick(4, L5)


# ================= E: multi-level explosion =================
def E1():
    # filter: part only below the first level, and the answer differs from the number of paths (qty multiplication matters)
    x = rng.choice(asm)
    ex = W.parts(W.explode(x))
    cand = [c for c, q in ex.items() if c not in direct(x) and q != path_count(x, c)]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("E", "E1", f"In total, what quantity of part {num(c)} is needed to build one unit of product {num(x)} under the current BOM, counting all sub-assembly levels?",
               "scalar", ex[c], dict(product=num(x), part=num(c)))


def E2():
    # filter: some parts sit below the first level
    x = rng.choice(asm)
    ex = W.parts(W.explode(x))
    if len(ex) < 5 or len(ex) == len(W.parts(direct(x))):
        return False
    return add("E", "E2", f"How many distinct parts (purchased components) does product {num(x)} contain at any level of its current BOM?",
               "scalar", len(ex), dict(product=num(x)))


def E3():
    # filter: non-zero and different from the first-level-only cost
    x = rng.choice(asm)
    a, b = r2(W.purchased_cost(x)), r2(W.purchased_cost(x, TODAY, 1))
    if a == 0 or a == b:
        return False
    return add("E", "E3", f"What is the purchased-parts cost of one unit of product {num(x)} under the current BOM, i.e. the sum over all parts at any level of (quantity per unit x standard cost)? Round to 2 decimals.",
               "scalar", a, dict(product=num(x)))


def E4():
    # filter: differs from the number of manufactured sub-assemblies on the first level
    x = rng.choice(asm)
    s = [c for c in W.explode(x) if P[c]["make"]]
    if len(s) < 2 or len(s) == len([c for c in direct(x) if P[c]["make"]]):
        return False
    return add("E", "E4", f"How many distinct manufactured sub-assemblies (components with make_flag = true) appear at any level of the current BOM of product {num(x)}?",
               "scalar", len(s), dict(product=num(x)))


pick(12, E1); pick(10, E2); pick(10, E3); pick(8, E4)


# ================= W: where-used =================
is_bike = lambda pr: pr["cat"] == "Bikes"
_users = {}


def users(c, d=TODAY, bike=True):
    k = (c, d, bike)
    if k not in _users:
        _users[k] = W.users(c, d, is_bike if bike else (lambda pr: True))
    return _users[k]


def W1():
    # filter: 3..60 bikes, some contain it only below the first level
    c = rng.choice(parts_all + made_kids)
    u = users(c)
    if not 3 <= len(u) <= 60 or all(c in direct(b) for b in u):
        return False
    return add("W", "W1", f"How many bikes contain component {num(c)} at any level of their current BOM (directly or inside sub-assemblies)?",
               "scalar", len(u), dict(component=num(c)))


def W2():
    c = rng.choice(parts_all + made_kids)
    u = users(c)
    if not 2 <= len(u) <= 14 or all(c in direct(b) for b in u):
        return False
    return add("W", "W2", f"List the product numbers of all bikes that contain component {num(c)} at any level of their current BOM (directly or inside sub-assemblies).",
               "set", sorted(num(b) for b in u), dict(component=num(c)))


def W3():
    # filter: differs from the number of direct parents and from the number of bikes
    c = rng.choice(parts_all + made_kids)
    u = users(c, bike=False)
    par = [a for a in asm if c in direct(a)]
    if len(u) < 3 or len(u) == len(par) or len(u) == len(users(c)):
        return False
    return add("W", "W3", f"How many products (bikes and sub-assemblies alike) contain component {num(c)} at any level of their current BOM?",
               "scalar", len(u), dict(component=num(c)))


def W4():
    # filter: some (not all) bikes containing it are in the subcategory, and some contain it only below the first level
    c = rng.choice(parts_all + made_kids)
    sub = rng.choice(["Mountain Bikes", "Road Bikes", "Touring Bikes"])
    u = users(c)
    us = [b for b in u if P[b]["sub"] == sub]
    if len(us) < 2 or len(us) == len(u) or all(c in direct(b) for b in us):
        return False
    return add("W", "W4", f"How many bikes of subcategory '{sub}' contain component {num(c)} at any level of their current BOM?",
               "scalar", len(us), dict(component=num(c), subcategory=sub))


pick(10, W1); pick(8, W2); pick(8, W3); pick(7, W4)


# ================= A: aggregation through orders =================
TERR = {t: W.terr[t]["name"] for t in W.terr}
YEARS = [2022, 2023, 2024, 2025]
_dem = {}


def demand(t, y, ignore=False):
    k = (t, y, ignore)
    if k not in _dem:
        _dem[k] = W.demand(lambda o, ln: o["terr"] == t and o["date"].year == y, ignore_validity=ignore)
    return _dem[k]


def direct_sales(t, y):
    out = defaultdict(float)
    for o, ln in W.order_lines(lambda o, ln: o["terr"] == t and o["date"].year == y):
        out[ln["prod"]] += ln["qty"]
    return out


def A1():
    t, y = rng.choice(sorted(TERR)), rng.choice(YEARS)
    sub = rng.choice(sorted({P[p]["sub"] for p in P if P[p]["sub"]}))
    online = rng.choice([None, True, False])
    tot = sum(ln["qty"] for o, ln in W.order_lines(
        lambda o, ln: o["terr"] == t and o["date"].year == y and P[ln["prod"]]["sub"] == sub and (online is None or o["online"] == online)))
    if tot < 5:
        return False
    ch = "" if online is None else (" through online orders" if online else " through reseller (non-online) orders")
    return add("A", "A1", f"How many units of products in subcategory '{sub}' were sold in sales territory {TERR[t]} in {y}{ch}?",
               "scalar", tot, dict(territory=TERR[t], year=y, subcategory=sub, online=online))


def A2():
    # filter: line discounts matter (sum of line_total differs from sum of qty x unit_price)
    t, y, qtr = rng.choice(sorted(TERR)), rng.choice(YEARS), rng.randint(1, 4)
    ls = list(W.order_lines(lambda o, ln: o["terr"] == t and o["date"].year == y and (o["date"].month - 1) // 3 + 1 == qtr))
    rev = sum(ln["total"] for o, ln in ls)
    gross = sum(ln["qty"] * ln["price"] for o, ln in ls)
    if rev == 0 or abs(rev - gross) < 1:
        return False
    return add("A", "A2", f"What was the revenue of sales territory {TERR[t]} in Q{qtr} {y}? Round to 2 decimals.", "scalar", r2(rev),
               dict(territory=TERR[t], year=y, quarter=qtr))


def A3():
    # filter: the part is contained in sold products (not only sold directly) and the answer differs from the one
    # obtained when BOM validity is ignored
    t, y = rng.choice(sorted(TERR)), rng.choice(YEARS)
    dm, dmi, ds = demand(t, y), demand(t, y, True), direct_sales(t, y)
    cand = [c for c in dm if not P[c]["make"] and dm[c] - ds.get(c, 0) > 0 and abs(dm[c] - dmi.get(c, 0)) > 1e-9]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("A", "A3", f"What was the total demand for part {num(c)} from the orders of sales territory {TERR[t]} in {y}, i.e. units sold directly plus units contained at any level of the products sold, using the BOM valid on each order date?",
               "scalar", dm[c], dict(territory=TERR[t], year=y, component=num(c)))


def A4():
    t, y = rng.choice(sorted(TERR)), rng.choice(YEARS)
    u = defaultdict(float)
    for o, ln in W.order_lines(lambda o, ln: o["terr"] == t and o["date"].year == y and P[ln["prod"]]["cat"] == "Bikes"):
        u[ln["prod"]] += ln["qty"]
    top = sorted(u.items(), key=lambda kv: -kv[1])
    if len(top) < 4 or top[2][1] == top[3][1]:
        return False
    return add("A", "A4", f"Which 3 bikes sold the most units in sales territory {TERR[t]} in {y}? Return the 3 product numbers.",
               "set", [num(p) for p, _ in top[:3]], dict(territory=TERR[t], year=y))


def A5():
    # filter: the sub-assembly is both sold directly and contained in sold products
    t, y = rng.choice(sorted(TERR)), rng.choice(YEARS)
    dm, ds = demand(t, y), direct_sales(t, y)
    cand = [c for c in dm if P[c]["make"] and ds.get(c, 0) > 0 and dm[c] - ds[c] > 0]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("A", "A5", f"What was the total demand for sub-assembly {num(c)} from the orders of sales territory {TERR[t]} in {y}, counting units sold directly as well as units contained in other products sold (BOM valid on each order date)?",
               "scalar", dm[c], dict(territory=TERR[t], year=y, component=num(c)))


pick(8, A1); pick(9, A2); pick(10, A3); pick(7, A4); pick(8, A5)


# ================= V: as-of BOM =================
def V1():
    # filter: the as-of quantity is non-zero and differs from the current quantity
    x = rng.choice(asm)
    d = rand_date(x)
    then, now = W.parts(W.explode(x, d)), W.parts(W.explode(x))
    cand = [c for c in then if then[c] != now.get(c, 0)]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("V", "V1", f"What quantity of part {num(c)} did one unit of product {num(x)} require under the BOM valid on {d.isoformat()}, counting all sub-assembly levels?",
               "scalar", then[c], dict(product=num(x), part=num(c), date=d.isoformat()))


def V2():
    # filter: the as-of direct component set differs from the current one
    x = rng.choice(asm)
    d = rand_date(x)
    then, now = set(direct(x, d)), set(direct(x))
    if not 2 <= len(then) <= 12 or then == now:
        return False
    return add("V", "V2", f"List the product numbers of the components that product {num(x)} used directly (first BOM level) on {d.isoformat()}.",
               "set", sorted(num(c) for c in then), dict(product=num(x), date=d.isoformat()))


def V3():
    x = rng.choice(asm)
    d = rand_date(x)
    a, b = len(W.parts(W.explode(x, d))), len(W.parts(W.explode(x)))
    if a < 2 or a == b:
        return False
    return add("V", "V3", f"How many distinct parts (purchased components) did product {num(x)} contain at any level of the BOM valid on {d.isoformat()}?",
               "scalar", a, dict(product=num(x), date=d.isoformat()))


def V4():
    c = rng.choice(parts_all + made_kids)
    d = rand_date()
    a, b = len(users(c, d)), len(users(c))
    if a < 2 or a == b:
        return False
    return add("V", "V4", f"How many bikes contained component {num(c)} at any level of their BOM on {d.isoformat()}?",
               "scalar", a, dict(component=num(c), date=d.isoformat()))


def V5():
    x = rng.choice(asm)
    d = rand_date(x)
    a, b = r2(W.purchased_cost(x, d)), r2(W.purchased_cost(x))
    if a == 0 or a == b:
        return False
    return add("V", "V5", f"What was the purchased-parts cost of one unit of product {num(x)} under the BOM valid on {d.isoformat()}, i.e. the sum over all parts at any level of (quantity per unit x standard cost)? Round to 2 decimals.",
               "scalar", a, dict(product=num(x), date=d.isoformat()))


pick(10, V1); pick(8, V2); pick(8, V3); pick(6, V4); pick(4, V5)


# ================= out of single-query semantic-layer coverage =================
def V6x():
    x = rng.choice(asm)
    d1, d2 = sorted([rand_date(x), rand_date(x)])
    s1, s2 = set(direct(x, d1)), set(direct(x, d2))
    diff = s1 - s2
    if d1 == d2 or not 1 <= len(diff) <= 6:
        return False
    return add("V", "V6x", f"Which components did product {num(x)} use directly (first BOM level) on {d1.isoformat()} but no longer use directly on {d2.isoformat()}? Return their product numbers.",
               "set", sorted(num(c) for c in diff), dict(product=num(x), date1=d1.isoformat(), date2=d2.isoformat()), covered=False)


def V7x():
    x = rng.choice(asm)
    d = rand_date(x)
    then, now = W.parts(W.explode(x, d)), W.parts(W.explode(x))
    cand = [c for c in then if c in now and then[c] != now[c]]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("V", "V7x", f"By how much did the total quantity of part {num(c)} per unit of product {num(x)} (all levels) change from the BOM valid on {d.isoformat()} to the current BOM? Return current minus earlier.",
               "scalar", now[c] - then[c], dict(product=num(x), part=num(c), date=d.isoformat()), covered=False)


def W5x():
    b1, b2 = rng.sample(bikes, 2)
    e1, e2 = W.parts(W.explode(b1)), W.parts(W.explode(b2))
    diff = set(e1) - set(e2)
    if not 2 <= len(diff) <= 12:
        return False
    return add("W", "W5x", f"Which parts are contained (at any level of the current BOM) in bike {num(b1)} but not in bike {num(b2)}? Return their product numbers.",
               "set", sorted(num(c) for c in diff), dict(product1=num(b1), product2=num(b2)), covered=False)


def A6x():
    t1, t2 = rng.sample(sorted(TERR), 2)
    y = rng.choice(YEARS)
    sold = lambda t: {ln["prod"] for o, ln in W.order_lines(lambda o, ln: o["terr"] == t and o["date"].year == y and P[ln["prod"]]["cat"] == "Bikes")}
    diff = sold(t1) - sold(t2)
    if not 1 <= len(diff) <= 8:
        return False
    return add("A", "A6x", f"Which bikes were sold in sales territory {TERR[t1]} in {y} but not sold in sales territory {TERR[t2]} in {y}? Return their product numbers.",
               "set", sorted(num(p) for p in diff), dict(territory1=TERR[t1], territory2=TERR[t2], year=y), covered=False)


pick(4, V6x); pick(4, V7x); pick(4, W5x); pick(4, A6x)

for q in Q:
    if q["gold_type"] == "scalar":
        q["gold"] = float(q["gold"])
order = {c: i for i, c in enumerate("LEWAV")}
Q.sort(key=lambda q: (order[q["cls"]], q["qid"]))
if FRESH:
    for q in Q:
        q["qid"] = "F" + q["qid"]
with open(QUESTIONS, "w") as f:
    for q in Q:
        f.write(json.dumps(q) + "\n")
from collections import Counter
print(len(Q), dict(Counter(q["cls"] for q in Q)), dict(Counter(q["tpl"] for q in Q)),
      "out-of-layer:", sum(not q["s3_expressible"] for q in Q))
