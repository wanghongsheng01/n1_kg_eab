select root_kind, root_num, root_version, is_current, item_kind, item_num, color_id, is_spare, qty,
       item_num || '|' || coalesce(cast(color_id as varchar), '') as part_color
from {{ source('x', 'u_explosion') }}
