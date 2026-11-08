-- order lines converted to company-model units: SKU -> (bundle items ->) models via u_explosion at the order date
select o.order_date, o.status, o.store_id, e.item_code as model_code, sum(ol.qty * e.qty) as units
from {{ source('x', 'order_line') }} ol join {{ source('x', 'orders') }} o on o.order_id = ol.order_id join {{ source('x', 'listing') }} li on li.listing_id = ol.listing_id join {{ source('x', 'sales_sku') }} s on s.sku_id = li.sku_id
join {{ source('x', 'u_explosion') }} e on e.root_code = s.sku_code and e.item_kind = 'model'
 and e.valid_from <= o.order_date and (e.valid_to is null or o.order_date <= e.valid_to)
group by all
