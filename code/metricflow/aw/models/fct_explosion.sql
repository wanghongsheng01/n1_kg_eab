select e.root_code, e.item_code, e.qty, e.valid_from, e.valid_to, (e.valid_from <= DATE '2025-06-30' AND (e.valid_to IS NULL OR DATE '2025-06-30' <= e.valid_to)) as is_current,
       e.qty * p.standard_cost as cost
from {{ source('x', 'u_explosion') }} e left join {{ source('x', 'product') }} p on p.product_number = e.item_code
