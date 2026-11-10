#!/bin/bash
# Final round, second part (2026-11-09 night), requested by the third mock review:
#   fewshot <ds...>  SQL, SQL-U, Graph-T and Graph-U with three worked examples of other templates (build_fewshot.py)
#   fresh3           fresh question set on Graph-X (with and without the usage note), MetricFlow and Neo4j (typed, unified)
# Every step resumes from its output file; the budget was raised by the author.
cd "$(dirname "$0")"
source ./run_v2.sh none
export DAILY_CAP=${DAILY_CAP:-150} SHARED_CAP=${SHARED_CAP:-300}
case "$1" in
  fewshot) shift; for pass in 1 2; do for ds in "$@"; do
             W=${FW:-8} run $ds $DOM sql_fs,sql_u_fs,cypher_fs,cypher_u_fs 0 main_${ds}_fs.jsonl; done; done ;;
  fresh3)  qf () { echo $1_questions_fresh.jsonl; }; cf () { echo $1_s3_coverage_fresh.json; }
           for pass in 1 2; do for ds in xb aw lego; do
             run $ds $DOM cypher_x,cypher_x_tn,metricflow 0 main_${ds}_fresh3.jsonl
             neo $ds $DOM neo_t,neo_u 0 main_${ds}_fresh_neo.jsonl; done; done ;;
esac
echo "V4B-DONE $* $(date)" >> ../results/run_v4.log
