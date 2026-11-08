#!/bin/bash
# Review round: frozen fresh-seed replication (seed 20261108, instances of the main set excluded, qids prefixed F).
# SQL, SQL-U, Graph-U, Metric and SQL-X with the three main models. Starts after the review-round night runs; runs that
# fail at the daily cap are not recorded, so the pass is repeated after the next budget reset.
cd "$(dirname "$0")"
source ./run_v2.sh none
qf () { echo $1_questions_fresh.jsonl; }
cf () { echo $1_s3_coverage_fresh.json; }
until grep -q V3-NIGHT-DONE ../results/run_v3.log; do sleep 120; done
fresh () { for ds in xb aw lego; do run $ds $DOM sql,sql_u,cypher_u,semantic,sql_x 0 main_${ds}_fresh.jsonl; done; }
fresh
echo "FRESH-PASS1-DONE $(date)" >> ../results/run_v3.log
while [ "$(date +%Y-%m-%d)" = "2026-11-08" ]; do sleep 120; done
fresh
echo "FRESH-DONE $(date)" >> ../results/run_v3.log
