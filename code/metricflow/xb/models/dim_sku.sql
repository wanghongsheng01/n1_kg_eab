select sku_code, min(title) as title, bool_or(is_bundle) as is_bundle, min(market) as market from {{ source('x', 'sales_sku') }} group by sku_code
