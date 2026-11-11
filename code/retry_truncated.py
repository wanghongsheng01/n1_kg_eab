"""Truncation rule: a run in which any model call stopped at its output cap (finish_reason == "length") is re-run once,
with every call of that run allowed N1_MIN_MAX_TOKENS (8,000) output tokens.

Usage: N1_DATASET=<ds> N1_QFILE=<questions> N1_COVFILE=<coverage> N1_MIN_MAX_TOKENS=8000 python retry_truncated.py <main_*.jsonl>
Writes results/retry/<file> (resumable); apply_retries.py then replaces the truncated records in the main file.
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import dsconf
import run_pilot, run_agent

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")


def truncated(r):
    return any(a.get("finish") == "length" for a in r.get("attempts", []))


def main():
    src = sys.argv[1]
    base = os.path.join(RES, "pre_retry", src) if os.path.exists(os.path.join(RES, "pre_retry", src)) else os.path.join(RES, src)
    out = os.path.join(RES, os.environ.get("N1_RETRY_DIR", "retry"), src)  # retry_32k: thinking runs re-run with 32,000 tokens
    os.makedirs(os.path.dirname(out), exist_ok=True)
    qs = {json.loads(l)["qid"]: json.loads(l) for l in open(dsconf.QFILE)}
    todo = [r for r in map(json.loads, open(base)) if truncated(r)]
    done = set()
    if os.path.exists(out):
        done = {(d["model"], d["substrate"], d["qid"], d["rep"]) for d in map(json.loads, open(out))}
    skip = set(filter(None, os.environ.get("N1_RETRY_SKIP_MODELS", "").split(",")))   # models whose vendor API is gone
    jobs = [r for r in todo if (r["model"], r["substrate"], r["qid"], r["rep"]) not in done and r["model"] not in skip]
    print(f"{src}: {len(todo)} truncated, {len(jobs)} to run", flush=True)

    # N1_RETRY_VIA=claude-opus-5.5:claude-t1688 re-runs a model through another channel; the record keeps the original
    # model name and is marked with the channel, so analyze_v4.py merges it only if the paired channel check agrees
    via = dict(x.split(":") for x in filter(None, os.environ.get("N1_RETRY_VIA", "").split(",")))
    if via:   # only runs that are still truncated in the current file (earlier gateway re-runs already applied)
        cur = [r for r in map(json.loads, open(os.path.join(RES, src))) if truncated(r) and r["model"] in via]
        jobs = [r for r in cur if (r["model"], r["substrate"], r["qid"], r["rep"]) not in done]
        print(f"{src}: {len(jobs)} to run through {via}", flush=True)

    def go(r):
        q = qs[r["qid"]]
        m = via.get(r["model"], r["model"])
        rec = (run_agent.one(m, q, r["rep"]) if r["substrate"].startswith("agent")   # agent variants: N1_AGENT_VARIANT is set by the caller
               else run_pilot.one(m, r["substrate"], q, r["rep"]))
        if m != r["model"]:
            rec["model"], rec["channel"] = r["model"], m
        rec["retry"] = os.environ.get("N1_RETRY_TAG", "truncated")
        return rec

    with ThreadPoolExecutor(max_workers=int(os.environ.get("W", 4))) as ex:
        for f in as_completed([ex.submit(go, r) for r in jobs]):
            try:
                rec = f.result()
            except Exception as e:
                print("FAILED", repr(e)[:200], flush=True)
                continue
            with open(out, "a") as fo:
                fo.write(json.dumps(rec) + "\n")
    print("RETRY-DONE", src, flush=True)


if __name__ == "__main__":
    main()
