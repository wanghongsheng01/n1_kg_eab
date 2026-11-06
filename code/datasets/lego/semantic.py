"""S3: a governed semantic / metric layer over the LEGO (Rebrickable) composition database data/lego.duckdb.

Same JSON grammar and compiler contract as the xb layer (code/semantic.py): named metrics with fixed business
definitions, dimensions per metric, and a JSON query (metric, params, group_by, filters, order_by, limit, aggregate)
compiled deterministically to DuckDB SQL. SPEC is the documentation shown to the LLM; compile_query() is the compiler.
"""
import json, re

SPEC = """SEMANTIC LAYER `lego` (governed metrics; you query it with JSON, not SQL)

Business definitions baked into every metric:
- A set's contents come from its inventory. A set may have several inventory versions (1, 2, ...); the CURRENT inventory
  is the highest version and is used unless params.version names another one. A named version applies to the queried
  set's own inventory only; nested sets and minifigs inside it always use their own current inventory.
- An inventory lists parts (part x colour x quantity, flagged spare or not), nested sets x quantity and minifigs x quantity.
  A minifig has its own inventory of parts. Contents are exploded recursively: quantities multiply along the nesting
  path and sum over all paths ("at any depth" = through nested sets and minifigs).
- Spare parts are excluded unless params.include_spares = true.
- Part identity: group/filter by part.part_num alone to treat all colours together; add color.name for part x colour.
- Themes form a hierarchy. theme.name is the set's own theme; theme.root is its top-level ancestor; theme.rollup is
  every theme on the path from the set's theme up to the root (the set's theme itself included), so filtering
  theme.rollup = T selects the sets of T and of all its sub-themes. Grouping by theme.rollup counts a set once under
  each of its ancestor themes.
- Minifigs are not sets. Catalog metrics explode every set on its own (a set nested in another is also counted itself).

Query format (one JSON object):
{"metric": <name>, "params": {...}, "group_by": [<dim>...], "filters": [{"dim": <dim>, "op": "=|!=|in|>|>=|<|<=", "value": <v>}],
 "order_by": [{"field": "value" or <dim>, "desc": true|false}], "limit": <int>, "aggregate": null|"count"|"sum"}
The result has one column per group_by dimension plus a column `value`. `aggregate` is applied last:
"count" returns the number of result rows (it requires group_by), "sum" returns the sum of `value` over result rows.
Set dims (where available): set.set_num, set.name, set.year (integer), theme.name, theme.root, theme.rollup.

Metrics:
1. part_quantity -- exploded part quantities for ONE unit of one set (or one minifig).
   params: set_num (required; a set_num or a fig_num), version (int; default current), include_spares (bool, default false)
   dims: part.part_num, part.name, part.category, color.name
   value: quantity per one unit of the set
2. minifig_quantity -- exploded minifig quantities for ONE unit of one set (minifigs inside nested sets included).
   params: set_num (required), version (int; default current)
   dims: minifig.fig_num, minifig.name
   value: quantity per one unit of the set
3. set_contents -- the FIRST-LEVEL rows of one inventory of one set (no explosion; spares included as listed).
   params: set_num (required), version (int; default current)
   dims: item.kind ('part' | 'set' | 'minifig'), item.code (part_num / set_num / fig_num), color.name (parts only),
         item.is_spare (true/false)
   value: listed quantity
4. where_used -- sets that contain an item at any depth in their current exploded contents.
   params: exactly one of part_num | fig_num | set_num; color (colour name, only with part_num; default any colour);
           include_spares (bool, default false)
   dims: set dims
   value: quantity of the item per one unit of that set
5. catalog_parts -- exploded part quantities summed over sets (each set's current inventory exploded on its own).
   params: include_spares (bool, default false)
   dims: set dims, part.part_num, part.name, part.category, color.name
   value: part units
6. catalog_minifigs -- exploded minifig quantities summed over sets (each set exploded on its own).
   dims: set dims, minifig.fig_num, minifig.name
   value: minifig units
7. set_attr -- one row per set.
   dims: set dims, set.num_parts (the catalogue's printed piece count, not an explosion)
   value: number of sets
8. inventory_versions -- one row per inventory version of a set.
   dims: set dims, inventory.version
   value: number of inventory versions
"""


class SemanticError(Exception):
    pass


def _lit(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
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


CUR = "cur AS (SELECT set_num, id AS inv_id FROM inventories QUALIFY row_number() OVER (PARTITION BY set_num ORDER BY version DESC) = 1)"
THEME_CLOSURE = """tc AS (SELECT id AS theme_id, id AS anc_id FROM themes
  UNION ALL SELECT tc.theme_id, t.parent_id FROM tc JOIN themes t ON t.id = tc.anc_id WHERE t.parent_id IS NOT NULL)"""


def _reach(seed):
    """reach(root, node, kind, inv_id, mult, depth): every item reached from a root through nested sets / minifigs."""
    return f"""reach AS (
  {seed}
  UNION ALL
  SELECT r.root, k.child, k.kind, c.inv_id, r.mult * k.qty, r.depth + 1 FROM reach r
    JOIN (SELECT inventory_id, set_num AS child, 'set' AS kind, quantity AS qty FROM inventory_sets
          UNION ALL SELECT inventory_id, fig_num, 'minifig', quantity FROM inventory_minifigs) k ON k.inventory_id = r.inv_id
    LEFT JOIN cur c ON c.set_num = k.child
  WHERE r.depth < 10)"""


def _one_seed(p):
    if "set_num" not in p:
        raise SemanticError("this metric needs params.set_num")
    s = _lit(str(p["set_num"]))
    if p.get("version") is not None:
        try:
            v = int(p["version"])
        except (TypeError, ValueError):
            raise SemanticError("version must be an integer")
        return f"SELECT set_num AS root, set_num AS node, 'root' AS kind, id AS inv_id, 1 AS mult, 0 AS depth FROM inventories WHERE set_num = {s} AND version = {v}"
    return f"SELECT set_num AS root, set_num AS node, 'root' AS kind, inv_id, 1 AS mult, 0 AS depth FROM cur WHERE set_num = {s}"


ALL_SEED = "SELECT s.set_num AS root, s.set_num AS node, 'root' AS kind, c.inv_id, 1 AS mult, 0 AS depth FROM sets s LEFT JOIN cur c ON c.set_num = s.set_num"

SET_DIMS = {"set.set_num": "st.set_num", "set.name": "st.name", "set.year": "st.year", "theme.name": "th.name",
            "theme.root": "thr.name", "theme.rollup": "thu.name"}
PART_DIMS = {"part.part_num": "p.part_num", "part.name": "p.name", "part.category": "pc.name", "color.name": "co.name"}


def _set_join(alias_root):
    return (f" JOIN sets st ON st.set_num = {alias_root} JOIN themes th ON th.id = st.theme_id"
            " JOIN tc tcr ON tcr.theme_id = st.theme_id JOIN themes thr ON thr.id = tcr.anc_id AND thr.parent_id IS NULL{rollup}")


def _spares(p):
    v = p.get("include_spares", False)
    if isinstance(v, str):
        v = v.lower() == "true"
    return "" if v else " AND NOT ip.is_spare"


def compile_query(q):
    if isinstance(q, str):
        q = json.loads(q)
    if not isinstance(q, dict) or "metric" not in q:
        raise SemanticError("query must be a JSON object with a 'metric'")
    m, p = q["metric"], q.get("params") or {}
    gb, flt = list(q.get("group_by") or []), list(q.get("filters") or [])
    uses_set = True

    if m == "part_quantity":
        uses_set = False
        dims = dict(PART_DIMS)
        base = (f"WITH RECURSIVE {CUR}, {_reach(_one_seed(p))}\nSELECT {{sel}} FROM reach r "
                f"JOIN inventory_parts ip ON ip.inventory_id = r.inv_id{_spares(p)} JOIN parts p ON p.part_num = ip.part_num "
                "JOIN part_categories pc ON pc.id = p.part_cat_id JOIN colors co ON co.id = ip.color_id")
        val = "SUM(r.mult * ip.quantity)"
    elif m == "minifig_quantity":
        uses_set = False
        dims = {"minifig.fig_num": "mf.fig_num", "minifig.name": "mf.name"}
        base = (f"WITH RECURSIVE {CUR}, {_reach(_one_seed(p))}\nSELECT {{sel}} FROM reach r "
                "JOIN minifigs mf ON mf.fig_num = r.node AND r.kind = 'minifig'")
        val = "SUM(r.mult)"
    elif m == "set_contents":
        uses_set = False
        seed = _one_seed(p).replace("SELECT set_num AS root, set_num AS node, 'root' AS kind, ", "SELECT ").replace(
            ", 1 AS mult, 0 AS depth", "")
        dims = {"item.kind": "x.kind", "item.code": "x.code", "color.name": "co.name", "item.is_spare": "x.is_spare"}
        base = (f"WITH {CUR}, inv AS ({seed}), x AS ("
                "SELECT 'part' AS kind, ip.part_num AS code, ip.color_id, ip.is_spare, ip.quantity AS qty FROM inventory_parts ip JOIN inv ON inv.inv_id = ip.inventory_id "
                "UNION ALL SELECT 'set', s.set_num, NULL, FALSE, s.quantity FROM inventory_sets s JOIN inv ON inv.inv_id = s.inventory_id "
                "UNION ALL SELECT 'minifig', f.fig_num, NULL, FALSE, f.quantity FROM inventory_minifigs f JOIN inv ON inv.inv_id = f.inventory_id)\n"
                "SELECT {sel} FROM x LEFT JOIN colors co ON co.id = x.color_id")
        val = "SUM(x.qty)"
    elif m == "where_used":
        keys = [k for k in ("part_num", "fig_num", "set_num") if p.get(k)]
        if len(keys) != 1:
            raise SemanticError("where_used needs exactly one of params.part_num, params.fig_num, params.set_num")
        key = keys[0]
        dims = dict(SET_DIMS)
        head = f"WITH RECURSIVE {CUR}, {THEME_CLOSURE}, {_reach(ALL_SEED)}\nSELECT {{sel}} FROM reach r"
        if key == "part_num":
            base = head + f" JOIN inventory_parts ip ON ip.inventory_id = r.inv_id{_spares(p)} AND ip.part_num = {_lit(str(p['part_num']))}"
            if p.get("color") not in (None, "", "any"):
                base += f" JOIN colors co ON co.id = ip.color_id AND co.name = {_lit(str(p['color']))}"
            val = "SUM(r.mult * ip.quantity)"
        else:
            kind = "minifig" if key == "fig_num" else "set"
            base = head
            flt.append({"dim": "__node", "op": "=", "value": str(p[key])})
            flt.append({"dim": "__kind", "op": "=", "value": kind})
            dims.update({"__node": "r.node", "__kind": "r.kind"})
            val = "SUM(r.mult)"
        base += _set_join("r.root")
    elif m == "catalog_parts":
        dims = {**SET_DIMS, **PART_DIMS}
        base = (f"WITH RECURSIVE {CUR}, {THEME_CLOSURE}, {_reach(ALL_SEED)}\nSELECT {{sel}} FROM reach r "
                f"JOIN inventory_parts ip ON ip.inventory_id = r.inv_id{_spares(p)} JOIN parts p ON p.part_num = ip.part_num "
                "JOIN part_categories pc ON pc.id = p.part_cat_id JOIN colors co ON co.id = ip.color_id" + _set_join("r.root"))
        val = "SUM(r.mult * ip.quantity)"
    elif m == "catalog_minifigs":
        dims = {**SET_DIMS, "minifig.fig_num": "mf.fig_num", "minifig.name": "mf.name"}
        base = (f"WITH RECURSIVE {CUR}, {THEME_CLOSURE}, {_reach(ALL_SEED)}\nSELECT {{sel}} FROM reach r "
                "JOIN minifigs mf ON mf.fig_num = r.node AND r.kind = 'minifig'" + _set_join("r.root"))
        val = "SUM(r.mult)"
    elif m == "set_attr":
        dims = {**SET_DIMS, "set.num_parts": "st.num_parts"}
        base = f"WITH RECURSIVE {THEME_CLOSURE}\nSELECT {{sel}} FROM (SELECT set_num AS root FROM sets) r" + _set_join("r.root")
        val = "COUNT(*)"
    elif m == "inventory_versions":
        dims = {**SET_DIMS, "inventory.version": "i.version"}
        base = (f"WITH RECURSIVE {THEME_CLOSURE}\nSELECT {{sel}} FROM inventories i" + _set_join("i.set_num"))
        val = "COUNT(*)"
    else:
        raise SemanticError(f"unknown metric {m!r}")

    public = {k for k in dims if not k.startswith("__")}
    for g in gb:
        if g not in public:
            raise SemanticError(f"unknown group_by dimension {g!r} for {m}; allowed: {sorted(public)}")
    if uses_set:
        used = set(gb) | {f.get("dim") for f in flt}
        rollup = (" JOIN tc tcu ON tcu.theme_id = st.theme_id JOIN themes thu ON thu.id = tcu.anc_id"
                  if "theme.rollup" in used else "")
        base = base.replace("{rollup}", rollup)
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
