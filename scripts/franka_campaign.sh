#!/bin/bash
# N live runs of one program back to back on the Franka bench; stop at the
# first run that is not ok. Lives at /tmp/campaign.sh on omen; this is the copy
# of record. The eye is taught by every verified placement and probed by touch
# first in every batch (CALIBRATE=0 skips it): probing shoves a block a little, and
# it is the only truth along the block, see the notebook for 2026-11-17 15:50.
#
#   /tmp/campaign.sh examples/franka_real/stack_blocks/round_trip.py <seed0> <n>
prog=$1; seed0=$2; n=$3
cd ~/git/ASPIRE || exit 2
# The harness runs on CPUs 8-11, away from the GPU's interrupt lines (CPUs 2 and
# 4) and the robot NIC's (12): hard interrupts preempt even a FIFO-99 control
# thread, and with the desktop busy the 1 kHz loop lost 22-25% of its packets
# (2026-11-19, nine communication reflexes in a day).
# The wrist camera's table levelling is off: at 0.33 m the D405's table is too
# noisy for the plane fit, the look reused a stale fit and read up to 3 cm off;
# the calibrated pose alone is within 2 cm at every height (eye_check 9001/9004,
# 2026-11-19), and the touch calibration absorbs that.
export CAP_HARNESS_WRIST_LEVEL=0
# Energy-Efficient Ethernet off on the robot's port (Franka's FCI guidance): the
# link's low-power idle between packets cost the 1 kHz loop a quarter of its
# packets on a busy day (2026-11-19). Only when it is on: turning it off
# restarts autonegotiation, which drops a live FCI connection.
if sudo -n ethtool --show-eee enp8s0 2>/dev/null | grep -q 'EEE status: enabled'; then
  sudo -n ethtool --set-eee enp8s0 eee off && sleep 6 && echo 'EEE turned off'
fi
camera_dead() {  # the run never got a frame: the D405 re-enumerated after a cable tug and will not stream until reset
  grep -q "produced no frame\|VIDIOC_STREAMON\|not connected; found\|stream has stalled" /tmp/campaign_$1.log
}
run() {  # seed program -> prints True/False, sets $d
  timeout 1500 taskset -c 8-11 .venv-franka/bin/cap-harness run --benchmark franka_real --suite stack_blocks --task-id 0 --seed $1 --allow-motion --program "$2" --output-root ~/franka_runs > /tmp/campaign_$1.log 2>&1
  if camera_dead $1; then
    echo "seed $1: camera gave no frame; hardware reset and one retry" >&2
    .venv-franka/bin/python ~/bin/d405_reset.py >&2 && \
      timeout 1500 taskset -c 8-11 .venv-franka/bin/cap-harness run --benchmark franka_real --suite stack_blocks --task-id 0 --seed $1 --allow-motion --program "$2" --output-root ~/franka_runs > /tmp/campaign_$1.log 2>&1
  fi
  d=$(ls -td /home/doug/franka_runs/franka_real/stack_blocks/00-stack-blocks/$(printf %04d $1)/run-*/ 2>/dev/null | head -1)
  python3 -c "import json,sys; r=json.load(open(sys.argv[1]+\"/source/program-result.json\")); print(bool(r.get(\"ok\")) and bool((r.get(\"result\") or {}).get(\"ok\")))" "$d" 2>/dev/null
}
# Touch first, every batch (CALIBRATE=0 skips): placements teach the eye only
# along the fingers' travel; along the block a biased grasp reads as zero
# forever, and six grasps of run 0231 sat 15-20 mm from one end.
if [ "${CALIBRATE:-1}" = "1" ]; then
  ok=$(run $seed0 examples/franka_real/stack_blocks/touch_calibrate.py)
  echo "seed $seed0 calibrate ok=$ok $d"
  [ "$ok" = "True" ] || { echo "stopping: calibration not ok"; exit 1; }
else
  echo "seed $seed0 calibrate skipped (CALIBRATE=0)"
fi
for i in $(seq 1 $n); do
  seed=$((seed0+i))
  ok=$(run $seed "$prog")
  echo "seed $seed ok=$ok $d"
  ~/sync_episodes.sh   # blocking: the sync's USB-WiFi load stalls the 1 kHz loop when it overlaps a run (2026-11-19)   # the Spark gets every run as it lands
  [ "$ok" = "True" ] || { echo "stopping: run not ok"; exit 1; }
done
echo "campaign done"
