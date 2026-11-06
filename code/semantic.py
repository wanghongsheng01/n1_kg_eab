"""S3: a governed semantic / metric layer over the xb composition database.

The layer follows the MetricFlow / Cube pattern: named metrics with fixed business definitions, a set of
dimensions per metric, and a JSON query (metric, params, group_by, filters, order_by, limit, aggregate)
that a deterministic compiler turns into DuckDB SQL. The LLM never writes SQL in this substrate.
SPEC is the documentation shown to the LLM; compile_query() is the compiler.
"""
import json, re

TODAY = "2026-10-01"

SPEC = """SEMANTIC LAYER `xb` (governed metrics; you query it with JSON, not SQL)

Business definitions baked into every metric:
- Composition: a company model uses components; an assembly component contains further components, recursively.
  A "part" is a non-assembly component. Quantity per unit multiplies along a path and sums over all paths.
- A non-bundle sales SKU maps to one or more company models x qty. A bundle SKU contains item SKUs x qty.
- BOM validity: an edge is valid on date d if valid_from <= d and (valid_to is null or d <= valid_to).
  "Current" means as of 2026-10-01, which is the default `as_of`.
- Order metrics count COMPLETED orders only (cancelled orders are excluded by definition).

Query format (one JSON object):
{"metric": <name>, "params": {...}, "group_by": [<dim>...], "filters": [{"dim": <dim>, "op": "=|!=|in|>|>=|<|<=", "value": <v>}],
 "order_by": [{"field": "value" or <dim>, "desc": true|false}], "limit": <int>, "aggregate": null|"count"|"sum"}
The result has one column per group_by dimension plus a column `value`. `aggregate` is applied last:
"count" returns the number of result rows (it requires group_by), "sum" returns the sum of `value` over result rows.
Dates are 'YYYY-MM-DD' strings. order.year, order.quarter (1-4) and order.month are integers.

Metrics:
1. bom_quantity -- total quantity of each component needed for ONE unit of a product, exploded through all levels.
   params: product_code (required: a model_code, a non-bundle sku_code or a bundle sku_code), as_of (date), max_depth (int, 1 = direct children only; default unlimited)
   dims: component.code, component.name, component.category, component.is_assembly (true/false)
   value: quantity per one unit of the product
2. bom_cost -- sum over PARTS of (quantity per unit x component unit_cost) for ONE unit of a product.
   params: product_code (required), as_of
   dims: component.category
3. where_used -- products that contain a component at any depth.
   params: component_code (required), product_type ("model" | "sku" | "bundle", default "model"), as_of
   dims: product.code, product.name
   value: quantity of the component per one unit of that product
4. units_sold -- units sold in completed orders. With params.level = "model", SKU units are converted to model units
   via the SKU-model mapping (qty) and bundles are expanded into their item SKUs first.
   params: level ("sku" | "model", default "sku")
   dims: product.code (sku_code or model_code by level), model.product_line (level=model only), sku.is_bundle (level=sku only),
         order.date, order.year, order.quarter, order.month, store.name, store.platform, store.country
   value: units
5. revenue -- sum of order-line qty x unit_price over completed orders.
   dims: sku.code, sku.is_bundle, sku.market, order.date, order.year, order.quarter, order.month, store.name, store.platform, store.country
6. part_consumption -- units of PARTS consumed by completed orders: every order line is exploded (bundle -> SKUs -> models -> components)
   using the BOM valid on the ORDER DATE.
   dims: component.code, component.category, order.date, order.year, order.quarter, order.month, store.name, store.platform, store.country
   value: units of parts
7. listing_count -- number of store listings.
   dims: sku.code, store.name, store.platform, store.country
8. sku_model_map -- the SKU to company model mapping (non-bundle SKUs).
   dims: sku.code, model.code, model.product_line
   value: model units per SKU unit
9. component_attr -- one row per component with its attributes.
   dims: component.code, component.name, component.category, component.is_assembly, component.unit_cost
   value: 1
"""

ORDER_DIMS = {"order.date": "o.order_date", "order.year": "year(o.order_date)", "order.quarter": "quarter(o.order_date)",
              "order.month": "month(o.order_date)", "store.name": "st.store_name", "store.platform": "st.platform",
              "store.country": "st.country"}
COMP_DIMS = {"component.code": "c.component_code", "component.name": "c.name", "component.category": "c.category",
             "component.is_assembly": "c.is_assembly", "component.unit_cost": "c.unit_cost"}


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
            raise SemanticError(f"unknown filter dimension {d!r}; allowed: {sorted(dims)}")
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
    return f"{alias}.valid_from <= {date_expr} AND ({alias}.valid_to IS NULL OR {date_expr} <= {alias}.valid_to)"


def _explode_from_models(seed, date_expr_seed, max_depth=None):
    """seed: SQL producing (key, model_id, mult, d) rows; returns CTE text producing explode(key, component_id, qty, depth, d)."""
    lim = f" AND e.depth < {int(max_depth)}" if max_depth else " AND e.depth < 20"
    return f"""seed AS ({seed}),
explode AS (
  SELECT s.key, mb.component_id, s.mult * mb.qty AS qty, 1 AS depth, s.d FROM seed s
    JOIN model_bom mb ON mb.model_id = s.model_id AND {_valid('mb', 's.d')}
  UNION ALL
  SELECT e.key, cb.child_component_id, e.qty * cb.qty, e.depth + 1, e.d FROM explode e
    JOIN component_bom cb ON cb.parent_component_id = e.component_id AND {_valid('cb', 'e.d')}{lim}
)"""


def _product_seed(code, as_of):
    """(key, model_id, mult, d) for one unit of a model, non-bundle SKU or bundle SKU identified by code."""
    c, d = _lit(code), _lit(as_of)
    return f"""SELECT 0 AS key, m.model_id, 1 AS mult, {d} AS d FROM company_model m WHERE m.model_code = {c}
  UNION ALL SELECT 0, sm.model_id, sm.qty, {d} FROM sales_sku s JOIN sku_model sm ON sm.sku_id = s.sku_id WHERE s.sku_code = {c}
  UNION ALL SELECT 0, sm.model_id, bi.qty * sm.qty, {d} FROM sales_sku b JOIN bundle_item bi ON bi.bundle_sku_id = b.sku_id
    JOIN sku_model sm ON sm.sku_id = bi.item_sku_id WHERE b.sku_code = {c}"""


def compile_query(q):
    if isinstance(q, str):
        q = json.loads(q)
    if not isinstance(q, dict) or "metric" not in q:
        raise SemanticError("query must be a JSON object with a 'metric'")
    m, p = q["metric"], q.get("params") or {}
    gb, flt = q.get("group_by") or [], q.get("filters") or []
    as_of = p.get("as_of", TODAY)

    if m in ("bom_quantity", "bom_cost"):
        if "product_code" not in p:
            raise SemanticError("bom metrics need params.product_code")
        cte = _explode_from_models(_product_seed(p["product_code"], as_of), None,
                                   p.get("max_depth") if m == "bom_quantity" else None)
        dims = dict(COMP_DIMS)
        if m == "bom_quantity":
            base = f"WITH RECURSIVE {cte}\nSELECT {{sel}} FROM explode e JOIN component c ON c.component_id = e.component_id"
            val = "SUM(e.qty)"
        else:
            dims = {"component.category": "c.category"}
            base = f"WITH RECURSIVE {cte}\nSELECT {{sel}} FROM explode e JOIN component c ON c.component_id = e.component_id"
            flt = list(flt) + [{"dim": "__part", "op": "=", "value": True}]
            dims["__part"] = "NOT c.is_assembly"
            val = "SUM(e.qty * c.unit_cost)"
    elif m == "where_used":
        if "component_code" not in p:
            raise SemanticError("where_used needs params.component_code")
        pt = p.get("product_type", "model")
        d = _lit(as_of)
        seed = {"model": f"SELECT m.model_id AS key, m.model_id, 1 AS mult, {d} AS d FROM company_model m",
                "sku": f"SELECT sm.sku_id AS key, sm.model_id, sm.qty AS mult, {d} AS d FROM sku_model sm",
                "bundle": f"SELECT bi.bundle_sku_id AS key, sm.model_id, bi.qty * sm.qty AS mult, {d} AS d FROM bundle_item bi JOIN sku_model sm ON sm.sku_id = bi.item_sku_id"}
        if pt not in seed:
            raise SemanticError("product_type must be model, sku or bundle")
        cte = _explode_from_models(seed[pt], None)
        prod = ("(SELECT model_id AS key, model_code AS code, name FROM company_model)" if pt == "model"
                else "(SELECT sku_id AS key, sku_code AS code, title AS name FROM sales_sku)")
        dims = {"product.code": "pr.code", "product.name": "pr.name"}
        base = (f"WITH RECURSIVE {cte}\nSELECT {{sel}} FROM explode e JOIN component c ON c.component_id = e.component_id "
                f"JOIN {prod} pr ON pr.key = e.key")
        flt = list(flt) + [{"dim": "__c", "op": "=", "value": p["component_code"]}]
        dims["__c"] = "c.component_code"
        val = "SUM(e.qty)"
    elif m == "units_sold":
        lvl = p.get("level", "sku")
        dims = dict(ORDER_DIMS)
        if lvl == "sku":
            dims.update({"product.code": "s.sku_code", "sku.is_bundle": "s.is_bundle"})
            base = ("SELECT {sel} FROM order_line ol JOIN orders o ON o.order_id = ol.order_id AND o.status = 'completed' "
                    "JOIN listing li ON li.listing_id = ol.listing_id JOIN store st ON st.store_id = o.store_id "
                    "JOIN sales_sku s ON s.sku_id = li.sku_id")
            val = "SUM(ol.qty)"
        elif lvl == "model":
            dims.update({"product.code": "m.model_code", "model.product_line": "m.product_line"})
            base = ("WITH unit AS (SELECT sku_id AS sold, sku_id AS item, 1 AS q FROM sales_sku WHERE NOT is_bundle "
                    "UNION ALL SELECT bundle_sku_id, item_sku_id, qty FROM bundle_item)\n"
                    "SELECT {sel} FROM order_line ol JOIN orders o ON o.order_id = ol.order_id AND o.status = 'completed' "
                    "JOIN listing li ON li.listing_id = ol.listing_id JOIN store st ON st.store_id = o.store_id "
                    "JOIN unit u ON u.sold = li.sku_id JOIN sku_model sm ON sm.sku_id = u.item "
                    "JOIN company_model m ON m.model_id = sm.model_id")
            val = "SUM(ol.qty * u.q * sm.qty)"
        else:
            raise SemanticError("level must be sku or model")
    elif m == "revenue":
        dims = dict(ORDER_DIMS)
        dims.update({"sku.code": "s.sku_code", "sku.is_bundle": "s.is_bundle", "sku.market": "s.market"})
        base = ("SELECT {sel} FROM order_line ol JOIN orders o ON o.order_id = ol.order_id AND o.status = 'completed' "
                "JOIN listing li ON li.listing_id = ol.listing_id JOIN store st ON st.store_id = o.store_id "
                "JOIN sales_sku s ON s.sku_id = li.sku_id")
        val = "SUM(ol.qty * ol.unit_price)"
    elif m == "part_consumption":
        dims = dict(ORDER_DIMS)
        dims.update({"component.code": "c.component_code", "component.category": "c.category"})
        ofl = [f for f in flt if f.get("dim") in ORDER_DIMS]
        rest = [f for f in flt if f.get("dim") not in ORDER_DIMS]
        ow = " AND ".join(["o.status = 'completed'"] + _where(ofl, ORDER_DIMS))
        seed = ("SELECT ol.order_id * 1000 + ol.line_no AS key, sm.model_id, ol.qty * u.q * sm.qty AS mult, o.order_date AS d "
                "FROM order_line ol JOIN orders o ON o.order_id = ol.order_id JOIN store st ON st.store_id = o.store_id "
                "JOIN listing li ON li.listing_id = ol.listing_id "
                "JOIN (SELECT sku_id AS sold, sku_id AS item, 1 AS q FROM sales_sku WHERE NOT is_bundle "
                "UNION ALL SELECT bundle_sku_id, item_sku_id, qty FROM bundle_item) u ON u.sold = li.sku_id "
                f"JOIN sku_model sm ON sm.sku_id = u.item WHERE {ow}")
        cte = _explode_from_models(seed, None)
        base = (f"WITH RECURSIVE {cte}, line AS (SELECT DISTINCT ol.order_id * 1000 + ol.line_no AS key, o.order_date, "
                "st.store_name, st.platform, st.country FROM order_line ol JOIN orders o ON o.order_id = ol.order_id "
                "JOIN store st ON st.store_id = o.store_id)\n"
                "SELECT {sel} FROM explode e JOIN component c ON c.component_id = e.component_id AND NOT c.is_assembly "
                "JOIN line ln ON ln.key = e.key")
        dims = {"order.date": "ln.order_date", "order.year": "year(ln.order_date)", "order.quarter": "quarter(ln.order_date)",
                "order.month": "month(ln.order_date)", "store.name": "ln.store_name", "store.platform": "ln.platform",
                "store.country": "ln.country", "component.code": "c.component_code", "component.category": "c.category"}
        flt = rest
        val = "SUM(e.qty)"
    elif m == "listing_count":
        dims = {"sku.code": "s.sku_code", "store.name": "st.store_name", "store.platform": "st.platform", "store.country": "st.country"}
        base = "SELECT {sel} FROM listing li JOIN sales_sku s ON s.sku_id = li.sku_id JOIN store st ON st.store_id = li.store_id"
        val = "COUNT(*)"
    elif m == "sku_model_map":
        dims = {"sku.code": "s.sku_code", "model.code": "m.model_code", "model.product_line": "m.product_line"}
        base = "SELECT {sel} FROM sku_model sm JOIN sales_sku s ON s.sku_id = sm.sku_id JOIN company_model m ON m.model_id = sm.model_id"
        val = "SUM(sm.qty)"
    elif m == "component_attr":
        dims = dict(COMP_DIMS)
        base = "SELECT {sel} FROM component c"
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
