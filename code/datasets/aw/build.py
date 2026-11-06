"""Build the four AdventureWorks substrates from one snapshot (data/aw_src.duckdb, a byte copy of
data/aw_src.duckdb, built by code/make_sources.py; sha256 in README.md).

S1  data/aw.duckdb   relational, AW's own table split (product / subcategory / category / bill_of_materials /
                     sales_order / sales_order_line / customer / store / sales_territory).
S1u data/aw_u.duckdb same, but bill_of_materials is replaced by one code-keyed edge table u_composition.
S2  data/aw.kuzu     typed property graph. AW has a single BOM edge type, so the typed graph splits composition edges
                     the way an MRP/ERP-to-graph migration does, by the make/buy flag of the child:
                     (:Product)-[:HAS_SUBASSEMBLY]->(:Product) when the child is manufactured in-house (make_flag),
                     (:Product)-[:HAS_PART]->(:Product)        when the child is purchased.
                     (In this snapshot make_flag = true <=> the child has its own BOM, so the split is static.)
S2u data/aw_u.kuzu   same graph with one CONTAINS edge type for every composition step.

Cleaning applied to all substrates: unit codes trimmed, per_assembly_qty cast to DOUBLE, make_flag taken from the raw
Product.csv (column MakeFlag; not in the snapshot), sales_order.status dropped (constant 5 = shipped for all 31,465
orders), sales_territory added (the 10 rows of AW's public SalesTerritory table; the snapshot has only territory_id).
BOM header rows (assembly_id NULL, bom_level 0) are kept in S1 bill_of_materials (as in AW) and are dropped from the
edge-only substrates (u_composition, Kuzu) because they are not parent -> child edges.

Usage: python build.py
"""
import csv, os, shutil, sys
import duckdb, kuzu

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from __init__ import DATA, SRC_DB, SQL_DB, SQL_U_DB, KG_DB, KG_U_DB  # noqa: E402

RAW_PRODUCT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'raw', 'aw', 'Product.csv')
CSV = os.path.join(DATA, "aw_kg_csv")

# AdventureWorks SalesTerritory (TerritoryID, Name, CountryRegionCode, Group).
TERRITORY = [(1, "Northwest", "US", "North America"), (2, "Northeast", "US", "North America"),
             (3, "Central", "US", "North America"), (4, "Southwest", "US", "North America"),
             (5, "Southeast", "US", "North America"), (6, "Canada", "CA", "North America"),
             (7, "France", "FR", "Europe"), (8, "Germany", "DE", "Europe"), (9, "Australia", "AU", "Pacific"),
             (10, "United Kingdom", "GB", "Europe")]


def _rm(p):
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.exists(p):
        os.remove(p)


def make_flags():
    out = {}
    with open(RAW_PRODUCT, newline="") as f:
        for r in csv.reader(f, delimiter="\t"):
            out[int(r[0])] = r[3].strip() == "1"
    return out


def build_sql(path, unified):
    _rm(path)
    con = duckdb.connect(path)
    con.execute(f"ATTACH '{SRC_DB}' AS src (READ_ONLY)")
    con.execute("CREATE TEMP TABLE mk(product_id INTEGER, make_flag BOOLEAN)")
    con.executemany("INSERT INTO mk VALUES (?, ?)", sorted(make_flags().items()))
    con.execute("""CREATE TABLE product AS SELECT p.product_id, p.product_number, p.name, p.color, mk.make_flag,
        p.is_finished_good, p.standard_cost, p.list_price, p.product_line, p.subcategory_id
        FROM src.product p JOIN mk USING (product_id) ORDER BY p.product_id""")
    con.execute("CREATE TABLE product_subcategory AS SELECT subcategory_id, category_id, name FROM src.product_subcategory ORDER BY 1")
    con.execute("CREATE TABLE product_category AS SELECT category_id, name FROM src.product_category ORDER BY 1")
    con.execute("CREATE TABLE sales_territory(territory_id INTEGER, name VARCHAR, country_code VARCHAR, territory_group VARCHAR)")
    con.executemany("INSERT INTO sales_territory VALUES (?, ?, ?, ?)", TERRITORY)
    con.execute("CREATE TABLE store AS SELECT store_id, name FROM src.store ORDER BY 1")
    con.execute("CREATE TABLE customer AS SELECT customer_id, store_id, territory_id FROM src.customer ORDER BY 1")
    con.execute("""CREATE TABLE sales_order AS SELECT order_id, order_date, online_order, customer_id, territory_id, total_due
        FROM src.sales_order ORDER BY 1""")
    con.execute("""CREATE TABLE sales_order_line AS SELECT order_id, line_id, product_id, order_qty, unit_price, unit_discount,
        line_total FROM src.sales_order_line ORDER BY 1, 2""")
    if not unified:
        con.execute("""CREATE TABLE bill_of_materials AS SELECT bom_id, assembly_id, component_id, start_date, end_date,
            trim(unit) AS unit, bom_level, CAST(per_assembly_qty AS DOUBLE) AS per_assembly_qty
            FROM src.bill_of_materials ORDER BY bom_id""")
    else:
        con.execute("""CREATE TABLE u_composition AS SELECT pa.product_number AS parent_code, ch.product_number AS child_code,
            CAST(b.per_assembly_qty AS DOUBLE) AS qty, trim(b.unit) AS unit, b.start_date AS valid_from, b.end_date AS valid_to
            FROM src.bill_of_materials b JOIN src.product pa ON pa.product_id = b.assembly_id
            JOIN src.product ch ON ch.product_id = b.component_id
            WHERE b.assembly_id IS NOT NULL ORDER BY parent_code, child_code, valid_from""")
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog = current_database() ORDER BY 1").fetchall():
        print(os.path.basename(path), t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    con.close()


NODE_DDL = [
    "CREATE NODE TABLE Product(product_id INT64, product_number STRING, name STRING, color STRING, make_flag BOOLEAN, "
    "is_finished_good BOOLEAN, standard_cost DOUBLE, list_price DOUBLE, product_line STRING, PRIMARY KEY(product_id))",
    "CREATE NODE TABLE Subcategory(subcategory_id INT64, name STRING, PRIMARY KEY(subcategory_id))",
    "CREATE NODE TABLE Category(category_id INT64, name STRING, PRIMARY KEY(category_id))",
    "CREATE NODE TABLE Territory(territory_id INT64, name STRING, country_code STRING, territory_group STRING, PRIMARY KEY(territory_id))",
    "CREATE NODE TABLE Store(store_id INT64, name STRING, PRIMARY KEY(store_id))",
    "CREATE NODE TABLE Customer(customer_id INT64, PRIMARY KEY(customer_id))",
    "CREATE NODE TABLE SalesOrder(order_id INT64, order_date DATE, online_order BOOLEAN, total_due DOUBLE, PRIMARY KEY(order_id))",
]
REL_DDL = [
    "CREATE REL TABLE IN_SUBCATEGORY(FROM Product TO Subcategory)",
    "CREATE REL TABLE IN_CATEGORY(FROM Subcategory TO Category)",
    "CREATE REL TABLE BELONGS_TO(FROM Customer TO Store)",
    "CREATE REL TABLE PLACED_BY(FROM SalesOrder TO Customer)",
    "CREATE REL TABLE IN_TERRITORY(FROM SalesOrder TO Territory)",
    "CREATE REL TABLE HAS_LINE(FROM SalesOrder TO Product, line_id INT64, order_qty INT64, unit_price DOUBLE, unit_discount DOUBLE, line_total DOUBLE)",
]
EXPORTS = {
    "Product": "SELECT product_id, product_number, name, color, make_flag, is_finished_good, standard_cost, list_price, product_line FROM product",
    "Subcategory": "SELECT subcategory_id, name FROM product_subcategory",
    "Category": "SELECT category_id, name FROM product_category",
    "Territory": "SELECT territory_id, name, country_code, territory_group FROM sales_territory",
    "Store": "SELECT store_id, name FROM store",
    "Customer": "SELECT customer_id FROM customer",
    "SalesOrder": "SELECT order_id, order_date, online_order, total_due FROM sales_order",
    "IN_SUBCATEGORY": "SELECT product_id, subcategory_id FROM product WHERE subcategory_id IS NOT NULL",
    "IN_CATEGORY": "SELECT subcategory_id, category_id FROM product_subcategory",
    "BELONGS_TO": "SELECT customer_id, store_id FROM customer WHERE store_id IS NOT NULL",
    "PLACED_BY": "SELECT order_id, customer_id FROM sales_order",
    "IN_TERRITORY": "SELECT order_id, territory_id FROM sales_order",
    "HAS_LINE": "SELECT order_id, product_id, line_id, order_qty, unit_price, unit_discount, line_total FROM sales_order_line",
    "HAS_SUBASSEMBLY": """SELECT b.assembly_id, b.component_id, b.per_assembly_qty AS qty, b.unit, b.start_date AS valid_from,
        b.end_date AS valid_to, b.bom_level FROM bill_of_materials b JOIN product c ON c.product_id = b.component_id
        WHERE b.assembly_id IS NOT NULL AND c.make_flag""",
    "HAS_PART": """SELECT b.assembly_id, b.component_id, b.per_assembly_qty AS qty, b.unit, b.start_date AS valid_from,
        b.end_date AS valid_to, b.bom_level FROM bill_of_materials b JOIN product c ON c.product_id = b.component_id
        WHERE b.assembly_id IS NOT NULL AND NOT c.make_flag""",
    "CONTAINS": """SELECT assembly_id, component_id, per_assembly_qty AS qty, unit, start_date AS valid_from, end_date AS valid_to
        FROM bill_of_materials WHERE assembly_id IS NOT NULL""",
}


def build_kg(path, unified):
    _rm(path)
    os.makedirs(CSV, exist_ok=True)
    con = duckdb.connect(SQL_DB, read_only=True)
    for name, q in EXPORTS.items():
        con.execute(f"COPY ({q}) TO '{os.path.join(CSV, name + '.csv')}' (HEADER, DELIMITER ',')")
    con.close()
    k = kuzu.Connection(kuzu.Database(path))
    comp = (["CREATE REL TABLE CONTAINS(FROM Product TO Product, qty DOUBLE, unit STRING, valid_from DATE, valid_to DATE)"] if unified else
            ["CREATE REL TABLE HAS_SUBASSEMBLY(FROM Product TO Product, qty DOUBLE, unit STRING, valid_from DATE, valid_to DATE, bom_level INT64)",
             "CREATE REL TABLE HAS_PART(FROM Product TO Product, qty DOUBLE, unit STRING, valid_from DATE, valid_to DATE, bom_level INT64)"])
    for s in NODE_DDL + REL_DDL + comp:
        k.execute(s)
    rels = ["IN_SUBCATEGORY", "IN_CATEGORY", "BELONGS_TO", "PLACED_BY", "IN_TERRITORY", "HAS_LINE"] + (
        ["CONTAINS"] if unified else ["HAS_SUBASSEMBLY", "HAS_PART"])
    nodes = ["Product", "Subcategory", "Category", "Territory", "Store", "Customer", "SalesOrder"]
    for t in nodes + rels:
        k.execute(f"COPY {t} FROM '{os.path.join(CSV, t + '.csv')}' (HEADER=true)")
    for t in nodes:
        print(os.path.basename(path), t, k.execute(f"MATCH (n:{t}) RETURN count(n)").get_next()[0])
    for t in rels:
        print(os.path.basename(path), t, k.execute(f"MATCH ()-[r:{t}]->() RETURN count(r)").get_next()[0])


if __name__ == "__main__":
    build_sql(SQL_DB, unified=False)
    build_sql(SQL_U_DB, unified=True)
    build_kg(KG_DB, unified=False)
    build_kg(KG_U_DB, unified=True)
