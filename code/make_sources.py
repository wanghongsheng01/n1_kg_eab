"""Build the three source snapshots (data/{aw,lego,xb}_src.duckdb) from raw public inputs.

aw:   AdventureWorks CSV exports (microsoft/sql-server-samples, MIT) in <raw>/aw/
lego: Rebrickable catalog downloads (*.csv.gz) in <raw>/rebrickable/ (download them yourself; respect Rebrickable's terms)
xb:   the synthetic cross-border generator (seeded; --scale for larger instances)
Usage: python make_sources.py --raw <raw_dir> [--only aw,lego,xb] [--scale 1.0]
"""
import argparse
import csv
import os
import random
from datetime import date, timedelta

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
DBS = os.path.join(ROOT, "data")


def build_aw(path):
    con = duckdb.connect(path)
    d = os.path.join(RAW, "aw")

    def load(name, cols, delim="\t"):
        rows = []
        with open(os.path.join(d, name + ".csv"), encoding="utf-8", errors="replace") as f:
            text = f.read()
        if delim == "+|":
            recs = [r.split("+|") for r in text.split("&|\n") if r.strip()]
            if len(recs) <= 1:
                recs = [r.split("+|") for r in text.split("\n") if r.strip()]
        else:
            recs = [r.split("\t") for r in text.split("\n") if r.strip()]
        for r in recs:
            rows.append(r[: len(cols)] + [""] * (len(cols) - len(r)))
        return rows

    bom = load("BillOfMaterials", ["id", "asm", "comp", "start", "end", "um", "lvl", "qty", "mod"])
    con.execute("CREATE TABLE bill_of_materials(bom_id INT, assembly_id INT, component_id INT, start_date DATE, end_date DATE, unit VARCHAR, bom_level INT, per_assembly_qty DECIMAL(10,2))")
    con.executemany("INSERT INTO bill_of_materials VALUES (?,?,?,?,?,?,?,?)", [
        [int(r[0]), int(r[1]) if r[1] else None, int(r[2]), r[3][:10], r[4][:10] if r[4] else None, r[5].strip(), int(r[6]), float(r[7])] for r in bom])

    pc = ["ProductID", "Name", "ProductNumber", "MakeFlag", "FinishedGoodsFlag", "Color", "SafetyStockLevel", "ReorderPoint", "StandardCost", "ListPrice", "Size", "SizeUM", "WeightUM", "Weight", "DaysToManufacture", "ProductLine", "Class", "Style", "SubcatID", "ModelID", "SellStart", "SellEnd", "Disc", "guid", "mod"]
    prod = load("Product", pc)
    con.execute("CREATE TABLE product(product_id INT, name VARCHAR, product_number VARCHAR, is_finished_good BOOLEAN, color VARCHAR, standard_cost DOUBLE, list_price DOUBLE, product_line VARCHAR, subcategory_id INT)")
    con.executemany("INSERT INTO product VALUES (?,?,?,?,?,?,?,?,?)", [
        [int(r[0]), r[1], r[2], r[4] == "1", r[5] or None, float(r[8] or 0), float(r[9] or 0), r[15].strip() or None, int(r[18]) if r[18] else None] for r in prod])

    sub = load("ProductSubcategory", ["id", "cat", "name", "g", "m"])
    con.execute("CREATE TABLE product_subcategory(subcategory_id INT, category_id INT, name VARCHAR)")
    con.executemany("INSERT INTO product_subcategory VALUES (?,?,?)", [[int(r[0]), int(r[1]), r[2]] for r in sub])
    cat = load("ProductCategory", ["id", "name", "g", "m"])
    con.execute("CREATE TABLE product_category(category_id INT, name VARCHAR)")
    con.executemany("INSERT INTO product_category VALUES (?,?)", [[int(r[0]), r[1]] for r in cat])

    hdr = load("SalesOrderHeader", ["SalesOrderID", "Rev", "OrderDate", "DueDate", "ShipDate", "Status", "Online", "No", "PO", "Acct", "CustomerID", "SP", "TerritoryID", "B", "S", "SM", "CC", "CCA", "CR", "SubTotal", "Tax", "Freight", "TotalDue", "C", "g", "m"])
    con.execute("CREATE TABLE sales_order(order_id INT, order_date DATE, online_order BOOLEAN, customer_id INT, territory_id INT, status INT, total_due DOUBLE)")
    con.executemany("INSERT INTO sales_order VALUES (?,?,?,?,?,?,?)", [[int(r[0]), r[2][:10], r[6] == "1", int(r[10]), int(r[12]), int(r[5]), float(r[22])] for r in hdr])

    det = load("SalesOrderDetail", ["SalesOrderID", "DetailID", "Track", "Qty", "ProductID", "Offer", "Price", "Disc", "LineTotal", "g", "m"])
    con.execute("CREATE TABLE sales_order_line(order_id INT, line_id INT, product_id INT, order_qty INT, unit_price DOUBLE, unit_discount DOUBLE, line_total DOUBLE)")
    con.executemany("INSERT INTO sales_order_line VALUES (?,?,?,?,?,?,?)", [[int(r[0]), int(r[1]), int(r[4]), int(r[3]), float(r[6]), float(r[7]), float(r[8])] for r in det])

    cust = load("Customer", ["CustomerID", "PersonID", "StoreID", "TerritoryID", "Acct", "g", "m"])
    con.execute("CREATE TABLE customer(customer_id INT, store_id INT, territory_id INT)")
    con.executemany("INSERT INTO customer VALUES (?,?,?)", [[int(r[0]), int(r[2]) if r[2] else None, int(r[3]) if r[3] else None] for r in cust])

    stores = load("Store", ["id", "name", "sp", "demo", "g", "m"], delim="+|")
    con.execute("CREATE TABLE store(store_id INT, name VARCHAR)")
    con.executemany("INSERT INTO store VALUES (?,?)", [[int(r[0].strip()), r[1]] for r in stores if r[0].strip().isdigit()])
    con.close()


def build_lego(path):
    con = duckdb.connect(path)
    d = os.path.join(RAW, "rebrickable")
    keep = {
        "themes": "id,name,parent_id",
        "colors": "id,name",
        "part_categories": "id,name",
        "parts": "part_num,name,part_cat_id",
        "sets": "set_num,name,year,theme_id,num_parts",
        "minifigs": "fig_num,name,num_parts",
        "inventories": "id,version,set_num",
        "inventory_parts": "inventory_id,part_num,color_id,quantity,is_spare",
        "inventory_sets": "inventory_id,set_num,quantity",
        "inventory_minifigs": "inventory_id,fig_num,quantity",
    }
    for t, cols in keep.items():
        con.execute(f"CREATE TABLE {t} AS SELECT {cols} FROM read_csv('{d}/{t}.csv.gz', header=true, all_varchar=false, sample_size=-1, quote='\"')")
    con.close()


def build_xb(path, seed=20261102, scale=1.0):
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
        # Three assembly tiers (j % 3): a tier-t assembly may contain only tier t+1 assemblies, which bounds depth like a real product structure.
        tier = j % 3
        if tier < 2 and rng.random() < 0.7:
            for _ in range(rng.randint(1, 2)):
                k = rng.randint(0, (n_asm - 1) // 3) * 3 + tier + 1
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
    tmp = os.path.join(DBS, f"_ol_{os.getpid()}.csv")
    with open(tmp, "w", newline="") as f:
        csv.writer(f).writerows(ols)
    con.execute(f"INSERT INTO order_line SELECT * FROM read_csv('{tmp}', header=false)")
    os.remove(tmp)
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="aw,lego,xb")
    ap.add_argument("--raw", default=RAW)
    ap.add_argument("--out", default=DBS)
    args = ap.parse_args()
    RAW, DBS = args.raw, args.out
    os.makedirs(DBS, exist_ok=True)
    for name, fn in (("aw", build_aw), ("lego", build_lego), ("xb", build_xb)):
        if name in args.only.split(","):
            p = os.path.join(DBS, name + "_src.duckdb")
            if os.path.exists(p):
                os.remove(p)
            fn(p)
            con = duckdb.connect(p, read_only=True)
            for (t,) in con.execute("SHOW TABLES").fetchall():
                print(name, t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            con.close()
