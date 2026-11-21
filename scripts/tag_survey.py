"""Find AprilTags (36h11) in a recorded run's wrist video and place each on the
table: the tag's centre pixel back-projected through the camera pose at that
frame onto the table plane. Runs in a venv with OpenCV (omen's sam3 venv).

    tag_survey.py RUN_DIR [INTRINSICS_JSON] [CORRECTION_JSON]
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

TABLE_Z = 0.017
HAND_EYE_POS = np.array([-0.0595, -0.0087, -0.0537])
HAND_EYE_QUAT = (0.7071068, 0.0, 0.0, -0.7071068)  # wxyz


def quat_matrix(w, x, y, z):
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


run = Path(sys.argv[1])
intr = json.load(open(sys.argv[2] if len(sys.argv) > 2 else "/tmp/d405_color_intrinsics.json"))
corr = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {"offset_hand_xyz": [0, 0, 0]}
K = np.array([[intr["fx"], 0, intr["ppx"]], [0, intr["fy"], intr["ppy"]], [0, 0, 1]])
dist = np.array(intr["coeffs"])

raw = run / "episode" / "raw"
meta = json.load(open(raw / "metadata.json"))
pairs = meta["clock_pairs"]
# robot ms -> host s, linear through the clock pairs
r0, h0 = pairs[0]["robot_time_ms"], pairs[0]["host_t_s"]
r1, h1 = pairs[-1]["robot_time_ms"], pairs[-1]["host_t_s"]
arm_ms = np.load(raw / "arm-time_ms.npy").astype(float)
arm_host = h0 + (arm_ms - r0) * (h1 - h0) / (r1 - r0)
ee = np.load(raw / "arm-O_T_EE.npy")
dq = np.load(raw / "arm-dq.npy")
cam_t = np.load(raw / "camera-wrist-t_s.npy")
if cam_t[0] < 1e5 < arm_host[0]:
    cam_t = cam_t + pairs[0]["host_t_s"] + 1.8  # camera zero, fitted from the tags across survey looks (run 9021)
video = run / "media" / "videos" / "wrist-images-rgb.mp4"
if not video.exists():
    video = next(run.rglob("wrist-images-rgb.mp4"))

R_he = quat_matrix(*HAND_EYE_QUAT)
offset = np.array(corr.get("offset_hand_xyz", [0, 0, 0]), dtype=float)
detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
                                   cv2.aruco.DetectorParameters())

found = {}
capture = cv2.VideoCapture(str(video))
index = -1
while True:
    ok, bgr = capture.read()
    if not ok:
        break
    index += 1
    if index >= len(cam_t):
        break
    frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    a = int(np.clip(np.searchsorted(arm_host, cam_t[index]), 0, len(arm_host) - 1))
    # Still for 1.5 s either side: within a long hold, the clock pairing between
    # camera and arm (good to about a second) cannot pick the wrong pose.
    lo = int(np.clip(np.searchsorted(arm_host, cam_t[index] - 1.5), 0, len(arm_host) - 1))
    hi = int(np.clip(np.searchsorted(arm_host, cam_t[index] + 1.5), 0, len(arm_host) - 1))
    if hi <= lo or float(np.abs(dq[lo:hi]).max()) > 0.01:
        continue
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        continue
    T = np.asarray(ee[a], dtype=float).reshape(4, 4)
    if np.allclose(T[:3, 3], 0.0) and not np.allclose(T[3, :3], 0.0):
        T = T.T  # column-major as libfranka stores it: the translation is in the last row
    cam_R = T[:3, :3] @ R_he
    cam_p = T[:3, :3] @ (HAND_EYE_POS + offset) + T[:3, 3]
    for tag, c in zip(ids.flatten(), corners):
        centre = c.reshape(4, 2).mean(axis=0)
        und = cv2.undistortPoints(centre.reshape(1, 1, 2), K, dist).reshape(2)
        ray = cam_R @ np.array([und[0], und[1], 1.0])
        if ray[2] >= 0:
            continue
        s = (TABLE_Z - cam_p[2]) / ray[2]
        hit = cam_p + s * ray
        side = float(np.mean([np.linalg.norm(c.reshape(4, 2)[k] - c.reshape(4, 2)[(k + 1) % 4]) for k in range(4)]))
        found.setdefault(int(tag), []).append(
            {"frame": index, "hand": [round(float(v), 3) for v in T[:3, 3]],
             "pixel": [round(float(v), 1) for v in centre], "side_px": round(side, 1),
             "table_xy": [round(float(hit[0]), 4), round(float(hit[1]), 4)]})

for tag in sorted(found):
    rows = found[tag]
    by_look = {}
    for r in rows:
        by_look.setdefault((round(r["hand"][0], 2), round(r["hand"][1], 2), round(r["hand"][2], 2)), []).append(r["table_xy"])
    for look, xys in sorted(by_look.items()):
        a = np.array(xys)
        print(f"   tag {tag} from hand {list(look)}: {len(xys)} frames, mean {np.round(a.mean(axis=0), 4).tolist()}, spread {np.round(a.std(axis=0) * 1000, 1).tolist()} mm")
    xy = np.array([r["table_xy"] for r in rows])
    looks = sorted({tuple(r["hand"][:2]) for r in rows})
    print(f"tag {tag}: seen in {len(rows)} still frames from {len(looks)} hand positions")
    print(f"   table position mean {np.round(xy.mean(axis=0), 4).tolist()}  spread {np.round(xy.std(axis=0) * 1000, 1).tolist()} mm")
    print(f"   seen from hand xy {[list(l) for l in looks]}")
    print(f"   tag side {rows[0]['side_px']} px at the first sighting")
if not found:
    print("no tags found")
