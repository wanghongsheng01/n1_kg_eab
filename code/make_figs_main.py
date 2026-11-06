"""Figures for the paper from results/main_agg.json (run with ICDE2027/.venv, which has matplotlib)."""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = json.load(open(os.path.join(HERE, "results", "main_agg.json")))
FIG = os.path.join(HERE, "paper", "figs")
os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({"font.size": 8, "font.family": "serif", "pdf.fonttype": 42, "ps.fonttype": 42})

# Fig. 1: storage x modeling per dataset
ds, names = ["xb", "aw", "lego"], {"xb": "XB", "aw": "AW", "lego": "LEGO"}
subs = [("sql", "SQL (split)", "#4C72B0", ""), ("sql_u", "SQL-U (unified)", "#4C72B0", "//"),
        ("cypher", "Graph-T (typed)", "#DD8452", ""), ("cypher_u", "Graph-U (unified)", "#DD8452", "//")]
fig, ax = plt.subplots(figsize=(3.4, 1.9))
w = 0.2
for j, (s, lab, col, hat) in enumerate(subs):
    xs = [i + (j - 1.5) * w for i in range(len(ds))]
    ys = [100 * A["acc"].get(f"{d}|{s}", 0) for d in ds]
    ax.bar(xs, ys, w, label=lab, color=col, hatch=hat, edgecolor="black", linewidth=0.4)
ax.set_xticks(range(len(ds)))
ax.set_xticklabels([names[d] for d in ds])
ax.set_ylabel("accuracy (%)")
ax.set_ylim(0, 100)
ax.legend(ncol=2, fontsize=6.5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0))
fig.tight_layout()
fig.savefig(os.path.join(FIG, "storage_modeling.pdf"), bbox_inches="tight")

# Fig. 2: outcome composition per substrate (pooled)
order = [("sql", "SQL"), ("sql_u", "SQL-U"), ("cypher", "Graph-T"), ("cypher_u", "Graph-U"), ("semantic", "Metric"),
         ("classify", "Metric-R"), ("agent", "Agent")]
order = [(s, n) for s, n in order if s in A["outcomes"]]
fig, ax = plt.subplots(figsize=(3.4, 2.1))
parts = [("correct", "correct", "#55A868"), ("silent", "silent wrong", "#C44E52"), ("visible", "visible error", "#8C8C8C"),
         ("refused", "refused", "#64B5CD")]
bottom = [0] * len(order)
for k, lab, col in parts:
    ys = [100 * A["outcomes"][s][k] for s, _ in order]
    ax.bar(range(len(order)), ys, 0.65, bottom=bottom, label=lab, color=col, edgecolor="black", linewidth=0.3)
    bottom = [b + y for b, y in zip(bottom, ys)]
ax.set_xticks(range(len(order)))
ax.set_xticklabels([n for _, n in order], rotation=30, ha="right")
ax.set_ylabel("runs (%)")
ax.set_ylim(0, 100)
ax.legend(ncol=2, fontsize=6.5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0))
fig.tight_layout()
fig.savefig(os.path.join(FIG, "outcomes.pdf"), bbox_inches="tight")
print("ok")
