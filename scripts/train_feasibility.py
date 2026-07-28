"""Train and promotion-gate Hebbot's tiny visual-servo envelope classifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hebbot.learning.feasibility import extract_examples, save_artifact, train_candidate
from hebbot.substrate.store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="~/hebbot-data")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser()
    store = Store(data_dir / "hebbot.db")
    try:
        examples = extract_examples(store)
        result = train_candidate(examples)
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
