#!/bin/bash
# Review round: DS-Flash with thinking exhausts its 8,000-token budget in most LEGO graph runs without emitting a query.
# Those runs (and the few truncated XB/AW runs) are re-run once with 32,000 output tokens per call into results/retry_32k/;
# analyze_main.py reports the thinking contrast under both budgets. Runs over the daily cap are resumed after the reset.
cd "$(dirname "$0")"
PY=${PY:-python}
go () {
  for ds in xb aw lego; do
    N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json N1_MIN_MAX_TOKENS=32000 \
      N1_RETRY_DIR=retry_32k N1_RETRY_TAG=truncated32k N1_HTTP_TIMEOUT=900 $PY -W ignore retry_truncated.py main_${ds}_think.jsonl >> ../results/retry_32k.log 2>&1
  done
}
go
while [ "$(date +%Y-%m-%d)" = "2026-11-09" ]; do sleep 120; done
go
echo THINK32K-DONE >> ../results/retry_32k.log
