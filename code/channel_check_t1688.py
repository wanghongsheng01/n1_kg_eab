"""Channel check (2026-11-10 night): is Claude-Opus-5.5 served by a third-party provider (tokens1688) equivalent to the
organisation's gateway, on the cells where truncated gateway runs are to be re-run (LEGO, Neo4j typed and unified graphs)?
50 random LEGO questions (seed 20261110) whose gateway runs did not hit the output cap x {neo_t, neo_u}, same prompts and
1,500-token cap, model claude-t1688 (llm.py). The analysis reports verdict agreement with the gateway runs and the
accuracy of both channels on the same runs; the re-runs (results/retry_t1688/) are merged only if agreement is at least
95% and the discordant pairs show no significant difference (two-sided exact binomial test, p >= 0.05).
Usage: N1_DATASET=lego N1_QFILE=lego_questions.jsonl N1_COVFILE=lego_s3_coverage.json python channel_check_t1688.py
       python channel_check_t1688.py analyze"""
import json, math, os, random, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
SUBS = ("neo_t", "neo_u")
SRC = os.path.join(RES, "main_lego_fr_neo.jsonl")
OUT = os.path.join(RES, "channel_check_t1688_lego.jsonl")


def gateway():
    g = {}
    for l in open(SRC):
        r = json.loads(l)
        if r["model"] == "claude-opus-5.5" and r["substrate"] in SUBS:
            g[(r["substrate"], r["qid"])] = r
    return g


def sample():
    g = gateway()
    trunc = {q for (s, q), r in g.items() if any(a.get("finish") == "length" for a in r.get("attempts", []))}
    qs = [json.loads(l) for l in open(os.path.join(HERE, "data", "lego_questions.jsonl"))]
    qs = [q for q in qs if q["qid"] not in trunc and all((s, q["qid"]) in g for s in SUBS)]
    return random.Random(20261110).sample(qs, 50)


def run():
    import run_pilot
    done = {(d["substrate"], d["qid"]) for d in map(json.loads, open(OUT))} if os.path.exists(OUT) else set()
    jobs = [(s, q) for q in sample() for s in SUBS if (s, q["qid"]) not in done]
    with ThreadPoolExecutor(6) as ex:
        for rec in ex.map(lambda j: run_pilot.one("claude-t1688", j[0], j[1], 0), jobs):
            with open(OUT, "a") as f:
                f.write(json.dumps(rec) + "\n")


def analyze():
    g = gateway()
    rows = [(r["substrate"], r["qid"], g[(r["substrate"], r["qid"])]["correct"], r["correct"])
            for r in map(json.loads, open(OUT)) if (r["substrate"], r["qid"]) in g]
    n = len(rows)
    agree = sum(a == b for _, _, a, b in rows) / n
    b10 = sum(a and not b for _, _, a, b in rows)
    b01 = sum(b and not a for _, _, a, b in rows)
    k, m = min(b10, b01), b10 + b01
    p = min(1.0, 2 * sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m) if m else 1.0
    res = dict(n=n, agreement=agree, acc_gateway=sum(r[2] for r in rows) / n, acc_t1688=sum(r[3] for r in rows) / n,
               discordant_gateway_only=b10, discordant_t1688_only=b01, p_exact=p, merge=agree >= 0.95 and p >= 0.05)
    json.dump(res, open(os.path.join(RES, "channel_check_t1688.json"), "w"), indent=1)
    print(res)


if __name__ == "__main__":
    analyze() if sys.argv[1:] == ["analyze"] else run()
