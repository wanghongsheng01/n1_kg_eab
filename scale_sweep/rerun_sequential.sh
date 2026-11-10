#!/bin/sh
# Second pass, one process at a time: rebuild u_explosion from the existing xb_u.duckdb of every setting and re-measure
# the queries (the first pass ran the settings concurrently next to other jobs, which inflates times).
PY=${PY:-python}
cd "$(dirname "$0")"
for args in "--scale 1" "--scale 3" "--scale 6" "--scale 10" "--scale 1 --tiers 5"; do
  $PY sweep.py $args --closure-only || exit 1
done
$PY make_table.py
