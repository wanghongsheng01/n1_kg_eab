"""Substrate documentation shown to the LLM for the LEGO (Rebrickable) dataset. Same interface as code/prompts.py
(RULES, SQL_DOC, SQL_U_DOC, CYPHER_DOC, CYPHER_U_DOC, TASK, messages, repair). The business rules are stated once
(RULES) and shared by all four schema docs; S3 bakes them into its metric definitions (semantic.SPEC).
No substrate gets a worked example of a benchmark class.
"""
import importlib.util, os

_spec = importlib.util.spec_from_file_location("lego_semantic", os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic.py"))
_sem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sem)
SPEC = _sem.SPEC

RULES = """Business rules:
- A set's contents come from its inventory. A set can have several inventory versions (1, 2, ...); its CURRENT inventory
  is the one with the highest version number. Questions refer to the current inventory unless they name a version.
  A named version applies to the asked set's own inventory only: nested sets and minifigs inside it always contribute
  their own current inventory.
- An inventory lists parts (part x colour x quantity, each row flagged is_spare), nested sets (set x quantity) and
  minifigs (minifig x quantity). A minifig has its own inventory, which lists its parts.
- Contents are recursive: the parts of one unit of a set are the parts of its own inventory plus, for every nested set
  and every minifig, quantity x that item's parts (recursively). Quantities multiply along a nesting path and sum over
  all paths. "At any level" / "at all levels" means through nested sets and minifigs.
- Spare parts (is_spare = true) are excluded from every part count and from where-used unless the question explicitly
  says to include spare parts.
- Part identity: unless a colour is named, a part is identified by its part number alone (all colours together);
  "part P in colour C" and "part/colour combinations" use the pair (part number, colour). Colours are named by colour name.
- Themes form a hierarchy (a theme may have a parent theme; a top-level theme has none). A set belongs to one theme;
  "theme T including its sub-themes" means T and every theme below it at any depth.
- Minifigs are not sets. A set contains an item if the item occurs in its current exploded contents. When a question
  sums over several sets, each set is exploded on its own (a set nested in another set also counts as a set itself).
- num_parts is the catalogue's printed piece count; it is not derived from inventories and is not a part count here."""

SQL_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
sets(set_num VARCHAR PK, name VARCHAR, year INT, theme_id INT -> themes, num_parts INT)
themes(id INT PK, name VARCHAR, parent_id INT -> themes)            -- parent_id NULL for a top-level theme
minifigs(fig_num VARCHAR PK, name VARCHAR, num_parts INT)
parts(part_num VARCHAR PK, name VARCHAR, part_cat_id INT -> part_categories)
part_categories(id INT PK, name VARCHAR)
colors(id INT PK, name VARCHAR)
inventories(id INT PK, version INT, set_num VARCHAR)   -- set_num is a sets.set_num or a minifigs.fig_num
inventory_parts(inventory_id INT -> inventories, part_num VARCHAR -> parts, color_id INT -> colors, quantity INT, is_spare BOOLEAN)
inventory_sets(inventory_id INT -> inventories, set_num VARCHAR -> sets, quantity INT)          -- nested sets
inventory_minifigs(inventory_id INT -> inventories, fig_num VARCHAR -> minifigs, quantity INT)

{RULES}"""

RULES_U = RULES + """
- In u_composition / CONTAINS, every edge carries the version of the PARENT's inventory it comes from; the current
  inventory of a set or minifig is the highest version among its outgoing edges."""

SQL_U_DOC = f"""DATABASE (DuckDB SQL dialect; recursive CTEs are supported)
sets(set_num VARCHAR PK, name VARCHAR, year INT, theme_id INT -> themes, num_parts INT)
themes(id INT PK, name VARCHAR, parent_id INT -> themes)            -- parent_id NULL for a top-level theme
minifigs(fig_num VARCHAR PK, name VARCHAR, num_parts INT)
parts(part_num VARCHAR PK, name VARCHAR, part_cat_id INT -> part_categories)
part_categories(id INT PK, name VARCHAR)
colors(id INT PK, name VARCHAR)
u_composition(parent_kind VARCHAR, parent_num VARCHAR, version INT, child_kind VARCHAR, child_num VARCHAR,
              color_id INT -> colors, qty INT, is_spare BOOLEAN)
   -- one row per inventory line: parent_kind in ('set','minifig') with parent_num = set_num / fig_num;
   -- child_kind in ('set','minifig','part') with child_num = set_num / fig_num / part_num;
   -- color_id is NULL and is_spare FALSE unless child_kind = 'part'

{RULES_U}"""

# Uniform across datasets (main study): syntax notes only, identical in content to xb and aw, including the multi-type
# pattern syntax (A|B); no EXISTS hints. The note without the multi-type sentence is the ablation cypher_nh.
DIALECT = """Kuzu dialect notes: variable-length patterns need an upper bound, e.g. -[e:R*1..6]->; a predicate on every
relationship of a variable-length pattern is written -[e:R*1..6 (r, n | WHERE r.qty > 0)]->; several relationship types are matched with -[e:A|B*1..6]->; rels(e) returns the
relationship list, properties(rels(e), 'qty') the list of qty values, list_product(list) multiplies a list;
list_product of an empty list is 1 (so *0..k paths work)."""

CYPHER_DOC = f"""PROPERTY GRAPH (Kuzu, openCypher dialect)
Nodes:
(:Set {{set_num STRING, name, year INT64, num_parts INT64}})
(:Theme {{id INT64, name}})
(:Minifig {{fig_num STRING, name, num_parts INT64}})
(:Part {{part_num STRING, name}})
(:PartCategory {{id INT64, name}})
(:Color {{id INT64, name}})
(:Inventory {{id INT64, version INT64}})
Relationships:
(:Set)-[:HAS_INVENTORY]->(:Inventory)
(:Minifig)-[:HAS_INVENTORY]->(:Inventory)
(:Inventory)-[:CONTAINS_SET {{qty}}]->(:Set)              -- nested set
(:Inventory)-[:CONTAINS_MINIFIG {{qty}}]->(:Minifig)
(:Inventory)-[:HAS_PART {{color_id INT64, qty, is_spare BOOLEAN}}]->(:Part)   -- color_id = Color.id
(:Set)-[:IN_THEME]->(:Theme)
(:Theme)-[:SUBTHEME_OF]->(:Theme)                         -- child theme -> parent theme
(:Part)-[:IN_CATEGORY]->(:PartCategory)

{DIALECT}

{RULES}"""

CYPHER_U_DOC = f"""PROPERTY GRAPH (Kuzu, openCypher dialect)
Nodes:
(:Set {{set_num STRING, name, year INT64, num_parts INT64}})
(:Theme {{id INT64, name}})
(:Minifig {{fig_num STRING, name, num_parts INT64}})
(:Part {{part_num STRING, name}})
(:PartCategory {{id INT64, name}})
(:Color {{id INT64, name}})
Relationships (one composition relationship CONTAINS, one edge per inventory line):
(:Set)-[:CONTAINS {{version INT64, qty, color_id INT64, is_spare BOOLEAN}}]->(:Set)        -- nested set
(:Set)-[:CONTAINS {{version INT64, qty, color_id INT64, is_spare BOOLEAN}}]->(:Minifig)
(:Set)-[:CONTAINS {{version INT64, qty, color_id INT64, is_spare BOOLEAN}}]->(:Part)
(:Minifig)-[:CONTAINS {{version INT64, qty, color_id INT64, is_spare BOOLEAN}}]->(:Part)
   -- color_id = Color.id for part edges (NULL otherwise); is_spare is FALSE for non-part edges
(:Set)-[:IN_THEME]->(:Theme)
(:Theme)-[:SUBTHEME_OF]->(:Theme)                         -- child theme -> parent theme
(:Part)-[:IN_CATEGORY]->(:PartCategory)

{DIALECT}

{RULES_U}"""

# Closure substrates (code/build_closure.py): SQL-U / Graph-U plus the materialized total-contents relation; same rules text.
_X_MEANING = """qty = total quantity of the item contained in ONE unit of the root when the root's own inventory is version
   -- root_version, multiplied along every nesting path and summed over all paths; one row per root, root_version and item
   -- at any level below it (nested sets, minifigs and parts alike); is_current = true for the root's current inventory
   -- version; for a part item, color_id and is_spare are those of the inventory line it comes from (color_id NULL and
   -- is_spare FALSE for set and minifig items)"""
_U_LAST_SQL = "   -- color_id is NULL and is_spare FALSE unless child_kind = 'part'\n"
SQL_X_DOC = SQL_U_DOC.replace(_U_LAST_SQL, _U_LAST_SQL + """u_explosion(root_kind VARCHAR, root_num VARCHAR, root_version INT, is_current BOOLEAN, item_kind VARCHAR, item_num VARCHAR,
            color_id INT -> colors, is_spare BOOLEAN, qty BIGINT)
   -- materialized from u_composition (kinds and nums as there):
   -- """ + _X_MEANING + "\n")
_U_LAST_KG = "   -- color_id = Color.id for part edges (NULL otherwise); is_spare is FALSE for non-part edges\n"
CYPHER_X_DOC = CYPHER_U_DOC.replace(_U_LAST_KG, _U_LAST_KG + "".join(
    f"({a})-[:EXPLODES {{root_version INT64, is_current BOOLEAN, qty, color_id INT64, is_spare BOOLEAN}}]->({b})\n" for a, b in
    [(":Set", ":Set"), (":Set", ":Minifig"), (":Set", ":Part"), (":Minifig", ":Part")]) +
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
