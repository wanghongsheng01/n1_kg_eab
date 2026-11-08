#!/bin/bash
# Materialized total-requirements relation (SQL-X, Graph-X): all six models, rep 0.
cd "$(dirname "$0")"
PY=${PY:-python}
qf () { [ $1 = xb ] && echo xb_questions.jsonl || echo $1_questions.jsonl; }
cf () { [ $1 = xb ] && echo xb_s3_coverage.json || echo $1_s3_coverage.json; }
for ds in xb aw lego; do
  N1_DATASET=$ds N1_QFILE=$(qf $ds) N1_COVFILE=$(cf $ds) $PY -W ignore run_pilot.py --models "$1" --substrates sql_x,cypher_x \
    --rep 0 --workers ${W:-5} --out main_${ds}_x$2.jsonl >> ../results/main_${ds}_x$2.log 2>&1
done
echo "X-DONE $1" >> ../results/run_v2.log
