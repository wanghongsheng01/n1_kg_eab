"""Build the MetricFlow substrate of one or more datasets (run with .tools/mf_venv/bin/python).

  1. regenerate the dbt projects (gen_projects.py)
  2. copy data/<ds>_x.duckdb -> data/mf_<ds>.duckdb (the experiment's database is never touched)
  3. `dbt run` materialises the dbt models (dimension tables and facts, schema mf) into the copy
  4. `dbt parse` writes target/semantic_manifest.json (validated by MetricFlow on load)
  5. writes code/metricflow/<ds>/spec_mf.txt (the documentation shown to the LLM) from the loaded semantic manifest

Usage: .tools/mf_venv/bin/python code/metricflow/build.py [xb aw lego]
"""
import json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "data")
BIN = os.path.dirname(sys.executable)
sys.path.insert(0, HERE)
import gen_projects  # noqa: E402


def dbt(ds, *args):
    pdir = os.path.join(HERE, ds)
    env = dict(os.environ, DBT_PROFILES_DIR=pdir, MF_TARGET="build", DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    p = subprocess.run([os.path.join(BIN, "dbt"), *args, "--project-dir", pdir], env=env, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-6000:], p.stderr[-3000:])
        raise SystemExit(f"dbt {' '.join(args)} failed for {ds}")
    return p.stdout


def build(ds):
    t0 = time.time()
    gen_projects.write(ds)
    src, dst = os.path.join(DATA, f"{ds}_x.duckdb"), os.path.join(DATA, f"mf_{ds}.duckdb")
    for x in (dst, dst + ".wal"):
        if os.path.exists(x):
            os.remove(x)
    shutil.copyfile(src, dst)
    out = dbt(ds, "run")
    print(out.strip().splitlines()[-2:])
    dbt(ds, "parse")
    import mf_server
    eng = mf_server.engine(ds)
    spec = mf_server.render_spec(ds, eng)
    with open(os.path.join(HERE, ds, "spec_mf.txt"), "w") as f:
        f.write(spec)
    rep = dict(db=os.path.relpath(dst, ROOT), file_bytes=os.path.getsize(dst), build_s=round(time.time() - t0, 1),
               n_metrics=len(eng.list_metrics()), spec_chars=len(spec))
    print(ds, rep)
    return rep


if __name__ == "__main__":
    dss = sys.argv[1:] or ["xb", "aw", "lego"]
    path = os.path.join(HERE, "build_report.json")
    allrep = json.load(open(path)) if os.path.exists(path) else {}
    for ds in dss:
        allrep[ds] = build(ds)
    from importlib.metadata import version
    allrep["versions"] = {k: version(k) for k in ("dbt-metricflow", "metricflow", "dbt-semantic-interfaces",
                                                  "dbt-core", "dbt-duckdb", "duckdb")}
    allrep["versions"]["python"] = sys.version.split()[0]
    json.dump(allrep, open(path, "w"), indent=1, sort_keys=True)
