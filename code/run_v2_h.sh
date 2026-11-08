#!/bin/bash
# Graph-T with the multi-type syntax both inside the dialect note and as a separate note at the end (cypher_h).
cd "$(dirname "$0")"
PY=${PY:-python}
qf () { [ $1 = xb ] && echo xb_questions.jsonl || echo $1_questions.jsonl; }
cf () { [ $1 = xb ] && echo xb_s3_coverage.json || echo $1_s3_coverage.json; }
for ds in xb aw lego; do
  N1_DATASET=$ds N1_QFILE=$(qf $ds) N1_COVFILE=$(cf $ds) $PY -W ignore run_pilot.py --models ds-flash,ds-pro,kimi-k2.6 --substrates cypher_h \
    --rep 0 --workers 4 --out main_${ds}_g2h.jsonl >> ../results/main_${ds}_g2h.log 2>&1
done
echo "H-DONE" >> ../results/run_v2.log
