"""Generate the LEGO question set (data/lego_questions.jsonl) with gold answers from gold.py.

Classes (counts): L lookup (12), E explosion through nested sets and minifigs (50 + 4 out of S3 coverage),
W where-used with/without colour identity (36 + 4), A catalogue roll-ups over the theme hierarchy and years (42 + 4),
V inventory versions (34 + 4). Instances are drawn with a fixed seed and kept only if they satisfy the per-template
non-triviality filter written next to each template (E: the answer must change when nested sets/minifigs are ignored;
W: some users are reached only through nesting; A: the answer must change when sub-themes are ignored; V: the
version-k answer must differ from the current one; V5 asks an intermediate version k), so a substrate cannot score by ignoring the tested factor.
Templates X1-X4 are deliberately outside the semantic layer's coverage (set difference, average, conjunction of two
where-used conditions, ratio); their records carry s3_covered = false.

Record fields: qid ('LG-<cls><nn>'), cls, tpl, text, gold_type (scalar|set), gold, s3_covered, args (template
parameters; used only by check_gold.py, never shown to a model).
"""
import json, os, random, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gold import World  # noqa: E402

CODE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(os.path.dirname(CODE), "data")
W = World(os.path.join(DATA, "lego.duckdb"))
rng = random.Random(20261105)
Q, used = [], set()

# Fresh replication set (N1_FRESH=1, review round): a new seed, instances of the main set excluded, qids prefixed F.
FRESH = os.environ.get("N1_FRESH") == "1"
if FRESH:
    rng = random.Random(20261108)
    used |= {(q["tpl"], q["text"]) for q in map(json.loads, open(os.path.join(DATA, "lego_questions.jsonl")))}
YEARS = list(range(2012, 2026))


def add(cls, tpl, text, gtype, gold, args, covered=True):
    key = (tpl, text)
    if key in used:
        return False
    used.add(key)
    n = len([q for q in Q if q["cls"] == cls]) + 1
    if gtype == "scalar":
        gold = float(gold)
    else:
        gold = sorted(str(x) for x in gold)
    Q.append(dict(qid=f"LG-{cls}{n:02d}", cls=cls, tpl=tpl, text=text, gold_type=gtype, gold=gold,
                  s3_covered=covered, args=args))
    return True


def pick(n, gen, tries=20000):
    k = 0
    for _ in range(tries):
        if k == n:
            return
        if gen():
            k += 1
    if FRESH:  # the main set already used most instances of a small template: keep the rest
        print("fresh: short template", getattr(gen, "__name__", "?"), k, "of", n, flush=True); return
    raise RuntimeError(f"could not draw enough instances for {gen.__name__}")


# ---------------- precomputed indexes (all from gold.World) ----------------
SETS = sorted(W.sets)
EX = {s: W.explode(s) for s in SETS}                      # current, spares excluded
OWN = {s: W.own_parts(s) for s in SETS}
EXP = {s: W.by_part(EX[s]) for s in SETS}
OWNP = {s: W.by_part(OWN[s]) for s in SETS}
FIGS = {s: W.minifigs(s) for s in SETS}
DFIGS = {s: dict(W.direct(s)[2]) for s in SETS}
NEST = sorted(s for s in SETS if W.direct(s)[1])         # sets with nested sets (current inventory)
COMPLEX = sorted(s for s in SETS if W.direct(s)[1] or W.direct(s)[2])  # nested sets or minifigs
MULTI = sorted(s for s in SETS if len(W.versions(s)) > 1)
part_users = defaultdict(dict)        # part -> {set: {color: qty}}
own_users = defaultdict(set)          # part -> sets that list it directly (non-spare)
own_users_pc = defaultdict(set)       # (part, color) -> sets
for s in SETS:
    for (p, c), q in EX[s].items():
        part_users[p].setdefault(s, {})[c] = q
    for (p, c) in OWN[s]:
        own_users[p].add(s)
        own_users_pc[(p, c)].add(s)
fig_users = defaultdict(set)
for s in SETS:
    for f in FIGS[s]:
        fig_users[f].add(s)
dfig_users = defaultdict(set)
for s in SETS:
    for f in DFIGS[s]:
        dfig_users[f].add(s)
name_count = defaultdict(int)
for t in W.themes.values():
    name_count[t["name"]] += 1
UNIQ_THEMES = sorted(t for t, v in W.themes.items() if name_count[v["name"]] == 1)
PARENT_THEMES = sorted(t for t in UNIQ_THEMES if any(v["parent"] == t for v in W.themes.values()))
tn = lambda t: W.themes[t]["name"]
cn = lambda c: W.colors[c]


def users_pc(p, c):
    return {s for s, cs in part_users[p].items() if c in cs}


# ---------------- L: lookups (control) ----------------
def L1():
    s = rng.choice(SETS)
    if W.sets[s]["year"] < 2010:
        return False
    return add("L", "L1", f"In which year was LEGO set {s} released?", "scalar", W.sets[s]["year"], {"set_num": s})


def L2():
    s = rng.choice(MULTI)
    return add("L", "L2", f"How many inventory versions does LEGO set {s} have?", "scalar", len(W.versions(s)), {"set_num": s})


def L3():
    s = rng.choice(NEST)
    ns = [x for x, _ in W.direct(s)[1]]
    if not 2 <= len(ns) <= 6:
        return False
    return add("L", "L3", f"List the set numbers of the sets listed directly (first level only) in the current inventory of LEGO set {s}.",
               "set", ns, {"set_num": s})


def L4():
    s = rng.choice(COMPLEX)
    n = len(DFIGS[s])
    if n < 2:
        return False
    return add("L", "L4", f"How many different minifigs are listed directly (first level only) in the current inventory of LEGO set {s}?",
               "scalar", n, {"set_num": s})


for f in (L1, L2, L3, L4):
    pick(3, f)


# ---------------- E: explosion ----------------
def E1():
    s = rng.choice(COMPLEX)
    cand = [p for p, q in EXP[s].items() if q >= 2 and q != OWNP[s].get(p, 0)]
    if not cand:
        return False
    p = rng.choice(sorted(cand))
    return add("E", "E1", f"In total, how many units of part {p} (in any colour) does one LEGO set {s} contain, counting all levels, i.e. the parts inside its nested sets and minifigs as well?",
               "scalar", EXP[s][p], {"set_num": s, "part_num": p})


def E2():
    s = rng.choice(COMPLEX)
    cand = [(p, c) for (p, c), q in EX[s].items() if q != OWN[s].get((p, c), 0) and EXP[s][p] != q]
    if not cand:
        return False
    p, c = rng.choice(sorted(cand))
    return add("E", "E2", f"How many units of part {p} in colour {cn(c)} does one LEGO set {s} contain in total, counting the parts inside its nested sets and minifigs?",
               "scalar", EX[s][(p, c)], {"set_num": s, "part_num": p, "color": cn(c)})


def E3():
    s = rng.choice(COMPLEX)
    tot, own = sum(EX[s].values()), sum(OWN[s].values())
    sp = sum(W.explode(s, None, True).values())
    if tot == own or sp == tot or tot == 0:
        return False
    return add("E", "E3", f"How many parts in total (sum of quantities) does LEGO set {s} contain at all levels, including the parts of its nested sets and minifigs?",
               "scalar", tot, {"set_num": s})


def E4():
    s = rng.choice(COMPLEX)
    a, b = len(EXP[s]), len(OWNP[s])
    if a == b or a < 5:
        return False
    return add("E", "E4", f"How many distinct part numbers (ignoring colour) occur in LEGO set {s} at any level, including inside its nested sets and minifigs?",
               "scalar", a, {"set_num": s})


def E5():
    s = rng.choice(COMPLEX)
    a, b = len(EX[s]), len(OWN[s])
    if a == b or a == len(EXP[s]) or a < 5:
        return False
    return add("E", "E5", f"How many distinct part/colour combinations occur in LEGO set {s} at any level, including inside its nested sets and minifigs?",
               "scalar", a, {"set_num": s})


def E6():
    s = rng.choice(NEST)
    a, b = sum(FIGS[s].values()), sum(DFIGS[s].values())
    if a == b:
        return False
    return add("E", "E6", f"How many minifigs in total (sum of quantities) does LEGO set {s} contain, including the minifigs inside its nested sets?",
               "scalar", a, {"set_num": s})


def E7():
    s = rng.choice(COMPLEX)
    ws = W.explode(s, None, True)
    a, b, own = sum(ws.values()), sum(EX[s].values()), sum(W.own_parts(s, None, True).values())
    if a == b or a == own:
        return False
    return add("E", "E7", f"Including spare parts, how many parts in total (sum of quantities) does LEGO set {s} contain at all levels, including its nested sets and minifigs?",
               "scalar", a, {"set_num": s, "include_spares": True})


def E8():
    s = rng.choice(COMPLEX)
    bycat, owncat = defaultdict(int), defaultdict(int)
    for (p, c), q in EX[s].items():
        bycat[W.parts[p]["cat"]] += q
    for (p, c), q in OWN[s].items():
        owncat[W.parts[p]["cat"]] += q
    cand = [k for k in bycat if bycat[k] != owncat.get(k, 0) and bycat[k] >= 2]
    if not cand:
        return False
    k = rng.choice(sorted(cand))
    return add("E", "E8", f"How many parts of the part category '{W.cats[k]}' (sum of quantities) does LEGO set {s} contain at all levels, including its nested sets and minifigs?",
               "scalar", bycat[k], {"set_num": s, "category": W.cats[k]})


for f, n in ((E1, 8), (E2, 7), (E3, 7), (E4, 6), (E5, 6), (E6, 6), (E7, 5), (E8, 5)):
    pick(n, f)


# ---------------- W: where-used ----------------
W1C = sorted(p for p, us in part_users.items() if 3 <= len(us) <= 400 and set(us) != own_users[p])
pc_users = defaultdict(set)
for p, us in part_users.items():
    for s, cs in us.items():
        for c in cs:
            pc_users[(p, c)].add(s)
W2C = sorted(k for k, us in pc_users.items() if 3 <= len(us) <= 400 and us != own_users_pc[k] and us != set(part_users[k[0]]))


def W1():
    p = rng.choice(W1C)
    return add("W", "W1", f"How many LEGO sets contain part {p} (in any colour) at any level of their current inventory, i.e. directly or inside a nested set or minifig?",
               "scalar", len(part_users[p]), {"part_num": p})


def W2():
    p, c = rng.choice(W2C)
    return add("W", "W2", f"How many LEGO sets contain part {p} in colour {cn(c)} at any level of their current inventory, i.e. directly or inside a nested set or minifig?",
               "scalar", len(pc_users[(p, c)]), {"part_num": p, "color": cn(c)})


W3C = sorted(f for f, us in fig_users.items() if 2 <= len(us) <= 8 and us != dfig_users[f])


def W3():
    f = rng.choice(W3C)
    return add("W", "W3", f"List the set numbers of all LEGO sets that contain minifig {f}, either directly or inside a nested set.",
               "set", fig_users[f], {"fig_num": f})


def W4():
    p, c = rng.choice(W2C)
    y = rng.choice(YEARS)
    us = {s for s in pc_users[(p, c)] if W.sets[s]["year"] == y}
    ow = {s for s in own_users_pc[(p, c)] if W.sets[s]["year"] == y}
    if len(us) < 2 or us == ow or us == pc_users[(p, c)]:
        return False
    return add("W", "W4", f"How many LEGO sets released in {y} contain part {p} in colour {cn(c)} at any level, directly or inside a nested set or minifig?",
               "scalar", len(us), {"part_num": p, "color": cn(c), "year": y})


W5C = sorted(k for k, us in pc_users.items() if 2 <= len(us) <= 8 and us != own_users_pc[k] and us != set(part_users[k[0]]))


def W5():
    p, c = rng.choice(W5C)
    return add("W", "W5", f"List the set numbers of all LEGO sets that contain part {p} in colour {cn(c)} at any level, directly or inside a nested set or minifig.",
               "set", pc_users[(p, c)], {"part_num": p, "color": cn(c)})


for f, n in ((W1, 8), (W2, 8), (W3, 8), (W4, 6), (W5, 6)):
    pick(n, f)


# ---------------- A: catalogue roll-ups (themes incl. sub-themes, years) ----------------
def sets_in(t, y, sub=True):
    return [s for s in SETS if W.sets[s]["year"] == y and (W.in_subtree(s, t) if sub else W.sets[s]["theme"] == t)]


def A1():
    t, y = rng.choice(PARENT_THEMES), rng.choice(YEARS)
    a, b = len(sets_in(t, y)), len(sets_in(t, y, False))
    if a < 3 or a == b:
        return False
    return add("A", "A1", f"How many LEGO sets released in {y} belong to the theme '{tn(t)}' or to any of its sub-themes?",
               "scalar", a, {"theme": tn(t), "year": y})


def A2():
    t, y = rng.choice(PARENT_THEMES), rng.choice(YEARS)
    a = sum(sum(FIGS[s].values()) for s in sets_in(t, y))
    b = sum(sum(FIGS[s].values()) for s in sets_in(t, y, False))
    if a < 3 or a == b:
        return False
    return add("A", "A2", f"Across all LEGO sets released in {y} in the theme '{tn(t)}' including its sub-themes, how many minifigs are there in total (sum of quantities, minifigs inside nested sets included, each set counted on its own)?",
               "scalar", a, {"theme": tn(t), "year": y})


def A3():
    t, y = rng.choice(PARENT_THEMES), rng.choice(YEARS)
    a = sum(sum(EX[s].values()) for s in sets_in(t, y))
    b = sum(sum(EX[s].values()) for s in sets_in(t, y, False))
    if a < 10 or a == b:
        return False
    return add("A", "A3", f"Across all LEGO sets released in {y} in the theme '{tn(t)}' including its sub-themes, how many parts are there in total (sum of quantities at all levels, including nested sets and minifigs, each set counted on its own)?",
               "scalar", a, {"theme": tn(t), "year": y})


def A4():
    y = rng.choice(YEARS)
    cnt = defaultdict(int)
    for s in SETS:
        if W.sets[s]["year"] == y:
            cnt[W.root_theme(s)] += 1
    top = sorted(cnt.items(), key=lambda kv: -kv[1])
    direct = defaultdict(int)
    for s in SETS:
        if W.sets[s]["year"] == y:
            direct[W.sets[s]["theme"]] += 1
    dtop = {t for t, _ in sorted(direct.items(), key=lambda kv: -kv[1])[:3]}
    if len(top) < 4 or top[2][1] == top[3][1] or {t for t, _ in top[:3]} == dtop:
        return False
    return add("A", "A4", f"Which 3 top-level themes had the most LEGO sets released in {y}, counting the sets of all their sub-themes? Return the 3 theme names.",
               "set", [tn(t) for t, _ in top[:3]], {"year": y})


def A5():
    p, c = rng.choice(W2C)
    y = rng.choice(YEARS)
    ss = [s for s in SETS if W.sets[s]["year"] == y]
    a = sum(EX[s].get((p, c), 0) for s in ss)
    b = sum(OWN[s].get((p, c), 0) for s in ss)
    if a < 2 or a == b:
        return False
    return add("A", "A5", f"Summed over all LEGO sets released in {y} (each set's current inventory exploded on its own, including nested sets and minifigs), how many units of part {p} in colour {cn(c)} are there?",
               "scalar", a, {"part_num": p, "color": cn(c), "year": y})


def A6():
    t, y = rng.choice(PARENT_THEMES), rng.choice(YEARS)
    cols = lambda ss: {c for s in ss for (p, c) in EX[s]}
    a, b = len(cols(sets_in(t, y))), len(cols(sets_in(t, y, False)))
    if a < 3 or a == b:
        return False
    return add("A", "A6", f"How many distinct colours occur among the parts (at all levels, including nested sets and minifigs) of the LEGO sets released in {y} in the theme '{tn(t)}' including its sub-themes?",
               "scalar", a, {"theme": tn(t), "year": y})


for f, n in ((A1, 8), (A2, 7), (A3, 7), (A4, 6), (A5, 7), (A6, 7)):
    pick(n, f)


# ---------------- V: inventory versions ----------------
def V1():
    s = rng.choice(MULTI)
    a, b = W.by_part(W.explode(s, 1)), EXP[s]
    cand = [p for p in a if a[p] != b.get(p, 0)]
    if not cand:
        return False
    p = rng.choice(sorted(cand))
    return add("V", "V1", f"How many units of part {p} (in any colour) did version 1 of the inventory of LEGO set {s} contain, counting all levels (nested sets and minifigs)?",
               "scalar", a[p], {"set_num": s, "part_num": p, "version": 1})


def V2():
    s = rng.choice(MULTI)
    a, b = sum(W.explode(s, 1).values()), sum(EX[s].values())
    if a == b or a == 0:
        return False
    return add("V", "V2", f"How many parts in total (sum of quantities at all levels, including nested sets and minifigs) did version 1 of the inventory of LEGO set {s} contain?",
               "scalar", a, {"set_num": s, "version": 1})


def V3():
    s = rng.choice(MULTI)
    a, b = len(W.by_part(W.explode(s, 1))), len(EXP[s])
    if a == b or a == 0:
        return False
    return add("V", "V3", f"How many distinct part numbers (ignoring colour) did version 1 of the inventory of LEGO set {s} contain at any level?",
               "scalar", a, {"set_num": s, "version": 1})


def V4():
    s = rng.choice(MULTI)
    a, b = sum(W.minifigs(s, 1).values()), sum(FIGS[s].values())
    if a == b or (FRESH and a == 0):  # the main set has no empty version-1 inventory (an empty sum is NULL in the layer)
        return False
    return add("V", "V4", f"How many minifigs in total (sum of quantities, including minifigs inside nested sets) did version 1 of the inventory of LEGO set {s} contain?",
               "scalar", a, {"set_num": s, "version": 1})


MULTI3 = [s for s in MULTI if len(W.versions(s)) >= 3]


def V5():
    s = rng.choice(MULTI3)
    vs = W.versions(s)
    k = rng.choice(vs[1:-1])
    a, b, c = sum(W.explode(s, k).values()), sum(EX[s].values()), sum(W.explode(s, 1).values())
    if a == b or a == 0:
        return False
    return add("V", "V5", f"How many parts in total (sum of quantities at all levels) did version {k} of the inventory of LEGO set {s} contain?",
               "scalar", a, {"set_num": s, "version": k})


for f, n in ((V1, 8), (V2, 8), (V3, 7), (V4, 6), (V5, 5)):
    pick(n, f)


# ---------------- X: deliberately outside the semantic layer ----------------
def X1():
    s = rng.choice(MULTI)
    a, b = set(EXP[s]), set(W.by_part(W.explode(s, 1)))
    d = a - b
    if not 1 <= len(d) <= 6:
        return False
    return add("V", "X1", f"Which part numbers occur (at any level) in the current inventory of LEGO set {s} but not in version 1 of its inventory? Ignore colours.",
               "set", d, {"set_num": s}, covered=False)


def X2():
    t, y = rng.choice(PARENT_THEMES), rng.choice(YEARS)
    ss = sets_in(t, y)
    if len(ss) < 3 or len(ss) == len(sets_in(t, y, False)):
        return False
    avg = sum(sum(EX[s].values()) for s in ss) / len(ss)
    return add("A", "X2", f"What is the average number of parts per set (sum of quantities at all levels, including nested sets and minifigs) over the LEGO sets released in {y} in the theme '{tn(t)}' including its sub-themes? Round to 2 decimals.",
               "scalar", round(avg, 2), {"theme": tn(t), "year": y}, covered=False)


def X3():
    f = rng.choice(sorted(f for f, us in fig_users.items() if len(us) >= 3))
    cand = defaultdict(int)
    for s in fig_users[f]:
        for p in EXP[s]:
            cand[p] += 1
    ps = [p for p, n in cand.items() if 2 <= n < len(fig_users[f]) and len(part_users[p]) > n]
    if not ps:
        return False
    p = rng.choice(sorted(ps))
    n = len(fig_users[f] & set(part_users[p]))
    return add("W", "X3", f"How many LEGO sets contain both minifig {f} and part {p} (in any colour), each at any level?",
               "scalar", n, {"fig_num": f, "part_num": p}, covered=False)


def X4():
    s = rng.choice(COMPLEX)
    tot, own = sum(EX[s].values()), sum(OWN[s].values())
    if tot == 0 or tot == own:
        return False
    return add("E", "X4", f"What percentage of the total part quantity of LEGO set {s} (all levels, including nested sets and minifigs) comes from its nested sets and minifigs rather than from the parts listed directly in its own inventory? Round to 2 decimals.",
               "scalar", round(100.0 * (tot - own) / tot, 2), {"set_num": s}, covered=False)


for f in (X1, X2, X3, X4):
    pick(4, f)

if FRESH:
    for q in Q:
        q["qid"] = "F" + q["qid"]
with open(os.path.join(DATA, "lego_questions_fresh.jsonl" if FRESH else "lego_questions.jsonl"), "w") as fh:
    for q in Q:
        fh.write(json.dumps(q) + "\n")
print(len(Q), {c: sum(q["cls"] == c for q in Q) for c in "LEWAV"}, "out of S3 coverage:", sum(not q["s3_covered"] for q in Q))
