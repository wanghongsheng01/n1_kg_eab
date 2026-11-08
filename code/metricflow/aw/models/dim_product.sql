select p.product_number, p.name, p.color, p.make_flag, p.is_finished_good, p.standard_cost, p.list_price,
       p.product_line, sc.name as subcategory, pc.name as category
from {{ source('x', 'product') }} p
left join {{ source('x', 'product_subcategory') }} sc on sc.subcategory_id = p.subcategory_id
left join {{ source('x', 'product_category') }} pc on pc.category_id = sc.category_id
