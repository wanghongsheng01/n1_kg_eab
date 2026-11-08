select part_num as code, p.name, pc.name as category from {{ source('x', 'parts') }} p
left join {{ source('x', 'part_categories') }} pc on pc.id = p.part_cat_id
union all select fig_num, name, null from {{ source('x', 'minifigs') }}
union all select set_num, name, null from {{ source('x', 'sets') }}
