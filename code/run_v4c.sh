#!/bin/bash
# Final round, third part (2026-11-10): agent variants requested by the third mock review (strict routing rule; raw SQL
# over the unified tables), main models, main question set. Resumes from the output files.
cd "$(dirname "$0")"
source ./run_v2.sh none
export DAILY_CAP=${DAILY_CAP:-150} SHARED_CAP=${SHARED_CAP:-300}
for pass in 1 2; do for v in strict unified; do for ds in xb aw lego; do
  N1_AGENT_VARIANT=$v agent $ds $DOM 0 main_${ds}_agent_$v.jsonl; done; done; done
echo "V4C-DONE $(date)" >> ../results/run_v4.log
