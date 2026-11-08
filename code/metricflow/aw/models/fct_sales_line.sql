select l.order_id * 1000 + l.line_id as sales_line_id, o.order_date, o.online_order, o.territory_id,
       p.product_number, l.order_qty, l.line_total
from {{ source('x', 'sales_order_line') }} l join {{ source('x', 'sales_order') }} o on o.order_id = l.order_id
join {{ source('x', 'product') }} p on p.product_id = l.product_id
