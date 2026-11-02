"""Validate new cases without presenting authored model outputs as accuracy evidence."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "scripts"))
import build_interaction_set  # noqa: E402
import eval_routed  # noqa: E402

from healthops.assistant.toolbox import build_registry  # noqa: E402


def test_candidates_are_reproducible_separate_and_have_valid_oracles():
    cases = eval_routed.load_cases(ROOT / "eval/assistant_interaction.synthetic.jsonl")
    assert cases == build_interaction_set.build()
    assert len(cases) == 36
    assert all(c["split"] == "generalization_candidate" for c in cases)
    old = eval_routed.load_cases(ROOT / "eval/assistant_routed.synthetic.jsonl")
    assert not ({c["id"] for c in old} & {c["id"] for c in cases})
    registry = build_registry()
    for case in cases:
        for turn in case["turns"]:
            for name, args in eval_routed.gold_queries(case, turn):
                registry[name].params.model_validate(args)
