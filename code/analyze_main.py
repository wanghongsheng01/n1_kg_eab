"""Main-study analysis: results/main_*.jsonl -> results/main_summary.md, paper/numbers.tex, paper/tables/*.tex.

Every number quoted in the paper comes from this script (numbers.tex macros or generated tables).
"""
import glob, json, os, random, re
from collections import defaultdict
from math import comb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES, PAPER = os.path.join(HERE, "results"), os.path.join(HERE, "paper")
os.makedirs(os.path.join(PAPER, "tables"), exist_ok=True)
DS = ["xb", "aw", "lego"]
DSN = {"xb": "XB", "aw": "AW", "lego": "LEGO"}
SUBS = ["sql", "sql_u", "cypher", "cypher_u", "semantic", "classify", "agent"]
SN = {"sql": "SQL", "sql_u": "SQL-U", "cypher": "Graph-T", "cypher_u": "Graph-U", "semantic": "Metric",
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
COV = {ds: json.load(open(os.path.join(HERE, "data", COVF[ds]))) for ds in DS}
for r in R:
    r["cov"] = bool(COV[r["dataset"]].get(r["qid"], True))
    r["silent"] = r["outcome"] == "wrong"
    r["refused"] = r["outcome"] in ("abstain",)
idx = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in R if r.get("rep", 0) == 0}
rng = random.Random(0)
md, mac = [], {}


def acc(rs):
    return sum(r["correct"] for r in rs) / len(rs) if rs else float("nan")


def ci(xs, B=2000):
    if not xs:
        return float("nan"), float("nan")
    n = len(xs)
    m = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return m[int(.025 * B)], m[int(.975 * B)]


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


def macro(name, val):
    mac[name] = val


# ---------------- T1: accuracy by dataset x substrate x model ----------------
md.append("## T1 Accuracy (%), per dataset and model; pooled row per dataset")
md.append("| dataset | model | " + " | ".join(SN[s] for s in SUBS) + " |")
md.append("|---|---|" + "---|" * len(SUBS))
tex = ["\\begin{tabular}{ll" + "r" * len(SUBS) + "}", "\\toprule", " & & " + " & ".join(SN[s] for s in SUBS) + " \\\\", "\\midrule"]
for ds in DS:
    for m in MODELS + ["all"]:
        cells = []
        for s in SUBS:
            rs = sel(ds, None if m == "all" else m, s)
            cells.append(pct(acc(rs)) if rs else "--")
        md.append(f"| {ds} | {m} | " + " | ".join(cells) + " |")
        tex.append(f"{DSN[ds] if m == MODELS[0] else ''} & {'pooled' if m == 'all' else MN[m]} & " + " & ".join(
            (f"\\textbf{{{c}}}" if m == "all" else c) for c in cells) + " \\\\")
    tex.append("\\midrule" if ds != DS[-1] else "\\bottomrule")
tex.append("\\end{tabular}")
open(os.path.join(PAPER, "tables", "acc.tex"), "w").write("\n".join(tex) + "\n")
for s in SUBS:
    rs = sel(s=s)
    macro(f"Acc{SN[s].replace('-', '')}", pct(acc(rs)))
    for ds in DS:
        macro(f"Acc{SN[s].replace('-', '')}{DSN[ds]}", pct(acc(sel(ds, None, s))))
macro("NRuns", f"{len(idx):,}")
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
tex = ["\\begin{tabular}{l" + "r" * len(CLS) + "}", "\\toprule", " & " + " & ".join(CLS) + " \\\\", "\\midrule"]
for s in SUBS:
    tex.append(f"{SN[s]} & " + " & ".join(pct(acc(sel(s=s, c=c))) for c in CLS) + " \\\\")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "class.tex"), "w").write("\n".join(tex) + "\n")

# ---------------- T4: failure visibility ----------------
md.append("\n## T4 Failure visibility (pooled): failures, silent share, refusals, errors")
md.append("| substrate | runs | failures | silent | silent % of failures | refused | error/timeout/empty |")
md.append("|---|---|---|---|---|---|---|")
tex = ["\\begin{tabular}{lrrrr}", "\\toprule", " & acc. & failures & silent & silent share \\\\", "\\midrule"]
for s in SUBS:
    rs = sel(s=s)
    if not rs:
        continue
    f = [r for r in rs if not r["correct"]]
    sil = sum(r["silent"] for r in f)
    ref = sum(r["refused"] for r in f)
    md.append(f"| {SN[s]} | {len(rs)} | {len(f)} | {sil} | {pct(sil / len(f)) if f else '--'} | {ref} | {len(f) - sil - ref} |")
    tex.append(f"{SN[s]} & {pct(acc(rs))} & {len(f)} & {sil} & {pct(sil / len(f)) if f else '--'}\\% \\\\")
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
tex = ["\\begin{tabular}{l" + "r" * len(SUBS) + "}", "\\toprule", " & " + " & ".join(SN[s] for s in SUBS) + " \\\\", "\\midrule"]
for m in MODELS:
    cells, lat = [], []
    for s in SUBS:
        rs = sel(m=m, s=s)
        cells.append(f"{1000 * sum(r['usd'] for r in rs) / len(rs):.2f}" if rs else "--")
        lat.append(f"{sum(r['llm_s'] + r['exec_s'] for r in rs) / len(rs):.1f}" if rs else "--")
    md.append(f"| {m} \\$ | " + " | ".join(cells) + " |")
    md.append(f"| {m} s | " + " | ".join(lat) + " |")
    tex.append(f"{MN[m]} (\\$) & " + " & ".join(cells) + " \\\\")
    tex.append(f"{MN[m]} (s) & " + " & ".join(lat) + " \\\\")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "cost.tex"), "w").write("\n".join(tex) + "\n")
macro("TotalUSD", f"{sum(r['usd'] for r in idx.values()):.0f}")

open(os.path.join(RES, "main_summary.md"), "w").write("\n".join(md) + "\n")
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/analyze_main.py -- do not edit by hand\n")
    for k, v in sorted(mac.items()):
        k = re.sub(r"[^A-Za-z]", "", k.replace("1", "One").replace("2", "Two"))
        f.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
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
        f.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")

# ======================= additions after the simulated review =======================
# (1) template-clustered bootstrap: clusters = (dataset, template); all questions and models of a cluster move together
def cluster_boot(pairs_by_cluster, B=4000, seed=1):
    """pairs_by_cluster: {cluster: [(x, y), ...]} with x, y in {0,1}; returns (mean diff x-y, lo, hi, p_two_sided)."""
    r = random.Random(seed)
    keys = list(pairs_by_cluster)
    tot = sum(len(v) for v in pairs_by_cluster.values())
    obs = sum(x - y for v in pairs_by_cluster.values() for x, y in v) / tot
    ds_ = []
    for _ in range(B):
        smp = [keys[r.randrange(len(keys))] for _ in keys]
        n = sum(len(pairs_by_cluster[k]) for k in smp)
        ds_.append(sum(x - y for k in smp for x, y in pairs_by_cluster[k]) / n)
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
        d, lo, hi, _ = cluster_boot(pc, B=2000)
        k = SN[s].replace("-", "")
        macro(f"CiLo{k}", f"{100 * lo:.0f}")
        macro(f"CiHi{k}", f"{100 * hi:.0f}")

# (3) causes of SILENT failures only (free-query substrates), weighted by each stratum's failure count
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
                smp = [d for d in T if d["substrate"] == s and d["dataset"] == ds and d["outcome"] == "wrong"]
                if not smp:
                    continue
                w = nsil[(ds, s)] / len(smp)
                cat = lambda d: d["category"] if d["category"] in {k for k, _ in keys3} else "other"
                num += w * sum(cat(d) == g for d in smp)
                den += w * len(smp)
            cells.append(f"{100 * num / den:.0f}" if den else "--")
            if g == "T2_traversal":
                macro(f"SilTrav{SN[s].replace('-', '')}", f"{100 * num / den:.0f}" if den else "--")
        tex.append(f"{lab} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    open(os.path.join(PAPER, "tables", "silent_causes.tex"), "w").write("\n".join(tex) + "\n")

# (4) agent bypass among answered runs; (5) Metric before the interface validation; (6) missing executions
if A:
    ans = [r for r in A if r.get("route") in ("metric", "fallback")]
    macro("AgentBypassAnswered", pct(sum(r["route"] == "fallback" for r in ans) / len(ans)))
    macro("AgentNoAnswer", str(len(A) - len(ans)))
pre = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}.jsonl")
    pre += [json.loads(l) for l in open(p) if '"substrate": "semantic"' in l]
pre = [r for r in pre if r.get("rep", 0) == 0]
macro("AccMetricPreFix", pct(sum(r["correct"] for r in pre) / len(pre)))
design = len({(r["dataset"], r["qid"]) for r in idx.values()}) * len(MODELS) * len(SUBS)
macro("NDesign", f"{design:,}")
macro("NMissing", str(design - len(idx)))
sil_all = sum(r["silent"] for r in idx.values())
fail_all = sum(not r["correct"] for r in idx.values())
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
if AB:
    abx = {(r["dataset"], r["model"], r["substrate"], r["qid"]): r for r in AB}
    md.append("\n## T11 Ablations (rep 0)")
    for s, base in (("cypher_h", "cypher"), ("semantic_abs", "semantic")):
        rs = [r for r in AB if r["substrate"] == s]
        if not rs:
            continue
        pc = defaultdict(list)
        for r in rs:
            k = (r["dataset"], r["model"], base, r["qid"])
            if k in idx:
                pc[(r["dataset"], r["tpl"])].append((int(r["correct"]), int(idx[k]["correct"])))
        d, lo, hi, p = cluster_boot(pc)
        o = [r for r in rs if not r["cov"]]
        i_ = [r for r in rs if r["cov"]]
        ref_o = sum(r["outcome"] == "abstain" for r in o)
        ref_i = sum(r["outcome"] == "abstain" for r in i_)
        md.append(f"- {s} vs {base}: n={len(rs)} models={sorted({r['model'] for r in rs})} acc={pct(acc(rs))} "
                  f"diff={100 * d:+.1f} [{100 * lo:+.1f}, {100 * hi:+.1f}] p={p:.2g}; out-cov refused {ref_o}/{len(o)}; in-cov refused {ref_i}/{len(i_)}")
        k = "GraphTH" if s == "cypher_h" else "MetricA"
        macro(f"Acc{k}", pct(acc(rs)))
        macro(f"Diff{k}", f"{100 * d:+.0f}")
        macro(f"DiffLo{k}", f"{100 * lo:+.0f}")
        macro(f"DiffHi{k}", f"{100 * hi:+.0f}")
        macro(f"OutRefused{k}", f"{ref_o}/{len(o)}")
        macro(f"InRefused{k}", f"{ref_i}/{len(i_)}")
        macro(f"Models{k}", str(len({r['model'] for r in rs})))
        if s == "cypher_h":
            for ds in DS:
                macro(f"AccGraphTH{DSN[ds]}", pct(acc([r for r in rs if r["dataset"] == ds])))

# (9) in-coverage accuracy per dataset for Metric and SQL-U
for ds in DS:
    for s_ in ("semantic", "sql_u", "sql"):
        i_ = sel(ds, None, s_, cov=True)
        if i_:
            macro(f"InCov{SN[s_].replace('-', '')}{DSN[ds]}", pct(acc(i_)))
# (10) second graph engine: Neo4j 5 (neo_t = typed, neo_u = unified), paired with Kuzu and SQL
NEO = []
for ds in DS:
    p = os.path.join(RES, f"main_{ds}_neo.jsonl")
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            r.setdefault("dataset", ds)
            NEO.append(r)
if NEO:
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

open(os.path.join(RES, "main_summary.md"), "w").write("\n".join(md) + "\n")
with open(os.path.join(PAPER, "numbers.tex"), "w") as f:
    f.write("% generated by code/analyze_main.py -- do not edit by hand\n")
    for k, v in sorted(mac.items()):
        k = re.sub(r"[^A-Za-z]", "", k.replace("1", "One").replace("2", "Two"))
        f.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
