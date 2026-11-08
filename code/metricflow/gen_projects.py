"""Generate the three dbt / MetricFlow projects (code/metricflow/<ds>/) from one Python description.

Run with the MetricFlow venv (needs PyYAML):  .tools/mf_venv/bin/python code/metricflow/gen_projects.py

Each project is a standard dbt project: dbt models (SQL) materialised as tables in schema `mf` of data/mf_<ds>.duckdb
(a copy of data/<ds>_x.duckdb, i.e. SQL-U plus the materialised total-requirements relation u_explosion), plus
semantic_models / metrics YAML in the standard MetricFlow spec. MetricFlow has no recursion and no range joins, so
(1) BOM explosion / where-used metrics are defined over u_explosion (materialised closure) and (2) order-driven demand
metrics over a materialised fact that joins order lines with u_explosion on the validity interval at the order date.
MetricFlow has no query parameters either: every parameter of the self-built layer (product code, as-of date, version,
level, product type) becomes a where filter on a dimension / entity; defaults that are business definitions
(completed orders only, parts only for cost, spares excluded) are metric filters.
"""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "data")


def D(name, expr=None, typ="categorical", grain=None, desc=None):
    d = {"name": name, "type": typ}
    if desc:
        d["description"] = desc
    if expr:
        d["expr"] = expr
    if typ == "time":
        d["type_params"] = {"time_granularity": grain or "day"}
    return d


def E(name, typ, expr=None):
    e = {"name": name, "type": typ}
    if expr:
        e["expr"] = expr
    return e


def M(name, agg, expr=None, desc=None):
    m = {"name": name, "agg": agg}
    if desc:
        m["description"] = desc
    if expr:
        m["expr"] = expr
    return m


def SM(name, model, desc, entities, dims, measures=(), agg_time=None, primary_entity=None):
    s = {"name": name, "description": desc, "model": f"ref('{model}')"}
    if agg_time:
        s["defaults"] = {"agg_time_dimension": agg_time}
    if primary_entity:
        s["primary_entity"] = primary_entity
    s["entities"] = list(entities)
    s["dimensions"] = list(dims)
    if measures:
        s["measures"] = list(measures)
    return s


def MET(name, measure, desc, flt=None, label=None):
    m = {"name": name, "description": desc, "type": "simple", "label": label or name,
         "type_params": {"measure": measure}}
    if flt:
        m["filter"] = flt
    return m


DIM = lambda n: "{{ Dimension('" + n + "') }}"
SPINE = ("select cast(range as date) as date_day from range(date '1900-01-01', date '2031-01-01', interval 1 day)")


# ======================================================================================================================
# XB
# ======================================================================================================================
XB_TODAY = "2026-10-01"
XB_CUR = lambda a: f"({a}.valid_from <= DATE '{XB_TODAY}' AND ({a}.valid_to IS NULL OR DATE '{XB_TODAY}' <= {a}.valid_to))"
XB_LINES = ("from {{ source('x', 'order_line') }} ol join {{ source('x', 'orders') }} o on o.order_id = ol.order_id "
            "join {{ source('x', 'listing') }} li on li.listing_id = ol.listing_id "
            "join {{ source('x', 'sales_sku') }} s on s.sku_id = li.sku_id")
XB_MODELS = {
    "dim_node": """-- every composition node (company model, sales SKU, component), keyed by its code (codes are disjoint across kinds;
-- the 7 duplicated sku_code values of the generator collapse to one node, as in u_composition / u_explosion)
select model_code as code, 'model' as kind, name, cast(null as varchar) as category, cast(null as boolean) as is_assembly,
       cast(null as double) as unit_cost, cast(null as boolean) as is_bundle, cast(null as varchar) as market, product_line
from {{ source('x', 'company_model') }}
union all
select sku_code, 'sku', min(title), null, null, null, bool_or(is_bundle), min(market), null
from {{ source('x', 'sales_sku') }} group by sku_code
union all
select component_code, 'component', name, category, is_assembly, unit_cost, null, null, null
from {{ source('x', 'component') }}""",
    "dim_sku": "select sku_code, min(title) as title, bool_or(is_bundle) as is_bundle, min(market) as market "
               "from {{ source('x', 'sales_sku') }} group by sku_code",
    "dim_model": "select model_code, name, product_line from {{ source('x', 'company_model') }}",
    "dim_store": "select store_id, store_name, platform, country from {{ source('x', 'store') }}",
    "fct_explosion": f"""-- total requirements (all levels) of COMPONENTS per ONE unit of a company model or sales SKU, with validity
-- intervals (materialised closure). Restricted to the self-built layer's catalog: products = models / SKUs, items = components.
select e.root_kind, e.root_code, e.item_code, e.qty, e.valid_from, e.valid_to,
       {XB_CUR('e')} as is_current, e.qty * c.unit_cost as cost
from {{{{ source('x', 'u_explosion') }}}} e join {{{{ source('x', 'component') }}}} c on c.component_code = e.item_code
where e.root_kind in ('model', 'sku') and e.item_kind = 'component'""",
    "fct_composition": f"""-- first-level BOM lines per ONE unit of a product (the self-built bom_quantity with max_depth = 1): a model's direct
-- components; for a SKU (bundle or not) the direct components of its company models x model units per SKU unit
with d as (
  select 'model' as root_kind, u.parent_code as root_code, u.child_code as item_code, cast(u.qty as double) as qty,
         u.valid_from, u.valid_to
  from {{{{ source('x', 'u_composition') }}}} u where u.parent_kind = 'model' and u.child_kind = 'component'
  union all
  select 'sku', e.root_code, u.child_code, e.qty * u.qty, greatest(e.valid_from, u.valid_from),
         case when e.valid_to is null then u.valid_to when u.valid_to is null then e.valid_to else least(e.valid_to, u.valid_to) end
  from {{{{ source('x', 'u_explosion') }}}} e join {{{{ source('x', 'u_composition') }}}} u
    on u.parent_kind = 'model' and u.child_kind = 'component' and u.parent_code = e.item_code
  where e.root_kind = 'sku' and e.item_kind = 'model' and greatest(e.valid_from, u.valid_from) <=
        coalesce(case when e.valid_to is null then u.valid_to when u.valid_to is null then e.valid_to else least(e.valid_to, u.valid_to) end, DATE '9999-12-31'))
select d.*, {XB_CUR('d')} as is_current from d""",
    "fct_sku_model": "select parent_code as sku_code, child_code as model_code, qty from {{ source('x', 'u_composition') }} "
                     "where parent_kind = 'sku' and child_kind = 'model'",
    "fct_listing": "select li.listing_id, s.sku_code, li.store_id from {{ source('x', 'listing') }} li "
                   "join {{ source('x', 'sales_sku') }} s on s.sku_id = li.sku_id",
    "fct_order_line": f"""select ol.order_id * 1000 + ol.line_no as order_line_id, o.order_date, o.status, o.store_id, s.sku_code,
       ol.qty, ol.qty * ol.unit_price as revenue
{XB_LINES}""",
    "fct_model_units": f"""-- order lines converted to company-model units: SKU -> (bundle items ->) models via u_explosion at the order date
select o.order_date, o.status, o.store_id, e.item_code as model_code, sum(ol.qty * e.qty) as units
{XB_LINES}
join {{{{ source('x', 'u_explosion') }}}} e on e.root_code = s.sku_code and e.item_kind = 'model'
 and e.valid_from <= o.order_date and (e.valid_to is null or o.order_date <= e.valid_to)
group by all""",
    "fct_part_consumption": f"""-- order lines exploded to components with the BOM valid on the ORDER DATE (materialised demand fact)
select o.order_date, o.status, o.store_id, e.item_code as component_code, sum(ol.qty * e.qty) as units
{XB_LINES}
join {{{{ source('x', 'u_explosion') }}}} e on e.root_code = s.sku_code and e.item_kind = 'component'
 and e.valid_from <= o.order_date and (e.valid_to is null or o.order_date <= e.valid_to)
group by all""",
}
XB_SRC = ["company_model", "component", "listing", "order_line", "orders", "sales_sku", "store", "u_composition", "u_explosion"]

XB_NODE_DIMS = lambda role: {
    "product": [D("name"), D("is_bundle", desc="true for a bundle SKU (sales SKUs only)"),
                D("product_line", desc="company models only")],
    "component": [D("name"), D("category"), D("is_assembly", desc="true for an assembly, false for a part (components only)"),
                  D("unit_cost")],
}[role]
XB_BOMDIMS = [D("root_kind", desc="kind of the product: 'model' | 'sku' (sales SKU, bundle or not)"),
              D("valid_from", typ="time", desc="first day of the validity interval"),
              D("valid_to", typ="time", desc="last day of the validity interval (NULL = open-ended)"),
              D("is_current", desc="true if the row is valid on 2026-10-01 (the current BOM)")]
XB_SM = [
    SM("product", "dim_node", "Products (company models and sales SKUs) as the containing side of a BOM.",
       [E("product", "primary", "code")], XB_NODE_DIMS("product")),
    SM("component", "dim_node", "Components (assemblies and parts) as the contained side of a BOM.",
       [E("component", "primary", "code")], XB_NODE_DIMS("component") + [D("catalog_date", "DATE '2026-10-01'", "time")],
       [M("component_count", "sum", "case when kind = 'component' then 1 else 0 end")], agg_time="catalog_date"),
    SM("sku", "dim_sku", "Sales SKUs.", [E("sku", "primary", "sku_code")],
       [D("title"), D("is_bundle"), D("market")]),
    SM("model", "dim_model", "Company models.", [E("model", "primary", "model_code")], [D("name"), D("product_line")]),
    SM("store", "dim_store", "Stores.", [E("store", "primary", "store_id")],
       [D("name", "store_name"), D("platform"), D("country")]),
    SM("explosion", "fct_explosion",
       "Total requirements: quantity of each item per ONE unit of a product, multiplied along every path through all "
       "levels and summed over paths, stored as validity intervals of constant quantity.",
       [E("product", "foreign", "root_code"), E("component", "foreign", "item_code")], XB_BOMDIMS,
       [M("explosion_qty", "sum", "qty"), M("explosion_cost", "sum", "cost"),
        M("explosion_items", "count_distinct", "item_code"), M("explosion_roots", "count_distinct", "root_code")],
       agg_time="valid_from", primary_entity="explosion"),
    SM("composition", "fct_composition", "Direct (first-level) BOM lines.",
       [E("product", "foreign", "root_code"), E("component", "foreign", "item_code")], XB_BOMDIMS,
       [M("composition_qty", "sum", "qty")], agg_time="valid_from", primary_entity="composition"),
    SM("sku_model", "fct_sku_model", "SKU to company model mapping (non-bundle SKUs).",
       [E("sku", "foreign", "sku_code"), E("model", "foreign", "model_code"),
        ] , [D("map_date", "DATE '1900-01-01'", "time")], [M("sku_model_qty", "sum", "qty")],
       agg_time="map_date", primary_entity="sku_model"),
    SM("listing", "fct_listing", "Store listings of sales SKUs.",
       [E("listing", "primary", "listing_id"), E("sku", "foreign", "sku_code"), E("store", "foreign", "store_id")],
       [D("listing_date", "DATE '1900-01-01'", "time")], [M("listings", "count", "listing_id")], agg_time="listing_date"),
    SM("order_line", "fct_order_line", "Order lines (one row per order line, SKU level).",
       [E("order_line", "primary", "order_line_id"), E("sku", "foreign", "sku_code"), E("store", "foreign", "store_id")],
       [D("order_date", typ="time"), D("status", desc="'completed' | 'cancelled'")],
       [M("sku_units", "sum", "qty"), M("line_revenue", "sum", "revenue")], agg_time="order_date"),
    SM("model_sale", "fct_model_units", "Order lines converted to company-model units (bundles expanded).",
       [E("model", "foreign", "model_code"), E("store", "foreign", "store_id")],
       [D("order_date", typ="time"), D("status")], [M("model_units", "sum", "units")],
       agg_time="order_date", primary_entity="model_sale"),
    SM("consumption", "fct_part_consumption",
       "Order lines exploded to components with the BOM valid on each order date.",
       [E("component", "foreign", "component_code"), E("store", "foreign", "store_id")],
       [D("order_date", typ="time"), D("status")], [M("consumed_units", "sum", "units")],
       agg_time="order_date", primary_entity="consumption"),
]
COMPLETED = lambda sm: DIM(f"{sm}__status") + " = 'completed'"
XB_METRICS = [
    MET("bom_quantity", "explosion_qty",
        "Total quantity of each component needed for ONE unit of a product (company model, non-bundle SKU or bundle SKU), "
        "exploded through all levels. Select the product with an Entity('product') filter and the BOM state with "
        "explosion__is_current or the validity interval."),
    MET("bom_direct_quantity", "composition_qty",
        "Quantity of each DIRECT (first-level) child of a product per one unit (the max_depth = 1 case of bom_quantity)."),
    MET("bom_component_count", "explosion_items",
        "Number of distinct components in the all-level explosion of a product (combine with the filters of bom_quantity)."),
    MET("bom_cost", "explosion_cost",
        "Sum over PARTS (non-assembly components) at any level of (quantity per unit x unit_cost) for ONE unit of a product.",
        DIM("component__is_assembly") + " = false"),
    MET("where_used", "explosion_qty",
        "Quantity of a component per one unit of each product that contains it at any depth (filter the component with an "
        "Entity('component') filter, group by product)."),
    MET("where_used_count", "explosion_roots",
        "Number of distinct products that contain a component at any depth (combine with the filters of where_used)."),
    MET("units_sold", "sku_units", "Units sold in completed orders at SKU level (bundle SKUs count as themselves).",
        COMPLETED("order_line")),
    MET("units_sold_model", "model_units",
        "Units sold in completed orders converted to company-model units: SKU units x model qty of the SKU-model mapping; "
        "bundles are expanded into their item SKUs first (the level = 'model' case of units_sold).", COMPLETED("model_sale")),
    MET("revenue", "line_revenue", "Sum of order-line qty x unit_price over completed orders.", COMPLETED("order_line")),
    MET("part_consumption", "consumed_units",
        "Units of PARTS consumed by completed orders: every order line exploded (bundle -> SKUs -> models -> components) "
        "with the BOM valid on the ORDER DATE.",
        COMPLETED("consumption") + " AND " + DIM("component__is_assembly") + " = false"),
    MET("listing_count", "listings", "Number of store listings."),
    MET("sku_model_map", "sku_model_qty", "The SKU to company model mapping (non-bundle SKUs); value = model units per SKU unit."),
    MET("component_attr", "component_count", "Number of components (one per component; group by component attributes)."),
]

# ======================================================================================================================
# AW
# ======================================================================================================================
AW_TODAY = "2025-06-30"
AW_CUR = lambda a: f"({a}.valid_from <= DATE '{AW_TODAY}' AND ({a}.valid_to IS NULL OR DATE '{AW_TODAY}' <= {a}.valid_to))"
AW_MODELS = {
    "dim_product": """select p.product_number, p.name, p.color, p.make_flag, p.is_finished_good, p.standard_cost, p.list_price,
       p.product_line, sc.name as subcategory, pc.name as category
from {{ source('x', 'product') }} p
left join {{ source('x', 'product_subcategory') }} sc on sc.subcategory_id = p.subcategory_id
left join {{ source('x', 'product_category') }} pc on pc.category_id = sc.category_id""",
    "dim_territory": "select territory_id, name, country_code, territory_group from {{ source('x', 'sales_territory') }}",
    "fct_explosion": f"""select e.root_code, e.item_code, e.qty, e.valid_from, e.valid_to, {AW_CUR('e')} as is_current,
       e.qty * p.standard_cost as cost
from {{{{ source('x', 'u_explosion') }}}} e left join {{{{ source('x', 'product') }}}} p on p.product_number = e.item_code""",
    "fct_composition": f"""select u.parent_code as root_code, u.child_code as item_code, u.qty, u.valid_from, u.valid_to,
       {AW_CUR('u')} as is_current
from {{{{ source('x', 'u_composition') }}}} u""",
    "fct_sales_line": """select l.order_id * 1000 + l.line_id as sales_line_id, o.order_date, o.online_order, o.territory_id,
       p.product_number, l.order_qty, l.line_total
from {{ source('x', 'sales_order_line') }} l join {{ source('x', 'sales_order') }} o on o.order_id = l.order_id
join {{ source('x', 'product') }} p on p.product_id = l.product_id""",
    "fct_component_demand": """-- units required by order lines: the sold product itself plus every item of its explosion valid on the order date
with ln as (
  select p.product_number, o.order_date, o.territory_id, o.online_order, sum(l.order_qty) as q
  from {{ source('x', 'sales_order_line') }} l join {{ source('x', 'sales_order') }} o on o.order_id = l.order_id
  join {{ source('x', 'product') }} p on p.product_id = l.product_id group by all)
select product_number as component_number, order_date, territory_id, online_order, sum(q) as units from (
  select product_number, order_date, territory_id, online_order, cast(q as double) as q from ln
  union all
  select e.item_code, ln.order_date, ln.territory_id, ln.online_order, ln.q * e.qty from ln
  join {{ source('x', 'u_explosion') }} e on e.root_code = ln.product_number
   and e.valid_from <= ln.order_date and (e.valid_to is null or ln.order_date <= e.valid_to))
group by all""",
}
AW_SRC = ["product", "product_subcategory", "product_category", "sales_territory", "sales_order", "sales_order_line",
          "u_composition", "u_explosion"]
AW_PDIMS = [D("name"), D("color"), D("subcategory"), D("category"), D("product_line"),
            D("make_flag", desc="true = manufactured, false = purchased part"), D("is_finished_good"),
            D("standard_cost"), D("list_price")]
AW_BOMDIMS = [D("valid_from", typ="time", desc="first day of the validity interval"),
              D("valid_to", typ="time", desc="last day of the validity interval (NULL = open-ended)"),
              D("is_current", desc="true if the row is valid on 2025-06-30 (the current BOM)")]
AW_SM = [
    SM("product", "dim_product", "Products (identified by product_number), as BOM product or as sold product.",
       [E("product", "primary", "product_number")], AW_PDIMS + [D("catalog_date", "DATE '2025-06-30'", "time")],
       [M("product_count", "count", "product_number")], agg_time="catalog_date"),
    SM("component", "dim_product", "Products as the contained side of a BOM (components / parts / sub-assemblies).",
       [E("component", "primary", "product_number")],
       [D("name"), D("make_flag", desc="true = manufactured sub-assembly, false = purchased part"), D("is_finished_good"),
        D("subcategory"), D("category"), D("standard_cost")]),
    SM("territory", "dim_territory", "Sales territories.", [E("territory", "primary", "territory_id")],
       [D("name"), D("country", "country_code"), D("group", "territory_group")]),
    SM("explosion", "fct_explosion",
       "Total requirements: quantity of each component per ONE unit of a product through all BOM levels, stored as "
       "validity intervals of constant quantity.",
       [E("product", "foreign", "root_code"), E("component", "foreign", "item_code")], AW_BOMDIMS,
       [M("explosion_qty", "sum", "qty"), M("explosion_cost", "sum", "cost"),
        M("explosion_items", "count_distinct", "item_code"), M("explosion_roots", "count_distinct", "root_code")],
       agg_time="valid_from", primary_entity="explosion"),
    SM("composition", "fct_composition", "Direct (first-level) BOM lines.",
       [E("product", "foreign", "root_code"), E("component", "foreign", "item_code")], AW_BOMDIMS,
       [M("composition_qty", "sum", "qty")], agg_time="valid_from", primary_entity="composition"),
    SM("sales_line", "fct_sales_line", "Sales order lines.",
       [E("sales_line", "primary", "sales_line_id"), E("product", "foreign", "product_number"),
        E("territory", "foreign", "territory_id")],
       [D("order_date", typ="time"), D("online", "online_order", desc="true = online (individual) order, false = reseller")],
       [M("sold_units", "sum", "order_qty"), M("line_total", "sum", "line_total")], agg_time="order_date"),
    SM("demand", "fct_component_demand",
       "Units of each product required by order lines (sold directly plus contained at any level, BOM valid on the order date).",
       [E("component", "foreign", "component_number"), E("territory", "foreign", "territory_id")],
       [D("order_date", typ="time"), D("online", "online_order")], [M("demand_units", "sum", "units")],
       agg_time="order_date", primary_entity="demand"),
]
AW_METRICS = [
    MET("bom_quantity", "explosion_qty",
        "Total quantity of each component needed for ONE unit of a product, exploded through all BOM levels. Select the "
        "product with an Entity('product') filter and the BOM state with explosion__is_current or the validity interval."),
    MET("bom_direct_quantity", "composition_qty",
        "Quantity of each DIRECT (first-level) BOM line of a product per unit (the max_depth = 1 case of bom_quantity)."),
    MET("bom_component_count", "explosion_items",
        "Number of distinct components in the all-level explosion of a product (combine with the filters of bom_quantity)."),
    MET("purchased_cost", "explosion_cost",
        "Sum over PARTS (make_flag = false) at any level of (quantity per unit x standard_cost) for ONE unit of a product.",
        DIM("component__make_flag") + " = false"),
    MET("where_used", "explosion_qty",
        "Quantity of a component per one unit of each product that contains it at any BOM level (every assembly product is "
        "considered, including intermediate sub-assemblies; filter product__category = 'Bikes' for bikes only)."),
    MET("where_used_count", "explosion_roots",
        "Number of distinct products that contain a component at any level (combine with the filters of where_used)."),
    MET("units_sold", "sold_units", "Sum of order-line quantities."),
    MET("revenue", "line_total", "Sum of order-line line_total (after discount; excludes tax and freight)."),
    MET("component_demand", "demand_units",
        "Units of each product required by order lines: units sold directly plus units contained at any level of the sold "
        "products, using the BOM valid on each order date."),
    MET("product_attr", "product_count", "Number of products (one row per product; group by product attributes)."),
]

# ======================================================================================================================
# LEGO
# ======================================================================================================================
LEGO_MODELS = {
    "dim_set": """-- sets with their theme hierarchy flattened into levels (theme_level_1 = top-level theme; depth <= 3)
with recursive tp(theme_id, anc_id, d) as (
  select id, id, 0 from {{ source('x', 'themes') }}
  union all select tp.theme_id, t.parent_id, tp.d + 1 from tp join {{ source('x', 'themes') }} t on t.id = tp.anc_id
  where t.parent_id is not null),
path as (
  select tp.theme_id, list(t.name order by tp.d desc) as names
  from tp join {{ source('x', 'themes') }} t on t.id = tp.anc_id group by tp.theme_id)
select s.set_num, s.name, s.year, s.num_parts, make_date(cast(s.year as integer), 1, 1) as release_date,
       th.name as theme_name, p.names[1] as theme_root,
       p.names[1] as theme_level_1, p.names[2] as theme_level_2, p.names[3] as theme_level_3
from {{ source('x', 'sets') }} s left join {{ source('x', 'themes') }} th on th.id = s.theme_id
left join path p on p.theme_id = s.theme_id""",
    "dim_item": """select part_num as code, p.name, pc.name as category from {{ source('x', 'parts') }} p
left join {{ source('x', 'part_categories') }} pc on pc.id = p.part_cat_id
union all select fig_num, name, null from {{ source('x', 'minifigs') }}
union all select set_num, name, null from {{ source('x', 'sets') }}""",
    "dim_color": "select id as color_id, name from {{ source('x', 'colors') }}",
    "fct_explosion": """select root_kind, root_num, root_version, is_current, item_kind, item_num, color_id, is_spare, qty,
       item_num || '|' || coalesce(cast(color_id as varchar), '') as part_color
from {{ source('x', 'u_explosion') }}""",
    "fct_composition": """select u.parent_kind as root_kind, u.parent_num as root_num, u.version as root_version,
       u.version = max(u.version) over (partition by u.parent_kind, u.parent_num) as is_current,
       u.child_kind as item_kind, u.child_num as item_num, u.color_id, u.is_spare, u.qty
from {{ source('x', 'u_composition') }} u""",
    "fct_inventory": """select parent_num as set_num, version, version = max(version) over (partition by parent_num) as is_current
from (select distinct parent_num, version from {{ source('x', 'u_composition') }} where parent_kind = 'set')""",
}
LEGO_SRC = ["themes", "sets", "parts", "part_categories", "minifigs", "colors", "u_composition", "u_explosion"]
LEGO_BOMDIMS = [D("root_kind", desc="'set' | 'minifig' (a minifig's own inventory)"),
                D("root_version", desc="inventory version of the root set (1, 2, ...)"),
                D("is_current", desc="true for the root's current (highest-version) inventory"),
                D("item_kind", desc="'part' | 'minifig' | 'set'"),
                D("is_spare", desc="true if the part line is a spare part"),
                D("snapshot_date", "DATE '2026-01-01'", "time")]
LEGO_SM = [
    SM("set", "dim_set",
       "LEGO sets. A set belongs to theme T or one of its sub-themes iff T is one of theme_level_1..theme_level_3.",
       [E("set", "primary", "set_num")],
       [D("name"), D("release_year", "year", desc="release year (integer)"), D("num_parts", desc="catalogue piece count (not an explosion)"),
        D("theme_name", desc="the set's own theme"), D("theme_root", desc="top-level ancestor theme"),
        D("theme_level_1", desc="theme path, level 1 (= theme_root)"), D("theme_level_2", desc="theme path, level 2"),
        D("theme_level_3", desc="theme path, level 3"), D("release_date", typ="time", grain="year")],
       [M("set_count", "count", "set_num")], agg_time="release_date"),
    SM("item", "dim_item", "Items: parts (part_num), minifigs (fig_num) and sets (set_num).",
       [E("item", "primary", "code")], [D("name"), D("category", desc="part category (parts only)")]),
    SM("color", "dim_color", "Colours.", [E("color", "primary", "color_id")], [D("name")]),
    SM("explosion", "fct_explosion",
       "Exploded contents: quantity of each item per ONE unit of a root set / minifig, through nested sets and minifigs "
       "(quantities multiply along the path and sum over paths). One row group per inventory version of the ROOT; "
       "nested sets and minifigs always contribute their own current inventory.",
       [E("set", "foreign", "root_num"), E("item", "foreign", "item_num"), E("color", "foreign", "color_id")],
       LEGO_BOMDIMS,
       [M("explosion_qty", "sum", "qty"), M("explosion_items", "count_distinct", "item_num"),
        M("explosion_part_colors", "count_distinct", "part_color"), M("explosion_colors", "count_distinct", "color_id"),
        M("explosion_roots", "count_distinct", "root_num")],
       agg_time="snapshot_date", primary_entity="explosion"),
    SM("composition", "fct_composition", "First-level inventory lines (no explosion; spares listed as is).",
       [E("set", "foreign", "root_num"), E("item", "foreign", "item_num"), E("color", "foreign", "color_id")],
       LEGO_BOMDIMS, [M("composition_qty", "sum", "qty"), M("composition_items", "count_distinct", "item_num")],
       agg_time="snapshot_date", primary_entity="composition"),
    SM("inventory", "fct_inventory", "Inventory versions of sets.", [E("set", "foreign", "set_num")],
       [D("version"), D("is_current"), D("inventory_date", "DATE '2026-01-01'", "time")],
       [M("inventory_count", "count", "version")], agg_time="inventory_date", primary_entity="inventory"),
]
EX = lambda n: DIM("explosion__" + n)
NOSPARE = EX("is_spare") + " = false"
CATALOG = EX("root_kind") + " = 'set' AND " + EX("is_current") + " = true"
LEGO_METRICS = [
    MET("part_quantity", "explosion_qty",
        "Exploded part quantities for ONE unit of one set (or minifig) at any depth; spare parts excluded. Select the set "
        "with an Entity('set') filter and the inventory with explosion__is_current = true or explosion__root_version = n.",
        EX("item_kind") + " = 'part' AND " + NOSPARE),
    MET("part_quantity_incl_spares", "explosion_qty", "As part_quantity but spare parts included.",
        EX("item_kind") + " = 'part'"),
    MET("distinct_part_count", "explosion_items",
        "Number of distinct part numbers (colours ignored) in the explosion of a set; spares excluded.",
        EX("item_kind") + " = 'part' AND " + NOSPARE),
    MET("distinct_part_color_count", "explosion_part_colors",
        "Number of distinct part x colour combinations in the explosion of a set; spares excluded.",
        EX("item_kind") + " = 'part' AND " + NOSPARE),
    MET("minifig_quantity", "explosion_qty",
        "Exploded minifig quantities for ONE unit of one set (minifigs inside nested sets included).",
        EX("item_kind") + " = 'minifig'"),
    MET("set_contents", "composition_qty",
        "FIRST-LEVEL rows of one inventory of one set (no explosion; spares included as listed); value = listed quantity."),
    MET("set_contents_item_count", "composition_items",
        "Number of distinct items (item codes) listed at the first level of one inventory of one set."),
    MET("where_used", "explosion_qty",
        "Sets that contain an item at any depth in their CURRENT exploded contents (spare parts excluded); value = quantity "
        "of the item per one unit of the set. Filter the item with an Entity('item') filter (and color__name), group by set.",
        CATALOG + " AND " + NOSPARE),
    MET("where_used_count", "explosion_roots",
        "Number of distinct sets that contain an item at any depth in their current exploded contents (spares excluded).",
        CATALOG + " AND " + NOSPARE),
    MET("catalog_parts", "explosion_qty",
        "Exploded part quantities summed over sets, each set's current inventory exploded on its own (spares excluded).",
        CATALOG + " AND " + EX("item_kind") + " = 'part' AND " + NOSPARE),
    MET("catalog_color_count", "explosion_colors",
        "Number of distinct colours among the exploded parts of the selected sets (current inventories, spares excluded).",
        CATALOG + " AND " + EX("item_kind") + " = 'part' AND " + NOSPARE),
    MET("catalog_minifigs", "explosion_qty",
        "Exploded minifig quantities summed over sets, each set exploded on its own (current inventories).",
        CATALOG + " AND " + EX("item_kind") + " = 'minifig'"),
    MET("set_attr", "set_count", "Number of sets (one row per set; group by set attributes)."),
    MET("inventory_versions", "inventory_count", "Number of inventory versions of a set."),
]

PROJECTS = {
    "xb": (XB_MODELS, XB_SRC, XB_SM, XB_METRICS),
    "aw": (AW_MODELS, AW_SRC, AW_SM, AW_METRICS),
    "lego": (LEGO_MODELS, LEGO_SRC, LEGO_SM, LEGO_METRICS),
}


def dump(path, obj):
    with open(path, "w") as f:
        yaml.safe_dump(obj, f, sort_keys=False, width=120, allow_unicode=True)


def write(ds):
    models, src, sms, mets = PROJECTS[ds]
    pdir = os.path.join(HERE, ds)
    mdir = os.path.join(pdir, "models")
    os.makedirs(mdir, exist_ok=True)
    for f in os.listdir(mdir):
        os.remove(os.path.join(mdir, f))
    dump(os.path.join(pdir, "dbt_project.yml"), {
        "name": f"mf_{ds}", "version": "1.0.0", "config-version": 2, "profile": f"mf_{ds}",
        "model-paths": ["models"], "target-path": "target", "clean-targets": ["target"],
        "models": {f"mf_{ds}": {"+materialized": "table"}}})
    db = os.path.join(DATA, f"mf_{ds}.duckdb")
    dump(os.path.join(pdir, "profiles.yml"), {f"mf_{ds}": {
        "target": "{{ env_var('MF_TARGET', 'build') }}",
        "outputs": {"build": {"type": "duckdb", "path": db, "schema": "mf", "threads": 1},
                    # compile-only target: never opens the database file (the semantic manifest fixes relation names)
                    "compile": {"type": "duckdb", "path": ":memory:", "schema": "mf", "threads": 1}}}})
    dump(os.path.join(mdir, "sources.yml"), {"version": 2, "sources": [
        {"name": "x", "schema": "main", "tables": [{"name": t} for t in src]}]})
    for name, sql in models.items():
        with open(os.path.join(mdir, name + ".sql"), "w") as f:
            f.write(sql.strip() + "\n")
    with open(os.path.join(mdir, "metricflow_time_spine.sql"), "w") as f:
        f.write(SPINE + "\n")
    dump(os.path.join(mdir, "time_spine.yml"), {"version": 2, "models": [{
        "name": "metricflow_time_spine", "description": "Time spine with one row per day.",
        "time_spine": {"standard_granularity_column": "date_day"},
        "columns": [{"name": "date_day", "granularity": "day"}]}]})
    dump(os.path.join(mdir, "semantic_models.yml"), {"semantic_models": sms})
    dump(os.path.join(mdir, "metrics.yml"), {"metrics": mets})
    print("wrote", pdir)


if __name__ == "__main__":
    for ds in PROJECTS:
        write(ds)
