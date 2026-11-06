"""Second graph engine: copy a Kuzu graph into its own Neo4j 5 instance with identical labels, relationship types and
property names/types, so that the Cypher documentation differs only in the dialect note.

Usage: python neo4j_build.py <kuzu_path> <variant_name> <port_offset>
  -> .tools/neo4j_vars/<variant>/{conf,data,logs,run,import}; HTTP on 7474+offset, Bolt on 7687+offset.
Start/stop: python neo4j_build.py start|stop <variant_name>
"""
import csv, os, shutil, subprocess, sys, time
import kuzu

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(HERE, ".tools")
NEO = os.path.join(TOOLS, "neo4j")
JAVA_HOME = os.path.join(TOOLS, "jdk", "Contents", "Home")
TYPES = {"INT64": "long", "INT32": "int", "INT16": "int", "INT8": "int", "DOUBLE": "double", "FLOAT": "float",
         "STRING": "string", "BOOL": "boolean", "DATE": "date", "DECIMAL": "double"}


def rows(con, q):
    r = con.execute(q)
    out = []
    while r.has_next():
        out.append(r.get_next())
    return out


def vdir(name):
    return os.path.join(TOOLS, "neo4j_vars", name)


def env(name):
    return {**os.environ, "JAVA_HOME": JAVA_HOME, "NEO4J_CONF": os.path.join(vdir(name), "conf"),
            "NEO4J_HOME": NEO, "PATH": os.path.join(JAVA_HOME, "bin") + ":" + os.environ["PATH"]}


def ntype(t):
    return TYPES.get(t.split("(")[0], "string")


def export(kpath, name):
    con = kuzu.Connection(kuzu.Database(kpath, read_only=True))
    imp = os.path.join(vdir(name), "import")
    shutil.rmtree(imp, ignore_errors=True)
    os.makedirs(imp)
    nodes, rels = [], []
    tables = rows(con, "CALL show_tables() RETURN *")
    pk = {}
    for _, t, kind, *_ in tables:
        if kind != "NODE":
            continue
        info = rows(con, f"CALL table_info('{t}') RETURN *")
        props = [(i[1], i[2]) for i in info]
        pk[t] = next(i[1] for i in info if i[4])
        f = os.path.join(imp, f"n_{t}.csv")
        with open(f, "w", newline="") as fo:
            w = csv.writer(fo)
            w.writerow([f":ID({t})"] + [f"{p}:{ntype(ty)}" for p, ty in props])
            for r in rows(con, f"MATCH (n:{t}) RETURN " + ", ".join(f"n.{p}" for p, _ in props)):
                w.writerow([r[[p for p, _ in props].index(pk[t])]] + ["" if v is None else ("true" if v is True else "false" if v is False else v) for v in r])
        nodes.append((t, f))
    for _, t, kind, *_ in tables:
        if kind != "REL":
            continue
        info = rows(con, f"CALL table_info('{t}') RETURN *")
        props = [(i[1], i[2]) for i in info]
        for src, dst, *_ in rows(con, f"CALL show_connection('{t}') RETURN *"):
            f = os.path.join(imp, f"r_{t}_{src}_{dst}.csv")
            with open(f, "w", newline="") as fo:
                w = csv.writer(fo)
                w.writerow([f":START_ID({src})", f":END_ID({dst})"] + [f"{p}:{ntype(ty)}" for p, ty in props])
                q = f"MATCH (a:{src})-[r:{t}]->(b:{dst}) RETURN a.{pk[src]}, b.{pk[dst]}" + "".join(f", r.{p}" for p, _ in props)
                for r in rows(con, q):
                    w.writerow(["" if v is None else ("true" if v is True else "false" if v is False else v) for v in r])
            rels.append((t, f))
    return nodes, rels


def configure(name, offset):
    d = vdir(name)
    for sub in ("conf", "data", "logs", "run"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    override = ("server.directories.", "server.http.listen_address", "server.bolt.listen_address",
                "server.memory.heap.", "server.memory.pagecache.size")
    conf = "\n".join(l for l in open(os.path.join(NEO, "conf", "neo4j.conf")).read().splitlines()
                     if not l.strip().startswith(override))
    conf += f"""
server.directories.data={os.path.join(d, 'data')}
server.directories.logs={os.path.join(d, 'logs')}
server.directories.run={os.path.join(d, 'run')}
server.directories.import={os.path.join(d, 'import')}
server.http.listen_address=127.0.0.1:{7474 + offset}
server.bolt.listen_address=127.0.0.1:{7687 + offset}
server.memory.heap.initial_size=512m
server.memory.heap.max_size=1g
server.memory.pagecache.size=512m
"""
    last = {}   # keep only the last setting of every key (the base file may repeat keys)
    for l in conf.splitlines():
        if "=" in l and not l.strip().startswith("#"):
            last[l.split("=", 1)[0].strip()] = l.strip()
    open(os.path.join(d, "conf", "neo4j.conf"), "w").write("\n".join(last.values()) + "\n")
    open(os.path.join(d, "port"), "w").write(str(7474 + offset))


def build(kpath, name, offset):
    configure(name, offset)
    nodes, rels = export(kpath, name)
    cmd = [os.path.join(NEO, "bin", "neo4j-admin"), "database", "import", "full", "neo4j", "--overwrite-destination",
           "--skip-bad-relationships=false", "--bad-tolerance=0"]
    cmd += [f"--nodes={t}={f}" for t, f in nodes] + [f"--relationships={t}={f}" for t, f in rels]
    p = subprocess.run(cmd, env=env(name), capture_output=True, text=True)
    print(p.stdout[-1500:], p.stderr[-1500:])
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def start(name):
    p = subprocess.run([os.path.join(NEO, "bin", "neo4j"), "start"], env=env(name), capture_output=True, text=True)
    print(p.stdout[-500:], p.stderr[-500:])
    import requests
    port = open(os.path.join(vdir(name), "port")).read().strip()
    for _ in range(120):
        try:
            if requests.get(f"http://127.0.0.1:{port}/", timeout=2).status_code == 200:
                print("up", name, port)
                return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("neo4j did not start")


def stop(name):
    p = subprocess.run([os.path.join(NEO, "bin", "neo4j"), "stop"], env=env(name), capture_output=True, text=True)
    print(p.stdout[-300:], p.stderr[-300:])


if __name__ == "__main__":
    if sys.argv[1] in ("start", "stop"):
        (start if sys.argv[1] == "start" else stop)(sys.argv[2])
    else:
        build(sys.argv[1], sys.argv[2], int(sys.argv[3]))
