"""Unit tests for v0.4 Phase 3 multi-storm AR hindcast ensemble validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VAL = _load_module("validate_sf", "pipeline/03_validate_sf.py")


def test_aggregate_ensemble_311_metrics() -> None:
    events = [
        {
            "id": "ar1",
            "label": "AR 1",
            "scenario": "s1",
            "valid_land_points": 400,
            "by_buffer": [
                {
                    "radius_m": 0.0,
                    "ar_roc_auc": 0.56,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.20,
                            "control_rate": 0.05,
                            "lift": 4.0,
                        },
                        "0.1524m": {
                            "capture_rate": 0.12,
                            "control_rate": 0.03,
                            "lift": 4.0,
                        },
                    },
                },
                {
                    "radius_m": 25.0,
                    "ar_roc_auc": 0.68,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.70,
                            "control_rate": 0.35,
                            "lift": 2.0,
                        },
                        "0.1524m": {
                            "capture_rate": 0.50,
                            "control_rate": 0.25,
                            "lift": 2.0,
                        },
                    },
                },
                {
                    "radius_m": 50.0,
                    "ar_roc_auc": 0.66,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.84,
                            "control_rate": 0.60,
                            "lift": 1.4,
                        },
                        "0.1524m": {
                            "capture_rate": 0.72,
                            "control_rate": 0.48,
                            "lift": 1.5,
                        },
                    },
                },
            ],
        },
        {
            "id": "ar2",
            "label": "AR 2",
            "scenario": "s2",
            "valid_land_points": 200,
            "by_buffer": [
                {
                    "radius_m": 0.0,
                    "ar_roc_auc": 0.54,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.10,
                            "control_rate": 0.05,
                            "lift": 2.0,
                        },
                        "0.1524m": {
                            "capture_rate": 0.06,
                            "control_rate": 0.03,
                            "lift": 2.0,
                        },
                    },
                },
                {
                    "radius_m": 25.0,
                    "ar_roc_auc": 0.64,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.60,
                            "control_rate": 0.40,
                            "lift": 1.5,
                        },
                        "0.1524m": {
                            "capture_rate": 0.40,
                            "control_rate": 0.25,
                            "lift": 1.6,
                        },
                    },
                },
                {
                    "radius_m": 50.0,
                    "ar_roc_auc": 0.62,
                    "thresholds": {
                        "0.05m": {
                            "capture_rate": 0.80,
                            "control_rate": 0.64,
                            "lift": 1.25,
                        },
                        "0.1524m": {
                            "capture_rate": 0.68,
                            "control_rate": 0.50,
                            "lift": 1.36,
                        },
                    },
                },
            ],
        },
    ]

    agg = _VAL.aggregate_ensemble_311_metrics(events)
    assert agg["n_events"] == 2
    assert agg["total_311_points"] == 600
    mean_bufs = {b["radius_m"]: b for b in agg["ensemble_mean"]["by_buffer"]}
    assert mean_bufs[0.0]["ar_roc_auc"] == pytest.approx(0.55)
    assert mean_bufs[25.0]["ar_roc_auc"] == pytest.approx(0.66)
    assert mean_bufs[25.0]["thresholds"]["0.05m"]["capture_rate"] == pytest.approx(0.65)
    assert mean_bufs[25.0]["thresholds"]["0.05m"]["lift"] == pytest.approx(1.75)
    assert mean_bufs[50.0]["thresholds"]["0.1524m"]["lift"] == pytest.approx(1.43)


def test_aggregate_ensemble_311_metrics_empty_raises() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _VAL.aggregate_ensemble_311_metrics([])
