#!/bin/bash
# Applies the truncation rule to every result file the paper uses.
# Phase 1 (now): files of the gateway models. Phase 2: after the second repetition has finished (needs the reset budget),
# all main-model files, then the Neo4j files with their instances started.
cd "$(dirname "$0")"
PY=${PY:-python}
./run_v2_retry.sh main_xb_fr.jsonl main_aw_fr.jsonl main_lego_fr.jsonl main_aw_fr_agent.jsonl main_lego_fr_agent.jsonl \
  main_aw_mf_fr.jsonl main_lego_mf_fr.jsonl
until [ "$(grep -c REP1-DONE ../results/run_v2.log)" -ge 2 ]; do sleep 120; done
for i in 1 2; do  # second pass picks up runs that failed on the daily cap
  ./run_v2_retry.sh main_xb.jsonl main_aw.jsonl main_lego.jsonl main_aw_m2.jsonl main_lego_m2.jsonl main_xb_m2.jsonl \
    main_aw_mf.jsonl main_lego_mf.jsonl main_xb_mf.jsonl main_lego_abl.jsonl main_xb_abl.jsonl main_lego_g2.jsonl main_xb_g2.jsonl \
    main_lego_g2h.jsonl main_lego_x.jsonl main_lego_para.jsonl main_aw_r1.jsonl main_lego_r1.jsonl main_xb_r1.jsonl \
    main_lego_agent.jsonl main_xb_agent.jsonl main_lego_r1_agent.jsonl
  for ds in xb aw lego; do
    $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u; sleep 20
    ./run_v2_retry.sh main_${ds}_neo2.jsonl
    $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u
  done
done
echo RETRY-ALL-DONE >> ../results/retry.log
