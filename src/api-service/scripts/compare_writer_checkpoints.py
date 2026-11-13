"""Compare complete human-reviewed writer replays under matched serving settings."""

import argparse
import json
from pathlib import Path

from scripts.eval_writer_checkpoint import compare_reviews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    runs = [json.loads((p / "run.json").read_text()) for p in (args.before, args.after)]
    for key in (
        "inputs_sha256",
        "serving_precision",
        "temperature",
        "max_tokens",
        "enable_thinking",
    ):
        if runs[0][key] != runs[1][key]:
            raise ValueError(f"Unmatched evaluation setting: {key}")
    reviews = [json.loads((p / "review.json").read_text()) for p in (args.before, args.after)]
    if any(len(r) != 11 for r in reviews):
        raise ValueError("Score all 11 frozen writer inputs, not a favorable subset")
    print(json.dumps(compare_reviews(*reviews), indent=2))


if __name__ == "__main__":
    main()
