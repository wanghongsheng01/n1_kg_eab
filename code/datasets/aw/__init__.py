"""AdventureWorks (AW) dataset package for the N1 benchmark.

Paths of the built artefacts (see build.py). A runner can import this package as `datasets.aw` from code/ and use
`prompts` (SQL_DOC, SQL_U_DOC, CYPHER_DOC, CYPHER_U_DOC, TASK, messages, repair) and `semantic` (SPEC, compile_query).
"""
import os

CODE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(os.path.dirname(CODE), "data")
SRC_DB = os.path.join(DATA, "aw_src.duckdb")
SQL_DB = os.path.join(DATA, "aw.duckdb")
SQL_U_DB = os.path.join(DATA, "aw_u.duckdb")
KG_DB = os.path.join(DATA, "aw.kuzu")
KG_U_DB = os.path.join(DATA, "aw_u.kuzu")
QUESTIONS = os.path.join(DATA, "aw_questions.jsonl")
COVERAGE = os.path.join(DATA, "aw_s3_coverage.json")
MAIN_QUESTIONS = QUESTIONS
if os.environ.get("N1_FRESH") == "1":  # fresh replication set (review round)
    QUESTIONS = os.path.join(DATA, "aw_questions_fresh.jsonl")
    COVERAGE = os.path.join(DATA, "aw_s3_coverage_fresh.json")
REF_DATE = "2025-06-30"   # "current" in every substrate document
