"""LLM-free correctness check of the closure substrates sql_x / cypher_x (built by build_closure.py).

Per dataset:
 1. structure : no two u_explosion rows of the same (root, item[, version, colour, spare]) overlap in time; valid_from <= valid_to.
 2. triples   : 200 random (root, item, date) triples (LEGO: (root, version, item)) drawn with a fixed seed; the qty read
                from u_explosion must equal an independent recursive computation by the dataset's gold.World (pure Python
                over the split source tables, id-keyed, memo-free of build_closure.py); about 30% of the dates are interval
                boundaries and about 25% of the items are not contained (expected qty 0).
 3. questions : one hand-written reference query per template over sql_x (u_explosion + the SQL-U tables, no recursion
                over composition) for EVERY question of the dataset; the result must match gold with score.match().
 4. cypher_x  : the EXPLODES relationship equals u_explosion row for row after mapping node keys to codes, plus
                hand-written Cypher over EXPLODES for the E1/W1/V1-style templates.
Writes results/closure_check.json. Usage: python check_closure.py [--ds xb,aw,lego]
"""
import argparse, datetime as dt, importlib.util, json, os, random, re, sys
from collections import defaultdict
import duckdb, kuzu

CODE = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(CODE)
DATA = os.path.join(HERE, "data")
sys.path.insert(0, CODE)
from score import match  # noqa: E402

ONE = dt.timedelta(days=1)


def load_gold(ds):
    path = os.path.join(CODE, "gold.py") if ds == "xb" else os.path.join(CODE, "datasets", ds, "gold.py")
    spec = importlib.util.spec_from_file_location(f"gold_{ds}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def valid(alias, d):
    return f"{alias}.valid_from <= DATE '{d}' AND ({alias}.valid_to IS NULL OR DATE '{d}' <= {alias}.valid_to)"


# =====================================================================================================================
# reference queries over sql_x
XB_TODAY = "2026-10-01"
XB_RE = {
    "L1a": r"sales SKU (\S+) map to", "L1b": r"sales SKU (\S+) listed", "L1c": r"company model (\S+) uses directly",
    "E1": r"units of part (\S+) are needed to build one unit of company model (\S+) under",
    "E2": r"company model (\S+) contain at any level", "E3": r"company model (\S+) under the current BOM, i\.e\.",
    "E4": r"units of part (\S+) are contained in one unit of the bundle SKU (\S+) under",
    "W1": r"contain component (\S+) at any level", "W2": r"contain component (\S+) at any level",
    "W3": r"contain part (\S+) at any level", "W4": r"contain company model (\S+) through",
    "A1": r"units of company model (\S+) were sold in completed orders in (\d{4})",
    "A2": r"on platform (.+?) in Q(\d) (\d{4})\?",
    "A3": r"units of part (\S+) were consumed by completed orders of store (.+?) in (\d{4})\? Explode",
    "A4": r"in country (\S+) in (\d{4})",
    "V1": r"units of part (\S+) did one unit of company model (\S+) require on (\d{4}-\d\d-\d\d)",
    "V2": r"company model (\S+) use directly \(first level\) on (\d{4}-\d\d-\d\d)",
    "V3": r"company model (\S+) under the BOM valid on (\d{4}-\d\d-\d\d)",
    "V4": r"company model (\S+) contain under the BOM valid on (\d{4}-\d\d-\d\d)",
}
PARTS_XB = "item_kind = 'component' AND item_code IN (SELECT component_code FROM component WHERE NOT is_assembly)"
ORDERS_XB = """FROM order_line ol JOIN orders o USING (order_id) JOIN listing l USING (listing_id) JOIN store st ON st.store_id = l.store_id
  JOIN sales_sku s ON s.sku_id = l.sku_id JOIN u_explosion e ON e.root_kind = 'sku' AND e.root_code = s.sku_code"""


def ref_xb(q):
    t, tpl = q["text"], q["tpl"]
    g = re.search(XB_RE[tpl], t).groups()
    T = XB_TODAY
    if tpl == "L1a":
        return f"SELECT item_code FROM u_explosion WHERE root_kind = 'sku' AND root_code = {lit(g[0])} AND item_kind = 'model'"
    if tpl == "L1b":
        return f"SELECT count(*) FROM listing l JOIN sales_sku s USING (sku_id) WHERE s.sku_code = {lit(g[0])}"
    if tpl == "L1c":
        return (f"SELECT DISTINCT child_code FROM u_composition u WHERE parent_kind = 'model' AND parent_code = {lit(g[0])} "
                f"AND {valid('u', T)}")
    if tpl in ("E1", "V1"):
        d = g[2] if tpl == "V1" else T
        return (f"SELECT qty FROM u_explosion e WHERE root_kind = 'model' AND root_code = {lit(g[1])} AND item_kind = 'component' "
                f"AND item_code = {lit(g[0])} AND {valid('e', d)}")
    if tpl == "E4":
        return (f"SELECT qty FROM u_explosion e WHERE root_kind = 'sku' AND root_code = {lit(g[1])} AND item_kind = 'component' "
                f"AND item_code = {lit(g[0])} AND {valid('e', T)}")
    if tpl in ("E2", "E3", "V3", "V4"):
        d = g[1] if tpl in ("V3", "V4") else T
        agg = {"E2": "count(*)", "V4": "sum(e.qty)"}.get(tpl, "round(sum(e.qty * c.unit_cost), 2)")
        return (f"SELECT {agg} FROM u_explosion e JOIN component c ON c.component_code = e.item_code WHERE e.root_kind = 'model' "
                f"AND e.root_code = {lit(g[0])} AND e.item_kind = 'component' AND NOT c.is_assembly AND {valid('e', d)}")
    if tpl in ("W1", "W2"):
        sel = "count(DISTINCT root_code)" if tpl == "W1" else "DISTINCT root_code"
        return (f"SELECT {sel} FROM u_explosion e WHERE root_kind = 'model' AND item_kind = 'component' AND item_code = {lit(g[0])} "
                f"AND {valid('e', T)}")
    if tpl == "W3":
        return (f"SELECT count(DISTINCT root_code) FROM u_explosion e WHERE root_kind = 'sku' AND item_kind = 'component' "
                f"AND item_code = {lit(g[0])} AND {valid('e', T)} "
                f"AND root_code IN (SELECT sku_code FROM sales_sku WHERE NOT is_bundle)")
    if tpl == "W4":
        return (f"SELECT count(DISTINCT root_code) FROM u_explosion e WHERE root_kind = 'sku' AND item_kind = 'model' "
                f"AND item_code = {lit(g[0])} AND root_code IN (SELECT sku_code FROM sales_sku WHERE is_bundle)")
    if tpl == "A1":
        return (f"SELECT sum(ol.qty * e.qty) {ORDERS_XB} WHERE o.status = 'completed' AND year(o.order_date) = {g[1]} "
                f"AND e.item_kind = 'model' AND e.item_code = {lit(g[0])} "
                f"AND e.valid_from <= o.order_date AND (e.valid_to IS NULL OR o.order_date <= e.valid_to)")
    if tpl == "A2":
        return (f"SELECT round(sum(ol.qty * ol.unit_price), 2) FROM order_line ol JOIN orders o USING (order_id) JOIN listing l USING (listing_id) "
                f"JOIN store st ON st.store_id = l.store_id WHERE o.status = 'completed' AND st.platform = {lit(g[0])} "
                f"AND year(o.order_date) = {g[2]} AND quarter(o.order_date) = {g[1]}")
    if tpl == "A3":
        return (f"SELECT sum(ol.qty * e.qty) {ORDERS_XB} WHERE o.status = 'completed' AND st.store_name = {lit(g[1])} "
                f"AND year(o.order_date) = {g[2]} AND e.item_kind = 'component' AND e.item_code = {lit(g[0])} "
                f"AND e.valid_from <= o.order_date AND (e.valid_to IS NULL OR o.order_date <= e.valid_to)")
    if tpl == "A4":
        return (f"SELECT e.item_code, sum(ol.qty * e.qty) AS u {ORDERS_XB} WHERE o.status = 'completed' AND st.country = {lit(g[0])} "
                f"AND year(o.order_date) = {g[1]} AND e.item_kind = 'model' GROUP BY 1 ORDER BY u DESC LIMIT 3")
    if tpl == "V2":
        dq = lambda d: (f"SELECT child_code, sum(qty) AS q FROM u_composition u WHERE parent_kind = 'model' AND parent_code = {lit(g[0])} "
                        f"AND {valid('u', d)} GROUP BY 1")
        return (f"SELECT coalesce(a.child_code, b.child_code) FROM ({dq(g[1])}) a FULL OUTER JOIN ({dq(T)}) b USING (child_code) "
                f"WHERE coalesce(a.q, 0) <> coalesce(b.q, 0)")
    raise KeyError(tpl)


AW_TODAY = "2025-06-30"
BIKES_AW = """(SELECT p.product_number FROM product p JOIN product_subcategory s USING (subcategory_id)
  JOIN product_category c USING (category_id) WHERE c.name = 'Bikes')"""
PARTS_AW = "(SELECT product_number FROM product WHERE NOT make_flag)"


def ref_aw(q):
    a, tpl, T = q["args"], q["tpl"], AW_TODAY
    if tpl == "L1":
        return f"SELECT standard_cost FROM product WHERE name = {lit(a['name'])}"
    if tpl == "L2":
        return f"SELECT s.name FROM product p JOIN product_subcategory s USING (subcategory_id) WHERE p.product_number = {lit(a['product'])}"
    if tpl in ("L3", "V2"):
        d = a.get("date", T)
        return f"SELECT DISTINCT child_code FROM u_composition u WHERE parent_code = {lit(a['product'])} AND {valid('u', d)}"
    if tpl == "L4":
        return (f"SELECT sum(qty) FROM u_composition u WHERE parent_code = {lit(a['product'])} AND child_code = {lit(a['component'])} "
                f"AND {valid('u', T)}")
    if tpl == "L5":
        return (f"SELECT count(*) FROM product p JOIN product_subcategory s USING (subcategory_id) WHERE s.name = {lit(a['subcategory'])} "
                f"AND p.list_price > {a['threshold']}")
    if tpl in ("E1", "V1"):
        return (f"SELECT qty FROM u_explosion e WHERE root_code = {lit(a['product'])} AND item_code = {lit(a['part'])} "
                f"AND {valid('e', a.get('date', T))}")
    if tpl in ("E2", "V3", "E3", "V5", "E4"):
        d = a.get("date", T)
        agg = "round(sum(e.qty * p.standard_cost), 2)" if tpl in ("E3", "V5") else "count(*)"
        flag = "p.make_flag" if tpl == "E4" else "NOT p.make_flag"
        return (f"SELECT {agg} FROM u_explosion e JOIN product p ON p.product_number = e.item_code WHERE e.root_code = {lit(a['product'])} "
                f"AND {flag} AND {valid('e', d)}")
    if tpl in ("W1", "W2", "W3", "W4", "V4"):
        d = a.get("date", T)
        sel = "DISTINCT e.root_code" if tpl == "W2" else "count(DISTINCT e.root_code)"
        scope = "" if tpl == "W3" else f"AND e.root_code IN {BIKES_AW}"
        if tpl == "W4":
            scope += (f" AND e.root_code IN (SELECT p.product_number FROM product p JOIN product_subcategory s USING (subcategory_id) "
                      f"WHERE s.name = {lit(a['subcategory'])})")
        return f"SELECT {sel} FROM u_explosion e WHERE e.item_code = {lit(a['component'])} {scope} AND {valid('e', d)}"
    if tpl == "W5x":
        ex = lambda p: f"SELECT item_code FROM u_explosion e WHERE root_code = {lit(p)} AND item_code IN {PARTS_AW} AND {valid('e', T)}"
        return f"{ex(a['product1'])} EXCEPT {ex(a['product2'])}"
    terr = lambda n: f"o.territory_id = (SELECT territory_id FROM sales_territory WHERE name = {lit(n)})"
    if tpl == "A1":
        ch = "" if a["online"] is None else f"AND o.online_order = {str(a['online']).upper()}"
        return (f"SELECT sum(ol.order_qty) FROM sales_order_line ol JOIN sales_order o USING (order_id) JOIN product p USING (product_id) "
                f"JOIN product_subcategory s USING (subcategory_id) WHERE {terr(a['territory'])} AND year(o.order_date) = {a['year']} "
                f"AND s.name = {lit(a['subcategory'])} {ch}")
    if tpl == "A2":
        return (f"SELECT round(sum(ol.line_total), 2) FROM sales_order_line ol JOIN sales_order o USING (order_id) "
                f"WHERE {terr(a['territory'])} AND year(o.order_date) = {a['year']} AND quarter(o.order_date) = {a['quarter']}")
    if tpl in ("A3", "A5"):
        c = lit(a["component"])
        frm = "FROM sales_order_line ol JOIN sales_order o USING (order_id) JOIN product p USING (product_id)"
        whr = f"WHERE {terr(a['territory'])} AND year(o.order_date) = {a['year']}"
        return (f"SELECT coalesce((SELECT sum(ol.order_qty) {frm} {whr} AND p.product_number = {c}), 0) + "
                f"coalesce((SELECT sum(ol.order_qty * e.qty) {frm} JOIN u_explosion e ON e.root_code = p.product_number {whr} "
                f"AND e.item_code = {c} AND e.valid_from <= o.order_date AND (e.valid_to IS NULL OR o.order_date <= e.valid_to)), 0)")
    if tpl == "A4":
        return (f"SELECT p.product_number, sum(ol.order_qty) AS u FROM sales_order_line ol JOIN sales_order o USING (order_id) "
                f"JOIN product p USING (product_id) WHERE {terr(a['territory'])} AND year(o.order_date) = {a['year']} "
                f"AND p.product_number IN {BIKES_AW} GROUP BY 1 ORDER BY u DESC LIMIT 3")
    if tpl == "A6x":
        s = lambda n: (f"SELECT DISTINCT p.product_number FROM sales_order_line ol JOIN sales_order o USING (order_id) JOIN product p "
                       f"USING (product_id) WHERE {terr(n)} AND year(o.order_date) = {a['year']} AND p.product_number IN {BIKES_AW}")
        return f"{s(a['territory1'])} EXCEPT {s(a['territory2'])}"
    if tpl == "V6x":
        dl = lambda d: f"SELECT child_code FROM u_composition u WHERE parent_code = {lit(a['product'])} AND {valid('u', d)}"
        return f"{dl(a['date1'])} EXCEPT {dl(a['date2'])}"
    if tpl == "V7x":
        qd = lambda d: (f"(SELECT coalesce(sum(qty), 0) FROM u_explosion e WHERE root_code = {lit(a['product'])} "
                        f"AND item_code = {lit(a['part'])} AND {valid('e', d)})")
        return f"SELECT {qd(T)} - {qd(a['date'])}"
    raise KeyError(tpl)


SUB_LG = lambda th: f"""WITH RECURSIVE sub(id) AS (SELECT id FROM themes WHERE name = {lit(th)}
  UNION SELECT t.id FROM themes t JOIN sub ON t.parent_id = sub.id) """


def ref_lego(q):
    a, tpl = q["args"], q["tpl"]
    s = lit(a.get("set_num", ""))
    cur = "e.root_kind = 'set' AND e.is_current"
    ver = f"e.root_kind = 'set' AND e.root_num = {s} AND " + (f"e.root_version = {a['version']}" if "version" in a else "e.is_current")
    col = lambda: f"e.color_id = (SELECT id FROM colors WHERE name = {lit(a['color'])})"
    NS = "e.item_kind = 'part' AND NOT e.is_spare"
    if tpl == "L1":
        return f"SELECT year FROM sets WHERE set_num = {s}"
    if tpl == "L2":
        return f"SELECT count(DISTINCT version) FROM u_composition WHERE parent_kind = 'set' AND parent_num = {s}"
    if tpl in ("L3", "L4"):
        k = "set" if tpl == "L3" else "minifig"
        sel = "DISTINCT child_num" if tpl == "L3" else "count(DISTINCT child_num)"
        return (f"SELECT {sel} FROM u_composition u WHERE parent_kind = 'set' AND parent_num = {s} AND child_kind = '{k}' "
                f"AND version = (SELECT max(version) FROM u_composition WHERE parent_kind = 'set' AND parent_num = {s})")
    if tpl in ("E1", "V1"):
        return f"SELECT sum(e.qty) FROM u_explosion e WHERE {ver} AND {NS} AND e.item_num = {lit(a['part_num'])}"
    if tpl == "E2":
        return f"SELECT sum(e.qty) FROM u_explosion e WHERE {ver} AND {NS} AND e.item_num = {lit(a['part_num'])} AND {col()}"
    if tpl in ("E3", "V2", "V5"):
        return f"SELECT sum(e.qty) FROM u_explosion e WHERE {ver} AND {NS}"
    if tpl == "E7":
        return f"SELECT sum(e.qty) FROM u_explosion e WHERE {ver} AND e.item_kind = 'part'"
    if tpl in ("E4", "V3"):
        return f"SELECT count(DISTINCT e.item_num) FROM u_explosion e WHERE {ver} AND {NS}"
    if tpl == "E5":
        return f"SELECT count(*) FROM (SELECT DISTINCT e.item_num, e.color_id FROM u_explosion e WHERE {ver} AND {NS})"
    if tpl in ("E6", "V4"):
        return f"SELECT coalesce(sum(e.qty), 0) FROM u_explosion e WHERE {ver} AND e.item_kind = 'minifig'"
    if tpl == "E8":
        return (f"SELECT sum(e.qty) FROM u_explosion e JOIN parts p ON p.part_num = e.item_num JOIN part_categories pc ON pc.id = p.part_cat_id "
                f"WHERE {ver} AND {NS} AND pc.name = {lit(a['category'])}")
    if tpl in ("W1", "W2", "W4", "W5"):
        sel = "DISTINCT e.root_num" if tpl == "W5" else "count(DISTINCT e.root_num)"
        x = f" AND {col()}" if "color" in a else ""
        if "year" in a:
            x += f" AND e.root_num IN (SELECT set_num FROM sets WHERE year = {a['year']})"
        return f"SELECT {sel} FROM u_explosion e WHERE {cur} AND {NS} AND e.item_num = {lit(a['part_num'])}{x}"
    if tpl == "W3":
        return f"SELECT DISTINCT e.root_num FROM u_explosion e WHERE {cur} AND e.item_kind = 'minifig' AND e.item_num = {lit(a['fig_num'])}"
    inth = lambda: f"SELECT set_num FROM sets WHERE year = {a['year']} AND theme_id IN (SELECT id FROM sub)"
    if tpl == "A1":
        return SUB_LG(a["theme"]) + f"SELECT count(*) FROM ({inth()})"
    if tpl in ("A2", "A3"):
        k = "e.item_kind = 'minifig'" if tpl == "A2" else NS
        return SUB_LG(a["theme"]) + f"SELECT sum(e.qty) FROM u_explosion e WHERE {cur} AND {k} AND e.root_num IN ({inth()})"
    if tpl == "A4":
        return f"""WITH RECURSIVE up(set_num, tid, pid) AS (SELECT s.set_num, t.id, t.parent_id FROM sets s JOIN themes t ON t.id = s.theme_id
  WHERE s.year = {a['year']} UNION ALL SELECT up.set_num, t.id, t.parent_id FROM up JOIN themes t ON t.id = up.pid)
SELECT t.name, count(*) AS n FROM up JOIN themes t ON t.id = up.tid WHERE up.pid IS NULL GROUP BY 1 ORDER BY n DESC LIMIT 3"""
    if tpl == "A5":
        return (f"SELECT sum(e.qty) FROM u_explosion e WHERE {cur} AND {NS} AND e.item_num = {lit(a['part_num'])} AND {col()} "
                f"AND e.root_num IN (SELECT set_num FROM sets WHERE year = {a['year']})")
    if tpl == "A6":
        return SUB_LG(a["theme"]) + f"SELECT count(DISTINCT e.color_id) FROM u_explosion e WHERE {cur} AND {NS} AND e.root_num IN ({inth()})"
    if tpl == "X1":
        ex = lambda v: f"SELECT e.item_num FROM u_explosion e WHERE e.root_kind = 'set' AND e.root_num = {s} AND {v} AND {NS}"
        return f"{ex('e.is_current')} EXCEPT {ex('e.root_version = 1')}"
    if tpl == "X2":
        return SUB_LG(a["theme"]) + (f"SELECT round(avg(coalesce(t.n, 0)), 2) FROM ({inth()}) x LEFT JOIN (SELECT e.root_num, sum(e.qty) AS n "
                                     f"FROM u_explosion e WHERE {cur} AND {NS} GROUP BY 1) t ON t.root_num = x.set_num")
    if tpl == "X3":
        return (f"SELECT count(*) FROM (SELECT e.root_num FROM u_explosion e WHERE {cur} AND e.item_kind = 'minifig' AND e.item_num = {lit(a['fig_num'])} "
                f"INTERSECT SELECT e.root_num FROM u_explosion e WHERE {cur} AND {NS} AND e.item_num = {lit(a['part_num'])})")
    if tpl == "X4":
        own = (f"(SELECT coalesce(sum(qty), 0) FROM u_composition WHERE parent_kind = 'set' AND parent_num = {s} AND child_kind = 'part' "
               f"AND NOT is_spare AND version = (SELECT max(version) FROM u_composition WHERE parent_kind = 'set' AND parent_num = {s}))")
        tot = f"(SELECT sum(e.qty) FROM u_explosion e WHERE {ver} AND {NS})"
        return f"SELECT round(100.0 * ({tot} - {own}) / {tot}, 2)"
    raise KeyError(tpl)


REF = {"xb": ref_xb, "aw": ref_aw, "lego": ref_lego}
# templates whose reference query still needs a recursive CTE over a NON-composition hierarchy (LEGO themes)
THEME_RECURSION = {"lego": {"A1", "A2", "A3", "A4", "A6", "X2"}}
# templates answered from u_composition (first level) or from non-composition tables only
NO_CLOSURE = {"xb": {"L1b", "L1c", "V2", "A2"}, "aw": {"L1", "L2", "L3", "L4", "L5", "V2", "V6x", "A1", "A2", "A4", "A6x"},
              "lego": {"L1", "L2", "L3", "L4", "A1", "A4"}}


def check_questions(ds, con):
    qs = [json.loads(l) for l in open(os.path.join(DATA, f"{ds}_questions.jsonl"))]
    per_tpl, fails = defaultdict(lambda: [0, 0]), []
    for q in qs:
        sql = REF[ds](q)
        try:
            rows = con.execute(sql).fetchall()
            ok = bool(match(rows, q))
        except Exception as e:
            rows, ok = f"ERROR {e}", False
        per_tpl[q["tpl"]][0 if ok else 1] += 1
        if not ok:
            fails.append(dict(qid=q["qid"], tpl=q["tpl"], gold=q["gold"], got=str(rows)[:300], sql=sql))
    per_cls = defaultdict(lambda: [0, 0])
    cls_of = {q["tpl"]: q["cls"] for q in qs}
    for tpl, (p, f) in per_tpl.items():
        per_cls[cls_of[tpl]][0] += p
        per_cls[cls_of[tpl]][1] += f
    return dict(n=len(qs), passed=sum(p for p, f in per_tpl.values()), failed=sum(f for p, f in per_tpl.values()),
                per_class={c: dict(passed=p, failed=f) for c, (p, f) in sorted(per_cls.items())},
                per_template={t: dict(passed=p, failed=f) for t, (p, f) in sorted(per_tpl.items())},
                templates_using_u_explosion=sorted(t for t in per_tpl if t not in NO_CLOSURE[ds]),
                templates_without_closure=sorted(t for t in per_tpl if t in NO_CLOSURE[ds]),
                templates_needing_theme_recursion=sorted(THEME_RECURSION.get(ds, ())), failures=fails)


# =====================================================================================================================
# structure
def check_structure(ds, con):
    if ds == "lego":
        dup = con.execute("""SELECT count(*) FROM (SELECT root_kind, root_num, root_version, item_kind, item_num, color_id, is_spare, count(*) n
                             FROM u_explosion GROUP BY ALL HAVING n > 1)""").fetchone()[0]
        cur = con.execute("""SELECT count(*) FROM (SELECT root_kind, root_num, max(root_version) mv, max(CASE WHEN is_current THEN root_version END) cv
                             FROM u_explosion GROUP BY 1, 2) WHERE mv <> cv OR cv IS NULL""").fetchone()[0]
        zero = con.execute("SELECT count(*) FROM u_explosion WHERE qty <= 0").fetchone()[0]
        return dict(duplicate_keys=dup, roots_with_wrong_is_current=cur, nonpositive_qty=zero, ok=dup == 0 and cur == 0 and zero == 0)
    key = "root_code, item_code" if ds == "aw" else "root_kind, root_code, item_kind, item_code"
    on = " AND ".join(f"a.{k.strip()} = b.{k.strip()}" for k in key.split(","))
    overlap = con.execute(f"""SELECT count(*) FROM (SELECT *, row_number() OVER () AS rid FROM u_explosion) a
        JOIN (SELECT *, row_number() OVER () AS rid FROM u_explosion) b ON {on} AND a.rid < b.rid
        AND a.valid_from <= coalesce(b.valid_to, DATE '9999-12-31') AND b.valid_from <= coalesce(a.valid_to, DATE '9999-12-31')""").fetchone()[0]
    bad = con.execute("SELECT count(*) FROM u_explosion WHERE valid_to < valid_from OR qty <= 0").fetchone()[0]
    # adjacent intervals with equal qty must have been merged
    unmerged = con.execute(f"""SELECT count(*) FROM u_explosion a JOIN u_explosion b ON {on}
        AND b.valid_from = a.valid_to + INTERVAL 1 DAY AND a.qty = b.qty""").fetchone()[0]
    return dict(overlapping_pairs=overlap, empty_or_nonpositive=bad, unmerged_adjacent=unmerged, ok=overlap == 0 and bad == 0 and unmerged == 0)


# =====================================================================================================================
# 200 random triples against gold.World
def x_qty_dated(con, ds, root, item, d):
    if ds == "aw":
        sql = f"SELECT coalesce(sum(qty), 0), count(*) FROM u_explosion e WHERE root_code = ? AND item_code = ? AND {valid('e', d)}"
        return con.execute(sql, [root, item]).fetchone()
    sql = (f"SELECT coalesce(sum(qty), 0), count(*) FROM u_explosion e WHERE root_kind = ? AND root_code = ? AND item_kind = ? "
           f"AND item_code = ? AND {valid('e', d)}")
    return con.execute(sql, [root[0], root[1], item[0], item[1]]).fetchone()


def check_triples(ds, con, G, n=200, seed=20261107):
    rng = random.Random(seed)
    res, bad = [], []
    if ds == "lego":
        W = G.World(os.path.join(DATA, "lego.duckdb"))
        roots = con.execute("SELECT DISTINCT root_kind, root_num, root_version FROM u_explosion ORDER BY ALL").fetchall()
        multi = [r for r in roots if r[2] != max(W.versions(r[1]))]
        items = con.execute("SELECT DISTINCT item_kind, item_num, color_id FROM u_explosion ORDER BY ALL").fetchall()
        nzl = 0
        for i in range(n):
            rk, rn, v = rng.choice(multi) if i % 3 == 0 else rng.choice(roots)
            pe, pi = W.explode(rn, v, True), W.explode(rn, v, False)
            figs, nest = W.minifigs(rn, v), W.nested_sets(rn, v)
            cand = [("part", p, c) for (p, c) in pe] + [("minifig", f, None) for f in figs] + [("set", s, None) for s in nest]
            it = rng.choice(cand) if cand and rng.random() < 0.75 else rng.choice(items)
            ik, inum, icol = it
            got = con.execute("""SELECT coalesce(sum(qty) FILTER (WHERE NOT is_spare), 0), coalesce(sum(qty), 0) FROM u_explosion
                 WHERE root_kind = ? AND root_num = ? AND root_version = ? AND item_kind = ? AND item_num = ? AND color_id IS NOT DISTINCT FROM ?""",
                              [rk, rn, v, ik, inum, icol]).fetchone()
            if ik == "part":
                exp = (pi.get((inum, icol), 0), pe.get((inum, icol), 0))
            else:
                g = (figs if ik == "minifig" else nest).get(inum, 0)
                exp = (g, g)
            ok = tuple(map(float, got)) == tuple(map(float, exp))
            nzl += exp[1] == 0
            res.append(ok)
            if not ok:
                bad.append(dict(root=[rk, rn, v], item=list(it), got=list(got), gold=list(exp)))
        return dict(n=n, passed=sum(res), failed=n - sum(res), zero_expected=nzl, failures=bad[:20])
    if ds == "aw":
        W = G.World(os.path.join(DATA, "aw.duckdb"))
        num = {p: W.prod[p]["num"] for p in W.prod}
        roots = sorted(num[a] for a in W.assemblies())
        by_num = W.by_num
        lo, hi = dt.date(2021, 1, 1), dt.date(2025, 6, 30)
        gold_ex = lambda r, d: {num[c]: q for c, q in W.explode(by_num[r], d).items()}
        allitems = sorted(num[p] for p in W.prod)
        mk = lambda x: x
    else:
        W = G.World(os.path.join(DATA, "xb.duckdb"))
        dupcodes = {r[0] for r in con.execute("SELECT sku_code FROM sales_sku GROUP BY 1 HAVING count(*) > 1").fetchall()}
        cc, mc, sc = (lambda c: W.comp[c]["code"]), (lambda m: W.model[m]["code"]), (lambda s: W.sku[s]["code"])
        roots = ([("sku", sc(s)) for s in sorted(W.sku) if sc(s) not in dupcodes]
                 + [("model", mc(m)) for m in sorted(W.mbom)] + [("component", cc(c)) for c in sorted(W.cbom)])
        lo, hi = dt.date(2022, 1, 1), dt.date(2026, 12, 31)

        def gold_ex(r, d):
            k, code = r
            if k == "component":
                return {("component", cc(c)): q for c, q in W.explode_component(W.comp_by_code[code], d).items()}
            if k == "model":
                return {("component", cc(c)): q for c, q in W.explode_model(W.model_by_code[code], d).items()}
            sid = W.sku_by_code[code]
            out = {("component", cc(c)): q for c, q in W.explode_sku(sid, d).items()}
            out.update({("model", mc(m)): q for m, q in W.sku_models(sid).items()})
            for s2, q in W.bundle.get(sid, []):
                out[("sku", sc(s2))] = out.get(("sku", sc(s2)), 0) + q
            return out
        allitems = [("component", cc(c)) for c in sorted(W.comp)] + [("model", mc(m)) for m in sorted(W.model)]
    nz = 0
    for i in range(n):
        r = rng.choice(roots)
        if rng.random() < 0.3:     # an interval boundary of this root (inclusive bounds matter)
            bs = con.execute("SELECT DISTINCT valid_from, valid_to FROM u_explosion WHERE " +
                             ("root_code = ?" if ds == "aw" else "root_kind = ? AND root_code = ?"),
                             [r] if ds == "aw" else list(r)).fetchall()
            # (no date before the documented 1900-01-01 sentinel of the SKU-level edges; gold.py has no SKU-edge dates)
            pts = sorted({x for a, b in bs for x in (a, b, b and b + ONE, a - ONE) if x and x >= dt.date(1900, 1, 1)})
            d = rng.choice(pts) if pts else lo
        else:
            d = lo + dt.timedelta(days=rng.randrange((hi - lo).days))
        ex = gold_ex(r, d)
        it = rng.choice(sorted(ex, key=str)) if ex and rng.random() < 0.75 else rng.choice(allitems)
        exp = ex.get(it, 0)
        nz += exp == 0
        got, nrow = x_qty_dated(con, ds, r, it, d)
        ok = abs(float(got) - float(exp)) < 1e-9 and nrow <= 1
        res.append(ok)
        if not ok:
            bad.append(dict(root=r, item=it, date=str(d), got=float(got), rows=nrow, gold=float(exp)))
    return dict(n=n, passed=sum(res), failed=n - sum(res), zero_expected=nz, failures=bad[:20])


# =====================================================================================================================
# cypher_x
def check_cypher(ds, con, qs):
    k = kuzu.Connection(kuzu.Database(os.path.join(DATA, f"{ds}_x.kuzu"), read_only=True))
    out = {}
    # (a) EXPLODES equals u_explosion after mapping node keys to codes
    if ds == "xb":
        maps = {"SKU": dict(con.execute("SELECT sku_id, sku_code FROM sales_sku").fetchall()),
                "Model": dict(con.execute("SELECT model_id, model_code FROM company_model").fetchall()),
                "Component": dict(con.execute("SELECT component_id, component_code FROM component").fetchall())}
        kind = {"SKU": "sku", "Model": "model", "Component": "component"}
        pk = {"SKU": "sku_id", "Model": "model_id", "Component": "component_id"}
        krows = []
        for a, b in [("SKU", "SKU"), ("SKU", "Model"), ("SKU", "Component"), ("Model", "Component"), ("Component", "Component")]:
            r = k.execute(f"MATCH (x:{a})-[e:EXPLODES]->(y:{b}) RETURN x.{pk[a]}, y.{pk[b]}, e.qty, e.valid_from, e.valid_to")
            while r.has_next():
                x, y, q, vf, vt = r.get_next()
                krows.append((kind[a], maps[a][x], kind[b], maps[b][y], float(q), vf, vt))
        srows = con.execute("SELECT root_kind, root_code, item_kind, item_code, qty, valid_from, valid_to FROM u_explosion").fetchall()
        dup = {r[0] for r in con.execute("SELECT sku_code FROM sales_sku GROUP BY 1 HAVING count(*) > 1").fetchall()}
        # bundles that contain a duplicated sku_code: the code-keyed u_composition merges both SKU records
        dupb = {r[0] for r in con.execute(f"SELECT DISTINCT parent_code FROM u_composition WHERE child_kind = 'sku' AND child_code IN ({','.join(map(lit, dup))})").fetchall()}
        affected = lambda r: (r[0] == "sku" and (r[1] in dup or r[1] in dupb)) or (r[2] == "sku" and r[3] in dup)
        ks, ss = {r for r in krows if not affected(r)}, {r for r in srows if not affected(r)}
        out["explodes_vs_u_explosion"] = dict(kuzu_rows=len(krows), sql_rows=len(srows), compared_rows=len(ss), equal=ks == ss,
                                              only_kuzu=len(ks - ss), only_sql=len(ss - ks),
                                              excluded_note=f"rows of the {len(dup)} duplicated sku_code values and the {len(dupb)} bundles containing them "
                                                            "(Graph-U is id-keyed, SQL-U code-keyed)")
    elif ds == "aw":
        mp = dict(con.execute("SELECT product_id, product_number FROM product").fetchall())
        r = k.execute("MATCH (x:Product)-[e:EXPLODES]->(y:Product) RETURN x.product_id, y.product_id, e.qty, e.valid_from, e.valid_to")
        krows = []
        while r.has_next():
            x, y, q, vf, vt = r.get_next()
            krows.append((mp[x], mp[y], float(q), vf, vt))
        srows = con.execute("SELECT root_code, item_code, qty, valid_from, valid_to FROM u_explosion").fetchall()
        out["explodes_vs_u_explosion"] = dict(kuzu_rows=len(krows), sql_rows=len(srows), equal=sorted(krows) == sorted(srows))
    else:
        krows = []
        for a, b, pa, pb in [("Set", "Set", "set_num", "set_num"), ("Set", "Minifig", "set_num", "fig_num"),
                             ("Set", "Part", "set_num", "part_num"), ("Minifig", "Part", "fig_num", "part_num")]:
            df = k.execute(f"MATCH (x:{a})-[e:EXPLODES]->(y:{b}) RETURN x.{pa}, e.root_version, e.is_current, y.{pb}, e.color_id, e.is_spare, e.qty").get_as_df()
            for x, v, c, y, col, sp, q in df.itertuples(index=False, name=None):
                krows.append((a.lower(), x, int(v), bool(c), b.lower(), y, None if col != col else int(col), bool(sp), int(q)))
        srows = con.execute("SELECT root_kind, root_num, root_version, is_current, item_kind, item_num, color_id, is_spare, qty FROM u_explosion").fetchall()
        out["explodes_vs_u_explosion"] = dict(kuzu_rows=len(krows), sql_rows=len(srows),
                                              equal=sorted(krows, key=str) == sorted(srows, key=str))
    # (b) a few hand-written Cypher queries over EXPLODES
    cy = []
    for q in qs:
        c = cypher_ref(ds, q)
        if c is None:
            continue
        r = k.execute(c)
        rows = []
        while r.has_next():
            rows.append(r.get_next())
        cy.append((q["qid"], q["tpl"], bool(match(rows, q)), c if not match(rows, q) else None, str(rows)[:200]))
    out["cypher_questions"] = dict(n=len(cy), passed=sum(x[2] for x in cy), failed=sum(not x[2] for x in cy),
                                   templates=sorted({x[1] for x in cy}), failures=[x for x in cy if not x[2]])
    return out


def cypher_ref(ds, q):
    tpl, t = q["tpl"], q["text"]
    v = lambda d: f"e.valid_from <= date('{d}') AND (e.valid_to IS NULL OR date('{d}') <= e.valid_to)"
    if ds == "xb":
        if tpl not in ("E1", "V1", "W1", "E3"):
            return None
        g = re.search(XB_RE[tpl], t).groups()
        if tpl in ("E1", "V1"):
            d = g[2] if tpl == "V1" else XB_TODAY
            return f"MATCH (m:Model {{model_code: '{g[1]}'}})-[e:EXPLODES]->(c:Component {{component_code: '{g[0]}'}}) WHERE {v(d)} RETURN e.qty"
        if tpl == "E3":
            return (f"MATCH (m:Model {{model_code: '{g[0]}'}})-[e:EXPLODES]->(c:Component) WHERE NOT c.is_assembly AND {v(XB_TODAY)} "
                    f"RETURN round(sum(e.qty * c.unit_cost), 2)")
        return f"MATCH (m:Model)-[e:EXPLODES]->(c:Component {{component_code: '{g[0]}'}}) WHERE {v(XB_TODAY)} RETURN count(DISTINCT m.model_code)"
    if ds == "aw":
        a = q["args"]
        if tpl in ("E1", "V1"):
            return (f"MATCH (x:Product {{product_number: '{a['product']}'}})-[e:EXPLODES]->(y:Product {{product_number: '{a['part']}'}}) "
                    f"WHERE {v(a.get('date', AW_TODAY))} RETURN e.qty")
        if tpl in ("E3", "V5"):
            return (f"MATCH (x:Product {{product_number: '{a['product']}'}})-[e:EXPLODES]->(y:Product) WHERE NOT y.make_flag AND "
                    f"{v(a.get('date', AW_TODAY))} RETURN round(sum(e.qty * y.standard_cost), 2)")
        if tpl == "W3":
            return (f"MATCH (x:Product)-[e:EXPLODES]->(y:Product {{product_number: '{a['component']}'}}) WHERE {v(AW_TODAY)} "
                    f"RETURN count(DISTINCT x.product_number)")
        return None
    a = q["args"]
    if tpl in ("E3", "V2", "V5"):
        cond = f"e.root_version = {a['version']}" if "version" in a else "e.is_current"
        return (f"MATCH (s:Set {{set_num: '{a['set_num']}'}})-[e:EXPLODES]->(p:Part) WHERE {cond} AND NOT e.is_spare RETURN sum(e.qty)")
    if tpl == "W3":
        return (f"MATCH (s:Set)-[e:EXPLODES]->(f:Minifig {{fig_num: '{a['fig_num']}'}}) WHERE e.is_current RETURN DISTINCT s.set_num")
    if tpl == "E6":
        return (f"MATCH (s:Set {{set_num: '{a['set_num']}'}})-[e:EXPLODES]->(f:Minifig) WHERE e.is_current RETURN sum(e.qty)")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", default="xb,aw,lego")
    a = ap.parse_args()
    out_path = os.path.join(HERE, "results", "closure_check.json")
    report = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for ds in a.ds.split(","):
        con = duckdb.connect(os.path.join(DATA, f"{ds}_x.duckdb"), read_only=True)
        con.execute("SET max_temp_directory_size = '2GB'")
        G = load_gold(ds)
        r = dict(structure=check_structure(ds, con))
        print(ds, "structure", r["structure"], flush=True)
        r["triples"] = check_triples(ds, con, G)
        print(ds, "triples", {k: v for k, v in r["triples"].items() if k != "failures"}, flush=True)
        r["questions"] = check_questions(ds, con)
        print(ds, "questions", r["questions"]["passed"], "/", r["questions"]["n"], r["questions"]["per_class"], flush=True)
        qs = [json.loads(l) for l in open(os.path.join(DATA, f"{ds}_questions.jsonl"))]
        r["cypher_x"] = check_cypher(ds, con, qs)
        print(ds, "cypher_x", {k: (v if k != "cypher_questions" else {kk: vv for kk, vv in v.items() if kk != "failures"})
                               for k, v in r["cypher_x"].items()}, flush=True)
        report[ds] = r
        con.close()
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1, default=str)


if __name__ == "__main__":
    main()
