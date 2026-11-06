"""Build the three substrates over one snapshot of the synthetic composition database `xb`.

S1 (SQL)      : data/xb.duckdb, the relational schema as generated (read-only copy of the source snapshot).
S2 (KG)       : data/xb.kuzu, a property graph with the same facts (Kuzu, Cypher).
S3 (semantic) : no extra storage; semantic.py compiles metric queries to SQL over data/xb.duckdb.

Usage: python build_substrates.py
"""
import os, shutil, duckdb, kuzu

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
SRC = os.path.join(DATA, "xb_src.duckdb")
SQL_DB = os.path.join(DATA, "xb.duckdb")
KG_DB = os.path.join(DATA, "xb.kuzu")
CSV = os.path.join(DATA, "kg_csv")
SUFFIX = ""


def configure(src, suffix):
    """Point the builders at another source snapshot (used for the perturbed instances)."""
    global SRC, SQL_DB, KG_DB, CSV, SUFFIX
    SRC, SUFFIX = src, suffix
    SQL_DB = os.path.join(DATA, f"xb{suffix}.duckdb")
    KG_DB = os.path.join(DATA, f"xb{suffix}.kuzu")
    CSV = os.path.join(DATA, f"kg_csv{suffix}")


def build_sql():
    if os.path.exists(SQL_DB):
        os.remove(SQL_DB)
    con = duckdb.connect(SQL_DB)
    con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog='src' AND table_schema='main'").fetchall():
        con.execute(f"CREATE TABLE main.{t} AS SELECT * FROM src.main.{t}")
    con.close()


def build_kg():
    for p in (KG_DB, CSV):
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    os.makedirs(CSV)
    con = duckdb.connect(SQL_DB, read_only=True)
    exports = {
        "component": "SELECT component_id, component_code, name, category, is_assembly, unit_cost FROM component",
        "model": "SELECT model_id, model_code, name, product_line FROM company_model",
        "sku": "SELECT sku_id, sku_code, title, market, is_bundle FROM sales_sku",
        "store": "SELECT store_id, store_name, platform, country FROM store",
        "listing": "SELECT listing_id, external_id FROM listing",
        "orders": "SELECT order_id, order_date, status FROM orders",
        "has_part": "SELECT parent_component_id, child_component_id, qty, valid_from, valid_to FROM component_bom",
        "uses": "SELECT model_id, component_id, qty, valid_from, valid_to FROM model_bom",
        "maps_to": "SELECT sku_id, model_id, qty FROM sku_model",
        "bundles": "SELECT bundle_sku_id, item_sku_id, qty FROM bundle_item",
        "of_sku": "SELECT listing_id, sku_id FROM listing",
        "listed_in": "SELECT listing_id, store_id FROM listing",
        "placed_in": "SELECT order_id, store_id FROM orders",
        "has_line": "SELECT order_id, listing_id, line_no, qty, unit_price FROM order_line",
    }
    for name, q in exports.items():
        con.execute(f"COPY ({q}) TO '{os.path.join(CSV, name + '.csv')}' (HEADER, DELIMITER ',')")
    con.close()

    db = kuzu.Database(KG_DB)
    k = kuzu.Connection(db)
    ddl = [
        "CREATE NODE TABLE Component(component_id INT64, component_code STRING, name STRING, category STRING, is_assembly BOOLEAN, unit_cost DOUBLE, PRIMARY KEY(component_id))",
        "CREATE NODE TABLE Model(model_id INT64, model_code STRING, name STRING, product_line STRING, PRIMARY KEY(model_id))",
        "CREATE NODE TABLE SKU(sku_id INT64, sku_code STRING, title STRING, market STRING, is_bundle BOOLEAN, PRIMARY KEY(sku_id))",
        "CREATE NODE TABLE Store(store_id INT64, store_name STRING, platform STRING, country STRING, PRIMARY KEY(store_id))",
        "CREATE NODE TABLE Listing(listing_id INT64, external_id STRING, PRIMARY KEY(listing_id))",
        "CREATE NODE TABLE SalesOrder(order_id INT64, order_date DATE, status STRING, PRIMARY KEY(order_id))",
        "CREATE REL TABLE HAS_PART(FROM Component TO Component, qty INT64, valid_from DATE, valid_to DATE)",
        "CREATE REL TABLE USES(FROM Model TO Component, qty INT64, valid_from DATE, valid_to DATE)",
        "CREATE REL TABLE MAPS_TO(FROM SKU TO Model, qty INT64)",
        "CREATE REL TABLE BUNDLES(FROM SKU TO SKU, qty INT64)",
        "CREATE REL TABLE OF_SKU(FROM Listing TO SKU)",
        "CREATE REL TABLE LISTED_IN(FROM Listing TO Store)",
        "CREATE REL TABLE PLACED_IN(FROM SalesOrder TO Store)",
        "CREATE REL TABLE HAS_LINE(FROM SalesOrder TO Listing, line_no INT64, qty INT64, unit_price DOUBLE)",
    ]
    for s in ddl:
        k.execute(s)
    load = [("Component", "component"), ("Model", "model"), ("SKU", "sku"), ("Store", "store"), ("Listing", "listing"),
            ("SalesOrder", "orders"), ("HAS_PART", "has_part"), ("USES", "uses"), ("MAPS_TO", "maps_to"), ("BUNDLES", "bundles"),
            ("OF_SKU", "of_sku"), ("LISTED_IN", "listed_in"), ("PLACED_IN", "placed_in"), ("HAS_LINE", "has_line")]
    for t, f in load:
        k.execute(f"COPY {t} FROM '{os.path.join(CSV, f + '.csv')}' (HEADER=true)")
    for t in ["Component", "Model", "SKU", "Store", "Listing", "SalesOrder"]:
        print(t, k.execute(f"MATCH (n:{t}) RETURN count(n)").get_next()[0])
    for t in ["HAS_PART", "USES", "MAPS_TO", "BUNDLES", "OF_SKU", "LISTED_IN", "PLACED_IN", "HAS_LINE"]:
        print(t, k.execute(f"MATCH ()-[r:{t}]->() RETURN count(r)").get_next()[0])




# ---------------------------------------------------------------------------------------------------------------
# Modeling variants ("_u" = unified composition): every composition step (bundle->SKU, SKU->model, model->component,
# component->component) is one edge type, so a single recursive pattern walks the whole product structure.
U_DATE_MIN = "1900-01-01"


def build_unified():
    upath = os.path.join(DATA, f"xb{SUFFIX}_u.duckdb")
    if os.path.exists(upath):
        os.remove(upath)
    con = duckdb.connect(upath)
    con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog='src' AND table_schema='main'").fetchall():
        con.execute(f"CREATE TABLE main.{t} AS SELECT * FROM src.main.{t}")
    con.execute(f"""CREATE TABLE u_composition AS
      SELECT 'sku' AS parent_kind, b.sku_code AS parent_code, 'sku' AS child_kind, i.sku_code AS child_code, bi.qty,
             DATE '{U_DATE_MIN}' AS valid_from, CAST(NULL AS DATE) AS valid_to
        FROM bundle_item bi JOIN sales_sku b ON b.sku_id = bi.bundle_sku_id JOIN sales_sku i ON i.sku_id = bi.item_sku_id
      UNION ALL SELECT 'sku', s.sku_code, 'model', m.model_code, sm.qty, DATE '{U_DATE_MIN}', NULL
        FROM sku_model sm JOIN sales_sku s ON s.sku_id = sm.sku_id JOIN company_model m ON m.model_id = sm.model_id
      UNION ALL SELECT 'model', m.model_code, 'component', c.component_code, mb.qty, mb.valid_from, mb.valid_to
        FROM model_bom mb JOIN company_model m ON m.model_id = mb.model_id JOIN component c ON c.component_id = mb.component_id
      UNION ALL SELECT 'component', p.component_code, 'component', c.component_code, cb.qty, cb.valid_from, cb.valid_to
        FROM component_bom cb JOIN component p ON p.component_id = cb.parent_component_id JOIN component c ON c.component_id = cb.child_component_id""")
    print("u_composition", con.execute("SELECT count(*) FROM u_composition").fetchone()[0])
    for t in ("bundle_item", "sku_model", "model_bom", "component_bom"):
        con.execute(f"DROP TABLE {t}")
    con.close()

    path = os.path.join(DATA, f"xb{SUFFIX}_u.kuzu")
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(SQL_DB, read_only=True)
    parts = {
        "c_bundle": "SELECT b.sku_id AS f, i.sku_id AS t, bi.qty, DATE '1900-01-01' AS valid_from, CAST(NULL AS DATE) AS valid_to FROM bundle_item bi JOIN sales_sku b ON b.sku_id=bi.bundle_sku_id JOIN sales_sku i ON i.sku_id=bi.item_sku_id",
        "c_skumodel": "SELECT sku_id AS f, model_id AS t, qty, DATE '1900-01-01' AS valid_from, CAST(NULL AS DATE) AS valid_to FROM sku_model",
        "c_modelcomp": "SELECT model_id AS f, component_id AS t, qty, valid_from, valid_to FROM model_bom",
        "c_compcomp": "SELECT parent_component_id AS f, child_component_id AS t, qty, valid_from, valid_to FROM component_bom",
    }
    for name, q in parts.items():
        con.execute(f"COPY ({q}) TO '{os.path.join(CSV, name + '.csv')}' (HEADER, DELIMITER ',')")
    con.close()
    db = kuzu.Database(path)
    k = kuzu.Connection(db)
    for s in ["CREATE NODE TABLE Component(component_id INT64, component_code STRING, name STRING, category STRING, is_assembly BOOLEAN, unit_cost DOUBLE, PRIMARY KEY(component_id))",
              "CREATE NODE TABLE Model(model_id INT64, model_code STRING, name STRING, product_line STRING, PRIMARY KEY(model_id))",
              "CREATE NODE TABLE SKU(sku_id INT64, sku_code STRING, title STRING, market STRING, is_bundle BOOLEAN, PRIMARY KEY(sku_id))",
              "CREATE NODE TABLE Store(store_id INT64, store_name STRING, platform STRING, country STRING, PRIMARY KEY(store_id))",
              "CREATE NODE TABLE Listing(listing_id INT64, external_id STRING, PRIMARY KEY(listing_id))",
              "CREATE NODE TABLE SalesOrder(order_id INT64, order_date DATE, status STRING, PRIMARY KEY(order_id))",
              "CREATE REL TABLE CONTAINS(FROM SKU TO SKU, FROM SKU TO Model, FROM Model TO Component, FROM Component TO Component, qty INT64, valid_from DATE, valid_to DATE)",
              "CREATE REL TABLE OF_SKU(FROM Listing TO SKU)", "CREATE REL TABLE LISTED_IN(FROM Listing TO Store)",
              "CREATE REL TABLE PLACED_IN(FROM SalesOrder TO Store)",
              "CREATE REL TABLE HAS_LINE(FROM SalesOrder TO Listing, line_no INT64, qty INT64, unit_price DOUBLE)"]:
        k.execute(s)
    for t, f in [("Component", "component"), ("Model", "model"), ("SKU", "sku"), ("Store", "store"), ("Listing", "listing"),
                 ("SalesOrder", "orders"), ("OF_SKU", "of_sku"), ("LISTED_IN", "listed_in"), ("PLACED_IN", "placed_in"),
                 ("HAS_LINE", "has_line")]:
        k.execute(f"COPY {t} FROM '{os.path.join(CSV, f + '.csv')}' (HEADER=true)")
    for f, (a, b) in {"c_bundle": ("SKU", "SKU"), "c_skumodel": ("SKU", "Model"), "c_modelcomp": ("Model", "Component"),
                      "c_compcomp": ("Component", "Component")}.items():
        k.execute(f"COPY CONTAINS_{a}_{b} FROM '{os.path.join(CSV, f + '.csv')}' (HEADER=true)")
    print("CONTAINS", k.execute("MATCH ()-[r:CONTAINS]->() RETURN count(r)").get_next()[0])


if __name__ == "__main__":
    build_sql()
    build_kg()
    build_unified()
