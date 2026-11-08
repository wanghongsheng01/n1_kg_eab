select parent_code as sku_code, child_code as model_code, qty from {{ source('x', 'u_composition') }} where parent_kind = 'sku' and child_kind = 'model'
