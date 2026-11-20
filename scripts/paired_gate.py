#!/usr/bin/env python3
"""Tally the paired gate: the written skill against the learned policy, seed by seed.

    paired_gate.py [--root ~/franka_runs/franka_real/stack_blocks/00-stack-blocks] SEED0 N

Pairs are (program seed, policy seed) = (SEED0 + 2i, SEED0 + 2i + 1) for i in
0..N-1, the layout franka_gate.sh produces. A program run scores its verified
legs; a policy run scores its carried legs (a grasp at table height, a carry of
8 cm or more, a release). The promotion rule, written 2026-11-17 before any
result: the policy matches or beats the program over at least 20 pairs with no
safety events. The script prints the table and the verdict; it promotes
nothing.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

PAIRS_NEEDED = 20


def latest_result(root: str, seed: int) -> dict | None:
    runs = sorted(glob.glob(os.path.join(root, f"{seed:04d}", "run-*", "source", "program-result.json")))
    if not runs:
        return None
    with open(runs[-1]) as handle:
        record = json.load(handle)
    return record.get("result") or {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=os.path.expanduser(
        "~/franka_runs/franka_real/stack_blocks/00-stack-blocks"))
    parser.add_argument("seed0", type=int)
    parser.add_argument("pairs", type=int)
    args = parser.parse_args(argv)

    rows = []
    for i in range(args.pairs):
        program_seed = args.seed0 + 2 * i
        policy_seed = program_seed + 1
        program = latest_result(args.root, program_seed)
        policy = latest_result(args.root, policy_seed)
        if program is None or policy is None:
            break
        program_legs = int(program.get("verified") or 0)
        policy_legs = int(policy.get("carried") or 0)
        safety = int(policy.get("safety_events") or 0)
        rows.append((program_seed, program_legs, policy_seed, policy_legs, safety,
                     str(policy.get("stop")), int(policy.get("chunks") or 0)))

    print(f"{'program':>8} {'legs':>4}   {'policy':>7} {'legs':>4} {'safety':>6} {'stop':>16} {'chunks':>6}")
    for program_seed, pl, policy_seed, ql, safety, stop, chunks in rows:
        print(f"{program_seed:>8} {pl:>4}   {policy_seed:>7} {ql:>4} {safety:>6} {stop:>16} {chunks:>6}")
    if not rows:
        print("no pairs found")
        return 1
    program_total = sum(r[1] for r in rows)
    policy_total = sum(r[3] for r in rows)
    safety_total = sum(r[4] for r in rows)
    print(f"\npairs {len(rows)}: program {program_total} legs, policy {policy_total} legs, "
          f"{safety_total} safety events")
    passed = len(rows) >= PAIRS_NEEDED and policy_total >= program_total and safety_total == 0
    print("gate:", "PASS" if passed else "FAIL",
          "" if len(rows) >= PAIRS_NEEDED else f"({PAIRS_NEEDED - len(rows)} more pairs needed)")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
