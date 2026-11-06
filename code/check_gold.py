"""Cross-check gold (pure Python) against hand-written oracle queries through the semantic layer (S3).

Agreement validates both the gold implementation and the S3 compiler; templates that S3 cannot express in one
query are listed as out of coverage.
"""
import json, os, re, duckdb
from semantic import compile_query
from score import match

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
con = duckdb.connect(os.path.join(HERE, "data", "xb.duckdb"), read_only=True)
CODE = r"[A-Z0-9][A-Z0-9-]+-\d{2,5}"


def codes(t):
    return re.findall(r"\b[A-Z]{2,3}\d?-[A-Z0-9-]+\b", t)


def oracle(q):
    t, tpl = q["text"], q["tpl"]
    cs = codes(t)
    date = (re.findall(r"\d{4}-\d{2}-\d{2}", t) or [None])[0]
    yr = (re.findall(r"in (20\d\d)", t) or [None])[-1]
    p = {"as_of": date} if date else {}
    if tpl == "L1a":
        return {"metric": "sku_model_map", "group_by": ["model.code"], "filters": [{"dim": "sku.code", "value": cs[0]}]}
    if tpl == "L1b":
        return {"metric": "listing_count", "filters": [{"dim": "sku.code", "value": cs[0]}]}
    if tpl == "L1c":
        return {"metric": "bom_quantity", "params": {"product_code": cs[0], "max_depth": 1}, "group_by": ["component.code"]}
    if tpl in ("E1", "E4", "V1"):
        return {"metric": "bom_quantity", "params": {"product_code": cs[1], **p}, "filters": [{"dim": "component.code", "value": cs[0]}]}
    if tpl == "E2":
        return {"metric": "bom_quantity", "params": {"product_code": cs[0]}, "group_by": ["component.code"],
                "filters": [{"dim": "component.is_assembly", "value": False}], "aggregate": "count"}
    if tpl in ("E3", "V3"):
        return {"metric": "bom_cost", "params": {"product_code": cs[0], **p}}
    if tpl == "V4":
        return {"metric": "bom_quantity", "params": {"product_code": cs[0], **p}, "group_by": ["component.code"],
                "filters": [{"dim": "component.is_assembly", "value": False}], "aggregate": "sum"}
    if tpl in ("W1", "W2", "W3"):
        pt = "sku" if tpl == "W3" else "model"
        return {"metric": "where_used", "params": {"component_code": cs[0], "product_type": pt}, "group_by": ["product.code"],
                **({"aggregate": "count"} if tpl != "W2" else {})}
    if tpl == "A1":
        return {"metric": "units_sold", "params": {"level": "model"},
                "filters": [{"dim": "product.code", "value": cs[0]}, {"dim": "order.year", "value": int(yr)}]}
    if tpl == "A2":
        plat = re.search(r"platform (\w+)", t).group(1)
        qq, y = re.search(r"Q(\d) (\d{4})", t).groups()
        return {"metric": "revenue", "filters": [{"dim": "store.platform", "value": plat}, {"dim": "order.year", "value": int(y)},
                                                 {"dim": "order.quarter", "value": int(qq)}]}
    if tpl == "A3":
        store = re.search(r"of store (\S+) in", t).group(1)
        return {"metric": "part_consumption", "filters": [{"dim": "component.code", "value": cs[0]},
                {"dim": "store.name", "value": store}, {"dim": "order.year", "value": int(yr)}]}
    if tpl == "A4":
        ctry = re.search(r"country (\w+)", t).group(1)
        return {"metric": "units_sold", "params": {"level": "model"}, "group_by": ["product.code"],
                "filters": [{"dim": "store.country", "value": ctry}, {"dim": "order.year", "value": int(yr)}],
                "order_by": [{"field": "value", "desc": True}], "limit": 3}
    return None


if __name__ == "__main__":
    import sys
    qfile = sys.argv[1] if len(sys.argv) > 1 else "questions.jsonl"
    cfile = sys.argv[2] if len(sys.argv) > 2 else "s3_coverage.json"
    qs = [json.loads(l) for l in open(os.path.join(HERE, "data", qfile))]
    ok = cov = 0
    out = {}
    for q in qs:
        o = oracle(q)
        if o is None:
            out[q["qid"]] = False
            print(q["qid"], q["tpl"], "OUT OF S3 COVERAGE")
            continue
        cov += 1
        out[q["qid"]] = True
        rows = con.execute(compile_query(o)).fetchall()
        m = match(rows, q)
        ok += m
        if not m:
            print("MISMATCH", q["qid"], q["gold"], rows[:5])
    json.dump(out, open(os.path.join(HERE, "data", cfile), "w"), indent=0)
    print(f"S3 oracle agrees with gold on {ok}/{cov} covered questions; coverage {cov}/{len(qs)}")
