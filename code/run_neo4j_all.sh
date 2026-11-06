#!/bin/bash
# Neo4j replication: per dataset start its two instances, run neo_t/neo_u, stop them.
# Phase 1: DeepSeek-Flash now; phase 2: DeepSeek-V4-Pro and Kimi after the daily budget resets.
cd "$(dirname "$0")"
PY=${PY:-python}
run_phase () {
  for ds in xb aw lego; do
    until [ -f ../.tools/neo4j_vars/${ds}_u/data/databases/neo4j/neostore ] || [ -d ../.tools/neo4j_vars/${ds}_u/data/databases/neo4j ]; do sleep 30; done
    $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u
    QF=$([ $ds = xb ] && echo xb_questions.jsonl || echo ${ds}_questions.jsonl)
    CF=$([ $ds = xb ] && echo xb_s3_coverage.json || echo ${ds}_s3_coverage.json)
    N1_DATASET=$ds N1_QFILE=$QF N1_COVFILE=$CF DAILY_CAP=15 $PY run_pilot.py --models "$1" --substrates neo_t,neo_u --rep 0 --workers 5 --out main_${ds}_neo.jsonl >> ../results/main_${ds}_neo.log 2>&1
    $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u
  done
}
run_phase ds-flash
while [ "$(date +%Y-%m-%d)" = "2026-11-05" ]; do sleep 60; done
sleep 600
run_phase ds-pro,kimi-k2.6
echo NEO4J-ALL-DONE >> ../results/neo4j_driver.log
