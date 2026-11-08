select li.listing_id, s.sku_code, li.store_id from {{ source('x', 'listing') }} li join {{ source('x', 'sales_sku') }} s on s.sku_id = li.sku_id
