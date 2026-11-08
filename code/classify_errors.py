"""LLM-assisted error taxonomy for failed rep-0 runs (one primary category each). Spot-checked by hand.

Usage: python classify_errors.py  -> results/error_taxonomy.jsonl
"""
import glob, json, os, random, re
from concurrent.futures import ThreadPoolExecutor
import dsconf
import prompts as P
import semantic as SEM
from llm import chat
TASK = dsconf.task_table(P, SEM.SPEC)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATS = {
    "T1_schema_link": "wrong table, column, label or identifier (e.g. matched name instead of code, wrong entity)",
    "T2_traversal": "wrong composition traversal: wrong edge type or direction, missing a level, missing direct children, depth bound too small, not recursive",
    "T3_quantity": "wrong quantity semantics: no multiplication along the path, a level's qty dropped, counting paths/rows instead of summing quantities, double counting",
    "T4_validity": "BOM validity / as-of date applied wrongly or only on some levels, or wrong date",
    "T5_mapping": "SKU-model mapping or bundle expansion wrong (not expanded, wrong direction, qty ignored)",
    "T6_order_filter": "wrong order filter: status, date range, store, platform, country",
    "T7_output": "result shape or aggregation wrong: extra rows/columns, wrong aggregate, top-k, rounding, NULL instead of 0",
    "T8_dialect": "query-language or engine misuse: unsupported function/syntax, binder/parser error not repaired",
    "T9_api_misuse": "semantic-layer API misuse: wrong metric, wrong params, misuse of aggregate/filters",
    "T10_out_of_coverage": "the question cannot be expressed in this interface and the answer is necessarily wrong",
    "T11_question_misread": "misread what the question asks (e.g. parts vs components, wrong entity type)",
}


def prompt(r, q):
    att = r["attempts"][-1]
    doc = TASK.get(r["substrate"], TASK["semantic"])[1]
    cat = "\n".join(f"- {k}: {v}" for k, v in CATS.items())
    return [{"role": "system", "content": "You diagnose why a generated database query gave a wrong answer. Answer with JSON only."},
            {"role": "user", "content": f"""INTERFACE DOCUMENTATION:\n{doc}\n\nQUESTION: {q['text']}\nCORRECT ANSWER: {q['gold']}
GENERATED QUERY (final attempt):\n{att['text'][:3000]}\nRESULT ROWS (first rows): {r['rows'][:5]}\nEXECUTION ERROR: {att['err']}

Choose the ONE primary cause from:\n{cat}\nReturn {{"category": "<key>", "reason": "<one sentence>"}}."""}]


def main():
    qs = {json.loads(l)["qid"]: json.loads(l) for l in open(dsconf.QFILE)}
    files = os.environ.get("N1_TAX_RUNS", "runs.jsonl,runs_u.jsonl").split(",")
    has_m2 = any(f.endswith("_m2.jsonl") for f in files)   # m2 supersedes Metric/Metric-R in the main file
    has_g2 = any(f.endswith("_g2.jsonl") for f in files)   # g2 supersedes Graph-T/Graph-U (uniform Cypher note)
    R = [r for f in files for r in map(json.loads, open(os.path.join(HERE, "results", f)))
         if (not has_m2 or f.endswith("_m2.jsonl") or r["substrate"] not in ("semantic", "classify"))
         and (not has_g2 or f.endswith("_g2.jsonl") or r["substrate"] not in ("cypher", "cypher_u"))
         and r["substrate"] != "cypher_nh"]
    R = [r for r in R if r["rep"] == 0 and not r["correct"] and r["outcome"] != "abstain"
         and (r["substrate"] in TASK or r["substrate"] == "classify")]
    cap = int(os.environ.get("N1_TAX_CAP", "0"))
    if cap:   # stratified sample: at most `cap` failures per substrate (fixed seed)
        rng, by = random.Random(7), {}
        for r in R:
            by.setdefault(r["substrate"], []).append(r)
        R = [r for xs in by.values() for r in rng.sample(xs, min(cap, len(xs)))]

    def go(r):
        out = chat(os.environ.get("N1_TAX_MODEL", "ds-pro"), prompt(r, qs[r["qid"]]), max_tokens=300)
        m = re.search(r"\{.*\}", out["text"], re.S)
        try:
            d = json.loads(m.group(0))
        except Exception:
            d = {"category": "unparsed", "reason": out["text"][:200]}
        return dict(dataset=dsconf.DS, model=r["model"], substrate=r["substrate"], qid=r["qid"], cls=r["cls"], outcome=r["outcome"], **d)

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(go, R))
    with open(os.path.join(HERE, "results", os.environ.get("N1_TAX_OUT", "error_taxonomy.jsonl")), "w") as f:
        for d in res:
            f.write(json.dumps(d) + "\n")
    print(len(res))


if __name__ == "__main__":
    main()
