select id as color_id, name from {{ source('x', 'colors') }}
