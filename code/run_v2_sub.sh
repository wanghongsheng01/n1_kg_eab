#!/bin/bash
# Generic: run_v2_sub.sh <models> <substrates> <out-suffix> [rep]
cd "$(dirname "$0")"
PY=${PY:-python}
qf () { [ $1 = xb ] && echo xb_questions.jsonl || echo $1_questions.jsonl; }
cf () { [ $1 = xb ] && echo xb_s3_coverage.json || echo $1_s3_coverage.json; }
for ds in xb aw lego; do
  N1_DATASET=$ds N1_QFILE=$(qf $ds) N1_COVFILE=$(cf $ds) $PY -W ignore run_pilot.py --models "$1" --substrates "$2" \
    --rep ${4:-0} --workers ${W:-5} --out main_${ds}_$3.jsonl >> ../results/main_${ds}_$3.log 2>&1
done
echo "SUB-DONE $1 $2 $3" >> ../results/run_v2.log
