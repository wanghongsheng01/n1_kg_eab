#!/bin/bash
# Final round: a third labeler (DS-V4-Pro with thinking) on the same stratified failure sample as tax_main/tax2_main;
# analyze_main.py reports three-way agreement and the majority label.
cd "$(dirname "$0")"
PY=${PY:-python}
export DAILY_CAP=${DAILY_CAP:-150} SHARED_CAP=${SHARED_CAP:-300} N1_MIN_MAX_TOKENS=8000 N1_HTTP_TIMEOUT=900
for ds in xb aw lego; do
  F=main_${ds}.jsonl,main_${ds}_m2.jsonl,main_${ds}_g2.jsonl
  N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json N1_TAX_RUNS=$F N1_TAX_CAP=40 N1_TAX_MODEL=ds-pro-think N1_TAX_OUT=tax3_main_${ds}.jsonl $PY -W ignore classify_errors.py
done
echo TAX3-DONE >> ../results/run_v4.log
