"""Try each image-axis convention for the wrist camera and keep the one under
which every survey look puts each tag in the same place."""
import itertools, json, sys
from pathlib import Path
import cv2, numpy as np
TABLE_Z = 0.017
HE_POS = np.array([-0.0595, -0.0087, -0.0537])
run = Path(sys.argv[1]); raw = run / "episode" / "raw"
intr = json.load(open(sys.argv[2])); corr = json.load(open(sys.argv[3]))
K = np.array([[intr["fx"], 0, intr["ppx"]], [0, intr["fy"], intr["ppy"]], [0, 0, 1]]); dist = np.array(intr["coeffs"])
meta = json.load(open(raw / "metadata.json")); pairs = meta["clock_pairs"]
r0, h0 = pairs[0]["robot_time_ms"], pairs[0]["host_t_s"]; r1, h1 = pairs[-1]["robot_time_ms"], pairs[-1]["host_t_s"]
arm_host = h0 + (np.load(raw / "arm-time_ms.npy").astype(float) - r0) * (h1 - h0) / (r1 - r0)
ee = np.load(raw / "arm-O_T_EE.npy"); dq = np.load(raw / "arm-dq.npy")
cam_t = np.load(raw / "camera-wrist-t_s.npy") + pairs[0]["host_t_s"]
offset = np.array(corr.get("offset_hand_xyz", [0, 0, 0]), float)
det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), cv2.aruco.DetectorParameters())

R = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float)
frames = []
cap = cv2.VideoCapture(str(next(run.rglob("wrist-images-rgb.mp4")))); i = -1
raw_t = np.load(raw / "camera-wrist-t_s.npy")
while True:
    ok, bgr = cap.read()
    if not ok: break
    i += 1
    if i >= len(raw_t): break
    corners, ids, _ = det.detectMarkers(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    if ids is None: continue
    for t, c in zip(ids.flatten(), corners):
        u = cv2.undistortPoints(c.reshape(4, 2).mean(axis=0).reshape(1, 1, 2), K, dist).reshape(2)
        frames.append((float(raw_t[i]), int(t), np.array([u[0], u[1], 1.0])))
def spread_for(zero):
    pts = {}
    for t_rel, tag, ray in frames:
        a = int(np.clip(np.searchsorted(arm_host, zero + t_rel), 0, len(arm_host) - 1))
        if float(np.abs(dq[a]).max()) > 0.01: continue
        T = np.asarray(ee[a], float).reshape(4, 4)
        if np.allclose(T[:3, 3], 0) and not np.allclose(T[3, :3], 0): T = T.T
        if T[2, 3] > 0.4: continue
        d = T[:3, :3] @ R @ ray; p = T[:3, :3] @ (HE_POS + offset) + T[:3, 3]
        if d[2] >= 0: continue
        pts.setdefault(tag, []).append(p + (TABLE_Z - p[2]) / d[2] * d)
    if not pts: return None, {}
    s = np.mean([np.linalg.norm(np.std(np.array(v), axis=0)) for v in pts.values()])
    return s, {t: (len(v), np.round(np.mean(v, axis=0)[:2], 4).tolist(), np.round(np.std(np.array(v), axis=0)[:2] * 1000, 1).tolist()) for t, v in pts.items()}
base = pairs[0]["host_t_s"]
results = []
for off in np.arange(-1.0, 4.01, 0.1):
    s, m = spread_for(base + off)
    if s is not None: results.append((s, off, m))
results.sort(key=lambda r: r[0])
for s, off, m in results[:5]:
    print(f"camera zero = start + {off:+.1f} s: spread {s*1000:.1f} mm  {m}")
print("arm log starts at start +", round(float(arm_host[0] - base), 2), "s")
