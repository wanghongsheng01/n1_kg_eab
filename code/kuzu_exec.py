"""Execute one Cypher query in a separate process (Kuzu can crash on some generated queries).
Usage: python kuzu_exec.py <db_path> < query.cypher ; prints a JSON object {rows} or {error}."""
import json, sys
import kuzu

db, q = sys.argv[1], sys.stdin.read()
try:
    con = kuzu.Connection(kuzu.Database(db, read_only=True))
    con.set_query_timeout(60000)
    r = con.execute(q)
    rows = []
    while r.has_next() and len(rows) < 1000:
        rows.append(r.get_next())
    print(json.dumps({"rows": rows}, default=str))
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {str(e)[:600]}"}))
