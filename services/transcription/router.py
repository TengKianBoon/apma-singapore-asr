"""Provider readiness and pricing for transcription routes.

This module is deliberately non-networked. It gives the dashboard and future
orchestrators one place to decide whether a configured route is actually ready
for an explicitly confirmed live request.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from services.transcription.models import TranscriptionModelSpec, configured_model_specs, get_model_spec


def _hostname(url: str) -> str:
    return str(urlsplit(str(url)).hostname or "").lower()


def _is_beijing_dashscope_api(url: str) -> bool:
    host = _hostname(url)
    return host == "dashscope.aliyuncs.com" or host.endswith(
        ".cn-beijing.maas.aliyuncs.com"
    )


def _configured_price(spec: TranscriptionModelSpec, cfg: Any) -> float | None:
    if spec.provider == "openai":
        value = cfg.openai_price_per_minute.get(spec.model_id)
    elif spec.provider == "meralion":
        billing_mode = str(
            getattr(cfg, "meralion_billing_mode", "trial_free")
        ).strip().lower()
        value = 0.0 if billing_mode == "trial_free" else cfg.meralion_price_per_minute_usd
    elif spec.provider == "google":
        value = (
            cfg.gemini_transcribe_price_per_minute_usd
            if spec.provider_code == "Gem35T"
            else cfg.gemini_price_per_minute_usd
        )
    elif spec.provider == "alibaba":
        value = cfg.qwen_filetrans_price_per_minute_usd
    else:
        value = None
    return None if value is None else float(value)


def get_runtime_model_status(model_id: str, cfg: Any) -> dict:
    """Return provider-neutral readiness without exposing any credential."""

    spec = get_model_spec(model_id, cfg)
    gate_enabled = bool(getattr(cfg, spec.enabled_config_attr, False))
    credential_available = bool(getattr(cfg, spec.credential_config_attr, None))
    qwen_staging_mode = None
    qwen_staging_supported = True
    if spec.provider == "alibaba":
        qwen_staging_mode = str(
            getattr(cfg, "qwen_filetrans_staging_mode", "private_oss")
        ).strip().lower()
        if qwen_staging_mode == "data_uri":
            credential_available = bool(getattr(cfg, "dashscope_api_key", None))
        elif qwen_staging_mode == "dashscope_temporary":
            credential_available = bool(getattr(cfg, "dashscope_api_key", None))
            qwen_staging_supported = bool(
                _is_beijing_dashscope_api(getattr(cfg, "dashscope_api_base_url", ""))
                and _hostname(
                    getattr(cfg, "dashscope_temporary_upload_api_base_url", "")
                )
                == "dashscope.aliyuncs.com"
            )
        else:
            credential_available = all(
                bool(getattr(cfg, name, None))
                for name in (
                    "dashscope_api_key",
                    "aliyun_oss_access_key_id",
                    "aliyun_oss_access_key_secret",
                    "aliyun_oss_endpoint",
                    "aliyun_oss_bucket",
                )
            )
    price = _configured_price(spec, cfg)
    pricing_configured = price is not None
    billing_mode = (
        str(getattr(cfg, "meralion_billing_mode", "trial_free")).strip().lower()
        if spec.provider == "meralion"
        else "metered"
    )
    cost_cap_included = not (
        spec.provider == "meralion" and billing_mode == "trial_free"
    )
    billing_display = (
        "MERaLiON M3ASR — trial/free access; $0 billable"
        if not cost_cap_included
        else (
            f"Metered at ${price:g}/min"
            if price is not None
            else "Metered pricing not configured"
        )
    )
    runnable = bool(
        spec.live_adapter_ready
        and gate_enabled
        and credential_available
        and qwen_staging_supported
        and pricing_configured
    )

    if not spec.live_adapter_ready:
        readiness_reason = "Live adapter is not implemented; setup is required."
    elif not gate_enabled:
        readiness_reason = "Provider live gate is disabled."
    elif not credential_available:
        readiness_reason = (
            (
                "A Beijing-region Model Studio API key is required for "
                "DashScope temporary upload."
                if qwen_staging_mode == "dashscope_temporary"
                else (
                    "A Model Studio API key is required for bounded data-URI input."
                    if qwen_staging_mode == "data_uri"
                    else "Secure Model Studio and private OSS setup is incomplete."
                )
            )
            if spec.provider == "alibaba"
            else "Provider credential is not available in the server environment."
        )
    elif not qwen_staging_supported:
        readiness_reason = (
            "DashScope temporary upload is Beijing-only; use a Beijing task endpoint "
            "or switch to private_oss for Singapore."
        )
    elif not pricing_configured:
        readiness_reason = "Provider pricing is not configured."
    elif not cost_cap_included:
        readiness_reason = "Ready for an explicitly confirmed, quota-limited trial run."
    else:
        readiness_reason = "Ready for an explicitly confirmed, cost-capped live run."

    return {
        "model": spec.model_id,
        "provider": spec.provider,
        "provider_label": spec.provider_label,
        "provider_code": spec.provider_code,
        "tier_label": spec.tier_label,
        "description": spec.description,
        "response_format": spec.response_format,
        "diarization": spec.diarization,
        "price_per_minute_usd": price,
        "billing_mode": billing_mode,
        "cost_cap_included": cost_cap_included,
        "billing_display": billing_display,
        "quota_note": (
            "Provider quota, tier, rate, and availability limits remain separate "
            "from USD budget limits."
            if not cost_cap_included
            else None
        ),
        "pricing_basis": spec.pricing_basis,
        "pricing_source_url": spec.pricing_source_url,
        "pricing_note": spec.pricing_note,
        "live_adapter_ready": spec.live_adapter_ready,
        "gate_enabled": gate_enabled,
        "credential_available": credential_available,
        "staging_mode": qwen_staging_mode,
        "pricing_configured": pricing_configured,
        "runnable": runnable,
        "readiness_reason": readiness_reason,
    }


def configured_runtime_models(cfg: Any) -> list[dict]:
    return [get_runtime_model_status(spec.model_id, cfg) for spec in configured_model_specs(cfg)]


__all__ = ["configured_runtime_models", "get_runtime_model_status"]
