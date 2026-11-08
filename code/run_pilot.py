"""Run the pilot: questions x substrates x models, one generation + at most one repair on execution error.

Usage: python run_pilot.py --models ds-flash,ds-pro,glm-air --substrates sql,cypher,semantic [--rep 0] [--workers 6]
Writes results/runs.jsonl (one line per model x substrate x question x rep; resumable).
"""
import argparse, json, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb, kuzu
import dsconf
import prompts as P
import semantic as SEM
import mf_layer as MF
from llm import chat
from semantic import compile_query
from score import match

TASK = dict(dsconf.task_table(P, SEM.SPEC))
# Every Cypher note (both engines, both modelings, all datasets) states the multi-type pattern syntax A|B.
# Ablations (identical wording on every dataset):
#  cypher_nh / neo_t_nh = Graph-T without the multi-type sentence (the first main run's XB and LEGO wording)
#  semantic_abs         = Metric plus an explicit permission to answer UNSUPPORTED (optional-refusal control for Metric-R)
_MULTI = re.compile(r"several relationship\s+types are matched with -\[e:A\|B\*1\.\.6\]->; ")
TASK["cypher_nh"] = (TASK["cypher"][0], _MULTI.sub("", TASK["cypher"][1]))
#  cypher_h = Graph-T with the same syntax repeated as a separate note after the business rules (end of the prompt)
MULTI_TYPE_NOTE = ("\nAdditional Kuzu dialect note: one variable-length pattern may match several relationship types, "
                   "written -[e:A|B*1..6]->.")
TASK["cypher_h"] = (TASK["cypher"][0], TASK["cypher"][1] + MULTI_TYPE_NOTE)
# Second graph engine (Neo4j 5): same schema text, Kuzu dialect note replaced by an equally syntax-only Neo4j note
NEO_NOTE_NH = ("Neo4j dialect notes: a variable-length pattern is written -[:R*1..6]->; bind the path, p = (a)-[:R*1..6]->(b), "
               "and use relationships(p); all(r IN relationships(p) WHERE r.qty > 0) filters every relationship of the path; "
               "reduce(acc = 1, r IN relationships(p) | acc * r.qty) multiplies a property along the path; date literals are "
               "written date('2026-10-01'); d.year extracts a field.")
NEO_NOTE = NEO_NOTE_NH.replace("filters every relationship of the path; ", "filters every relationship of the path; "
                               "several relationship types are matched with -[:A|B*1..6]->; ")

def _neo_doc(doc, note=NEO_NOTE):
    doc = re.sub(r"Kuzu dialect notes:.*?(?=\n\n|\Z)", lambda m: note, doc, flags=re.S)
    return doc.replace("(Kuzu, openCypher dialect)", "(Neo4j 5, Cypher)").replace("Kuzu", "Neo4j")


_neo_inst = "Write ONE Neo4j Cypher query that answers the question. Return only the query in a ```cypher block."
TASK["neo_t"] = (_neo_inst, _neo_doc(TASK["cypher"][1]))
TASK["neo_u"] = (_neo_inst, _neo_doc(TASK["cypher_u"][1]))
TASK["neo_t_nh"] = (_neo_inst, _neo_doc(TASK["cypher_nh"][1], NEO_NOTE_NH))
# DuckDB dialect note (sql_dn, sql_u_dn, sql_x_dn): the counterpart of the Kuzu note, syntax only, placed where the Kuzu
# note is (after the schema, before the business rules); added after review, the main-grid SQL substrates have no note
DUCK_NOTE = ("DuckDB dialect notes: a recursive CTE has one anchor query and one recursive query, WITH RECURSIVE t AS (anchor "
             "UNION ALL step), and only that step may reference t; to follow several edge tables, join t to a UNION ALL of those "
             "tables inside the step; date literals are written DATE '2026-10-01'; extract('year' FROM d) extracts a field.")
for _s in ("sql", "sql_u", "sql_x"):
    if _s in TASK:
        _doc = TASK[_s][1].replace("\n\nBusiness rules:", "\n\n" + DUCK_NOTE + "\n\nBusiness rules:", 1)
        assert DUCK_NOTE in _doc, _s
        TASK[_s + "_dn"] = (TASK[_s][0], _doc)
# Graph-X with a usage sentence (cypher_x_tn): tests whether the main models' re-traversal of the materialized edge is
# removed by saying how to use it; added after review
X_NOTE = ("an EXPLODES edge already links a root to every item below it with the total quantity, so match one EXPLODES "
          "edge and never traverse EXPLODES with *; ")
if "cypher_x" in TASK:
    _doc = TASK["cypher_x"][1].replace("Kuzu dialect notes: ", "Kuzu dialect notes: " + X_NOTE, 1)
    assert X_NOTE in _doc
    TASK["cypher_x_tn"] = (TASK["cypher_x"][0], _doc)
TASK["semantic_abs"] = (TASK["semantic"][0] + " If no metric can express the question, reply with the single word "
                        "UNSUPPORTED instead of a query.", SEM.SPEC)


def messages(substrate, question):
    inst, doc = TASK[substrate]
    sys_ = ("You answer questions over a product-composition database. " + inst +
            " The query result is taken as the answer, so it must return exactly the requested value(s): "
            "a single row for a single number, one row per item for a list.")
    return [{"role": "system", "content": sys_}, {"role": "user", "content": doc + "\n\nQUESTION: " + question}]


def repair(substrate, prev_text, error):
    return {"role": "user", "content": f"Executing your query failed with this error:\n{error[:800]}\nReturn a corrected query in the same format."}

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "results", "runs.jsonl")
TIMEOUT = 60
_tls = threading.local()
_sql = duckdb.connect(dsconf.SQL_DB, read_only=True)
_sql_u = duckdb.connect(dsconf.SQL_U_DB, read_only=True)
_sql_x = duckdb.connect(dsconf.SQL_X_DB, read_only=True) if os.path.exists(dsconf.SQL_X_DB) else None  # closure (build_closure.py)
_wlock = threading.Lock()


def extract(text, lang):
    m = re.findall(r"```(?:" + lang + r")?\s*\n(.*?)```", text, re.S | re.I)
    if m:
        return m[-1].strip()
    m = re.findall(r"```\w*\s*\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def run_sql(sql, db="sql"):
    cur = getattr(_tls, db, None)
    if cur is None:
        cur = (_sql if db == "sql" else _sql_x if db == "sql_x" else _sql_u).cursor()
        # generated recursive queries can spill gigabytes to disk; cap it so a runaway query fails instead
        cur.execute("SET max_temp_directory_size = '300MB'")
        setattr(_tls, db, cur)
    t = threading.Timer(TIMEOUT, cur.interrupt)
    t.start()
    try:
        return cur.execute(sql).fetchmany(1000)
    finally:
        t.cancel()


def run_cypher(q, db="kg"):
    """Kuzu runs in a child process: some generated queries crash the engine (SIGBUS/SIGSEGV)."""
    path = dsconf.KG if db == "kg" else dsconf.KG_U
    return cypher_subprocess(path, q)


def run_neo(q, variant):
    import requests
    port = open(os.path.join(HERE, ".tools", "neo4j_vars", f"{dsconf.DS}_{variant}", "port")).read().strip()
    try:
        out = requests.post(f"http://127.0.0.1:{port}/db/neo4j/tx/commit", json={"statements": [{"statement": q}]},
                            timeout=TIMEOUT + 30).json()
    except requests.exceptions.ReadTimeout:
        raise RuntimeError("Interrupted: wall-clock timeout")
    if out.get("errors"):
        e = out["errors"][0]
        raise RuntimeError(f"{e.get('code', '')}: {e.get('message', '')[:500]}")
    return [d["row"] for d in out["results"][0]["data"]][:1000]


def cypher_subprocess(path, q):
    import subprocess, sys
    try:
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "kuzu_exec.py"), path], input=q,
                           capture_output=True, text=True, timeout=TIMEOUT + 30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Interrupted: wall-clock timeout")
    if p.returncode != 0:
        raise RuntimeError(f"EngineCrash: kuzu process exited with code {p.returncode}")
    out = json.loads(p.stdout.strip().splitlines()[-1])
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["rows"]


class Abstain(Exception):
    pass


def route(substrate, text):
    """Which interface a hybrid answer used ('metric' or 'fallback'); 'abstain' for an UNSUPPORTED reply."""
    if "```" not in text and "UNSUPPORTED" in text.upper():
        return "abstain"
    if substrate == "hybrid" and re.search(r"```sql", text, re.I):
        return "fallback"
    return "metric" if substrate in ("semantic", "semantic_abs", "hybrid") else substrate


def q_(text, base):
    q = extract(text, "sql")
    return q, run_sql(q, base)


def execute(substrate, text):
    if substrate == "classify":
        substrate = "semantic"
    if substrate in ("semantic_abs", "hybrid"):
        rt = route(substrate, text)
        if rt == "abstain":
            raise Abstain("UNSUPPORTED")
        if rt == "fallback":
            q = extract(text, "sql")
            return q, run_sql(q, "sql")
        q = extract(text, "json")
        return q, run_sql(compile_query(json.loads(q)))
    if substrate == "metricflow":  # public-standard semantic layer: MetricFlow compiles, SQL runs on data/mf_<ds>.duckdb
        q = extract(text, "json")
        return q, MF.run(MF.compile_mf(json.loads(q), dsconf.DS), dsconf.DS)
    lang = {"sql": "sql", "sql_u": "sql", "cypher": "cypher", "cypher_nh": "cypher", "cypher_h": "cypher", "cypher_u": "cypher", "semantic": "json",
            "neo_t": "cypher", "neo_u": "cypher", "neo_t_nh": "cypher", "sql_x": "sql", "cypher_x": "cypher",
            "sql_dn": "sql", "sql_u_dn": "sql", "sql_x_dn": "sql", "cypher_x_tn": "cypher"}[substrate]
    if substrate.endswith("_dn"):  # same databases as the main-grid SQL substrates
        return q_(text, substrate[:-3])
    q = extract(text, lang)
    if substrate == "sql_x":
        return q, run_sql(q, "sql_x")
    if substrate in ("cypher_x", "cypher_x_tn"):
        return q, cypher_subprocess(dsconf.KG_X, q)
    if substrate in ("neo_t", "neo_u", "neo_t_nh"):
        return q, run_neo(q, "t" if substrate.startswith("neo_t") else "u")
    if substrate in ("sql", "sql_u"):
        return q, run_sql(q, substrate)
    if substrate in ("cypher", "cypher_nh", "cypher_h", "cypher_u"):
        return q, run_cypher(q, "kg_u" if substrate == "cypher_u" else "kg")
    return q, run_sql(compile_query(json.loads(q)))


CLASSIFY_SYS = ("You route questions over a product-composition data warehouse to a registry of governed metrics. "
                "Decide whether ONE registered metric can answer the question exactly (with its params, dimensions and filters). "
                "Reply with JSON only: {\"question_topic\": \"<metric name>\" or \"not_registered\", \"reason\": \"<one sentence>\"}. "
                "Choose not_registered when no metric computes exactly what is asked; then the system answers 'not provided'.")


def classify(model, q, rep):
    """S9 step 1 (fact-registry pattern): closed-vocabulary routing with an explicit not_registered label."""
    SPEC = SEM.SPEC
    r = chat(model, [{"role": "system", "content": CLASSIFY_SYS},
                     {"role": "user", "content": SPEC + "\n\nQUESTION: " + q["text"]}], rep=rep, max_tokens=300)
    m = re.search(r"\{.*\}", r["text"], re.S)
    try:
        topic = json.loads(m.group(0)).get("question_topic")
    except Exception:
        topic = None
    return r, topic


def one(model, substrate, q, rep):
    pre = None
    if substrate == "classify":
        pre, topic = classify(model, q, rep)
        if topic == "not_registered" or topic is None:
            rec = dict(dataset=dsconf.DS, model=model, substrate=substrate, qid=q["qid"], cls=q["cls"], tpl=q["tpl"], rep=rep, topic=topic,
                       attempts=[dict(text=pre["text"], usd=pre["usd"], latency=pre["latency"], exec_s=0.0, err="Abstain",
                                      in_tok=pre["usage"].get("prompt_tokens", 0), out_tok=pre["usage"].get("completion_tokens", 0),
                                      finish=pre.get("finish"))],
                       rows=[], n_rows=None, correct=False, outcome="abstain", route="abstain")
            for f in ("usd", "in_tok", "out_tok"):
                rec[f] = rec["attempts"][0][f]
            rec["llm_s"], rec["exec_s"] = pre["latency"], 0.0
            return rec
        msgs = messages("semantic", q["text"] + f"\n(Routing decision: use the metric `{topic}`.)")
    else:
        msgs = messages(substrate, q["text"])
    rec = dict(dataset=dsconf.DS, model=model, substrate=substrate, qid=q["qid"], cls=q["cls"], tpl=q["tpl"], rep=rep, attempts=[])
    if pre is not None:
        rec["topic"] = topic
        rec["attempts"].append(dict(text=pre["text"], usd=pre["usd"], latency=pre["latency"], exec_s=0.0, err=None,
                                    in_tok=pre["usage"].get("prompt_tokens", 0), out_tok=pre["usage"].get("completion_tokens", 0),
                                    finish=pre.get("finish")))
    rows, err = None, None
    for attempt in range(2):
        r = chat(model, msgs, rep=rep)
        t0 = time.time()
        try:
            query, rows = execute(substrate, r["text"])
            err = None
        except Abstain:
            query, rows, err = None, None, "Abstain"
        except Exception as e:
            query, rows, err = None, None, f"{type(e).__name__}: {str(e)[:600]}"
        ex = time.time() - t0
        rec["attempts"].append(dict(text=r["text"], usd=r["usd"], latency=r["latency"], exec_s=ex, err=err,
                                    in_tok=r["usage"].get("prompt_tokens", 0), out_tok=r["usage"].get("completion_tokens", 0),
                                    finish=r.get("finish")))
        if err is None or err == "Abstain":
            break
        msgs = msgs + [{"role": "assistant", "content": r["text"]}, repair(substrate, r["text"], err)]
    rec["rows"] = [[str(x) for x in row] for row in (rows or [])[:20]]
    rec["n_rows"] = None if rows is None else len(rows)
    rec["correct"] = bool(match(rows, q)) if err is None else False
    if rec["correct"]:
        rec["outcome"] = "correct"
    elif err == "Abstain":
        rec["outcome"] = "abstain"
    elif err is not None:
        rec["outcome"] = ("crash" if "EngineCrash" in err else
                          "timeout" if ("interrupt" in err.lower() or "timeout" in err.lower()) else "error")
    elif not rows:
        rec["outcome"] = "empty"
    else:
        rec["outcome"] = "wrong"
    rec["route"] = route(substrate, rec["attempts"][-1]["text"])
    rec["usd"] = sum(a["usd"] for a in rec["attempts"])
    rec["llm_s"] = sum(a["latency"] for a in rec["attempts"])
    rec["exec_s"] = sum(a["exec_s"] for a in rec["attempts"])
    rec["in_tok"] = sum(a["in_tok"] for a in rec["attempts"])
    rec["out_tok"] = sum(a["out_tok"] for a in rec["attempts"])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="ds-flash")
    ap.add_argument("--substrates", default="sql,cypher,semantic")
    ap.add_argument("--rep", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="runs.jsonl")
    a = ap.parse_args()
    global OUT
    OUT = os.path.join(HERE, "results", a.out)
    qs = [json.loads(l) for l in open(dsconf.QFILE)]
    if a.limit:
        qs = qs[:a.limit]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            d = json.loads(l)
            done.add((d["model"], d["substrate"], d["qid"], d["rep"]))
    jobs = [(m, s, q) for m in a.models.split(",") for s in a.substrates.split(",") for q in qs
            if (m, s, q["qid"], a.rep) not in done]
    print(f"{len(jobs)} jobs", flush=True)
    by_model = {}
    for j in jobs:
        by_model.setdefault(j[0], []).append(j)
    # one pool per model so a slow vendor does not block the others
    pools = {m: ThreadPoolExecutor(max_workers=(2 if m.startswith("glm") else a.workers)) for m in by_model}
    futs = [pools[m].submit(one, m, s, q, a.rep) for m, js in by_model.items() for (m, s, q) in js]
    n = 0
    for f in as_completed(futs):
        try:
            rec = f.result()
        except Exception as e:
            print("FAILED", repr(e)[:200], flush=True)
            continue
        with _wlock:
            with open(OUT, "a") as fo:
                fo.write(json.dumps(rec) + "\n")
        n += 1
        if n % 25 == 0:
            print(n, rec["model"], rec["substrate"], rec["qid"], rec["outcome"], flush=True)
    print("done", n, flush=True)


if __name__ == "__main__":
    main()
