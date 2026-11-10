"""Two reproducibility checks for the sweep (read-only on data/; writes only scale_sweep/work/check/).
  1. scale 1 of the sweep reproduces the benchmark: u_composition of scale_sweep/work/scale1_tiers3/xb_u.duckdb equals
     u_composition of data/xb_u.duckdb (opened READ_ONLY), and u_explosion has the row count in results/closure_build.json.
  2. the local generator copy make_xb_tiers.build_xb(tiers=3) generates the same u_composition as the original generator.
Usage: python check_repro.py   (after `python sweep.py --scale 1`)
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import duckdb
import sweep, make_xb_tiers

PROJ = os.path.dirname(HERE)
Q = "SELECT * FROM u_composition ORDER BY ALL"


def comp(path):
    con = duckdb.connect(path, read_only=True)
    r = con.execute(Q).fetchall()
    con.close()
    return r


def main():
    out = {}
    s1 = os.path.join(HERE, "work", "scale1_tiers3")
    a = comp(os.path.join(s1, "xb_u.duckdb"))
    b = comp(os.path.join(PROJ, "data", "xb_u.duckdb"))
    con = duckdb.connect(os.path.join(s1, "xb_x.duckdb"), read_only=True)
    nx = con.execute("SELECT count(*) FROM u_explosion").fetchone()[0]
    con.close()
    ref = json.load(open(os.path.join(PROJ, "results", "closure_build.json")))["xb"]["sql_x"]["rows"]
    out["scale1_equals_benchmark_u_composition"] = a == b
    out["scale1_u_composition_rows"] = [len(a), len(b)]
    out["scale1_u_explosion_rows_vs_benchmark"] = [nx, ref]

    work = os.path.join(HERE, "work", "check")
    os.makedirs(work, exist_ok=True)
    sweep.redirect(work)
    src, up = os.path.join(work, "xb_src.duckdb"), os.path.join(work, "xb_u.duckdb")
    for p in (src, up):
        if os.path.exists(p):
            os.remove(p)
    make_xb_tiers.build_xb(src, seed=20261102, scale=1.0, tiers=3)
    sweep.build_u(src, up)
    out["tiers_copy_with_3_equals_original"] = comp(up) == a
    print(out)
    res = json.load(open(sweep.RESULTS)) if os.path.exists(sweep.RESULTS) else {}
    res["repro_checks"] = out
    json.dump(res, open(sweep.RESULTS, "w"), indent=1, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
