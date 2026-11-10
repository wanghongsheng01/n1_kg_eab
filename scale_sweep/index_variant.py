"""Variant: the same where-used / explode lookups on a COPY of xb_x.duckdb with ART indexes on u_explosion
(item_kind, item_code) and (root_kind, root_code). The copy lives in scale_sweep/work/<tag>/xb_x_idx.duckdb.
Finding (DuckDB 1.4.5): EXPLAIN shows a SEQ_SCAN for both lookups even with these indexes (also with a single-column
ART on item_code, probed in work/scale10_tiers3/_probe.duckdb), so the indexed copy gives no speed-up; kept as a record.
Usage: python index_variant.py --tag scale10_tiers3   (after sweep.py for that tag)
"""
import argparse, json, os, shutil, time
import duckdb
import sweep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args()
    work = os.path.join(sweep.HERE, "work", a.tag)
    src, dst = os.path.join(work, "xb_x.duckdb"), os.path.join(work, "xb_x_idx.duckdb")
    for x in (dst, dst + ".wal"):
        if os.path.exists(x):
            os.remove(x)
    shutil.copyfile(src, dst)
    con = duckdb.connect(dst)
    t0 = time.time()
    con.execute("CREATE INDEX ux_item ON u_explosion(item_kind, item_code)")
    con.execute("CREATE INDEX ux_root ON u_explosion(root_kind, root_code)")
    con.execute("CHECKPOINT")
    t_idx = time.time() - t0
    con.close()
    R = json.load(open(sweep.RESULTS))
    r = R[a.tag]
    q = sweep.measure(dst, a.n, "2026-10-01", r["seed"], a.reps, a.tag + "+idx")
    r["index_variant"] = dict(index_build_s=round(t_idx, 2), db_bytes=os.path.getsize(dst), queries=q,
                              note="copy of xb_x.duckdb with ART indexes on u_explosion(item_kind,item_code) and (root_kind,root_code); DuckDB 1.4.5 still plans SEQ_SCAN for both lookups (EXPLAIN), so no speed-up")
    R = json.load(open(sweep.RESULTS))
    R[a.tag] = r
    json.dump(R, open(sweep.RESULTS, "w"), indent=1, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
