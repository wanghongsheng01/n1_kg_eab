#!/bin/bash
# Final round, seventh part (2026-11-10 night): the organisation's gateway reached its spending limit and the authors
# chose other channels instead of raising it. Volcengine Ark serves two further thinking models, run on the same five
# substrates as the other thinking models and reported as their own columns (never merged with gateway cells):
#   athink1 <ds>  GLM-5.2 (glm-5-2-260617, Ark) with thinking enabled, temperature 0.6
#   dthink1 <ds>  Doubao-Seed-2.1-Pro (doubao-seed-2-1-pro-260915, Ark, ByteDance) with thinking enabled, temperature 1.0
# Endpoint and key come only from ARK_BASE_URL / ARK_API_KEY (read from the Keychain at launch).
cd "$(dirname "$0")"
source ./run_v2.sh none
: "${ARK_API_KEY:?set ARK_API_KEY}"
ds=$2
case "$1" in
  athink1) for pass in 1 2; do
             N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${AW_:-12} run $ds glm52-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_glm52.jsonl; done ;;
  fthink1) for pass in 1 2; do   # GLM-5.3-Flash (glm-5-3-flash-260828, Ark): GLM-5.2 reasoned for minutes per call, too slow for the deadline
             N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${FW:-12} run $ds glm53f-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_glm53f.jsonl; done ;;
  dthink1) for pass in 1 2; do
             N1_INTERLEAVE=1 N1_MIN_MAX_TOKENS=32000 N1_HTTP_TIMEOUT=900 W=${DW:-12} run $ds doubao-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think_doubao.jsonl; done ;;
esac
echo "V4G-DONE $1 $ds $(date)" >> ../results/run_v4.log
