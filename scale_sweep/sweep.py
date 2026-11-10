"""Model-free scalability sweep of the materialized total-requirements relation u_explosion (substrate sql_x) for XB.

For one (scale, tiers) setting this script
  1. generates the XB source with the UNCHANGED generator code/make_sources.build_xb (tiers = 3) or with the local copy
     scale_sweep/make_xb_tiers.build_xb (tiers != 3; one-line change: number of assembly tiers),
  2. builds xb_u.duckdb (u_composition) with the SQL of code/build_substrates.build_unified (DuckDB part only),
  3. builds xb_x.duckdb (u_composition + u_explosion) by calling code/build_closure.build_sql unchanged,
  4. measures source size, depth, closure size/time and warm query latency (recursive CTE over u_composition vs lookup on
     u_explosion) and checks that both return identical answers.

Nothing under data/, results/ or code/ is written: the module-level path constants of the imported project modules are
redirected to scale_sweep/work/<tag>/ before any builder is called. No LLM/API calls.

Usage:  python sweep.py --scale 1 [--tiers 3] [--seed 20261102] [--n 100] [--date 2026-10-01] [--closure-only | --skip-build]
Writes scale_sweep/work/<tag>/ (databases) and merges the measurements into scale_sweep/results.json under key <tag>.
"""
import argparse, datetime as dt, json, os, platform, random, shutil, statistics, subprocess, sys, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "code"))
sys.path.insert(0, HERE)

import duckdb  # noqa: E402
import make_sources as ms  # noqa: E402
import build_substrates as bs  # noqa: E402
import build_closure as bc  # noqa: E402
import make_xb_tiers  # noqa: E402

RESULTS = os.path.join(HERE, "results.json")


def redirect(work):
    """Point every path constant of the imported project modules into scale_sweep/work/<tag>/."""
    ms.ROOT, ms.RAW, ms.DBS = work, os.path.join(work, "raw"), work
    bs.HERE, bs.DATA = work, work
    bs.SRC, bs.SQL_DB = os.path.join(work, "xb_src.duckdb"), os.path.join(work, "xb.duckdb")
    bs.KG_DB, bs.CSV = os.path.join(work, "xb.kuzu"), os.path.join(work, "kg_csv")
    bc.HERE, bc.DATA = work, work
    for m in (ms, bs, bc):
        for name in ("ROOT", "RAW", "DBS", "HERE", "DATA", "SRC", "SQL_DB", "KG_DB", "CSV"):
            v = getattr(m, name, None)
            if isinstance(v, str):
                assert os.path.abspath(v).startswith(HERE), (m.__name__, name, v)


def build_u(src, upath):
    """The DuckDB half of code/build_substrates.build_unified (verbatim SQL; the Kuzu half is not needed here)."""
    if os.path.exists(upath):
        os.remove(upath)
    con = duckdb.connect(upath)
    con.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog='src' AND table_schema='main'").fetchall():
        con.execute(f"CREATE TABLE main.{t} AS SELECT * FROM src.main.{t}")
    U = bs.U_DATE_MIN
    con.execute(f"""CREATE TABLE u_composition AS
      SELECT 'sku' AS parent_kind, b.sku_code AS parent_code, 'sku' AS child_kind, i.sku_code AS child_code, bi.qty,
             DATE '{U}' AS valid_from, CAST(NULL AS DATE) AS valid_to
        FROM bundle_item bi JOIN sales_sku b ON b.sku_id = bi.bundle_sku_id JOIN sales_sku i ON i.sku_id = bi.item_sku_id
      UNION ALL SELECT 'sku', s.sku_code, 'model', m.model_code, sm.qty, DATE '{U}', NULL
        FROM sku_model sm JOIN sales_sku s ON s.sku_id = sm.sku_id JOIN company_model m ON m.model_id = sm.model_id
      UNION ALL SELECT 'model', m.model_code, 'component', c.component_code, mb.qty, mb.valid_from, mb.valid_to
        FROM model_bom mb JOIN company_model m ON m.model_id = mb.model_id JOIN component c ON c.component_id = mb.component_id
      UNION ALL SELECT 'component', p.component_code, 'component', c.component_code, cb.qty, cb.valid_from, cb.valid_to
        FROM component_bom cb JOIN component p ON p.component_id = cb.parent_component_id JOIN component c ON c.component_id = cb.child_component_id""")
    for t in ("bundle_item", "sku_model", "model_bom", "component_bom"):
        con.execute(f"DROP TABLE {t}")
    con.execute("CHECKPOINT")
    con.close()


def max_depth(edges, as_of=None):
    """Longest root-to-leaf path (in edges) of the composition DAG, optionally restricted to edges valid on `as_of`."""
    out = defaultdict(set)
    for p, c, q, a, b in edges:
        if as_of is None or (a <= as_of and (b is None or as_of <= b)):
            out[p].add(c)
    memo = {}
    sys.setrecursionlimit(100000)

    def h(n):
        if n not in memo:
            memo[n] = 1 + max((h(c) for c in out[n]), default=-1) if out.get(n) else 0
        return memo[n]
    return max(h(n) for n in list(out))


VALID = "{a}.valid_from <= DATE '{d}' AND ({a}.valid_to IS NULL OR DATE '{d}' <= {a}.valid_to)"
LEAF = "item_kind = 'component' AND item_code IN (SELECT component_code FROM component WHERE NOT is_assembly)"

Q_REC = """WITH RECURSIVE r(item_kind, item_code, q) AS (
  SELECT c.child_kind, c.child_code, CAST(c.qty AS DOUBLE) FROM u_composition c
   WHERE c.parent_kind = $kind AND c.parent_code = $code AND {vc}
  UNION ALL
  SELECT c.child_kind, c.child_code, r.q * c.qty FROM r JOIN u_composition c
    ON c.parent_kind = r.item_kind AND c.parent_code = r.item_code AND {vc})
SELECT item_kind, item_code, sum(q) AS qty FROM r WHERE {leaf} GROUP BY item_kind, item_code ORDER BY item_kind, item_code"""

Q_LOOK = """SELECT item_kind, item_code, qty FROM u_explosion x
 WHERE x.root_kind = $kind AND x.root_code = $code AND {vx} AND {leaf} ORDER BY item_kind, item_code"""

Q_WU_REC = """WITH RECURSIVE u(kind, code) AS (
  SELECT c.parent_kind, c.parent_code FROM u_composition c WHERE c.child_kind = 'component' AND c.child_code = $code AND {vc}
  UNION
  SELECT c.parent_kind, c.parent_code FROM u JOIN u_composition c ON c.child_kind = u.kind AND c.child_code = u.code AND {vc})
SELECT kind, code FROM u ORDER BY kind, code"""

Q_WU_LOOK = """SELECT root_kind, root_code FROM u_explosion x
 WHERE x.item_kind = 'component' AND x.item_code = $code AND {vx} ORDER BY root_kind, root_code"""


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def lat(xs):
    return dict(p50_ms=round(statistics.median(xs) * 1e3, 3), p95_ms=round(pct(xs, 0.95) * 1e3, 3),
                mean_ms=round(statistics.mean(xs) * 1e3, 3), max_ms=round(max(xs) * 1e3, 3))


def same(a, b, tol=1e-9):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if len(x) == 3:                       # (item_kind, item_code, qty): keys equal, qty equal up to rounding
            if x[:2] != y[:2] or abs(x[2] - y[2]) > tol * max(1.0, abs(x[2])):
                return False
        elif tuple(x) != tuple(y):            # where-used: (root_kind, root_code)
            return False
    return True


def timed_pair(con, qa, qb, params, reps):
    """Warm-up both queries once, then time `reps` alternating executions each; returns both answers and per-key medians."""
    ra = con.execute(qa, params).fetchall()
    rb = con.execute(qb, params).fetchall()
    ta, tb = [], []
    for _ in range(reps):
        t0 = time.perf_counter(); con.execute(qa, params).fetchall(); ta.append(time.perf_counter() - t0)
        t0 = time.perf_counter(); con.execute(qb, params).fetchall(); tb.append(time.perf_counter() - t0)
    return ra, rb, statistics.median(ta), statistics.median(tb)


def measure(xdb, n, as_of, seed, reps, label):
    con = duckdb.connect(xdb, read_only=True)
    vc, vx = VALID.format(a="c", d=as_of), VALID.format(a="x", d=as_of)
    q_rec, q_look = Q_REC.format(vc=vc, leaf=LEAF), Q_LOOK.format(vx=vx, leaf=LEAF)
    q_wu_rec, q_wu_look = Q_WU_REC.format(vc=vc), Q_WU_LOOK.format(vx=vx)
    rng = random.Random(seed)
    roots = sorted(con.execute("SELECT DISTINCT parent_kind, parent_code FROM u_composition").fetchall())
    leaves = sorted(con.execute("SELECT DISTINCT child_code FROM u_composition WHERE child_kind = 'component' AND child_code IN "
                                "(SELECT component_code FROM component WHERE NOT is_assembly)").fetchall())
    s_roots, s_leaves = rng.sample(roots, n), rng.sample(leaves, n)
    out = {}
    for name, qa, qb, sample, mk in (("explode", q_rec, q_look, s_roots, lambda r: {"kind": r[0], "code": r[1]}),
                                     ("where_used", q_wu_rec, q_wu_look, s_leaves, lambda r: {"code": r[0]})):
        ta, tb, mism, sizes, empty = [], [], [], [], 0
        for r in sample:
            ra, rb, a, b = timed_pair(con, qa, qb, mk(r), reps)
            ta.append(a); tb.append(b); sizes.append(len(ra)); empty += (len(ra) == 0)
            if not same(ra, rb):
                mism.append(dict(key=list(r), n_rec=len(ra), n_lookup=len(rb)))
        out[name] = dict(n=len(sample), recursive=lat(ta), lookup=lat(tb), speedup_p50=round(statistics.median(ta) / statistics.median(tb), 1),
                         mismatches=len(mism), mismatch_examples=mism[:5], answer_rows_mean=round(statistics.mean(sizes), 1),
                         answer_rows_max=max(sizes), empty_answers=empty)
        if name == "explode":
            kinds = defaultdict(int)
            for k, _ in sample:
                kinds[k] += 1
            out[name]["root_kinds"] = dict(kinds)
        print(label, name, {k: v for k, v in out[name].items() if k != "mismatch_examples"}, flush=True)
    out["sample"] = dict(seed=seed, n_candidate_roots=len(roots), n_candidate_leaves=len(leaves), as_of=as_of,
                         timing="per key: 1 warm-up run of each query, then %d alternating timed runs; per-key median; "
                                "p50/p95 over the %d keys" % (reps, n))
    con.close()
    return out


def machine():
    def sh(c):
        try:
            return subprocess.check_output(c, shell=True, text=True).strip()
        except Exception:
            return None
    return dict(cpu=sh("sysctl -n machdep.cpu.brand_string"), mem_bytes=int(sh("sysctl -n hw.memsize") or 0),
                ncpu=os.cpu_count(), os=platform.platform(), python=platform.python_version(), duckdb=duckdb.__version__,
                load_avg_at_start=list(os.getloadavg()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, required=True)
    ap.add_argument("--tiers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--date", default="2026-10-01")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--skip-build", action="store_true", help="reuse work/<tag>/ databases; only re-measure")
    ap.add_argument("--closure-only", action="store_true", help="reuse source and xb_u.duckdb; rebuild u_explosion and re-measure")
    a = ap.parse_args()
    tag = a.tag or f"scale{a.scale:g}_tiers{a.tiers}"
    work = os.path.join(HERE, "work", tag)
    os.makedirs(work, exist_ok=True)
    redirect(work)
    src, upath, xpath = os.path.join(work, "xb_src.duckdb"), os.path.join(work, "xb_u.duckdb"), os.path.join(work, "xb_x.duckdb")
    rep = dict(scale=a.scale, tiers=a.tiers, seed=a.seed, machine=machine(), started=dt.datetime.now().isoformat(timespec="seconds"),
               generator="code/make_sources.build_xb (unchanged)" if a.tiers == 3 else "scale_sweep/make_xb_tiers.build_xb(tiers=%d)" % a.tiers)
    prev = (json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}).get(tag, {})
    if a.closure_only:
        for k in ("source_build_s", "u_build_s"):
            if k in prev:
                rep[k] = prev[k]
        cl = {}
        bc.build_sql("xb", cl)
        rep["closure_build"] = cl["sql_x"]
        rep["closure_build_first_run"] = prev.get("closure_build_first_run", prev.get("closure_build"))
    elif not a.skip_build:
        for p in (src, upath, xpath):
            for x in (p, p + ".wal"):
                if os.path.exists(x):
                    os.remove(x)
        t0 = time.time()
        if a.tiers == 3:
            ms.build_xb(src, seed=a.seed, scale=a.scale)
        else:
            make_xb_tiers.build_xb(src, seed=a.seed, scale=a.scale, tiers=a.tiers)
        rep["source_build_s"] = round(time.time() - t0, 2)
        t0 = time.time()
        build_u(src, upath)
        rep["u_build_s"] = round(time.time() - t0, 2)
        print(tag, "source", rep["source_build_s"], "s; u", rep["u_build_s"], "s", flush=True)
        cl = {}
        bc.build_sql("xb", cl)            # reads work/xb_u.duckdb, writes work/xb_x.duckdb (paths redirected above)
        rep["closure_build"] = cl["sql_x"]
    else:
        for k in ("source_build_s", "u_build_s", "closure_build", "closure_build_first_run"):
            if k in prev:
                rep[k] = prev[k]

    con = duckdb.connect(xpath, read_only=True)
    edges = con.execute("SELECT parent_kind || ':' || parent_code, child_kind || ':' || child_code, qty, valid_from, valid_to FROM u_composition").fetchall()
    as_of = dt.date.fromisoformat(a.date)
    src_stats = dict(
        n_items=con.execute("SELECT count(*) FROM (SELECT parent_kind k, parent_code c FROM u_composition UNION "
                            "SELECT child_kind, child_code FROM u_composition)").fetchone()[0],
        n_components=con.execute("SELECT count(*) FROM component").fetchone()[0],
        n_assemblies=con.execute("SELECT count(*) FROM component WHERE is_assembly").fetchone()[0],
        n_models=con.execute("SELECT count(*) FROM company_model").fetchone()[0],
        n_skus=con.execute("SELECT count(*) FROM sales_sku").fetchone()[0],
        n_composition_edges=len(edges),
        edges_by_kind={f"{p}->{c}": n for p, c, n in con.execute(
            "SELECT parent_kind, child_kind, count(*) FROM u_composition GROUP BY ALL ORDER BY ALL").fetchall()},
        max_depth_any_date=max_depth(edges),
        max_depth_as_of=max_depth(edges, as_of),
        src_db_bytes=os.path.getsize(src),
        u_db_bytes=os.path.getsize(upath),
    )
    n_x = con.execute("SELECT count(*) FROM u_explosion").fetchone()[0]
    con.close()
    rep["source"] = src_stats
    cb = rep.get("closure_build", {})
    rep["closure"] = dict(u_explosion_rows=n_x, rows_per_composition_edge=round(n_x / len(edges), 2),
                          closure_compute_s=cb.get("closure_s"), closure_total_build_s=cb.get("total_build_s"),
                          db_bytes_without_u_explosion=os.path.getsize(upath), db_bytes_with_u_explosion=os.path.getsize(xpath),
                          u_explosion_bytes_delta=os.path.getsize(xpath) - os.path.getsize(upath))
    print(tag, "source", src_stats, flush=True)
    print(tag, "closure", rep["closure"], flush=True)
    rep["queries"] = measure(xpath, a.n, a.date, a.seed, a.reps, tag)
    rep["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    rep["load_avg_at_end"] = list(os.getloadavg())
    import resource
    rep["peak_rss_bytes_process"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
    allrep = json.load(open(RESULTS)) if os.path.exists(RESULTS) else {}
    allrep[tag] = rep
    with open(RESULTS, "w") as f:
        json.dump(allrep, f, indent=1, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
