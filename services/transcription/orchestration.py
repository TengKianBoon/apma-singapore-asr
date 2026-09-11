"""Provider-neutral transcription quality-mode planning.

The planner is deliberately non-networked. It converts a user-selected quality
mode into an auditable route plan, computes initial and worst-case cost bounds,
and reports readiness without reading or returning credential values.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.transcription.models import configured_model_specs
from services.transcription.router import get_runtime_model_status


PLAN_SCHEMA = "apma.transcription.execution-plan.v1"
DEFAULT_RESCUE_PROVIDERS = {"google"}

QUALITY_MODES = {
    "economy": {
        "label": "Economy",
        "policy": "primary_only",
        "description": "Run one selected primary provider/model.",
    },
    "recommended": {
        "label": "Recommended",
        "policy": "primary_then_conditional_rescue",
        "description": "Run the primary route, then rescue only Amber or Red regions.",
    },
    "max_quality": {
        "label": "Max Quality / Benchmark",
        "policy": "selected_provider_benchmark",
        "description": "Run and retain all explicitly selected benchmark routes.",
    },
}


def _validate_nonnegative(value: float, field_name: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def _default_external_models(primary_model_id: str, cfg: Any) -> list[str]:
    """Choose at most one non-primary model per external provider family."""

    primary_provider = get_runtime_model_status(primary_model_id, cfg)["provider"]
    selected: list[str] = []
    seen_providers = {primary_provider}
    for spec in configured_model_specs(cfg):
        # New selectable providers do not silently join an established
        # recommended workflow. They remain available when explicitly chosen.
        if spec.provider not in DEFAULT_RESCUE_PROVIDERS:
            continue
        if spec.provider in seen_providers:
            continue
        seen_providers.add(spec.provider)
        selected.append(spec.model_id)
    return selected


def _ordered_model_ids(
    quality_mode: str,
    primary_model_id: str,
    selected_model_ids: Sequence[str] | None,
    cfg: Any,
) -> list[str]:
    if selected_model_ids is None:
        requested = [primary_model_id]
        if quality_mode != "economy":
            requested.extend(_default_external_models(primary_model_id, cfg))
    else:
        requested = [primary_model_id, *selected_model_ids]

    ordered: list[str] = []
    for model_id in requested:
        normalized = str(model_id).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    if quality_mode == "economy":
        return ordered[:1]
    return ordered


def build_execution_plan(
    *,
    quality_mode: str,
    primary_model_id: str,
    duration_minutes: float,
    cfg: Any,
    selected_model_ids: Sequence[str] | None = None,
    max_cost_usd: float | None = None,
) -> dict:
    """Build an auditable, cost-bounded plan without executing provider calls."""

    mode = str(quality_mode).strip().lower()
    if mode not in QUALITY_MODES:
        supported = ", ".join(QUALITY_MODES)
        raise ValueError(f"Unsupported quality mode {quality_mode!r}. Supported modes: {supported}")

    duration = _validate_nonnegative(duration_minutes, "duration_minutes")
    cap = _validate_nonnegative(
        cfg.max_cost_per_job_usd if max_cost_usd is None else max_cost_usd,
        "max_cost_usd",
    )
    model_ids = _ordered_model_ids(mode, primary_model_id, selected_model_ids, cfg)

    routes: list[dict] = []
    for index, model_id in enumerate(model_ids):
        status = get_runtime_model_status(model_id, cfg)
        if index == 0:
            phase = "primary"
        elif mode == "recommended":
            phase = "conditional_rescue"
        else:
            phase = "benchmark"

        price = status["price_per_minute_usd"]
        estimate = None if price is None else round(duration * float(price), 9)
        routes.append({
            "route_index": index,
            "phase": phase,
            "trigger": "always" if index == 0 or mode == "max_quality" else "amber_or_red_only",
            "required_for_initial_execution": index == 0,
            "required_for_mode_completion": index == 0 or mode in {"economy", "max_quality"},
            "model": status["model"],
            "provider": status["provider"],
            "provider_label": status["provider_label"],
            "provider_code": status["provider_code"],
            "response_format": status["response_format"],
            "diarization": status["diarization"],
            "price_per_minute_usd": price,
            "estimated_cost_usd": estimate,
            "billing_mode": status["billing_mode"],
            "cost_cap_included": status["cost_cap_included"],
            "billing_display": status["billing_display"],
            "runnable": status["runnable"],
            "readiness_reason": status["readiness_reason"],
        })

    primary = routes[0]
    all_prices_known = all(route["estimated_cost_usd"] is not None for route in routes)
    initial_estimate = primary["estimated_cost_usd"]
    maximum_estimate = (
        round(
            sum(
                float(route["estimated_cost_usd"])
                for route in routes
                if route["cost_cap_included"]
            ),
            9,
        )
        if all_prices_known
        else None
    )
    initial_cap_passed = initial_estimate is not None and float(initial_estimate) <= cap
    maximum_cap_passed = maximum_estimate is not None and float(maximum_estimate) <= cap
    all_routes_runnable = all(route["runnable"] for route in routes)

    blocking_reasons: list[str] = []
    if not primary["runnable"]:
        blocking_reasons.append(
            f"Primary route {primary['provider_code']} is not runnable: {primary['readiness_reason']}"
        )
    if initial_estimate is None:
        blocking_reasons.append("Primary route price is not configured.")
    elif not initial_cap_passed:
        blocking_reasons.append(
            f"Initial estimate ${initial_estimate:.6f} exceeds cap ${cap:.6f}."
        )

    mode_blockers = [
        f"{route['provider_code']}: {route['readiness_reason']}"
        for route in routes
        if not route["runnable"]
    ]
    if not all_prices_known:
        mode_blockers.append("At least one selected route has no configured price.")
    elif not maximum_cap_passed:
        mode_blockers.append(
            f"Worst-case estimate ${maximum_estimate:.6f} exceeds cap ${cap:.6f}."
        )

    return {
        "schema_version": PLAN_SCHEMA,
        "quality_mode": mode,
        "quality_mode_label": QUALITY_MODES[mode]["label"],
        "policy": QUALITY_MODES[mode]["policy"],
        "description": QUALITY_MODES[mode]["description"],
        "duration_minutes": duration,
        "max_cost_usd": cap,
        "rescue_trigger_grades": ["amber", "red"] if mode == "recommended" else [],
        "routes": routes,
        "initial_estimated_cost_usd": initial_estimate,
        "maximum_estimated_cost_usd": maximum_estimate,
        "initial_cost_cap_passed": initial_cap_passed,
        "maximum_cost_cap_passed": maximum_cap_passed,
        "initial_execution_allowed": bool(primary["runnable"] and initial_cap_passed),
        "full_plan_ready": bool(all_routes_runnable and maximum_cap_passed),
        "blocking_reasons": blocking_reasons,
        "mode_readiness_reasons": mode_blockers,
        "network_calls_performed": 0,
    }


__all__ = ["PLAN_SCHEMA", "QUALITY_MODES", "build_execution_plan"]
