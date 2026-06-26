"""Blue-block centroid in a wrist frame. Usage: segblue.py <abs_or_rel_path> [data_dir]
The navy cube has near-zero red (R~1) and strong B-G; table/shadow/bright-bg all have high R.
Prints: u v npix  (or 'none N')."""
import sys, os
import numpy as np
from PIL import Image
p = sys.argv[1]
if not os.path.isabs(p):
    p = os.path.join(sys.argv[2] if len(sys.argv) > 2 else "/home/doug/hebbot-data", p)
img = np.array(Image.open(p).convert("RGB")).astype(int)
R, G, B = img[:, :, 0], img[:, :, 1], img[:, :, 2]
m = (R < 40) & (B - G > 18) & (B > 50)
ys, xs = np.where(m)
if len(xs) < 30:
    print("none", int(len(xs))); sys.exit(0)
print(f"{xs.mean():.1f} {ys.mean():.1f} {int(len(xs))}")
