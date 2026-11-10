#!/bin/bash
# Final-review round (2026-11-07): uniform Cypher dialect note (multi-type sentence everywhere), its no-hint ablation,
# three further models from the organisation's gateway, and a second repetition of the main grid.
# The gateway endpoint and key come from LLMHUB_BASE_URL / LLMHUB_API_KEY and are never written to disk.
cd "$(dirname "$0")"
PY=${PY:-python}
DOM=${DOM:-ds-flash,ds-pro,kimi-k2.6}
FR=gpt-6-sol,claude-opus-5.5,qwen3.8-27b
qf () { [ $1 = xb ] && echo xb_questions.jsonl || echo $1_questions.jsonl; }
cf () { [ $1 = xb ] && echo xb_s3_coverage.json || echo $1_s3_coverage.json; }
run () {  # ds models substrates rep out
  N1_DATASET=$1 N1_QFILE=$(qf $1) N1_COVFILE=$(cf $1) $PY -W ignore run_pilot.py --models $2 --substrates $3 --rep $4 --workers ${W:-6} --out $5 >> ../results/${5%.jsonl}.log 2>&1
}
agent () {  # ds models rep out
  N1_DATASET=$1 N1_QFILE=$(qf $1) N1_COVFILE=$(cf $1) $PY -W ignore run_agent.py --models $2 --rep $3 --workers ${W:-6} --out $4 >> ../results/${4%.jsonl}.log 2>&1
}
neo () {  # ds models substrates rep out
  $PY neo4j_build.py start $1_t; $PY neo4j_build.py start $1_u
  run $1 $2 $3 $4 $5
  $PY neo4j_build.py stop $1_t; $PY neo4j_build.py stop $1_u
}
case "$1" in
  graph)     # domestic models: Kuzu reruns with the uniform note, then Neo4j
    for ds in xb aw lego; do run $ds $DOM cypher,cypher_u,cypher_nh 0 main_${ds}_g2.jsonl; done
    for ds in xb aw lego; do neo $ds $DOM neo_t,neo_u,neo_t_nh 0 main_${ds}_neo2.jsonl; done
    echo GRAPH-DONE >> ../results/run_v2.log ;;
  frontier)  # gateway models: full grid on Kuzu + agent
    for ds in xb aw lego; do
      run $ds $FR sql,sql_u,cypher,cypher_u,semantic,classify 0 main_${ds}_fr.jsonl
      agent $ds $FR 0 main_${ds}_fr_agent.jsonl
    done
    echo FRONTIER-DONE >> ../results/run_v2.log ;;
  rep1)      # domestic models: second repetition of the main grid
    for ds in xb aw lego; do
      run $ds $DOM sql,sql_u,cypher,cypher_u,semantic,classify 1 main_${ds}_r1.jsonl
      agent $ds $DOM 1 main_${ds}_r1_agent.jsonl
    done
    echo REP1-DONE >> ../results/run_v2.log ;;
esac
