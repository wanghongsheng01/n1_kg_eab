#!/bin/bash
# Final round, eighth part (2026-11-10 night): Claude-Opus-5.5 through a third-party provider (tokens1688), because the
# organisation's gateway reached its spending limit. (1) paired channel check on LEGO Neo4j (channel_check_t1688.py);
# (2) the 39 truncated gateway runs on LEGO Neo4j re-run with 8,000 tokens into results/retry_t1688/, merged by
# analyze_v4.py only if the check passes. Endpoint and key only from T1688_BASE_URL / T1688_API_KEY.
cd "$(dirname "$0")"
source ./run_v2.sh none
: "${T1688_API_KEY:?set T1688_API_KEY}"
echo 1 > ../results/.neo_hold_lego_t; echo 1 > ../results/.neo_hold_lego_u
$PY neo4j_build.py start lego_t; $PY neo4j_build.py start lego_u
export N1_DATASET=lego N1_QFILE=$(qf lego) N1_COVFILE=$(cf lego)
$PY -W ignore channel_check_t1688.py >> ../results/channel_check_t1688.log 2>&1
$PY channel_check_t1688.py analyze >> ../results/channel_check_t1688.log 2>&1
N1_MIN_MAX_TOKENS=8000 N1_RETRY_DIR=retry_t1688 N1_RETRY_VIA=claude-opus-5.5:claude-t1688 W=4 \
  $PY -W ignore retry_truncated.py main_lego_fr_neo.jsonl >> ../results/retry_t1688.log 2>&1
echo 0 > ../results/.neo_hold_lego_t; echo 0 > ../results/.neo_hold_lego_u
$PY neo4j_build.py stop lego_t; $PY neo4j_build.py stop lego_u
echo "V4H-DONE $(date)" >> ../results/run_v4.log
