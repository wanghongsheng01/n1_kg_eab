#!/bin/bash
# Truncation rule (retry_truncated.py): re-run once, with an 8,000-token output cap, every run that hit its output cap.
# Usage: run_v2_retry.sh <main_*.jsonl> ...  (resumable; runs over the daily cap fail and are picked up by the next call)
cd "$(dirname "$0")"
PY=${PY:-python}
for f in "$@"; do
  ds=$(echo $f | sed -E 's/^main_([a-z]+).*/\1/')
  qf=${ds}_questions.jsonl; [[ $f == *_para* ]] && qf=${ds}_questions_para.jsonl
  cf=${ds}_s3_coverage.json; [[ $f == *_fresh* ]] && { qf=${ds}_questions_fresh.jsonl; cf=${ds}_s3_coverage_fresh.json; }
  N1_DATASET=$ds N1_QFILE=$qf N1_COVFILE=$cf N1_MIN_MAX_TOKENS=8000 \
    $PY -W ignore retry_truncated.py $f >> ../results/retry.log 2>&1
done
echo "RETRY-BATCH-DONE $*" >> ../results/retry.log
