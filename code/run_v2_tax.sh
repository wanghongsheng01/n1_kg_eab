#!/bin/bash
# Error taxonomy after the graph reruns (DeepSeek-Flash labels) and a second labeler (Claude-Opus-5.5) on the same sample.
cd "$(dirname "$0")"
PY=${PY:-python}
for ds in xb lego; do
  F=main_${ds}.jsonl,main_${ds}_m2.jsonl,main_${ds}_g2.jsonl
  N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json N1_TAX_RUNS=$F N1_TAX_CAP=40 N1_TAX_MODEL=ds-flash N1_TAX_OUT=tax_main_${ds}.jsonl $PY -W ignore classify_errors.py
  N1_DATASET=$ds N1_QFILE=${ds}_questions.jsonl N1_COVFILE=${ds}_s3_coverage.json N1_TAX_RUNS=$F N1_TAX_CAP=40 N1_TAX_MODEL=claude-opus-5.5 N1_TAX_OUT=tax2_main_${ds}.jsonl $PY -W ignore classify_errors.py
done
echo TAX-DONE >> ../results/run_v2.log
