import sys, numpy as np
# Calibration: three (EE_x, EE_y, pixel_u, pixel_v) at z=0.20
P0 = (0.448654, 0.003971, 329.1, 394.5)
P1 = (0.478536, 0.003918, 327.0, 334.3)
P2 = (0.447677, 0.033300, 268.4, 397.9)
E = np.array([[P1[0]-P0[0], P2[0]-P0[0]],
              [P1[1]-P0[1], P2[1]-P0[1]]])
Pp = np.array([[P1[2]-P0[2], P2[2]-P0[2]],
               [P1[3]-P0[3], P2[3]-P0[3]]])
J = Pp @ np.linalg.inv(E)          # pixel per meter
Jinv = np.linalg.inv(J)            # meter per pixel
GOAL = np.array([357.5, 372.7])    # grasp pixel at z=0.20
if len(sys.argv) >= 5:
    ex, ey, u, v = map(float, sys.argv[1:5])
    dpix = GOAL - np.array([u, v])
    dee = Jinv @ dpix
    nx, ny = ex + dee[0], ey + dee[1]
    print(f"J=[[{J[0,0]:.0f},{J[0,1]:.0f}],[{J[1,0]:.0f},{J[1,1]:.0f}]] px/m")
    print(f"pixel_err=({dpix[0]:+.1f},{dpix[1]:+.1f})  ee_step=({dee[0]*1000:+.1f},{dee[1]*1000:+.1f})mm")
    print(f"TARGET_EE {nx:.4f} {ny:.4f}")
else:
    print(f"J=[[{J[0,0]:.0f},{J[0,1]:.0f}],[{J[1,0]:.0f},{J[1,1]:.0f}]] px/m  scale~{abs(J[1,0]):.0f},{abs(J[0,1]):.0f} (fx/z={394/0.20:.0f})")
