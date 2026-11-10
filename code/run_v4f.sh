#!/bin/bash
# Final round: truncation rule (retry_truncated.py, 8,000 output tokens) on the final-round files with thinking disabled,
# after every driver has finished, then apply_retries.py. Kimi-K2.6 runs are not re-run: its vendor account ran out and
# the gateway is not equivalent (channel_check.py), so its truncated final-round runs keep their first result.
# Needs LLMHUB_BASE_URL / LLMHUB_API_KEY in the environment (never stored on disk).
cd "$(dirname "$0")"
PY=${PY:-python}; N1_RETRY_SKIP_MODELS=${N1_RETRY_SKIP_MODELS:-kimi-k2.6}; export PY N1_RETRY_SKIP_MODELS DAILY_CAP=${DAILY_CAP:-150} SHARED_CAP=${SHARED_CAP:-300}
F=""; for ds in xb aw lego; do F="$F main_${ds}_fs.jsonl main_${ds}_fresh2.jsonl main_${ds}_fresh3.jsonl main_${ds}_fresh_agent.jsonl"; done
for pass in 1 2; do
  ./run_v2_retry.sh $F
  for v in strict unified; do N1_AGENT_VARIANT=$v ./run_v2_retry.sh main_xb_agent_$v.jsonl main_aw_agent_$v.jsonl main_lego_agent_$v.jsonl; done
  for ds in xb aw lego; do
    $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u; sleep 20
    ./run_v2_retry.sh main_${ds}_fresh_neo.jsonl main_${ds}_fr_neo.jsonl
    $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u
  done
done
$PY apply_retries.py
echo "V4F-DONE $(date)" >> ../results/run_v4.log
