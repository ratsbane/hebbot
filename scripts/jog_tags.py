"""Doug jogs the fingertips to the centre of each AprilTag with the Xbox
controller; each START records where the hand is. Two tags, then done.

Run on omen in ~/hebbot/.venv with nothing else holding the robot."""
import json
import sys
import time

sys.path.insert(0, "/home/doug/hebbot/src")

import panda_py
from panda_py import libfranka

from hebbot.skills.primitives import teleop_xbox

HOST = "192.168.0.253"
OUT = "/tmp/jog_tags.json"

panda = panda_py.Panda(HOST)
gripper = libfranka.Gripper(HOST)
context = {"panda": panda, "gripper": gripper}
marks = []
for name in ("first tag", "second tag"):
    print(f"jog to the centre of the {name}, then press START", flush=True)
    result = teleop_xbox(speed=0.04, max_runtime=600.0, _context=context)
    pose = panda.get_pose()
    mark = {"tag": name, "exited_via": result.get("exited_via"),
            "position": [round(float(v), 4) for v in pose[:3, 3]],
            "yaw_deg": round(float(__import__("math").degrees(__import__("math").atan2(pose[1, 0], pose[0, 0]))), 1)}
    marks.append(mark)
    print(json.dumps(mark), flush=True)
    json.dump(marks, open(OUT, "w"))
    time.sleep(1.0)
print("done", flush=True)
