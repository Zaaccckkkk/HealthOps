"""Supplemental tests never reuse development account histories or change the old holdout."""

from eval.writer_chain_cases import ACCOUNTS as development
from eval.writer_duration_cases import ACCOUNTS, cases
from eval.writer_holdout_cases import frozen_cases


def test_duration_test_accounts_do_not_overlap_training_or_validation_cohorts():
    rows = cases()
    assert len(rows) == 6 and len({r["id"] for r in rows}) == 6
    assert not set(ACCOUNTS) & set(development)
    assert all(r["split"] == "heldout_test" and r["training_eligible"] is False for r in rows)
    assert all(r["expected_queries"][0]["arguments"]["metric"] == "sleep_duration" for r in rows)
    assert all(len(r["questions"]) == 1 for r in rows)
    assert len(frozen_cases()) == 20
