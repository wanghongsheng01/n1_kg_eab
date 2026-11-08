select parent_num as set_num, version, version = max(version) over (partition by parent_num) as is_current
from (select distinct parent_num, version from {{ source('x', 'u_composition') }} where parent_kind = 'set')
