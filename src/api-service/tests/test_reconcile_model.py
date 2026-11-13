"""CPU serving parity and artifact integrity; no cloud calls or clinical accuracy claims."""

import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from healthops.reconciliation.model import TransformerPredictor, build_net, feature_rows


def test_checkpoint_load_integrity_and_one_model_window(tmp_path):
    torch = pytest.importorskip("torch")
    cfg = dict(d_model=8, heads=2, layers=1, ff=16, dropout=0.0, window=14)
    torch.manual_seed(42)
    net = build_net(cfg)
    torch.save(net.state_dict(), tmp_path / "transformer.pt")
    scaler = {k: {"mean": 100.0, "sd": 1.0} for k in ("sleep", "steps", "rhr")}
    scaler["steps"]["mean"] = 8.0
    (tmp_path / "meta.json").write_text(json.dumps({"cfg": cfg, "scaler": scaler}))
    manifest = {
        "model_version": "fixture",
        "data_version": "synthetic-fixture",
        "sha256": {
            n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
            for n in ("meta.json", "transformer.pt")
        },
    }
    (tmp_path / "serving.json").write_text(json.dumps(manifest))
    model = TransformerPredictor(str(tmp_path))
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(14)]
    q = model.predict(days, {}, days[0], "resting_heart_rate")
    assert len(q) == 3 and 0 <= q[0] <= q[1] <= q[2]
    (tmp_path / "meta.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum mismatch"):
        TransformerPredictor(str(tmp_path))


def test_training_architecture_and_feature_order_parity():
    torch = pytest.importorskip("torch")
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    training_path = Path(__file__).resolve().parents[2] / "model-training"
    if not training_path.exists():
        pytest.skip("Training source is checked locally, not shipped in the API container")
    sys.path.insert(0, str(training_path))
    from training.transformer_model import Net
    from training.windows import Scaler, day_matrix

    cfg = dict(d_model=8, heads=2, layers=1, ff=16, dropout=0.0, window=14)
    training = Net(cfg).eval()
    serving = build_net(cfg).eval()
    serving.load_state_dict(training.state_dict(), strict=True)
    x = torch.randn(2, 14, 30)
    with torch.inference_mode():
        assert torch.equal(training(x), serving(x))
    day = date(2026, 8, 20)
    stats = {k: {"mean": 10.0, "sd": 2.0} for k in ("sleep", "steps", "rhr")}
    row = dict(
        context_stress="low",
        context_workout="none",
        context_travel=False,
        context_illness=False,
        context_subjective_fatigue=3,
        day_of_week=day.weekday(),
    )
    readings = {}
    for metric, short, value in [
        ("sleep_duration", "sleep", 25200),
        ("steps", "steps", 7000),
        ("resting_heart_rate", "rhr", 65),
    ]:
        row[f"truth_{short}"] = 0.0  # targets are irrelevant to serving features
        for channel, p in [("healthkit", "hk"), ("google_health", "gh")]:
            val = value if p == "hk" else None
            row[f"obs_{p}_{short}"] = (
                float("nan") if val is None else val / (60 if short == "sleep" else 1)
            )
            readings[(day, metric, channel)] = val
    expected = day_matrix(pd.DataFrame([row]), Scaler(stats=stats))[0]
    actual = feature_rows([day], readings, stats, day)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
