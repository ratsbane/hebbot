"""Train and gate a versioned vision_grasp warm-start candidate from Hebbot episodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hebbot.substrate.store import Store
from hebbot.learning.warm_start import extract_verified_examples, save_artifact, train_candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="~/hebbot-data")
    parser.add_argument("--min-samples", type=int, default=6)
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser()
    store = Store(data_dir / "hebbot.db")
    try:
        examples = extract_verified_examples(store)
        result = train_candidate(examples, min_samples=args.min_samples)
        path = save_artifact(result, data_dir / "policies")
        print(json.dumps({
            "artifact": str(path), "promoted": result.promoted,
            "reasons": result.reasons, "metrics": result.artifact,
        }, indent=2))
        return 0 if result.promoted else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
