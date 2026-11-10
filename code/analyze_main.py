"""Main-study analysis: results/main_*.jsonl -> results/main_summary.md, paper/numbers.tex, paper/tables/*.tex.

Every number quoted in the paper comes from this script (numbers.tex macros or generated tables).
"""
import glob, json, os, random, re
from collections import defaultdict, Counter
from math import comb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES, PAPER = os.path.join(HERE, "results"), os.path.join(HERE, "paper")
os.makedirs(os.path.join(PAPER, "tables"), exist_ok=True)
DS = ["xb", "aw", "lego"]
DSN = {"xb": "XB", "aw": "AW", "lego": "LEGO"}
SUBS = ["sql", "sql_u", "cypher", "cypher_u", "semantic", "classify", "agent"]
XSUBS = ["sql_x", "cypher_x"]
SN = {"metricflow": "MetricFlow", "sql_x": "SQL-X", "cypher_x": "Graph-X", "sql": "SQL", "sql_u": "SQL-U", "cypher": "Graph-T", "cypher_u": "Graph-U", "semantic": "Metric",
      "classify": "Metric-R", "agent": "Agent"}
MODELS = ["ds-flash", "ds-pro", "kimi-k2.6"]
MN = {"ds-flash": "DS-Flash", "ds-pro": "DS-V4-Pro", "kimi-k2.6": "Kimi-K2.6"}
CLS = ["L", "E", "W", "A", "V"]
COVF = {"xb": "xb_s3_coverage.json", "aw": "aw_s3_coverage.json", "lego": "lego_s3_coverage.json"}

R = []
for ds in DS:
    # main_<ds>_m2.jsonl re-executes Metric and Metric-R after the compiler started rejecting `aggregate: count`
    # without group_by (validation as in production metric layers); it supersedes those substrates in main_<ds>.jsonl.
    m2 = os.path.join(RES, f"main_{ds}_m2.jsonl")
    use_m2 = os.path.exists(m2)
    for f in (f"main_{ds}.jsonl", f"main_{ds}_m2.jsonl", f"main_{ds}_agent.jsonl"):
        p = os.path.join(RES, f)
        if not os.path.exists(p):
            continue
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            if use_m2 and f == f"main_{ds}.jsonl" and r["substrate"] in ("semantic", "classify"):
                continue
            R.append(r)
# main_<ds>_g2.jsonl re-executes Graph-T and Graph-U with the uniform Cypher note (multi-type sentence on every dataset,
# as AW had from the start); it supersedes those substrates. Its cypher_nh rows (note without that sentence) are an ablation.
G2NH = []
for ds, f in [(d, f"main_{d}_{v}.jsonl") for d in DS for v in ("x", "mf")]:
    # x: materialized total-requirements relation (SQL-X, Graph-X); mf: dbt MetricFlow layer; three main models
    p = os.path.join(RES, f)
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            R.append(r)
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_g2.jsonl")
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            (G2NH if r["substrate"] == "cypher_nh" else R).append(r)
COV = {ds: json.load(open(os.path.join(HERE, "data", COVF[ds]))) for ds in DS}


def is_null(r):
    """a single NULL/NaN cell: a wrong result the caller can detect, reported apart from silent failures"""
    return r["outcome"] == "wrong" and r.get("n_rows") == 1 and all(
        str(c).strip().lower() in ("none", "nan", "null", "") for c in (r.get("rows") or [[None]])[0])
for r in R:
    r["cov"] = bool(COV[r["dataset"]].get(r["qid"], True))
    r["null"] = is_null(r)
    r["silent"] = r["outcome"] == "wrong" and not r["null"]
    r["refused"] = r["outcome"] in ("abstain",)
idx = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in R if r.get("rep", 0) == 0}
rng = random.Random(0)
md, mac = [], {}


def acc(rs):
    return sum(r["correct"] for r in rs) / len(rs) if rs else float("nan")


def mcnemar(a, b):
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n = n01 + n10
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(n01, n10) + 1)) / 2 ** n)
    return n01, n10, p


def sel(ds=None, m=None, s=None, c=None, cov=None):
    return [r for r in idx.values() if (ds is None or r["dataset"] == ds) and (m is None or r["model"] == m)
            and (s is None or r["substrate"] == s) and (c is None or r["cls"] == c) and (cov is None or r["cov"] == cov)]


def pct(x):
    return f"{100 * x:.0f}" if x == x else "--"


def texnum(v):
    """A leading minus becomes a math minus, after which LaTeX cannot break the line (a hyphen allows a break)."""
    return re.sub(r"^-(?=[0-9.])", lambda _: "\\ensuremath{-}", str(v))


def ranks(cells, low=False):
    """Competition rank of each printed value (1 = best; ties share a rank); None for non-numbers."""
    v = [float(c) if re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", c) else None for c in cells]
    return [None if x is None else 1 + sum((y < x) if low else (y > x) for y in v if y is not None) for x in v]


def marked(cells, low=False, second=True):
    """Bold the best and underline the second-best printed value."""
    return [f"\\textbf{{{c}}}" if r == 1 else f"\\underline{{{c}}}" if r == 2 and second else c
            for c, r in zip(cells, ranks(cells, low))]


def by_column(rows, low=False, second=True):
    """Apply marked() down each column of a list of rows of cells."""
    cols = [marked(list(col), low, second) for col in zip(*rows)]
    return [list(r) for r in zip(*cols)]


def count_cells(rows):
    """Per column: how often a cell is best / second best, as 'b/s'."""
    return [f"{sum(r[j] == 1 for r in rows)}/{sum(r[j] == 2 for r in rows)}" for j in range(len(rows[0]))]


def macro(name, val):
    mac[name] = re.sub(r"^[+-]0$", "0", val) if isinstance(val, str) else val


# ---------------- T1: accuracy by dataset x substrate x model ----------------
md.append("## T1 Accuracy (%), per dataset and model; pooled row per dataset")
md.append("| dataset | model | " + " | ".join(SN[s] for s in SUBS) + " |")
md.append("|---|---|" + "---|" * len(SUBS))
tex = ["\\begin{tabular}{ll" + "r" * len(SUBS) + "}", "\\toprule", " & & " + " & ".join(SN[s] for s in SUBS) + " \\\\", "\\midrule"]
rk = []   # ranks of the per-model rows, for the count row
for ds in DS:
    for m in MODELS + ["all"]:
        cells = []
        for s in SUBS:
            rs = sel(ds, None if m == "all" else m, s)
            cells.append(pct(acc(rs)) if rs else "--")
        md.append(f"| {ds} | {m} | " + " | ".join(cells) + " |")
        if m != "all":
            rk.append(ranks(cells))
        tex.append(f"{DSN[ds] if m == MODELS[0] else ''} & {'pooled' if m == 'all' else MN[m]} & " + " & ".join(marked(cells)) + " \\\\")
    tex.append("\\midrule")
tex += [" & best/2nd & " + " & ".join(count_cells(rk)) + " \\\\", "\\bottomrule", "\\end{tabular}"]
for j, s_ in enumerate(SUBS):   # best / second places per substrate over the nine model rows (for the text)
    macro(f"AccBest{SN[s_].replace('-', '')}", str(sum(r[j] == 1 for r in rk)))
    macro(f"AccSecond{SN[s_].replace('-', '')}", str(sum(r[j] == 2 for r in rk)))
open(os.path.join(PAPER, "tables", "acc.tex"), "w").write("\n".join(tex) + "\n")
for s in SUBS:
    rs = sel(s=s)
    macro(f"Acc{SN[s].replace('-', '')}", pct(acc(rs)))
    for ds in DS:
        macro(f"Acc{SN[s].replace('-', '')}{DSN[ds]}", pct(acc(sel(ds, None, s))))
macro("NRuns", f"{sum(r['substrate'] in SUBS for r in idx.values()):,}")
# truncation rule: runs that hit the output cap, re-run once with an 8,000-token cap (retry_truncated.py, apply_retries.py)
_rt = {}
for p in glob.glob(os.path.join(RES, "main_*.jsonl")):
    for l in open(p):
        r = json.loads(l); _rt[(r.get("dataset", p), r["model"], r["substrate"], r["qid"], r.get("rep", 0))] = r
_rt = [r for r in _rt.values() if r.get("retry") == "truncated" and r["model"] != "ds-flash-think"]  # thinking runs already had 8,000 tokens
macro("NRetry", f"{len(_rt):,}")
macro("NRetryClaude", pct(sum(r["model"] == "claude-opus-5.5" for r in _rt) / len(_rt)) if _rt else "--")
macro("NQuestions", str(len({(r['dataset'], r['qid']) for r in idx.values()})))

# ---------------- T2: paired tests (storage vs modeling) ----------------
pairs = [("sql", "cypher", "storage (typed)"), ("sql_u", "cypher_u", "storage (unified)"),
         ("cypher", "cypher_u", "modeling (graph)"), ("sql", "sql_u", "modeling (relational)"),
         ("sql", "semantic", "interface"), ("semantic", "classify", "structural refusal"), ("semantic", "agent", "agent")]
md.append("\n## T2 Paired McNemar (rep 0): n10 = first right & second wrong, n01 = first wrong & second right")
md.append("| dataset | model | pair | factor | acc1 | acc2 | n10 | n01 | p |")
md.append("|---|---|---|---|---|---|---|---|---|")
for ds in DS + [None]:
    for m in MODELS + [None]:
        for a, b, fac in pairs:
            keys = [(r["dataset"], r["model"], r["qid"]) for r in sel(ds, m, a) if (r["dataset"], r["model"], b, r["qid"]) in idx]
            if not keys:
                continue
            xa = [idx[(d, mm, a, q)]["correct"] for d, mm, q in keys]
            xb = [idx[(d, mm, b, q)]["correct"] for d, mm, q in keys]
            n01, n10, p = mcnemar(xa, xb)
            md.append(f"| {ds or 'all'} | {m or 'all'} | {SN[a]} vs {SN[b]} | {fac} | {pct(sum(xa) / len(xa))} | {pct(sum(xb) / len(xb))} | {n10} | {n01} | {p:.2g} |")
            if ds is None and m is None:
                k = SN[a].replace('-', '') + SN[b].replace('-', '')
                macro(f"P{k}", f"{p:.1g}" if p >= 1e-3 else "{<}10^{-3}")
                macro(f"Gap{k}", f"{abs(100 * (sum(xa) - sum(xb)) / len(xa)):.0f}")

# ---------------- T3: accuracy by class (pooled over datasets and models) ----------------
md.append("\n## T3 Accuracy by class (%), pooled")
md.append("| class | " + " | ".join(SN[s] for s in SUBS) + " |")
md.append("|---|" + "---|" * len(SUBS))
for c in CLS:
    md.append(f"| {c} | " + " | ".join(pct(acc(sel(s=s, c=c))) for s in SUBS) + " |")
# transposed in the paper (substrates as rows) so that it fits one column
tex = ["\\begin{tabular}{l" + "r" * len(CLS) + "r}", "\\toprule", " & " + " & ".join(CLS) + " & best/2nd \\\\", "\\midrule"]
CSUBS = SUBS + XSUBS + ["metricflow"]   # materialized and MetricFlow rows: same questions, three main models
rows = [[pct(acc(sel(s=s, c=c))) for c in CLS] for s in CSUBS]
rk = list(zip(*[ranks(list(col)) for col in zip(*rows)]))   # ranks within each class (column)
for s, cells, r in zip(CSUBS, by_column(rows), rk):
    if s == XSUBS[0]:
        tex.append("\\midrule")
    tex.append(f"{SN[s]} & " + " & ".join(cells) + f" & {sum(x == 1 for x in r)}/{sum(x == 2 for x in r)} \\\\")
    macro(f"ClassBest{SN[s].replace('-', '')}", str(sum(x == 1 for x in r)))
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "class.tex"), "w").write("\n".join(tex) + "\n")

# ---------------- T4: failure visibility ----------------
md.append("\n## T4 Failure visibility (pooled): failures, silent share, refusals, errors")
md.append("| substrate | runs | failures | silent | silent % of failures | single NULL | refused | error/timeout/empty |")
md.append("|---|---|---|---|---|---|---|---|")
tex = ["\\begin{tabular}{lrrrrr}", "\\toprule", " & acc. & failures & silent & NULL & silent share \\\\", "\\midrule"]
for s in SUBS:
    rs = sel(s=s)
    if not rs:
        continue
    f = [r for r in rs if not r["correct"]]
    sil = sum(r["silent"] for r in f)
    ref = sum(r["refused"] for r in f)
    nul = sum(r["null"] for r in f)
    md.append(f"| {SN[s]} | {len(rs)} | {len(f)} | {sil} | {pct(sil / len(f)) if f else '--'} | {nul} | {ref} | {len(f) - sil - ref - nul} |")
    tex.append(f"{SN[s]} & {pct(acc(rs))} & {len(f)} & {sil} & {nul} & {pct(sil / len(f)) if f else '--'}\\% \\\\")
    macro(f"Silent{SN[s].replace('-', '')}", pct(sil / len(f)) if f else "--")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "silent.tex"), "w").write("\n".join(tex) + "\n")

# ---------------- T5: coverage behaviour of Metric / Metric-R / Agent ----------------
md.append("\n## T5 Coverage behaviour (pooled): in-coverage accuracy, out-of-coverage outcomes")
md.append("| substrate | in-cov acc | out-cov correct | out-cov refused | out-cov silent | false refusals in-cov | answered acc |")
md.append("|---|---|---|---|---|---|---|")
for s in ["semantic", "classify", "agent", "sql", "sql_u"]:
    i, o = sel(s=s, cov=True), sel(s=s, cov=False)
    if not i:
        continue
    ans = [r for r in i + o if not r["refused"]]
    md.append(f"| {SN[s]} | {pct(acc(i))} ({sum(r['correct'] for r in i)}/{len(i)}) | {sum(r['correct'] for r in o)}/{len(o)} | "
              f"{sum(r['refused'] for r in o)} | {sum(r['silent'] for r in o)} | {sum(r['refused'] for r in i)} | {pct(acc(ans))} |")
    k = SN[s].replace('-', '')
    macro(f"InCov{k}", pct(acc(i)))
    macro(f"OutCorrect{k}", f"{sum(r['correct'] for r in o)}/{len(o)}")
    macro(f"OutRefused{k}", f"{sum(r['refused'] for r in o)}/{len(o)}")
    macro(f"OutSilent{k}", f"{sum(r['silent'] for r in o)}/{len(o)}")
    macro(f"FalseRef{k}", f"{sum(r['refused'] for r in i)}/{len(i)}")
    macro(f"AnsAcc{k}", pct(acc(ans)))

# ---------------- T6: agent routes (governance bypass) ----------------
A = sel(s="agent")
if A:
    md.append("\n## T6 Agent routes: share of answers produced by the governed metric layer vs raw SQL")
    md.append("| dataset | model | acc | metric route | raw-SQL route | none | bypass on in-cov | acc via metric | acc via SQL | mean turns |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for ds in DS + [None]:
        for m in MODELS + [None]:
            rs = sel(ds, m, "agent")
            if not rs:
                continue
            mt = [r for r in rs if r.get("route") == "metric"]
            fb = [r for r in rs if r.get("route") == "fallback"]
            byp = [r for r in fb if r["cov"]]
            md.append(f"| {ds or 'all'} | {m or 'all'} | {pct(acc(rs))} | {len(mt)} | {len(fb)} | {len(rs) - len(mt) - len(fb)} | "
                      f"{len(byp)}/{sum(r['cov'] for r in rs)} | {pct(acc(mt))} | {pct(acc(fb))} | {sum(len(r['attempts']) for r in rs) / len(rs):.1f} |")
    fb = [r for r in A if r.get("route") == "fallback"]
    macro("AgentBypass", pct(len(fb) / len(A)))
    macro("AgentBypassInCov", pct(sum(r["cov"] for r in fb) / max(1, sum(r["cov"] for r in A))))
    macro("AgentTurns", f"{sum(len(r['attempts']) for r in A) / len(A):.1f}")

# ---------------- T7: cost and latency ----------------
md.append("\n## T7 Cost (USD per 1k questions) and latency (s, LLM + execution), per model and substrate")
md.append("| model | " + " | ".join(SN[s] for s in SUBS) + " |")
md.append("|---|" + "---|" * len(SUBS))
for m in MODELS:
    cells, lat = [], []
    for s in SUBS:
        rs = sel(m=m, s=s)
        cells.append(f"{1000 * sum(r['usd'] for r in rs) / len(rs):.2f}" if rs else "--")
        lat.append(f"{sum(r['llm_s'] + r['exec_s'] for r in rs) / len(rs):.1f}" if rs else "--")
    md.append(f"| {m} \\$ | " + " | ".join(cells) + " |")
    md.append(f"| {m} s | " + " | ".join(lat) + " |")
# one column in the paper: substrates as rows (with the materialized substrates and MetricFlow), models as columns
tex = ["\\begin{tabular}{lrrrrrr}", "\\toprule", " & \\multicolumn{3}{c}{USD per 1k questions} & \\multicolumn{3}{c}{latency (s)} \\\\",
       "\\cmidrule(lr){2-4}\\cmidrule(lr){5-7}", " & " + " & ".join(["Flash", "Pro", "Kimi"] * 2) + " \\\\", "\\midrule"]
CSUBS = SUBS + XSUBS + ["metricflow"]
rows = []
for s in CSUBS:
    usd = [f"{1000 * sum(r['usd'] for r in rs) / len(rs):.2f}" if rs else "--" for rs in (sel(m=m, s=s) for m in MODELS)]
    lat = [f"{sum(r['llm_s'] + r['exec_s'] for r in rs) / len(rs):.1f}" if rs else "--" for rs in (sel(m=m, s=s) for m in MODELS)]
    rows.append(usd + lat)
crk = [ranks(list(col), low=True) for col in zip(*rows)]   # per column: ranks of the substrates
for s, i in (("sql_x", CSUBS.index("sql_x")),):
    macro("CostUSDTopSQLX", str(sum(c[i] <= 2 for c in crk[:3])))
    macro("CostUSDBestSQLX", str(sum(c[i] == 1 for c in crk[:3])))
for s, cells in zip(CSUBS, by_column(rows, low=True)):   # lowest cost and latency per column
    if s == XSUBS[0]:
        tex.append("\\midrule")
    tex.append(f"{SN[s]} & " + " & ".join(cells) + " \\\\")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "cost.tex"), "w").write("\n".join(tex) + "\n")
macro("TotalUSD", f"{sum(r['usd'] for r in idx.values() if r['substrate'] in SUBS):.0f}")

open(os.path.join(RES, "main_summary.md"), "w").write("\n".join(md) + "\n")
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/analyze_main.py -- do not edit by hand\n")
    for k, v in sorted(mac.items()):
        k = re.sub(r"[^A-Za-z]", "", k.replace("1", "One").replace("2", "Two"))
        f.write(f"\\newcommand{{\\{k}}}{{{texnum(v)}}}\n")
print("\n".join(md))

# ---------------- aggregates for figures ----------------
agg = {"acc": {}, "outcomes": {}}
for ds in DS:
    for s in SUBS:
        rs = sel(ds, None, s)
        if rs:
            agg["acc"][f"{ds}|{s}"] = acc(rs)
for s in SUBS:
    rs = sel(s=s)
    if rs:
        n = len(rs)
        agg["outcomes"][s] = {"correct": sum(r["correct"] for r in rs) / n,
                              "silent": sum(r["silent"] for r in rs) / n,
                              "refused": sum(r["refused"] for r in rs) / n,
                              "visible": sum((not r["correct"]) and (not r["silent"]) and (not r["refused"]) for r in rs) / n}
json.dump(agg, open(os.path.join(RES, "main_agg.json"), "w"), indent=1)

# ---------------- T8: error taxonomy (LLM-assisted, stratified sample of 40 failures per dataset x substrate) ----------------
T = []
for ds in DS:
    p = os.path.join(RES, f"tax_main_{ds}.jsonl")
    if os.path.exists(p):
        T += [json.loads(l) for l in open(p)]
if T:
    for d in T:
        if d["substrate"] in ("semantic", "classify") and not COV[d["dataset"]].get(d["qid"], True):
            d["category"] = "T10_out_of_coverage"
    groups = [("T2_traversal", "traversal scope"), ("T3_quantity", "quantity semantics"), ("T4_validity", "validity / version"),
              ("T5_mapping", "mapping / bundles"), ("T1_schema_link", "schema linking"), ("T8_dialect", "dialect / syntax"),
              ("T9_api_misuse", "interface misuse"), ("T10_out_of_coverage", "out of coverage"), ("other", "other")]
    keys = {g for g, _ in groups}
    tsubs = [s for s in ("sql", "sql_u", "cypher", "cypher_u") if any(d["substrate"] == s for d in T)]
    tex = ["\\begin{tabular}{l" + "r" * len(tsubs) + "}", "\\toprule", " & " + " & ".join(SN[s] for s in tsubs) + " \\\\", "\\midrule"]
    md.append("\n## T8 Primary error cause (% of sampled failures per substrate, pooled over datasets)")
    md.append("| cause | " + " | ".join(SN[s] for s in tsubs) + " |")
    md.append("|---|" + "---|" * len(tsubs))
    for g, lab in groups:
        cells = []
        for s in tsubs:
            ds_ = [d for d in T if d["substrate"] == s]
            n = sum((d["category"] if d["category"] in keys else "other") == g for d in ds_)
            cells.append(f"{100 * n / len(ds_):.0f}" if ds_ else "--")
        md.append(f"| {lab} | " + " | ".join(cells) + " |")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
    tex += ["\\midrule", "sampled failures & " + " & ".join(str(sum(d['substrate'] == s for d in T)) for s in tsubs) + " \\\\",
            "\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "errors.tex"), "w").write("\n".join(tex) + "\n")
    for s in tsubs:
        ds_ = [d for d in T if d["substrate"] == s]
        macro(f"Trav{SN[s].replace('-', '')}", f"{100 * sum(d['category'] == 'T2_traversal' for d in ds_) / len(ds_):.0f}")
        macro(f"Dial{SN[s].replace('-', '')}", f"{100 * sum(d['category'] == 'T8_dialect' for d in ds_) / len(ds_):.0f}")
    macro("TaxN", str(len(T)))

# second, independent labeler (another vendor's model, same prompt and sample): agreement and Cohen's kappa
T2 = []
for ds in DS:
    p = os.path.join(RES, f"tax2_main_{ds}.jsonl")
    if os.path.exists(p):
        T2 += [json.loads(l) for l in open(p)]
if T and T2:
    for d in T2:
        if d["substrate"] in ("semantic", "classify") and not COV[d["dataset"]].get(d["qid"], True):
            d["category"] = "T10_out_of_coverage"
    k1 = {(d["dataset"], d["model"], d["substrate"], d["qid"]): d["category"] for d in T}
    pairs_ = [(k1[k], d["category"]) for d in T2 for k in [(d["dataset"], d["model"], d["substrate"], d["qid"])] if k in k1]
    if pairs_:
        po = sum(a == b for a, b in pairs_) / len(pairs_)
        ca = defaultdict(int); cb_ = defaultdict(int)
        for a, b in pairs_:
            ca[a] += 1; cb_[b] += 1
        pe = sum(ca[c] * cb_[c] for c in set(ca) | set(cb_)) / len(pairs_) ** 2
        macro("TaxPairs", str(len(pairs_)))
        macro("TaxAgree", pct(po))
        macro("TaxKappa", f"{(po - pe) / (1 - pe):.2f}")
        trav = [(a == "T2_traversal", b == "T2_traversal") for a, b in pairs_]
        macro("TaxTravBoth", pct(sum(a and b for a, b in trav) / max(1, sum(a for a, _ in trav))))
        md.append(f"\n## T8b second labeler: {len(pairs_)} pairs, agreement {100 * po:.0f}%, kappa {(po - pe) / (1 - pe):.2f}")

# ---------------- robustness: perturbed XB instances for the main runs ----------------
rp = os.path.join(RES, "robust_main_xb.jsonl")
if os.path.exists(rp):
    B = [json.loads(l) for l in open(rp)]
    ok = [b for b in B if b["orig"]]
    lucky = [b for b in ok if not all(x is not False for x in b["pert"])]
    md.append(f"\n## T9 Perturbed XB instances (main runs, Agent excluded): {len(ok)} correct runs, {len(lucky)} fail on a perturbed instance")
    macro("PertCorrect", f"{len(ok):,}")
    macro("PertLucky", str(len(lucky)))

open(os.path.join(RES, "main_summary.md"), "w").write("\n".join(md) + "\n")
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/analyze_main.py -- do not edit by hand\n")
    for k, v in sorted(mac.items()):
        k = re.sub(r"[^A-Za-z]", "", k.replace("1", "One").replace("2", "Two"))
        f.write(f"\\newcommand{{\\{k}}}{{{texnum(v)}}}\n")

# ======================= additions after the simulated review =======================
# (1) template-clustered bootstrap: clusters = (dataset, template); all questions and models of a cluster move together
def cluster_boot(pairs_by_cluster, B=20000, seed=1):
    """pairs_by_cluster: {cluster: [(x, y), ...]} with x, y in {0,1}; returns (mean diff x-y, lo, hi, p_two_sided)."""
    r = random.Random(seed)
    sums = [(sum(x - y for x, y in v), len(v)) for v in pairs_by_cluster.values()]
    k = len(sums)
    obs = sum(a for a, _ in sums) / sum(n for _, n in sums)
    ds_ = []
    for _ in range(B):
        a = n = 0
        for _ in range(k):
            sa, sn = sums[r.randrange(k)]
            a += sa; n += sn
        ds_.append(a / n)
    ds_.sort()
    lo, hi = ds_[int(.025 * B)], ds_[int(.975 * B)]
    p = min(1.0, 2 * min(sum(d <= 0 for d in ds_), sum(d >= 0 for d in ds_)) / B)
    return obs, lo, hi, max(p, 1 / B)


def paired(a, b, ds=None):
    out = defaultdict(list)
    for r in sel(ds, None, a):
        k = (r["dataset"], r["model"], b, r["qid"])
        if k in idx:
            out[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
    return out


EFFECTS = [("sql", "cypher", "storage, split/typed", "SQL $-$ Graph-T"),
           ("sql_u", "cypher_u", "storage, unified", "SQL-U $-$ Graph-U"),
           ("sql_u", "sql", "modeling, relational", "SQL-U $-$ SQL"),
           ("cypher_u", "cypher", "modeling, graph", "Graph-U $-$ Graph-T")]
rows, family = [], []
for a, b, fac, lab in EFFECTS:
    cells = []
    for ds in DS + [None]:
        pc = paired(a, b, ds)
        if not pc:
            cells.append(None)
            continue
        d, lo, hi, p = cluster_boot(pc)
        cells.append((d, lo, hi, p))
        family.append(((a, b, ds), p))
    rows.append((lab, cells))
# Holm over the 16-test family
fam = sorted(family, key=lambda t: t[1])
m_ = len(fam)
holm, run = {}, 0.0
for i, (k, p) in enumerate(fam):
    run = max(run, min(1.0, (m_ - i) * p))
    holm[k] = run
tex = ["\\begin{tabular}{lrrrr}", "\\toprule", "effect (points) & XB & AW & LEGO & pooled \\\\", "\\midrule"]
md.append("\n## T10 Effects with template-clustered 95% bootstrap CIs (points; Holm-adjusted p over 16 tests)")
md.append("| effect | XB | AW | LEGO | pooled |")
md.append("|---|---|---|---|---|")
for (a, b, fac, lab), (_, cells) in zip(EFFECTS, rows):
    fm, ft = [], []
    for ds, c in zip(DS + [None], cells):
        if c is None:
            fm.append("--"); ft.append("--"); continue
        d, lo, hi, p = c
        ph = holm[(a, b, ds)]
        star = "" if ph >= 0.05 else ("$^{*}$" if ph >= 0.001 else "$^{**}$")
        fm.append(f"{100 * d:+.0f} [{100 * lo:+.0f}, {100 * hi:+.0f}] p_holm={ph:.2g}")
        z = lambda v: f"{100 * v:+.0f}".replace("-0", "0").replace("+0", "0")
        ft.append(f"{z(d)} [{z(lo)},{z(hi)}]{star}")
        key = SN[a].replace("-", "") + SN[b].replace("-", "") + (DSN[ds] if ds else "All")
        macro(f"Eff{key}", f"{100 * d:.0f}")
        macro(f"EffLo{key}", f"{100 * lo:.0f}")
        macro(f"EffHi{key}", f"{100 * hi:.0f}")
    md.append(f"| {fac} | " + " | ".join(fm) + " |")
    tex.append(f"{lab} & " + " & ".join(ft) + " \\\\")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "effects.tex"), "w").write("\n".join(tex) + "\n")
macro("NTemplates", str(len({(r["dataset"], r["tpl"]) for r in idx.values()})))

# (2) accuracy with template-clustered CIs per substrate (pooled)
for s in SUBS:
    pc = defaultdict(list)
    for r in sel(s=s):
        pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), 0))
    if pc:
        d, lo, hi, _ = cluster_boot(pc)
        k = SN[s].replace("-", "")
        macro(f"CiLo{k}", f"{100 * lo:.0f}")
        macro(f"CiHi{k}", f"{100 * hi:.0f}")

# (3) causes of SILENT failures only (free-query substrates), weighted by each stratum's failure count. Where the second
# (Claude-Opus-5.5) and third (DS-V4-Pro with thinking) labelers exist, a failure gets the majority label of the three;
# without a majority the third labeler decides.
_lab = {}
for _pre in ("tax2", "tax3"):
    for ds in DS:
        _p = os.path.join(RES, f"{_pre}_main_{ds}.jsonl")
        if os.path.exists(_p):
            for _l in open(_p):
                _d = json.loads(_l)
                _lab[(_pre, _d["dataset"], _d["model"], _d["substrate"], _d["qid"])] = _d["category"]
if T and any(k[0] == "tax3" for k in _lab):
    T = [dict(d) for d in T]
    for d in T:
        k = (d["dataset"], d["model"], d["substrate"], d["qid"])
        c2, c3 = _lab.get(("tax2",) + k), _lab.get(("tax3",) + k)
        if c2 and c3:
            votes = Counter([d["category"], c2, c3]).most_common()
            d["category"] = votes[0][0] if votes[0][1] >= 2 else c3
if T:
    tsubs = ["sql", "sql_u", "cypher", "cypher_u"]
    keys3 = [("T2_traversal", "traversal scope"), ("T3_quantity", "quantity semantics"), ("T4_validity", "validity / version"),
             ("T5_mapping", "mapping / bundles"), ("other", "other")]
    tex = ["\\begin{tabular}{l" + "r" * len(tsubs) + "}", "\\toprule", " & " + " & ".join(SN[s] for s in tsubs) + " \\\\", "\\midrule"]
    # stratum weight = (#silent failures in stratum) / (#sampled silent failures in stratum)
    nsil = defaultdict(int)
    for r in idx.values():
        if r["silent"]:
            nsil[(r["dataset"], r["substrate"])] += 1
    for g, lab in keys3:
        cells = []
        for s in tsubs:
            num = den = 0.0
            for ds in DS:
                smp = [d for d in T if d["substrate"] == s and d["dataset"] == ds and d["outcome"] == "wrong"
                       and idx.get((ds, d["model"], s, d["qid"]), {}).get("silent")]
                if not smp:
                    continue
                w = nsil[(ds, s)] / len(smp)
                cat = lambda d: d["category"] if d["category"] in {k for k, _ in keys3} else "other"
                num += w * sum(cat(d) == g for d in smp)
                den += w * len(smp)
            cells.append(f"{100 * num / den:.0f}" if den else "--")
            if g == "T2_traversal":
                macro(f"SilTrav{SN[s].replace('-', '')}", f"{100 * num / den:.0f}" if den else "--")
            if g == "T4_validity":
                macro(f"SilVal{SN[s].replace('-', '')}", f"{100 * num / den:.0f}" if den else "--")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "silent_causes.tex"), "w").write("\n".join(tex) + "\n")

# (4) agent bypass among answered runs; (5) Metric before the interface validation; (6) missing executions
if A:
    ans = [r for r in A if r.get("route") in ("metric", "fallback")]
    macro("AgentBypassAnswered", pct(sum(r["route"] == "fallback" for r in ans) / len(ans)))
    macro("AgentNoAnswer", str(len(A) - len(ans)))
    ic = [r for r in A if r["cov"]]
    macro("AgentAccMetricInCov", pct(acc([r for r in ic if r.get("route") == "metric"])))
    macro("AgentAccSQLInCov", pct(acc([r for r in ic if r.get("route") == "fallback"])))
    # paired check of the selection: the same covered questions the agent answered from raw SQL, run on forced substrates
    byq = [r for r in ic if r.get("route") == "fallback"]
    for s_ in ("semantic", "sql", "sql_u"):
        pr = [idx[(r["dataset"], r["model"], s_, r["qid"])] for r in byq if (r["dataset"], r["model"], s_, r["qid"]) in idx]
        macro(f"AgentPaired{SN[s_].replace('-', '')}", pct(acc(pr)))
for s_ in SUBS:
    f = [r for r in sel(s=s_) if not r["correct"]]
    macro(f"Null{SN[s_].replace('-', '')}", pct(sum(r["null"] for r in f) / len(f)) if f else "--")
pre = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}.jsonl")
    pre += [json.loads(l) for l in open(p) if '"substrate": "semantic"' in l]
pre = [r for r in pre if r.get("rep", 0) == 0]
macro("AccMetricPreFix", pct(sum(r["correct"] for r in pre) / len(pre)))
design = len({(r["dataset"], r["qid"]) for r in idx.values()}) * len(MODELS) * len(SUBS)
macro("NDesign", f"{design:,}")
macro("NMissing", str(design - sum(r["substrate"] in SUBS for r in idx.values())))
sil_all = sum(r["silent"] for r in idx.values() if r["substrate"] in SUBS)   # the main grid (Table VI)
fail_all = sum(not r["correct"] for r in idx.values() if r["substrate"] in SUBS)
macro("SilentAllShare", pct(sil_all / fail_all))
# output tokens (shortest-output claim)
for s in ("sql_u", "semantic"):
    rs = sel(s=s)
    macro(f"OutTok{SN[s].replace('-', '')}", f"{sum(r['out_tok'] for r in rs) / len(rs):.0f}")

# (7) pilot variants (runs_v3 = optional refusal and LLM-routed fallback; runs_v4b = registry)
pv = os.path.join(RES, "runs_v3.jsonl")
if os.path.exists(pv):
    PV = [json.loads(l) for l in open(pv)]
    covp = json.load(open(os.path.join(HERE, "data", "s3_coverage.json")))
    ab = [r for r in PV if r["substrate"] == "semantic_abs"]
    hy = [r for r in PV if r["substrate"] == "hybrid"]
    macro("PilotAbsRefusedOut", f"{sum(r['outcome'] == 'abstain' for r in ab if not covp[r['qid']])}/{sum(not covp[r['qid']] for r in ab)}")
    macro("PilotHybFallbackOut", f"{sum(r.get('route') == 'fallback' for r in hy if not covp[r['qid']])}/{sum(not covp[r['qid']] for r in hy)}")

# (8) ablations after the review: Graph-T + multi-type note (cypher_h); Metric + optional refusal (semantic_abs)
AB = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_abl.jsonl")
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            r["cov"] = bool(COV[ds].get(r["qid"], True))
            AB.append(r)
for r in G2NH:
    r["cov"] = bool(COV[r["dataset"]].get(r["qid"], True))
# the first cypher_h runs appended the note to the old (non-uniform) documentation; they are superseded by main_<ds>_g2h.jsonl
AB = [r for r in AB if r["substrate"] != "cypher_h"] + G2NH
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_g2h.jsonl")
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            r["cov"] = bool(COV[ds].get(r["qid"], True))
            AB.append(r)
if AB:
    md.append("\n## T11 Ablations (rep 0)")
    for s, base in (("cypher_nh", "cypher"), ("cypher_h", "cypher"), ("semantic_abs", "semantic")):
        rs = [r for r in AB if r["substrate"] == s]
        if not rs:
            continue
        pc = defaultdict(list)
        for r in rs:
            k = (r["dataset"], r["model"], base, r["qid"])
            if k in idx:
                pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
        if s == "cypher_nh":  # report the sentence's effect: with (main) minus without (ablation)
            pc = {c: [(y, x) for x, y in v] for c, v in pc.items()}
        d, lo, hi, p = cluster_boot(pc)
        o = [r for r in rs if not r["cov"]]
        i_ = [r for r in rs if r["cov"]]
        ref_o = sum(r["outcome"] == "abstain" for r in o)
        ref_i = sum(r["outcome"] == "abstain" for r in i_)
        md.append(f"- {s} vs {base}: n={len(rs)} models={sorted({r['model'] for r in rs})} acc={pct(acc(rs))} "
                  f"diff={100 * d:+.1f} [{100 * lo:+.1f}, {100 * hi:+.1f}] p={p:.2g}; out-cov refused {ref_o}/{len(o)}; in-cov refused {ref_i}/{len(i_)}")
        k = {"cypher_nh": "GraphTNH", "cypher_h": "GraphTH"}.get(s, "MetricA")
        macro(f"Acc{k}", pct(acc(rs)))
        macro(f"Diff{k}", f"{100 * d:+.0f}")
        macro(f"DiffLo{k}", f"{100 * lo:+.0f}")
        macro(f"DiffHi{k}", f"{100 * hi:+.0f}")
        macro(f"OutRefused{k}", f"{ref_o}/{len(o)}")
        macro(f"InRefused{k}", f"{ref_i}/{len(i_)}")
        macro(f"Models{k}", str(len({r['model'] for r in rs})))
        if s in ("cypher_nh", "cypher_h"):
            for ds in DS:
                macro(f"Acc{k}{DSN[ds]}", pct(acc([r for r in rs if r["dataset"] == ds])))
                pcd = {c: v for c, v in pc.items() if c[0] == ds}
                if pcd:
                    d_, lo_, hi_, _ = cluster_boot(pcd)
                    macro(f"Diff{k}{DSN[ds]}", f"{100 * d_:+.0f}")
                    macro(f"DiffLo{k}{DSN[ds]}", f"{100 * lo_:+.0f}")
                    macro(f"DiffHi{k}{DSN[ds]}", f"{100 * hi_:+.0f}")

# SQL against the best-documented typed graph (cypher_h)
if AB:
    ixh = dict(idx)
    ixh.update({(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in AB if r["substrate"] == "cypher_h"})
    for ds in DS + [None]:
        pc = defaultdict(list)
        for (d, m, s_, q), r in ixh.items():
            if s_ == "sql" and (ds is None or d == ds) and (d, m, "cypher_h", q) in ixh:
                pc[(d, r["tpl"])].append((int(r["correct"]), int(ixh[(d, m, "cypher_h", q)]["correct"])))
        if pc:
            e = cluster_boot(pc)
            k = "SQLGraphTH" + (DSN[ds] if ds else "All")
            macro(f"Eff{k}", f"{100 * e[0]:.0f}"); macro(f"EffLo{k}", f"{100 * e[1]:.0f}"); macro(f"EffHi{k}", f"{100 * e[2]:.0f}")

# (9) in-coverage accuracy per dataset for Metric and SQL-U
for ds in DS:
    for s_ in ("semantic", "sql_u", "sql"):
        i_ = sel(ds, None, s_, cov=True)
        if i_:
            macro(f"InCov{SN[s_].replace('-', '')}{DSN[ds]}", pct(acc(i_)))
# (10) second graph engine: Neo4j 5 (neo_t = typed, neo_u = unified), paired with Kuzu and SQL
NEO = []
NEONH = []
for ds in DS:
    # main_<ds>_neo2.jsonl: Neo4j with the uniform note (multi-type sentence); supersedes main_<ds>_neo.jsonl
    p2, p1 = (os.path.join(RES, f"main_{ds}_neo{v}.jsonl") for v in ("2", ""))
    p = p2 if os.path.exists(p2) else p1
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            r["null"] = is_null(r)
            r["silent"] = r["outcome"] == "wrong" and not r["null"]
            (NEONH if r["substrate"] == "neo_t_nh" else NEO).append(r)
if NEO:
    for a in ("neo_t", "neo_u"):
        rs = [r for r in NEO if r["substrate"] == a]
        f = [r for r in rs if not r["correct"]]
        k = "NeoT" if a == "neo_t" else "NeoU"
        macro(f"Silent{k}", pct(sum(r["silent"] for r in f) / len(f)) if f else "--")
        macro(f"Null{k}", pct(sum(r["null"] for r in f) / len(f)) if f else "--")
    # engine semantics of an empty sum: Kuzu returns NULL (counted apart), Neo4j returns 0 (a silent wrong number).
    # Two uniform rules: NULL counted as silent on Kuzu; a single 0 counted as detectable, like NULL, on both engines.
    zero = lambda r: r["outcome"] == "wrong" and r.get("n_rows") == 1 and str((r.get("rows") or [[None]])[0][0]).strip() in ("0", "0.0")
    for a, b, k in (("cypher", "neo_t", "T"), ("cypher_u", "neo_u", "U")):
        fk = [r for r in sel(s=a) if not r["correct"]]
        fn = [r for r in NEO if r["substrate"] == b and not r["correct"]]
        macro(f"SilentNullGraph{k}", pct(sum(r["silent"] or r["null"] for r in fk) / len(fk)))
        macro(f"SilentNoZeroGraph{k}", pct(sum(r["silent"] and not zero(r) for r in fk) / len(fk)))
        macro(f"SilentNoZeroNeo{k}", pct(sum(r["silent"] and not zero(r) for r in fn) / len(fn)))
    if NEONH:
        macro("AccNeoTNH", pct(acc(NEONH)))
    nx = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in NEO}
    def pairs_neo(a, b, ds=None):
        """a, b may be neo_* (from NEO) or a main substrate (from idx)."""
        src = lambda s: nx if s.startswith("neo") else idx
        out = defaultdict(list)
        for (d, m, s_, q), r in src(a).items():
            if s_ != a or (ds and d != ds):
                continue
            k = (d, m, b, q)
            if k in src(b):
                out[(d, r["tpl"])].append((int(r["correct"]), int(src(b)[k]["correct"])))
        return out
    md.append("\n## T12 Second graph engine (Neo4j 5): accuracy and paired effects (template-clustered 95% CI)")
    tex = ["\\begin{tabular}{lrrrr}", "\\toprule", "(points) & XB & AW & LEGO & pooled \\\\", "\\midrule"]
    comps = [("neo_t", None, "Neo4j typed (acc.)"), ("neo_u", None, "Neo4j unified (acc.)"),
             ("neo_t", "cypher", "Neo4j $-$ K\\`uzu, typed"), ("neo_u", "cypher_u", "Neo4j $-$ K\\`uzu, unified"),
             ("sql", "neo_t", "SQL $-$ Neo4j typed"), ("sql_u", "neo_u", "SQL-U $-$ Neo4j unified"),
             ("neo_u", "neo_t", "Neo4j unified $-$ typed")]
    for a, b, lab in comps:
        cells = []
        for ds in DS + [None]:
            if b is None:
                rs = [r for r in NEO if r["substrate"] == a and (ds is None or r["dataset"] == ds)]
                cells.append(pct(acc(rs)) if rs else "--")
                if rs:
                    macro(f"Acc{'NeoT' if a == 'neo_t' else 'NeoU'}{DSN[ds] if ds else ''}", pct(acc(rs)))
                continue
            pc = pairs_neo(a, b, ds)
            if not pc:
                cells.append("--"); continue
            d, lo, hi, p = cluster_boot(pc)
            z = lambda v: f"{100 * v:+.0f}".replace("-0", "0").replace("+0", "0")
            cells.append(f"{z(d)} [{z(lo)},{z(hi)}]")
            key = {"neo_t|cypher": "NeoKuzuT", "neo_u|cypher_u": "NeoKuzuU", "sql|neo_t": "SQLNeoT",
                   "sql_u|neo_u": "SQLUNeoU", "neo_u|neo_t": "NeoUNeoT"}[f"{a}|{b}"] + (DSN[ds] if ds else "All")
            macro(f"Eff{key}", f"{100 * d:.0f}")
            macro(f"EffLo{key}", f"{100 * lo:.0f}")
            macro(f"EffHi{key}", f"{100 * hi:.0f}")
        md.append(f"| {lab} | " + " | ".join(cells) + " |")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "neo4j.tex"), "w").write("\n".join(tex) + "\n")
    macro("NeoModels", str(len({r["model"] for r in NEO})))
    macro("NeoRuns", f"{len(NEO):,}")


def load(pattern):
    out = []
    for ds in DS:
        for p in sorted(glob.glob(os.path.join(RES, pattern.format(ds=ds)))):
            for l in open(p):
                r = json.loads(l)
                r.setdefault("dataset", ds)
                r["cov"] = bool(COV[ds].get(r["qid"], True))
                r["null"] = is_null(r)
                r["silent"] = r["outcome"] == "wrong" and not r["null"]
                r["refused"] = r["outcome"] == "abstain"
                out.append(r)
    return out


def effect(ix, a, b, ds=None):
    pc = defaultdict(list)
    for (d, m, s_, q), r in ix.items():
        if s_ == a and (ds is None or d == ds) and (d, m, b, q) in ix:
            pc[(d, r["tpl"])].append((int(r["correct"]), int(ix[(d, m, b, q)]["correct"])))
    return cluster_boot(pc) if pc else None


z = lambda v: f"{100 * v:+.0f}".replace("-0", "0").replace("+0", "0")
# (11) three further models from other vendors (gateway): GPT-6-Sol, Claude-Opus-5.5, Qwen3.8-27B (open weights)
FRM = ["gpt-6-sol", "claude-opus-5.5", "qwen3.8-27b"]
MN.update({"gpt-6-sol": "GPT-6-Sol", "claude-opus-5.5": "Claude-Opus-5.5", "qwen3.8-27b": "Qwen3.8-27B"})
FR = load("main_{ds}_fr.jsonl") + load("main_{ds}_fr_agent.jsonl") + load("main_{ds}_x_fr.jsonl") + load("main_{ds}_mf_fr.jsonl")
assert {r["model"] for r in FR} <= {"gpt-6-sol", "claude-opus-5.5", "qwen3.8-27b"}, "further-model files only"
if FR:
    fx = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in FR if r.get("rep", 0) == 0}
    FSUBS = SUBS + [x for x in XSUBS + ["metricflow"] if any(r["substrate"] == x for r in FR)]
    md.append("\n## T13 Further models (pooled over datasets): accuracy (%) and SQL-vs-graph effects (points, 95% CI)")
    tex = ["\\begin{tabular}{l" + "r" * len(FSUBS) + "}", "\\toprule", " & " + " & ".join(SN[s] for s in FSUBS) + " \\\\", "\\midrule"]
    rk = []
    for m in FRM + [None]:
        cells = [pct(acc([r for r in fx.values() if r["substrate"] == s_ and (m is None or r["model"] == m)])) for s_ in FSUBS]
        md.append(f"| {m or 'pooled'} | " + " | ".join(cells) + " |")
        if m:
            rk.append(ranks(cells))
        else:
            tex.append("\\midrule")
        tex.append((MN[m] if m else "pooled") + " & " + " & ".join(marked(cells)) + " \\\\")
        tg = {"gpt-6-sol": "GPT", "claude-opus-5.5": "Claude", "qwen3.8-27b": "Qwen"}.get(m, "")
        for s_, c in zip(FSUBS, cells):
            macro(f"FrAcc{SN[s_].replace('-', '')}{tg}", c)
    tex += ["\\midrule", "best/2nd & " + " & ".join(count_cells(rk)) + " \\\\", "\\bottomrule", "\\end{tabular}"]
    macro("FrBestSQLX", str(sum(r[FSUBS.index("sql_x")] == 1 for r in rk)) if "sql_x" in FSUBS else "--")
    open(os.path.join(PAPER, "tables", "frontier.tex"), "w").write("\n".join(tex) + "\n")
    for a, b, k in (("sql", "cypher", "SQLGraphT"), ("sql_u", "cypher_u", "SQLUGraphU"), ("sql_u", "sql", "SQLUSQL"),
                    ("cypher_u", "cypher", "GraphUGraphT"), ("sql_x", "sql_u", "SQLXSQLU"), ("cypher_x", "cypher_u", "GraphXGraphU")):
        for m in FRM + [None]:
            sub = {kk: v for kk, v in fx.items() if m is None or kk[1] == m}
            e = effect(sub, a, b)
            if e:
                tag = {"gpt-6-sol": "GPT", "claude-opus-5.5": "Claude", "qwen3.8-27b": "Qwen"}.get(m, "All")
                macro(f"FrEff{k}{tag}", f"{100 * e[0]:.0f}")
                if m is None:
                    macro(f"FrEffLo{k}", f"{100 * e[1]:.0f}"); macro(f"FrEffHi{k}", f"{100 * e[2]:.0f}")
                md.append(f"- {k} {m or 'pooled'}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}")
    # the two frontier models together, per dataset: where the storage gap remains, and whether materialization closes it
    FRONT = ("gpt-6-sol", "claude-opus-5.5")
    fxf = {kk: v for kk, v in fx.items() if kk[1] in FRONT}
    for a, b, k in (("sql", "cypher", "SQLGraphT"), ("sql_u", "cypher_u", "SQLUGraphU"), ("sql_x", "cypher_x", "SQLXGraphX"),
                    ("cypher_x", "cypher_u", "GraphXGraphU")):
        for ds in DS + [None]:
            e = effect(fxf, a, b, ds)
            if e:
                tag = DSN[ds] if ds else "All"
                macro(f"FrontEff{k}{tag}", f"{100 * e[0]:.0f}")
                macro(f"FrontEffLo{k}{tag}", f"{100 * e[1]:.0f}"); macro(f"FrontEffHi{k}{tag}", f"{100 * e[2]:.0f}")
                md.append(f"- frontier {k} {ds or 'pooled'}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}")
    for s_ in ("cypher", "cypher_u", "cypher_x", "sql_x"):
        for ds in DS:
            macro(f"FrontAcc{SN[s_].replace('-', '')}{DSN[ds]}", pct(acc([r for kk, r in fxf.items() if kk[2] == s_ and kk[0] == ds])))
            for m, tag in (("gpt-6-sol", "GPT"), ("claude-opus-5.5", "Claude")):
                macro(f"FrontAcc{SN[s_].replace('-', '')}{DSN[ds]}{tag}", pct(acc([r for kk, r in fxf.items() if kk[2] == s_ and kk[0] == ds and kk[1] == m])))
    for m, tag in (("gpt-6-sol", "GPT"), ("claude-opus-5.5", "Claude")):
        e = effect({kk: v for kk, v in fx.items() if kk[1] == m}, "cypher_u", "cypher", "lego")
        if e:
            macro(f"FrEffGraphUGraphTLEGO{tag}", f"{100 * e[0]:.0f}")
    fa = [r for r in fx.values() if r["substrate"] == "agent"]
    macro("FrAgentBypassAnswered", "--")
    if fa:
        ans = [r for r in fa if r.get("route") in ("metric", "fallback")]
        macro("FrAgentBypassAnswered", pct(sum(r["route"] == "fallback" for r in ans) / max(1, len(ans))))
        # per-model bypass share among answered runs, all six models
        bp = []
        for m, rs_ in [(m, [r for r in A if r["model"] == m]) for m in MODELS] + [(m, [r for r in fa if r["model"] == m]) for m in FRM]:
            an = [r for r in rs_ if r.get("route") in ("metric", "fallback")]
            if an:
                bp.append(100 * sum(r["route"] == "fallback" for r in an) / len(an))
                md.append(f"- agent bypass {m}: {bp[-1]:.0f}% of {len(an)} answered")
        macro("AgentBypassMin", f"{min(bp):.0f}"); macro("AgentBypassMax", f"{max(bp):.0f}")
    for s_ in ("semantic", "classify"):
        o = [r for r in fx.values() if r["substrate"] == s_ and not r["cov"]]
        macro(f"FrOutRefused{SN[s_].replace('-', '')}", f"{sum(r['refused'] for r in o)}/{len(o)}")
    macro("FrRuns", f"{len(fx):,}")

# (12) second repetition of the main grid (rep 1) for the three main models
R1 = load("main_{ds}_r1*.jsonl")
if R1:
    r1 = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in R1}
    both = [(idx[k], r1[k]) for k in r1 if k in idx]
    md.append("\n## T14 Second repetition: question-level flip rate and accuracy change per substrate (pooled)")
    tex = ["\\begin{tabular}{lrrr}", "\\toprule", " & rep 0 & rep 1 & flips (\\%) \\\\", "\\midrule"]
    for s_ in SUBS:
        bs = [(a, b) for a, b in both if a["substrate"] == s_]
        if not bs:
            continue
        fl = sum(a["correct"] != b["correct"] for a, b in bs) / len(bs)
        a0, a1 = acc([a for a, _ in bs]), acc([b for _, b in bs])
        md.append(f"| {SN[s_]} | {len(bs)} | {pct(a0)} | {pct(a1)} | flips {100 * fl:.1f}% |")
        tex.append(f"{SN[s_]} & {pct(a0)} & {pct(a1)} & {100 * fl:.0f} \\\\")
        macro(f"Flip{SN[s_].replace('-', '')}", f"{100 * fl:.0f}")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "repeat.tex"), "w").write("\n".join(tex) + "\n")
    macro("FlipAll", f"{100 * sum(a['correct'] != b['correct'] for a, b in both) / len(both):.0f}")
    macro("RepOneRuns", f"{len(both):,}")
    for a, b, lab in [(x[0], x[1], x[3]) for x in EFFECTS]:
        e0, e1 = effect(idx, a, b), effect(r1, a, b)
        if e1:
            k = SN[a].replace("-", "") + SN[b].replace("-", "")
            macro(f"RepEff{k}", f"{100 * e1[0]:.0f}")
            md.append(f"- {lab}: rep0 {z(e0[0])}, rep1 {z(e1[0])} [{z(e1[1])}, {z(e1[2])}]")

cb = os.path.join(RES, "closure_build.json")
if os.path.exists(cb):
    CB = json.load(open(cb))
    for ds in DS:
        x = CB[ds]["sql_x"]
        macro(f"XRows{DSN[ds]}", f"{x['rows']:,}")
        macro(f"XRatio{DSN[ds]}", f"{x['rows'] / x['n_edges_input']:.1f}")
        macro(f"XBuild{DSN[ds]}", f"{x['total_build_s']:.1f}")
        macro(f"XGrowth{DSN[ds]}", f"{100 * (x['file_bytes'] / x['base_file_bytes'] - 1):.0f}")
# (14) public semantic layer (dbt MetricFlow) against the self-built metric layer
if sel(s="metricflow"):
    for ds in DS + [None]:
        tag = DSN[ds] if ds else ""
        macro(f"InCovMetricFlow{tag}", pct(acc(sel(ds, None, "metricflow", cov=True))))
        macro(f"AccMetricFlow{tag}", pct(acc(sel(ds, None, "metricflow"))))
        ixc = {k: r for k, r in idx.items() if r["cov"]}
        e = effect(ixc, "metricflow", "semantic", ds)
        if e:
            k = "MetricFlowMetricCov" + (tag or "All")
            macro(f"Eff{k}", f"{100 * e[0]:.0f}"); macro(f"EffLo{k}", f"{100 * e[1]:.0f}"); macro(f"EffHi{k}", f"{100 * e[2]:.0f}")
            md.append(f"- MetricFlow vs Metric (covered) {ds or 'all'}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
    o = sel(s="metricflow", cov=False)
    macro("OutSilentMetricFlow", f"{sum(r['silent'] for r in o)}/{len(o)}")
    macro("OutCorrectMetricFlow", f"{sum(r['correct'] for r in o)}/{len(o)}")
    rs = sel(s="metricflow")
    macro("InTokMetricFlow", f"{sum(r['in_tok'] for r in rs) / len(rs):,.0f}")
    macro("InTokMetric", f"{sum(r['in_tok'] for r in sel(s='semantic')) / len(sel(s='semantic')):,.0f}")
    f = [r for r in rs if not r["correct"]]
    macro("SilentMetricFlow", pct(sum(r["silent"] for r in f) / len(f)) if f else "--")

# (13) RQ6: materialized total-requirements relation (SQL-X, Graph-X) against the best substrates
if sel(s="sql_x"):
    md.append("\n## T15 Materialized total requirements: accuracy (%) and paired effects (points, 95% CI), main models")
    tex = ["\\begin{tabular}{lrrrr}", "\\toprule", " & XB & AW & LEGO & pooled \\\\", "\\midrule"]
    XR = ("sql_u", "sql_x", "cypher_u", "cypher_x")
    xrows = [[pct(acc(sel(ds, None, s_))) for ds in DS] + [pct(acc(sel(s=s_)))] for s_ in XR]
    macro("ClosureBestSQLX", str(sum(c[XR.index("sql_x")] == 1 for c in (ranks(list(col)) for col in zip(*xrows)))))
    for s_, cells, mc in zip(XR, xrows, by_column(xrows)):   # best and second best per column
        tex.append(f"{SN[s_]} & " + " & ".join(mc) + " \\\\")
        md.append(f"| {SN[s_]} | " + " | ".join(cells) + " |")
        macro(f"Acc{SN[s_].replace('-', '')}", cells[-1])
        for ds, c in zip(DS, cells):
            macro(f"Acc{SN[s_].replace('-', '')}{DSN[ds]}", c)
    cov = {}
    for s_ in ("semantic", "sql_u", "sql_x"):
        cells = [pct(acc(sel(ds, None, s_, cov=True))) for ds in DS] + [pct(acc(sel(s=s_, cov=True)))]
        macro(f"InCov{SN[s_].replace('-', '')}", cells[-1])
        if s_ in ("semantic", "sql_x"):
            cov[s_] = cells
        for ds, c in zip(DS, cells):
            macro(f"InCov{SN[s_].replace('-', '')}{DSN[ds]}", c)
    mc = by_column([cov["semantic"], cov["sql_x"]], second=False)   # two rows: bold the better one
    tex += ["\\midrule", "Metric, covered & " + " & ".join(mc[0]) + " \\\\", "SQL-X, covered & " + " & ".join(mc[1]) + " \\\\"]
    tex.append("\\midrule")
    for a, b, lab in (("sql_x", "sql_u", "SQL-X $-$ SQL-U"), ("cypher_x", "cypher_u", "Graph-X $-$ Graph-U"),
                      ("sql_x", "cypher_x", "SQL-X $-$ Graph-X")):
        cells = []
        for ds in DS + [None]:
            e = effect(idx, a, b, ds)
            cells.append(f"{z(e[0])} [{z(e[1])},{z(e[2])}]" if e else "--")
            if e:
                k = SN[a].replace("-", "") + SN[b].replace("-", "") + (DSN[ds] if ds else "All")
                macro(f"Eff{k}", f"{100 * e[0]:.0f}"); macro(f"EffLo{k}", f"{100 * e[1]:.0f}"); macro(f"EffHi{k}", f"{100 * e[2]:.0f}")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
        md.append(f"| {lab} | " + " | ".join(cells) + " |")
    # covered questions: SQL-X against the metric layer (paired on covered questions only)
    ixc = {k: r for k, r in idx.items() if r["cov"]}
    for ds in DS + [None]:
        e = effect(ixc, "sql_x", "semantic", ds)
        if e:
            k = "SQLXMetricCov" + (DSN[ds] if ds else "All")
            macro(f"Eff{k}", f"{100 * e[0]:.0f}"); macro(f"EffLo{k}", f"{100 * e[1]:.0f}"); macro(f"EffHi{k}", f"{100 * e[2]:.0f}")
            md.append(f"- SQL-X vs Metric (covered) {ds or 'all'}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "closure.tex"), "w").write("\n".join(tex) + "\n")
    for s_ in XSUBS:
        f = [r for r in sel(s=s_) if not r["correct"]]
        macro(f"Silent{SN[s_].replace('-', '')}", pct(sum(r["silent"] for r in f) / len(f)) if f else "--")
        rs = sel(s=s_)
        macro(f"OutTok{SN[s_].replace('-', '')}", f"{sum(r['out_tok'] for r in rs) / len(rs):.0f}" if rs else "--")
    # Graph-X failures that traverse the materialized edge transitively (-[e:EXPLODES*..]->), re-multiplying totals
    gx = [r for r in sel(s="cypher_x") if not r["correct"]]
    vl = [r for r in gx if re.search(r"EXPLODES\s*\*", r["attempts"][-1]["text"])]
    macro("GraphXVarLenFail", pct(len(vl) / len(gx)) if gx else "--")
    # how SQL-X queries use the relation: alone (a lookup) or joined with order, bundle or detail tables
    sx = sel(s="sql_x")
    ux = [r for r in sx if "u_explosion" in r["attempts"][-1]["text"]]
    jx = [r for r in ux if set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", r["attempts"][-1]["text"], re.I)) - {"u_explosion"}]
    if sx and ux:
        macro("XUse", pct(len(ux) / len(sx))); macro("XUseJoin", pct(len(jx) / len(ux)))
        macro("XAccJoin", pct(acc(jx))); macro("XAccAlone", pct(acc([r for r in ux if r not in jx])))
    md.append("| class | " + " | ".join(SN[s_] for s_ in ("sql_u", "sql_x", "cypher_u", "cypher_x", "semantic")) + " |")
    for c in CLS:
        md.append(f"| {c} | " + " | ".join(pct(acc(sel(s=s_, c=c))) for s_ in ("sql_u", "sql_x", "cypher_u", "cypher_x", "semantic")) + " |")

# (15) paraphrased questions (gpt-6-sol paraphrase, claude-opus-5.5 meaning check), same gold
PA = load("main_{ds}_para.jsonl")
if PA:
    md.append("\n## T16 Paraphrased questions: accuracy on original vs paraphrased wording (paired, same model)")
    tex = ["\\begin{tabular}{lrrr}", "\\toprule", " & original & paraphrased & $\\Delta$ [95\\% CI] \\\\", "\\midrule"]
    pmods = sorted({r["model"] for r in PA})
    for s_ in ("sql", "sql_u", "cypher", "cypher_u", "semantic", "metricflow", "sql_x"):
        pc = defaultdict(list)
        for r in PA:
            k = (r["dataset"], r["model"], s_, r["qid"])
            if r["substrate"] == s_ and k in idx:
                pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
        if not pc:
            continue
        n = sum(len(v) for v in pc.values())
        a_p = sum(x for v in pc.values() for x, _ in v) / n
        a_o = sum(y for v in pc.values() for _, y in v) / n
        e = cluster_boot(pc)
        tex.append(f"{SN[s_]} & {pct(a_o)} & {pct(a_p)} & {z(e[0])} [{z(e[1])},{z(e[2])}] \\\\")
        md.append(f"| {SN[s_]} | n={n} | {pct(a_o)} | {pct(a_p)} | {z(e[0])} [{z(e[1])}, {z(e[2])}] |")
        k = SN[s_].replace("-", "")
        macro(f"ParaAcc{k}", pct(a_p)); macro(f"ParaDiff{k}", f"{100 * e[0]:+.0f}")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "para.tex"), "w").write("\n".join(tex) + "\n")
    macro("ParaModels", ", ".join(MN.get(m, m) for m in pmods))
    macro("ParaRuns", f"{len(PA):,}")
    ps = os.path.join(RES, "paraphrase_stats.json")
    if os.path.exists(ps):
        P_ = json.load(open(ps))
        macro("ParaN", str(sum(P_[d]["n"] for d in DS if d in P_ and "n" in P_[d])) if all("n" in P_.get(d, {}) for d in DS) else "577")

# (review round) DuckDB dialect note on the SQL substrates (main_<ds>_dn.jsonl), paired with the runs without it, and
# the engine sensitivity of LEGO SQL: queries with several recursive branches replayed on SQLite (replay_multibranch.py)
DN = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_dn.jsonl")
    if os.path.exists(p):
        DN += [dict(json.loads(l), dataset=ds) for l in open(p)]
DN = list({(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in DN}.values())
if DN:
    md.append("\n## DuckDB dialect note: with note vs. without (paired, same model and question)")
    for base in ("sql", "sql_u", "sql_x"):
        for ds in DS + [None]:
            pc = defaultdict(list)
            for r in DN:
                k = (r["dataset"], r["model"], base, r["qid"])
                if r["substrate"] == base + "_dn" and k in idx and (ds is None or r["dataset"] == ds):
                    pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
            if not pc:
                continue
            n = sum(len(v) for v in pc.values())
            a_n = sum(x for v in pc.values() for x, _ in v) / n
            e = cluster_boot(pc)
            k = SN[base].replace("-", "") + (DSN[ds] if ds else "")
            macro(f"DnAcc{k}", pct(a_n)); macro(f"DnEff{k}", f"{100 * e[0]:+.0f}")
            macro(f"DnEffLo{k}", f"{100 * e[1]:+.0f}"); macro(f"DnEffHi{k}", f"{100 * e[2]:+.0f}")
            md.append(f"| {SN[base]} | {DSN.get(ds, 'all')} | n={n} | {pct(a_n)} | {100 * e[0]:+.1f} [{100 * e[1]:+.1f}, {100 * e[2]:+.1f}] |")
    macro("DnModels", str(len({r["model"] for r in DN})))
rp = os.path.join(RES, "replay_multibranch.json")
if os.path.exists(rp):
    RP = json.load(open(rp))["lego/sql"]
    macro("LegoMBShare", pct(RP["affected"] / RP["runs"]))
    macro("LegoMBRecovered", str(RP["recovered"])); macro("LegoMBAffected", str(RP["affected"]))
    if "correct_if_sqlite" in RP:
        macro("LegoMBTimeout", str(RP["timeouts"]))
        macro("LegoSQLite", pct(RP["correct_if_sqlite"] / RP["runs"]))
        macro("LegoSQLiteMax", pct((RP["correct_if_sqlite"] + RP["timeouts"]) / RP["runs"]))
    md.append(f"\n## LEGO SQL multi-branch recursion replayed on SQLite: {RP}")

# (review round) Graph-X with a usage note (xtn), second repetition of SQL-X/Graph-X (xr1), DS-Flash with thinking (think),
# replication on a fresh question set (fresh), and execution latency
XTN = load("main_{ds}_xtn.jsonl")
if XTN:
    md.append("\n## Graph-X usage note (one EXPLODES edge is already the total; do not traverse it with *), paired with Graph-X")
    vlre = lambda r: bool(re.search(r"EXPLODES\s*\*", r["attempts"][-1]["text"]))
    for ds in DS + [None]:
        pc = defaultdict(list)
        for r in XTN:
            k = (r["dataset"], r["model"], "cypher_x", r["qid"])
            if k in idx and (ds is None or r["dataset"] == ds):
                pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
        if not pc:
            continue
        e = cluster_boot(pc)
        tag = DSN[ds] if ds else ""
        macro(f"XtnAcc{tag}", pct(acc([r for r in XTN if ds is None or r["dataset"] == ds])))
        macro(f"XtnEff{tag}", f"{100 * e[0]:+.0f}"); macro(f"XtnEffLo{tag}", f"{100 * e[1]:+.0f}"); macro(f"XtnEffHi{tag}", f"{100 * e[2]:+.0f}")
        md.append(f"- {ds or 'all'}: acc {pct(acc([r for r in XTN if ds is None or r['dataset'] == ds]))} vs Graph-X "
                  f"{pct(acc(sel(ds, None, 'cypher_x')))}; diff {100 * e[0]:+.1f} [{100 * e[1]:+.1f}, {100 * e[2]:+.1f}]")
    macro("XtnVarLen", pct(sum(map(vlre, XTN)) / len(XTN)))
    macro("GraphXVarLen", pct(sum(map(vlre, sel(s="cypher_x"))) / len(sel(s="cypher_x"))))
    ixt = dict(idx); ixt.update({(r["dataset"], r["model"], "cypher_x", r["qid"]): r for r in XTN})
    e = effect(ixt, "sql_x", "cypher_x")
    macro("EffSQLXGraphXtn", f"{100 * e[0]:.0f}"); macro("EffLoSQLXGraphXtn", f"{100 * e[1]:.0f}"); macro("EffHiSQLXGraphXtn", f"{100 * e[2]:.0f}")
    md.append(f"- SQL-X minus Graph-X with the note: {z(e[0])} [{z(e[1])}, {z(e[2])}]")

XR1 = load("main_{ds}_x_r1.jsonl")
if XR1:
    md.append("\n## Second repetition of SQL-X and Graph-X")
    xr = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in XR1}
    for s_ in XSUBS:
        bs = [(idx[k], r) for k, r in xr.items() if k[2] == s_ and k in idx]
        fl = sum(a["correct"] != b["correct"] for a, b in bs) / len(bs)
        macro(f"Flip{SN[s_].replace('-', '')}", f"{100 * fl:.0f}")
        macro(f"RepAcc{SN[s_].replace('-', '')}", pct(acc([b for _, b in bs])))
        md.append(f"- {SN[s_]}: n={len(bs)} rep0 {pct(acc([a for a, _ in bs]))} rep1 {pct(acc([b for _, b in bs]))} flips {100 * fl:.1f}%")
    # rep-1 effects: SQL-X and Graph-X (rep 1) against SQL-U and Graph-U (rep 1 of the main grid)
    r1x = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in load("main_{ds}_r1*.jsonl")}
    r1x.update(xr)
    for a, b in (("sql_x", "sql_u"), ("cypher_x", "cypher_u"), ("sql_x", "cypher_x")):
        e = effect(r1x, a, b)
        if e:
            k = SN[a].replace("-", "") + SN[b].replace("-", "")
            macro(f"RepEff{k}", f"{100 * e[0]:.0f}"); macro(f"RepEffLo{k}", f"{100 * e[1]:.0f}"); macro(f"RepEffHi{k}", f"{100 * e[2]:.0f}")
            md.append(f"- rep1 {k}: {z(e[0])} [{z(e[1])}, {z(e[2])}] (rep0 {z(effect(idx, a, b)[0])})")

TH8 = load("main_{ds}_think.jsonl")
trunc_ = lambda r: any(a.get("finish") == "length" for a in r.get("attempts", []))
# runs that exhausted the 8,000-token budget are re-run once with 32,000 tokens (results/retry_32k/); both budgets are reported
T32 = {}
for ds in DS:
    p = os.path.join(RES, "retry_32k", f"main_{ds}_think.jsonl")
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l); r.setdefault("dataset", ds)
            T32[(ds, r["substrate"], r["qid"])] = r
TH = [T32.get((r["dataset"], r["substrate"], r["qid"]), r) if trunc_(r) else r for r in TH8]
if TH8:
    for s_ in ("cypher", "cypher_u"):
        k = SN[s_].replace("-", "")
        lg = [r for r in TH8 if r["substrate"] == s_ and r["dataset"] == "lego"]
        macro(f"ThinkEightAcc{k}LEGO", pct(acc(lg)))
        macro(f"ThinkEightExh{k}LEGO", pct(sum(trunc_(r) and not r["correct"] for r in lg) / len(lg)))
        lg32 = [r for r in TH if r["substrate"] == s_ and r["dataset"] == "lego"]
        macro(f"ThinkExh{k}LEGO", pct(sum(trunc_(r) and not r["correct"] for r in lg32) / len(lg32)))
    macro("ThinkRetried", str(sum(trunc_(r) for r in TH8)))
    macro("ThinkReplaced", str(sum(1 for r in TH8 if trunc_(r) and (r["dataset"], r["substrate"], r["qid"]) in T32)))
    e8 = effect({(r["dataset"], "ds-flash", r["substrate"], r["qid"]): r for r in TH8}, "sql_u", "cypher_u")
    macro("ThinkEightEffSQLUGraphU", f"{100 * e8[0]:.0f}")
if TH:
    md.append("\n## DS-Flash with thinking (8,000 output tokens; runs that exhaust it re-run with 32,000) against DS-Flash without thinking (paired)")
    th = {(r["dataset"], "ds-flash", r["substrate"], r["qid"]): r for r in TH}
    for s_ in ("sql", "sql_u", "cypher", "cypher_u", "sql_x"):
        pc = defaultdict(list)
        for (d, m, ss, q), r in th.items():
            if ss == s_ and (d, m, ss, q) in idx:
                pc[(d, r["tpl"])].append((int(r["correct"]), int(idx[(d, m, ss, q)]["correct"])))
        e = cluster_boot(pc)
        k = SN[s_].replace("-", "")
        a_t = acc([r for r in TH if r["substrate"] == s_])
        macro(f"ThinkAcc{k}", pct(a_t)); macro(f"FlashAcc{k}", pct(acc(sel(m="ds-flash", s=s_))))
        macro(f"ThinkDiff{k}", f"{100 * e[0]:+.0f}"); macro(f"ThinkDiffLo{k}", f"{100 * e[1]:+.0f}"); macro(f"ThinkDiffHi{k}", f"{100 * e[2]:+.0f}")
        for ds in DS:
            macro(f"ThinkAcc{k}{DSN[ds]}", pct(acc([r for r in TH if r["substrate"] == s_ and r["dataset"] == ds])))
            macro(f"FlashAcc{k}{DSN[ds]}", pct(acc(sel(ds, "ds-flash", s_))))
        md.append(f"- {SN[s_]}: think {pct(a_t)} vs no-think {pct(acc(sel(m='ds-flash', s=s_)))}; diff {100 * e[0]:+.1f} [{100 * e[1]:+.1f}, {100 * e[2]:+.1f}]")
    # the factor effects within the thinking model
    for a, b in (("sql", "cypher"), ("sql_u", "cypher_u"), ("sql_u", "sql"), ("cypher_u", "cypher"), ("sql_x", "sql_u")):
        e = effect(th, a, b)
        k = SN[a].replace("-", "") + SN[b].replace("-", "")
        macro(f"ThinkEff{k}", f"{100 * e[0]:.0f}"); macro(f"ThinkEffLo{k}", f"{100 * e[1]:.0f}"); macro(f"ThinkEffHi{k}", f"{100 * e[2]:.0f}")
        md.append(f"- think {k}: {z(e[0])} [{z(e[1])}, {z(e[2])}]; DS-Flash without thinking "
                  f"{z(effect({kk: v for kk, v in idx.items() if kk[1] == 'ds-flash'}, a, b)[0])}")
    macro("ThinkOutTok", f"{sum(r['out_tok'] for r in TH) / len(TH):,.0f}")
    macro("FlashOutTok", f"{sum(r['out_tok'] for r in sel(m='ds-flash') if r['substrate'] in ('sql', 'sql_u', 'cypher', 'cypher_u', 'sql_x')) / sum(1 for r in sel(m='ds-flash') if r['substrate'] in ('sql', 'sql_u', 'cypher', 'cypher_u', 'sql_x')):,.0f}")
    macro("ThinkUSD", f"{1000 * sum(r['usd'] for r in TH) / len(TH):.2f}")
    macro("ThinkRuns", f"{len(TH):,}")

FRESH = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_fresh.jsonl")
    if os.path.exists(p):
        cv = json.load(open(os.path.join(HERE, "data", f"{ds}_s3_coverage_fresh.json")))
        rows_ = {}
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            r["cov"] = bool(cv.get(r["qid"], True))
            rows_[(ds, r["model"], r["substrate"], r["qid"])] = r
        FRESH += list(rows_.values())
if FRESH:
    fz = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in FRESH}
    FSB = ["sql", "sql_u", "cypher_u", "semantic", "sql_x"]
    md.append("\n## Replication on a fresh question set (seed 20261108, main-set instances excluded): accuracy (%), main set in brackets")
    tex = ["\\begin{tabular}{lrrrr}", "\\toprule", " & XB & AW & LEGO & pooled \\\\", "\\midrule"]
    for s_ in FSB:
        cells, mcells = [], []
        for ds in DS + [None]:
            rs = [r for r in FRESH if r["substrate"] == s_ and (ds is None or r["dataset"] == ds)]
            cells.append(pct(acc(rs))); mcells.append(pct(acc(sel(ds, None, s_))))
            macro(f"FreshAcc{SN[s_].replace('-', '')}{DSN[ds] if ds else ''}", pct(acc(rs)))
        tex.append(f"{SN[s_]} & " + " & ".join(f"{c} ({mc})" for c, mc in zip(cells, mcells)) + " \\\\")
        md.append(f"| {SN[s_]} | " + " | ".join(f"{c} ({mc})" for c, mc in zip(cells, mcells)) + " |")
    tex.append("\\midrule")
    for a, b, lab in (("sql_u", "sql", "SQL-U $-$ SQL"), ("sql_u", "cypher_u", "SQL-U $-$ Graph-U"), ("sql_x", "sql_u", "SQL-X $-$ SQL-U")):
        cells = []
        for ds in DS + [None]:
            e, e0 = effect(fz, a, b, ds), effect(idx, a, b, ds)
            cells.append(f"{z(e[0])} [{z(e[1])},{z(e[2])}]" if e else "--")
            if e:
                k = SN[a].replace("-", "") + SN[b].replace("-", "") + (DSN[ds] if ds else "All")
                macro(f"FreshEff{k}", f"{100 * e[0]:.0f}"); macro(f"FreshEffLo{k}", f"{100 * e[1]:.0f}"); macro(f"FreshEffHi{k}", f"{100 * e[2]:.0f}")
                md.append(f"- fresh {k}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}; main set {z(e0[0])}")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
    i_ = [r for r in FRESH if r["substrate"] == "semantic" and r["cov"]]
    o_ = [r for r in FRESH if r["substrate"] == "semantic" and not r["cov"]]
    macro("FreshInCovMetric", pct(acc(i_)))
    macro("FreshOutSilentMetric", pct(sum(r["outcome"] == "wrong" and not is_null(r) for r in o_) / len(o_)) if o_ else "--")
    macro("FreshInCovSQLX", pct(acc([r for r in FRESH if r["substrate"] == "sql_x" and r["cov"]])))
    tex.append("Metric, covered & " + " & ".join(pct(acc([r for r in i_ if ds is None or r["dataset"] == ds])) + f" ({pct(acc(sel(ds, None, 'semantic', cov=True)))})" for ds in DS + [None]) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "fresh.tex"), "w").write("\n".join(tex) + "\n")
    macro("FreshQuestions", str(len({(r["dataset"], r["qid"]) for r in FRESH})))
    macro("FreshRuns", f"{len(FRESH):,}")
    for ds in DS:
        macro(f"FreshQ{DSN[ds]}", str(len({r["qid"] for r in FRESH if r["dataset"] == ds})))

# execution latency of the generated query (engine time only, final attempt), main grid and SQL-X/Graph-X
md.append("\n## Execution time of the final query (ms): median and 95th percentile")
for s_ in SUBS[:-1] + XSUBS:
    ex = sorted(r["attempts"][-1].get("exec_s", r["exec_s"]) for r in sel(s=s_) if r["attempts"])
    if ex:
        k = SN[s_].replace("-", "")
        p50, p95 = ex[len(ex) // 2], ex[int(.95 * (len(ex) - 1))]
        macro(f"ExecMed{k}", f"{1000 * p50:.0f}"); macro(f"ExecPninetyfive{k}", f"{1000 * p95:,.0f}")
        md.append(f"| {SN[s_]} | {1000 * p50:.0f} | {1000 * p95:.0f} |")

# equivalence (TOST at +-5 points) for the "match" claims: the 90% template-clustered interval must lie inside (-5, +5)
def ci90(pc, B=20000, seed=1):
    r = random.Random(seed)
    sums = [(sum(x - y for x, y in v), len(v)) for v in pc.values()]
    k = len(sums)
    ds_ = []
    for _ in range(B):
        a = n = 0
        for _ in range(k):
            sa, sn = sums[r.randrange(k)]
            a += sa; n += sn
        ds_.append(a / n)
    ds_.sort()
    return ds_[int(.05 * B)], ds_[int(.95 * B)]


def pc_of(ix, a, b, ds=None):
    pc = defaultdict(list)
    for (d, m, s_, q), r in ix.items():
        if s_ == a and (ds is None or d == ds) and (d, m, b, q) in ix:
            pc[(d, r["tpl"])].append((int(r["correct"]), int(ix[(d, m, b, q)]["correct"])))
    return pc


md.append("\n## Equivalence at +-5 points (90% template-clustered interval)")
tost = [("SQLXMetricCov", {k: r for k, r in idx.items() if r["cov"]}, "sql_x", "semantic")]
if FR:
    tost.append(("FrontSQLXGraphX", fxf, "sql_x", "cypher_x"))
    for m, tg in (("gpt-6-sol", "GPT"), ("claude-opus-5.5", "Claude"), ("qwen3.8-27b", "Qwen")):
        fm_ = {kk: v for kk, v in fx.items() if kk[1] == m}
        best = max((s_ for s_ in FSUBS if s_ != "sql_x"), key=lambda s_: acc([r for kk, r in fm_.items() if kk[2] == s_]))
        tost.append((f"SQLXBest{tg}", fm_, "sql_x", best))
for k, ix, a, b in tost:
    lo, hi = ci90(pc_of(ix, a, b))
    macro(f"TostLo{k}", f"{100 * lo:+.1f}"); macro(f"TostHi{k}", f"{100 * hi:+.1f}")
    md.append(f"- {k} ({a} vs {b}): 90% CI [{100 * lo:+.1f}, {100 * hi:+.1f}] -> {'equivalent' if -.05 < lo and hi < .05 else 'not shown equivalent'}")

# cost of the substrates outside Table XII, and the total spend of the study (every recorded execution, including
# the pilot, re-runs, ablations and the review round; the gateway models are priced at 0 and reported in tokens)
for s_ in ("sql_u", "sql_x", "cypher_x", "metricflow"):
    rs = sel(s=s_)
    if rs:
        macro(f"CostK{SN[s_].replace('-', '')}", f"{1000 * sum(r['usd'] for r in rs) / len(rs):.2f}")
if FR:
    frs = list(fx.values())
    macro("FrTokPerQ", f"{sum(r['in_tok'] + r['out_tok'] for r in frs) / len(frs):,.0f}")
    macro("FrTokTotal", f"{sum(r['in_tok'] + r['out_tok'] for r in frs) / 1e6:.0f}")
_all = {}
for p in glob.glob(os.path.join(RES, "main_*.jsonl")) + glob.glob(os.path.join(RES, "runs_*.jsonl")):
    for l in open(p):
        r = json.loads(l)
        _all[(p, r.get("dataset"), r["model"], r["substrate"], r["qid"], r.get("rep", 0))] = r.get("usd", 0)
for p in glob.glob(os.path.join(RES, "retry", "*.jsonl")):  # the truncated originals that a re-run replaced
    if not os.path.exists(os.path.join(RES, "pre_retry", os.path.basename(p))):
        continue
    rr = {(r["model"], r["substrate"], r["qid"], r["rep"]) for r in map(json.loads, open(p))}
    for r in map(json.loads, open(os.path.join(RES, "pre_retry", os.path.basename(p)))):
        if (r["model"], r["substrate"], r["qid"], r["rep"]) in rr and any(a.get("finish") == "length" for a in r.get("attempts", [])):
            _all[("pre", p, r.get("dataset"), r["model"], r["substrate"], r["qid"], r["rep"])] = r.get("usd", 0)
macro("StudyUSD", f"{sum(_all.values()):,.0f}")
macro("StudyRuns", f"{len(_all):,}")

# (the refusal-utility analysis is in analyze_v4.py)
open(os.path.join(RES, "main_summary.md"), "w").write("\n".join(md) + "\n")
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/analyze_main.py -- do not edit by hand\n")
    for k, v in sorted(mac.items()):
        k = re.sub(r"[^A-Za-z]", "", k.replace("1", "One").replace("2", "Two"))
        f.write(f"\\newcommand{{\\{k}}}{{{texnum(v)}}}\n")
