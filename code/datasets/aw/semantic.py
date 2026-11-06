"""S3: a governed semantic / metric layer over the AdventureWorks database (data/aw.duckdb).

Same JSON grammar as the xb layer (code/semantic.py): metric, params, group_by, filters, order_by, limit, aggregate.
SPEC is the documentation shown to the LLM; compile_query() is the deterministic JSON -> DuckDB SQL compiler.
"""
import json, re

TODAY = "2025-06-30"

SPEC = """SEMANTIC LAYER `aw` (AdventureWorks; governed metrics; you query it with JSON, not SQL)

Business definitions baked into every metric:
- Composition: an assembly product has a bill of materials (BOM) of component products x quantity; a component that is
  itself manufactured (make_flag = true) has its own BOM, recursively. A "part" is a purchased product (make_flag = false).
  Quantity per unit multiplies along a path and sums over all paths.
- BOM validity: a BOM line is valid on date d if start_date <= d and (end_date is null or d <= end_date).
  "Current" means as of 2025-06-30, which is the default `as_of`.
- Products are identified by product_number (e.g. 'BK-R93R-44'). Revenue is the sum of order-line line_total
  (after discount; order total_due also contains tax and freight and is never used).
- Component demand of orders: every order line requires its own product (units sold directly) plus every component at
  any level of that product's BOM valid on the ORDER DATE (units contained), times the ordered quantity.

Query format (one JSON object):
{"metric": <name>, "params": {...}, "group_by": [<dim>...], "filters": [{"dim": <dim>, "op": "=|!=|in|>|>=|<|<=", "value": <v>}],
 "order_by": [{"field": "value" or <dim>, "desc": true|false}], "limit": <int>, "aggregate": null|"count"|"sum"}
The result has one column per group_by dimension plus a column `value`. `aggregate` is applied last:
"count" returns the number of result rows (it requires group_by), "sum" returns the sum of `value` over result rows.
Dates are 'YYYY-MM-DD' strings. order.year, order.quarter (1-4) and order.month are integers; order.online is true for
online (individual customer) orders and false for reseller orders.

Metrics:
1. bom_quantity -- total quantity of each component needed for ONE unit of a product, exploded through all levels.
   params: product_number (required), as_of (date), max_depth (int, 1 = direct BOM lines only; default unlimited)
   dims: component.number, component.name, component.make_flag (true/false), component.is_finished_good,
         component.subcategory, component.category, component.standard_cost
   value: quantity per one unit of the product
2. purchased_cost -- sum over PARTS (make_flag = false) at any level of (quantity per unit x standard_cost) for ONE unit
   of a product.
   params: product_number (required), as_of
   dims: component.subcategory, component.category
3. where_used -- products that contain a component at any level of their BOM (every assembly product is considered,
   including intermediate sub-assemblies; filter product.category = 'Bikes' for bikes only).
   params: component_number (required), as_of
   dims: product.number, product.name, product.subcategory, product.category, product.make_flag, product.is_finished_good
   value: quantity of the component per one unit of that product
4. units_sold -- sum of order-line quantities.
   dims: product.number, product.name, product.color, product.subcategory, product.category, product.product_line,
         order.date, order.year, order.quarter, order.month, order.online, territory.name, territory.country, territory.group
   value: units
5. revenue -- sum of order-line line_total. Same dims as units_sold.
6. component_demand -- units of each product required by order lines: units sold directly plus units contained at any
   level of the sold products, using the BOM valid on each order date.
   dims: component.number, component.name, component.make_flag, component.subcategory, component.category,
         order.date, order.year, order.quarter, order.month, order.online, territory.name, territory.country, territory.group
   value: units
7. product_attr -- one row per product with its attributes.
   dims: product.number, product.name, product.color, product.subcategory, product.category, product.product_line,
         product.make_flag, product.is_finished_good, product.standard_cost, product.list_price
   value: 1 (number of products in the group)
"""

PROD = "(SELECT p.*, sc.name AS subcategory, pc.name AS category FROM product p LEFT JOIN product_subcategory sc ON sc.subcategory_id = p.subcategory_id LEFT JOIN product_category pc ON pc.category_id = sc.category_id)"


def _pdims(prefix, alias):
    return {f"{prefix}.number": f"{alias}.product_number", f"{prefix}.name": f"{alias}.name", f"{prefix}.color": f"{alias}.color",
            f"{prefix}.subcategory": f"{alias}.subcategory", f"{prefix}.category": f"{alias}.category",
            f"{prefix}.product_line": f"{alias}.product_line", f"{prefix}.make_flag": f"{alias}.make_flag",
            f"{prefix}.is_finished_good": f"{alias}.is_finished_good", f"{prefix}.standard_cost": f"{alias}.standard_cost",
            f"{prefix}.list_price": f"{alias}.list_price"}


def _odims(date, terr, online):
    return {"order.date": date, "order.year": f"year({date})", "order.quarter": f"quarter({date})",
            "order.month": f"month({date})", "order.online": online, "territory.name": f"{terr}.name",
            "territory.country": f"{terr}.country_code", "territory.group": f"{terr}.territory_group"}


COMP_KEYS = ["component.number", "component.name", "component.make_flag", "component.is_finished_good",
             "component.subcategory", "component.category", "component.standard_cost"]
SALE_PKEYS = ["product.number", "product.name", "product.color", "product.subcategory", "product.category", "product.product_line"]


class SemanticError(Exception):
    pass


def _lit(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return f"DATE '{v}'"
        if v.lower() in ("true", "false"):
            return v.upper()
        return "'" + v.replace("'", "''") + "'"
    raise SemanticError(f"bad literal {v!r}")


def _where(filters, dims):
    out = []
    for f in filters or []:
        d, op, v = f.get("dim"), f.get("op", "="), f.get("value")
        if d not in dims:
            raise SemanticError(f"unknown filter dimension {d!r}; allowed: {sorted(k for k in dims if not k.startswith('__'))}")
        if op not in ("=", "!=", "in", ">", ">=", "<", "<="):
            raise SemanticError(f"bad op {op!r}")
        if op == "in":
            if not isinstance(v, list):
                raise SemanticError("'in' needs a list")
            out.append(f"{dims[d]} IN ({', '.join(_lit(x) for x in v)})")
        else:
            out.append(f"{dims[d]} {op} {_lit(v)}")
    return out


def _valid(alias, date_expr):
    return f"{alias}.start_date <= {date_expr} AND ({alias}.end_date IS NULL OR {date_expr} <= {alias}.end_date)"


def _explode(seed, max_depth=None, carry=""):
    """seed: SQL producing (key, product_id, mult, d{carry}) rows; returns CTE text producing
    explode(key, component_id, qty, depth, d{carry}) over BOM lines valid on d."""
    lim = f" AND e.depth < {int(max_depth)}" if max_depth else " AND e.depth < 20"
    cc = "".join(f", s.{c.strip()}" for c in carry.split(",") if c.strip())
    ce = "".join(f", e.{c.strip()}" for c in carry.split(",") if c.strip())
    return f"""seed AS ({seed}),
explode AS (
  SELECT s.key, b.component_id, s.mult * b.per_assembly_qty AS qty, 1 AS depth, s.d{cc} FROM seed s
    JOIN bill_of_materials b ON b.assembly_id = s.product_id AND {_valid('b', 's.d')}
  UNION ALL
  SELECT e.key, b.component_id, e.qty * b.per_assembly_qty, e.depth + 1, e.d{ce} FROM explode e
    JOIN bill_of_materials b ON b.assembly_id = e.component_id AND {_valid('b', 'e.d')}{lim}
)"""


def compile_query(q):
    if isinstance(q, str):
        q = json.loads(q)
    if not isinstance(q, dict) or "metric" not in q:
        raise SemanticError("query must be a JSON object with a 'metric'")
    m, p = q["metric"], q.get("params") or {}
    gb, flt = q.get("group_by") or [], list(q.get("filters") or [])
    as_of = _lit(p.get("as_of", TODAY))
    pd_ = _pdims("component", "c")

    if m in ("bom_quantity", "purchased_cost"):
        if "product_number" not in p:
            raise SemanticError(f"{m} needs params.product_number")
        seed = f"SELECT 0 AS key, product_id, 1.0 AS mult, {as_of} AS d FROM product WHERE product_number = {_lit(p['product_number'])}"
        cte = _explode(seed, p.get("max_depth") if m == "bom_quantity" else None)
        base = f"WITH RECURSIVE {cte}\nSELECT {{sel}} FROM explode e JOIN {PROD} c ON c.product_id = e.component_id"
        if m == "bom_quantity":
            dims = {k: pd_[k] for k in COMP_KEYS}
            val = "SUM(e.qty)"
        else:
            dims = {k: pd_[k] for k in ("component.subcategory", "component.category")}
            dims["__part"] = "NOT c.make_flag"
            flt.append({"dim": "__part", "op": "=", "value": True})
            val = "SUM(e.qty * c.standard_cost)"
    elif m == "where_used":
        if "component_number" not in p:
            raise SemanticError("where_used needs params.component_number")
        seed = f"SELECT DISTINCT assembly_id AS key, assembly_id AS product_id, 1.0 AS mult, {as_of} AS d FROM bill_of_materials WHERE assembly_id IS NOT NULL"
        cte = _explode(seed)
        base = (f"WITH RECURSIVE {cte}\nSELECT {{sel}} FROM explode e JOIN product c ON c.product_id = e.component_id "
                f"JOIN {PROD} pr ON pr.product_id = e.key")
        allp = _pdims("product", "pr")
        dims = {k: allp[k] for k in ("product.number", "product.name", "product.subcategory", "product.category",
                                      "product.make_flag", "product.is_finished_good")}
        dims["__c"] = "c.product_number"
        flt.append({"dim": "__c", "op": "=", "value": p["component_number"]})
        val = "SUM(e.qty)"
    elif m in ("units_sold", "revenue"):
        allp = _pdims("product", "pr")
        dims = {k: allp[k] for k in SALE_PKEYS}
        dims.update(_odims("o.order_date", "t", "o.online_order"))
        base = (f"SELECT {{sel}} FROM sales_order_line l JOIN sales_order o ON o.order_id = l.order_id "
                f"JOIN sales_territory t ON t.territory_id = o.territory_id JOIN {PROD} pr ON pr.product_id = l.product_id")
        val = "SUM(l.order_qty)" if m == "units_sold" else "SUM(l.line_total)"
    elif m == "component_demand":
        odims = _odims("o.order_date", "t", "o.online_order")
        ofl = [f for f in flt if f.get("dim") in odims]
        flt = [f for f in flt if f.get("dim") not in odims]
        ow = " AND ".join(_where(ofl, odims)) or "TRUE"
        lines = (f"SELECT l.product_id, o.order_date AS d, o.territory_id, o.online_order, SUM(l.order_qty) AS q "
                 f"FROM sales_order_line l JOIN sales_order o ON o.order_id = l.order_id "
                 f"JOIN sales_territory t ON t.territory_id = o.territory_id WHERE {ow} GROUP BY ALL")
        seed = (f"SELECT row_number() OVER () AS key, product_id, CAST(q AS DOUBLE) AS mult, d, territory_id, online_order "
                f"FROM ({lines})")
        cte = _explode(seed, None, carry="territory_id, online_order")
        base = (f"WITH RECURSIVE {cte},\nreq AS (SELECT product_id AS component_id, mult AS qty, d, territory_id, online_order FROM seed "
                f"UNION ALL SELECT component_id, qty, d, territory_id, online_order FROM explode)\n"
                f"SELECT {{sel}} FROM req e JOIN {PROD} c ON c.product_id = e.component_id "
                f"JOIN sales_territory t ON t.territory_id = e.territory_id")
        dims = {k: pd_[k] for k in COMP_KEYS if k != "component.standard_cost" and k != "component.is_finished_good"}
        dims.update(_odims("e.d", "t", "e.online_order"))
        val = "SUM(e.qty)"
    elif m == "product_attr":
        dims = _pdims("product", "pr")
        base = f"SELECT {{sel}} FROM {PROD} pr"
        val = "COUNT(*)"
    else:
        raise SemanticError(f"unknown metric {m!r}")

    public = {k for k in dims if not k.startswith("__")}
    for g in gb:
        if g not in public:
            raise SemanticError(f"unknown group_by dimension {g!r} for {m}; allowed: {sorted(public)}")
    sel = ", ".join([f'{dims[g]} AS "{g}"' for g in gb] + [f"{val} AS value"])
    sql = base.replace("{sel}", sel)
    w = _where(flt, dims)
    if w:
        sql += " WHERE " + " AND ".join(w)
    if gb:
        sql += " GROUP BY " + ", ".join(dims[g] for g in gb)
    ob = q.get("order_by") or []
    if ob:
        parts = []
        for o in ob:
            f = o.get("field")
            if f != "value" and f not in gb:
                raise SemanticError(f"order_by field {f!r} must be 'value' or a group_by dimension")
            parts.append(("value" if f == "value" else f'"{f}"') + (" DESC" if o.get("desc") else " ASC"))
        sql += " ORDER BY " + ", ".join(parts)
    if q.get("limit"):
        sql += f" LIMIT {int(q['limit'])}"
    agg = q.get("aggregate")
    if agg == "count" and not gb:
        # validation as in production metric layers: counting the rows of an ungrouped (single-row) result is always 1
        raise SemanticError("aggregate 'count' counts result rows; without group_by the result has one row. "
                            "Add the entity dimension to count to group_by (e.g. its code).")
    if agg == "count":
        sql = f"SELECT COUNT(*) AS value FROM ({sql})"
    elif agg == "sum":
        sql = f"SELECT SUM(value) AS value FROM ({sql})"
    elif agg not in (None, "", "none"):
        raise SemanticError("aggregate must be null, 'count' or 'sum'")
    return sql
