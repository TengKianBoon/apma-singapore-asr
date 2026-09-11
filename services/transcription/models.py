"""Stable registry for selectable transcription models.

The registry separates product roles from provider model identifiers so model
selection, pricing, response formats, and diarization behavior cannot drift
independently across the dashboard and service layer.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TranscriptionModelSpec:
    model_id: str
    provider: str
    provider_label: str
    provider_code: str
    tier_label: str
    description: str
    response_format: str
    diarization: bool = False
    pricing_basis: str = "unconfigured"
    pricing_source_url: str | None = None
    pricing_note: str = ""
    live_adapter_ready: bool = True
    enabled_config_attr: str = ""
    credential_config_attr: str = ""


def _all_model_specs(cfg: Any) -> tuple[TranscriptionModelSpec, ...]:
    """Return active and historical adapter specs for internal compatibility."""

    meralion_trial_free = (
        str(getattr(cfg, "meralion_billing_mode", "trial_free")).strip().lower()
        == "trial_free"
    )

    candidates = (
        TranscriptionModelSpec(
            model_id=cfg.openai_default_model,
            provider="openai",
            provider_label="OpenAI",
            provider_code="gpt4oMini",
            tier_label="Economy",
            description="Lowest-cost default for routine transcription.",
            response_format="json",
            pricing_basis="official_per_audio_minute",
            pricing_source_url="https://developers.openai.com/api/docs/pricing",
            pricing_note="Official audio-minute rate.",
            enabled_config_attr="enable_live_openai_transcription",
            credential_config_attr="openai_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.openai_recommended_model,
            provider="openai",
            provider_label="OpenAI",
            provider_code="gptTr",
            tier_label="Recommended Multilingual",
            description="Recommended option for multilingual and code-switched meetings.",
            response_format="json",
            pricing_basis="official_per_audio_minute",
            pricing_source_url="https://developers.openai.com/api/docs/models/gpt-transcribe",
            pricing_note="Official estimated audio-minute rate.",
            enabled_config_attr="enable_live_openai_transcription",
            credential_config_attr="openai_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.openai_premium_model,
            provider="openai",
            provider_label="OpenAI",
            provider_code="gpt4oTr",
            tier_label="High Accuracy",
            description="Higher-accuracy transcription without speaker attribution.",
            response_format="json",
            pricing_basis="operator_estimate",
            pricing_source_url="https://developers.openai.com/api/docs/pricing",
            pricing_note="Configurable estimate; provider billing is token based.",
            enabled_config_attr="enable_live_openai_transcription",
            credential_config_attr="openai_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.openai_diarize_model,
            provider="openai",
            provider_label="OpenAI",
            provider_code="gpt4oDiarz",
            tier_label="Speaker Labels",
            description="Transcription with provider speaker labels and timestamps.",
            response_format="diarized_json",
            diarization=True,
            pricing_basis="operator_estimate",
            pricing_source_url="https://developers.openai.com/api/docs/pricing",
            pricing_note="Configurable estimate; provider billing is token based.",
            enabled_config_attr="enable_live_openai_transcription",
            credential_config_attr="openai_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.meralion_transcription_model,
            provider="meralion",
            provider_label="MERaLiON",
            provider_code="M3ASR",
            tier_label="SG / Hokkien Specialist",
            description="Hosted specialist route for regional multilingual and code-switched audio.",
            response_format="json",
            pricing_basis=(
                "limited_time_free_trial"
                if meralion_trial_free
                else "operator_configured_metered"
            ),
            pricing_source_url=(
                "https://www.imda.gov.sg/about-imda/emerging-technologies-and-research/"
                "national-multimodal-llm-programme"
                if meralion_trial_free
                else "https://docs.meralion.ai/"
            ),
            pricing_note=(
                "Trial access — no metered USD charge configured; provider quota, "
                "tier, rate, and availability limits remain separate."
                if meralion_trial_free
                else "Operator-configured metered price participates in the paid-provider cap."
            ),
            live_adapter_ready=True,
            enabled_config_attr="enable_live_meralion_transcription",
            credential_config_attr="meralion_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.gemini_flash_model,
            provider="google",
            provider_label="Google Gemini",
            provider_code="Gem37F",
            tier_label="Benchmark / Fallback",
            description="Low-thinking independent benchmark and fallback route.",
            response_format="json",
            pricing_basis="operator_estimate",
            pricing_source_url="https://ai.google.dev/gemini-api/docs/pricing",
            pricing_note="Configurable audio-minute estimate used for the hard run cap.",
            live_adapter_ready=True,
            enabled_config_attr="enable_live_gemini_transcription",
            credential_config_attr="gemini_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.gemini_transcribe_model,
            provider="google",
            provider_label="Google Gemini",
            provider_code="Gem35T",
            tier_label="Native Transcription",
            description="Native audio transcription with diarization and word timestamps.",
            response_format="diarized_json",
            diarization=True,
            pricing_basis="operator_estimate",
            pricing_source_url="https://ai.google.dev/gemini-api/docs/pricing",
            pricing_note="Configurable audio-minute estimate used for the hard run cap.",
            live_adapter_ready=True,
            enabled_config_attr="enable_live_gemini_transcription",
            credential_config_attr="gemini_api_key",
        ),
        TranscriptionModelSpec(
            model_id=cfg.qwen_filetrans_model,
            provider="alibaba",
            provider_label="Alibaba Cloud Qwen",
            provider_code="QwenA3FT",
            tier_label="Long-file Multilingual + Speakers",
            description=(
                "Long-file transcription with native word timestamps and speaker "
                "diarization, including Hokkien and regional Chinese varieties."
            ),
            response_format="diarized_json",
            diarization=True,
            pricing_basis="official_per_audio_second_converted_to_minute",
            pricing_source_url=(
                "https://www.alibabacloud.com/help/en/model-studio/model-pricing"
            ),
            pricing_note=(
                "Official Singapore rate converted from USD 0.000035 per audio second; "
                "free quota is not assumed by APMA's safety cap."
            ),
            live_adapter_ready=True,
            enabled_config_attr="enable_live_qwen_filetrans_transcription",
            credential_config_attr="dashscope_api_key",
        ),
    )

    seen: dict[str, str] = {}
    seen_codes: set[str] = set()
    result: list[TranscriptionModelSpec] = []
    for spec in candidates:
        model_id = str(spec.model_id).strip()
        if not model_id:
            raise ValueError(f"Transcription model ID is empty for tier: {spec.tier_label}")
        if model_id in seen:
            raise ValueError(
                "Duplicate transcription model ID "
                f"{model_id!r} configured for {seen[model_id]!r} and {spec.tier_label!r}"
            )
        seen[model_id] = spec.tier_label
        if spec.provider_code in seen_codes:
            raise ValueError(f"Duplicate transcription provider code {spec.provider_code!r}")
        seen_codes.add(spec.provider_code)
        result.append(spec)
    return tuple(result)


def configured_model_specs(cfg: Any) -> tuple[TranscriptionModelSpec, ...]:
    """Return only models selectable in the current APMA application."""

    removed_model_ids = {
        str(cfg.gemini_flash_model).strip(),
    }
    return tuple(
        spec for spec in _all_model_specs(cfg) if spec.model_id not in removed_model_ids
    )


def get_model_spec(model_id: str, cfg: Any) -> TranscriptionModelSpec:
    # Historical adapters and retained artifacts still need their exact model
    # metadata, even when a retired route is no longer exposed by the app.
    for spec in _all_model_specs(cfg):
        if spec.model_id == model_id:
            return spec
    supported = ", ".join(spec.model_id for spec in configured_model_specs(cfg))
    raise ValueError(
        f"Unsupported transcription model {model_id!r}. Supported models: {supported}"
    )


def is_diarization_model(model_id: str, cfg: Any) -> bool:
    return get_model_spec(model_id, cfg).diarization


def configure_model_chunking(base_cfg: Any, model_id: str) -> Any:
    """Return a copy with conservative request limits for one live model.

    Long recordings remain supported by producing more bounded chunks.  The
    source duration is not shortened and the common overlap/timestamp logic is
    unchanged.
    """

    cfg = copy(base_cfg)
    spec = get_model_spec(model_id, cfg)
    if spec.provider == "alibaba":
        staging_mode = str(cfg.qwen_filetrans_staging_mode).strip().lower()
        if staging_mode == "data_uri":
            safe_duration = int(getattr(cfg, "provider_safe_chunk_duration_sec", 300))
            request_limit = min(
                int(cfg.qwen_filetrans_file_size_limit_bytes),
                int(cfg.qwen_filetrans_data_uri_limit_bytes),
            )
            if safe_duration <= 0 or request_limit <= 0:
                raise ValueError("Qwen data-URI chunk limits must be positive")
            cfg.default_chunk_duration_sec = min(
                int(cfg.default_chunk_duration_sec), safe_duration
            )
            cfg.max_chunk_duration_sec = min(
                int(cfg.max_chunk_duration_sec), safe_duration
            )
            cfg.min_chunk_duration_sec = min(
                int(cfg.min_chunk_duration_sec),
                max(1, int(cfg.default_chunk_duration_sec) // 2),
            )
            cfg.target_max_chunk_bytes = min(
                int(cfg.target_max_chunk_bytes), request_limit
            )
            cfg.hard_max_chunk_bytes = min(
                int(cfg.hard_max_chunk_bytes), request_limit
            )
            cfg.target_max_chunk_bytes = min(
                int(cfg.target_max_chunk_bytes), int(cfg.hard_max_chunk_bytes)
            )
            return cfg
        safe_duration = int(cfg.qwen_filetrans_max_diarized_duration_sec)
        if safe_duration <= 0:
            raise ValueError("qwen_filetrans_max_diarized_duration_sec must be positive")
        # Aim below the two-hour diarization ceiling so silence-boundary search
        # and overlap never push a request beyond the documented limit.
        cfg.default_chunk_duration_sec = min(safe_duration, 110 * 60)
        cfg.max_chunk_duration_sec = safe_duration
        cfg.min_chunk_duration_sec = min(
            int(cfg.min_chunk_duration_sec),
            int(cfg.default_chunk_duration_sec),
        )
        request_limit = int(cfg.qwen_filetrans_file_size_limit_bytes)
        if request_limit <= 0:
            raise ValueError("Request byte limit is not positive for QwenA3FT")
        # The generic 20/24 MiB defaults protect inline/base64 providers and
        # would unnecessarily split long Filetrans requests. A two-hour mono
        # canonical WAV is normally well below this guarded OSS staging limit.
        cfg.target_max_chunk_bytes = min(request_limit, 512 * 1024 * 1024)
        cfg.hard_max_chunk_bytes = min(request_limit, 768 * 1024 * 1024)
        return cfg

    safe_duration = int(getattr(cfg, "provider_safe_chunk_duration_sec", 300))
    if safe_duration <= 0:
        raise ValueError("provider_safe_chunk_duration_sec must be positive")

    cfg.default_chunk_duration_sec = min(
        int(cfg.default_chunk_duration_sec), safe_duration
    )
    cfg.max_chunk_duration_sec = min(int(cfg.max_chunk_duration_sec), safe_duration)
    # Preserve useful silence-boundary freedom instead of forcing every cut at
    # exactly the target duration.
    cfg.min_chunk_duration_sec = min(
        int(cfg.min_chunk_duration_sec),
        max(1, int(cfg.default_chunk_duration_sec) // 2),
    )

    if spec.provider == "openai":
        request_limit = int(cfg.openai_file_size_limit_bytes)
    elif spec.provider == "meralion":
        request_limit = min(
            int(cfg.meralion_file_size_limit_bytes),
            int(cfg.meralion_json_audio_limit_bytes),
        )
    elif spec.provider == "google":
        request_limit = min(
            int(cfg.gemini_inline_file_size_limit_bytes),
            int(cfg.gemini_generate_content_inline_audio_limit_bytes),
        )
        cfg.external_transcription_timeout_seconds = max(
            float(cfg.external_transcription_timeout_seconds),
            float(cfg.gemini_generate_content_timeout_seconds),
        )
    else:
        return cfg

    if request_limit <= 0:
        raise ValueError(f"Request byte limit is not positive for {spec.provider_code}")
    cfg.target_max_chunk_bytes = min(int(cfg.target_max_chunk_bytes), request_limit)
    cfg.hard_max_chunk_bytes = min(int(cfg.hard_max_chunk_bytes), request_limit)
    cfg.target_max_chunk_bytes = min(
        int(cfg.target_max_chunk_bytes), int(cfg.hard_max_chunk_bytes)
    )
    return cfg


__all__ = [
    "TranscriptionModelSpec",
    "configured_model_specs",
    "configure_model_chunking",
    "get_model_spec",
    "is_diarization_model",
]
