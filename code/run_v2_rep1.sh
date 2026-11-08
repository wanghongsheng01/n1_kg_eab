#!/bin/bash
# Second repetition for the three main models; resumes after the daily budget resets (runs over the cap are not recorded).
cd "$(dirname "$0")"
./run_v2.sh rep1
while [ "$(date +%Y-%m-%d)" = "2026-11-07" ]; do sleep 120; done
sleep 300
./run_v2.sh rep1
