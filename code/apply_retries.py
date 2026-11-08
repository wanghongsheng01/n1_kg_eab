"""Replace truncated records in results/main_*.jsonl by their re-runs in results/retry/ (see retry_truncated.py).
The first application copies each original file to results/pre_retry/; re-applying starts from that copy, so it is idempotent.
Where a resumed run left several records for one execution, the last one is the one analyzed; it is kept, and replaced
only if one of its calls stopped at the output cap."""
import glob, json, os, shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
os.makedirs(os.path.join(RES, "pre_retry"), exist_ok=True)
key = lambda d: (d.get("dataset"), d["model"], d["substrate"], d["qid"], d["rep"])
trunc = lambda d: any(a.get("finish") == "length" for a in d.get("attempts", []))
for rp in sorted(glob.glob(os.path.join(RES, "retry", "main_*.jsonl"))):
    name = os.path.basename(rp)
    orig = os.path.join(RES, "pre_retry", name)
    if not os.path.exists(orig):
        shutil.copy2(os.path.join(RES, name), orig)
    new = {key(d): d for d in map(json.loads, open(rp))}
    last = {}
    for d in map(json.loads, open(orig)):
        last.pop(key(d), None); last[key(d)] = d
    n = 0
    with open(os.path.join(RES, name), "w") as fo:
        for k, d in last.items():
            if k in new and trunc(d):
                d = new[k]; n += 1
            fo.write(json.dumps(d) + "\n")
    print(f"{name}: replaced {n} of {len(new)} re-runs")
