select u.parent_code as root_code, u.child_code as item_code, u.qty, u.valid_from, u.valid_to,
       (u.valid_from <= DATE '2025-06-30' AND (u.valid_to IS NULL OR DATE '2025-06-30' <= u.valid_to)) as is_current
from {{ source('x', 'u_composition') }} u
