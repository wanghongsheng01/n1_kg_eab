#!/bin/bash
# Final round (2026-11-09 evening): completes the review-round contrasts.
#   think2  DS-V4-Pro and Kimi-K2.6 with thinking enabled on SQL, SQL-U, Graph-T, Graph-U and SQL-X (32,000 output tokens)
#   fresh2  fresh question set on Graph-T and Metric-R, and the agent, with the three main models
# The budget for these runs was raised by the author (DAILY_CAP/SHARED_CAP below); every step resumes from its output file.
cd "$(dirname "$0")"
source ./run_v2.sh none
export DAILY_CAP=${DAILY_CAP:-150} SHARED_CAP=${SHARED_CAP:-300} N1_HTTP_TIMEOUT=900
case "$1" in
  think2) m=$2; for pass in 1 2; do for ds in xb aw lego; do
            N1_MIN_MAX_TOKENS=32000 W=${TW:-8} run $ds $m sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_${m%-think}.jsonl; done; done ;;
  fresh2) qf () { echo $1_questions_fresh.jsonl; }; cf () { echo $1_s3_coverage_fresh.json; }
          for pass in 1 2; do for ds in xb aw lego; do
            run $ds $DOM cypher,classify 0 main_${ds}_fresh2.jsonl
            agent $ds $DOM 0 main_${ds}_fresh_agent.jsonl; done; done ;;
esac
echo "V4-DONE $1 $2 $(date)" >> ../results/run_v4.log
