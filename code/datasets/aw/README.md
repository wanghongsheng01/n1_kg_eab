# AW: AdventureWorks dataset package for the N1 benchmark

Interpreter: `python` (duckdb 1.4.5, kuzu 0.11.3). Scripts can be run
from any directory; all paths come from `__init__.py`. No LLM/API calls are made anywhere in this package.

```
python build.py          # data/aw.duckdb, data/aw_u.duckdb, data/aw.kuzu, data/aw_u.kuzu (+ data/aw_kg_csv/)
python gen_questions.py  # data/aw_questions.jsonl (188 questions, fixed seed 20261105)
python check_gold.py     # data/aw_s3_coverage.json + all cross-checks below
```

## Source and license

- Snapshot: built by `code/make_sources.py` into `data/aw_src.duckdb`,
  sha256 `86a0cf7e194d100fefd0650deae64048d756819483c25f6e819dab1c330ceab5`. Dates in the snapshot are shifted
  (+11 years) relative to the Microsoft original; the shifted dates are used unchanged.
- `make_flag` is read from the raw `raw/aw/Product.csv` (column MakeFlag), which the snapshot lacks.
- `sales_territory` (10 rows: name, country code, group) is AdventureWorks' public SalesTerritory table, added because the
  snapshot only carries `territory_id`.
- AdventureWorks is distributed in `microsoft/sql-server-samples`, whose `license.txt` states the MIT License.

## Substrates

| file | substrate | composition representation |
|---|---|---|
| `aw.duckdb` | S1 SQL | AW's own split: `bill_of_materials(bom_id, assembly_id, component_id, start_date, end_date, unit, bom_level, per_assembly_qty)`, id-keyed, including AW's 103 header rows (`assembly_id` NULL, `bom_level` 0) |
| `aw_u.duckdb` | S1 SQL, unified | `u_composition(parent_code, child_code, qty, unit, valid_from, valid_to)` keyed by `product_number`; header rows and `bom_level` dropped; replaces `bill_of_materials` |
| `aw.kuzu` | S2 KG, typed | `(:Product)-[:HAS_SUBASSEMBLY]->(:Product)` (child manufactured, `make_flag`) and `(:Product)-[:HAS_PART]->(:Product)` (child purchased); edge props `qty, unit, valid_from, valid_to, bom_level` |
| `aw_u.kuzu` | S2 KG, unified | one `(:Product)-[:CONTAINS {qty, unit, valid_from, valid_to}]->(:Product)` |
| (`semantic.py`) | S3 metric layer | compiles JSON over `aw.duckdb` |

Shared tables / nodes: `product(product_id, product_number, name, color, make_flag, is_finished_good, standard_cost,
list_price, product_line, subcategory_id)`, `product_subcategory`, `product_category`, `sales_territory`, `store`,
`customer(customer_id, store_id, territory_id)`, `sales_order(order_id, order_date, online_order, customer_id,
territory_id, total_due)`, `sales_order_line(order_id, line_id, product_id, order_qty, unit_price, unit_discount,
line_total)`. In Kuzu: `Product, Subcategory, Category, Territory, Store, Customer, SalesOrder` with
`IN_SUBCATEGORY, IN_CATEGORY, BELONGS_TO, PLACED_BY, IN_TERRITORY, HAS_LINE{line_id, order_qty, unit_price,
unit_discount, line_total}`.

Cleaning: unit codes trimmed; `per_assembly_qty` cast DECIMAL(10,2) -> DOUBLE (all values are integral; avoids DECIMAL
scale growth inside recursive CTEs); `sales_order.status` dropped (constant 5 = shipped for all orders).

### Design choice: typed vs. unified graph

AW has a single BOM edge type, so a graph with one `HAS_COMPONENT` edge would make typed = unified. The typed graph
therefore splits composition edges as an MRP/ERP-to-graph migration typically does, by the make/buy flag of the child:
`HAS_SUBASSEMBLY` (child manufactured in-house, 1,702 edges) vs `HAS_PART` (child purchased, 874 edges). In this
snapshot `make_flag = true` iff the product has its own BOM lines (238 assemblies; no purchased product has children),
so the split is static and loses no information; a recursive query must traverse both types
(`-[:HAS_SUBASSEMBLY|HAS_PART*1..k]->`). Splitting by `bom_level` was rejected because AW's stored `bom_level` is a
denormalised depth that is inconsistent for shared sub-assemblies (79 assemblies have child lines with more than one
`bom_level`). `bom_level` is kept as a column/property in the typed substrates (as in AW) and dropped in the unified ones.

## Business rules (identical in every substrate document, `prompts.RULES`)

Composition multiplies quantities along a path and sums over paths; a "part" is a purchased product (`make_flag =
false`); validity is inclusive (`start <= d <= end`, NULL end = open); **"current" = 2025-06-30** (the last order date
is 2025-06-29; the BOM does not change after 2021-12-22, so any date >= 2021-12-22 gives the same BOM); revenue =
sum(`line_total`) (after discount; `total_due` includes tax and freight); component demand = units sold directly +
units contained at any level of the sold products, exploded with the BOM valid on the order date. Bikes = category
'Bikes'.

## Semantic layer (`semantic.py`)

Same JSON grammar as xb (`metric, params, group_by, filters, order_by, limit, aggregate`). Metrics: `bom_quantity`
(product_number, as_of, max_depth), `purchased_cost`, `where_used` (component_number, as_of; every assembly incl.
intermediate sub-assemblies, filter `product.category`), `units_sold`, `revenue`, `component_demand` (order-date BOM,
direct + contained), `product_attr`.

## Data facts (from `gold.py`)

- 504 products, 238 assemblies (all `make_flag`), 97 bikes with a BOM; 2,576 composition edges (+103 header rows).
- BOM depth (current): at most 4 levels below a bike (97 assemblies of depth 4, 78 of depth 3).
- Dated BOM history is real and confined to 2021-03-03 .. 2021-12-22 (19 distinct start dates): 189 (assembly,
  component) pairs have more than one dated version (re-quantified), 4 lines end without a successor (removed), and most
  lines start after their assembly's earliest line (BOMs were built up over 2021). 237 of 238 assemblies have a direct
  BOM that differs between some date in 2021 and the current date; no two versions of the same line overlap.
- Orders: 31,465 orders / 121,317 lines, 2022-05-30 .. 2025-06-29, 10 territories; 266 products sold, 190 of them have a
  BOM (bikes, frames, wheels, ...), the rest are purchased goods (clothing, accessories, some components).
- Quirk: every order date is after the last BOM change, so "BOM valid on the order date" equals the current BOM in AW.
  The A3 filter therefore requires the answer to differ from the explosion that ignores validity (which double-counts
  superseded lines); a versioned-demand effect across order dates cannot be tested on AW (it is tested on xb).
- Quirk: quantities across bikes of the same part are uniform in AW (no bike needs more of a part than another), so a
  "which bike needs the most of X" template was replaced by a subcategory-restricted where-used count (W4).
- Units: `EA` (2,578 lines), `OZ` (92, paint), `IN` (9); quantities are summed in each line's own unit.

## Questions (`data/aw_questions.jsonl`, 188)

Record fields as xb (`qid` prefixed `AW-`, `cls`, `tpl`, `text`, `gold_type`, `gold`) plus `s3_expressible` (bool) and
`args` (instance parameters used only by `check_gold.py`; never show them to a model).

| class | n | templates (n) |
|---|---|---|
| L lookup | 21 | L1 standard cost by name (4), L2 subcategory (4), L3 current direct BOM set (5), L4 current direct qty of a re-versioned line (4), L5 count with list-price filter (4) |
| E explosion | 40 | E1 deep part qty, answer != path count (12), E2 distinct parts, != first-level count (10), E3 purchased-parts cost, != first-level cost (10), E4 distinct manufactured sub-assemblies, != first-level count (8) |
| W where-used | 37 | W1 # bikes containing C, some only indirectly (10), W2 list of those bikes, 2..14 (8), W3 # all products containing C, != direct parents and != bikes (8), W4 # bikes of a subcategory (7), **W5x** parts in bike 1 not in bike 2 (4) |
| A orders | 46 | A1 units by subcategory/territory/year[/channel] (8), A2 revenue by territory/quarter, line discounts matter (9), A3 part demand with order-date BOM, contained > 0 and != validity-ignored (10), A4 top-3 bikes, no tie at rank 3/4 (7), A5 sub-assembly demand, sold directly and contained (8), **A6x** bikes sold in T1 not in T2 (4) |
| V as-of | 44 | V1 part qty on d != current (10), V2 direct BOM set on d != current set (8), V3 distinct parts on d != current (8), V4 # bikes containing C on d != current (6), V5 purchased cost on d != current (4), **V6x** direct lines on d1 not on d2 (4), **V7x** qty change between d and current (4) |

As-of dates are drawn from the 2021 change window; 40% of them are a start/end date of one of the product's own BOM
lines, and 9 of the 30 covered V1/V2/V3/V5 answers change if either validity bound is made exclusive.

## Coverage and agreement (`python check_gold.py`)

- Out of single-query semantic-layer coverage: 16/188 (8.5%; templates W5x, A6x, V6x, V7x: set differences and
  comparisons across two dates). `data/aw_s3_coverage.json` marks them `false`.
- gold.py vs hand-written S3 oracle (one JSON query per template, compiled by `semantic.py`): **172/172** agree.
- Hand-written SQL on `aw_u.duckdb` (E1, E3, V1, V2, V5, W1, A2, A3, A5): 81/81 agree; SQL on `aw.duckdb` (E1, V1): 22/22.
- Hand-written Cypher on `aw_u.kuzu` (`CONTAINS`) and on `aw.kuzu` (`HAS_SUBASSEMBLY|HAS_PART`) for E1, E3, V1, V5, W1,
  A2: 55/55 agree on each.

## Plugging into a runner

Import the package from `code/` as `datasets.aw` (e.g. `from datasets.aw import prompts, semantic, SQL_DB, ...`), not
as top-level `prompts` / `semantic`, which would resolve to the xb modules in `code/`. `prompts` exposes the same names
as xb (`RULES, SQL_DOC, SQL_U_DOC, CYPHER_DOC, CYPHER_U_DOC, TASK, messages, repair`); `semantic` exposes `SPEC,
compile_query, SemanticError`. Database paths, the question file and the coverage file are constants in `__init__.py`.
`score.py` in `code/` is reused unchanged.
