"""Print the summary table (Markdown) of scale_sweep/results.json. Usage: python make_table.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    R = json.load(open(os.path.join(HERE, "results.json")))
    keys = sorted([k for k in R if k.startswith("scale")], key=lambda k: (R[k]["tiers"], R[k]["scale"]))
    print("| scale | tiers | items | edges | depth | T rows | rows/edge | closure build s (compute / total) | DB MB w/o T | DB MB with T "
          "| explode rec p50/p95 ms | explode lookup p50/p95 ms | explode mism. | where-used rec p50/p95 ms | where-used lookup p50/p95 ms | wu mism. |")
    print("|" + "---|" * 16)
    for k in keys:
        r = R[k]
        s, c, q = r["source"], r["closure"], r["queries"]
        e, w = q["explode"], q["where_used"]
        mb = lambda b: f"{b / 2 ** 20:.1f}"
        pp = lambda d: f"{d['p50_ms']:.1f} / {d['p95_ms']:.1f}"
        print(f"| {r['scale']:g} | {r['tiers']} | {s['n_items']:,} | {s['n_composition_edges']:,} | {s['max_depth_any_date']} | "
              f"{c['u_explosion_rows']:,} | {c['rows_per_composition_edge']} | {c['closure_compute_s']} / {c['closure_total_build_s']} | "
              f"{mb(c['db_bytes_without_u_explosion'])} | {mb(c['db_bytes_with_u_explosion'])} | {pp(e['recursive'])} | {pp(e['lookup'])} | "
              f"{e['mismatches']} | {pp(w['recursive'])} | {pp(w['lookup'])} | {w['mismatches']} |")


if __name__ == "__main__":
    main()
