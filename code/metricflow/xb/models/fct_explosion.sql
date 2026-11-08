-- total requirements (all levels) of COMPONENTS per ONE unit of a company model or sales SKU, with validity
-- intervals (materialised closure). Restricted to the self-built layer's catalog: products = models / SKUs, items = components.
select e.root_kind, e.root_code, e.item_code, e.qty, e.valid_from, e.valid_to,
       (e.valid_from <= DATE '2026-10-01' AND (e.valid_to IS NULL OR DATE '2026-10-01' <= e.valid_to)) as is_current, e.qty * c.unit_cost as cost
from {{ source('x', 'u_explosion') }} e join {{ source('x', 'component') }} c on c.component_code = e.item_code
where e.root_kind in ('model', 'sku') and e.item_kind = 'component'
