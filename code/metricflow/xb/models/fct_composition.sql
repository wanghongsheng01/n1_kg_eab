-- first-level BOM lines per ONE unit of a product (the self-built bom_quantity with max_depth = 1): a model's direct
-- components; for a SKU (bundle or not) the direct components of its company models x model units per SKU unit
with d as (
  select 'model' as root_kind, u.parent_code as root_code, u.child_code as item_code, cast(u.qty as double) as qty,
         u.valid_from, u.valid_to
  from {{ source('x', 'u_composition') }} u where u.parent_kind = 'model' and u.child_kind = 'component'
  union all
  select 'sku', e.root_code, u.child_code, e.qty * u.qty, greatest(e.valid_from, u.valid_from),
         case when e.valid_to is null then u.valid_to when u.valid_to is null then e.valid_to else least(e.valid_to, u.valid_to) end
  from {{ source('x', 'u_explosion') }} e join {{ source('x', 'u_composition') }} u
    on u.parent_kind = 'model' and u.child_kind = 'component' and u.parent_code = e.item_code
  where e.root_kind = 'sku' and e.item_kind = 'model' and greatest(e.valid_from, u.valid_from) <=
        coalesce(case when e.valid_to is null then u.valid_to when u.valid_to is null then e.valid_to else least(e.valid_to, u.valid_to) end, DATE '9999-12-31'))
select d.*, (d.valid_from <= DATE '2026-10-01' AND (d.valid_to IS NULL OR DATE '2026-10-01' <= d.valid_to)) as is_current from d
