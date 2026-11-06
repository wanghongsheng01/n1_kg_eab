"""Build the four LEGO (Rebrickable) substrates for the N1 benchmark.

Source : data/lego_src.duckdb, built by code/make_sources.py (sha256 printed and
         written to data/lego_src.sha256).
Scope  : every set released in year >= 2010, plus everything those sets reach recursively through ANY inventory version
         (nested sets, their inventories, minifigs, parts). Inventory records with no content rows (no parts, no nested
         sets, no minifigs) are dropped; they carry no composition fact and would otherwise make the "current version"
         differ between the inventory-node model and the unified-edge model. All themes, colours and part categories kept.

S1  data/lego.duckdb    Rebrickable relational split (sets, themes, inventories, inventory_parts, inventory_sets,
                        inventory_minifigs, minifigs, parts, part_categories, colors).
S1u data/lego_u.duckdb  inventories + inventory_* replaced by one edge table u_composition.
S2  data/lego.kuzu      typed property graph; Inventory is a node (faithful ERP-like migration).
S2u data/lego_u.kuzu    no Inventory node; one CONTAINS rel-table group with a version property.

Usage: python build.py
"""
import hashlib, os, shutil, time
import duckdb, kuzu

CODE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(os.path.dirname(CODE), "data")
ORIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'data', 'lego_src.duckdb')  # built by code/make_sources.py
SRC = os.path.join(DATA, "lego_src.duckdb")
SQL_DB = os.path.join(DATA, "lego.duckdb")
SQL_U = os.path.join(DATA, "lego_u.duckdb")
KG_DB = os.path.join(DATA, "lego.kuzu")
KG_U = os.path.join(DATA, "lego_u.kuzu")
STAGE = os.path.join(DATA, "lego_kg_parquet")
MIN_YEAR = 2010


def _rm(p):
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.exists(p):
        os.remove(p)
    for ext in (".wal", ".lock"):
        if os.path.exists(p + ext):
            os.remove(p + ext)


def copy_source():
    if not os.path.exists(SRC):
        shutil.copyfile(ORIG, SRC)
    h = hashlib.sha256(open(SRC, "rb").read()).hexdigest()
    open(SRC.replace(".duckdb", ".sha256"), "w").write(f"{h}  lego_src.duckdb\n")
    print("lego_src.duckdb sha256", h)


def build_sql():
    _rm(SQL_DB)
    con = duckdb.connect(SQL_DB)
    con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
    # non-empty inventories only
    con.execute("""CREATE TEMP TABLE inv AS SELECT i.* FROM src.main.inventories i
      WHERE EXISTS (SELECT 1 FROM src.main.inventory_parts p WHERE p.inventory_id = i.id)
         OR EXISTS (SELECT 1 FROM src.main.inventory_sets p WHERE p.inventory_id = i.id)
         OR EXISTS (SELECT 1 FROM src.main.inventory_minifigs p WHERE p.inventory_id = i.id)""")
    # recursive closure of in-scope set_nums through any inventory version
    con.execute(f"""CREATE TEMP TABLE scope_sets AS
      WITH RECURSIVE r(set_num) AS (
        SELECT set_num FROM src.main.sets WHERE year >= {MIN_YEAR}
        UNION
        SELECT s.set_num FROM r JOIN inv i ON i.set_num = r.set_num JOIN src.main.inventory_sets s ON s.inventory_id = i.id)
      SELECT DISTINCT set_num FROM r""")
    con.execute("""CREATE TEMP TABLE scope_figs AS SELECT DISTINCT m.fig_num FROM scope_sets ss
      JOIN inv i ON i.set_num = ss.set_num JOIN src.main.inventory_minifigs m ON m.inventory_id = i.id""")
    con.execute("""CREATE TEMP TABLE scope_inv AS SELECT i.* FROM inv i
      WHERE i.set_num IN (SELECT set_num FROM scope_sets) OR i.set_num IN (SELECT fig_num FROM scope_figs)""")
    con.execute("CREATE TABLE themes AS SELECT * FROM src.main.themes ORDER BY id")
    con.execute("CREATE TABLE colors AS SELECT * FROM src.main.colors ORDER BY id")
    con.execute("CREATE TABLE part_categories AS SELECT * FROM src.main.part_categories ORDER BY id")
    con.execute("CREATE TABLE sets AS SELECT * FROM src.main.sets WHERE set_num IN (SELECT set_num FROM scope_sets) ORDER BY set_num")
    con.execute("CREATE TABLE minifigs AS SELECT * FROM src.main.minifigs WHERE fig_num IN (SELECT fig_num FROM scope_figs) ORDER BY fig_num")
    con.execute("CREATE TABLE inventories AS SELECT id, version, set_num FROM scope_inv ORDER BY id")
    con.execute("""CREATE TABLE inventory_parts AS SELECT p.* FROM src.main.inventory_parts p
      WHERE p.inventory_id IN (SELECT id FROM scope_inv) ORDER BY inventory_id, part_num, color_id, is_spare""")
    con.execute("""CREATE TABLE inventory_sets AS SELECT p.* FROM src.main.inventory_sets p
      WHERE p.inventory_id IN (SELECT id FROM scope_inv) ORDER BY inventory_id, set_num""")
    con.execute("""CREATE TABLE inventory_minifigs AS SELECT p.* FROM src.main.inventory_minifigs p
      WHERE p.inventory_id IN (SELECT id FROM scope_inv) ORDER BY inventory_id, fig_num""")
    con.execute("""CREATE TABLE parts AS SELECT * FROM src.main.parts
      WHERE part_num IN (SELECT DISTINCT part_num FROM inventory_parts) ORDER BY part_num""")
    for t in ("sets", "themes", "inventories", "inventory_parts", "inventory_sets", "inventory_minifigs", "minifigs",
              "parts", "part_categories", "colors"):
        print(f"  {t:20s}", con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    # integrity: every reference resolves inside the snapshot
    chk = {
        "inventory set_num resolves": "SELECT count(*) FROM inventories WHERE set_num NOT IN (SELECT set_num FROM sets) AND set_num NOT IN (SELECT fig_num FROM minifigs)",
        "nested set has row in sets": "SELECT count(*) FROM inventory_sets WHERE set_num NOT IN (SELECT set_num FROM sets)",
        "minifig has row": "SELECT count(*) FROM inventory_minifigs WHERE fig_num NOT IN (SELECT fig_num FROM minifigs)",
        "minifig has exactly one inventory": "SELECT count(*) FROM (SELECT set_num FROM inventories WHERE set_num LIKE 'fig-%' GROUP BY 1 HAVING count(*) > 1)",
    }
    for k, q in chk.items():
        n = con.execute(q).fetchone()[0]
        assert n == 0, (k, n)
    con.close()


U_SQL = """SELECT 'set' AS parent_kind, i.set_num AS parent_num, i.version, 'set' AS child_kind, s.set_num AS child_num,
       CAST(NULL AS BIGINT) AS color_id, s.quantity AS qty, FALSE AS is_spare
  FROM {p}inventory_sets s JOIN {p}inventories i ON i.id = s.inventory_id
UNION ALL
SELECT 'set', i.set_num, i.version, 'minifig', m.fig_num, NULL, m.quantity, FALSE
  FROM {p}inventory_minifigs m JOIN {p}inventories i ON i.id = m.inventory_id
UNION ALL
SELECT CASE WHEN i.set_num LIKE 'fig-%' THEN 'minifig' ELSE 'set' END, i.set_num, i.version, 'part', p.part_num,
       p.color_id, p.quantity, p.is_spare
  FROM {p}inventory_parts p JOIN {p}inventories i ON i.id = p.inventory_id"""


def build_sql_u():
    _rm(SQL_U)
    con = duckdb.connect(SQL_U)
    con.execute(f"ATTACH '{SQL_DB}' AS s (READ_ONLY)")
    for t in ("sets", "themes", "minifigs", "parts", "part_categories", "colors"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM s.main.{t}")
    con.execute(f"CREATE TABLE u_composition AS {U_SQL.format(p='s.main.')} ORDER BY 1, 2, 3, 4, 5, 6, 8")
    print("  u_composition", con.execute("SELECT count(*) FROM u_composition").fetchone()[0])
    # the unified rule "current = highest version among the parent's edges" must equal max(inventories.version)
    bad = con.execute("""SELECT count(*) FROM (SELECT set_num, max(version) v FROM s.main.inventories GROUP BY 1) a
      FULL JOIN (SELECT parent_num, max(version) v FROM u_composition GROUP BY 1) b ON a.set_num = b.parent_num
      WHERE a.v IS DISTINCT FROM b.v""").fetchone()[0]
    assert bad == 0, bad
    con.close()


def _export(con, name, q):
    con.execute(f"COPY ({q}) TO '{os.path.join(STAGE, name + '.parquet')}' (FORMAT PARQUET)")
    return os.path.join(STAGE, name + ".parquet")


NODE_DDL = [
    "CREATE NODE TABLE Set(set_num STRING, name STRING, year INT64, num_parts INT64, PRIMARY KEY(set_num))",
    "CREATE NODE TABLE Theme(id INT64, name STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE Minifig(fig_num STRING, name STRING, num_parts INT64, PRIMARY KEY(fig_num))",
    "CREATE NODE TABLE Part(part_num STRING, name STRING, PRIMARY KEY(part_num))",
    "CREATE NODE TABLE PartCategory(id INT64, name STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE Color(id INT64, name STRING, PRIMARY KEY(id))",
]
COMMON_REL_DDL = [
    "CREATE REL TABLE IN_THEME(FROM Set TO Theme)",
    "CREATE REL TABLE SUBTHEME_OF(FROM Theme TO Theme)",
    "CREATE REL TABLE IN_CATEGORY(FROM Part TO PartCategory)",
]
NODE_Q = {
    "Set": "SELECT set_num, name, year, num_parts FROM sets",
    "Theme": "SELECT id, name FROM themes",
    "Minifig": "SELECT fig_num, name, num_parts FROM minifigs",
    "Part": "SELECT part_num, name FROM parts",
    "PartCategory": "SELECT id, name FROM part_categories",
    "Color": "SELECT id, name FROM colors",
}
COMMON_REL_Q = {
    "IN_THEME": "SELECT set_num, theme_id FROM sets",
    "SUBTHEME_OF": "SELECT id, parent_id FROM themes WHERE parent_id IS NOT NULL",
    "IN_CATEGORY": "SELECT part_num, part_cat_id FROM parts",
}


def _copy(t, path):
    """COPY statement; a member of a rel-table group is named GROUP_From_To and loaded with (from=..., to=...)."""
    parts = t.split("_")
    if len(parts) >= 3 and parts[-1][0].isupper() and parts[-1][1:].islower() and parts[-2][0].isupper() and parts[-2][1:].islower():
        return f"COPY {'_'.join(parts[:-2])} FROM '{path}' (from='{parts[-2]}', to='{parts[-1]}')"
    return f"COPY {t} FROM '{path}'"


def _count(k, label, q):
    print(f"  {label:22s}", k.execute(q).get_next()[0])


def build_kg():
    _rm(KG_DB)
    os.makedirs(STAGE, exist_ok=True)
    con = duckdb.connect(SQL_DB, read_only=True)
    files = {t: _export(con, t, q) for t, q in {**NODE_Q, **COMMON_REL_Q}.items()}
    files["Inventory"] = _export(con, "Inventory", "SELECT id, version FROM inventories")
    files["HAS_INVENTORY_Set_Inventory"] = _export(con, "inv_set", "SELECT set_num, id FROM inventories WHERE set_num NOT LIKE 'fig-%'")
    files["HAS_INVENTORY_Minifig_Inventory"] = _export(con, "inv_fig", "SELECT set_num, id FROM inventories WHERE set_num LIKE 'fig-%'")
    files["CONTAINS_SET"] = _export(con, "contains_set", "SELECT inventory_id, set_num, quantity AS qty FROM inventory_sets")
    files["CONTAINS_MINIFIG"] = _export(con, "contains_minifig", "SELECT inventory_id, fig_num, quantity AS qty FROM inventory_minifigs")
    files["HAS_PART"] = _export(con, "has_part", "SELECT inventory_id, part_num, color_id, quantity AS qty, is_spare FROM inventory_parts")
    con.close()
    k = kuzu.Connection(kuzu.Database(KG_DB))
    for s in NODE_DDL + ["CREATE NODE TABLE Inventory(id INT64, version INT64, PRIMARY KEY(id))"] + COMMON_REL_DDL + [
            "CREATE REL TABLE HAS_INVENTORY(FROM Set TO Inventory, FROM Minifig TO Inventory)",
            "CREATE REL TABLE CONTAINS_SET(FROM Inventory TO Set, qty INT64)",
            "CREATE REL TABLE CONTAINS_MINIFIG(FROM Inventory TO Minifig, qty INT64)",
            "CREATE REL TABLE HAS_PART(FROM Inventory TO Part, color_id INT64, qty INT64, is_spare BOOLEAN)"]:
        k.execute(s)
    for t in list(NODE_Q) + ["Inventory"] + list(COMMON_REL_Q) + ["HAS_INVENTORY_Set_Inventory", "HAS_INVENTORY_Minifig_Inventory",
                                                                 "CONTAINS_SET", "CONTAINS_MINIFIG", "HAS_PART"]:
        k.execute(_copy(t, files[t]))
    for t in list(NODE_Q) + ["Inventory"]:
        _count(k, t, f"MATCH (n:{t}) RETURN count(n)")
    for t in list(COMMON_REL_Q) + ["HAS_INVENTORY", "CONTAINS_SET", "CONTAINS_MINIFIG", "HAS_PART"]:
        _count(k, t, f"MATCH ()-[r:{t}]->() RETURN count(r)")


def build_kg_u():
    _rm(KG_U)
    os.makedirs(STAGE, exist_ok=True)
    con = duckdb.connect(SQL_U, read_only=True)
    files = {t: _export(con, "u_" + t, q) for t, q in {**NODE_Q, **COMMON_REL_Q}.items()}
    cols = "parent_num, child_num, version, qty, color_id, is_spare"
    for pk, ck, a, b in [("set", "set", "Set", "Set"), ("set", "minifig", "Set", "Minifig"), ("set", "part", "Set", "Part"),
                         ("minifig", "part", "Minifig", "Part")]:
        files[f"CONTAINS_{a}_{b}"] = _export(con, f"u_c_{pk}_{ck}",
                                             f"SELECT {cols} FROM u_composition WHERE parent_kind = '{pk}' AND child_kind = '{ck}'")
    con.close()
    k = kuzu.Connection(kuzu.Database(KG_U))
    for s in NODE_DDL + COMMON_REL_DDL + [
            "CREATE REL TABLE CONTAINS(FROM Set TO Set, FROM Set TO Minifig, FROM Set TO Part, FROM Minifig TO Part, "
            "version INT64, qty INT64, color_id INT64, is_spare BOOLEAN)"]:
        k.execute(s)
    for t in list(NODE_Q) + list(COMMON_REL_Q) + ["CONTAINS_Set_Set", "CONTAINS_Set_Minifig", "CONTAINS_Set_Part", "CONTAINS_Minifig_Part"]:
        k.execute(_copy(t, files[t]))
    for t in list(NODE_Q):
        _count(k, t, f"MATCH (n:{t}) RETURN count(n)")
    _count(k, "CONTAINS", "MATCH ()-[r:CONTAINS]->() RETURN count(r)")


def _size(p):
    if os.path.isdir(p):
        return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)
    return os.path.getsize(p)


if __name__ == "__main__":
    copy_source()
    for name, fn, path in [("lego.duckdb", build_sql, SQL_DB), ("lego_u.duckdb", build_sql_u, SQL_U),
                           ("lego.kuzu", build_kg, KG_DB), ("lego_u.kuzu", build_kg_u, KG_U)]:
        t0 = time.time()
        print(f"[{name}]")
        fn()
        print(f"  built in {time.time() - t0:.1f}s, size {_size(path) / 1e6:.1f} MB")
    shutil.rmtree(STAGE, ignore_errors=True)
