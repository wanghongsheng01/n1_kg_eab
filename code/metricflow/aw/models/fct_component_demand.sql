-- units required by order lines: the sold product itself plus every item of its explosion valid on the order date
with ln as (
  select p.product_number, o.order_date, o.territory_id, o.online_order, sum(l.order_qty) as q
  from {{ source('x', 'sales_order_line') }} l join {{ source('x', 'sales_order') }} o on o.order_id = l.order_id
  join {{ source('x', 'product') }} p on p.product_id = l.product_id group by all)
select product_number as component_number, order_date, territory_id, online_order, sum(q) as units from (
  select product_number, order_date, territory_id, online_order, cast(q as double) as q from ln
  union all
  select e.item_code, ln.order_date, ln.territory_id, ln.online_order, ln.q * e.qty from ln
  join {{ source('x', 'u_explosion') }} e on e.root_code = ln.product_number
   and e.valid_from <= ln.order_date and (e.valid_to is null or ln.order_date <= e.valid_to))
group by all
