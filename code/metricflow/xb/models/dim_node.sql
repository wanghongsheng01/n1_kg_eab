-- every composition node (company model, sales SKU, component), keyed by its code (codes are disjoint across kinds;
-- the 7 duplicated sku_code values of the generator collapse to one node, as in u_composition / u_explosion)
select model_code as code, 'model' as kind, name, cast(null as varchar) as category, cast(null as boolean) as is_assembly,
       cast(null as double) as unit_cost, cast(null as boolean) as is_bundle, cast(null as varchar) as market, product_line
from {{ source('x', 'company_model') }}
union all
select sku_code, 'sku', min(title), null, null, null, bool_or(is_bundle), min(market), null
from {{ source('x', 'sales_sku') }} group by sku_code
union all
select component_code, 'component', name, category, is_assembly, unit_cost, null, null, null
from {{ source('x', 'component') }}
