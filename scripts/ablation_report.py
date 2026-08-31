"""Report the substrate/memory/policy ablation over stored episodes (lab notebook #147)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hebbot.learning.benchmark import report
from hebbot.substrate.store import Store


def _table(summary: list[dict]) -> str:
    header = f"{'policy_version':<16}{'arm':<12}{'n':>4}{'success':>10}  95% CI"
    lines = [header, "-" * len(header)]
    for row in summary:
        interval = row["wilson_95"]
        span = f"[{interval[0]:.2f}, {interval[1]:.2f}]" if interval else "-"
        lines.append(
            f"{str(row['policy_version'] or '-'):<16}{row['arm']:<12}{row['n']:>4}"
            f"{row['success_rate']:>9.0%}  {span}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="~/hebbot-data")
    parser.add_argument("--skill", default="vision_grasp")
    parser.add_argument("--success-field", default="task_verified_success")
    parser.add_argument("--json", action="store_true", help="emit the full record")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    store = Store(data_dir / "hebbot.db")
    try:
        record = report(store, skill_name=args.skill, success_field=args.success_field)
        if args.json:
            print(json.dumps(record, indent=2))
            return 0
        print(f"{args.skill}: {record['n_episodes']} episodes with a verdict\n")
        print(_table(record["summary"]))
        comparison = record["comparison"]
        print()
        if comparison["comparisons"]:
            for row in comparison["comparisons"]:
                verdict = (
                    "not distinguishable from noise"
                    if row["intervals_overlap"] else "intervals separate"
                )
                print(
                    f"{row['arm']} vs {row['baseline']} @ {row['policy_version']}: "
                    f"{row['delta']:+.0%} "
                    f"(n={row['n_arm']} vs {row['n_baseline']}) — {verdict}"
                )
        else:
            print("No within-version arm comparison is possible yet.")
        for row in comparison["unrunnable"]:
            print(f"  {row['policy_version']}: {row['reason']} "
                  f"(arms present: {', '.join(row['arms_present'])})")
        print(f"\n{comparison['note']}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
