"""S8: an agent that calls low-level governed tools directly (the production pattern: an agent CLI talking to a governed
metric service and a raw read-only SQL service through an MCP gateway).

Tools (vendor-neutral JSON actions, no native function calling, so every vendor gets the same protocol):
  list_metrics                -> one line per metric
  metric_detail {name}        -> that metric's section of the semantic-layer spec
  describe_tables             -> the detail-table schema and business rules (same text as S1)
  query {q}                   -> governed metric query (semantic.compile_query), result rows
  run_sql {sql}               -> read-only DuckDB SQL over detail tables, result rows
  answer {step}               -> the result of that earlier query/run_sql step is the answer
At most MAX_STEPS model calls; no answer by then counts as no answer.

Usage: python run_agent.py --models ds-flash,ds-pro,kimi-k2.6 [--rep 0] [--workers 6] [--out runs_agent.jsonl]
"""
import argparse, json, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import dsconf
from llm import chat
from prompts import SQL_DOC
from semantic import SPEC, compile_query
from score import match
import run_pilot as RP

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_STEPS = 8
try:  # metric catalog = one line per metric; falls back to the whole spec if a dataset formats it differently
    _parts = re.split(r"\n(?=\d+\. \w+ -- )", SPEC.split("Metrics:\n", 1)[1])
    METRICS = {re.match(r"\d+\. (\w+)", p).group(1): p.strip() for p in _parts}
    HEADER = SPEC.split("Metrics:\n", 1)[0]
    CATALOG = "\n".join(f"- {k}: {v.split('--', 1)[1].splitlines()[0].strip()}" for k, v in METRICS.items())
except Exception:
    METRICS, HEADER, CATALOG = {"all": SPEC}, "", "(single specification; call metric_detail with name 'all')\n" + SPEC[:400]

SYSTEM = """You answer questions over a product-composition data warehouse by calling tools. Reply with exactly ONE JSON
object per turn and nothing else:
{"action": "list_metrics"}
{"action": "metric_detail", "name": "<metric>"}
{"action": "describe_tables"}
{"action": "query", "q": <governed metric query object>}     (governed metric layer; preferred when a metric fits)
{"action": "run_sql", "sql": "<one DuckDB SELECT over the detail tables>"}   (raw read-only SQL)
{"action": "answer", "step": <number of the query/run_sql step whose result is the answer>}
The chosen result is taken as the answer, so it must contain exactly the requested value(s): one row for a single
number, one row per item for a list. You have at most %d turns.""" % MAX_STEPS


def tool(act, steps):
    a = act.get("action")
    if a == "list_metrics":
        return "METRICS:\n" + CATALOG
    if a == "metric_detail":
        n = act.get("name")
        if n not in METRICS:
            raise ValueError(f"unknown metric {n!r}")
        return HEADER + "\n" + METRICS[n]
    if a == "describe_tables":
        return SQL_DOC
    if a == "query":
        q = act.get("q")
        rows = RP.run_sql(compile_query(q if isinstance(q, dict) else json.loads(q)), "sql")
        return rows
    if a == "run_sql":
        return RP.run_sql(act["sql"], "sql")
    raise ValueError(f"unknown action {a!r}")


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object")
    return json.loads(m.group(0))


def one(model, q, rep):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "QUESTION: " + q["text"]}]
    rec = dict(dataset=dsconf.DS, model=model, substrate="agent", qid=q["qid"], cls=q["cls"], tpl=q["tpl"], rep=rep, attempts=[], tools=[])
    results, final, err = {}, None, None
    for k in range(1, MAX_STEPS + 1):
        r = chat(model, msgs, rep=rep, max_tokens=1200)
        t0 = time.time()
        obs, act = None, None
        try:
            act = parse(r["text"])
            if act.get("action") == "answer":
                final = int(act.get("step"))
                if final not in results:
                    raise ValueError(f"step {final} has no query result")
            else:
                obs = tool(act, results)
                if isinstance(obs, list):
                    results[k] = obs
        except Exception as e:
            obs = f"ERROR {type(e).__name__}: {str(e)[:500]}"
            final = None if act is None or act.get("action") != "answer" else None
        rec["attempts"].append(dict(text=r["text"], usd=r["usd"], latency=r["latency"], exec_s=time.time() - t0,
                                    err=obs if isinstance(obs, str) and obs.startswith("ERROR") else None,
                                    in_tok=r["usage"].get("prompt_tokens", 0), out_tok=r["usage"].get("completion_tokens", 0),
                                    finish=r.get("finish")))
        rec["tools"].append((act or {}).get("action", "parse_error"))
        if final is not None:
            break
        shown = obs if isinstance(obs, str) else f"RESULT of step {k} ({len(obs)} rows): {[list(map(str, x)) for x in obs[:20]]}"
        msgs = msgs + [{"role": "assistant", "content": r["text"]}, {"role": "user", "content": f"[step {k}] {shown}"}]
    rows = results.get(final) if final is not None else None
    rec["route"] = (None if final is None else
                    "metric" if rec["tools"][final - 1] == "query" else "fallback")
    rec["rows"] = [[str(x) for x in row] for row in (rows or [])[:20]]
    rec["n_rows"] = None if rows is None else len(rows)
    rec["correct"] = bool(match(rows, q)) if rows is not None else False
    rec["outcome"] = ("correct" if rec["correct"] else "no_answer" if rows is None else "empty" if not rows else "wrong")
    for f in ("usd", "in_tok", "out_tok"):
        rec[f] = sum(a[f] for a in rec["attempts"])
    rec["llm_s"] = sum(a["latency"] for a in rec["attempts"])
    rec["exec_s"] = sum(a["exec_s"] for a in rec["attempts"])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="ds-flash")
    ap.add_argument("--rep", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="runs_agent.jsonl")
    a = ap.parse_args()
    out = os.path.join(HERE, "results", a.out)
    qs = [json.loads(l) for l in open(dsconf.QFILE)][: a.limit or None]
    done = set()
    if os.path.exists(out):
        done = {(d["model"], d["qid"], d["rep"]) for d in map(json.loads, open(out))}
    jobs = [(m, q) for m in a.models.split(",") for q in qs if (m, q["qid"], a.rep) not in done]
    print(len(jobs), "jobs", flush=True)
    pools = {m: ThreadPoolExecutor(2 if m.startswith("glm") else a.workers) for m in a.models.split(",")}
    futs = [pools[m].submit(one, m, q, a.rep) for m, q in jobs]
    n = 0
    for f in as_completed(futs):
        try:
            rec = f.result()
        except Exception as e:
            print("FAILED", repr(e)[:200], flush=True)
            continue
        with open(out, "a") as fo:
            fo.write(json.dumps(rec) + "\n")
        n += 1
        if n % 25 == 0:
            print(n, rec["model"], rec["qid"], rec["outcome"], flush=True)
    print("done", n, flush=True)


if __name__ == "__main__":
    main()
