#!/bin/bash
# Review round (2026-11-08): experiments requested by the pre-submission mock review.
#   dn     SQL substrates with a DuckDB dialect note, the counterpart of the Kuzu note (main models)
#   xtn    Graph-X with a sentence on how to use the materialized edge (main models)
#   xr1    second repetition of SQL-X and Graph-X (main models)
#   think  DS-Flash with thinking enabled on SQL, SQL-U, Graph-T, Graph-U and SQL-X (output cap 8,000 tokens)
# Runs over the daily cap fail and are not recorded; every step resumes from its output file.
cd "$(dirname "$0")"
source ./run_v2.sh none
M=${MODELS:-$DOM}
case "$1" in
  dn)    for ds in xb aw lego; do run $ds $M sql_dn,sql_u_dn,sql_x_dn 0 main_${ds}_dn.jsonl; done ;;
  xtn)   for ds in xb aw lego; do run $ds $M cypher_x_tn 0 main_${ds}_xtn.jsonl; done ;;
  xr1)   for ds in xb aw lego; do run $ds $M sql_x,cypher_x 1 main_${ds}_x_r1.jsonl; done ;;
  think) for ds in xb aw lego; do N1_MIN_MAX_TOKENS=8000 run $ds ds-flash-think sql,sql_u,cypher,cypher_u,sql_x 0 main_${ds}_think.jsonl; done ;;
esac
echo "V3-DONE $1 $M" >> ../results/run_v3.log
