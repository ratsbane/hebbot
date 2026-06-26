# Parallax-aware image servo: target plane can be elevated h metres above the table.
# Usage: servo_zh.py EEx EEy EEz u v [h]
import sys, math
GP_U=(328.9,5.94); GP_V=(235.6,27.4)
JINV0=((-1.168537e-05,-4.959711e-04),(-4.821881e-04,1.770091e-05))
ex,ey,z,u,v = map(float, sys.argv[1:6])
h = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
d = z - h                                   # camera-to-target vertical distance
tu = GP_U[0]+GP_U[1]/d; tv = GP_V[0]+GP_V[1]/d
du,dv = tu-u, tv-v; s = d/0.20              # Jinv gain ~ f/d
dex = (JINV0[0][0]*du+JINV0[0][1]*dv)*s
dey = (JINV0[1][0]*du+JINV0[1][1]*dv)*s
print(f"d={d:.3f} target=({tu:.1f},{tv:.1f}) err=({du:+.1f},{dv:+.1f}) |{math.hypot(du,dv):.1f}px| step=({dex*1000:+.1f},{dey*1000:+.1f})mm")
print(f"TARGET_EE {ex+dex:.4f} {ey+dey:.4f}")
