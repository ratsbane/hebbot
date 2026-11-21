"""How far the dataset converter's image-to-arm pairing is from one built on
absolute clocks: each camera frame's own capture stamp (host monotonic)
against arm rows mapped robot-clock -> host by the run's two clock pairs."""
import json
import sys

import numpy as np

raw = sys.argv[1]
meta = json.load(open(raw + "/metadata.json"))
pairs = meta["clock_pairs"]
arm_ms = np.load(raw + "/arm-time_ms.npy").astype(float).reshape(-1)
t = np.load(raw + "/camera-wrist-t_s.npy")
ft = np.load(raw + "/camera-wrist-frame_t_s.npy")
r0, h0 = pairs[0]["robot_time_ms"], pairs[0]["host_t_s"]
r1, h1 = pairs[-1]["robot_time_ms"], pairs[-1]["host_t_s"]
k = (h1 - h0) / ((r1 - r0) / 1000)
print("host/robot clock ratio", round(k, 4),
      "| arm log first row", round((arm_ms[0] - r0) / 1000, 3), "s after the start pair",
      "| arm log spans", round((arm_ms[-1] - arm_ms[0]) / 1000, 2), "s,", len(arm_ms), "rows")
arm_host_true = h0 + (arm_ms - r0) / 1000 * k
row_true = np.clip(np.searchsorted(arm_host_true, ft, side="right") - 1, 0, len(arm_ms) - 1)
arm_conv = (arm_ms - arm_ms[0]) / 1000 * k
row_conv = np.clip(np.searchsorted(arm_conv, t, side="right") - 1, 0, len(arm_ms) - 1)
off = (arm_ms[row_conv] - arm_ms[row_true]) / 1000
n = len(off)
print("converter pairs each image with the arm state this many seconds later, by quarter:",
      [round(float(np.median(off[i * n // 4:(i + 1) * n // 4])), 3) for i in range(4)])
