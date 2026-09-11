import pytest

from services.config import load_config
from services.transcription.orchestration import build_execution_plan


def _ready_openai_config(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    return load_config()


def test_economy_plan_uses_one_cost_capped_primary_route(monkeypatch):
    cfg = _ready_openai_config(monkeypatch)

    plan = build_execution_plan(
        quality_mode="economy",
        primary_model_id="gpt-4o-mini-transcribe",
        duration_minutes=10,
        cfg=cfg,
        max_cost_usd=0.04,
    )

    assert plan["schema_version"] == "apma.transcription.execution-plan.v1"
    assert plan["policy"] == "primary_only"
    assert len(plan["routes"]) == 1
    assert plan["initial_estimated_cost_usd"] == pytest.approx(0.03)
    assert plan["maximum_estimated_cost_usd"] == pytest.approx(0.03)
    assert plan["initial_execution_allowed"] is True
    assert plan["full_plan_ready"] is True
    assert plan["network_calls_performed"] == 0
    assert "fake-openai-key" not in repr(plan)


def test_recommended_plan_exposes_ready_active_external_rescue_route(monkeypatch):
    cfg = _ready_openai_config(monkeypatch)
    monkeypatch.setenv("ENABLE_LIVE_GEMINI_TRANSCRIPTION", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_PRICE_PER_MINUTE_USD", "0.02")
    cfg = load_config()

    plan = build_execution_plan(
        quality_mode="recommended",
        primary_model_id="gpt-transcribe",
        duration_minutes=10,
        cfg=cfg,
        max_cost_usd=1.0,
    )

    assert [route["provider"] for route in plan["routes"]] == ["openai", "google"]
    assert [route["phase"] for route in plan["routes"]] == [
        "primary",
        "conditional_rescue",
    ]
    assert [route["required_for_mode_completion"] for route in plan["routes"]] == [
        True,
        False,
    ]
    assert plan["rescue_trigger_grades"] == ["amber", "red"]
    assert plan["initial_execution_allowed"] is True
    assert plan["full_plan_ready"] is True
    assert plan["mode_readiness_reasons"] == []
    assert "fake-" not in repr(plan)


def test_max_quality_requires_every_selected_route_and_worst_case_cap(monkeypatch):
    cfg = _ready_openai_config(monkeypatch)

    plan = build_execution_plan(
        quality_mode="max_quality",
        primary_model_id="gpt-4o-mini-transcribe",
        selected_model_ids=["gpt-4o-transcribe"],
        duration_minutes=60,
        cfg=cfg,
        max_cost_usd=0.20,
    )

    assert plan["initial_estimated_cost_usd"] == pytest.approx(0.18)
    assert plan["maximum_estimated_cost_usd"] == pytest.approx(0.54)
    assert plan["initial_execution_allowed"] is True
    assert plan["full_plan_ready"] is False
    assert any("Worst-case estimate" in reason for reason in plan["mode_readiness_reasons"])


def test_plan_rejects_unknown_mode_and_negative_values():
    cfg = load_config()

    with pytest.raises(ValueError, match="Unsupported quality mode"):
        build_execution_plan(
            quality_mode="fastest",
            primary_model_id="gpt-4o-mini-transcribe",
            duration_minutes=1,
            cfg=cfg,
        )
    with pytest.raises(ValueError, match="duration_minutes must be non-negative"):
        build_execution_plan(
            quality_mode="economy",
            primary_model_id="gpt-4o-mini-transcribe",
            duration_minutes=-1,
            cfg=cfg,
        )
