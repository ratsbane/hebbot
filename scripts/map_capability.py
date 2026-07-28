"""Generate an arm-free cuRobo reachability/path map over the tabletop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from hebbot.learning.capability_map import boundary_candidates, sample_grid, save_map


HOME_Q = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8211")
    parser.add_argument("--output", default="/tmp/hebbot_capability_map.json")
    parser.add_argument("--z", type=float, default=0.20)
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--x-min", type=float, default=0.25)
    parser.add_argument("--x-max", type=float, default=0.65)
    parser.add_argument("--y-min", type=float, default=-0.40)
    parser.add_argument("--y-max", type=float, default=0.40)
    parser.add_argument("--no-plan", action="store_true")
    args = parser.parse_args()
    xs = np.arange(args.x_min, args.x_max + args.step / 2, args.step).round(6).tolist()
    ys = np.arange(args.y_min, args.y_max + args.step / 2, args.step).round(6).tolist()
    result = sample_grid(
        xs=xs, ys=ys, ee_z=args.z, start_q=HOME_Q, url=args.url,
        plan_all=not args.no_plan,
    )
    path = save_map(result, Path(args.output))
    counts = {
        "samples": len(result["samples"]),
        "ik_reachable": sum(s["ik_reachable"] for s in result["samples"]),
        "path_found": sum(s.get("path_found") is True for s in result["samples"]),
        "boundary_candidates": len(boundary_candidates(result)),
    }
    print(json.dumps({"path": str(path), "counts": counts,
                      "boundary_candidates": boundary_candidates(result)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
