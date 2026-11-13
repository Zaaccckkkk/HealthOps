"""Operator CLI argument handling (no database): the token lifetime option."""

import pytest

from healthops.cli import DEFAULT_TOKEN_DAYS, MAX_TOKEN_DAYS, build_parser


def test_days_defaults_to_seven():
    args = build_parser().parse_args(["issue-token", "--user-id", "0" * 32, "--output", "x.json"])
    assert args.days == DEFAULT_TOKEN_DAYS == 7


def test_days_accepts_a_reviewer_lifetime():
    args = build_parser().parse_args(
        ["create-user", "--name", "reviewer", "--output", "x.json", "--days", "30"]
    )
    assert args.days == 30


@pytest.mark.parametrize("value", ["0", "-5", str(MAX_TOKEN_DAYS + 1), "365", "week"])
def test_days_rejects_out_of_range_or_non_integer(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["issue-token", "--user-id", "0" * 32, "--output", "x.json", "--days", value]
        )
