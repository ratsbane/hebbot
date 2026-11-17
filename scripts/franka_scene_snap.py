import numpy as np
from cap_harness.yam_real.cameras import RealSenseCamera
from PIL import Image

cam = RealSenseCamera("938422073914", resolution=(640, 480), fps=15)
try:
    frame = None
    for _ in range(20):
        frame = cam.read() or frame
finally:
    cam.close()
Image.fromarray(frame.rgb).save("/tmp/scene_now.png")
d = frame.depth_m
print("depth valid %:", round(float((d > 0).mean()) * 100, 1))
print("depth median (valid):", round(float(np.median(d[d > 0])), 3))
