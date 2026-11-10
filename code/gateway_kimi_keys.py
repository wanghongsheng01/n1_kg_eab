"""Kimi-K2.6 records served through the organisation's gateway (2026-11-10, after the vendor account ran out) are not
equivalent to vendor-API runs (channel_check.py), so the analysis excludes them. Every file is append-only; the line
counts below were taken at 15:35 on 2026-11-10, before the first gateway call, so every Kimi-K2.6 record after them came
through the gateway. Writes results/kimi_gateway_excluded.json (file, dataset, model, substrate, qid, rep)."""
import json, os

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
BEFORE = {"main_aw_fresh3.jsonl": 1690, "main_lego_fresh3.jsonl": 1655, "main_xb_fresh3.jsonl": 1701,
          "main_aw_fresh_neo.jsonl": 1126, "main_lego_fresh_neo.jsonl": 736, "main_xb_fresh_neo.jsonl": 1134}
keys = []
for f, n in BEFORE.items():
    for i, l in enumerate(open(os.path.join(RES, f))):
        r = json.loads(l)
        if i >= n and r["model"] == "kimi-k2.6":
            keys.append([f, r.get("dataset"), r["model"], r["substrate"], r["qid"], r.get("rep", 0)])
json.dump(keys, open(os.path.join(RES, "kimi_gateway_excluded.json"), "w"), indent=0)
print(len(keys), "gateway Kimi-K2.6 records excluded")
