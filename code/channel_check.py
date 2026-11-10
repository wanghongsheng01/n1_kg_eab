"""Channel check (2026-11-10): are Kimi-K2.6 runs served through the organisation's gateway equivalent to the vendor API?
30 random questions per dataset (seed 20261110) x {SQL, SQL-U}, Kimi-K2.6 with thinking disabled, through the gateway
(N1_KIMI_GATEWAY=1) with repeat index 7 (a fresh cache key). The analysis compares gateway-vs-vendor agreement of the
verdicts with the agreement of the vendor's two repetitions (rep 0 vs rep 1) on the same runs.
Usage: N1_DATASET=<ds> N1_QFILE=<ds>_questions.jsonl N1_COVFILE=<ds>_s3_coverage.json N1_KIMI_GATEWAY=1 python channel_check.py
       python channel_check.py analyze"""
import json, os, random, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
SUBS = ("sql", "sql_u")


def sample(ds):
    qf = os.path.join(HERE, "data", "xb_questions.jsonl" if ds == "xb" else f"{ds}_questions.jsonl")
    qs = [json.loads(l) for l in open(qf)]
    return random.Random(20261110).sample(qs, 30)


def run():
    import dsconf, run_pilot
    ds = dsconf.DS
    out = os.path.join(RES, f"channel_check_{ds}.jsonl")
    done = {(d["substrate"], d["qid"]) for d in map(json.loads, open(out))} if os.path.exists(out) else set()
    jobs = [(s, q) for q in sample(ds) for s in SUBS if (s, q["qid"]) not in done]
    with ThreadPoolExecutor(8) as ex:
        for rec in ex.map(lambda j: run_pilot.one("kimi-k2.6", j[0], j[1], 7), jobs):
            with open(out, "a") as f:
                f.write(json.dumps(rec) + "\n")


def analyze():
    rows = []
    for ds in ("xb", "aw", "lego"):
        v = {}
        for f, rep in ((f"main_{ds}.jsonl", 0), (f"main_{ds}_r1.jsonl", 1), (f"channel_check_{ds}.jsonl", 7)):
            for l in open(os.path.join(RES, f)):
                r = json.loads(l)
                if r["model"] == "kimi-k2.6" and r["substrate"] in SUBS and r["rep"] == rep:
                    v[(rep, r["substrate"], r["qid"])] = r["correct"]
        for (rep, s, q), c in v.items():
            if rep == 7 and (0, s, q) in v and (1, s, q) in v:
                rows.append((ds, s, v[(0, s, q)], v[(1, s, q)], c))
    n = len(rows)
    a01 = sum(r[2] == r[3] for r in rows) / n
    a07 = sum(r[2] == r[4] for r in rows) / n
    a17 = sum(r[3] == r[4] for r in rows) / n
    res = dict(n=n, vendor_rep0_vs_rep1=a01, gateway_vs_vendor_rep0=a07, gateway_vs_vendor_rep1=a17,
               acc_rep0=sum(r[2] for r in rows) / n, acc_rep1=sum(r[3] for r in rows) / n, acc_gateway=sum(r[4] for r in rows) / n)
    json.dump(res, open(os.path.join(RES, "channel_check.json"), "w"), indent=1)
    print(res)


if __name__ == "__main__":
    analyze() if sys.argv[1:] == ["analyze"] else run()
