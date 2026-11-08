#!/bin/bash
# Review round: truncation rule on the review-round files and on the runs whose retry failed at the daily cap on 11-08
# (main_<ds>_neo.jsonl is superseded by neo2 and not used).
cd "$(dirname "$0")"
PY=${PY:-python}
F="main_xb.jsonl main_aw.jsonl main_lego.jsonl main_aw_m2.jsonl main_lego_m2.jsonl main_xb_m2.jsonl
  main_aw_mf.jsonl main_lego_mf.jsonl main_xb_mf.jsonl main_lego_abl.jsonl main_xb_abl.jsonl main_lego_g2.jsonl main_xb_g2.jsonl
  main_lego_g2h.jsonl main_lego_x.jsonl main_aw_r1.jsonl main_lego_r1.jsonl main_xb_r1.jsonl
  main_lego_agent.jsonl main_lego_r1_agent.jsonl"
V3=""; for ds in xb aw lego; do V3="$V3 main_${ds}_dn.jsonl main_${ds}_xtn.jsonl main_${ds}_think.jsonl main_${ds}_fresh.jsonl"; done
for i in 1 2; do
  ./run_v2_retry.sh $F $V3
  for ds in xb aw lego; do
    $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u; sleep 20
    ./run_v2_retry.sh main_${ds}_neo2.jsonl
    $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u
  done
done
echo RETRY-V3-DONE >> ../results/retry.log
