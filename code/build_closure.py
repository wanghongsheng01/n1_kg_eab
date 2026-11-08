"""Build the closure substrates sql_x / cypher_x: the SQL-U database (resp. the Graph-U Kuzu graph) plus ONE materialized
total-requirements relation u_explosion (resp. relationship EXPLODES).

In MRP terms u_explosion is Vazsonyi's Gozinto total-requirements matrix T = A + A^2 + ... = (I - A)^-1 - I, where A is
the direct-usage matrix of u_composition. With validity intervals on the edges, A(t) is piecewise constant between the
edge breakpoints, so T(t) is too; every (root, item) pair is stored as maximal intervals of constant qty.

  xb   : u_explosion(root_kind, root_code, item_kind, item_code, qty DOUBLE, valid_from DATE, valid_to DATE)
  aw   : u_explosion(root_code, item_code, qty DOUBLE, valid_from DATE, valid_to DATE)        (one node kind: product)
  lego : u_explosion(root_kind, root_num, root_version, is_current, item_kind, item_num, color_id, is_spare, qty BIGINT)
         one row group per (root, inventory version of the ROOT); nested sets / minifigs always contribute their own
         current (highest-version) inventory, exactly as gold.py; part items keep (part_num, color_id) and the is_spare
         flag of the inventory line that contributes them, so "spares excluded" is a filter and "including spares" a sum.

Semantics (identical to each dataset's gold.py): qty = total quantity of item per ONE unit of root, multiplied along each
path and summed over all paths; for xb/aw it holds for every date d with valid_from <= d and (valid_to IS NULL or
d <= valid_to); adjacent intervals with equal qty are merged; every root that has descendants and every descendant
(assemblies and leaves) is included; a root is not its own item.

The sql closure is computed from <ds>_u.duckdb (u_composition, code-keyed); the Kuzu closure from the CONTAINS edges of
<ds>_u.kuzu (keyed by the graph's node primary keys; for xb these are ids, which differ from codes for the 7 duplicated
sku_code values), so each substrate is closed over its own composition relation.

Usage: python build_closure.py --ds xb|aw|lego [--no-kuzu]
Writes data/<ds>_x.duckdb, data/<ds>_x.kuzu and updates results/closure_build.json.
"""
import argparse, datetime as dt, json, os, shutil, sys, time
from collections import defaultdict
import duckdb, kuzu
import pandas as pd

CODE = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(CODE)
DATA = os.path.join(HERE, "data")
ONE = dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------------------------------------
# closure engines
def dated_closure(edges):
    """edges: iterable of (parent, child, qty, valid_from, valid_to) over hashable node keys.
    Returns a sorted list of (root, item, qty, valid_from, valid_to) with maximal constant-qty intervals."""
    out = defaultdict(list)
    for p, c, q, a, b in edges:
        out[p].append((c, float(q), a, b))
    for p in out:
        out[p].sort(key=lambda e: (str(e[0]), e[2], e[3] or dt.date.max, e[1]))
    bp_memo = {}

    def bps(n):
        """Sorted change points of the explosion of n: every valid_from and valid_to + 1 day in n's reachable subgraph."""
        if n in bp_memo:
            return bp_memo[n]
        s = set()
        for c, q, a, b in out.get(n, ()):
            s.add(a)
            if b is not None:
                s.add(b + ONE)
            s |= set(bps(c))
        bp_memo[n] = r = sorted(s)
        return r

    import bisect
    ex_memo = {}

    def explode(n, d):
        seg = bps(n)
        i = bisect.bisect_right(seg, d) - 1
        if i < 0:
            return {}
        key = (n, seg[i])                       # explosion of n is constant on [seg[i], seg[i+1])
        if key in ex_memo:
            return ex_memo[key]
        acc = defaultdict(float)
        for c, q, a, b in out.get(n, ()):
            if a <= d and (b is None or d <= b):
                acc[c] += q
                for cc, qq in explode(c, d).items():
                    acc[cc] += q * qq
        ex_memo[key] = r = dict(acc)
        return r

    rows = []
    for root in sorted(out, key=str):
        seg = bps(root)
        run = {}                                # item -> [start, qty]
        for i, s in enumerate(seg):
            ex = explode(root, s)
            for it in [it for it in run if it not in ex or ex[it] != run[it][1]]:
                st, q = run.pop(it)
                rows.append((root, it, q, st, s - ONE))
            for it, q in ex.items():
                if q != 0 and it not in run:
                    run[it] = [s, q]
        for it, (st, q) in run.items():
            rows.append((root, it, q, st, None))
    rows.sort(key=lambda r: (str(r[0]), str(r[1]), r[3]))
    return rows


def version_closure(edges):
    """LEGO. edges: (parent, version, child, color_id, is_spare, qty); parent/child are (kind, num).
    Returns (root, version, is_current, item, color_id, is_spare, qty) rows, item = (kind, num)."""
    inv = defaultdict(lambda: defaultdict(list))
    for p, v, c, col, sp, q in edges:
        inv[p][v].append((c, col, bool(sp), int(q)))
    cur = {p: max(vs) for p, vs in inv.items()}
    memo = {}

    def explode(n, v):
        key = (n, v)
        if key in memo:
            return memo[key]
        acc = defaultdict(int)
        for c, col, sp, q in inv[n][v]:
            if c[0] == "part":
                acc[(c, col, sp)] += q
            else:
                acc[(c, None, False)] += q
                if c in inv:
                    for k, qq in explode(c, cur[c]).items():
                        acc[k] += q * qq
        memo[key] = r = dict(acc)
        return r

    rows = []
    for p in sorted(inv):
        for v in sorted(inv[p]):
            for (c, col, sp), q in sorted(explode(p, v).items(), key=lambda kv: (kv[0][0], -1 if kv[0][1] is None else kv[0][1], kv[0][2])):
                if q != 0:
                    rows.append((p, v, v == cur[p], c, col, sp, q))
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# edge loaders
def sql_edges(ds, con):
    if ds == "xb":
        return [((pk, pc), (ck, cc), q, a, b) for pk, pc, ck, cc, q, a, b in con.execute(
            "SELECT parent_kind, parent_code, child_kind, child_code, qty, valid_from, valid_to FROM u_composition").fetchall()]
    if ds == "aw":
        return con.execute("SELECT parent_code, child_code, qty, valid_from, valid_to FROM u_composition").fetchall()
    return [((pk, pn), v, (ck, cn), col, sp, q) for pk, pn, v, ck, cn, col, q, sp in con.execute(
        "SELECT parent_kind, parent_num, version, child_kind, child_num, color_id, qty, is_spare FROM u_composition").fetchall()]


KZ = {  # dataset -> (node label -> primary key), CONTAINS label pairs
    "xb": ({"SKU": "sku_id", "Model": "model_id", "Component": "component_id"},
           [("SKU", "SKU"), ("SKU", "Model"), ("Model", "Component"), ("Component", "Component")]),
    "aw": ({"Product": "product_id"}, [("Product", "Product")]),
    "lego": ({"Set": "set_num", "Minifig": "fig_num", "Part": "part_num"},
             [("Set", "Set"), ("Set", "Minifig"), ("Set", "Part"), ("Minifig", "Part")]),
}


def kuzu_edges(ds, k):
    pk, pairs = KZ[ds]
    edges = []
    for a, b in pairs:
        if ds == "lego":
            df = k.execute(f"MATCH (x:{a})-[r:CONTAINS]->(y:{b}) RETURN x.{pk[a]}, r.version, y.{pk[b]}, r.color_id, r.is_spare, r.qty").get_as_df()
            for xp, v, yp, col, sp, q in df.itertuples(index=False, name=None):
                edges.append(((a, xp), int(v), ("part" if b == "Part" else b, yp), None if pd.isna(col) else int(col), bool(sp), int(q)))
        else:
            df = k.execute(f"MATCH (x:{a})-[r:CONTAINS]->(y:{b}) RETURN x.{pk[a]}, y.{pk[b]}, r.qty, r.valid_from, r.valid_to").get_as_df()
            for xp, yp, q, vf, vt in df.itertuples(index=False, name=None):
                vf = pd.Timestamp(vf).date()
                vt = None if pd.isna(vt) else pd.Timestamp(vt).date()
                edges.append(((a, int(xp)), (b, int(yp)), q, vf, vt))
    return edges


# ---------------------------------------------------------------------------------------------------------------------
def _rm(p):
    for x in (p, p + ".wal", p + ".tmp"):
        if os.path.isdir(x):
            shutil.rmtree(x)
        elif os.path.exists(x):
            os.remove(x)


def frame_sql(ds, rows):
    if ds == "xb":
        return pd.DataFrame([(rt[0], rt[1], it[0], it[1], q, a, b) for rt, it, q, a, b in rows],
                            columns=["root_kind", "root_code", "item_kind", "item_code", "qty", "valid_from", "valid_to"])
    if ds == "aw":
        return pd.DataFrame(rows, columns=["root_code", "item_code", "qty", "valid_from", "valid_to"])
    # lego u_composition kinds are lower case ('set', 'minifig', 'part')
    return pd.DataFrame([(p[0], p[1], v, cur, c[0], c[1], col, sp, q) for p, v, cur, c, col, sp, q in rows],
                        columns=["root_kind", "root_num", "root_version", "is_current", "item_kind", "item_num", "color_id", "is_spare", "qty"])


SQL_DDL = {
    "xb": "CREATE TABLE u_explosion(root_kind VARCHAR, root_code VARCHAR, item_kind VARCHAR, item_code VARCHAR, qty DOUBLE, valid_from DATE, valid_to DATE)",
    "aw": "CREATE TABLE u_explosion(root_code VARCHAR, item_code VARCHAR, qty DOUBLE, valid_from DATE, valid_to DATE)",
    "lego": "CREATE TABLE u_explosion(root_kind VARCHAR, root_num VARCHAR, root_version INT, is_current BOOLEAN, item_kind VARCHAR, "
            "item_num VARCHAR, color_id INT, is_spare BOOLEAN, qty BIGINT)",
}


def build_sql(ds, rep):
    src, dst = os.path.join(DATA, f"{ds}_u.duckdb"), os.path.join(DATA, f"{ds}_x.duckdb")
    t0 = time.time()
    con = duckdb.connect(src, read_only=True)
    edges = sql_edges(ds, con)
    con.close()
    rows = version_closure(edges) if ds == "lego" else dated_closure(edges)
    t_closure = time.time() - t0
    _rm(dst)
    shutil.copyfile(src, dst)
    con = duckdb.connect(dst)
    con.execute("SET max_temp_directory_size = '2GB'")
    con.execute("SET threads = 1")         # insertion order (and so table order) stays deterministic
    df = frame_sql(ds, rows)
    con.execute(SQL_DDL[ds])
    con.register("df", df)
    con.execute("INSERT INTO u_explosion SELECT * FROM df")
    con.unregister("df")
    con.execute("CHECKPOINT")
    n = con.execute("SELECT count(*) FROM u_explosion").fetchone()[0]
    rk = "root_code" if ds == "aw" else ("root_kind, root_num" if ds == "lego" else "root_kind, root_code")
    stats = dict(roots=con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {rk} FROM u_explosion)").fetchone()[0],
                 rows_per_composition_edge=round(n / len(edges), 2))
    if ds == "lego":
        stats["rows_by_kind"] = {f"{a}->{b}": c for a, b, c in con.execute(
            "SELECT root_kind, item_kind, count(*) FROM u_explosion GROUP BY ALL ORDER BY ALL").fetchall()}
        stats["root_versions"] = con.execute("SELECT count(*) FROM (SELECT DISTINCT root_kind, root_num, root_version FROM u_explosion)").fetchone()[0]
        stats["rows_not_current"] = con.execute("SELECT count(*) FROM u_explosion WHERE NOT is_current").fetchone()[0]
        stats["rows_spare"] = con.execute("SELECT count(*) FROM u_explosion WHERE is_spare").fetchone()[0]
    else:
        if ds == "xb":
            stats["rows_by_kind"] = {f"{a}->{b}": c for a, b, c in con.execute(
                "SELECT root_kind, item_kind, count(*) FROM u_explosion GROUP BY ALL ORDER BY ALL").fetchall()}
        stats["rows_closed_interval"] = con.execute("SELECT count(*) FROM u_explosion WHERE valid_to IS NOT NULL").fetchone()[0]
        stats["max_intervals_per_pair"] = con.execute(
            f"SELECT max(n) FROM (SELECT count(*) n FROM u_explosion GROUP BY {rk}, {'item_code' if ds == 'aw' else 'item_kind, item_code'})").fetchone()[0]
    con.close()
    rep["sql_x"] = dict(db=os.path.relpath(dst, HERE), rows=n, stats=stats, n_edges_input=len(edges), closure_s=round(t_closure, 2),
                        total_build_s=round(time.time() - t0, 2), file_bytes=os.path.getsize(dst),
                        base_file_bytes=os.path.getsize(src))
    print("sql_x", rep["sql_x"], flush=True)


def build_kuzu(ds, rep):
    src, dst = os.path.join(DATA, f"{ds}_u.kuzu"), os.path.join(DATA, f"{ds}_x.kuzu")
    stage = os.path.join(DATA, f"{ds}_x_stage")
    t0 = time.time()
    db = kuzu.Database(src, read_only=True)
    k = kuzu.Connection(db)
    edges = kuzu_edges(ds, k)
    del k, db
    rows = version_closure(edges) if ds == "lego" else dated_closure(edges)
    t_closure = time.time() - t0
    _rm(dst)
    shutil.copyfile(src, dst)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    pk, _ = KZ[ds]
    groups = defaultdict(list)
    if ds == "lego":
        lab = {"Set": "Set", "Minifig": "Minifig", "part": "Part"}
        for p, v, cur, c, col, sp, q in rows:
            groups[(p[0], lab[c[0]])].append((p[1], c[1], v, cur, q, col, sp))
        cols = ["from", "to", "root_version", "is_current", "qty", "color_id", "is_spare"]
        props = "root_version INT64, is_current BOOLEAN, qty INT64, color_id INT64, is_spare BOOLEAN"
    else:
        for r, it, q, a, b in rows:
            groups[(r[0], it[0])].append((r[1], it[1], q, a, b))
        cols = ["from", "to", "qty", "valid_from", "valid_to"]
        props = "qty DOUBLE, valid_from DATE, valid_to DATE"
    pairs = sorted(groups)
    k = kuzu.Connection(kuzu.Database(dst))
    k.execute(f"CREATE REL TABLE EXPLODES({', '.join(f'FROM {a} TO {b}' for a, b in pairs)}, {props})")
    sc = duckdb.connect()
    for a, b in pairs:
        f = os.path.join(stage, f"{a}_{b}.parquet")
        df = pd.DataFrame(groups[(a, b)], columns=cols)
        sc.register("df", df)
        typ = {"color_id": "BIGINT", "root_version": "BIGINT", "valid_from": "DATE", "valid_to": "DATE"}
        casts = ", ".join(f'CAST("{c}" AS {typ[c]}) AS "{c}"' if c in typ else f'"{c}"' for c in cols)
        sc.execute(f"COPY (SELECT {casts} FROM df) TO '{f}' (FORMAT PARQUET)")
        sc.unregister("df")
        k.execute(f"COPY EXPLODES FROM '{f}'" + (f" (from='{a}', to='{b}')" if len(pairs) > 1 else ""))
    n = k.execute("MATCH ()-[r:EXPLODES]->() RETURN count(r)").get_next()[0]
    del k
    shutil.rmtree(stage)
    rep["cypher_x"] = dict(db=os.path.relpath(dst, HERE), rel_rows=n, rel_pairs=[f"{a}->{b}" for a, b in pairs],
                           n_edges_input=len(edges), closure_s=round(t_closure, 2), total_build_s=round(time.time() - t0, 2),
                           file_bytes=os.path.getsize(dst), base_file_bytes=os.path.getsize(src))
    print("cypher_x", rep["cypher_x"], flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", required=True, choices=["xb", "aw", "lego"])
    ap.add_argument("--no-kuzu", action="store_true")
    a = ap.parse_args()
    free = shutil.disk_usage(DATA).free
    if free < 5 * 2 ** 30:
        sys.exit(f"only {free / 2 ** 30:.1f} GiB free; refusing to build")
    rep = {}
    build_sql(a.ds, rep)
    if not a.no_kuzu:
        build_kuzu(a.ds, rep)
    out = os.path.join(HERE, "results", "closure_build.json")
    allrep = json.load(open(out)) if os.path.exists(out) else {}
    allrep[a.ds] = rep
    with open(out, "w") as f:
        json.dump(allrep, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
