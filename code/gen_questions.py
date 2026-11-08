"""Generate the 50-question pilot set over xb with gold answers from gold.py.

Five classes (counts): L lookup (6), E multi-level explosion (12), W where-used (10), A aggregation through orders (12),
V versioning / as-of (10; the xb generator versions quantities only, never adds or removes an edge). Instances are drawn with a fixed seed and kept only if they satisfy the per-template
"non-triviality" filter written below (e.g. the as-of answer must differ from the current answer), so that a
substrate cannot score by ignoring the factor the class is meant to test.
"""
import json, os, random, datetime as dt
from collections import defaultdict
from gold import World, TODAY

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = World(os.path.join(HERE, "data", "xb.duckdb"))
MULT = int(os.environ.get("N1_MULT", "1"))          # 1 = the frozen 50-question pilot set; 4 = the 200-question main set
OUTNAME = os.environ.get("N1_OUT", "questions.jsonl")
rng = random.Random(20261103 if MULT == 1 else 20261105)

# Fresh replication set (N1_FRESH=1, review round): a new seed, instances of the main set excluded, qids prefixed F.
FRESH = os.environ.get("N1_FRESH") == "1"
if FRESH:
    rng = random.Random(20261108)
YEARS = (2024, 2025) if MULT == 1 else (2023, 2024, 2025)
Q = []
used = set()
if FRESH:
    used |= {(q["tpl"], q["text"]) for q in map(json.loads, open(os.path.join(HERE, "data", "xb_questions.jsonl")))}


def add(cls, tpl, text, gtype, gold, **meta):
    key = (tpl, text)
    if key in used:
        return False
    used.add(key)
    Q.append(dict(qid=f"{cls}{len([q for q in Q if q['cls'] == cls]) + 1:02d}", cls=cls, tpl=tpl, text=text,
                  gold_type=gtype, gold=gold, **meta))
    return True


def r2(x):
    return round(float(x), 2)


def rand_date(lo=dt.date(2023, 3, 1), hi=dt.date(2026, 6, 30)):
    return lo + dt.timedelta(days=rng.randrange((hi - lo).days))


models = sorted(W.model)
nb_skus = sorted(s for s in W.sku if not W.sku[s]["bundle"])
bundles = sorted(s for s in W.sku if W.sku[s]["bundle"])
mc = lambda m: W.model[m]["code"]
cc = lambda c: W.comp[c]["code"]
sc = lambda s: W.sku[s]["code"]


def pick(n, gen):
    k = 0
    for _ in range(200000):
        if k == n:
            return
        if gen():
            k += 1
    if FRESH:  # the main set already used most instances of a small template: keep the rest
        print("fresh: short template", getattr(gen, "__name__", "?"), k, "of", n, flush=True); return
    raise RuntimeError("could not draw enough instances")


# ---------------- L: lookups (control) ----------------
def L1a():
    s = rng.choice(nb_skus)
    if len(W.sku_model[s]) != 1:
        return False
    return add("L", "L1a", f"Which company model does the sales SKU {sc(s)} map to? Return the model code.",
               "set", [mc(W.sku_model[s][0][0])])


def L1b():
    s = rng.choice(nb_skus + bundles)
    n = sum(1 for l in W.listing.values() if l["sku"] == s)
    if n < 2:
        return False
    return add("L", "L1b", f"In how many stores is the sales SKU {sc(s)} listed?", "scalar", n)


def L1c():
    m = rng.choice(models)
    direct = W.explode_model(m, TODAY, max_depth=1)
    if not 3 <= len(direct) <= 7:
        return False
    return add("L", "L1c", f"List the codes of the components that company model {mc(m)} uses directly (first level only) in its current BOM.",
               "set", sorted(cc(c) for c in direct))


pick(2 * MULT, L1a); pick(2 * MULT, L1b); pick(2 * MULT, L1c)


# ---------------- E: multi-level explosion ----------------
def E1():
    m = rng.choice(models)
    ex = W.parts(W.explode_model(m))
    direct = W.explode_model(m, TODAY, max_depth=1)
    cand = [c for c, q in ex.items() if c not in direct and q >= 2]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("E", "E1", f"In total, how many units of part {cc(c)} are needed to build one unit of company model {mc(m)} under the current BOM, counting all sub-assembly levels?",
               "scalar", ex[c])


def E2():
    m = rng.choice(models)
    ex = W.parts(W.explode_model(m))
    if len(ex) < 8:
        return False
    return add("E", "E2", f"How many distinct parts (non-assembly components) does company model {mc(m)} contain at any level of its current BOM?",
               "scalar", len(ex))


def E3():
    m = rng.choice(models)
    ex = W.parts(W.explode_model(m))
    if not ex:
        return False
    cost = sum(q * W.comp[c]["cost"] for c, q in ex.items())
    return add("E", "E3", f"What is the total parts cost of one unit of company model {mc(m)} under the current BOM, i.e. the sum over all parts at any level of (quantity per unit x part unit cost)? Round to 2 decimals.",
               "scalar", r2(cost))


def E4():
    b = rng.choice(bundles)
    ex = W.parts(W.explode_sku(b))
    cand = [c for c, q in ex.items() if q >= 2]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("E", "E4", f"How many units of part {cc(c)} are contained in one unit of the bundle SKU {sc(b)} under the current BOM (through its item SKUs, their company models and all sub-assembly levels)?",
               "scalar", ex[c])


for f in (E1, E2, E3, E4):
    pick(3 * MULT, f)


# ---------------- W: where-used ----------------
def users_model(c, d=TODAY):
    return sorted(m for m in models if c in W.explode_model(m, d))


asm = sorted(c for c in W.comp if W.comp[c]["asm"])
prt = sorted(c for c in W.comp if not W.comp[c]["asm"])


def W1():
    c = rng.choice(asm + prt)
    u = users_model(c)
    direct = [m for m in u if c in W.explode_model(m, TODAY, 1)]
    if not 3 <= len(u) <= 25 or len(direct) == len(u):
        return False
    return add("W", "W1", f"How many company models contain component {cc(c)} at any level (directly or inside sub-assemblies) under the current BOM?",
               "scalar", len(u))


def W2():
    c = rng.choice(asm + prt)
    u = users_model(c)
    direct = [m for m in u if c in W.explode_model(m, TODAY, 1)]
    if not 2 <= len(u) <= 6 or len(direct) == len(u):
        return False
    return add("W", "W2", f"List the codes of all company models that contain component {cc(c)} at any level (directly or inside sub-assemblies) under the current BOM.",
               "set", [mc(m) for m in u])


def W3():
    c = rng.choice(prt)
    u = set(users_model(c))
    skus = [s for s in nb_skus if any(m in u for m, _ in W.sku_model[s])]
    if not 3 <= len(skus) <= 60:
        return False
    return add("W", "W3", f"How many non-bundle sales SKUs contain part {cc(c)} at any level under the current BOM?", "scalar", len(skus))


def W4():
    m = rng.choice(models)
    bs = [b for b in bundles if m in W.sku_models(b)]
    if len(bs) < 2:
        return False
    return add("W", "W4", f"How many bundle SKUs contain company model {mc(m)} through at least one of their item SKUs?", "scalar", len(bs))


pick(3 * MULT, W1); pick(2 * MULT, W2); pick(3 * MULT, W3); pick(2 * MULT, W4)


# ---------------- A: aggregation through orders ----------------
def model_units(pred):
    out = defaultdict(float)
    for o, st, ln in W.completed_lines(pred):
        for m, q in W.sku_models(W.listing[ln["listing"]]["sku"]).items():
            out[m] += ln["qty"] * q
    return out


units_by_year = {y: model_units(lambda o, st, ln, y=y: o["date"].year == y) for y in YEARS}
direct_by_year = {y: defaultdict(float) for y in YEARS}
for o, st, ln in W.completed_lines(lambda o, st, ln: o["date"].year in YEARS):
    s = W.listing[ln["listing"]]["sku"]
    if not W.sku[s]["bundle"]:
        for m, q in W.sku_model[s]:
            direct_by_year[o["date"].year][m] += ln["qty"] * q


def A1():
    y = rng.choice(list(YEARS))
    m = rng.choice(models)
    tot, dirc = units_by_year[y].get(m, 0), direct_by_year[y].get(m, 0)
    if tot < 5 or tot == dirc:
        return False
    return add("A", "A1", f"How many units of company model {mc(m)} were sold in completed orders in {y}, counting both SKUs that map to it directly and SKUs inside bundles?",
               "scalar", tot)


def A2():
    p = rng.choice(sorted({s["platform"] for s in W.store.values()}))
    y, qtr = rng.choice(list(YEARS)), rng.randint(1, 4)
    rev = sum(ln["qty"] * ln["price"] for o, st, ln in W.completed_lines(
        lambda o, st, ln: st["platform"] == p and o["date"].year == y and (o["date"].month - 1) // 3 + 1 == qtr))
    return add("A", "A2", f"What was the total revenue (sum of quantity x unit price) of completed orders on platform {p} in Q{qtr} {y}? Round to 2 decimals.",
               "scalar", r2(rev))


def A3():
    sid = rng.choice(sorted(W.store))
    y = rng.choice(list(YEARS))
    lines = list(W.completed_lines(lambda o, st, ln: o["store"] == sid and o["date"].year == y))
    asof, now = defaultdict(float), defaultdict(float)
    for o, st, ln in lines:
        s = W.listing[ln["listing"]]["sku"]
        for c, q in W.parts(W.explode_sku(s, o["date"])).items():
            asof[c] += ln["qty"] * q
        for c, q in W.parts(W.explode_sku(s, TODAY)).items():
            now[c] += ln["qty"] * q
    cand = [c for c in asof if asof[c] >= 3 and abs(asof[c] - now.get(c, 0)) > 0]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("A", "A3", f"How many units of part {cc(c)} were consumed by completed orders of store {W.store[sid]['name']} in {y}? Explode every order line (bundles included) with the BOM that was valid on the order date.",
               "scalar", asof[c])


def A4():
    ctry = rng.choice(sorted({s["country"] for s in W.store.values()}))
    y = rng.choice(list(YEARS))
    u = model_units(lambda o, st, ln: st["country"] == ctry and o["date"].year == y)
    top = sorted(u.items(), key=lambda kv: -kv[1])
    if len(top) < 4 or top[2][1] == top[3][1]:
        return False
    return add("A", "A4", f"Which 3 company models sold the most units in completed orders in country {ctry} in {y}, counting units inside bundles? Return the 3 model codes.",
               "set", [mc(m) for m, _ in top[:3]])


for f in (A1, A2, A3, A4):
    pick(3 * MULT, f)


# ---------------- V: versioning / as-of ----------------
def V1():
    m = rng.choice(models)
    d = rand_date()
    then, now = W.parts(W.explode_model(m, d)), W.parts(W.explode_model(m))
    cand = [c for c in then if then[c] != now.get(c, 0)]
    if not cand:
        return False
    c = rng.choice(sorted(cand))
    return add("V", "V1", f"How many units of part {cc(c)} did one unit of company model {mc(m)} require on {d.isoformat()}, counting all sub-assembly levels of the BOM valid on that date?",
               "scalar", then[c])


def V2():
    m = rng.choice(models)
    d = rand_date()
    then, now = W.explode_model(m, d, 1), W.explode_model(m, TODAY, 1)
    diff = [c for c in set(then) | set(now) if then.get(c, 0) != now.get(c, 0)]
    if not 1 <= len(diff) <= 5:
        return False
    return add("V", "V2", f"Which components did company model {mc(m)} use directly (first level) on {d.isoformat()} with a different quantity than in its current BOM? Return the component codes.",
               "set", sorted(cc(c) for c in diff))


def V3():
    m = rng.choice(models)
    d = rand_date()
    cost = lambda ex: sum(q * W.comp[c]["cost"] for c, q in W.parts(ex).items())
    a, b = r2(cost(W.explode_model(m, d))), r2(cost(W.explode_model(m)))
    if a == b or a == 0:
        return False
    return add("V", "V3", f"What was the total parts cost of one unit of company model {mc(m)} under the BOM valid on {d.isoformat()}, i.e. the sum over all parts at any level of (quantity per unit x part unit cost)? Round to 2 decimals.",
               "scalar", a)


def V4():
    m = rng.choice(models)
    d = rand_date()
    a, b = sum(W.parts(W.explode_model(m, d)).values()), sum(W.parts(W.explode_model(m)).values())
    if a == b or a == 0:
        return False
    return add("V", "V4", f"How many part units in total (the sum of quantities over all parts at any level) did one unit of company model {mc(m)} contain under the BOM valid on {d.isoformat()}?",
               "scalar", a)


pick(3 * MULT, V1); pick(2 * MULT, V2); pick(3 * MULT, V3); pick(2 * MULT, V4)

for q in Q:
    if q["gold_type"] == "scalar":
        q["gold"] = float(q["gold"])
if FRESH:
    for q in Q:
        q["qid"] = "F" + q["qid"]
with open(os.path.join(HERE, "data", OUTNAME), "w") as f:
    for q in Q:
        f.write(json.dumps(q) + "\n")
print(len(Q), {c: sum(q["cls"] == c for q in Q) for c in "LEWAV"})
