"""Instance-robustness check (no LLM calls).

1. Build K perturbed instances of xb that keep every key and code but resample quantities, costs, prices, order
   statuses and BOM version boundaries (data/xb_p{k}*.duckdb / *.kuzu).
2. Recompute each question's gold on each instance with gold.World (parameters parsed from the question text).
3. Re-execute the final query of every run on each instance; a run is "instance-robust" if it is correct on the
   original and on all perturbed instances.

Usage: python perturb.py build      # once
       python perturb.py check      # after runs; writes results/robust.jsonl
"""
import glob, json, os, random, re, sys, datetime as dt
from collections import defaultdict
import duckdb
import build_substrates as B
from gold import World, TODAY

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
K = 2


def build(k):
    rng = random.Random(1000 + k)
    src = os.path.join(DATA, f"xb_p{k}_src.duckdb")
    if os.path.exists(src):
        os.remove(src)
    con = duckdb.connect(src)
    con.execute(f"ATTACH '{os.path.join(DATA, 'xb_src.duckdb')}' AS o (READ_ONLY)")
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog='o' AND table_schema='main'").fetchall():
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM o.main.{t}")
    con.execute("DETACH o")

    def rewrite(table, cols, fn):
        rows = con.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        rows = fn(rows)
        con.execute(f"DELETE FROM {table}")
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)

    def bom(rows):
        groups = defaultdict(list)
        for r in rows:
            groups[(r[0], r[1])].append(list(r))
        out = []
        for key, vs in groups.items():
            vs.sort(key=lambda v: v[3])
            for v in vs:
                if rng.random() < 0.5:
                    v[2] = rng.randint(1, 4)
            for a, b in zip(vs, vs[1:]):      # shift the boundary between consecutive versions
                lo = max(a[3] + dt.timedelta(days=1), b[3] - dt.timedelta(days=90))
                hi = b[4] - dt.timedelta(days=1) if b[4] else b[3] + dt.timedelta(days=90)
                if lo < hi:
                    nb = lo + dt.timedelta(days=rng.randrange((hi - lo).days + 1))
                    a[4], b[3] = nb - dt.timedelta(days=1), nb
            out += [tuple(v) for v in vs]
        return out

    rewrite("component_bom", ["parent_component_id", "child_component_id", "qty", "valid_from", "valid_to"], bom)
    rewrite("model_bom", ["model_id", "component_id", "qty", "valid_from", "valid_to"], bom)
    flip = lambda p: (lambda rows: [tuple(r[:-1]) + ((3 - r[-1]) if rng.random() < p else r[-1],) for r in rows])
    rewrite("sku_model", ["sku_id", "model_id", "qty"], flip(0.2))
    rewrite("bundle_item", ["bundle_sku_id", "item_sku_id", "qty"], flip(0.3))
    rewrite("component", ["component_id", "component_code", "name", "category", "is_assembly", "unit_cost"],
            lambda rows: [r[:5] + (round(r[5] * rng.uniform(0.8, 1.2), 2) if rng.random() < 0.3 else r[5],) for r in rows])
    rewrite("orders", ["order_id", "store_id", "order_date", "status"],
            lambda rows: [r[:3] + ((("cancelled" if r[3] == "completed" else "completed") if rng.random() < 0.05 else r[3]),) for r in rows])
    rewrite("order_line", ["order_id", "line_no", "listing_id", "qty", "unit_price"],
            lambda rows: [r[:3] + (rng.randint(1, 3) if rng.random() < 0.3 else r[3],
                                   round(r[4] * rng.uniform(0.9, 1.1), 2) if rng.random() < 0.3 else r[4]) for r in rows])
    con.close()
    B.configure(src, f"_p{k}")
    B.build_sql()
    B.build_kg()
    B.build_unified()


def codes(t):
    return re.findall(r"\b[A-Z]{2,3}\d?-[A-Z0-9-]+\b", t)


def gold_for(q, W):
    t, tpl = q["text"], q["tpl"]
    cs = codes(t)
    d = re.findall(r"\d{4}-\d{2}-\d{2}", t)
    d = dt.date.fromisoformat(d[0]) if d else TODAY
    yr = int((re.findall(r"in (20\d\d)", t) or ["0"])[-1])
    mid = lambda c: W.model_by_code[c]
    cid = lambda c: W.comp_by_code[c]
    sid = lambda c: W.sku_by_code[c]
    cost = lambda ex: round(sum(qq * W.comp[c]["cost"] for c, qq in W.parts(ex).items()), 2)
    users = lambda c, d=TODAY: [m for m in W.model if c in W.explode_model(m, d)]

    def model_units(pred):
        out = defaultdict(float)
        for o, st, ln in W.completed_lines(pred):
            for m, qq in W.sku_models(W.listing[ln["listing"]]["sku"]).items():
                out[m] += ln["qty"] * qq
        return out

    if tpl == "L1a":
        return [W.model[m]["code"] for m, _ in W.sku_model[sid(cs[0])]]
    if tpl == "L1b":
        return float(sum(1 for l in W.listing.values() if l["sku"] == sid(cs[0])))
    if tpl == "L1c":
        return sorted(W.comp[c]["code"] for c in W.explode_model(mid(cs[0]), TODAY, 1))
    if tpl in ("E1", "V1"):
        return float(W.explode_model(mid(cs[1]), d).get(cid(cs[0]), 0))
    if tpl == "E4":
        return float(W.explode_sku(sid(cs[1]), d).get(cid(cs[0]), 0))
    if tpl == "E2":
        return float(len(W.parts(W.explode_model(mid(cs[0])))))
    if tpl in ("E3", "V3"):
        return cost(W.explode_model(mid(cs[0]), d))
    if tpl == "V4":
        return float(sum(W.parts(W.explode_model(mid(cs[0]), d)).values()))
    if tpl == "V2":
        a, b = W.explode_model(mid(cs[0]), d, 1), W.explode_model(mid(cs[0]), TODAY, 1)
        return sorted(W.comp[c]["code"] for c in set(a) | set(b) if a.get(c, 0) != b.get(c, 0))
    if tpl == "W1":
        return float(len(users(cid(cs[0]))))
    if tpl == "W2":
        return [W.model[m]["code"] for m in users(cid(cs[0]))]
    if tpl == "W3":
        u = set(users(cid(cs[0])))
        return float(sum(1 for s in W.sku if not W.sku[s]["bundle"] and any(m in u for m, _ in W.sku_model[s])))
    if tpl == "W4":
        return float(sum(1 for b in W.sku if W.sku[b]["bundle"] and mid(cs[0]) in W.sku_models(b)))
    if tpl == "A1":
        return float(model_units(lambda o, st, ln: o["date"].year == yr).get(mid(cs[0]), 0))
    if tpl == "A2":
        plat = re.search(r"platform (\w+)", t).group(1)
        qq, y = map(int, re.search(r"Q(\d) (\d{4})", t).groups())
        return round(sum(ln["qty"] * ln["price"] for o, st, ln in W.completed_lines(
            lambda o, st, ln: st["platform"] == plat and o["date"].year == y and (o["date"].month - 1) // 3 + 1 == qq)), 2)
    if tpl == "A3":
        store = re.search(r"of store (\S+) in", t).group(1)
        tot = 0.0
        for o, st, ln in W.completed_lines(lambda o, st, ln: st["name"] == store and o["date"].year == yr):
            tot += ln["qty"] * W.explode_sku(W.listing[ln["listing"]]["sku"], o["date"]).get(cid(cs[0]), 0)
        return tot
    if tpl == "A4":
        ctry = re.search(r"country (\w+)", t).group(1)
        u = sorted(model_units(lambda o, st, ln: st["country"] == ctry and o["date"].year == yr).items(), key=lambda kv: -kv[1])
        return {"top": [W.model[m]["code"] for m, _ in u[:3]], "tie": len(u) > 3 and u[2][1] == u[3][1]}
    raise ValueError(tpl)


def check():
    import run_pilot as RP
    from semantic import compile_query
    from score import match
    qs = {json.loads(l)["qid"]: json.loads(l) for l in open(os.path.join(DATA, os.environ.get("N1_QFILE", "questions.jsonl")))}
    # sanity: recomputed gold on the original instance equals the stored gold
    W0 = World(os.path.join(DATA, "xb.duckdb"))
    for q in qs.values():
        g = gold_for(q, W0)
        g = g["top"] if isinstance(g, dict) else g
        assert (abs(g - q["gold"]) < 0.01) if q["gold_type"] == "scalar" else (sorted(g) == sorted(q["gold"])), q["qid"]
    golds = {}
    for k in range(1, K + 1):
        Wk = World(os.path.join(DATA, f"xb_p{k}.duckdb"))
        golds[k] = {qid: gold_for(q, Wk) for qid, q in qs.items()}
    sql = {k: {"sql": duckdb.connect(os.path.join(DATA, f"xb_p{k}.duckdb"), read_only=True),
               "sql_u": duckdb.connect(os.path.join(DATA, f"xb_p{k}_u.duckdb"), read_only=True)} for k in golds}
    rpath = os.path.join(HERE, "results", os.environ.get("N1_PERTURB_OUT", "robust.jsonl"))
    seen = set()
    if os.path.exists(rpath):
        for l in open(rpath):
            d = json.loads(l)
            seen.add((d["model"], d["substrate"], d["qid"], d["rep"]))
    out = open(rpath, "a")
    lang = {"sql": "sql", "sql_u": "sql", "cypher": "cypher", "cypher_u": "cypher", "semantic": "json", "classify": "json"}
    files = sorted(glob.glob(os.path.join(HERE, "results", os.environ.get("N1_PERTURB_RUNS", "runs*.jsonl"))))
    superseded = any(f.endswith("_m2.jsonl") for f in files)   # m2 re-executes Metric/Metric-R
    for f in files:
        for l in open(f):
            r = json.loads(l)
            if (r["model"], r["substrate"], r["qid"], r["rep"]) in seen or r["substrate"] not in lang:
                continue
            if superseded and not f.endswith("_m2.jsonl") and r["substrate"] in ("semantic", "classify"):
                continue
            rec = dict(model=r["model"], substrate=r["substrate"], qid=r["qid"], rep=r["rep"], orig=r["correct"], pert=[])
            if r["attempts"][-1]["err"] is None:
                text = RP.extract(r["attempts"][-1]["text"], lang[r["substrate"]])
                for k in golds:
                    g = golds[k][r["qid"]]
                    tie = isinstance(g, dict) and g["tie"]
                    g = g["top"] if isinstance(g, dict) else g
                    q = dict(qs[r["qid"]], gold=g)
                    try:
                        s = r["substrate"]
                        if s in ("sql", "sql_u"):
                            rows = sql[k][s].execute(text).fetchmany(1000)
                        elif s in ("semantic", "classify"):
                            rows = sql[k]["sql"].execute(compile_query(json.loads(text))).fetchmany(1000)
                        else:
                            rows = RP.cypher_subprocess(os.path.join(DATA, f"xb_p{k}{'' if s == 'cypher' else '_u'}.kuzu"), text)
                        rec["pert"].append(None if tie else bool(match(rows, q)))
                    except Exception as e:
                        rec["pert"].append(False)
            out.write(json.dumps(rec) + "\n")
            out.flush()
    out.close()


if __name__ == "__main__":
    if sys.argv[1] == "build":
        for k in range(1, K + 1):
            build(k)
    else:
        check()
