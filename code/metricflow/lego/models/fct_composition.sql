select u.parent_kind as root_kind, u.parent_num as root_num, u.version as root_version,
       u.version = max(u.version) over (partition by u.parent_kind, u.parent_num) as is_current,
       u.child_kind as item_kind, u.child_num as item_num, u.color_id, u.is_spare, u.qty
from {{ source('x', 'u_composition') }} u
