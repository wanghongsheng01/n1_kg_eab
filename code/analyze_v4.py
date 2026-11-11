"""Final-round analysis (2026-11-10): the experiments requested by the third mock review.
results/main_*.jsonl -> paper/numbers_v4.tex (macros), results/v4_summary.md.

It reuses the data loading of analyze_main.py verbatim (everything before its first table), so the main-grid records are
identical. Blocks: thinking for the three main models, the fresh question set on every substrate, the few-shot condition,
agent variants, three-labeler agreement (and the human audit when present), equivalence tests for parity statements,
both repetitions pooled, and the refusal-utility decision aid.
"""
import json, os, random, re
from collections import defaultdict, Counter

CODE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(CODE, "analyze_main.py")).read()
exec(compile(_src[:_src.index("# ---------------- T1:")], "analyze_main.py(head)", "exec"))  # R, idx, COV, sel, acc, pct, macro, ...
V4, OUTMD = {}, []
mac = V4  # analyze_main's macro() writes into the global `mac`


def cluster_boot(pc, B=20000, seed=1, level=.95):
    """template-clustered bootstrap of the mean paired difference (as in analyze_main.py); returns (d, lo, hi, p)"""
    r = random.Random(seed)
    sums = [(sum(x - y for x, y in v), len(v)) for v in pc.values()]
    k = len(sums)
    obs = sum(a for a, _ in sums) / sum(n for _, n in sums)
    bs = []
    for _ in range(B):
        a = n = 0
        for _ in range(k):
            sa, sn = sums[r.randrange(k)]
            a += sa; n += sn
        bs.append(a / n)
    bs.sort()
    t = (1 - level) / 2
    p = min(1.0, 2 * min(sum(d <= 0 for d in bs), sum(d >= 0 for d in bs)) / B)
    return obs, bs[int(t * B)], bs[int((1 - t) * B)], max(p, 1 / B)


def pcs(ix, a, b, ds=None, ix_b=None, models=None):
    """paired clusters (dataset, template) of substrate a in ix against substrate b in ix_b (default ix)"""
    ix_b = ix if ix_b is None else ix_b
    pc = defaultdict(list)
    for (d, m, s_, q), r in ix.items():
        if s_ == a and (ds is None or d == ds) and (models is None or m in models):
            k = (d, m, b, q)
            if k in ix_b:
                pc[(d, r["tpl"])].append((int(r["correct"]), int(ix_b[k]["correct"])))
    return pc


def eff(*a, **k):
    pc = pcs(*a, **k)
    return cluster_boot(pc) if pc else None


def tost(*a, margin=.05, **k):
    """equivalence at +-margin: the 90% clustered interval lies inside (-margin, +margin)"""
    pc = pcs(*a, **k)
    if not pc:
        return None
    d, lo, hi, _ = cluster_boot(pc, level=.90)
    return d, lo, hi, (-margin < lo and hi < margin)


def z(v):
    return f"{100 * v:+.0f}".replace("+0", "0").replace("-0", "0")


def fmt0(e):
    return f"{z(e[0])} [{z(e[1])}, {z(e[2])}]" if e else "--"


def mk(name, e, sign=False):
    if e:
        f = (lambda v: f"{100 * v:+.0f}") if sign else (lambda v: f"{100 * v:.0f}")
        macro(name, f(e[0])); macro(name + "Lo", f(e[1])); macro(name + "Hi", f(e[2]))


_GWX = {tuple(k) for k in json.load(open(os.path.join(RES, "kimi_gateway_excluded.json")))} \
    if os.path.exists(os.path.join(RES, "kimi_gateway_excluded.json")) else set()   # gateway_kimi_keys.py


def loadf(pattern, cov=None, rep=0):
    """records of results/<pattern> (one per execution, the last one wins), flagged like analyze_main.py;
    Kimi-K2.6 records served through the gateway become the separate model "kimi-k2.6-gw" (channel_check.py: not
    equivalent to the vendor API), so they never enter a Kimi-K2.6 cell"""
    out = {}
    for ds in DS:
        p = os.path.join(RES, pattern.format(ds=ds))
        if not os.path.exists(p):
            continue
        cv = COV[ds] if cov is None else json.load(open(os.path.join(HERE, "data", cov.format(ds=ds))))
        for l in open(p):
            r = json.loads(l)
            if int(r.get("rep", 0)) != rep:
                continue
            if (os.path.basename(p), r.get("dataset", ds), r["model"], r["substrate"], r["qid"], r.get("rep", 0)) in _GWX:
                r["model"] = "kimi-k2.6-gw"
            r.setdefault("dataset", ds)
            r["cov"] = bool(cv.get(r["qid"], True))
            r["null"] = is_null(r)
            r["silent"] = r["outcome"] == "wrong" and not r["null"]
            r["refused"] = r["outcome"] == "abstain"
            out[(ds, r["model"], r["substrate"], r["qid"])] = r
    return out


K = lambda s: {"cypher_x_tn": "GraphXtn"}.get(s) or re.sub("[^A-Za-z]", "", SN.get(s, s))   # macro-safe substrate names
MK = {"ds-flash": "Flash", "ds-pro": "Pro", "kimi-k2.6": "Kimi"}   # macro-safe model names (no digits)
EFF = [("sql", "cypher", "SQLGraphT"), ("sql_u", "cypher_u", "SQLUGraphU"), ("sql_u", "sql", "SQLUSQL"),
       ("cypher_u", "cypher", "GraphUGraphT"), ("sql_x", "sql_u", "SQLXSQLU")]
SN.update({"neo_t": "Neo-T", "neo_u": "Neo-U", "cypher_x_tn": "Graph-X+", "agent_strict": "Agent-S", "agent_unified": "Agent-U"})

# ---------------- (1) thinking enabled for the three main models ----------------
trunc_ = lambda r: any(a.get("finish") == "length" for a in r.get("attempts", []))
TH = {}
th8 = loadf("main_{ds}_think.jsonl")   # DS-Flash: 8,000 tokens, exhausted runs re-run with 32,000 (results/retry_32k)
r32 = loadf(os.path.join("retry_32k", "main_{ds}_think.jsonl"))
for k, r in th8.items():
    TH[(k[0], "ds-flash", k[2], k[3])] = r32.get(k, r) if trunc_(r) else r
for m, f in (("ds-pro", "main_{ds}_think_ds-pro.jsonl"), ("kimi-k2.6", "main_{ds}_think_kimi.jsonl")):
    for k, r in loadf(f).items():
        TH[(k[0], m, k[2], k[3])] = r   # 32,000 output tokens from the start
OUTMD.append("## Thinking enabled (three main models; base model with thinking disabled in brackets)")
THS = ["sql", "sql_u", "cypher", "cypher_u", "sql_x"]
for m in MODELS:
    n = sum(1 for k in TH if k[1] == m)
    cells = []
    for s_ in THS:
        rs = [r for k, r in TH.items() if k[1] == m and k[2] == s_]
        cells.append(f"{pct(acc(rs))} ({pct(acc(sel(m=m, s=s_)))})")
        macro(f"ThAcc{K(s_)}{MK[m]}", pct(acc(rs)))
    OUTMD.append(f"- {MN[m]} ({n} runs, {sum(trunc_(r) and not r['correct'] for k, r in TH.items() if k[1] == m)} exhausted): "
                 + ", ".join(f"{SN[s]} {c}" for s, c in zip(THS, cells)))
for s_ in THS:
    rs = [r for k, r in TH.items() if k[2] == s_]
    macro(f"ThAcc{K(s_)}", pct(acc(rs)))
    mk(f"ThGain{K(s_)}", eff(TH, s_, s_, ix_b=idx), sign=True)
for a, b, nm in EFF:
    e = eff(TH, a, b)
    mk(f"ThEff{nm}", e)
    t = tost(TH, a, b)
    OUTMD.append(f"- thinking pooled {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}; 90% [{z(t[1])}, {z(t[2])}] equiv5={t[3]}; "
                 f"without thinking {z(eff(idx, a, b, models=MODELS)[0])}; per model "
                 + ", ".join(f"{MN[m]} {z(eff(TH, a, b, models={m})[0])}" for m in MODELS if pcs(TH, a, b, models={m})))
    if t:
        macro(f"ThTost{nm}Lo", f"{100 * t[1]:+.1f}"); macro(f"ThTost{nm}Hi", f"{100 * t[2]:+.1f}")
    for m in MODELS:
        e_ = eff(TH, a, b, models={m})
        if e_:
            mk(f"ThEff{nm}{MK[m]}", e_)
    for ds in DS:
        mk(f"ThEff{nm}{DSN[ds]}", eff(TH, a, b, ds))
macro("ThRuns", f"{len(TH):,}")
macro("ThRunsNew", f"{sum(1 for k in TH if k[1] != 'ds-flash'):,}")
macro("ThOutTok", f"{sum(r['out_tok'] for r in TH.values()) / len(TH):,.0f}")
macro("ThUSD", f"{sum(r['usd'] for r in TH.values()):,.0f}")
for s_ in ("cypher", "cypher_u"):
    lg = [r for k, r in TH.items() if k[0] == "lego" and k[2] == s_]
    macro(f"ThAcc{K(s_)}LEGO", pct(acc(lg)))
    macro(f"ThExh{K(s_)}LEGO", pct(sum(trunc_(r) and not r["correct"] for r in lg) / len(lg)) if lg else "--")

# ---------------- (2) fresh question set on every substrate ----------------
FZ = {}
for f in ("main_{ds}_fresh.jsonl", "main_{ds}_fresh2.jsonl", "main_{ds}_fresh3.jsonl", "main_{ds}_fresh_neo.jsonl",
          "main_{ds}_fresh_agent.jsonl"):
    FZ.update(loadf(f, cov="{ds}_s3_coverage_fresh.json"))
# main-set counterparts of the substrates outside idx: Neo4j (neo2 supersedes neo), Graph-X with the note, MetricFlow
MAINX = dict(idx)
MAINX.update({k: r for k, r in loadf("main_{ds}_neo.jsonl").items() if k[2] in ("neo_t", "neo_u")})
MAINX.update({k: r for k, r in loadf("main_{ds}_neo2.jsonl").items() if k[2] in ("neo_t", "neo_u")})
MAINX.update(loadf("main_{ds}_xtn.jsonl"))
FSUB = ["sql", "sql_u", "cypher", "cypher_u", "neo_t", "neo_u", "semantic", "classify", "metricflow", "agent", "sql_x",
        "cypher_x", "cypher_x_tn"]
OUTMD.append("\n## Fresh question set, every substrate: accuracy % fresh (main), main models")
tex = ["\\begin{tabular}{lrrlrr}", "\\toprule", "Substrate & Main & Fresh & Substrate & Main & Fresh \\\\", "\\midrule"]
cells = []
for s_ in FSUB:
    f_ = [r for k, r in FZ.items() if k[2] == s_ and k[1] in MODELS]
    dsf = {k[0] for k in FZ if k[2] == s_}   # compare on the datasets the fresh set covers for this substrate
    m_ = [r for k, r in MAINX.items() if k[2] == s_ and k[1] in MODELS and k[0] in dsf]
    if not f_:
        continue
    macro(f"FzAcc{K(s_)}", pct(acc(f_))); macro(f"MainAcc{K(s_)}", pct(acc(m_)))
    cells.append((SN[s_], pct(acc(m_)), pct(acc(f_))))
    OUTMD.append(f"| {SN[s_]} | {pct(acc(f_))} ({pct(acc(m_))}) | n={len(f_)} |")
half = (len(cells) + 1) // 2
for i in range(half):
    a = cells[i]; b = cells[i + half] if i + half < len(cells) else ("", "", "")
    tex.append(f"{a[0]} & {a[1]} & {a[2]} & {b[0]} & {b[1]} & {b[2]} \\\\")
tex += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "fresh_all.tex"), "w").write("\n".join(tex) + "\n")
for a, b, nm in EFF + [("neo_u", "neo_t", "NeoUNeoT"), ("sql_u", "neo_u", "SQLUNeoU"), ("sql", "neo_t", "SQLNeoT"),
                       ("sql_x", "cypher_x_tn", "SQLXGraphXtn")]:
    e = eff(FZ, a, b, models=set(MODELS))
    if e:
        mk(f"FzEff{nm}", e)
        OUTMD.append(f"- fresh {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}; main {z(eff(MAINX, a, b, models=MODELS)[0]) if pcs(MAINX, a, b, models=MODELS) else '--'}")
fi = [r for k, r in FZ.items() if k[2] == "classify" and not r["cov"] and k[1] in MODELS]
macro("FzOutRefusedRateMetricR", pct(sum(r["refused"] for r in fi) / len(fi)) if fi else "--")
fo = [r for k, r in FZ.items() if k[2] == "semantic" and not r["cov"] and k[1] in MODELS]
macro("FzOutSilentRateMetric", pct(sum(r["silent"] for r in fo) / len(fo)) if fo else "--")
fa = [r for k, r in FZ.items() if k[2] == "agent" and k[1] in MODELS]
if fa:
    ans = [r for r in fa if r.get("route")]
    macro("FzAgentBypass", pct(sum(r["route"] == "fallback" for r in ans) / len(ans)))
macro("FzRuns", f"{sum(1 for k in FZ if k[1] in MODELS):,}")
macro("FzMaxDiff", str(max(abs(int(a) - int(b)) for _, a, b in cells if a.isdigit() and b.isdigit())))
macro("FzSubs", str(len(cells)))
# Kimi-K2.6 served through the gateway: its own column, never merged with the vendor-API cells
GW = {k: r for k, r in FZ.items() if k[1] == "kimi-k2.6-gw"}
macro("GwRuns", f"{len(GW):,}")
macro("GwAccNeo", pct(acc([r for k, r in GW.items() if k[2] in ("neo_t", "neo_u")])) if GW else "--")
macro("GwAccNeoVendor", pct(acc([r for k, r in FZ.items() if k[1] == "kimi-k2.6" and k[2] in ("neo_t", "neo_u")])))
_cc = os.path.join(RES, "channel_check.json")
if os.path.exists(_cc):
    cc = json.load(open(_cc))
    macro("ChanN", str(cc["n"])); macro("ChanAgreeVendor", pct(cc["vendor_rep0_vs_rep1"]))
    macro("ChanAgreeGw", pct(cc["gateway_vs_vendor_rep0"])); macro("ChanAccGw", pct(cc["acc_gateway"]))
    macro("ChanAccVendor", pct(cc["acc_rep0"]))
_gt = os.path.join(RES, "main_aw_think_kimi_gateway_excluded.jsonl")
if os.path.exists(_gt):
    import statistics
    g = [json.loads(l) for l in open(_gt)]
    macro("GwThRuns", str(len(g)))
    macro("GwThOutMed", f"{statistics.median(r['out_tok'] for r in g):,.0f}")
    macro("ThKimiOutMed", f"{statistics.median(r['out_tok'] for k, r in TH.items() if k[1] == 'kimi-k2.6'):,.0f}")
    OUTMD.append(f"- Kimi-K2.6 gateway-served: fresh Neo4j {mac['GwAccNeo']}% (vendor {mac['GwAccNeoVendor']}%), n={len(GW)}; "
                 f"thinking via gateway n={len(g)}, median output tokens {mac['GwThOutMed']} vs vendor {mac['ThKimiOutMed']}")

# ---------------- (3) few-shot condition ----------------
FS = loadf("main_{ds}_fs.jsonl")
FSX = {(d, m, s_[:-3], q): r for (d, m, s_, q), r in FS.items()}   # keyed by the base substrate
if FSX:
    OUTMD.append("\n## Few-shot (main questions; 3 worked examples of other templates from the fresh set): accuracy % few-shot (zero-shot)")
    dss = sorted({k[0] for k in FSX}, key=DS.index)
    macro("FsDatasets", ", ".join(DSN[d] for d in dss))
    for s_ in ("sql", "sql_u", "cypher", "cypher_u"):
        rs = [r for k, r in FSX.items() if k[2] == s_]
        zs = [idx[k] for k in FSX if k[2] == s_ and k in idx]
        macro(f"FsAcc{K(s_)}", pct(acc(rs))); macro(f"FsZeroAcc{K(s_)}", pct(acc(zs)))
        e = eff(FSX, s_, s_, ix_b=idx)
        mk(f"FsGain{K(s_)}", e, sign=True)
        OUTMD.append(f"- {SN[s_]}: {pct(acc(rs))} ({pct(acc(zs))}), gain {z(e[0])} [{z(e[1])}, {z(e[2])}]; per dataset "
                     + ", ".join(f"{DSN[d]} {pct(acc([r for k, r in FSX.items() if k[2] == s_ and k[0] == d]))}" for d in dss))
    for a, b, nm in EFF[:4]:
        e = eff(FSX, a, b)
        e0 = eff({k: r for k, r in idx.items() if k[0] in dss}, a, b)
        mk(f"FsEff{nm}", e)
        OUTMD.append(f"- few-shot {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}; zero-shot same datasets {z(e0[0])} [{z(e0[1])}, {z(e0[2])}]")
        for d in dss:
            mk(f"FsEff{nm}{DSN[d]}", eff(FSX, a, b, d))
    # share of graph failures that are parse/bind (dialect) errors, zero-shot vs few-shot
    for s_ in ("cypher", "cypher_u"):
        for lab, src in (("Fs", [r for k, r in FSX.items() if k[2] == s_]), ("Zero", [idx[k] for k in FSX if k[2] == s_ and k in idx])):
            f_ = [r for r in src if not r["correct"]]
            macro(f"{lab}ErrShare{K(s_)}", pct(sum(r["outcome"] == "error" for r in f_) / len(f_)) if f_ else "--")
    macro("FsRuns", f"{len(FSX):,}")

# ---------------- (4) agent variants ----------------
AG = dict(idx)
for v in ("strict", "unified"):
    AG.update(loadf(f"main_{{ds}}_agent_{v}.jsonl"))
OUTMD.append("\n## Agent variants: accuracy, share of answers from raw SQL, accuracy by route")
for s_ in ("agent", "agent_strict", "agent_unified"):
    rs = [r for k, r in AG.items() if k[2] == s_ and k[1] in MODELS]
    if not rs:
        continue
    ans = [r for r in rs if r.get("route")]
    byp = [r for r in ans if r["route"] == "fallback"]
    gov = [r for r in ans if r["route"] == "metric"]
    cov_ans = [r for r in ans if r["cov"]]
    k_ = K(s_).replace("Agent", "Ag")
    macro(f"{k_}Acc", pct(acc(rs))); macro(f"{k_}Bypass", pct(len(byp) / len(ans)))
    macro(f"{k_}BypassCov", pct(sum(r["route"] == "fallback" for r in cov_ans) / len(cov_ans)))
    macro(f"{k_}AccSQL", pct(acc(byp))); macro(f"{k_}AccMetric", pct(acc(gov)))
    OUTMD.append(f"- {SN[s_]}: acc {pct(acc(rs))}, raw-SQL share {pct(len(byp) / len(ans))} (covered {pct(sum(r['route'] == 'fallback' for r in cov_ans) / len(cov_ans))}), "
                 f"acc via SQL {pct(acc(byp))}, via metric {pct(acc(gov))}, n={len(rs)}")
    if s_ != "agent":
        mk(f"{k_}Gain", eff(AG, s_, "agent", models=MODELS), sign=True)
# the bypass cost with an interval: on covered questions, agent answers taken from raw SQL vs the forced metric layer
pc = defaultdict(list)
for (d, m, s_, q), r in idx.items():
    if s_ == "agent" and r.get("route") == "fallback" and r["cov"] and (d, m, "semantic", q) in idx:
        pc[(d, r["tpl"])].append((int(r["correct"]), int(idx[(d, m, "semantic", q)]["correct"])))
if pc:
    e = cluster_boot(pc)
    mk("AgBypassCost", e, sign=True)
    OUTMD.append(f"- bypassed covered answers vs Metric on the same runs: {z(e[0])} [{z(e[1])}, {z(e[2])}] n={sum(map(len, pc.values()))}")

# ---------------- (5) failure-cause labels: three labelers and the human audit ----------------
def labels(prefix):
    out = {}
    for ds in DS:
        p = os.path.join(RES, f"{prefix}_main_{ds}.jsonl")
        if os.path.exists(p):
            for l in open(p):
                d = json.loads(l)
                out[(d["dataset"], d["model"], d["substrate"], d["qid"])] = d["category"]
    return out


def kappa(pairs):
    po = sum(a == b for a, b in pairs) / len(pairs)
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / len(pairs) ** 2
    return po, (po - pe) / (1 - pe)


L1, L2, L3 = labels("tax"), labels("tax2"), labels("tax3")
FREEQ = ("sql", "sql_u", "cypher", "cypher_u")
if L3:
    keys = [k for k in L1 if k in L2 and k in L3]
    for nm, (x, y) in {"OneTwo": (L1, L2), "OneThree": (L1, L3), "TwoThree": (L2, L3)}.items():
        po, kp = kappa([(x[k], y[k]) for k in keys])
        macro(f"Kappa{nm}", f"{kp:.2f}"); macro(f"Agree{nm}", pct(po))
    kps = [float(mac[f"Kappa{nm}"]) for nm in ("OneTwo", "OneThree", "TwoThree")]
    macro("KappaMin", f"{min(kps):.2f}"); macro("KappaMax", f"{max(kps):.2f}")
    maj = {}
    for k in keys:
        c = Counter([L1[k], L2[k], L3[k]]).most_common()
        maj[k] = c[0][0] if c[0][1] >= 2 else None
    macro("TaxMajorityShare", pct(sum(v is not None for v in maj.values()) / len(maj)))
    macro("TaxThree", str(len(keys)))
    for s_ in FREEQ:
        ks = [k for k in keys if k[2] == s_ and maj[k]]
        macro(f"TravMaj{K(s_)}", pct(sum(maj[k] == "T2_traversal" for k in ks) / len(ks)) if ks else "--")
        macro(f"DialMaj{K(s_)}", pct(sum(maj[k] == "T8_dialect" for k in ks) / len(ks)) if ks else "--")
        OUTMD.append(f"- majority label {SN[s_]}: traversal {pct(sum(maj[k] == 'T2_traversal' for k in ks) / len(ks))}%, "
                     f"dialect {pct(sum(maj[k] == 'T8_dialect' for k in ks) / len(ks))}% (DS-Flash: traversal "
                     f"{pct(sum(L1[k] == 'T2_traversal' for k in ks) / len(ks))}%)")
    OUTMD.append(f"- three labelers on {len(keys)} failures: kappa 1-2 {mac['KappaOneTwo']}, 1-3 {mac['KappaOneThree']}, 2-3 {mac['KappaTwoThree']}; majority exists for {mac['TaxMajorityShare']}%")
HA = next((p for p in (os.path.join(HERE, "R2_sprint", "human_audit"), os.path.join(RES, "human_audit")) if os.path.isdir(p)), "")
if os.path.exists(os.path.join(HA, "audit_120_labelled.xlsx")):
    import openpyxl
    key = {d["id"]: d for d in json.load(open(os.path.join(HA, "audit_120_key.json")))}
    ws = openpyxl.load_workbook(os.path.join(HA, "audit_120_labelled.xlsx")).active
    hum = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] in key and row[7]:
            d = key[row[0]]
            hum[(d["dataset"], d["model"], d["substrate"], d["qid"])] = (row[7].strip(), str(row[8] or "").strip().lower())
    for nm, L in (("One", L1), ("Two", L2), ("Three", L3)):
        pr = [(hum[k][0], L[k]) for k in hum if k in L]
        if pr:
            po, kp = kappa(pr)
            macro(f"HumKappa{nm}", f"{kp:.2f}"); macro(f"HumAgree{nm}", pct(po))
    if L3:   # the human label against the three-labeler majority used in Table VII
        pr = [(hum[k][0], maj[k] or L3[k]) for k in hum if k in maj]
        po, kp = kappa(pr)
        macro("HumKappaMaj", f"{kp:.2f}"); macro("HumAgreeMaj", pct(po)); macro("HumMajN", str(len(pr)))
    p2 = os.path.join(HA, "audit_120_labelled_2.xlsx")   # optional second annotator on part of the items
    if os.path.exists(p2):
        h2 = {}
        for row in openpyxl.load_workbook(p2).active.iter_rows(min_row=2, values_only=True):
            if row[0] in key and row[7]:
                d = key[row[0]]
                h2[(d["dataset"], d["model"], d["substrate"], d["qid"])] = row[7].strip()
        pr = [(hum[k][0], h2[k]) for k in h2 if k in hum]
        if pr:
            po, kp = kappa(pr)
            macro("HumHumKappa", f"{kp:.2f}"); macro("HumHumAgree", pct(po)); macro("HumHumN", str(len(pr)))
            OUTMD.append(f"- second annotator n={len(pr)}: kappa with the first {kp:.2f}, agreement {pct(po)}%")
    macro("HumN", str(len(hum)))
    macro("HumTrav", pct(sum(v[0] == "T2_traversal" for v in hum.values()) / len(hum)))
    macro("HumDial", pct(sum(v[0] == "T8_dialect" for v in hum.values()) / len(hum)))
    wr = [v[1] for v in hum.values() if v[1] in ("yes", "no")]
    macro("HumReallyWrong", pct(sum(v == "yes" for v in wr) / len(wr)) if wr else "--")
    OUTMD.append(f"- human audit n={len(hum)}: kappa vs DS-Flash {mac.get('HumKappaOne')}, vs Claude {mac.get('HumKappaTwo')}, vs DS-Pro {mac.get('HumKappaThree')}; really wrong {mac['HumReallyWrong']}%")

# ---------------- (6) equivalence tests for the parity statements; both repetitions pooled ----------------
OUTMD.append("\n## Equivalence (90% clustered interval inside +-5 points)")
NEO = dict(idx)
NEO.update({k: r for k, r in loadf("main_{ds}_neo2.jsonl").items() if k[2] in ("neo_t", "neo_u")})
for ds in DS + [None]:
    for a, b, nm in (("sql", "neo_t", "SQLNeoT"), ("sql_u", "neo_u", "SQLUNeoU")):
        e = eff(NEO, a, b, ds, models=MODELS)
        t = tost(NEO, a, b, ds, models=MODELS)
        if e:
            k_ = f"Neo{nm}{DSN[ds] if ds else 'All'}"
            mk(k_, e, sign=True)
            macro(k_ + "Equiv", "yes" if t[3] else "no")
            OUTMD.append(f"- {k_}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g}; 90% [{z(t[1])}, {z(t[2])}] equiv={t[3]}")
# both repetitions: rep 1 of the main grid (and agent) joins rep 0; clusters keep both repetitions together
R1X = {}
for f in ("main_{ds}_r1.jsonl", "main_{ds}_r1_agent.jsonl", "main_{ds}_x_r1.jsonl"):
    R1X.update(loadf(f, rep=1))
BOTH = dict(idx)
BOTH.update({(d, m + "#1", s_, q): r for (d, m, s_, q), r in R1X.items()})
for a, b, nm in EFF:
    e = eff(BOTH, a, b, models=set(MODELS) | {m + "#1" for m in MODELS})
    mk(f"BothEff{nm}", e)
    OUTMD.append(f"- both repetitions {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}] p={e[3]:.2g} (rep 0: {z(eff(idx, a, b, models=MODELS)[0])})")

# ---------------- (6a) further models: Neo4j, and Qwen3.8-27B with thinking ----------------
FRM = ["gpt-6-sol", "claude-opus-5.5", "qwen3.8-27b"]
FRB = {}
for f in ("main_{ds}_fr.jsonl", "main_{ds}_x_fr.jsonl"):
    FRB.update(loadf(f))
FRN = dict(FRB)
_NEO = {k: r for k, r in loadf("main_{ds}_fr_neo.jsonl").items() if k[2] in ("neo_t", "neo_u")}
# Claude-Opus-5.5's truncated LEGO Neo4j runs re-run through a third-party provider (run_v4h.sh) enter only if the
# paired channel check (channel_check_t1688.py) finds that provider equivalent to the gateway on the same cells
_T1 = os.path.join(RES, "channel_check_t1688.json")
T1688 = json.load(open(_T1)) if os.path.exists(_T1) else None
_t1 = loadf("retry_t1688/main_{ds}_fr_neo.jsonl") if T1688 and T1688["merge"] else {}
_NEO.update({k: r for k, r in _t1.items() if k in _NEO and trunc_(_NEO[k])})
if T1688:
    macro("TOneN", str(T1688["n"])); macro("TOneAgree", pct(T1688["agreement"]))
    macro("TOneAccT", pct(T1688["acc_t1688"])); macro("TOneAccG", pct(T1688["acc_gateway"])); macro("TOneRuns", str(len(_t1)))
macro("FrTOneSentence", (f" Claude-Opus-5.5's {len(_t1)} truncated LEGO runs were re-run through a second provider after a paired check on "
                         f"{T1688['n']} runs of the same cells found it equivalent to the gateway (verdict agreement {pct(T1688['agreement'])}\\%, "
                         f"accuracy {pct(T1688['acc_t1688'])}\\% vs.\\ {pct(T1688['acc_gateway'])}\\%).") if _t1 else "")
_have = defaultdict(set)
for d, m, s_, q in _NEO:
    _have[(d, m)].add((s_, q))
# a (dataset, model) cell enters only when both Neo4j graphs ran on every question (Qwen3.8-27B's shared endpoint did not finish AW and LEGO)
_full = {dm for dm, v in _have.items() if len(v) == 2 * max(len({q for _, q in v}), len({q for (d2, m2, s2, q) in FRB if (d2, m2) == dm and s2 == "sql"}))}
FRN.update({k: r for k, r in _NEO.items() if (k[0], k[1]) in _full})
macro("FrNeoQwenDS", ", ".join(DSN[d] for d in DS if (d, "qwen3.8-27b") in _full) or "none")
if any(k[2] == "neo_t" for k in FRN):
    for s_ in ("neo_t", "neo_u"):
        macro(f"FrNeoAcc{K(s_)}", pct(acc([r for k, r in FRN.items() if k[2] == s_])))
        for m in ("gpt-6-sol", "claude-opus-5.5"):
            macro(f"FrNeoAcc{K(s_)}{ {'gpt-6-sol': 'GPT', 'claude-opus-5.5': 'Claude'}[m] }", pct(acc([r for k, r in FRN.items() if k[2] == s_ and k[1] == m])))
    for a, b, nm in (("sql", "neo_t", "SQLNeoT"), ("sql_u", "neo_u", "SQLUNeoU"), ("neo_u", "neo_t", "NeoUNeoT")):
        mk(f"FrNeoEff{nm}", eff(FRN, a, b), sign=True)
        for ds in DS:
            mk(f"FrNeoEff{nm}{DSN[ds]}", eff(FRN, a, b, ds), sign=True)
        e = eff(FRN, a, b)
        OUTMD.append(f"- further models on Neo4j {nm}: {fmt0(e)}; per dataset "
                     + ", ".join(f"{DSN[d]} {fmt0(eff(FRN, a, b, d))}" for d in DS))
    macro("FrNeoRuns", f"{sum(1 for k in FRN if k[2] in ('neo_t', 'neo_u')):,}")
QT = {k: r for k, r in loadf("main_{ds}_think_qwen.jsonl").items()}
QT = {(d, "qwen3.8-27b", s_, q): r for (d, m, s_, q), r in QT.items()}


def complete(ix):
    """keep only questions run on all five thinking substrates (a run still in progress covers a random question sample)"""
    have = defaultdict(set)
    for d, m, s_, q in ix:
        have[(d, m, q)].add(s_)
    return {k: r for k, r in ix.items() if have[(k[0], k[1], k[3])] >= set(THS)}


QT = complete(QT)
if QT:
    for s_ in THS:
        macro(f"QwThAcc{K(s_)}", pct(acc([r for k, r in QT.items() if k[2] == s_])))
        macro(f"QwAcc{K(s_)}", pct(acc([FRB[k] for k in QT if k[2] == s_ and k in FRB])))
    for a, b, nm in EFF:
        mk(f"QwThEff{nm}", eff(QT, a, b))
        e = eff(QT, a, b)
        if e:
            OUTMD.append(f"- Qwen thinking {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}]; without thinking {fmt0(eff(FRB, a, b, models={'qwen3.8-27b'}))}")
    macro("QwThRuns", f"{len(QT):,}")
    macro("QwThQuestions", f"{len({(k[0], k[3]) for k in QT}):,}")
GLT = complete({(d, "glm-5.3", s_, q): r for (d, m, s_, q), r in loadf("main_{ds}_think_glm.jsonl").items()})   # GLM-5.3, thinking
if GLT:
    for s_ in THS:
        macro(f"GlThAcc{K(s_)}", pct(acc([r for k, r in GLT.items() if k[2] == s_])))
    for a, b, nm in EFF:
        mk(f"GlThEff{nm}", eff(GLT, a, b))
        e = eff(GLT, a, b)
        if e:
            OUTMD.append(f"- GLM-5.3 thinking {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
    t = tost(GLT, "cypher_u", "cypher")
    if t:
        macro("GlThTostGraphUGraphTLo", f"{100 * t[1]:+.1f}"); macro("GlThTostGraphUGraphTHi", f"{100 * t[2]:+.1f}")
    macro("GlThRuns", f"{len(GLT):,}")
    macro("GlThOutTok", f"{sum(r['out_tok'] for r in GLT.values()) / len(GLT):,.0f}")
KT = complete({(d, "kimi-k3.1-pro", s_, q): r for (d, m, s_, q), r in loadf("main_{ds}_think_kimi3.jsonl").items()})   # Kimi K3.1 Pro (Ark)
if KT:
    for s_ in THS:
        macro(f"KtThAcc{K(s_)}", pct(acc([r for k, r in KT.items() if k[2] == s_])))
    for a, b, nm in EFF:
        mk(f"KtThEff{nm}", eff(KT, a, b))
        e = eff(KT, a, b)
        if e:
            OUTMD.append(f"- Kimi K3.1 Pro thinking {nm}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
    macro("KtThRuns", f"{len(KT):,}")
    macro("KtThOutTok", f"{sum(r['out_tok'] for r in KT.values()) / len(KT):,.0f}")
# two further thinking models on Volcengine Ark (run_v4g.sh), each its own column: GLM-5.3-Flash (Ark) is a different model
# and channel from the gateway's GLM-5.3 and never merged with it; Doubao-Seed-2.1-Pro (ByteDance) adds a seventh vendor.
# (GLM-5.2 on Ark, main_*_think_glm52.jsonl, reasoned for minutes per call and was stopped after a few runs; not analysed.)
G2T = complete({(d, "glm-5.3-flash-ark", s_, q): r for (d, m, s_, q), r in loadf("main_{ds}_think_glm53f.jsonl").items()})
DBT = complete({(d, "doubao-seed-2.1-pro", s_, q): r for (d, m, s_, q), r in loadf("main_{ds}_think_doubao.jsonl").items()})
for pre, nm, ix in (("GlFlTh", "GLM-5.3-Flash (Ark)", G2T), ("DbTh", "Doubao-Seed-2.1-Pro", DBT)):
    if not ix:
        continue
    for s_ in THS:
        macro(f"{pre}Acc{K(s_)}", pct(acc([r for k, r in ix.items() if k[2] == s_])))
    for a, b, en in EFF:
        mk(f"{pre}Eff{en}", eff(ix, a, b))
        e = eff(ix, a, b)
        if e:
            OUTMD.append(f"- {nm} thinking {en}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
    macro(f"{pre}Runs", f"{len(ix):,}")
    macro(f"{pre}OutTok", f"{sum(r['out_tok'] for r in ix.values()) / len(ix):,.0f}")
# coverage of the three further thinking models: complete datasets, or the seeded random sample finished so far
_nq = {d: sum(1 for _ in open(os.path.join(HERE, "data", f"{d}_questions.jsonl"))) for d in DS}
_cov = []
for nm, ix in (("Qwen3.8-27B", QT), ("GLM-5.3", GLT), ("Kimi-K3.1-Pro", KT), ("GLM-5.3-Flash (Ark)", G2T), ("Doubao-Seed-2.1-Pro", DBT)):
    if not ix:
        continue
    qs = {(k[0], k[3]) for k in ix}
    full = [d for d in DS if sum(q[0] == d for q in qs) == _nq[d]]
    part = [d for d in DS if d not in full and any(q[0] == d for q in qs)]
    if len(full) == len(DS):
        _cov.append(f"{nm} on every question")
    elif not full:
        _cov.append(f"{nm} on a seeded random sample of {len(qs):,} questions")
    else:
        _cov.append(f"{nm} on every {' and '.join(DSN[d] for d in full)} question"
                    + "".join(f" and a seeded random sample of {sum(q[0] == d for q in qs):,} {DSN[d]} questions" for d in part))
macro("ThFurtherCoverage", "; ".join(_cov) + "; each sampled question ran on all five substrates")
# where the store gap of the further thinking models lies: per-dataset SQL minus Graph-T (point estimates); LEGO only for
# models with at least 30 complete LEGO questions, so that GLM-5.3-Flash (Ark), stopped after a few LEGO questions, is not read as LEGO evidence
_g_xa, _g_lg = [], []
for ix in (GLT, KT, G2T, DBT):
    if not ix:
        continue
    for d in ("xb", "aw"):
        e = eff(ix, "sql", "cypher", d)
        if e:
            _g_xa.append(round(100 * e[0]))
    if len({k[3] for k in ix if k[0] == "lego"}) >= 30:
        e = eff(ix, "sql", "cypher", "lego")
        if e:
            _g_lg.append(round(100 * e[0]))
if _g_xa and _g_lg:
    macro("ThFurGapXBAWLo", str(min(_g_xa))); macro("ThFurGapXBAWHi", str(max(_g_xa)))
    macro("ThFurGapLEGOLo", str(min(_g_lg))); macro("ThFurGapLEGOHi", str(max(_g_lg)))
    macro("GlFlThLEGOQ", str(len({k[3] for k in G2T if k[0] == "lego"})))
# Kimi-K2.6 runs of the final round that hit the output cap and could not be re-run (vendor account ended)
_kt = 0
for f in ("fs", "fresh2", "fresh3", "fresh_neo", "fresh_agent", "agent_strict", "agent_unified"):
    for r in loadf(f"main_{{ds}}_{f}.jsonl").values():
        _kt += r["model"] == "kimi-k2.6" and trunc_(r)
macro("KimiTruncKept", str(_kt))
# further-model Neo4j runs that hit the output cap and were not re-run (Qwen3.8-27B is not re-run; Claude's LEGO re-runs await the gateway)
_ft = sum(trunc_(r) for r in _NEO.values())
macro("FrTruncKeptSentence", f" Likewise, {_ft} truncated Neo4j runs of the further models keep their first result." if _ft else "")
# Kimi-K2.6 with thinking: complete on XB, part of AW (vendor account exhausted; the author decided against a top-up)
kt = [k for k in TH if k[1] == "kimi-k2.6"]
macro("ThKimiRuns", f"{len(kt):,}")
macro("ThKimiAW", f"{sum(k[0] == 'aw' for k in kt):,}")
macro("ThKimiAWShare", pct(sum(k[0] == "aw" for k in kt) / (len(THS) * sum(1 for _ in open(os.path.join(HERE, "data", "aw_questions.jsonl"))))))
# the claim "no model, engine, prompt or reasoning setting makes the graph significantly more accurate than SQL":
# every SQL-minus-graph contrast, per condition, model and dataset (and pooled), whose 95% interval lies below zero
COND = [("main", idx, [("sql", "cypher"), ("sql_u", "cypher_u")]), ("neo4j", NEO, [("sql", "neo_t"), ("sql_u", "neo_u")]),
        ("few-shot", FSX, [("sql", "cypher"), ("sql_u", "cypher_u")]), ("thinking", TH, [("sql", "cypher"), ("sql_u", "cypher_u")]),
        ("further", FRB, [("sql", "cypher"), ("sql_u", "cypher_u")]), ("further-neo4j", FRN, [("sql", "neo_t"), ("sql_u", "neo_u")]),
        ("qwen-thinking", QT, [("sql", "cypher"), ("sql_u", "cypher_u")]), ("glm-thinking", GLT, [("sql", "cypher"), ("sql_u", "cypher_u")]),
        ("kimi3-thinking", KT, [("sql", "cypher"), ("sql_u", "cypher_u")]),
        ("glm53f-ark-thinking", G2T, [("sql", "cypher"), ("sql_u", "cypher_u")]), ("doubao-thinking", DBT, [("sql", "cypher"), ("sql_u", "cypher_u")])]
wins, ntest = [], 0
for nm, ix, pairs in COND:
    for m in sorted({k[1] for k in ix}):
        for a, b in pairs:
            for ds in DS + [None]:
                e = eff(ix, a, b, ds, models={m})
                if e:
                    ntest += 1
                    if e[2] < 0:
                        wins.append(f"{nm} {m} {a}-{b} {ds}: {z(e[0])} [{z(e[1])}, {z(e[2])}]")
OUTMD.append(f"\n## Graph significantly more accurate than SQL: {len(wins)} of {ntest} per-model contrasts")
OUTMD += ["- " + w for w in wins]
macro("GraphWinTests", f"{ntest:,}"); macro("GraphWins", str(len(wins)))
macro("GraphWinsText", "none favors" if not wins else f"{len(wins)} favor")

# ---------------- (6b) one effects table across conditions: main grid (with Holm stars), Neo4j, few-shot, thinking ----------------
def fmt(e):
    f = lambda v: f"{100 * v:+.0f}".replace("+0", "0").replace("-0", "0")
    return f"{f(e[0])} [{f(e[1])},{f(e[2])}]" if e else "--"


base = open(os.path.join(PAPER, "tables", "effects.tex")).read().split("\\bottomrule")[0].rstrip().split("\n")
rows_ = base[:]
blocks = [("Neo4j, same graphs", NEO, [("sql", "neo_t", "SQL $-$ Neo-T"), ("sql_u", "neo_u", "SQL-U $-$ Neo-U"), ("neo_u", "neo_t", "Neo-U $-$ Neo-T")], MODELS),
          ("Further models (GPT-6-Sol, Claude-Opus-5.5, Qwen3.8-27B), Neo4j", FRN, [("sql", "neo_t", "SQL $-$ Neo-T"), ("sql_u", "neo_u", "SQL-U $-$ Neo-U")], FRM),
          ("Three worked examples (from the fresh set)", FSX, [("sql", "cypher", "SQL $-$ Graph-T"), ("sql_u", "cypher_u", "SQL-U $-$ Graph-U")], None),
          ("Thinking enabled", TH, [("sql", "cypher", "SQL $-$ Graph-T"), ("sql_u", "cypher_u", "SQL-U $-$ Graph-U"), ("sql_u", "sql", "SQL-U $-$ SQL"),
                                    ("cypher_u", "cypher", "Graph-U $-$ Graph-T")], None)]
for title, ix, effs, ms in blocks:
    if not ix or not any(k[2] == effs[0][1] for k in ix):
        continue
    rows_.append("\\midrule")
    rows_.append(f"\\multicolumn{{5}}{{l}}{{\\emph{{{title}}}}} \\\\")
    for a, b, lab in effs:
        cells = [fmt(eff(ix, a, b, ds, models=ms)) for ds in DS + [None]]
        rows_.append(f"{lab} & " + " & ".join(cells) + " \\\\")
rows_ += ["\\bottomrule", "\\end{tabular}"]
open(os.path.join(PAPER, "tables", "effects_all.tex"), "w").write(re.sub(r"(?<=[\s\[,(])-(?=\d)", r"\\textminus{}", "\n".join(rows_)) + "\n")
for ds in DS:
    mk(f"NeoUNeoT{DSN[ds]}", eff(NEO, "neo_u", "neo_t", ds, models=MODELS), sign=True)

# ---------------- (7) refusal utility: +1 correct, 0 refusal, -lam wrong ----------------
def _wrong(r):
    return (not r["correct"]) and not r["refused"]


def breakeven(a, b, B=20000, seed=1):
    cl = defaultdict(lambda: [0, 0])
    for (d, m, s_, q), r in idx.items():
        if s_ == a and (d, m, b, q) in idx:
            rb = idx[(d, m, b, q)]
            c = cl[(d, r["tpl"])]
            c[0] += int(r["correct"]) - int(rb["correct"]); c[1] += int(_wrong(r)) - int(_wrong(rb))
    v = list(cl.values()); rr = random.Random(seed); k = len(v); bs = []
    for _ in range(B):
        da = dw = 0
        for _ in range(k):
            x = v[rr.randrange(k)]; da += x[0]; dw += x[1]
        bs.append(da / dw if dw > 0 else float("inf"))
    bs.sort()
    da, dw = sum(x[0] for x in v), sum(x[1] for x in v)
    return (da / dw if dw > 0 else float("inf")), bs[int(.025 * B)], bs[int(.975 * B)]


lam = breakeven("semantic", "classify")
macro("BreakEven", f"{lam[0]:.1f}"); macro("BreakEvenLo", f"{lam[1]:.1f}"); macro("BreakEvenHi", f"{lam[2]:.1f}")
lamx = breakeven("sql_x", "classify")   # SQL-X has fewer wrong answers: Metric-R never overtakes it at the point estimate
macro("BreakEvenLoSQLX", f"{lamx[1]:.1f}")
for s_ in ("semantic", "classify", "sql_x", "agent"):
    rs = [r for r in idx.values() if r["substrate"] == s_]
    macro(f"Wrong{K(s_)}", pct(sum(map(_wrong, rs)) / len(rs))); macro(f"Refused{K(s_)}", pct(sum(r["refused"] for r in rs) / len(rs)))
OUTMD.append(f"\n## Refusal utility: Metric-R overtakes Metric at lam={lam[0]:.2f} [{lam[1]:.2f}, {lam[2]:.2f}]")
for s_, f_, nm in (("semantic", "silent", "OutSilentRate"), ("classify", "refused", "OutRefusedRate")):
    pc = defaultdict(list)
    for r in idx.values():
        if r["substrate"] == s_ and not r["cov"]:
            pc[(r["dataset"], r["tpl"])].append((int(r[f_]), 0))
    d, lo, hi, _ = cluster_boot(pc)
    macro(nm + K(s_), f"{100 * d:.0f}"); macro(nm + K(s_) + "Lo", f"{100 * lo:.0f}"); macro(nm + K(s_) + "Hi", f"{100 * hi:.0f}")
    macro("OutTemplates", str(len(pc)))

# ---------------- (8) strict scoring sensitivity: a scalar answer must be a row with exactly one numeric cell ----------------
def _isnum(x):
    try:
        float(str(x)); return True
    except ValueError:
        return False


GT = {}
for ds in DS:
    for l in open(os.path.join(HERE, "data", f"{ds}_questions.jsonl")):
        q = json.loads(l); GT[(ds, q["qid"])] = q["gold_type"]
OUTMD.append("\n## Strict scalar scoring (row with exactly one numeric cell): verdicts that change")
chg = {}
for s_ in ["sql", "sql_u", "cypher", "cypher_u", "semantic", "classify", "agent", "sql_x", "metricflow"]:
    rs = [r for r in idx.values() if r["substrate"] == s_]
    flip = [r for r in rs if r["correct"] and GT.get((r["dataset"], r["qid"])) == "scalar" and r.get("rows")
            and sum(map(_isnum, r["rows"][0])) > 1]
    chg[s_] = len(flip) / len(rs)
    OUTMD.append(f"- {SN[s_]}: {len(flip)} of {len(rs)} runs ({100 * len(flip) / len(rs):.1f}%)")
macro("StrictMaxDrop", f"{100 * max(chg.values()):.1f}")
macro("StrictMaxSub", SN[max(chg, key=chg.get)])

# ---------------- (9) scale and depth sweep of the total-requirements relation (scale_sweep/, no model calls) ----------------
SW = os.path.join(HERE, "scale_sweep", "results.json")
if os.path.exists(SW):
    sw = json.load(open(SW))
    big, deep = sw["scale10_tiers3"], sw["scale1_tiers5"]
    macro("ScaleEdges", f"{big['source']['n_composition_edges']:,}")
    macro("ScaleRows", f"{big['closure']['u_explosion_rows'] / 1e6:.1f}")
    macro("ScaleRowsPerEdge", f"{big['closure']['rows_per_composition_edge']:.1f}")
    macro("ScaleBuild", f"{big['closure']['closure_total_build_s']:.0f}")
    macro("ScaleLookup", f"{big['queries']['explode']['lookup']['p50_ms']:.1f}")
    macro("ScaleRecursive", f"{big['queries']['explode']['recursive']['p50_ms']:.0f}")
    macro("DeepRowsPerEdge", f"{deep['closure']['rows_per_composition_edge']:.1f}")
    macro("ScaleChecks", f"{sum(sw[k]['queries'][q]['n'] for k in sw if k != 'repro_checks' for q in ('explode', 'where_used')):,}")
    macro("ScaleMismatch", str(sum(sw[k]['queries'][q]['mismatches'] for k in sw if k != 'repro_checks' for q in ('explode', 'where_used'))))

# ---------------- (10) standing of the recommended configuration (SQL-X) per model, ranked by printed accuracy ----------------
def standing(ix, m, subs):
    """competition rank of SQL-X among the substrates run for model m, on printed (rounded) pooled accuracy"""
    a = {s_: pct(acc([r for k, r in ix.items() if k[1] == m and k[2] == s_])) for s_ in subs
         if any(k[1] == m and k[2] == s_ for k in ix)}
    return 1 + sum(int(v) > int(a["sql_x"]) for v in a.values()), a
FRA = dict(FRB)
for f in ("main_{ds}_fr_agent.jsonl", "main_{ds}_mf_fr.jsonl"):
    FRA.update(loadf(f))
ALLS = SUBS + XSUBS + ["metricflow"]
rk_main = {m: standing(idx, m, ALLS) for m in MODELS}
rk_fr = {m: standing(FRA, m, ALLS) for m in FRM}
rk_th = {m: standing(TH, m, THS) for m in MODELS if any(k[1] == m for k in TH)}
if QT:
    rk_th["qwen3.8-27b"] = standing(QT, "qwen3.8-27b", THS)
if GLT:
    rk_th["glm-5.3"] = standing(GLT, "glm-5.3", THS)
if KT:
    rk_th["kimi-k3.1-pro"] = standing(KT, "kimi-k3.1-pro", THS)
if G2T:
    rk_th["glm-5.3-flash-ark"] = standing(G2T, "glm-5.3-flash-ark", THS)
if DBT:
    rk_th["doubao-seed-2.1-pro"] = standing(DBT, "doubao-seed-2.1-pro", THS)
OUTMD.append("\n## Standing of SQL-X (rank among all substrates, printed pooled accuracy)")
for lab, rk in (("no thinking", {**rk_main, **rk_fr}), ("thinking", rk_th)):
    for m, (r_, a) in rk.items():
        OUTMD.append(f"- {lab} {m}: rank {r_}; " + ", ".join(f"{SN[s_]} {v}" for s_, v in a.items()))
macro("StModels", str(len(rk_main) + len(rk_fr)))
macro("StBestModels", str(sum(r_ == 1 for r_, _ in list(rk_main.values()) + list(rk_fr.values()))))
macro("StThModels", str(len(rk_th)))
macro("StThBest", str(sum(r_ == 1 for r_, _ in rk_th.values())))

# ---------------- output ----------------
with open(os.path.join(PAPER, "numbers_v4.tex"), "w") as f:
    f.write("% generated by code/analyze_v4.py -- do not edit\n")
    for k_, v in sorted(V4.items()):
        f.write(f"\\newcommand{{\\{k_}}}{{{texnum(v)}}}\n")
open(os.path.join(RES, "v4_summary.md"), "w").write("\n".join(OUTMD) + "\n")
print("\n".join(OUTMD))
