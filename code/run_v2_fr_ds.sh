#!/bin/bash
# Further models on one dataset (parallel helper for run_v2.sh frontier; duplicate calls are cache hits).
cd "$(dirname "$0")"
PY=${PY:-python}
ds=$1; FR=gpt-6-sol,claude-opus-5.5,qwen3.8-27b
N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json $PY -W ignore run_pilot.py --models $FR --substrates sql,sql_u,cypher,cypher_u,semantic,classify --rep 0 --workers ${W:-4} --out main_${ds}_fr.jsonl >> ../results/main_${ds}_fr.log 2>&1
N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json $PY -W ignore run_agent.py --models $FR --rep 0 --workers ${W:-4} --out main_${ds}_fr_agent.jsonl >> ../results/main_${ds}_fr_agent.log 2>&1
echo "FR-DS-DONE $ds" >> ../results/run_v2.log
