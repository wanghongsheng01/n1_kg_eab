#!/bin/bash
# Final round, fourth part (2026-11-10): gateway models.
#   frneo   GPT-6-Sol, Claude-Opus-5.5 and Qwen3.8-27B on Neo4j (typed and unified graphs)
#   qthink  Qwen3.8-27B with thinking enabled on SQL, SQL-U, Graph-T, Graph-U and SQL-X (32,000 output tokens)
#   kthink1 Kimi K3.1 Pro (Volcengine Ark) with thinking enabled, same substrates
#   gthink1 GLM-5.3 with thinking enabled, same substrates (the vendor account of Kimi-K2.6 ran out; author: no top-up)
# The gateway endpoint and key come only from LLMHUB_BASE_URL / LLMHUB_API_KEY in the environment.
cd "$(dirname "$0")"
source ./run_v2.sh none
: "${LLMHUB_API_KEY:?set LLMHUB_API_KEY}"
case "$1" in
  frneo)  for pass in 1 2; do for ds in xb aw lego; do
            echo 1 > ../results/.neo_hold_${ds}_t; echo 1 > ../results/.neo_hold_${ds}_u
            $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u
            W=6 run $ds $FR neo_t,neo_u 0 main_${ds}_fr_neo.jsonl
            echo 0 > ../results/.neo_hold_${ds}_t; echo 0 > ../results/.neo_hold_${ds}_u
            pgrep -f "main_${ds}_fresh_neo" > /dev/null || { $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u; }
          done; done ;;
  frneo1) ds=$2; for pass in 1 2; do   # one dataset per driver, so that datasets run side by side
            echo 1 > ../results/.neo_hold_${ds}_t; echo 1 > ../results/.neo_hold_${ds}_u
            $PY neo4j_build.py start ${ds}_t; $PY neo4j_build.py start ${ds}_u
            W=6 run $ds $FR neo_t,neo_u 0 main_${ds}_fr_neo.jsonl
            echo 0 > ../results/.neo_hold_${ds}_t; echo 0 > ../results/.neo_hold_${ds}_u
            pgrep -f "main_${ds}_fresh_ne[o]" > /dev/null || { $PY neo4j_build.py stop ${ds}_t; $PY neo4j_build.py stop ${ds}_u; }
          done ;;
  qthink) for pass in 1 2; do for ds in xb aw lego; do
            N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=10 run $ds qwen-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_qwen.jsonl; done; done ;;
  gthink1) ds=$2; for pass in 1 2; do   # GLM-5.3 (Zhipu) with thinking enabled, the second cross-vendor thinking model
            N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${GW:-12} run $ds glm-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_glm.jsonl; done ;;
  kthink1) ds=$2; for pass in 1 2; do   # Kimi K3.1 Pro (Volcengine Ark, kimi-k3-1-pro-261024) with thinking enabled
            : "${ARK_API_KEY:?set ARK_API_KEY}"
            N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${KW:-16} run $ds kimi3-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_kimi3.jsonl; done ;;
  qthink1) ds=$2; for pass in 1 2; do   # one dataset per driver, so the three datasets run side by side
            N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${QW:-12} run $ds qwen-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_qwen.jsonl; done ;;
esac
echo "V4D-DONE $1 $(date)" >> ../results/run_v4.log
