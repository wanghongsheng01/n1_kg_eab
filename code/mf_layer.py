"""Public-standard semantic-layer substrate: dbt MetricFlow (Apache 2.0) over data/mf_<ds>.duckdb.

The projects live in code/metricflow/<ds>/ (dbt models + semantic_models / metrics YAML); build them with
  .tools/mf_venv/bin/python code/metricflow/build.py
MetricFlow runs in its own virtualenv (.tools/mf_venv), so this module (experiment venv) talks to a long-lived compile
server (code/metricflow/mf_server.py) through a pipe; compiled SQL is cached on disk by the canonical query text
(code/metricflow/<ds>/compile_cache.json), so repeated runs never start MetricFlow. SQL runs here with the experiment's
DuckDB on data/mf_<ds>.duckdb (read only).

  SPEC_MF[ds]          documentation shown to the LLM (rendered from the semantic manifest by build.py)
  spec(ds)             same, loaded lazily
  compile_mf(q, ds)    MetricFlow query JSON -> DuckDB SQL (through MetricFlowEngine.explain, i.e. `mf query --explain`)
  run(sql, ds)         execute on data/mf_<ds>.duckdb with a timeout (returns at most 1000 rows)

Query JSON = the arguments of `mf query`: {"metrics": [...], "group_by": [...], "where": [...], "order_by": [...], "limit": n}.
"""
import json, os, subprocess, threading

CODE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CODE)
MF_DIR = os.path.join(CODE, "metricflow")
MF_PY = os.path.join(ROOT, ".tools", "mf_venv", "bin", "python")
DATA = os.path.join(ROOT, "data")
DATASETS = ("xb", "aw", "lego")
TIMEOUT = 60


class MetricFlowError(Exception):
    pass


def spec(ds):
    with open(os.path.join(MF_DIR, ds, "spec_mf.txt")) as f:
        return f.read()


class _Spec(dict):
    def __missing__(self, ds):
        self[ds] = v = spec(ds)
        return v


SPEC_MF = _Spec()

_lock = threading.Lock()
_servers, _caches = {}, {}


def _cache_path(ds):
    return os.path.join(MF_DIR, ds, "compile_cache.json")


def _cache(ds):
    if ds not in _caches:
        p = _cache_path(ds)
        c = {}
        if os.path.exists(p):
            try:
                c = json.load(open(p))
            except ValueError:
                c = {}
        # a cache is valid only for the semantic manifest it was compiled against
        man = os.path.join(MF_DIR, ds, "target", "semantic_manifest.json")
        stamp = str(os.path.getmtime(man)) if os.path.exists(man) else ""
        if c.get("__manifest__") != stamp:
            c = {"__manifest__": stamp}
        _caches[ds] = c
    return _caches[ds]


def _server(ds):
    p = _servers.get(ds)
    if p is None or p.poll() is not None:
        p = subprocess.Popen([MF_PY, os.path.join(MF_DIR, "mf_server.py"), ds], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        while True:
            line = p.stdout.readline()
            if not line:
                raise MetricFlowError("MetricFlow compile server failed to start")
            if line.startswith("{") and json.loads(line).get("ready"):
                break
        _servers[ds] = p
    return p


def canonical(q):
    if isinstance(q, str):
        q = json.loads(q)
    return json.dumps(q, sort_keys=True, ensure_ascii=False)


def compile_mf(q, ds=None):
    """MetricFlow query (dict or JSON text) -> SQL. Raises MetricFlowError with MetricFlow's message on failure."""
    ds = ds or os.environ.get("N1_DATASET", "xb")
    key = canonical(q)
    with _lock:
        c = _cache(ds)
        if key in c:
            hit = c[key]
        else:
            p = _server(ds)
            p.stdin.write(json.dumps({"q": json.loads(key)}) + "\n")
            p.stdin.flush()
            line = p.stdout.readline()
            if not line:
                _servers.pop(ds, None)
                raise MetricFlowError("MetricFlow compile server exited")
            hit = json.loads(line)
            c[key] = hit
            tmp = _cache_path(ds) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(c, f)
            os.replace(tmp, _cache_path(ds))
    if "error" in hit:
        raise MetricFlowError(hit["error"][:1500])
    return hit["sql"]


_tls = threading.local()
_cons = {}


def run(sql, ds=None):
    import duckdb
    ds = ds or os.environ.get("N1_DATASET", "xb")
    cur = getattr(_tls, ds, None)
    if cur is None:
        with _lock:
            if ds not in _cons:
                _cons[ds] = duckdb.connect(os.path.join(DATA, f"mf_{ds}.duckdb"), read_only=True)
        cur = _cons[ds].cursor()
        cur.execute("SET max_temp_directory_size = '300MB'")
        setattr(_tls, ds, cur)
    t = threading.Timer(TIMEOUT, cur.interrupt)
    t.start()
    try:
        return cur.execute(sql).fetchmany(1000)
    finally:
        t.cancel()


def close():
    for p in _servers.values():
        try:
            p.stdin.close()
            p.wait(timeout=10)
        except Exception:
            p.kill()
    _servers.clear()
