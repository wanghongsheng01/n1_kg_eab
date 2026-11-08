"""MetricFlow compile server (runs in .tools/mf_venv). Compiles MetricFlow queries to DuckDB SQL with the MetricFlow
engine itself (MetricFlowEngine.explain, the code path of `mf query --explain`); it never executes SQL and never opens
the database file (dbt target `compile` is an in-memory DuckDB; relation names come from the semantic manifest).

Protocol (one JSON object per line on stdin -> one per line on stdout):
  {"q": {"metrics": [...], "group_by": [...], "where": [...], "order_by": [...], "limit": n}}  ->  {"sql": "..."} | {"error": "..."}
Usage: .tools/mf_venv/bin/python code/metricflow/mf_server.py <ds>
"""
import io, json, logging, os, sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
logging.disable(logging.WARNING)


def engine(ds):
    pdir = os.path.join(HERE, ds)
    os.environ.update(DBT_PROFILES_DIR=pdir, DBT_PROJECT_DIR=pdir, MF_TARGET="compile",
                      DBT_SEND_ANONYMOUS_USAGE_STATS="false", MF_DISABLE_TELEMETRY="1")
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    import pathlib
    cfg = CLIConfiguration()
    with redirect_stdout(io.StringIO()):
        cfg.setup(dbt_profiles_path=pathlib.Path(pdir), dbt_project_path=pathlib.Path(pdir), configure_file_logging=False)
    return cfg.mf


def normalize(q):
    """Validate the query JSON and return the MetricFlowQueryRequest arguments."""
    if isinstance(q, str):
        q = json.loads(q)
    if not isinstance(q, dict):
        raise ValueError("the query must be a JSON object")
    unknown = set(q) - {"metrics", "group_by", "where", "order_by", "limit"}
    if unknown:
        raise ValueError(f"unknown query keys {sorted(unknown)}; allowed: metrics, group_by, where, order_by, limit")
    lst = lambda v: [] if v is None else ([v] if isinstance(v, str) else list(v))
    metrics, gb, where, ob = lst(q.get("metrics")), lst(q.get("group_by")), lst(q.get("where")), lst(q.get("order_by"))
    if not metrics:
        raise ValueError("the query needs at least one metric in 'metrics'")
    for x in metrics + gb + where + ob:
        if not isinstance(x, str):
            raise ValueError(f"metrics, group_by, where and order_by must be lists of strings; got {x!r}")
    limit = q.get("limit")
    if limit is not None:
        limit = int(limit)
    return dict(metric_names=metrics, group_by_names=gb or None, where_constraints=where or None,
                order_by_names=ob or None, limit=limit)


def compile_sql(eng, q):
    from metricflow.engine.metricflow_engine import MetricFlowQueryRequest
    req = MetricFlowQueryRequest.create_with_random_request_id(**normalize(q))
    res = eng.explain(mf_request=req)
    return res.sql_statement.without_descriptions.sql


# ---------------------------------------------------------------------------------------------------------------------
# documentation shown to the LLM, rendered from the loaded semantic manifest (`mf list metrics` / `mf list dimensions`)
HEADERS = {}
QUERY_FORMAT = """Query format: ONE JSON object holding the arguments of `mf query`:
{"metrics": [<metric name>, ...], "group_by": [<group-by item>, ...], "where": [<filter>, ...],
 "order_by": [<metric or group-by item>, or "-<...>" for descending], "limit": <int>}
- A group-by item is an entity name (e.g. "{ent}"; the result column holds its code) or a dimension
  "<entity>__<dimension>" (e.g. "{dim}"). Time dimensions take a grain suffix: "metric_time__year", "metric_time__quarter",
  "metric_time__month", "metric_time__day" (or "<entity>__<time dimension>__<grain>"); the group-by item
  "TimeDimension('metric_time', date_part_name='year')" (also 'quarter', 'month') gives the integer year / quarter / month.
- Each where filter is a SQL boolean expression over templated references; all filters are ANDed:
  {{ Dimension('<entity>__<dimension>') }} = 'value', {{ Entity('<entity>') }} = 'code',
  {{ TimeDimension('metric_time', 'day') }} >= '2024-01-01', {{ TimeDimension('metric_time', 'year') }} = '2024-01-01'
  (a grain truncates the date: year -> Jan 1, quarter -> first day of the quarter), and
  {{ TimeDimension('metric_time', date_part_name='year') }} = 2024 (also 'quarter', 'month').
  A filter may use any dimension or entity listed for the metric, also ones not in group_by.
- The result has one column per group-by item plus one column per metric (named like the metric).
- MetricFlow has no query parameters and no defaults: the product / component / set / item a metric is about is selected
  with an Entity filter, and every other choice (BOM state, version, kind, ...) with a dimension filter.
"""


def render_spec(ds, eng):
    import yaml
    sem = yaml.safe_load(open(os.path.join(HERE, ds, "models", "semantic_models.yml")))["semantic_models"]
    met_yaml = {m["name"]: m for m in yaml.safe_load(open(os.path.join(HERE, ds, "models", "metrics.yml")))["metrics"]}
    ddesc = {}
    for s in sem:
        prim = s.get("primary_entity") or next(e["name"] for e in s["entities"] if e["type"] == "primary")
        for d in s["dimensions"]:
            ddesc[f"{prim}__{d['name']}"] = (d.get("description"), d["type"])
    ents = {"xb": ("component", "component__category"), "aw": ("component", "component__category"),
            "lego": ("item", "set__release_year")}[ds]
    out = [HEADERS[ds].strip(), "", QUERY_FORMAT.replace("{ent}", ents[0]).replace("{dim}", ents[1]), "Metrics:"]
    used = set()
    known = {m.name: m for m in eng.list_metrics(include_dimensions=False)}
    for i, name in enumerate(met_yaml, 1):
        m, y = known[name], met_yaml[name]
        ent = sorted({e.name for e in eng.entities_for_metrics([m.name])})
        dims = []
        for d in eng.simple_dimensions_for_metrics([m.name]):
            n = d.dunder_name
            if n.startswith("metric_time") or n in dims or n.split("__")[-1] in TECHNICAL:
                continue
            dims.append(n)
            used.add(n)
        out.append(f"{i}. {m.name} -- {y['description']}")
        if y.get("filter"):
            out.append(f"   always applied (metric filter): {y['filter'].strip()}")
        out.append(f"   entities: {', '.join(ent)}")
        out.append(f"   dimensions: {', '.join(dims)}" + (", metric_time" if any(
            d.dunder_name.startswith("metric_time") for d in eng.simple_dimensions_for_metrics([m.name])) else ""))
    out += ["", "Dimension notes:"]
    for n in sorted(used):
        desc, typ = ddesc.get(n, (None, None))
        if desc or typ == "time":
            out.append(f"- {n}: {(desc or '').strip()}{' (time dimension)' if typ == 'time' else ''}")
    out.append("- metric_time: " + METRIC_TIME[ds])
    return "\n".join(out) + "\n"


# constant agg-time dimensions that MetricFlow requires on every measure-bearing semantic model; not shown to the LLM
TECHNICAL = {"catalog_date", "snapshot_date", "map_date", "listing_date", "inventory_date"}

METRIC_TIME = {
    "xb": "the order date for order metrics (units_sold, units_sold_model, revenue, part_consumption); not meaningful for "
          "BOM, listing and mapping metrics.",
    "aw": "the order date for units_sold, revenue and component_demand; not meaningful for BOM and product metrics.",
    "lego": "the release year for set_attr; not meaningful for the other metrics.",
}

HEADERS["xb"] = """SEMANTIC LAYER `xb` (a dbt MetricFlow semantic layer: governed metrics; you query it with a MetricFlow query, not SQL)

Business definitions baked into every metric:
- Composition: a company model uses components; an assembly component contains further components, recursively.
  A "part" is a non-assembly component (component__is_assembly = false). Quantity per unit multiplies along a path and
  sums over all paths. A non-bundle sales SKU maps to one or more company models x qty. A bundle SKU contains item SKUs x qty.
  The BOM metrics are defined over the materialised all-level explosion (one row per product x contained item x validity
  interval); products are company models, non-bundle SKUs and bundle SKUs (explosion__root_kind 'model' | 'sku',
  product__is_bundle tells bundles apart).
- BOM validity: a BOM row is valid on date d if valid_from <= d and (valid_to is null or d <= valid_to). "Current" means
  as of 2026-10-01. Every BOM metric query must select ONE BOM state, either the current BOM:
  {{ Dimension('explosion__is_current') }} = true   (composition__is_current for bom_direct_quantity)
  or the BOM valid on a date d:
  {{ TimeDimension('explosion__valid_from', 'day') }} <= 'd' AND ({{ TimeDimension('explosion__valid_to', 'day') }} IS NULL OR {{ TimeDimension('explosion__valid_to', 'day') }} >= 'd')
- Order metrics count COMPLETED orders only (cancelled orders are excluded by definition); part_consumption explodes
  each order line with the BOM valid on its order date.
- Codes: company models, SKUs and components are identified by their codes (entities model / sku / product / component).
"""

HEADERS["aw"] = """SEMANTIC LAYER `aw` (AdventureWorks; a dbt MetricFlow semantic layer: governed metrics; you query it with a MetricFlow query, not SQL)

Business definitions baked into every metric:
- Composition: an assembly product has a bill of materials (BOM) of component products x quantity; a component that is
  itself manufactured (make_flag = true) has its own BOM, recursively. A "part" is a purchased product (make_flag = false).
  Quantity per unit multiplies along a path and sums over all paths. The BOM metrics are defined over the materialised
  all-level explosion (one row per product x contained component x validity interval).
- BOM validity: a BOM line is valid on date d if valid_from <= d and (valid_to is null or d <= valid_to). "Current" means
  as of 2025-06-30. Every BOM metric query must select ONE BOM state, either the current BOM:
  {{ Dimension('explosion__is_current') }} = true   (composition__is_current for bom_direct_quantity)
  or the BOM valid on a date d:
  {{ TimeDimension('explosion__valid_from', 'day') }} <= 'd' AND ({{ TimeDimension('explosion__valid_to', 'day') }} IS NULL OR {{ TimeDimension('explosion__valid_to', 'day') }} >= 'd')
- Products are identified by product_number (e.g. 'BK-R93R-44'), the value of the entities product and component.
  Revenue is the sum of order-line line_total (after discount; order total_due also contains tax and freight and is never used).
- Component demand of orders: every order line requires its own product (units sold directly) plus every component at
  any level of that product's BOM valid on the ORDER DATE (units contained), times the ordered quantity.
- sales_line__online / demand__online is true for online (individual customer) orders and false for reseller orders.
"""

HEADERS["lego"] = """SEMANTIC LAYER `lego` (a dbt MetricFlow semantic layer: governed metrics; you query it with a MetricFlow query, not SQL)

Business definitions baked into every metric:
- A set's contents come from its inventory. A set may have several inventory versions (1, 2, ...); the CURRENT inventory
  is the highest version. Per-set metrics (part_quantity, minifig_quantity, distinct_*, set_contents*) hold one row group
  per inventory version of the queried set, so every such query must select ONE version, either the current one:
  {{ Dimension('explosion__is_current') }} = true   (composition__is_current for set_contents*)
  or a named one: {{ Dimension('explosion__root_version') }} = 2. A named version applies to the queried set's own
  inventory only; nested sets and minifigs inside it always use their own current inventory.
- An inventory lists parts (part x colour x quantity, flagged spare or not), nested sets x quantity and minifigs x quantity.
  A minifig has its own inventory of parts. Contents are exploded recursively: quantities multiply along the nesting
  path and sum over all paths ("at any depth" = through nested sets and minifigs).
- Spare parts are excluded by the metric definitions (part_quantity_incl_spares includes them).
- Part identity: the item entity is the part_num (all colours together); add color__name for part x colour.
- Themes form a hierarchy. set__theme_name is the set's own theme; set__theme_root is its top-level ancestor;
  set__theme_level_1/2/3 is the theme path from the root down to the set's theme. The sets of theme T and of all its
  sub-themes are those with T in one of theme_level_1, theme_level_2, theme_level_3.
- Minifigs are not sets. Catalog metrics (catalog_*, where_used*) explode every set's CURRENT inventory on its own (a set
  nested in another is also counted itself).
"""


def main():
    ds = sys.argv[1]
    eng = engine(ds)
    real_stdout = sys.stdout
    print(json.dumps({"ready": True}), file=real_stdout, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            with redirect_stdout(io.StringIO()):
                sql = compile_sql(eng, req["q"])
            resp = {"sql": sql}
        except Exception as e:  # report MetricFlow's own error text (it is what the LLM gets on repair)
            msg = str(e).strip() or type(e).__name__
            resp = {"error": f"{type(e).__name__}: {msg}"}
        print(json.dumps(resp), file=real_stdout, flush=True)


if __name__ == "__main__":
    main()
