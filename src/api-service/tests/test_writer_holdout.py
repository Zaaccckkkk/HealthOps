"""Holdout isolation and valid tool contracts, without model inference."""

import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid5

import pytest

from eval.writer_chain_cases import ACCOUNTS as DEVELOPMENT_ACCOUNTS
from eval.writer_holdout_cases import ACCOUNTS, cases, frozen_cases
from healthops.assistant.toolbox import build_registry
from healthops.synthetic_import import NAMESPACE, digest
from scripts.eval_writer_chain import EDGE_ID, ROOT, load_accounts


def test_new_accounts_and_queries_are_disjoint_and_use_supported_tool_schemas():
    rows = cases()
    assert len(rows) == len({r["id"] for r in rows}) == 20
    assert len({r["source_user_id"] for r in rows}) == 20
    assert not set(ACCOUNTS) & set(DEVELOPMENT_ACCOUNTS)
    assert sum(len(r["questions"]) for r in rows) == 22
    for row in rows:
        assert row["split"] == "heldout_test" and not row["training_eligible"]
        assert row["data_kind"] == "synthetic"
        assert not {"cards", "evidence", "reference_answer", "chosen", "rejected"} & row.keys()
        assert row["manual_rubric"]
        for query in row["expected_queries"]:
            build_registry()[query["tool"]].params.model_validate(query["arguments"])
            assert query["arguments"]["timezone"] == row["timezone"]
    assert cases() == rows
    assert frozen_cases() == rows


def test_empty_and_short_history_questions_use_the_inspected_account_window():
    by_family = {r["family"]: r for r in cases()}
    empty = by_family["empty_window"]
    args = empty["expected_queries"][0]["arguments"]
    first = ACCOUNTS[empty["source_user_id"]][0]
    assert args["start_date"] <= args["end_date"] < first
    for family in ("single_source_reconcile", "short_sleep_estimate"):
        row = by_family[family]
        target = date.fromisoformat(row["expected_queries"][0]["arguments"]["date"])
        first = date.fromisoformat(ACCOUNTS[row["source_user_id"]][0])
        assert (target - first).days == 2
    today = by_family["unfinished_steps"]
    assert today["today"] == today["expected_queries"][0]["arguments"]["end_date"]


def test_freeze_rejects_question_changes(monkeypatch):
    from eval import writer_holdout_cases

    altered = cases()
    altered[0]["questions"][0] = "A changed question"
    monkeypatch.setattr(writer_holdout_cases, "cases", lambda: altered)
    with pytest.raises(ValueError, match="Frozen holdout changed"):
        writer_holdout_cases.frozen_cases()


@pytest.mark.parametrize("extra_account", [False, True])
def test_holdout_database_disallows_even_the_development_edge_account(tmp_path, extra_account):
    source = next(iter(ACCOUNTS))
    manifest_hash = digest(ROOT.parents[1] / "data/manifests/synthetic-v0.2.yaml")
    uid = uuid5(NAMESPACE, f"synthetic-v0.2:{manifest_hash}:{source}")
    user = {
        "user_id": source,
        "api_user_id": str(uid),
        "display_name": "Synthetic test",
        "records": 2,
    }
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "version": "synthetic-v0.2",
                "manifest_sha256": manifest_hash,
                "users": [user],
            }
        )
    )
    (tmp_path / f"{uid}.json").write_text(json.dumps({"user_id": str(uid), "token": "test-only"}))

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, *_):
            return SimpleNamespace(display_name="Synthetic test")

        def scalar(self, *_):
            return 2

        def scalars(self, *_):
            return [uid, EDGE_ID] if extra_account else [uid]

    if extra_account:
        with pytest.raises(ValueError, match="outside this synthetic run"):
            load_accounts(tmp_path, Session, required_accounts={source}, allow_edge=False)
    else:
        _, accounts = load_accounts(tmp_path, Session, required_accounts={source}, allow_edge=False)
        assert set(accounts) == {source}


@pytest.mark.parametrize(
    "users",
    [list(DEVELOPMENT_ACCOUNTS), list(ACCOUNTS)[:-1], list(ACCOUNTS) + [next(iter(ACCOUNTS))]],
)
def test_holdout_import_rejects_old_missing_or_duplicate_accounts_before_database_access(
    tmp_path, users
):
    manifest = ROOT.parents[1] / "data/manifests/synthetic-v0.2.yaml"
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "version": "synthetic-v0.2",
                "manifest_sha256": digest(manifest),
                "users": [{"user_id": u} for u in users],
            }
        )
    )

    def unexpected_database_access():
        raise AssertionError("Invalid imports must fail before querying a database")

    with pytest.raises(ValueError, match="exact-account"):
        load_accounts(
            tmp_path, unexpected_database_access, required_accounts=ACCOUNTS, allow_edge=False
        )
