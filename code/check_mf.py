"""Correctness check of the MetricFlow substrate without LLMs.

For EVERY question of each dataset that the self-built metric layer covers (data/<ds>_s3_coverage.json), write the
reference MetricFlow query programmatically from the question's template parameters (mirroring the self-built
layer's oracles in check_gold.py / datasets/<ds>/check_gold.py), compile it through MetricFlow (mf_layer.compile_mf),
execute it on data/mf_<ds>.duckdb and compare with the gold answer using score.match.

Usage (experiment venv): python code/check_mf.py [xb aw lego]      -> results/mf_check.json
"""
import json, os, re, sys, time
from collections import Counter, defaultdict

CODE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CODE)
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, CODE)
import mf_layer as MF  # noqa: E402
from score import match  # noqa: E402


def lit(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


Dm = lambda n, v, op="=": "{{ Dimension('" + n + "') }} " + op + " " + lit(v)
En = lambda n, v: "{{ Entity('" + n + "') }} = " + lit(v)
YEAR = lambda y: "{{ TimeDimension('metric_time', date_part_name='year') }} = " + str(int(y))
QUARTER = lambda q: "{{ TimeDimension('metric_time', date_part_name='quarter') }} = " + str(int(q))


def state(sm, date=None):
    """BOM state filter: current, or valid on a date (validity interval)."""
    if date is None:
        return Dm(f"{sm}__is_current", True)
    vf = "{{ TimeDimension('" + sm + "__valid_from', 'day') }}"
    vt = "{{ TimeDimension('" + sm + "__valid_to', 'day') }}"
    return f"{vf} <= '{date}' AND ({vt} IS NULL OR {vt} >= '{date}')"


def Q(metric, where=(), group_by=(), order_by=(), limit=None):
    q = {"metrics": [metric]}
    if group_by:
        q["group_by"] = list(group_by)
    q["where"] = list(where)
    if order_by:
        q["order_by"] = list(order_by)
    if limit:
        q["limit"] = limit
    return q


# ---------------------------------------------------------------------------------------------------------------------
def codes(t):
    return re.findall(r"\b[A-Z]{2,3}\d?-[A-Z0-9-]+\b", t)


def oracle_xb(q):
    t, tpl = q["text"], q["tpl"]
    cs = codes(t)
    date = (re.findall(r"\d{4}-\d{2}-\d{2}", t) or [None])[0]
    yr = (re.findall(r"in (20\d\d)", t) or [None])[-1]
    st = state("explosion", date)
    if tpl == "L1a":
        return Q("sku_model_map", [En("sku", cs[0])], ["model"])
    if tpl == "L1b":
        return Q("listing_count", [En("sku", cs[0])])
    if tpl == "L1c":
        return Q("bom_direct_quantity", [En("product", cs[0]), state("composition")], ["component"])
    if tpl in ("E1", "E4", "V1"):
        return Q("bom_quantity", [En("product", cs[1]), En("component", cs[0]), st])
    if tpl == "E2":
        return Q("bom_component_count", [En("product", cs[0]), Dm("component__is_assembly", False), st])
    if tpl in ("E3", "V3"):
        return Q("bom_cost", [En("product", cs[0]), st])
    if tpl == "V4":
        return Q("bom_quantity", [En("product", cs[0]), Dm("component__is_assembly", False), st])
    if tpl in ("W1", "W2"):
        w = [En("component", cs[0]), Dm("explosion__root_kind", "model"), st]
        return Q("where_used", w, ["product"]) if tpl == "W2" else Q("where_used_count", w)
    if tpl == "W3":
        return Q("where_used_count", [En("component", cs[0]), Dm("explosion__root_kind", "sku"),
                                      Dm("product__is_bundle", False), st])
    if tpl == "A1":
        return Q("units_sold_model", [En("model", cs[0]), YEAR(yr)])
    if tpl == "A2":
        plat = re.search(r"platform (\w+)", t).group(1)
        qq, y = re.search(r"Q(\d) (\d{4})", t).groups()
        return Q("revenue", [Dm("store__platform", plat), YEAR(y), QUARTER(qq)])
    if tpl == "A3":
        store = re.search(r"of store (\S+) in", t).group(1)
        return Q("part_consumption", [En("component", cs[0]), Dm("store__name", store), YEAR(yr)])
    if tpl == "A4":
        ctry = re.search(r"country (\w+)", t).group(1)
        return Q("units_sold_model", [Dm("store__country", ctry), YEAR(yr)], ["model"], ["-units_sold_model"], 3)
    return None


def oracle_aw(q):
    a, t = q["args"], q["tpl"]
    d = a.get("date")
    st = state("explosion", d)
    bikes = Dm("product__category", "Bikes")
    if t == "L1":
        return Q("product_attr", [Dm("product__name", a["name"])], ["product__standard_cost"])
    if t == "L2":
        return Q("product_attr", [En("product", a["product"])], ["product__subcategory"])
    if t in ("L3", "V2"):
        return Q("bom_direct_quantity", [En("product", a["product"]), state("composition", d)], ["component"])
    if t == "L4":
        return Q("bom_direct_quantity", [En("product", a["product"]), En("component", a["component"]), state("composition")])
    if t == "L5":
        return Q("product_attr", [Dm("product__subcategory", a["subcategory"]), Dm("product__list_price", a["threshold"], ">")])
    if t in ("E1", "V1"):
        return Q("bom_quantity", [En("product", a["product"]), En("component", a["part"]), st])
    if t in ("E2", "V3", "E4"):
        return Q("bom_component_count", [En("product", a["product"]), Dm("component__make_flag", t == "E4"), st])
    if t in ("E3", "V5"):
        return Q("purchased_cost", [En("product", a["product"]), st])
    if t in ("W1", "W2", "V4"):
        w = [En("component", a["component"]), bikes, st]
        return Q("where_used", w, ["product"]) if t == "W2" else Q("where_used_count", w)
    if t == "W3":
        return Q("where_used_count", [En("component", a["component"]), st])
    if t == "W4":
        return Q("where_used_count", [En("component", a["component"]), Dm("product__subcategory", a["subcategory"]), st])
    if t == "A1":
        w = [Dm("product__subcategory", a["subcategory"]), Dm("territory__name", a["territory"]), YEAR(a["year"])]
        if a["online"] is not None:
            w.append(Dm("sales_line__online", a["online"]))
        return Q("units_sold", w)
    if t == "A2":
        return Q("revenue", [Dm("territory__name", a["territory"]), YEAR(a["year"]), QUARTER(a["quarter"])])
    if t in ("A3", "A5"):
        return Q("component_demand", [En("component", a["component"]), Dm("territory__name", a["territory"]), YEAR(a["year"])])
    if t == "A4":
        return Q("units_sold", [bikes, Dm("territory__name", a["territory"]), YEAR(a["year"])], ["product"],
                 ["-units_sold"], 3)
    return None


def oracle_lego(q):
    a, t = q["args"], q["tpl"]
    S = En("set", a["set_num"]) if "set_num" in a else None
    v = a.get("version")
    ver = Dm("explosion__root_version", int(v)) if v else Dm("explosion__is_current", True)
    cur = Dm("explosion__is_current", True)
    if "theme" in a:
        th = a["theme"]
        TY = ["(" + " OR ".join(Dm(f"set__theme_level_{i}", th) for i in (1, 2, 3)) + ")"]
    else:
        TY = []
    if "year" in a:
        TY.append(Dm("set__release_year", int(a["year"])))
    if t == "L1":
        return Q("set_attr", [S], ["set__release_year"])
    if t == "L2":
        return Q("inventory_versions", [S])
    if t == "L3":
        return Q("set_contents", [S, Dm("composition__item_kind", "set"), Dm("composition__is_current", True)], ["item"])
    if t == "L4":
        return Q("set_contents_item_count", [S, Dm("composition__item_kind", "minifig"), Dm("composition__is_current", True)])
    if t in ("E1", "V1"):
        return Q("part_quantity", [S, En("item", a["part_num"]), ver])
    if t == "E2":
        return Q("part_quantity", [S, En("item", a["part_num"]), Dm("color__name", a["color"]), cur])
    if t in ("E3", "V2", "V5"):
        return Q("part_quantity", [S, ver])
    if t in ("E4", "V3"):
        return Q("distinct_part_count", [S, ver])
    if t == "E5":
        return Q("distinct_part_color_count", [S, cur])
    if t in ("E6", "V4"):
        return Q("minifig_quantity", [S, ver])
    if t == "E7":
        return Q("part_quantity_incl_spares", [S, cur])
    if t == "E8":
        return Q("part_quantity", [S, Dm("item__category", a["category"]), cur])
    if t in ("W1", "W2", "W4", "W5"):
        w = [En("item", a["part_num"])] + ([Dm("color__name", a["color"])] if "color" in a else []) + \
            ([Dm("set__release_year", int(a["year"]))] if "year" in a else [])
        return Q("where_used", w, ["set"]) if t == "W5" else Q("where_used_count", w)
    if t == "W3":
        return Q("where_used", [En("item", a["fig_num"])], ["set"])
    if t == "A1":
        return Q("set_attr", TY)
    if t == "A2":
        return Q("catalog_minifigs", TY)
    if t == "A3":
        return Q("catalog_parts", TY)
    if t == "A4":
        return Q("set_attr", [Dm("set__release_year", int(a["year"]))], ["set__theme_root"], ["-set_attr"], 3)
    if t == "A5":
        return Q("catalog_parts", [Dm("set__release_year", int(a["year"])), En("item", a["part_num"]), Dm("color__name", a["color"])])
    if t == "A6":
        return Q("catalog_color_count", TY)
    return None


ORACLES = {"xb": oracle_xb, "aw": oracle_aw, "lego": oracle_lego}
QSETS = {"xb": [("xb_questions.jsonl", "xb_s3_coverage.json"), ("questions.jsonl", "s3_coverage.json")],
         "aw": [("aw_questions.jsonl", "aw_s3_coverage.json")], "lego": [("lego_questions.jsonl", "lego_s3_coverage.json")]}


def check(ds, qfile, cfile):
    qs = [json.loads(l) for l in open(os.path.join(DATA, qfile))]
    cov = json.load(open(os.path.join(DATA, cfile)))
    fn = ORACLES[ds]
    res = dict(questions=len(qs), self_built_covered=sum(bool(cov.get(q["qid"])) for q in qs), mf_expressed=0, passed=0,
               failures=[], by_template={}, uncovered_templates=sorted({q["tpl"] for q in qs if not cov.get(q["qid"])}))
    bt = defaultdict(Counter)
    t_compile = t_exec = 0.0
    examples = {}
    for q in qs:
        if not cov.get(q["qid"]):
            continue
        o = fn(q)
        bt[q["tpl"]]["n"] += 1
        if o is None:
            res["failures"].append(dict(qid=q["qid"], tpl=q["tpl"], why="not expressed"))
            continue
        res["mf_expressed"] += 1
        examples.setdefault(q["tpl"], o)
        try:
            t0 = time.time()
            sql = MF.compile_mf(o, ds)
            t1 = time.time()
            rows = MF.run(sql, ds)
            t2 = time.time()
            t_compile += t1 - t0
            t_exec += t2 - t1
        except Exception as e:
            res["failures"].append(dict(qid=q["qid"], tpl=q["tpl"], why=f"{type(e).__name__}: {str(e)[:500]}", query=o))
            continue
        ok = match(rows, q)
        res["passed"] += ok
        bt[q["tpl"]]["pass"] += ok
        if not ok:
            res["failures"].append(dict(qid=q["qid"], tpl=q["tpl"], why="mismatch", gold=q["gold"],
                                        got=[[str(x) for x in r] for r in rows[:8]], query=o))
    res["by_template"] = {k: f"{v['pass']}/{v['n']}" for k, v in sorted(bt.items())}
    res["compile_s_total"], res["exec_s_total"] = round(t_compile, 2), round(t_exec, 2)
    res["reference_query_per_template"] = examples
    return res


if __name__ == "__main__":
    dss = sys.argv[1:] or ["xb", "aw", "lego"]
    out_path = os.path.join(ROOT, "results", "mf_check.json")
    allres = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for ds in dss:
        for qfile, cfile in QSETS[ds]:
            r = check(ds, qfile, cfile)
            allres[f"{ds}:{qfile}"] = r
            print(f"{ds} {qfile}: MetricFlow reference queries pass {r['passed']}/{r['self_built_covered']} covered questions "
                  f"(expressed {r['mf_expressed']}); by template {r['by_template']}")
            for f in r["failures"]:
                print("   FAIL", f["qid"], f["tpl"], f["why"][:300], f.get("gold"), f.get("got"))
    allres["_meta"] = dict(note="reference MetricFlow queries built from template parameters; compiled by MetricFlow "
                                "(mf_layer.compile_mf) and executed on data/mf_<ds>.duckdb; scored with score.match",
                           date=time.strftime("%Y-%m-%d %H:%M"))
    json.dump(allres, open(out_path, "w"), indent=1)
    MF.close()
