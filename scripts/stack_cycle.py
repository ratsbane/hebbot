"""Autonomous stack/unstack cycles: the arm resets itself between attempts.

Usage: stack_cycle.py <tag> <n_cycles> [base] [pick]

Each cycle is one scored stack attempt followed by an unscored self-reset. A scored
failure is data, not an emergency: the loop looks (touch_look rewrites the beliefs
from the camera and a confirming probe), and goes on to the next cycle when both
blocks are accounted for on the table — unstacking first if the failure left them
piled. The loop stops for a human only when the world cannot be explained: a block
still in the gripper, a look that cannot account for every block, or a refusal the
loop has no action for. A reset failure gets the same look and one more try; a look
that finds the pick already off the pile IS the reset (f1, 2026-11-11: a failed
grasp unstacked the block by shoving it).

Writes /tmp/cycle_<tag>.jsonl, one line per leg, and prints a running tally.
"""
import json, sys, time
from hebbot.mcp_server import make_hardware_context
from hebbot.substrate import Substrate
from hebbot.skills.primitives import move_to_joints, open_gripper
from hebbot.skills.touch_stack import register_all
from hebbot.memory.scene import register_all as register_scene, scene_recall

tag = sys.argv[1]
cycles = int(sys.argv[2]) if len(sys.argv) > 2 else 3
base_color = sys.argv[3] if len(sys.argv) > 3 else "green"
pick_color = sys.argv[4] if len(sys.argv) > 4 else "blue"
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
ctx = make_hardware_context("192.168.0.253", False)
sub = Substrate("/home/doug/hebbot-data"); register_all(sub); register_scene(sub)
ledger = open(f"/tmp/cycle_{tag}.jsonl", "a")


def log(leg, cycle, res):
    row = {"t": round(time.time(), 1), "cycle": cycle, "leg": leg,
           "stage": res.get("stage"), "success": res.get("task_verified_success"),
           "elapsed_s": res.get("elapsed_s"), "n_probes": res.get("n_probes"),
           "note": res.get("note"),
           "z_error_mm": (res.get("place") or {}).get("z_error_mm"),
           "grip_high_mm": (res.get("grasp") or {}).get("grip_high_mm"),
           "hinted": [(res.get(k) or {}).get("hinted") for k in ("base", "pick")],
           "pick_yaw_deg": (res.get("pick") or {}).get("long_axis_deg"),
           "dest_xy": res.get("dest_xy"), "dest_yaw_deg": res.get("dest_yaw_deg"),
           "holding": res.get("holding"),
           "rest": {c: e.get("resting_on")
                    for c, e in (res.get("found") or {}).items()} or None}
    ledger.write(json.dumps(row, default=str) + "\n"); ledger.flush()
    print(json.dumps(row, default=str), flush=True)
    return row


def home():
    open_gripper(_context=ctx)
    move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)


print("--- memory before ---", flush=True)
before = scene_recall(_store=sub.store)
print(before.get("summary") or before.get("note"), flush=True)

RETRY_STAGES = ("place_no_contact", "grasp", "pile_changed", "not_on_pile", "no_belief")


def pick_resting_on():
    b = scene_recall(object=pick_color, _store=sub.store).get("belief") or {}
    return (b.get("properties") or {}).get("resting_on")


def unstack_with_retry(cycle):
    """One reset leg. A failure that fresh eyes might explain gets one investigation
    (touch_look rewrites the beliefs) and one more try. A look that finds the pick
    already off the pile is the reset, accomplished by accident."""
    for attempt in range(2):
        r = sub.call("touch_unstack",
                     params={"pick_color": pick_color, "base_color": base_color}, context=ctx)
        row = log("unstack", cycle, r.get("result", r))
        home()
        if row["success"]:
            return True
        if row["stage"] not in RETRY_STAGES or attempt:
            return False   # an envelope refusal, a lost block, or already retried: a human
        lk = sub.call("touch_look",
                      params={"colors": [pick_color, base_color]}, context=ctx)
        log("look", cycle, lk.get("result", lk))
        home()
        resting = pick_resting_on()
        if resting == "table":
            print(f"the look found {pick_color} already off the pile: reset accomplished.",
                  flush=True)
            return True
        if resting != base_color:
            return False   # still unexplained after looking: stop for a human
    return False


# find_pick/find_base: f4 cycle 14 halted on a one-frame far-look artifact (blue
# "predicted off the table" 30 cm from a touch-verified position 40 s earlier). The
# look confirms by probe, so a transient sighting costs one look, not a human.
STACK_RECOVER_STAGES = ("grasp", "place_no_contact", "not_stacked",
                        "find_pick", "find_base")


def recover_after_failed_stack(cycle, row):
    """A failed stack is already scored; what matters now is whether the world is
    accounted for. Look, and go on when both blocks sit on the table — unstacking
    first if the failure actually left them piled (a verify can miss a real stack)."""
    if row["stage"] not in STACK_RECOVER_STAGES or row.get("holding"):
        return False
    lk = sub.call("touch_look",
                  params={"colors": [pick_color, base_color]}, context=ctx)
    lrow = log("look", cycle, lk.get("result", lk))
    home()
    rest = lrow.get("rest") or {}
    if rest.get(pick_color) == "table" and rest.get(base_color) == "table":
        return True
    if rest.get(pick_color) == base_color:
        return unstack_with_retry(cycle)
    return False


stacks = attempts = 0
home()

# A pile left by an earlier run (or an earlier stopped loop) is reset first, unscored.
start = scene_recall(object=pick_color, _store=sub.store)
if ((start.get("belief") or {}).get("properties") or {}).get("resting_on") == base_color:
    print(f"{pick_color} starts on {base_color}: resetting first.", flush=True)
    if not unstack_with_retry(0):
        print("could not reset the starting pile; stopping for a human.", flush=True)
        sub.close(); sys.exit(1)

for cycle in range(1, cycles + 1):
    r = sub.call("touch_stack",
                 params={"base_color": base_color, "pick_color": pick_color}, context=ctx)
    row = log("stack", cycle, r.get("result", r))
    attempts += 1; stacks += bool(row["success"])
    print(f"tally {stacks}/{attempts}", flush=True)
    home()
    if not row["success"]:
        if recover_after_failed_stack(cycle, row):
            continue   # the failure is scored and the world explained: next cycle
        print("stack failed and the loop could not recover; stopping for a human.",
              flush=True)
        break
    if not unstack_with_retry(cycle):
        print("unstack did not complete; stopping for a human.", flush=True)
        break

print("--- memory after ---", flush=True)
after = scene_recall(_store=sub.store)
print(after.get("summary") or after.get("note"), flush=True)
sub.close()
print(f"done: {stacks}/{attempts} stacks verified", flush=True)
