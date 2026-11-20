#!/bin/bash
# One dream cycle on the Spark: rebuild the LeRobot dataset from every relayed
# episode, train ACT on it, and leave a dated checkpoint. Training only; no
# checkpoint is promoted here -- that takes the morning paired gate on the
# bench. Runs under nohup from cron or by hand:
#
#   ~/hebbot_tools/dream.sh [steps] [batch]
#
# Episodes live in ~/episodes/<suite>/<seed>/run-*/ (relayed from omen); the
# dataset goes to ~/datasets/<name>; runs to ~/runs/dream-<date>/.
set -euo pipefail
steps=${1:-5000}
batch=${2:-16}
name=franka_round_trip
task="carry the blue block between A and B"
py=~/venvs/lerobot/bin/python
stamp=$(date +%Y%m%d-%H%M)
out=~/runs/dream-$stamp
mkdir -p ~/runs ~/datasets
log=~/runs/dream-$stamp.log
exec > "$log" 2>&1
echo "dream $stamp: steps=$steps batch=$batch"

# Do not fight the LLM server for the GPU: wait while it is busy.
until [ "$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)" -lt 20 ]; do
  echo "gpu busy, waiting"; sleep 300
done

rm -rf ~/datasets/$name
$py ~/hebbot_tools/franka_raw_to_lerobot.py --root ~/datasets/$name --repo-id hebbot/$name \
  --task "$task" --program round_trip.py ~/episodes/stack_blocks/*/run-* 2>&1 | grep -v -i "warning\|svt\|mp4 @"
episodes=$($py - <<EOF
import json; print(json.load(open("$HOME/datasets/$name/meta/info.json"))["total_episodes"])
EOF
)
echo "dataset: $episodes episodes"
if [ "$episodes" -lt 3 ]; then echo "too few episodes to train"; exit 0; fi

$py -m lerobot.scripts.lerobot_train \
  --dataset.repo_id=hebbot/$name --dataset.root=$HOME/datasets/$name \
  --policy.type=act --policy.device=cuda --policy.push_to_hub=false \
  --output_dir="$out" --steps="$steps" --batch_size="$batch" --log_freq=250 --save_freq="$steps"
echo "dream done: $out"
