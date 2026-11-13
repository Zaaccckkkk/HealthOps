"""Serving prompts are registered separately from immutable training prompt provenance."""

import hashlib
import json
from pathlib import Path

import pytest

from healthops.assistant import narrative

CONFIG = Path(__file__).resolve().parents[2] / "model-training" / "writer_dpo" / "config.json"


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.skipif(not CONFIG.is_file(), reason="model-training config not checked out")
def test_service_prompt_is_the_registered_current_version():
    config = json.loads(CONFIG.read_text())
    current = config.get("writer_system_serving", config["writer_system_current"])
    assert narrative.PROMPT_VERSION == current
    assert fingerprint(narrative.SYSTEM) == config["writer_prompts"][current]["fingerprint"]
    assert (
        hashlib.sha256(narrative.SYSTEM.encode()).hexdigest()
        == config["writer_prompts"][current]["raw_sha256"]
    )
    for name, text in narrative.SYSTEM_VERSIONS.items():
        assert fingerprint(text) == config["writer_prompts"][name]["fingerprint"]


def test_writer_calls_are_recognized_for_every_registered_version():
    for text in narrative.SYSTEM_VERSIONS.values():
        assert narrative.is_writer_system({"role": "system", "content": text})
    assert narrative.messages("q", {"sources": []}, "en")[0] == {
        "role": "system",
        "content": narrative.SYSTEM,
    }
    assert not narrative.is_writer_system({"role": "system", "content": "Route this question"})
    assert not narrative.is_writer_system({"role": "user", "content": narrative.SYSTEM})


def test_v4_prompt_adds_presentation_rules_without_dropping_v3_safety_rules():
    v3, v4 = narrative.SYSTEM_V3, narrative.SYSTEM_V4
    assert "Presentation rules for the user-facing answer" in v4
    assert "2-5 short sentences" in v3 and "FIRST concisely" in v4
    for rule in (
        "closeness to an estimate is\nnot accuracy",
        "Do not calculate new statistics",
        "Return ONLY a JSON object",
    ):
        assert rule in v3 and rule in v4


def test_v5_uses_precomputed_text_and_preserves_training_prompt():
    assert "do not recalculate seconds" in narrative.SYSTEM_V5
    assert "Apple Health for healthkit or HealthKit" in narrative.SYSTEM_V5
    assert "unless the user explicitly requests seconds" in narrative.SYSTEM_V4
    assert "Only when the user explicitly requests seconds" in narrative.SYSTEM_V5
    assert "minimum, maximum, or mean is not automatically a first or last observation" in (
        narrative.SYSTEM_V5
    )
    if CONFIG.is_file():
        config = json.loads(CONFIG.read_text())
        assert config["writer_system_current"] == "grounded-answer-v4-presentation-rules"
        assert config["writer_prompts"][narrative.PROMPT_VERSION]["status"] == "candidate"
