#!/bin/bash
# N live runs of one program back to back on the Franka bench; stop at the
# first run that is not ok. Lives at /tmp/campaign.sh on omen; this is the copy
# of record. The eye is taught by every verified placement; a touch calibration
# runs first only when asked (CALIBRATE=1), since its probing shoves the block.
#
#   /tmp/campaign.sh examples/franka_real/stack_blocks/round_trip.py <seed0> <n>
prog=$1; seed0=$2; n=$3
cd ~/git/ASPIRE || exit 2
run() {  # seed program -> prints True/False, sets $d
  timeout 1500 .venv-franka/bin/cap-harness run --benchmark franka_real --suite stack_blocks --task-id 0 --seed $1 --allow-motion --program "$2" --output-root ~/franka_runs > /tmp/campaign_$1.log 2>&1
  d=$(ls -td /home/doug/franka_runs/franka_real/stack_blocks/00-stack-blocks/$(printf %04d $1)/run-*/ 2>/dev/null | head -1)
  python3 -c "import json,sys; r=json.load(open(sys.argv[1]+\"/source/program-result.json\")); print(bool(r.get(\"ok\")) and bool((r.get(\"result\") or {}).get(\"ok\")))" "$d" 2>/dev/null
}
if [ "${CALIBRATE:-0}" = "1" ]; then
  ok=$(run $seed0 examples/franka_real/stack_blocks/touch_calibrate.py)
  echo "seed $seed0 calibrate ok=$ok $d"
  [ "$ok" = "True" ] || { echo "stopping: calibration not ok"; exit 1; }
else
  echo "seed $seed0 calibrate skipped (CALIBRATE=1 to probe by touch; the placements teach the eye)"
fi
for i in $(seq 1 $n); do
  seed=$((seed0+i))
  ok=$(run $seed "$prog")
  echo "seed $seed ok=$ok $d"
  (~/sync_episodes.sh &)   # the Spark gets every run as it lands
  [ "$ok" = "True" ] || { echo "stopping: run not ok"; exit 1; }
done
echo "campaign done"
