"""Local copy of code/make_sources.build_xb with ONE change: the number of assembly tiers is a parameter `tiers`
(the original hard-codes 3). With tiers=3 it is identical to the original generator (checked by sweep.py --check-tiers).
The temporary order-line CSV is written next to the output database (the original writes it to data/).
Used only by the scale sweep (scale_sweep/sweep.py --tiers 5)."""
import csv
import os
import random
from datetime import date, timedelta

import duckdb


def build_xb(path, seed=20261102, scale=1.0, tiers=3):
    """Synthetic cross-border manufacturer-retailer. Four-level SKU model: component -> company model -> sales SKU -> bundle."""
    rng = random.Random(seed)
    con = duckdb.connect(path)
    lines = ["ErgoRise", "AlpineDesk", "TerraLift", "NovaMount", "VistaArm", "SwiftBase", "CoreStand", "AeroDesk"]
    cats = ["tabletop", "leg", "motor", "controller", "cable", "fastener", "bracket", "crossbar", "foot", "gearbox"]
    finishes = ["BLK", "WHT", "OAK", "WAL", "GRY"]
    n_leaf = int(3000 * scale)
    n_asm = int(800 * scale)
    n_model = int(800 * scale)
    n_sku = int(3000 * scale)
    n_bundle = int(400 * scale)
    n_store = 24
    n_orders = int(120000 * scale)

    comps = []
    for i in range(n_leaf):
        c = rng.choice(cats)
        comps.append((i + 1, f"{c[:3].upper()}-{rng.randint(100, 999)}-{finishes[i % 5]}-{i:05d}", f"{c} {rng.choice(['A', 'B', 'C', 'S', 'X'])}{rng.randint(1, 99)}", c, False, round(rng.uniform(0.2, 90), 2)))
    for j in range(n_asm):
        i = n_leaf + j
        c = rng.choice(["frame", "lift column", "drive kit", "wiring kit", "hardware pack"])
        comps.append((i + 1, f"ASM-{rng.randint(100, 999)}-{i:05d}", f"{c} {rng.randint(1, 99)}", "assembly", True, 0.0))
    con.execute("CREATE TABLE component(component_id INT, component_code VARCHAR, name VARCHAR, category VARCHAR, is_assembly BOOLEAN, unit_cost DOUBLE)")
    con.executemany("INSERT INTO component VALUES (?,?,?,?,?,?)", comps)

    # Sub-assembly edges: strictly from lower to higher ids among assemblies (DAG), plus leaves; a small popular-part core makes diamonds common.
    popular = rng.sample(range(1, n_leaf + 1), max(30, n_leaf // 50))
    d0, d1 = date(2023, 1, 1), date(2026, 9, 30)
    span = (d1 - d0).days

    def rand_validity():
        # 70% open-ended since start of history; 30% engineering change with a cut-over date.
        if rng.random() < 0.7:
            return [(d0, None)]
        cut = d0 + timedelta(days=rng.randint(60, span - 60))
        return [(d0, cut - timedelta(days=1)), (cut, None)]

    cb = []
    for j in range(n_asm):
        pid = n_leaf + j + 1
        kids = set()
        for _ in range(rng.randint(2, 7)):
            kids.add(rng.choice(popular) if rng.random() < 0.5 else rng.randint(1, n_leaf))
        # `tiers` assembly tiers (j % tiers): a tier-t assembly may contain only tier t+1 assemblies (original: tiers = 3).
        tier = j % tiers
        if tier < tiers - 1 and rng.random() < 0.7:
            for _ in range(rng.randint(1, 2)):
                k = rng.randint(0, (n_asm - 1) // tiers) * tiers + tier + 1
                if k < n_asm:
                    kids.add(n_leaf + k + 1)
        for k in kids:
            for (a, b) in rand_validity():
                cb.append((pid, k, rng.choice([1, 1, 2, 2, 4, 6, 8]), a, b))
    con.execute("CREATE TABLE component_bom(parent_component_id INT, child_component_id INT, qty INT, valid_from DATE, valid_to DATE)")
    con.executemany("INSERT INTO component_bom VALUES (?,?,?,?,?)", cb)

    models = []
    mb = []
    for m in range(n_model):
        ln = rng.choice(lines)
        code = f"{ln[:2].upper()}{rng.randint(1, 9)}-{rng.choice(['120', '140', '160', '180'])}{rng.choice(finishes)}{m % 97:02d}"
        models.append((m + 1, code + f"-{m:04d}", f"{ln} height-adjustable desk {code[:3]}", ln))
        kids = set(n_leaf + rng.randint(1, n_asm) for _ in range(rng.randint(1, 4)))
        kids |= set(rng.randint(1, n_leaf) for _ in range(rng.randint(1, 5)))
        for k in kids:
            for (a, b) in rand_validity():
                mb.append((m + 1, k, rng.choice([1, 1, 1, 2, 2, 4]), a, b))
    con.execute("CREATE TABLE company_model(model_id INT, model_code VARCHAR, name VARCHAR, product_line VARCHAR)")
    con.executemany("INSERT INTO company_model VALUES (?,?,?,?)", models)
    con.execute("CREATE TABLE model_bom(model_id INT, component_id INT, qty INT, valid_from DATE, valid_to DATE)")
    con.executemany("INSERT INTO model_bom VALUES (?,?,?,?,?)", mb)

    # Sales SKUs: near-identical codes are deliberate (variant suffix R = refurbished, -US/-DE market).
    skus, skm = [], []
    markets = ["US", "DE", "UK", "JP", "CA"]
    for s in range(n_sku):
        m = rng.randint(1, n_model)
        mk = rng.choice(markets)
        suffix = rng.choice(["", "", "", "R", "P"])
        code = f"{models[m - 1][1].rsplit('-', 1)[0]}-{mk}{suffix}-{s % 89:02d}"
        skus.append((s + 1, code, f"{models[m - 1][2]} ({mk})", mk, False))
        skm.append((s + 1, m, 1))
        if rng.random() < 0.12:  # sales SKU packaged from two company models
            skm.append((s + 1, rng.randint(1, n_model), rng.choice([1, 2])))
    bi = []
    for b in range(n_bundle):
        sid = n_sku + b + 1
        mk = rng.choice(markets)
        skus.append((sid, f"BDL-{mk}-{b:04d}", f"Bundle {b} ({mk})", mk, True))
        for it in rng.sample(range(1, n_sku + 1), rng.randint(2, 4)):
            bi.append((sid, it, rng.choice([1, 1, 2])))
    con.execute("CREATE TABLE sales_sku(sku_id INT, sku_code VARCHAR, title VARCHAR, market VARCHAR, is_bundle BOOLEAN)")
    con.executemany("INSERT INTO sales_sku VALUES (?,?,?,?,?)", skus)
    con.execute("CREATE TABLE sku_model(sku_id INT, model_id INT, qty INT)")
    con.executemany("INSERT INTO sku_model VALUES (?,?,?)", skm)
    con.execute("CREATE TABLE bundle_item(bundle_sku_id INT, item_sku_id INT, qty INT)")
    con.executemany("INSERT INTO bundle_item VALUES (?,?,?)", bi)

    plats = ["Amazon", "Amazon", "Amazon", "Shopify", "Walmart", "Wayfair"]
    stores = [(i + 1, f"{plats[i % 6]}-{markets[i % 5]}-{i:02d}", plats[i % 6], markets[i % 5]) for i in range(n_store)]
    con.execute("CREATE TABLE store(store_id INT, store_name VARCHAR, platform VARCHAR, country VARCHAR)")
    con.executemany("INSERT INTO store VALUES (?,?,?,?)", stores)

    listings = []
    for s in skus:
        for st in rng.sample(stores, rng.randint(1, 3)):
            listings.append((len(listings) + 1, st[0], s[0], "B0" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(8))))
    con.execute("CREATE TABLE listing(listing_id INT, store_id INT, sku_id INT, external_id VARCHAR)")
    con.executemany("INSERT INTO listing VALUES (?,?,?,?)", listings)

    # Heavy-tailed demand.
    weights = [1.0 / (i + 1) ** 0.8 for i in range(len(listings))]
    picks = rng.choices(range(len(listings)), weights=weights, k=int(n_orders * 1.4))
    con.execute("CREATE TABLE orders(order_id INT, store_id INT, order_date DATE, status VARCHAR)")
    con.execute("CREATE TABLE order_line(order_id INT, line_no INT, listing_id INT, qty INT, unit_price DOUBLE)")
    ords, ols = [], []
    idx = 0
    for o in range(n_orders):
        k = rng.choice([1, 1, 1, 2, 3])
        st = None
        ln = 1
        od = d0 + timedelta(days=rng.randint(0, span))
        for _ in range(k):
            if idx >= len(picks):
                break
            l = listings[picks[idx]]
            idx += 1
            if st is None:
                st = l[1]
            elif l[1] != st:
                continue
            ols.append((o + 1, ln, l[0], rng.choice([1, 1, 1, 2, 3]), round(rng.uniform(60, 900), 2)))
            ln += 1
        if st is not None:
            ords.append((o + 1, st, od, "cancelled" if rng.random() < 0.06 else "completed"))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?)", ords)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)), f"_ol_{os.getpid()}.csv")
    with open(tmp, "w", newline="") as f:
        csv.writer(f).writerows(ols)
    con.execute(f"INSERT INTO order_line SELECT * FROM read_csv('{tmp}', header=false)")
    os.remove(tmp)
    con.close()
