select model_code, name, product_line from {{ source('x', 'company_model') }}
