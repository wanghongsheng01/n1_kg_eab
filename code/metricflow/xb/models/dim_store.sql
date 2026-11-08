select store_id, store_name, platform, country from {{ source('x', 'store') }}
