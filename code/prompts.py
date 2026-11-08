"""Substrate documentation shown to the LLM. The business rules are stated identically for S1 and S2; S3 bakes
them into its metric definitions (semantic.SPEC). No substrate gets a worked example of a benchmark class."""
from semantic import SPEC

RULES = """Business rules:
- Composition: a company model uses components (model BOM); an assembly component (is_assembly = true) contains further
  components (component BOM), recursively. A "part" is a component with is_assembly = false. Quantity per unit
  multiplies along a path and sums over all paths.
- A non-bundle sales SKU maps to one or more company models x qty. A bundle SKU (is_bundle = true) has no model mapping;
  it contains item SKUs x qty, and the item SKUs map to models.
- BOM validity: a BOM edge is valid on date d if valid_from <= d and (valid_to IS NULL or d <= valid_to).
  "Current" means as of 2026-10-01.
- "Sold" / revenue refer to orders with status 'completed' only. An order line references a listing (a SKU in a store)."""

SQL_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
component(component_id INT PK, component_code VARCHAR, name VARCHAR, category VARCHAR, is_assembly BOOLEAN, unit_cost DOUBLE)
component_bom(parent_component_id INT -> component, child_component_id INT -> component, qty INT, valid_from DATE, valid_to DATE)
company_model(model_id INT PK, model_code VARCHAR, name VARCHAR, product_line VARCHAR)
model_bom(model_id INT -> company_model, component_id INT -> component, qty INT, valid_from DATE, valid_to DATE)
sales_sku(sku_id INT PK, sku_code VARCHAR, title VARCHAR, market VARCHAR, is_bundle BOOLEAN)
sku_model(sku_id INT -> sales_sku, model_id INT -> company_model, qty INT)
bundle_item(bundle_sku_id INT -> sales_sku, item_sku_id INT -> sales_sku, qty INT)
store(store_id INT PK, store_name VARCHAR, platform VARCHAR, country VARCHAR)
listing(listing_id INT PK, store_id INT -> store, sku_id INT -> sales_sku, external_id VARCHAR)
orders(order_id INT PK, store_id INT -> store, order_date DATE, status VARCHAR)   -- status in ('completed','cancelled')
order_line(order_id INT -> orders, line_no INT, listing_id INT -> listing, qty INT, unit_price DOUBLE)

{RULES}"""

CYPHER_DOC = f"""PROPERTY GRAPH (Kuzu, openCypher dialect)
Nodes:
(:Component {{component_id, component_code, name, category, is_assembly BOOLEAN, unit_cost DOUBLE}})
(:Model {{model_id, model_code, name, product_line}})
(:SKU {{sku_id, sku_code, title, market, is_bundle BOOLEAN}})
(:Store {{store_id, store_name, platform, country}})
(:Listing {{listing_id, external_id}})
(:SalesOrder {{order_id, order_date DATE, status STRING}})   -- status in ('completed','cancelled')
Relationships:
(:Component)-[:HAS_PART {{qty, valid_from DATE, valid_to DATE}}]->(:Component)
(:Model)-[:USES {{qty, valid_from DATE, valid_to DATE}}]->(:Component)
(:SKU)-[:MAPS_TO {{qty}}]->(:Model)
(:SKU)-[:BUNDLES {{qty}}]->(:SKU)          -- bundle SKU -> item SKU
(:Listing)-[:OF_SKU]->(:SKU)
(:Listing)-[:LISTED_IN]->(:Store)
(:SalesOrder)-[:PLACED_IN]->(:Store)
(:SalesOrder)-[:HAS_LINE {{line_no, qty, unit_price DOUBLE}}]->(:Listing)

Kuzu dialect notes: variable-length patterns need an upper bound, e.g. -[e:HAS_PART*1..6]->; a predicate on every
relationship of a variable-length pattern is written -[e:HAS_PART*1..6 (r, n | WHERE r.qty > 0)]->; several relationship types are matched with -[e:A|B*1..6]->; rels(e) returns
the relationship list, properties(rels(e), 'qty') the list of qty values, list_product(list) multiplies a list;
list_product of an empty list is 1 (so *0..k paths work); date literals are written date('2026-10-01');
date_part('year', d) extracts a field (year(d) does not exist).

{RULES}"""

RULES_U = RULES.replace("""- Composition: a company model uses components (model BOM); an assembly component (is_assembly = true) contains further
  components (component BOM), recursively.""", """- Composition: every composition step is one edge parent -> child x qty: bundle SKU -> item SKU, non-bundle SKU -> company
  model, company model -> component, assembly component -> component. An assembly component has is_assembly = true.""").replace(
    "- BOM validity: a BOM edge", "- BOM validity: a composition edge").replace(
    "  \"Current\" means as of 2026-10-01.", "  SKU-level edges carry valid_from = 1900-01-01 and valid_to = NULL. \"Current\" means as of 2026-10-01.")

SQL_U_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
component(component_id INT PK, component_code VARCHAR, name VARCHAR, category VARCHAR, is_assembly BOOLEAN, unit_cost DOUBLE)
company_model(model_id INT PK, model_code VARCHAR, name VARCHAR, product_line VARCHAR)
sales_sku(sku_id INT PK, sku_code VARCHAR, title VARCHAR, market VARCHAR, is_bundle BOOLEAN)
u_composition(parent_kind VARCHAR, parent_code VARCHAR, child_kind VARCHAR, child_code VARCHAR, qty INT, valid_from DATE, valid_to DATE)
   -- kinds are 'sku', 'model', 'component'; codes are sku_code, model_code, component_code
store(store_id INT PK, store_name VARCHAR, platform VARCHAR, country VARCHAR)
listing(listing_id INT PK, store_id INT -> store, sku_id INT -> sales_sku, external_id VARCHAR)
orders(order_id INT PK, store_id INT -> store, order_date DATE, status VARCHAR)   -- status in ('completed','cancelled')
order_line(order_id INT -> orders, line_no INT, listing_id INT -> listing, qty INT, unit_price DOUBLE)

{RULES_U}"""

CYPHER_U_DOC = CYPHER_DOC.replace("""(:Component)-[:HAS_PART {qty, valid_from DATE, valid_to DATE}]->(:Component)
(:Model)-[:USES {qty, valid_from DATE, valid_to DATE}]->(:Component)
(:SKU)-[:MAPS_TO {qty}]->(:Model)
(:SKU)-[:BUNDLES {qty}]->(:SKU)          -- bundle SKU -> item SKU""", """(:SKU)-[:CONTAINS {qty, valid_from DATE, valid_to DATE}]->(:SKU)        -- bundle SKU -> item SKU
(:SKU)-[:CONTAINS {qty, valid_from DATE, valid_to DATE}]->(:Model)      -- non-bundle SKU -> company model
(:Model)-[:CONTAINS {qty, valid_from DATE, valid_to DATE}]->(:Component)
(:Component)-[:CONTAINS {qty, valid_from DATE, valid_to DATE}]->(:Component)""").replace(
    "-[e:HAS_PART*1..6]->", "-[e:CONTAINS*1..6]->").replace("-[e:HAS_PART*1..6 (r, n", "-[e:CONTAINS*1..6 (r, n").replace(RULES, RULES_U)

# Closure substrates (build_closure.py): SQL-U / Graph-U plus the materialized total-requirements relation; same rules text.
_X_MEANING = """qty = total quantity of the item contained in ONE unit of the root, multiplied along every composition path and summed
   -- over all paths, on every date d with valid_from <= d and (valid_to IS NULL or d <= valid_to); one row per root, item at
   -- any level below it (assemblies and parts alike) and interval of constant qty"""
_U_KINDS = "   -- kinds are 'sku', 'model', 'component'; codes are sku_code, model_code, component_code\n"
SQL_X_DOC = SQL_U_DOC.replace(_U_KINDS, _U_KINDS + """u_explosion(root_kind VARCHAR, root_code VARCHAR, item_kind VARCHAR, item_code VARCHAR, qty DOUBLE, valid_from DATE, valid_to DATE)
   -- materialized from u_composition (kinds and codes as there):
   -- """ + _X_MEANING + "\n")
_U_LAST = "(:Component)-[:CONTAINS {qty, valid_from DATE, valid_to DATE}]->(:Component)\n"
CYPHER_X_DOC = CYPHER_U_DOC.replace(_U_LAST, _U_LAST + "".join(
    f"({a})-[:EXPLODES {{qty DOUBLE, valid_from DATE, valid_to DATE}}]->({b})\n" for a, b in
    [(":SKU", ":SKU"), (":SKU", ":Model"), (":SKU", ":Component"), (":Model", ":Component"), (":Component", ":Component")]) +
    "   -- materialized from CONTAINS; start node = root, end node = item:\n   -- " + _X_MEANING + "\n")
assert SQL_X_DOC != SQL_U_DOC and CYPHER_X_DOC != CYPHER_U_DOC

TASK = {
    "sql": ("Write ONE DuckDB SQL query that answers the question. Return only the query in a ```sql block.", SQL_DOC),
    "cypher": ("Write ONE Kuzu Cypher query that answers the question. Return only the query in a ```cypher block.", CYPHER_DOC),
    "sql_u": ("Write ONE DuckDB SQL query that answers the question. Return only the query in a ```sql block.", SQL_U_DOC),
    "cypher_u": ("Write ONE Kuzu Cypher query that answers the question. Return only the query in a ```cypher block.", CYPHER_U_DOC),
    "sql_x": ("Write ONE DuckDB SQL query that answers the question. Return only the query in a ```sql block.", SQL_X_DOC),
    "cypher_x": ("Write ONE Kuzu Cypher query that answers the question. Return only the query in a ```cypher block.", CYPHER_X_DOC),
    "semantic_abs": ("Write ONE semantic-layer JSON query that answers the question. Return only the JSON in a ```json block. "
                     "If no metric can express the question, reply with the single word UNSUPPORTED instead of a query.", SPEC),
    "hybrid": ("Prefer ONE semantic-layer JSON query (in a ```json block). Only if no metric can express the question, write ONE "
               "DuckDB SQL query over the detail tables instead (in a ```sql block); such answers are labelled as fallback. "
               "Return exactly one block.", SPEC + "\n\nDETAIL TABLES (for fallback only)\n" + SQL_DOC),
    "semantic": ("Write ONE semantic-layer JSON query that answers the question. Return only the JSON in a ```json block.", SPEC),
}


def messages(substrate, question):
    inst, doc = TASK[substrate]
    sys = ("You answer questions over a product-composition database. " + inst +
           " The query result is taken as the answer, so it must return exactly the requested value(s): "
           "a single row for a single number, one row per item for a list.")
    return [{"role": "system", "content": sys}, {"role": "user", "content": doc + "\n\nQUESTION: " + question}]


def repair(substrate, prev_text, error):
    return {"role": "user", "content": f"Executing your query failed with this error:\n{error[:800]}\nReturn a corrected query in the same format."}
