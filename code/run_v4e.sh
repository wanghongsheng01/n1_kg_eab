#!/bin/bash
# Final round, fill-in (2026-11-10): the Kimi-K2.6 vendor account ran out of balance during the final round, so every
# step that lost Kimi jobs is resumed with Kimi served through the organisation's gateway (same model, same request,
# N1_KIMI_GATEWAY in llm.py). Each step resumes from its output file, so only the missing jobs are run.
# Needs LLMHUB_BASE_URL / LLMHUB_API_KEY in the environment (never stored on disk).
cd "$(dirname "$0")"
export N1_KIMI_GATEWAY=1
case "$1" in
  think) TW=${TW:-12} ./run_v4.sh think2 kimi-think ;;
  restds)  # after the channel check (code/channel_check.py: gateway Kimi-K2.6 not equivalent to the vendor API), only the
         # DeepSeek models are resumed; Kimi-K2.6 runs through the gateway are excluded from the analysis
         unset N1_KIMI_GATEWAY; export DOM=ds-flash,ds-pro
         ./run_v4b.sh fresh3; FW=8 ./run_v4b.sh fewshot xb aw lego; ./run_v4c.sh; ./run_v4.sh fresh2 ;;
  rest)  # waits for the fresh3 driver (Neo4j on the fresh set), then resumes the other final-round steps
         while pgrep -f "run_v4b.sh fresh3" > /dev/null; do sleep 60; done
         ./run_v4b.sh fresh3; FW=8 ./run_v4b.sh fewshot xb aw lego; ./run_v4c.sh; ./run_v4.sh fresh2 ;;
esac
echo "V4E-DONE $1 $(date)" >> ../results/run_v4.log
