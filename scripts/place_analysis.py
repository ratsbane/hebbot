"""Why do pile places release high while table places are sub-millimetre?

Reads every touch_stack / touch_unstack episode outcome from the substrate store
(no arm). For each successful place, tabulates the quantities the release height
could depend on, then prints correlations. Candidate mechanisms:
  - slip: the block slides down in the jaws during the carry -> contact early by
    the slip; should correlate with how low on the block the jaws closed
    (hold_offset vs block height) and appear in BOTH stack and unstack places.
  - early trigger: the lag/force trip fires before contact; should correlate with
    arm configuration (target height, xy) and not with grip geometry.
  - model error: expected_contact mis-built from table_z/base_top; should show as
    a bias tracking the table estimate or the base top's measurement.
"""
import json
import math

from hebbot.substrate.store import Store

s = Store("/home/doug/hebbot-data/hebbot.db")
rows = s.conn.execute(
    "SELECT * FROM episodes WHERE skill_name IN ('touch_stack','touch_unstack')"
    " ORDER BY started_at").fetchall()
print(f"{len(rows)} episodes")

recs = []
for r in rows:
    d = dict(r)
    try:
        outcome = json.loads(d.get("outcome") or "{}")
    except Exception:
        continue
    res = outcome.get("result") or {}
    place = res.get("place") or {}
    grasp = res.get("grasp") or {}
    if not place.get("contact") or place.get("z_error_mm") is None:
        continue
    skill = d["skill_name"]
    if skill == "touch_stack":
        base, pick = res.get("base") or {}, res.get("pick") or {}
        table_z = res.get("table_z")
        base_top = base.get("top")
        hold = None
        if grasp.get("actual_z") is not None and table_z is not None:
            hold = grasp["actual_z"] - table_z
        pick_h = res.get("pick_height_mm")
        target_z = res.get("expected_contact_z")
        yaw_pick = pick.get("long_axis_deg")
        yaw_base = base.get("long_axis_deg")
        xy = base.get("xy") or [None, None]
    else:
        believed = res.get("believed") or {}
        base_top = None
        target_z = res.get("expected_contact_z")
        hold = None
        if grasp.get("actual_z") is not None and target_z is not None:
            # unstack: hold_offset = actual_z - base_top; expected = dest_table + hold
            hold = None
        pick_h = None
        yaw_pick = believed.get("yaw_deg")
        yaw_base = None
        xy = res.get("dest_xy") or [None, None]
    recs.append({
        "t": d.get("started_at"), "skill": skill, "stage": res.get("stage"),
        "ok": res.get("task_verified_success"),
        "z_err": place.get("z_error_mm"),
        "grip_high": grasp.get("grip_high_mm"),
        "actual_z": grasp.get("actual_z"), "actual_w": grasp.get("actual_width"),
        "hold_mm": None if hold is None else round(hold * 1000, 1),
        "pick_h_mm": pick_h, "target_z": target_z,
        "yaw_pick": yaw_pick, "yaw_base": yaw_base,
        "x": xy[0], "y": xy[1],
        "verify0": ((res.get("verify") or {}).get("readings") or [None])[-1],
        "expected_top": (res.get("verify") or {}).get("expected_top"),
    })

print(f"{len(recs)} places with contact")
print("\nskill        stage      z_err  grip  hold  pickH  targz   yawP   yawB      x       y   vtop-exp")
for q in recs:
    vd = None
    if q["verify0"] is not None and q["expected_top"] is not None:
        vd = round((q["verify0"] - q["expected_top"]) * 1000, 1)
    print(f"{q['skill']:<12} {q['stage']:<9} "
          f"{q['z_err']:>6} {q['grip_high'] if q['grip_high'] is not None else '':>5} "
          f"{q['hold_mm'] if q['hold_mm'] is not None else '':>5} "
          f"{q['pick_h_mm'] if q['pick_h_mm'] is not None else '':>5} "
          f"{round(q['target_z'],4) if q['target_z'] is not None else '':>7} "
          f"{round(q['yaw_pick'],1) if q['yaw_pick'] is not None else '':>6} "
          f"{round(q['yaw_base'],1) if q['yaw_base'] is not None else '':>6} "
          f"{round(q['x'],3) if q['x'] is not None else '':>7} "
          f"{round(q['y'],3) if q['y'] is not None else '':>7} "
          f"{vd if vd is not None else '':>8}")

import numpy as np

def corr(name, pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(pairs) < 4:
        print(f"{name}: n={len(pairs)} (too few)")
        return
    a, b = np.array([p[0] for p in pairs], float), np.array([p[1] for p in pairs], float)
    print(f"{name}: n={len(pairs)} r={np.corrcoef(a, b)[0,1]:+.2f}")

stacks = [q for q in recs if q["skill"] == "touch_stack"]
unstacks = [q for q in recs if q["skill"] == "touch_unstack"]
print(f"\nstack places: n={len(stacks)}  z_err mean "
      f"{np.mean([q['z_err'] for q in stacks]):+.1f} "
      f"sd {np.std([q['z_err'] for q in stacks]):.1f}")
print(f"unstack places: n={len(unstacks)}  z_err mean "
      f"{np.mean([q['z_err'] for q in unstacks]):+.1f} "
      f"sd {np.std([q['z_err'] for q in unstacks]):.1f}")
print("\n--- stack z_err correlations ---")
corr("vs grip_high", [(q["grip_high"], q["z_err"]) for q in stacks])
corr("vs hold_mm", [(q["hold_mm"], q["z_err"]) for q in stacks])
corr("vs |yaw_pick|", [(abs(q["yaw_pick"]) if q["yaw_pick"] is not None else None, q["z_err"]) for q in stacks])
corr("vs |yaw_base|", [(abs(q["yaw_base"]) if q["yaw_base"] is not None else None, q["z_err"]) for q in stacks])
corr("vs target_z", [(q["target_z"], q["z_err"]) for q in stacks])
corr("vs x", [(q["x"], q["z_err"]) for q in stacks])
corr("vs y", [(q["y"], q["z_err"]) for q in stacks])
corr("vs time", [(q["t"], q["z_err"]) for q in stacks])
corr("vs verify_err", [(q["z_err"],
                        (q["verify0"] - q["expected_top"]) * 1000
                        if q["verify0"] is not None and q["expected_top"] is not None
                        else None) for q in stacks])
print("\n--- unstack z_err correlations ---")
corr("vs grip_high", [(q["grip_high"], q["z_err"]) for q in unstacks])
corr("vs target_z", [(q["target_z"], q["z_err"]) for q in unstacks])
corr("vs x", [(q["x"], q["z_err"]) for q in unstacks])
corr("vs y", [(q["y"], q["z_err"]) for q in unstacks])
s.close()
