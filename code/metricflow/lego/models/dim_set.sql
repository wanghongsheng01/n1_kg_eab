-- sets with their theme hierarchy flattened into levels (theme_level_1 = top-level theme; depth <= 3)
with recursive tp(theme_id, anc_id, d) as (
  select id, id, 0 from {{ source('x', 'themes') }}
  union all select tp.theme_id, t.parent_id, tp.d + 1 from tp join {{ source('x', 'themes') }} t on t.id = tp.anc_id
  where t.parent_id is not null),
path as (
  select tp.theme_id, list(t.name order by tp.d desc) as names
  from tp join {{ source('x', 'themes') }} t on t.id = tp.anc_id group by tp.theme_id)
select s.set_num, s.name, s.year, s.num_parts, make_date(cast(s.year as integer), 1, 1) as release_date,
       th.name as theme_name, p.names[1] as theme_root,
       p.names[1] as theme_level_1, p.names[2] as theme_level_2, p.names[3] as theme_level_3
from {{ source('x', 'sets') }} s left join {{ source('x', 'themes') }} th on th.id = s.theme_id
left join path p on p.theme_id = s.theme_id
