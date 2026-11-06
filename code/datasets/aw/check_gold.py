"""Cross-check AW gold (gold.py, pure Python) against
 (1) a hand-written semantic-layer oracle query per template (S3, compiled by semantic.py over data/aw.duckdb);
     agreement must be 100% on covered questions; templates ending in 'x' are out of single-query coverage;
 (2) hand-written SQL over data/aw_u.duckdb, SQL over data/aw.duckdb, and Cypher over data/aw_u.kuzu / data/aw.kuzu
     for a subset of templates (every question of those templates).
Writes data/aw_s3_coverage.json ({qid: true/false}).
"""
import json, os, sys
import duckdb, kuzu

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.dirname(os.path.dirname(HERE)))         # code/ (after HERE) for the shared score.py
from __init__ import SQL_DB, SQL_U_DB, KG_DB, KG_U_DB, QUESTIONS, COVERAGE, REF_DATE  # noqa: E402
from semantic import compile_query  # noqa: E402
from score import match  # noqa: E402

BIKES = {"dim": "product.category", "value": "Bikes"}


def oracle(q):
    a, t = q["args"], q["tpl"]
    asof = {"as_of": a["date"]} if "date" in a else {}
    if t == "L1":
        return {"metric": "product_attr", "group_by": ["product.standard_cost"], "filters": [{"dim": "product.name", "value": a["name"]}]}
    if t == "L2":
        return {"metric": "product_attr", "group_by": ["product.subcategory"], "filters": [{"dim": "product.number", "value": a["product"]}]}
    if t in ("L3", "V2"):
        return {"metric": "bom_quantity", "params": {"product_number": a["product"], "max_depth": 1, **asof}, "group_by": ["component.number"]}
    if t == "L4":
        return {"metric": "bom_quantity", "params": {"product_number": a["product"], "max_depth": 1},
                "filters": [{"dim": "component.number", "value": a["component"]}]}
    if t == "L5":
        return {"metric": "product_attr", "filters": [{"dim": "product.subcategory", "value": a["subcategory"]},
                                                      {"dim": "product.list_price", "op": ">", "value": a["threshold"]}]}
    if t in ("E1", "V1"):
        return {"metric": "bom_quantity", "params": {"product_number": a["product"], **asof},
                "filters": [{"dim": "component.number", "value": a["part"]}]}
    if t in ("E2", "V3", "E4"):
        return {"metric": "bom_quantity", "params": {"product_number": a["product"], **asof}, "group_by": ["component.number"],
                "filters": [{"dim": "component.make_flag", "value": t == "E4"}], "aggregate": "count"}
    if t in ("E3", "V5"):
        return {"metric": "purchased_cost", "params": {"product_number": a["product"], **asof}}
    if t in ("W1", "W2", "V4"):
        return {"metric": "where_used", "params": {"component_number": a["component"], **asof}, "group_by": ["product.number"],
                "filters": [BIKES], **({} if t == "W2" else {"aggregate": "count"})}
    if t == "W3":
        return {"metric": "where_used", "params": {"component_number": a["component"]}, "group_by": ["product.number"], "aggregate": "count"}
    if t == "W4":
        return {"metric": "where_used", "params": {"component_number": a["component"]}, "group_by": ["product.number"],
                "filters": [{"dim": "product.subcategory", "value": a["subcategory"]}], "aggregate": "count"}
    if t == "A1":
        f = [{"dim": "product.subcategory", "value": a["subcategory"]}, {"dim": "territory.name", "value": a["territory"]},
             {"dim": "order.year", "value": a["year"]}]
        if a["online"] is not None:
            f.append({"dim": "order.online", "value": a["online"]})
        return {"metric": "units_sold", "filters": f}
    if t == "A2":
        return {"metric": "revenue", "filters": [{"dim": "territory.name", "value": a["territory"]}, {"dim": "order.year", "value": a["year"]},
                                                 {"dim": "order.quarter", "value": a["quarter"]}]}
    if t in ("A3", "A5"):
        return {"metric": "component_demand", "filters": [{"dim": "component.number", "value": a["component"]},
                {"dim": "territory.name", "value": a["territory"]}, {"dim": "order.year", "value": a["year"]}]}
    if t == "A4":
        return {"metric": "units_sold", "group_by": ["product.number"], "filters": [BIKES, {"dim": "territory.name", "value": a["territory"]},
                {"dim": "order.year", "value": a["year"]}], "order_by": [{"field": "value", "desc": True}], "limit": 3}
    return None


# ---------------- hand-written SQL / Cypher for a subset of templates ----------------
def _d(a):
    return a.get("date", REF_DATE)


def sql_u(q):
    a, t = q["args"], q["tpl"]
    rec = lambda root, d: f"""WITH RECURSIVE x(code, qty) AS (
  SELECT child_code, qty FROM u_composition WHERE parent_code = '{root}' AND valid_from <= DATE '{d}' AND (valid_to IS NULL OR DATE '{d}' <= valid_to)
  UNION ALL SELECT u.child_code, x.qty * u.qty FROM x JOIN u_composition u ON u.parent_code = x.code
    AND u.valid_from <= DATE '{d}' AND (u.valid_to IS NULL OR DATE '{d}' <= u.valid_to))"""
    if t in ("E1", "V1"):
        return rec(a["product"], _d(a)) + f" SELECT SUM(qty) FROM x WHERE code = '{a['part']}'"
    if t in ("E3", "V5"):
        return rec(a["product"], _d(a)) + " SELECT SUM(x.qty * p.standard_cost) FROM x JOIN product p ON p.product_number = x.code WHERE NOT p.make_flag"
    if t == "V2":
        return f"SELECT child_code FROM u_composition WHERE parent_code = '{a['product']}' AND valid_from <= DATE '{a['date']}' AND (valid_to IS NULL OR DATE '{a['date']}' <= valid_to)"
    if t == "W1":
        return f"""WITH RECURSIVE up(code) AS (SELECT parent_code FROM u_composition WHERE child_code = '{a['component']}'
  AND valid_from <= DATE '{REF_DATE}' AND (valid_to IS NULL OR DATE '{REF_DATE}' <= valid_to)
  UNION SELECT u.parent_code FROM up JOIN u_composition u ON u.child_code = up.code
  AND u.valid_from <= DATE '{REF_DATE}' AND (u.valid_to IS NULL OR DATE '{REF_DATE}' <= u.valid_to))
SELECT count(DISTINCT p.product_id) FROM up JOIN product p ON p.product_number = up.code
  JOIN product_subcategory s ON s.subcategory_id = p.subcategory_id JOIN product_category c ON c.category_id = s.category_id WHERE c.name = 'Bikes'"""
    if t == "A2":
        return f"""SELECT SUM(l.line_total) FROM sales_order_line l JOIN sales_order o USING (order_id) JOIN sales_territory t USING (territory_id)
WHERE t.name = '{a['territory']}' AND year(o.order_date) = {a['year']} AND quarter(o.order_date) = {a['quarter']}"""
    if t in ("A3", "A5"):
        return f"""WITH RECURSIVE ln AS (SELECT p.product_number AS code, o.order_date AS d, l.order_qty::DOUBLE AS qty FROM sales_order_line l
  JOIN sales_order o USING (order_id) JOIN sales_territory t USING (territory_id) JOIN product p USING (product_id)
  WHERE t.name = '{a['territory']}' AND year(o.order_date) = {a['year']}),
x(code, d, qty) AS (SELECT code, d, qty FROM ln
  UNION ALL SELECT u.child_code, x.d, x.qty * u.qty FROM x JOIN u_composition u ON u.parent_code = x.code
  AND u.valid_from <= x.d AND (u.valid_to IS NULL OR x.d <= u.valid_to))
SELECT SUM(qty) FROM x WHERE code = '{a['component']}'"""
    return None


def sql_typed(q):
    a, t = q["args"], q["tpl"]
    if t in ("E1", "V1"):
        d = _d(a)
        return f"""WITH RECURSIVE x(id, qty) AS (
  SELECT b.component_id, b.per_assembly_qty FROM bill_of_materials b JOIN product p ON p.product_id = b.assembly_id
  WHERE p.product_number = '{a['product']}' AND b.start_date <= DATE '{d}' AND (b.end_date IS NULL OR DATE '{d}' <= b.end_date)
  UNION ALL SELECT b.component_id, x.qty * b.per_assembly_qty FROM x JOIN bill_of_materials b ON b.assembly_id = x.id
  AND b.start_date <= DATE '{d}' AND (b.end_date IS NULL OR DATE '{d}' <= b.end_date))
SELECT SUM(qty) FROM x JOIN product p ON p.product_id = x.id WHERE p.product_number = '{a['part']}'"""
    return None


def _vf(d):
    return f"(r, n | WHERE r.valid_from <= date('{d}') AND (r.valid_to IS NULL OR date('{d}') <= r.valid_to))"


def cypher(q, rel):
    a, t = q["args"], q["tpl"]
    if t in ("E1", "V1"):
        return (f"MATCH (p:Product {{product_number: '{a['product']}'}})-[e:{rel}*1..8 {_vf(_d(a))}]->(c:Product {{product_number: '{a['part']}'}}) "
                f"RETURN sum(list_product(properties(rels(e), 'qty')))")
    if t in ("E3", "V5"):
        return (f"MATCH (p:Product {{product_number: '{a['product']}'}})-[e:{rel}*1..8 {_vf(_d(a))}]->(c:Product) WHERE NOT c.make_flag "
                f"RETURN sum(list_product(properties(rels(e), 'qty')) * c.standard_cost)")
    if t == "W1":
        return (f"MATCH (b:Product)-[e:{rel}*1..8 {_vf(REF_DATE)}]->(c:Product {{product_number: '{a['component']}'}}), "
                f"(b)-[:IN_SUBCATEGORY]->(:Subcategory)-[:IN_CATEGORY]->(k:Category {{name: 'Bikes'}}) RETURN count(DISTINCT b)")
    if t == "A2":
        return (f"MATCH (t:Territory {{name: '{a['territory']}'}})<-[:IN_TERRITORY]-(o:SalesOrder)-[l:HAS_LINE]->(:Product) "
                f"WHERE date_part('year', o.order_date) = {a['year']} AND (date_part('month', o.order_date) - 1) / 3 + 1 = {a['quarter']} "
                f"RETURN sum(l.line_total)")
    return None


def run_cypher(path, qs, rel, label):
    con = kuzu.Connection(kuzu.Database(path, read_only=True))
    ok = n = 0
    for q in qs:
        c = cypher(q, rel)
        if c is None:
            continue
        r = con.execute(c)
        rows = []
        while r.has_next():
            rows.append(r.get_next())
        n += 1
        good = match(rows, q)
        ok += good
        if not good:
            print("CYPHER MISMATCH", label, q["qid"], q["gold"], rows[:3])
    print(f"Cypher on {label}: {ok}/{n} agree")


if __name__ == "__main__":
    qs = [json.loads(l) for l in open(QUESTIONS)]
    con = duckdb.connect(SQL_DB, read_only=True)
    ok = cov = 0
    out = {}
    for q in qs:
        o = oracle(q)
        out[q["qid"]] = o is not None
        if o is None:
            assert not q["s3_expressible"], q["qid"]
            continue
        cov += 1
        rows = con.execute(compile_query(o)).fetchall()
        m = match(rows, q)
        ok += m
        if not m:
            print("MISMATCH", q["qid"], q["tpl"], q["gold"], rows[:5])
    json.dump(out, open(COVERAGE, "w"), indent=0)
    print(f"S3 oracle agrees with gold on {ok}/{cov} covered questions; coverage {cov}/{len(qs)}")

    for path, fn, label in ((SQL_U_DB, sql_u, "aw_u.duckdb"), (SQL_DB, sql_typed, "aw.duckdb")):
        c2 = duckdb.connect(path, read_only=True)
        k = n = 0
        for q in qs:
            s = fn(q)
            if s is None:
                continue
            rows = c2.execute(s).fetchall()
            n += 1
            g = match(rows, q)
            k += g
            if not g:
                print("SQL MISMATCH", label, q["qid"], q["gold"], rows[:3])
        print(f"hand-written SQL on {label}: {k}/{n} agree")
    run_cypher(KG_U_DB, qs, "CONTAINS", "aw_u.kuzu")
    run_cypher(KG_DB, qs, "HAS_SUBASSEMBLY|HAS_PART", "aw.kuzu")
