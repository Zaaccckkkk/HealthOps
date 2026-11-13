"""CPU contract tests for the v2 preference data contract; no model or GPU is involved."""

import copy
import json
import sys

import pytest

from writer_dpo import contract_v2 as v2
from writer_dpo.data import fingerprint
from writer_dpo.launch import trainable_prompt_sha256, verify_bundle

OLD, NEW = "old writer contract\n", "new writer contract with presentation rules\n"


def config():
    return {
        "writer_prompts": {
            "v3": {"fingerprint": fingerprint(OLD), "raw_sha256": "a", "status": "superseded"},
            "v4": {"fingerprint": fingerprint(NEW), "raw_sha256": "b", "status": "current"},
        },
        "writer_system_current": "v4",
    }


REG = v2.registry(config())


def packet(today="2026-10-04", value=6400):
    return {
        "today": today,
        "sources": [
            v2.daily_table(
                "e1.healthkit",
                "steps",
                "count",
                "healthkit",
                [("2026-10-03", value)],
                "daily_total",
                today=today,
            )
        ],
    }


def completion(answer, ids=("e1.healthkit",)):
    return [
        {"role": "assistant", "content": json.dumps({"answer": answer, "evidence_ids": list(ids)})}
    ]


def row(
    ident="draft-01",
    provenance="synthetic_independent",
    split="train",
    chosen="Apple Health recorded 6,400 steps on October 3.",
    rejected="Apple Health recorded 6,400 steps on 2026-10-03.",
    facts=None,
    system=NEW,
):
    facts = facts or packet()
    user = {
        "language": "en",
        "user_request": f"What was recorded on 2026-10-03? [{ident}]",
        "evidence": facts,
    }
    return {
        "schema_version": v2.CANDIDATE_SCHEMA,
        "id": ident,
        "split": "development",
        "proposed_split": split,
        "split_group": f"group-{ident}",
        "contract_blocked": False,
        "basic_validator_error": None,
        "provenance": provenance,
        "prompt_version": {"name": "v4", "fingerprint": fingerprint(NEW)},
        "prompt": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)},
        ],
        "evidence_packet": facts,
        "chosen": completion(chosen),
        "rejected": completion(rejected),
        "capture": {
            "trace_path": None,
            "trace_sha256": None,
            "captured_prompt_fingerprint": None,
            "captured_prompt_version": None,
            "captured_rejected": False,
        },
        "text_origin": "draft H/I columns of the review workbook",
        "review": {"workbook_sha256": "deadbeef", "sheet": "New pairs", "row": 2},
        "synthetic_additions": ["today = date + 1"],
    }


def isolation_file(tmp_path, entries):
    path = tmp_path / "holdout_ids.json"
    path.write_text(json.dumps({"schema": v2.ISOLATION_SCHEMA, "entries": entries}))
    return path


def isolated(tmp_path, *rows_):
    entries = [
        v2.isolation_entry(r["id"], "regression_test", r["prompt"], r["evidence_packet"], "fixture")
        for r in rows_
    ] or [unrelated_test_entry()]
    return v2.load_isolation(isolation_file(tmp_path, entries))


def unrelated_test_entry():
    """A regression case on another window, so ordinary fixtures do not collide with it."""
    facts = {
        "today": "2026-09-02",
        "sources": [
            v2.daily_table(
                "e1.google_health",
                "steps",
                "count",
                "google_health",
                [("2026-09-01", 1192)],
                "daily_total",
            )
        ],
    }
    prompt = [
        {"role": "system", "content": NEW},
        {
            "role": "user",
            "content": json.dumps(
                {"language": "en", "user_request": "holdout question", "evidence": facts}
            ),
        },
    ]
    return v2.isolation_entry("writer-holdout-x", "regression_test", prompt, facts, "fixture")


def no_origin(_row, _reg):
    return {}


# ----------------------------------------------------------------------------- registry
def test_registry_requires_a_registered_current_version():
    with pytest.raises(ValueError, match="not a registered"):
        v2.registry({"writer_prompts": {}, "writer_system_current": "v4"})
    bad = config()
    bad["writer_prompts"]["v4"]["status"] = "deployed"
    with pytest.raises(ValueError, match="Malformed"):
        v2.registry(bad)
    assert v2.prompt_version(OLD, REG) == "v3" and v2.prompt_version("other", REG) is None


@pytest.mark.parametrize("missing", ["fingerprint", "raw_sha256", "status"])
def test_registry_rejects_missing_required_field_even_with_extra_metadata(missing):
    bad = config()
    entry = bad["writer_prompts"]["v4"]
    del entry[missing]
    entry["deployment"] = "Additional metadata must not hide a missing required field"
    with pytest.raises(ValueError, match="Malformed"):
        v2.registry(bad)


# ----------------------------------------------------------------------------- packets
def test_builders_follow_the_production_summary_and_tie_rules():
    table = v2.daily_table(
        "e1.google_health",
        "resting_heart_rate",
        "bpm",
        "google_health",
        [("2026-10-24", 62), ("2026-10-25", None), ("2026-10-26", 65)],
        "daily_summary",
        today="2026-10-27",
    )
    assert table["statistic"] == "vendor daily summary"
    assert table["days"][1] == {
        "date": "2026-10-25",
        "value": None,
        "quality": "none",
        "basis": "none",
    }
    assert table["recorded_values_summary"] == {
        "minimum": "62.000000",
        "maximum": "65.000000",
        "mean_of_available_daily_values": "63.50",
        "first_to_last_change": "3.000000",
        "absolute_change": "3.000000",
        "change_direction": "increase",
        "first_date": "2026-10-24",
        "last_date": "2026-10-26",
        "not_a_complete_activity_or_health_assessment": True,
    }
    unusable = v2.daily_table(
        "e1.healthkit",
        "steps",
        "count",
        "healthkit",
        [("2026-10-28", None)],
        "none",
        ["overlapping_intervals"],
    )
    assert unusable["statistic"] == "no data" and unusable["days"][0]["quality"] == "unusable"
    tie = v2.reconciliation(
        "steps",
        "count",
        "2026-11-03",
        {"healthkit": 4800, "google_health": 5600},
        {"q05": 4420, "q50": 5200, "q95": 6060},
    )
    assert tie["comparison_to_estimate"]["tie"] is True
    assert tie["comparison_to_estimate"]["absolute_distances"] == {
        "healthkit": "400.000000",
        "google_health": "400.000000",
    }
    single = v2.reconciliation(
        "steps", "count", "2026-10-12", {"healthkit": 5200, "google_health": None}
    )
    assert single["comparison_possible"] is False and "model_estimate" not in single
    for item in (table, unusable, tie, single):
        v2.check_packet({"today": "2026-11-04", "sources": [item]})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["recorded_values_summary"].__setitem__("maximum", "9999.000000"),
        lambda s: s["days"].__setitem__(0, {**s["days"][0], "date": "2026-10-02"}),
        lambda s: s.__setitem__("statistic", "vendor daily total "),
        lambda s: s["days"][0].__setitem__("value", "6400"),
    ],
)
def test_tampered_daily_table_is_rejected(mutate):
    facts = packet()
    mutate(facts["sources"][0])
    with pytest.raises(ValueError, match="production builder"):
        v2.check_packet(facts)


def test_tampered_reconciliation_is_rejected():
    tie = v2.reconciliation(
        "steps",
        "count",
        "2026-11-03",
        {"healthkit": 4800, "google_health": 5600},
        {"q05": 4420, "q50": 5200, "q95": 6060},
    )
    tie["comparison_to_estimate"]["tie"] = False
    with pytest.raises(ValueError, match="production builder"):
        v2.check_packet({"today": "2026-11-04", "sources": [tie]})
    single = v2.reconciliation(
        "steps", "count", "2026-10-12", {"healthkit": 5200, "google_health": None}
    )
    single["model_estimate"] = {"q05": "1", "q50": "2", "q95": "3"}
    with pytest.raises(ValueError, match="production builder"):
        v2.check_packet({"today": "2026-10-13", "sources": [single]})


# ----------------------------------------------------------------------------- answers
def test_answer_warnings_cover_durations_dates_labels_and_numbers():
    sleep = {
        "today": "2026-10-16",
        "sources": [
            v2.daily_table(
                "e1.healthkit",
                "sleep_duration",
                "s",
                "healthkit",
                [("2026-10-15", 19470)],
                "interval_total_sum",
                today="2026-10-16",
            )
        ],
    }
    assert (
        v2.answer_warnings(
            "Apple Health recorded about 5 hours 25 minutes of sleep on October 15.", sleep
        )
        == []
    )
    warnings = v2.answer_warnings(
        "Apple Health recorded 5 hours 24 minutes, 19470 seconds, on 2026-10-15 "
        "(interval_total_sum); 42 steps.",
        sleep,
    )
    assert any("5h24m" in w for w in warnings)
    assert any("raw seconds" in w for w in warnings)
    assert any("ISO date" in w for w in warnings)
    assert any("interval_total_sum" in w for w in warnings)
    assert any("number 42" in w for w in warnings)
    assert v2.diff_spans("a b c", "a x c") == 1
    zh_a, zh_b = "\u53ea\u6709\u4e00\u4e2a\u6765\u6e90", "\u53ef\u7528channel\u4e0d\u8db3"
    assert v2.diff_spans(zh_a, zh_b) >= 1


# ----------------------------------------------------------------------------- rows
def test_synthetic_row_validates_and_text_origin_is_free_text(tmp_path):
    item = row()
    item["text_origin"] = "original F/G columns; H/I drafts not used"
    result, warnings = v2.validate_row(item, "train", REG, isolated(tmp_path))
    assert set(result) == {"prompt", "chosen", "rejected"} and warnings == []
    assert v2.verify_provenance(item, REG) == {}


def test_old_prompt_or_wrong_version_field_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="registered current"):
        v2.validate_row(row(system=OLD), "train", REG, isolated(tmp_path))
    item = row()
    item["prompt_version"]["name"] = "v3"
    with pytest.raises(ValueError, match="registered current"):
        v2.validate_row(item, "train", REG, isolated(tmp_path))


def test_synthetic_row_must_not_claim_a_capture():
    item = row()
    item["capture"]["trace_path"] = "/somewhere/.local/trace.json"
    with pytest.raises(ValueError, match="must not claim"):
        v2.verify_provenance(item, REG)
    del item["capture"]["trace_path"]
    item["synthetic_additions"] = None
    with pytest.raises(ValueError, match="synthetic_additions"):
        v2.verify_provenance(item, REG)


def replay_trace(tmp_path, item, raw, system=OLD, name="trace.json"):
    path = tmp_path / ".local" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "provenance": "reconstructed_synthetic_fixture_uploaded_via_API_not_cohort",
                "input_messages": [{"role": "system", "content": system}, item["prompt"][1]],
                "raw_model_output": raw,
            }
        )
    )
    item["capture"] = {
        "trace_path": str(path),
        "trace_sha256": v2.file_hash(path),
        "captured_prompt_fingerprint": fingerprint(system),
        "captured_prompt_version": v2.prompt_version(system, REG),
        "captured_rejected": True,
    }
    return path


def test_reused_old_prompt_capture_keeps_user_message_and_observed_output(tmp_path):
    item = row(provenance="captured_old_prompt_reused")
    raw = item["rejected"][0]["content"]
    path = replay_trace(tmp_path, item, raw)
    assert v2.verify_provenance(item, REG) == {
        "trace_sha256": v2.file_hash(path),
        "captured_prompt_version": "v3",
    }
    tampered = copy.deepcopy(item)
    tampered["rejected"][0]["content"] = json.dumps(
        {"answer": "invented", "evidence_ids": ["e1.healthkit"]}
    )
    with pytest.raises(ValueError, match="observed rejected"):
        v2.verify_provenance(tampered, REG)
    wrong_version = copy.deepcopy(item)
    wrong_version["capture"]["captured_prompt_version"] = "v4"
    with pytest.raises(ValueError, match="registered version"):
        v2.verify_provenance(wrong_version, REG)
    unregistered = row(provenance="captured_old_prompt_reused")
    replay_trace(tmp_path, unregistered, raw, system="never registered", name="unregistered.json")
    with pytest.raises(ValueError, match="registered version"):
        v2.verify_provenance(unregistered, REG)
    changed_user = copy.deepcopy(item)
    changed_user["prompt"][1] = {
        "role": "user",
        "content": json.dumps({"language": "en", "user_request": "other", "evidence": packet()}),
    }
    with pytest.raises(ValueError, match="User message differs"):
        v2.verify_provenance(changed_user, REG)
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="hash changed"):
        v2.verify_provenance(item, REG)


def test_edited_row_preserves_the_observed_output_for_audit(tmp_path):
    item = row(provenance="reviewed_edited")
    observed = json.dumps({"answer": "You were inactive.", "evidence_ids": ["e1.healthkit"]})
    replay_trace(tmp_path, item, observed)
    item["capture"].update(
        {
            "captured_rejected": False,
            "captured_rejected_text": observed,
            "edit_basis": "rejected_from_approved_chosen",
        }
    )
    assert v2.verify_provenance(item, REG)["captured_prompt_version"] == "v3"
    missing = copy.deepcopy(item)
    missing["capture"]["captured_rejected_text"] = "something else"
    with pytest.raises(ValueError, match="preserve the observed output"):
        v2.verify_provenance(missing, REG)
    no_basis = copy.deepcopy(item)
    no_basis["capture"]["edit_basis"] = ""
    with pytest.raises(ValueError, match="edit_basis"):
        v2.verify_provenance(no_basis, REG)


def test_archive_row_is_hashed_and_never_called_a_trace(tmp_path):
    archive = tmp_path / ".local" / "review.jsonl"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text("{}\n")
    item = row(provenance="archive_derived")
    item["archive"] = {"path": str(archive), "sha256": v2.file_hash(archive), "record": "row 12"}
    assert v2.verify_provenance(item, REG) == {"archive_sha256": v2.file_hash(archive)}
    item["capture"]["trace_path"] = str(archive)
    with pytest.raises(ValueError, match="must not claim a raw trace"):
        v2.verify_provenance(item, REG)


def test_trace_outside_local_directory_is_rejected(tmp_path):
    item = row(provenance="captured_old_prompt_reused")
    path = replay_trace(tmp_path, item, item["rejected"][0]["content"])
    elsewhere = tmp_path / "trace.json"
    elsewhere.write_text(path.read_text())
    item["capture"]["trace_path"] = str(elsewhere)
    with pytest.raises(ValueError, match="ignored .local"):
        v2.verify_provenance(item, REG)


# ----------------------------------------------------------------------------- isolation
def test_isolated_cases_block_identity_shared_evidence_and_near_duplicates(tmp_path):
    test_case = row("writer-holdout-v1-case", facts=packet(value=1192))
    index = isolated(tmp_path, test_case)
    same_question = row("other")
    same_question["prompt"][1] = test_case["prompt"][1]
    same_question["evidence_packet"] = test_case["evidence_packet"]
    with pytest.raises(ValueError, match="question_sha256 matches"):
        v2.validate_row(same_question, "train", REG, index)
    same_window = row("another", facts=packet(value=7))  # same metric/provider/window shape
    with pytest.raises(ValueError, match="evidence_signature matches"):
        v2.validate_row(same_window, "train", REG, index)
    different = row(
        "fresh",
        facts={
            "today": "2026-11-02",
            "sources": [
                v2.daily_table(
                    "e1.healthkit",
                    "steps",
                    "count",
                    "healthkit",
                    [("2026-11-01", 7)],
                    "daily_total",
                )
            ],
        },
    )
    different["chosen"] = completion("Apple Health recorded 7 steps on November 1.")
    different["rejected"] = completion("Apple Health recorded 7 steps on 2026-11-01.")
    v2.validate_row(different, "train", REG, index)


def test_malformed_isolation_list_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="malformed"):
        v2.load_isolation(isolation_file(tmp_path, [{"id": "x", "kind": "regression_test"}]))


def validation_row(ident="val"):
    facts = {
        "today": "2026-11-02",
        "sources": [
            v2.daily_table(
                "e1.healthkit", "steps", "count", "healthkit", [("2026-11-01", 7)], "daily_total"
            )
        ],
    }
    item = row(
        ident,
        split="validation",
        facts=facts,
        chosen="Apple Health recorded 7 steps on November 1.",
        rejected="Apple Health recorded 7 steps on 2026-11-01.",
    )
    return item


def test_cross_split_near_duplicate_chosen_or_shared_window_is_leakage(tmp_path):
    index = isolated(tmp_path)
    train, val = row("train"), validation_row()
    v2.inspect([train], [val], REG, index, provenance_check=no_origin)
    similar = validation_row("val2")
    similar["chosen"] = completion("Apple Health recorded 6,400 steps on October 3!")
    with pytest.raises(ValueError, match="near-duplicate chosen"):
        v2.inspect([train], [similar], REG, index, provenance_check=no_origin)
    shared = validation_row("val3")
    shared["evidence_packet"] = packet(value=1)
    shared["prompt"][1] = {
        "role": "user",
        "content": json.dumps(
            {"language": "en", "user_request": "q3", "evidence": packet(value=1)}
        ),
    }
    shared["chosen"] = completion("Apple Health recorded 1 steps on October 3.")
    shared["rejected"] = completion("Apple Health recorded 1 steps on 2026-10-03.")
    with pytest.raises(ValueError, match="signature"):
        v2.inspect([train], [shared], REG, index, provenance_check=no_origin)


def test_same_evidence_inside_one_split_is_reported_not_rejected(tmp_path):
    first, second = row("a"), row("b")
    report = v2.inspect(
        [first, second], [validation_row()], REG, isolated(tmp_path), provenance_check=no_origin
    )
    assert report[1]["notes"] == ["same evidence packet as a"] and report[0]["notes"] == []


# ----------------------------------------------------------------------------- approvals
def approve(report):
    return [
        {
            "id": r["id"],
            "split": r["split"],
            "pair_sha256": r["pair_sha256"],
            "synthetic_additions_sha256": r["synthetic_additions_sha256"],
            "acknowledged_warnings": r["warnings"],
            "decision": "approve",
            "reviewer": "fixture-reviewer",
        }
        for r in report
    ]


def test_any_content_origin_or_version_change_voids_the_approval(tmp_path):
    index = isolated(tmp_path)
    train, val = [row("a")], [validation_row()]
    report = v2.inspect(train, val, REG, index, provenance_check=no_origin)
    approvals = approve(report)
    for change in (
        lambda r: r["synthetic_additions"].append("q95 added"),
        lambda r: r.__setitem__("provenance", "archive_derived"),
        lambda r: r["chosen"][0].__setitem__(
            "content",
            json.dumps(
                {
                    "answer": "Apple Health recorded 6,400 steps on October 3!",
                    "evidence_ids": ["e1.healthkit"],
                }
            ),
        ),
        lambda r: r.__setitem__("archive", {"sha256": "x"}),
    ):
        changed = copy.deepcopy(train)
        change(changed[0])
        new_report = v2.inspect(changed, val, REG, index, provenance_check=no_origin)
        assert new_report[0]["pair_sha256"] != report[0]["pair_sha256"]
        with pytest.raises(ValueError, match="stale"):
            v2.export_bundle(
                changed,
                val,
                new_report,
                approvals,
                tmp_path / "out",
                REG,
                isolation_file(tmp_path, []),
                None,
            )


def test_unacknowledged_warnings_block_export(tmp_path):
    index = isolated(tmp_path)
    train = [
        row(
            "a",
            chosen="Apple Health recorded 6,400 steps on 2026-10-03 (daily_total).",
            rejected="Apple Health recorded 6,400 steps.",
        )
    ]
    report = v2.inspect(train, [validation_row()], REG, index, provenance_check=no_origin)
    assert report[0]["warnings"]
    approvals = approve(report)
    approvals[0]["acknowledged_warnings"] = []
    with pytest.raises(ValueError, match="stale"):
        v2.export_bundle(
            train,
            [validation_row()],
            report,
            approvals,
            tmp_path / "out",
            REG,
            isolation_file(tmp_path, []),
            None,
        )


def test_export_manifest_v2_is_accepted_by_launch_only_for_the_current_prompt(tmp_path):
    isolation = isolation_file(tmp_path, [unrelated_test_entry()])
    index = v2.load_isolation(isolation)
    train, val = [row("a"), row("b")], [validation_row()]
    report = v2.inspect(train, val, REG, index, provenance_check=no_origin)
    output = tmp_path / "bundle"
    manifest = v2.export_bundle(
        train, val, report, approve(report), output, REG, isolation, "archived-validation-sha"
    )
    assert manifest["schema"] == "healthops-writer-dpo-v2" and manifest["prompt_version"] == "v4"
    assert manifest["provenance_counts"] == {"synthetic_independent": 3}
    assert manifest["derived_from_bundle"] == "archived-validation-sha"
    verified = verify_bundle(output)
    assert verified["counts"] == {"train": 2, "validation": 1}
    assert trainable_prompt_sha256(config(), verified) == fingerprint(NEW)
    stale = dict(verified, prompt_version="v3")
    with pytest.raises(ValueError, match="different writer prompt"):
        trainable_prompt_sha256(config(), stale)
    assert (
        trainable_prompt_sha256(
            {"writer_system_sha256": "pinned"}, {"schema": "healthops-writer-dpo-v1"}
        )
        == "pinned"
    )
    with (output / "train.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="changed"):
        verify_bundle(output)


def test_cli_audit_writes_pending_approvals_not_an_export(tmp_path, monkeypatch):
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "train.candidates.jsonl").write_text(json.dumps(row("a")) + "\n")
    (candidates / "validation.candidates.jsonl").write_text(json.dumps(validation_row()) + "\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(NEW)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config()))
    isolation = isolation_file(tmp_path, [unrelated_test_entry()])
    output = tmp_path / ".local" / "audit"
    monkeypatch.setattr(v2, "CONFIG", cfg)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "contract_v2",
            "--candidates",
            str(candidates),
            "--system-prompt",
            str(prompt),
            "--output",
            str(output),
            "--isolation",
            str(isolation),
        ],
    )
    v2.main()
    drafts = json.loads((output / "approvals.draft.json").read_text())
    assert {d["decision"] for d in drafts} == {"pending"} and len(drafts) == 2
    assert not (output / "train.jsonl").exists()
    prompt.write_text(OLD)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "contract_v2",
            "--candidates",
            str(candidates),
            "--system-prompt",
            str(prompt),
            "--output",
            str(tmp_path / ".local" / "again"),
            "--isolation",
            str(isolation),
        ],
    )
    with pytest.raises(ValueError, match="registered current"):
        v2.main()


def test_empty_table_with_no_records_warning_is_production_shape():
    empty = {
        "evidence_id": "e3",
        "kind": "daily_table",
        "metric": "hrv_rmssd",
        "unit": "ms",
        "provider": None,
        "start_date": "2026-08-11",
        "end_date": "2026-08-17",
        "statistic": None,
        "warnings": ["no_records_in_window"],
        "includes_today": False,
        "days": [],
    }
    v2.check_packet({"today": "2026-08-18", "sources": [empty]})
    with pytest.raises(ValueError, match="production builder"):
        v2.check_packet(
            {"today": "2026-08-18", "sources": [dict(empty, statistic="vendor daily summary")]}
        )
    with pytest.raises(ValueError, match="production builder"):
        v2.check_packet({"today": "2026-08-18", "sources": [dict(empty, warnings=[])]})


def test_dates_followed_by_decimals_are_not_read_as_day_pairs():
    facts = {
        "today": "2026-08-18",
        "sources": [
            v2.daily_table(
                "e1.healthkit",
                "resting_heart_rate",
                "bpm",
                "healthkit",
                [("2026-08-12", 63.475058), ("2026-08-13", None), ("2026-08-14", 68.70151)],
                "sample_mean",
            )
        ],
    }
    answer = "Resting heart rate was 63.48 bpm on August 12 and 68.70 bpm on August 14."
    assert v2.answer_warnings(answer, facts) == []
    assert v2.answer_warnings("Readings on August 12 and 15 were similar.", facts) == [
        "date August 15 not in evidence"
    ]


def test_blocked_cases_block_identity_but_not_a_shared_window_shape(tmp_path):
    blocked = row("known-mixed_bases", facts=packet(value=1800))
    entry = v2.isolation_entry(
        blocked["id"], "blocked", blocked["prompt"], blocked["evidence_packet"], "fixture"
    )
    index = v2.load_isolation(isolation_file(tmp_path, [entry]))
    v2.validate_row(row("known-today_zh"), "train", REG, index)  # same window shape, different case
    with pytest.raises(ValueError, match="id matches isolated blocked"):
        v2.validate_row(row("known-mixed_bases"), "train", REG, index)
    same_evidence = row("other", facts=packet(value=1800))
    with pytest.raises(ValueError, match="evidence_fingerprint matches isolated blocked"):
        v2.validate_row(same_evidence, "train", REG, index)
