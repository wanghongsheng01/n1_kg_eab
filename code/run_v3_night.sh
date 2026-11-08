#!/bin/bash
# After the 2026-11-08 budget reset: review-round experiments for the main models, twice (the second pass picks up
# runs that failed at the cap), after the second repetition of the main grid has finished.
cd "$(dirname "$0")"
while [ "$(date +%Y-%m-%d)" = "2026-11-07" ]; do sleep 120; done
sleep 600
for pass in 1 2; do
  ./run_v3.sh dn; ./run_v3.sh xtn; ./run_v3.sh xr1; DAILY_CAP=9 ./run_v3.sh think
done
echo V3-NIGHT-DONE >> ../results/run_v3.log
