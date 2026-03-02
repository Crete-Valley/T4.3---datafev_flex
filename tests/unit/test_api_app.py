from __future__ import annotations

import asyncio
import io
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("fastapi")
from fastapi import UploadFile


class _FakeRequest:
    def __init__(self, content_type: str, payload: dict):
        self.headers = {"content-type": content_type}
        self._payload = payload

    async def json(self):
        return self._payload

    async def form(self):
        return self._payload


def _make_stage1_artifacts():
    planning_start = datetime(2024, 1, 1, 8, 0)
    planning_end = datetime(2024, 1, 1, 8, 30)
    time_step = timedelta(minutes=15)
    idx = pd.DatetimeIndex([planning_start, planning_start + time_step])

    return SimpleNamespace(
        planning_start=planning_start,
        planning_end=planning_end,
        time_step=time_step,
        output_dir="outputs/flex_potential_estimation",
        cluster_capability_summary={"1": {"downward_capability_kWh": 1.0, "upward_capability_kWh": 1.0}},
        cluster_capability_ts={
            "1": pd.DataFrame(
                {
                    "downward_capability_kW": [1.0, 1.0],
                    "upward_capability_kW": [1.0, 1.0],
                },
                index=idx,
            )
        },
    )


def test_stage1_endpoint_returns_stage1_id(monkeypatch):
    import api.app as app_module
    from api.cache import TTLObjectCache

    monkeypatch.setattr(app_module, "stage1_cache", TTLObjectCache(ttl_seconds=60, max_items=16))
    captured: dict = {}

    def _fake_stage1(**kwargs):
        captured.update(kwargs)
        artifacts = _make_stage1_artifacts()
        artifacts.output_dir = os.path.join(kwargs["output_dir"], "flex_potential_estimation")
        return artifacts

    monkeypatch.setattr(
        app_module,
        "run_flex_potential_estimation",
        _fake_stage1,
    )

    upload = UploadFile(filename="sample.xlsx", file=io.BytesIO(b"dummy excel bytes"))
    options = app_module.FlexPotentialEstimationOptions(output_dir="outputs")
    payload = app_module.flex_potential_estimation(input_file=upload, options=options)

    stage1_id = payload["stage1_id"]
    expected_job_root = os.path.join(os.path.abspath("outputs"), "jobs", stage1_id)
    assert stage1_id
    assert payload["cluster_count"] == 1
    assert payload["job_output_root"] == expected_job_root
    assert captured["output_dir"] == expected_job_root
    assert app_module.stage1_cache.get(stage1_id) is not None


def test_stage2_multipart_with_setpoint_file_uses_parser_and_clears_cache(monkeypatch):
    import api.app as app_module
    from api.cache import TTLObjectCache

    cache = TTLObjectCache(ttl_seconds=60, max_items=16)
    monkeypatch.setattr(app_module, "stage1_cache", cache)
    stage1_artifacts = _make_stage1_artifacts()
    stage1_id = cache.put(stage1_artifacts)

    parsed_commands = {
        "1": pd.Series(
            [0.0, 0.0],
            index=stage1_artifacts.cluster_capability_ts["1"].index,
            dtype=float,
        )
    }
    monkeypatch.setattr(
        app_module,
        "parse_stage2_setpoints_sheet",
        lambda *_args, **_kwargs: parsed_commands,
    )

    stage2_artifacts = SimpleNamespace(
        output_dir="outputs/flex_aware_smart_charge_scheduling",
        cluster_tracking_summary={"1": {"match_ratio": 1.0}},
        command_status=pd.DataFrame(
            [{"cluster_id": "1", "status": "accepted", "reason": "", "detail": "ok"}]
        )
    )
    monkeypatch.setattr(
        app_module,
        "run_flex_aware_smart_charge_scheduling",
        lambda **kwargs: stage2_artifacts,
    )

    upload = UploadFile(filename="setpoints.xlsx", file=io.BytesIO(b"dummy bytes"))
    req = _FakeRequest(
        content_type="multipart/form-data",
        payload={
            "stage1_id": stage1_id,
            "command_strategy": "midpoint",
            "setpoints_file": upload,
        },
    )

    payload = asyncio.run(app_module.flex_aware_smart_charge_scheduling(req))
    assert payload["accepted_clusters"] == ["1"]
    assert payload["tracking_mode"] == "strict"
    assert payload["match_tolerance_kw"] == pytest.approx(1e-3)
    assert payload["cluster_tracking_summary"]["1"]["match_ratio"] == pytest.approx(1.0)
    assert cache.get(stage1_id) is None


def test_stage2_unknown_stage1_id_returns_404():
    import api.app as app_module
    from api.cache import TTLObjectCache

    app_module.stage1_cache = TTLObjectCache(ttl_seconds=60, max_items=16)
    req = _FakeRequest(
        content_type="application/json",
        payload={"stage1_id": "missing-id", "command_strategy": "midpoint"},
    )

    with pytest.raises(app_module.HTTPException) as exc:
        asyncio.run(app_module.flex_aware_smart_charge_scheduling(req))
    assert exc.value.status_code == 404


def test_stage2_json_flex_band_payload_is_forwarded(monkeypatch):
    import api.app as app_module
    from api.cache import TTLObjectCache

    cache = TTLObjectCache(ttl_seconds=60, max_items=16)
    monkeypatch.setattr(app_module, "stage1_cache", cache)
    stage1_artifacts = _make_stage1_artifacts()
    stage1_id = cache.put(stage1_artifacts)

    captured: dict = {}

    def _fake_stage2(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_dir="outputs/flex_aware_smart_charge_scheduling",
            cluster_tracking_summary={"1": {"match_ratio": 1.0}},
            command_status=pd.DataFrame(
                [{"cluster_id": "1", "status": "accepted", "reason": "", "detail": "ok"}]
            )
        )

    monkeypatch.setattr(app_module, "run_flex_aware_smart_charge_scheduling", _fake_stage2)

    req = _FakeRequest(
        content_type="application/json",
        payload={
            "stage1_id": stage1_id,
            "command_type": "flex_band",
            "tracking_mode": "best_effort",
            "match_tolerance_kw": 0.25,
            "commands_by_cluster": {
                "1": [
                    {"timestamp": "2024-01-01T08:00:00", "p_min_kw": -1.0, "p_max_kw": 0.0},
                    {"timestamp": "2024-01-01T08:15:00", "p_min_kw": -1.0, "p_max_kw": 0.0},
                ]
            },
        },
    )

    payload = asyncio.run(app_module.flex_aware_smart_charge_scheduling(req))
    assert payload["accepted_clusters"] == ["1"]
    assert captured["command_type"] == "flex_band"
    assert captured["tracking_mode"] == "best_effort"
    assert captured["match_tolerance_kw"] == pytest.approx(0.25)
    assert isinstance(captured["commands_by_cluster"]["1"], pd.DataFrame)
    assert list(captured["commands_by_cluster"]["1"].columns) == ["p_min_kw", "p_max_kw"]


def test_stage2_json_flex_band_rejects_p_set_kw():
    import api.app as app_module
    from api.cache import TTLObjectCache

    app_module.stage1_cache = TTLObjectCache(ttl_seconds=60, max_items=16)
    req = _FakeRequest(
        content_type="application/json",
        payload={
            "stage1_id": "dummy",
            "command_type": "flex_band",
            "commands_by_cluster": {
                "1": [
                    {
                        "timestamp": "2024-01-01T08:00:00",
                        "p_min_kw": -1.0,
                        "p_max_kw": 0.0,
                        "p_set_kw": -0.5,
                    }
                ]
            },
        },
    )

    with pytest.raises(app_module.HTTPException) as exc:
        asyncio.run(app_module.flex_aware_smart_charge_scheduling(req))
    assert exc.value.status_code == 400
    assert "must not include 'p_set_kw'" in str(exc.value.detail)
