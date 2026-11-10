"""Human audit of silent-failure causes (review): 120 failures stratified by dataset and free-query substrate (10 each),
drawn from the labelled sample (results/tax_main_<ds>.jsonl) and shown without the model labels.

Usage: python make_human_audit.py -> R2_sprint/human_audit/audit_120.xlsx (+ the key that links rows to runs)
After labelling, analyze_main.py reads R2_sprint/human_audit/audit_120_labelled.xlsx if present.
"""
import json, os, random
import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Font

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "R2_sprint", "human_audit")
CATS = {  # the codebook of classify_errors.py, without the metric-layer categories
    "T1_schema_link": "wrong table, column, label or identifier (e.g. matched name instead of code, wrong entity)",
    "T2_traversal": "wrong composition traversal: wrong edge type or direction, missing a level, missing direct children, depth bound too small, not recursive",
    "T3_quantity": "wrong quantity semantics: no multiplication along the path, a level's qty dropped, counting paths/rows instead of summing quantities, double counting",
    "T4_validity": "BOM validity / as-of date applied wrongly or only on some levels, or wrong date",
    "T5_mapping": "SKU-model mapping or bundle expansion wrong (not expanded, wrong direction, qty ignored)",
    "T6_order_filter": "wrong order filter: status, date range, store, platform, country",
    "T7_output": "result shape or aggregation wrong: extra rows/columns, wrong aggregate, top-k, rounding, NULL instead of 0",
    "T8_dialect": "query-language or engine misuse: unsupported function/syntax, binder/parser error not repaired",
    "T11_question_misread": "misread what the question asks (e.g. parts vs components, wrong entity type)",
}
DS = ["xb", "aw", "lego"]


def main():
    T = [json.loads(l) for ds in DS for l in open(os.path.join(HERE, "results", f"tax_main_{ds}.jsonl"))]
    runs = {}
    for ds in DS:
        for f in (f"main_{ds}.jsonl", f"main_{ds}_g2.jsonl"):   # g2 supersedes Graph-T/Graph-U
            for l in open(os.path.join(HERE, "results", f)):
                r = json.loads(l)
                if r.get("rep", 0) == 0 and (f.endswith("_g2.jsonl") or r["substrate"] not in ("cypher", "cypher_u")):
                    runs[(ds, r["model"], r["substrate"], r["qid"])] = r
    qs = {(ds, json.loads(l)["qid"]): json.loads(l) for ds in DS for l in open(os.path.join(HERE, "data", f"{ds}_questions.jsonl"))}
    rng, items = random.Random(20261110), []
    for ds in DS:
        for s in ("sql", "sql_u", "cypher", "cypher_u"):
            pool = [d for d in T if d["dataset"] == ds and d["substrate"] == s and d["outcome"] == "wrong"
                    and (ds, d["model"], s, d["qid"]) in runs]
            items += rng.sample(pool, min(10, len(pool)))
    rng.shuffle(items)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "audit"
    ws.append(["id", "dataset", "language", "question", "gold answer", "generated query (final attempt)", "returned rows (first 5)",
               "primary cause (choose)", "answer really wrong? (yes/no/unsure)", "note (optional)"])
    key = []
    for i, d in enumerate(items, 1):
        r, q = runs[(d["dataset"], d["model"], d["substrate"], d["qid"])], qs[(d["dataset"], d["qid"])]
        ws.append([i, d["dataset"], "SQL" if d["substrate"].startswith("sql") else "Cypher (Kuzu)", q["text"],
                   json.dumps(q["gold"])[:600], r["attempts"][-1]["text"][:3000], json.dumps(r["rows"][:5])[:600], "", "", ""])
        key.append(dict(id=i, dataset=d["dataset"], model=d["model"], substrate=d["substrate"], qid=d["qid"]))
    n = len(items) + 1
    for rng_, opts in ((f"H2:H{n}", ",".join(CATS)), (f"I2:I{n}", "yes,no,unsure")):
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True); ws.add_data_validation(dv); dv.add(rng_)
    for col, w in zip("ABCDEFGHIJ", [5, 8, 12, 50, 30, 80, 40, 24, 16, 25]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "D2"
    cb = wb.create_sheet("codebook"); cb.append(["category", "meaning"])
    for k, v in CATS.items():
        cb.append([k, v])
    cb.column_dimensions["A"].width = 24; cb.column_dimensions["B"].width = 120
    os.makedirs(OUT, exist_ok=True)
    wb.save(os.path.join(OUT, "audit_120.xlsx"))
    json.dump(key, open(os.path.join(OUT, "audit_120_key.json"), "w"), indent=0)
    print(len(items), "items")


if __name__ == "__main__":
    main()
