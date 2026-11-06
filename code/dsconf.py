"""Dataset selection for the runners. Set N1_DATASET=xb|aw|lego (default xb) before importing run_pilot/run_agent.

For aw and lego, code/datasets/<ds>/ is put first on sys.path so that `import prompts` and `import semantic` load that
dataset's schema documents and semantic layer; the shared runner, scorer and LLM client stay in code/.
"""
import os, sys

DS = os.environ.get("N1_DATASET", "xb")
CODE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(CODE), "data")
if DS != "xb":
    sys.path.insert(0, os.path.join(CODE, "datasets", DS))
SQL_DB = os.path.join(DATA, f"{DS}.duckdb")
SQL_U_DB = os.path.join(DATA, f"{DS}_u.duckdb")
KG = os.path.join(DATA, f"{DS}.kuzu")
KG_U = os.path.join(DATA, f"{DS}_u.kuzu")
# xb keeps the frozen 50-question pilot as default; the main sets are <ds>_questions.jsonl
QFILE = os.path.join(DATA, os.environ.get("N1_QFILE", "questions.jsonl" if DS == "xb" else f"{DS}_questions.jsonl"))
COVFILE = os.path.join(DATA, os.environ.get("N1_COVFILE", "s3_coverage.json" if DS == "xb" else f"{DS}_s3_coverage.json"))


def task_table(P, SPEC):
    """The per-substrate (instruction, documentation) table; datasets other than xb only ship the documents."""
    if hasattr(P, "TASK"):
        return P.TASK
    q = "Write ONE {} query that answers the question. Return only the query in a ```{} block."
    return {
        "sql": (q.format("DuckDB SQL", "sql"), P.SQL_DOC),
        "sql_u": (q.format("DuckDB SQL", "sql"), P.SQL_U_DOC),
        "cypher": (q.format("Kuzu Cypher", "cypher"), P.CYPHER_DOC),
        "cypher_u": (q.format("Kuzu Cypher", "cypher"), P.CYPHER_U_DOC),
        "semantic": ("Write ONE semantic-layer JSON query that answers the question. Return only the JSON in a ```json block.", SPEC),
    }
