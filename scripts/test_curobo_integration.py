"""Arm-free test of the cuRobo move_to_pose IK path (client + offset + service + FK)."""
import numpy as np
import panda_py
from hebbot.skills.primitives import _curobo_ik

URL = "http://127.0.0.1:8211"
home = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]
poses = [("normal (0.45, 0, 0.30)", [0.45, 0.0, 0.30]),
         ("hard   (0.30, 0, 0.06)", [0.30, 0.0, 0.06])]

print("cuRobo move_to_pose IK path — arm-free validation")
print("(EE-frame target -> offset to panda_hand -> service -> FK-verify in panda_py EE frame)\n")
for label, pos in poses:
    cand = _curobo_ik(pos, [0.0, 1.0, 0.0, 0.0], home, URL)
    if cand is None:
        print(f"{label}: cuRobo returned None (would fall back to analytical)")
        continue
    reached = panda_py.fk(cand)[:3, 3]          # panda_py EE position at cuRobo's config
    err = float(np.linalg.norm(reached - np.array(pos)) * 1000)
    swing = float(np.max(np.abs(cand - np.array(home))))
    verdict = "USE cuRobo" if (err < 10 and swing < 2.5) else "REJECT -> analytical"
    print(f"{label}: EE reached={np.round(reached,4)}  err={err:.2f}mm  "
          f"max_swing={swing:.3f}rad  J4={cand[3]:+.3f}  -> {verdict}")
