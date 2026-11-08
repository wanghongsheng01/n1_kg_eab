select territory_id, name, country_code, territory_group from {{ source('x', 'sales_territory') }}
