#!/bin/bash
# The paired gate on the Franka bench: calibrate by touch, then alternate the
# written skill (round_trip.py) and the learned policy (learned_round_trip.py)
# on the same table, N pairs. The program's own placements set the scene the
# policy then sees, and the program's next run starts from wherever the policy
# left things: that is the self-reset. A program run that is not ok stops the
# gate (the scene needs a hand); a policy run that is not ok is the expected
# result and the gate goes on. Lives at /tmp/gate.sh on omen; this is the copy
# of record. The policy host (policy_server.py) must be up first.
#
#   /tmp/gate.sh <seed0> <pairs>
# Seeds: seed0 calibrates, then (seed0+1, seed0+2), (seed0+3, seed0+4), ...
# so paired_gate.py takes seed0+1 as its SEED0.
seed0=$1; pairs=$2; hung=0
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
program=examples/franka_real/stack_blocks/round_trip.py
policy=examples/franka_real/stack_blocks/learned_round_trip.py
url=${CAP_HARNESS_POLICY_URL:-http://127.0.0.1:8765}
curl -s -m 5 "$url/health" >/dev/null || { echo "no policy host at $url"; exit 3; }
camera_dead() {
  grep -q "produced no frame\|VIDIOC_STREAMON\|not connected; found\|stream has stalled" /tmp/campaign_$1.log
}
run() {  # seed program -> prints True/False
  timeout 900 taskset -c 8-11 .venv-franka/bin/cap-harness run --benchmark franka_real --suite stack_blocks --task-id 0 --seed $1 --allow-motion --program "$2" --output-root ~/franka_runs > /tmp/campaign_$1.log 2>&1
  d=$(ls -td /home/doug/franka_runs/franka_real/stack_blocks/00-stack-blocks/$(printf %04d $1)/run-*/ 2>/dev/null | head -1)
  if camera_dead $1 || grep -q "stream has stalled\|produced no frame" "$d/source/program-result.json" 2>/dev/null; then
    echo "seed $1: camera gave no frame; hardware reset and one retry" >&2
    .venv-franka/bin/python ~/bin/d405_reset.py >&2 && \
      timeout 900 taskset -c 8-11 .venv-franka/bin/cap-harness run --benchmark franka_real --suite stack_blocks --task-id 0 --seed $1 --allow-motion --program "$2" --output-root ~/franka_runs > /tmp/campaign_$1.log 2>&1
  fi
  d=$(ls -td /home/doug/franka_runs/franka_real/stack_blocks/00-stack-blocks/$(printf %04d $1)/run-*/ 2>/dev/null | head -1)
  python3 -c "import json,sys; r=json.load(open(sys.argv[1]+\"/source/program-result.json\")); print(bool(r.get(\"ok\")) and bool((r.get(\"result\") or {}).get(\"ok\")))" "$d" 2>/dev/null
}
# Tidy before the calibration as well: its targets must lie in the working
# band, and the last gate's probing can leave them outside it (0436: not seen).
declutter=examples/franka_real/stack_blocks/declutter.py
ok=$(run $seed0 "$declutter")
echo "seed $seed0 declutter ok=$ok"
ok=$(run $seed0 examples/franka_real/stack_blocks/touch_calibrate.py)
echo "seed $seed0 calibrate ok=$ok"
if [ -z "$ok" ]; then
  # The calibration hung (a reflex, then panda_py's recovery deadlocked against
  # the join, run 0422): park and go on with the eye as the last touch left it.
  echo "seed $seed0 calibration hung; parking"; bash /tmp/park.sh $((seed0 + 9000)) | tail -1
else
  [ "$ok" = "True" ] || { echo "stopping: calibration not ok"; exit 1; }
fi
# Blocks that touch cannot be picked (run 0344); the sweep spreads them. It
# runs before every program run, under the program's seed, and is a no-op on
# a clear table. A table it cannot clear stops the gate.
declutter=examples/franka_real/stack_blocks/declutter.py
for i in $(seq 1 $pairs); do
  s=$((seed0 + 2*i - 1))
  ok=$(run $s "$declutter")
  echo "seed $s declutter ok=$ok"
  # A declutter that is not ok is a warning, not a stop: its sweep may have been
  # refused for room (run 0397) while the table is still workable; the program
  # run that follows is the judge.
  [ "$ok" = "True" ] || echo "warning: declutter not ok at $s; going on"

  ok=$(run $s "$program")
  echo "seed $s program ok=$ok"
  ~/sync_episodes.sh   # blocking: the sync's USB-WiFi load stalls the 1 kHz loop when it overlaps a run (2026-11-19)
  if [ -z "$ok" ]; then
    # No result at all: the harness hung after a reflex and the timeout killed
    # it (runs 9006, 0395, 2026-11-19). That is the host, not the scene: reset
    # the wrist camera, park the arm, and go on; two in a row stops the gate.
    hung=$((hung + 1)); echo "seed $s hung; parking"; bash /tmp/park.sh $((s + 9000)) | tail -1
    [ "$hung" -ge 2 ] && { echo "stopping: two hung runs in a row"; exit 1; }
    continue
  fi
  hung=0
  [ "$ok" = "True" ] || { echo "stopping: program run not ok"; exit 1; }
  p=$((s + 1))
  ok=$(run $p "$policy")
  echo "seed $p policy ok=$ok $(python3 -c "import json,glob; d=sorted(glob.glob('/home/doug/franka_runs/franka_real/stack_blocks/00-stack-blocks/$(printf %04d $p)/run-*/source/program-result.json'))[-1]; r=json.load(open(d)).get('result') or {}; print('carried=%s stop=%s chunks=%s' % (r.get('carried'), r.get('stop'), r.get('chunks')))" 2>/dev/null)"
  ~/sync_episodes.sh   # blocking: the sync's USB-WiFi load stalls the 1 kHz loop when it overlaps a run (2026-11-19)
done
echo "gate done"
