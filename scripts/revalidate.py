"""Mint the first hardware verification evidence: the verifier gate, live.

Usage: verify_gate.py [hardware_tag]

Two interleaved rounds — the world needs a reset between stack attempts, and the
reset is itself a touch_unstack verification attempt — then touch_look, which
needs no reset. Each leg is one fresh dispatcher call with task-identity params
only, judged by the skill's own expectations. The harness stops the moment the
bench state stops being known, per campaign rules.
"""
import json
import sys

from hebbot.mcp_server import make_hardware_context
from hebbot.substrate import Substrate
from hebbot.skills.primitives import move_to_joints, open_gripper
from hebbot.skills.touch_stack import register_all
from hebbot.memory.scene import register_all as register_scene, scene_recall

TAG = sys.argv[1] if len(sys.argv) > 1 else "omen-franka-d405-2026-11"
HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
ctx = make_hardware_context("192.168.0.253", False)
sub = Substrate("/home/doug/hebbot-data")
register_all(sub)
register_scene(sub)


def home():
    open_gripper(_context=ctx)
    move_to_joints(q=HOME_Q, speed_factor=0.15, _context=ctx)


def leg(skill, params):
    report = sub.verify(skill, context=ctx, params=params, attempts=1, required=1,
                        hardware_tag=TAG)
    print(json.dumps({"skill": skill, **{k: report[k] for k in
          ("verified", "successes", "fingerprint")}}, default=str), flush=True)
    home()
    return report["successes"] == 1


print("--- memory before ---", flush=True)
print(scene_recall(_store=sub.store).get("summary"), flush=True)
home()

# A pile left by an earlier run is reset first — and that reset is itself an
# unstack verification leg, so the evidence ledger loses nothing.
start = scene_recall(object="blue", _store=sub.store)
if ((start.get("belief") or {}).get("properties") or {}).get("resting_on") == "green":
    print("blue starts on green: the reset is the first verification leg.", flush=True)
    if not leg("touch_unstack", {"pick_color": "blue", "base_color": "green"}):
        print("could not reset the starting pile; stopping for a human.", flush=True)
        sub.close()
        sys.exit(1)

# Round 1's reset is forced into the near-base region where places released
# 12-23 mm early (x <= 0.39, 2026-11-12): the validation target for the
# descent-rate contact trigger. Round 2 resets wherever the sampler likes.
UNSTACK_PARAMS = [
    {"pick_color": "blue", "base_color": "green", "dest_xy": [0.37, 0.0]},
    {"pick_color": "blue", "base_color": "green"},
]
for round_n in (1, 2):
    print(f"--- round {round_n} ---", flush=True)
    if not leg("touch_stack", {"base_color": "green", "pick_color": "blue"}):
        print("stack attempt failed: no pile to unstack; stopping for a human.", flush=True)
        break
    if not leg("touch_unstack", UNSTACK_PARAMS[round_n - 1]):
        print("unstack attempt failed: pile state unknown; stopping for a human.", flush=True)
        break
else:
    look = sub.verify("touch_look", context=ctx, params={"colors": ["blue", "green"]},
                      attempts=3, required=2, hardware_tag=TAG)
    print(json.dumps({"skill": "touch_look", **{k: look[k] for k in
          ("verified", "successes", "attempts_used")}}, default=str), flush=True)
    home()

print("--- place z_errors this run (newest first) ---", flush=True)
for skill in ("touch_stack", "touch_unstack"):
    for r in sub.store.conn.execute(
            "SELECT outcome FROM episodes WHERE skill_name=?"
            " ORDER BY started_at DESC LIMIT 2", (skill,)).fetchall():
        res = (json.loads(r[0] or "{}").get("result") or {})
        print(json.dumps({"skill": skill, "dest_xy": res.get("dest_xy"),
                          "z_error_mm": (res.get("place") or {}).get("z_error_mm"),
                          "stage": res.get("stage")}), flush=True)

print("--- verification status ---", flush=True)
for name in ("touch_stack", "touch_unstack", "touch_look"):
    print(json.dumps(sub.verification_status(name, hardware_tag=TAG), default=str),
          flush=True)
print("--- memory after ---", flush=True)
print(scene_recall(_store=sub.store).get("summary"), flush=True)
sub.close()
print("done", flush=True)
