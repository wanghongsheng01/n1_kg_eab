"""Engine sensitivity of the SQL results (no model calls).
DuckDB, like PostgreSQL, rejects a recursive CTE with more than one recursive branch
(base UNION ALL step1 UNION ALL step2) with "Table with name <cte> does not exist".
SQLite >= 3.34, SQL Server and Oracle accept it. For every SQL run that hit this error we re-execute
the query of the first such attempt on a SQLite copy of the same database and score it with the same
matcher. A query that SQLite rejects for any other dialect reason counts as not recovered.
Output: results/replay_multibranch.json (per substrate and dataset: runs, affected, recovered, timeouts at 60 s,
and correct_if_sqlite = runs correct on DuckDB or whose replayed query is correct on SQLite)."""
import json, os, re, sqlite3, sys, time
import duckdb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "code"))
from score import match

DATA, RES = os.path.join(HERE, "data"), os.path.join(HERE, "results")
SCR = os.environ.get("N1_SCRATCH", os.path.join(HERE, "results", "sqlite"))
MAIN = ("ds-flash", "ds-pro", "kimi-k2.6")
DBF = {"sql": "{ds}.duckdb", "sql_u": "{ds}_u.duckdb", "sql_x": "{ds}_x.duckdb"}
CTE_ERR = re.compile(r"Table with name (\w+) does not exist")


def extract(text):
    m = re.findall(r"```(?:sql)?\s*\n(.*?)```", text, re.S | re.I) or re.findall(r"```\w*\s*\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def to_sqlite(src, dst):
    if os.path.exists(dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d = duckdb.connect(src, read_only=True)
    s = sqlite3.connect(dst + ".tmp")
    for (t,) in d.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall():
        cols = d.execute(f'DESCRIBE "{t}"').fetchall()
        s.execute(f'CREATE TABLE "{t}" (' + ", ".join(f'"{c[0]}"' for c in cols) + ")")
        rows = d.execute(f'SELECT * FROM "{t}"').fetchall()
        conv = lambda v: v if v is None or isinstance(v, (int, float, str, bytes)) else str(v)
        s.executemany(f'INSERT INTO "{t}" VALUES (' + ",".join("?" * len(cols)) + ")", [tuple(map(conv, r)) for r in rows])
    s.commit(); s.close(); os.replace(dst + ".tmp", dst)


def run(con, q, timeout=60):
    t0 = time.time()
    con.set_progress_handler(lambda: 1 if time.time() - t0 > timeout else 0, 10000)
    try:
        return con.execute(q).fetchmany(1000), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:200]


out = {}
for ds in ("xb", "aw", "lego"):
    Q = {q["qid"]: q for q in map(json.loads, open(os.path.join(DATA, f"{ds}_questions.jsonl")))}
    recs = [json.loads(l) for l in open(os.path.join(RES, f"main_{ds}.jsonl"))]
    for sub, f in DBF.items():
        if not os.path.exists(os.path.join(DATA, f.format(ds=ds))):
            continue
        rs = [r for r in recs if r["substrate"] == sub and r["model"] in MAIN and r.get("rep", 0) == 0]
        if not rs:
            continue
        hit = [(r, a) for r in rs for a in r["attempts"][:1] + [a for a in r["attempts"][1:]]
               if a.get("err") and CTE_ERR.search(a["err"]) and CTE_ERR.search(a["err"]).group(1).lower() in a["text"].lower()]
        first = {}
        for r, a in hit:
            first.setdefault(id(r), (r, a))
        dst = os.path.join(SCR, f"{ds}_{sub}.sqlite")
        to_sqlite(os.path.join(DATA, f.format(ds=ds)), dst)
        con = sqlite3.connect(dst)
        rec, errs, okk = 0, {}, set()
        for k, (r, a) in first.items():
            rows, err = run(con, extract(a["text"]))
            ok = err is None and bool(match(rows, Q[r["qid"]]))
            rec += ok
            if ok:
                okk.add(k)
            if err:
                k = re.sub(r"[\"'`].*?[\"'`]", "<x>", err)[:60]; errs[k] = errs.get(k, 0) + 1
        out[f"{ds}/{sub}"] = dict(runs=len(rs), correct=sum(r["correct"] for r in rs), affected=len(first),
                                  affected_correct=sum(r["correct"] for r, _ in first.values()), recovered=rec,
                                  timeouts=errs.get("OperationalError: interrupted", 0),
                                  correct_if_sqlite=sum(bool(r["correct"]) or id(r) in okk for r in rs),
                                  sqlite_errors=dict(sorted(errs.items(), key=lambda x: -x[1])[:5]))
        print(ds, sub, out[f"{ds}/{sub}"], flush=True)
json.dump(out, open(os.path.join(RES, "replay_multibranch.json"), "w"), indent=1)
