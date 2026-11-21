"""The lag between the wrist video and the arm log, from motion onsets: each
time the arm starts or stops moving (joint speed crossing a threshold) against
the time the image starts or stops changing. Times are each stream's own
zero (camera t_s and arm rows from the first row), as the converter uses them."""
import json, sys
from pathlib import Path
import cv2, numpy as np
run = Path(sys.argv[1]); raw = run / "episode" / "raw"
meta = json.load(open(raw / "metadata.json")); pairs = meta["clock_pairs"]
arm_ms = np.load(raw / "arm-time_ms.npy").astype(float).reshape(-1)
scale = 1.0
if len(pairs) >= 2:
    hs = pairs[-1]["host_t_s"] - pairs[0]["host_t_s"]; rs = (pairs[-1]["robot_time_ms"] - pairs[0]["robot_time_ms"]) / 1000
    scale = hs / rs if rs > 0 else 1.0
arm_t = (arm_ms - arm_ms[0]) / 1000 * scale          # the converter's arm clock
dq = np.load(raw / "arm-dq.npy"); speed = np.abs(dq).max(axis=1)
cam_t = np.load(raw / "camera-wrist-t_s.npy")
cap = cv2.VideoCapture(str(next(run.rglob("wrist-images-rgb.mp4"))))
prev = None; diff = []
while True:
    ok, bgr = cap.read()
    if not ok: break
    g = cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (9, 9), 0).astype(np.float32)
    diff.append(0.0 if prev is None else float(np.mean(np.abs(g - prev))))
    prev = g
diff = np.array(diff[:len(cam_t)]); cam_t = cam_t[:len(diff)]
def edges(t, x, thr, min_gap):
    on = x > thr; out = []
    for i in range(1, len(on)):
        if on[i] != on[i - 1] and (not out or t[i] - out[-1][0] > min_gap):
            out.append((float(t[i]), "start" if on[i] else "stop"))
    return out
arm_edges = edges(arm_t, speed, 0.05, 0.3)
img_thr = max(1.0, float(np.percentile(diff, 60)))
img_edges = edges(cam_t, diff, img_thr, 0.3)
print("arm log first row at camera t =? (converter assumes 0)")
print("arm starts:", [round(t, 2) for t, k in arm_edges if k == "start"][:10])
print("img starts:", [round(t, 2) for t, k in img_edges if k == "start"][:10])
a = np.array([t for t, k in arm_edges if k == "start"]); b = np.array([t for t, k in img_edges if k == "start"])
if len(a) and len(b):
    lags = [float(b[np.argmin(np.abs(b - x - 0.8))] - x) for x in a]
    print("lag image minus arm, per start (s):", [round(l, 2) for l in lags])
    print("median lag:", round(float(np.median(lags)), 2), "s")
