"""AW substrate documentation shown to the LLM. Same interface as code/prompts.py (RULES, SQL_DOC, SQL_U_DOC, CYPHER_DOC,
CYPHER_U_DOC, TASK, messages, repair). The business rules are stated identically for S1/S2 (and their unified
variants, with the composition sentence adapted); S3 bakes them into its metric definitions (semantic.SPEC).
No substrate gets a worked example of a benchmark class."""
import importlib.util, os

try:
    from .semantic import SPEC
except ImportError:  # loaded as a plain module / by file path
    _s = importlib.util.spec_from_file_location("aw_semantic", os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic.py"))
    _m = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(_m)
    SPEC = _m.SPEC

COMP_TYPED = """- Composition: an assembly product has bill-of-materials (BOM) lines, each a component product x per-assembly quantity;
  a component that is manufactured in-house (make_flag = true) has its own BOM, recursively. A "part" is a purchased
  product (make_flag = false). Quantity per unit multiplies along a path and sums over all paths."""

RULES = f"""Business rules:
{COMP_TYPED}
- BOM validity: a BOM line is valid on date d if start_date <= d and (end_date IS NULL or d <= end_date).
  "Current" means as of 2025-06-30.
- Products are identified by product_number (e.g. 'BK-R93R-44'). "Bikes" are the products of category 'Bikes'.
- Revenue is the sum of order-line line_total (after discount); an order's total_due also contains tax and freight.
- Component demand of orders: every order line requires its own product (units sold directly) plus every component at
  any level of that product's BOM valid on the ORDER DATE (units contained), times the ordered quantity."""

_COMMON_TABLES = """product(product_id INT PK, product_number VARCHAR, name VARCHAR, color VARCHAR, make_flag BOOLEAN, is_finished_good BOOLEAN,
        standard_cost DOUBLE, list_price DOUBLE, product_line VARCHAR, subcategory_id INT -> product_subcategory)
product_subcategory(subcategory_id INT PK, category_id INT -> product_category, name VARCHAR)
product_category(category_id INT PK, name VARCHAR)"""

_SALES_TABLES = """sales_territory(territory_id INT PK, name VARCHAR, country_code VARCHAR, territory_group VARCHAR)
store(store_id INT PK, name VARCHAR)
customer(customer_id INT PK, store_id INT -> store, territory_id INT -> sales_territory)   -- store_id NULL for individual customers
sales_order(order_id INT PK, order_date DATE, online_order BOOLEAN, customer_id INT -> customer, territory_id INT -> sales_territory, total_due DOUBLE)
sales_order_line(order_id INT -> sales_order, line_id INT, product_id INT -> product, order_qty INT, unit_price DOUBLE,
                 unit_discount DOUBLE, line_total DOUBLE)"""

SQL_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
{_COMMON_TABLES}
bill_of_materials(bom_id INT PK, assembly_id INT -> product, component_id INT -> product, start_date DATE, end_date DATE,
                  unit VARCHAR, bom_level INT, per_assembly_qty DOUBLE)
   -- rows with assembly_id NULL are BOM header rows (top-level product markers), not component lines
{_SALES_TABLES}

{RULES}"""

RULES_U = RULES.replace(COMP_TYPED, """- Composition: every BOM line is one edge parent -> child x qty between two products; a child that is manufactured
  in-house (make_flag = true) has its own outgoing edges, recursively. A "part" is a purchased product
  (make_flag = false). Quantity per unit multiplies along a path and sums over all paths.""").replace(
    "- BOM validity: a BOM line is valid on date d if start_date <= d and (end_date IS NULL or d <= end_date).",
    "- BOM validity: a composition edge is valid on date d if valid_from <= d and (valid_to IS NULL or d <= valid_to).")

SQL_U_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
{_COMMON_TABLES}
u_composition(parent_code VARCHAR, child_code VARCHAR, qty DOUBLE, unit VARCHAR, valid_from DATE, valid_to DATE)
   -- codes are product.product_number
{_SALES_TABLES}

{RULES_U}"""

RULES_KG = RULES.replace(
    "- BOM validity: a BOM line is valid on date d if start_date <= d and (end_date IS NULL or d <= end_date).",
    "- BOM validity: a BOM edge is valid on date d if valid_from <= d and (valid_to IS NULL or d <= valid_to).")

_NODES = """Nodes:
(:Product {{product_id, product_number, name, color, make_flag BOOLEAN, is_finished_good BOOLEAN, standard_cost DOUBLE,
           list_price DOUBLE, product_line}})
(:Subcategory {{subcategory_id, name}})
(:Category {{category_id, name}})
(:Territory {{territory_id, name, country_code, territory_group}})
(:Store {{store_id, name}})
(:Customer {{customer_id}})
(:SalesOrder {{order_id, order_date DATE, online_order BOOLEAN, total_due DOUBLE}})"""

_RELS_SALES = """(:Product)-[:IN_SUBCATEGORY]->(:Subcategory)
(:Subcategory)-[:IN_CATEGORY]->(:Category)
(:Customer)-[:BELONGS_TO]->(:Store)           -- reseller customers only
(:SalesOrder)-[:PLACED_BY]->(:Customer)
(:SalesOrder)-[:IN_TERRITORY]->(:Territory)
(:SalesOrder)-[:HAS_LINE {{line_id, order_qty, unit_price DOUBLE, unit_discount DOUBLE, line_total DOUBLE}}]->(:Product)"""

_KUZU = """Kuzu dialect notes: variable-length patterns need an upper bound, e.g. -[e:{rel}*1..6]->; a predicate on every
relationship of a variable-length pattern is written -[e:{rel}*1..6 (r, n | WHERE r.qty > 0)]->; several relationship
types are matched with -[e:A|B*1..6]->; rels(e) returns the relationship list, properties(rels(e), 'qty') the list of
qty values, list_product(list) multiplies a list; list_product of an empty list is 1 (so *0..k paths work); date
literals are written date('2025-06-30'); date_part('year', d) extracts a field (year(d) does not exist)."""

CYPHER_DOC = f"""PROPERTY GRAPH (Kuzu, openCypher dialect)
{_NODES.format()}
Relationships:
(:Product)-[:HAS_SUBASSEMBLY {{qty DOUBLE, unit, valid_from DATE, valid_to DATE, bom_level}}]->(:Product)   -- child manufactured in-house
(:Product)-[:HAS_PART {{qty DOUBLE, unit, valid_from DATE, valid_to DATE, bom_level}}]->(:Product)          -- child purchased
{_RELS_SALES.format()}

{_KUZU.format(rel='HAS_PART')}

{RULES_KG}"""

CYPHER_U_DOC = f"""PROPERTY GRAPH (Kuzu, openCypher dialect)
{_NODES.format()}
Relationships:
(:Product)-[:CONTAINS {{qty DOUBLE, unit, valid_from DATE, valid_to DATE}}]->(:Product)   -- one BOM line: parent -> child
{_RELS_SALES.format()}

{_KUZU.format(rel='CONTAINS')}

{RULES_U}"""

TASK = {
    "sql": ("Write ONE DuckDB SQL query that answers the question. Return only the query in a ```sql block.", SQL_DOC),
    "cypher": ("Write ONE Kuzu Cypher query that answers the question. Return only the query in a ```cypher block.", CYPHER_DOC),
    "sql_u": ("Write ONE DuckDB SQL query that answers the question. Return only the query in a ```sql block.", SQL_U_DOC),
    "cypher_u": ("Write ONE Kuzu Cypher query that answers the question. Return only the query in a ```cypher block.", CYPHER_U_DOC),
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
