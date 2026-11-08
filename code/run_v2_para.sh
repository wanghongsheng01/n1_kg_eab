#!/bin/bash
# Paraphrased questions (gpt-6-sol paraphrase, claude-opus-5.5 verified): run_v2_para.sh <models> <out-suffix>
cd "$(dirname "$0")"
PY=${PY:-python}
cf () { [ $1 = xb ] && echo xb_s3_coverage.json || echo $1_s3_coverage.json; }
for ds in xb aw lego; do
  N1_DATASET=$ds N1_QFILE=${ds}_questions_para.jsonl N1_COVFILE=$(cf $ds) $PY -W ignore run_pilot.py --models "$1" \
    --substrates sql,sql_u,cypher,cypher_u,semantic,sql_x,metricflow --rep 0 --workers ${W:-5} --out main_${ds}_$2.jsonl >> ../results/main_${ds}_$2.log 2>&1
done
echo "PARA-DONE $1" >> ../results/run_v2.log
