"""Cross-check LEGO gold (pure Python, gold.py) against hand-written semantic-layer oracle queries (S3, semantic.py),
and spot-check templates with hand-written SQL on data/lego_u.duckdb and Cypher on data/lego_u.kuzu.

Writes data/lego_s3_coverage.json ({qid: covered}). Exits non-zero unless every covered question agrees.
Usage: python check_gold.py
"""
import json, os, sys
import duckdb, kuzu

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(os.path.dirname(CODE), "data")
sys.path.insert(0, HERE)
sys.path.append(CODE)
from semantic import compile_query  # noqa: E402  (datasets/lego/semantic.py)
from score import match  # noqa: E402  (code/score.py, reused unchanged)


def oracle(q):
    a, tpl = q["args"], q["tpl"]
    S = lambda v=None: {"set_num": a["set_num"], **({"version": v} if v else {})}
    if tpl == "L1":
        return {"metric": "set_attr", "group_by": ["set.year"], "filters": [{"dim": "set.set_num", "value": a["set_num"]}]}
    if tpl == "L2":
        return {"metric": "inventory_versions", "filters": [{"dim": "set.set_num", "value": a["set_num"]}]}
    if tpl == "L3":
        return {"metric": "set_contents", "params": S(), "group_by": ["item.code"], "filters": [{"dim": "item.kind", "value": "set"}]}
    if tpl == "L4":
        return {"metric": "set_contents", "params": S(), "group_by": ["item.code"], "filters": [{"dim": "item.kind", "value": "minifig"}],
                "aggregate": "count"}
    if tpl in ("E1", "V1"):
        return {"metric": "part_quantity", "params": S(a.get("version")), "filters": [{"dim": "part.part_num", "value": a["part_num"]}]}
    if tpl == "E2":
        return {"metric": "part_quantity", "params": S(), "filters": [{"dim": "part.part_num", "value": a["part_num"]},
                                                                      {"dim": "color.name", "value": a["color"]}]}
    if tpl in ("E3", "V2", "V5"):
        return {"metric": "part_quantity", "params": S(a.get("version"))}
    if tpl in ("E4", "V3"):
        return {"metric": "part_quantity", "params": S(a.get("version")), "group_by": ["part.part_num"], "aggregate": "count"}
    if tpl == "E5":
        return {"metric": "part_quantity", "params": S(), "group_by": ["part.part_num", "color.name"], "aggregate": "count"}
    if tpl in ("E6", "V4"):
        return {"metric": "minifig_quantity", "params": S(a.get("version"))}
    if tpl == "E7":
        return {"metric": "part_quantity", "params": {**S(), "include_spares": True}}
    if tpl == "E8":
        return {"metric": "part_quantity", "params": S(), "filters": [{"dim": "part.category", "value": a["category"]}]}
    if tpl in ("W1", "W2", "W4", "W5"):
        p = {"part_num": a["part_num"], **({"color": a["color"]} if "color" in a else {})}
        flt = [{"dim": "set.year", "value": a["year"]}] if "year" in a else []
        return {"metric": "where_used", "params": p, "group_by": ["set.set_num"], "filters": flt,
                **({"aggregate": "count"} if tpl != "W5" else {})}
    if tpl == "W3":
        return {"metric": "where_used", "params": {"fig_num": a["fig_num"]}, "group_by": ["set.set_num"]}
    TY = [{"dim": "theme.rollup", "value": a.get("theme")}, {"dim": "set.year", "value": a.get("year")}]
    if tpl == "A1":
        return {"metric": "set_attr", "filters": TY}
    if tpl == "A2":
        return {"metric": "catalog_minifigs", "filters": TY}
    if tpl == "A3":
        return {"metric": "catalog_parts", "filters": TY}
    if tpl == "A4":
        return {"metric": "set_attr", "group_by": ["theme.root"], "filters": [{"dim": "set.year", "value": a["year"]}],
                "order_by": [{"field": "value", "desc": True}], "limit": 3}
    if tpl == "A5":
        return {"metric": "catalog_parts", "filters": [{"dim": "set.year", "value": a["year"]}, {"dim": "part.part_num", "value": a["part_num"]},
                                                       {"dim": "color.name", "value": a["color"]}]}
    if tpl == "A6":
        return {"metric": "catalog_parts", "group_by": ["color.name"], "filters": TY, "aggregate": "count"}
    return None   # X1-X4: deliberately outside S3 coverage


# ---------------- independent hand-written SQL over the unified table (lego_u.duckdb) ----------------
def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def sql_u_explode(set_num, version=None, kind="part", spares=False):
    top = f"u.version = {int(version)}" if version else "u.version = (SELECT max(version) FROM u_composition WHERE parent_num = u.parent_num)"
    sp = "" if spares else " AND NOT u.is_spare"
    return f"""WITH RECURSIVE cv AS (SELECT parent_kind, parent_num, max(version) AS v FROM u_composition GROUP BY 1, 2),
x(node, kind, mult, depth) AS (
  SELECT u.child_num, u.child_kind, u.qty, 1 FROM u_composition u
   WHERE u.parent_kind = 'set' AND u.parent_num = {_lit(set_num)} AND {top}{sp}
  UNION ALL
  SELECT u.child_num, u.child_kind, x.mult * u.qty, x.depth + 1 FROM x
    JOIN cv ON cv.parent_kind = x.kind AND cv.parent_num = x.node
    JOIN u_composition u ON u.parent_kind = x.kind AND u.parent_num = x.node AND u.version = cv.v{sp}
   WHERE x.kind <> 'part' AND x.depth < 10)
SELECT COALESCE(SUM(mult), 0) FROM x WHERE kind = '{kind}'"""


def sql_u(q):
    a, t = q["args"], q["tpl"]
    if t in ("E3", "V2", "V5"):
        return sql_u_explode(a["set_num"], a.get("version"))
    if t == "E7":
        return sql_u_explode(a["set_num"], spares=True)
    if t in ("E6", "V4"):
        return sql_u_explode(a["set_num"], a.get("version"), kind="minifig")
    if t == "A1":
        return f"""WITH RECURSIVE sub(id) AS (SELECT id FROM themes WHERE name = {_lit(a['theme'])}
  UNION SELECT t.id FROM themes t JOIN sub ON t.parent_id = sub.id)
SELECT count(*) FROM sets WHERE year = {int(a['year'])} AND theme_id IN (SELECT id FROM sub)"""
    if t == "W3":
        return f"""WITH RECURSIVE cv AS (SELECT parent_kind, parent_num, max(version) AS v FROM u_composition GROUP BY 1, 2),
x(root, node, kind, depth) AS (
  SELECT cv.parent_num, u.child_num, u.child_kind, 1 FROM cv JOIN u_composition u
    ON u.parent_kind = 'set' AND cv.parent_kind = 'set' AND u.parent_num = cv.parent_num AND u.version = cv.v
  UNION ALL
  SELECT x.root, u.child_num, u.child_kind, x.depth + 1 FROM x JOIN cv ON cv.parent_kind = x.kind AND cv.parent_num = x.node
    JOIN u_composition u ON u.parent_kind = x.kind AND u.parent_num = x.node AND u.version = cv.v
   WHERE x.kind = 'set' AND x.depth < 10)
SELECT DISTINCT root FROM x WHERE kind = 'minifig' AND node = {_lit(a['fig_num'])}"""
    return None


# ---------------- independent hand-written Cypher over the unified graph (lego_u.kuzu) ----------------
# "current version" of a node = no outgoing CONTAINS edge with a higher version (correlated NOT EXISTS);
# nesting depth in this snapshot is at most set->set->set->minifig->part, so four fixed-depth patterns are summed.
def _cur(n, r):
    return f"NOT EXISTS {{ MATCH ({n})-[z:CONTAINS]->() WHERE z.version > {r}.version }}"


def cypher_u(q):
    a, t = q["args"], q["tpl"]
    if t in ("E3", "V2", "V5", "E6", "V4"):
        target = "(p:Minifig)" if t in ("E6", "V4") else "(p:Part)"
        v = a.get("version")
        top = f"r0.version = {int(v)}" if v else _cur("r", "r0")
        out = []
        names = ["x1", "x2", "x3"]
        for d in range(1, 5):
            hops = [f"(r:Set {{set_num: {_lit(a['set_num'])}}})"]
            conds = [top]
            for i in range(d):
                nxt = target if i == d - 1 else f"({names[i]})"
                hops.append(f"-[r{i}:CONTAINS]->{nxt}")
                if i > 0:
                    conds.append(_cur(names[i - 1], f"r{i}"))
            if target == "(p:Part)":
                conds.append(f"NOT r{d - 1}.is_spare")
            prod = " * ".join(f"r{i}.qty" for i in range(d))
            out.append(f"MATCH {''.join(hops)} WHERE {' AND '.join(conds)} RETURN coalesce(sum({prod}), 0)")
        return out
    if t == "A1":
        return [f"MATCH (s:Set)-[:IN_THEME]->(:Theme)-[:SUBTHEME_OF*0..3]->(t:Theme {{name: {_lit(a['theme'])}}}) "
                f"WHERE s.year = {int(a['year'])} RETURN count(DISTINCT s)"]
    if t == "W3":
        f = _lit(a["fig_num"])
        return [f"MATCH (r:Set)-[r0:CONTAINS]->(m:Minifig {{fig_num: {f}}}) WHERE {_cur('r', 'r0')} RETURN DISTINCT r.set_num",
                f"MATCH (r:Set)-[r0:CONTAINS]->(x1:Set)-[r1:CONTAINS]->(m:Minifig {{fig_num: {f}}}) WHERE {_cur('r', 'r0')} AND {_cur('x1', 'r1')} RETURN DISTINCT r.set_num",
                f"MATCH (r:Set)-[r0:CONTAINS]->(x1:Set)-[r1:CONTAINS]->(x2:Set)-[r2:CONTAINS]->(m:Minifig {{fig_num: {f}}}) WHERE {_cur('r', 'r0')} AND {_cur('x1', 'r1')} AND {_cur('x2', 'r2')} RETURN DISTINCT r.set_num"]
    return None


def run_cypher(k, qs, tpl):
    rows = []
    for c in qs:
        r = k.execute(c)
        while r.has_next():
            rows.append(r.get_next())
    if tpl == "W3":
        return [[x] for x in sorted({x[0] for x in rows})]
    if len(qs) > 1:
        return [[sum(float(x[0]) for x in rows)]]
    return rows


if __name__ == "__main__":
    qs = [json.loads(l) for l in open(os.path.join(DATA, "lego_questions.jsonl"))]
    con = duckdb.connect(os.path.join(DATA, "lego.duckdb"), read_only=True)
    ok = cov = 0
    out, bad = {}, []
    for q in qs:
        o = oracle(q)
        assert (o is not None) == q["s3_covered"], q["qid"]
        out[q["qid"]] = o is not None
        if o is None:
            continue
        cov += 1
        rows = con.execute(compile_query(o)).fetchall()
        m = match(rows, q)
        ok += m
        if not m:
            bad.append(q["qid"])
            print("MISMATCH S3", q["qid"], q["tpl"], q["gold"], rows[:5])
    json.dump(out, open(os.path.join(DATA, "lego_s3_coverage.json"), "w"), indent=0)
    print(f"S3 oracle agrees with gold on {ok}/{cov} covered questions; coverage {cov}/{len(qs)} "
          f"({100.0 * cov / len(qs):.1f}%); out of coverage {len(qs) - cov}")

    ucon = duckdb.connect(os.path.join(DATA, "lego_u.duckdb"), read_only=True)
    k = kuzu.Connection(kuzu.Database(os.path.join(DATA, "lego_u.kuzu"), read_only=True))
    for name, fn, run in (("SQL on lego_u.duckdb", sql_u, lambda s, q: ucon.execute(s).fetchall()),
                          ("Cypher on lego_u.kuzu", cypher_u, lambda s, q: run_cypher(k, s, q["tpl"]))):
        n = good = 0
        tpls = set()
        for q in qs:
            s = fn(q)
            if s is None:
                continue
            n += 1
            tpls.add(q["tpl"])
            rows = run(s, q)
            m = match(rows, q)
            good += m
            if not m:
                bad.append(q["qid"])
                print("MISMATCH", name, q["qid"], q["tpl"], q["gold"], rows[:5])
        print(f"{name}: agrees with gold on {good}/{n} questions (templates {sorted(tpls)})")
    sys.exit(1 if bad else 0)
