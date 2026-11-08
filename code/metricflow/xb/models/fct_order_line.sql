select ol.order_id * 1000 + ol.line_no as order_line_id, o.order_date, o.status, o.store_id, s.sku_code,
       ol.qty, ol.qty * ol.unit_price as revenue
from {{ source('x', 'order_line') }} ol join {{ source('x', 'orders') }} o on o.order_id = ol.order_id join {{ source('x', 'listing') }} li on li.listing_id = ol.listing_id join {{ source('x', 'sales_sku') }} s on s.sku_id = li.sku_id
