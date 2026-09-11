"""Configuration loader for APMA V5 (Task 3 scaffold).

Loads environment variables (optionally from .env) and returns a Config
dataclass with safe defaults. DRY_RUN is enforced True in this Task 3
scaffold to keep runs non-billable and safe.
"""

from dataclasses import dataclass, field
from typing import Optional
import os

from services.text_normalization import normalize_chinese_script_preference

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None


@dataclass
class Config:
    dry_run: bool = True
    log_level: str = "INFO"
    storage_path: str = "./jobs"
    host_storage_path: Optional[str] = None
    cleanup_completed_working_audio: bool = True
    max_retries: int = 3
    max_cost_per_job_usd: float = 5.0
    paid_provider_cost_buffer_percent: float = 15.0
    enable_diarization: bool = False
    job_id: Optional[str] = None
    # Canonical APMA display/output rule. Raw provider responses remain unchanged.
    chinese_script_preference: str = "simplified"
    # OpenAI transcription / live settings
    enable_live_openai_transcription: bool = False
    openai_default_model: str = "gpt-4o-mini-transcribe"
    openai_model: str = "gpt-4o-mini-transcribe"
    openai_recommended_model: str = "gpt-transcribe"
    openai_premium_model: str = "gpt-4o-transcribe"
    openai_diarize_model: str = "gpt-4o-transcribe-diarize"
    openai_price_per_minute: dict = field(
        default_factory=lambda: {
            "gpt-4o-mini-transcribe": 0.003,
            "gpt-transcribe": 0.0045,
            # These remain configurable operator estimates because the
            # provider publishes token-based audio pricing for these models.
            "gpt-4o-transcribe": 0.006,
            "gpt-4o-transcribe-diarize": 0.006,
        }
    )
    openai_file_size_limit_bytes: int = 25 * 1024 * 1024
    dashboard_upload_limit_bytes: int = 500 * 1024 * 1024
    openai_max_retries: int = 3
    openai_retry_backoff_base: float = 0.5
    openai_max_cost_per_chunk_usd: Optional[float] = None
    openai_api_key: Optional[str] = None
    # Opt-in comparison mode. Normal provider requests remain unchanged by default.
    enable_provider_timestamps: bool = False
    # Opt-in Gem35T speaker/timing evidence mode. Quality transcript wording is unchanged.
    enable_gemini_speaker_attribution: bool = False
    # External transcription routes remain disabled until explicitly enabled.
    enable_live_meralion_transcription: bool = False
    meralion_transcription_model: str = "MERaLiON-3-3B-ASR-Consortium"
    meralion_self_host_model: str = "MERaLiON/MERaLiON-3-3B-ASR"
    meralion_api_key: Optional[str] = None
    meralion_billing_mode: str = "trial_free"
    meralion_price_per_minute_usd: Optional[float] = None
    meralion_api_url: str = "https://api.meralion.ai/v1/audio/transcriptions"
    meralion_file_size_limit_bytes: int = 25 * 1024 * 1024
    # Raw audio is base64-embedded in JSON, so leave room for encoding and
    # request overhead below the provider/file ceiling.
    meralion_json_audio_limit_bytes: int = 9 * 1024 * 1024
    enable_live_gemini_transcription: bool = False
    gemini_flash_model: str = "gemini-3.7-flash"
    gemini_transcribe_model: str = "gemini-3.5-transcribe"
    gemini_transcription_model: str = "gemini-3.5-transcribe"
    gemini_api_key: Optional[str] = None
    gemini_price_per_minute_usd: Optional[float] = 0.003315
    gemini_transcribe_price_per_minute_usd: Optional[float] = 0.005
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_inline_file_size_limit_bytes: int = 20 * 1024 * 1024
    # GenerateContent embeds base64 audio inside a 20 MB total request. Nine MiB
    # also keeps APMA's normalized WAV chunks near five minutes so long-audio
    # inference can complete inside the bounded provider request timeout.
    gemini_generate_content_inline_audio_limit_bytes: int = 9 * 1024 * 1024
    gemini_generate_content_timeout_seconds: float = 300.0
    gemini_thinking_level: str = "low"
    # Alibaba Cloud Qwen long-file transcription. Filetrans accepts URL input,
    # so production uses private OSS while Beijing-only development can opt in
    # to DashScope's API-key-authenticated temporary upload service.
    enable_live_qwen_filetrans_transcription: bool = False
    qwen_filetrans_model: str = "qwen-audio-3.0-asr-flash-filetrans"
    dashscope_api_key: Optional[str] = None
    dashscope_api_base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    qwen_filetrans_staging_mode: str = "private_oss"
    # Compatibility path live-proven on bounded clips with DashScope International.
    # The raw-audio ceiling prevents large base64 request bodies.
    qwen_filetrans_data_uri_limit_bytes: int = 9 * 1024 * 1024
    dashscope_temporary_upload_api_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    qwen_filetrans_temporary_retention_hours: int = 48
    qwen_filetrans_price_per_minute_usd: float = 0.0021
    qwen_filetrans_file_size_limit_bytes: int = 2 * 1024 * 1024 * 1024
    qwen_filetrans_diarization_enabled: bool = True
    qwen_filetrans_max_diarized_duration_sec: int = 2 * 3600
    qwen_filetrans_poll_interval_seconds: float = 2.0
    qwen_filetrans_poll_timeout_seconds: float = 30 * 60
    aliyun_oss_access_key_id: Optional[str] = None
    aliyun_oss_access_key_secret: Optional[str] = None
    aliyun_oss_endpoint: Optional[str] = None
    aliyun_oss_bucket: Optional[str] = None
    aliyun_oss_object_prefix: str = "apma-temporary"
    aliyun_oss_signed_url_expiry_seconds: int = 6 * 3600
    external_transcription_timeout_seconds: float = 120.0
    external_transcription_max_retries: int = 3
    external_transcription_retry_backoff_base: float = 1.0
    external_transcription_retry_max_delay_seconds: float = 60.0
    # OpenAI live minutes / text-generation settings
    enable_live_openai_minutes: bool = False
    openai_minutes_model: str = "gpt-5.4-mini"
    openai_minutes_premium_model: str = "gpt-5.4"
    openai_minutes_best_model: str = "gpt-5.5"
    openai_minutes_price_per_million_tokens: dict = field(
        default_factory=lambda: {
            "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
            "gpt-5.4": {"input": 2.50, "output": 15.00},
            "gpt-5.5": {"input": 5.00, "output": 30.00},
        }
    )
    max_minutes_cost_per_job_usd: float = 2.0
    openai_minutes_output_tokens_by_style: dict = field(
        default_factory=lambda: {
            "standard": 2200,
            "deep_evidence": 4200,
            "action_focused": 1400,
        }
    )
    openai_minutes_api_url: str = "https://api.openai.com/v1/responses"
    openai_minutes_timeout_seconds: float = 180.0
    # Opt-in, separately approved transcript reconciliation. This never replaces
    # provider evidence and never receives audio; it operates on retained text.
    enable_live_openai_reconciliation: bool = False
    openai_reconciliation_model: str = "gpt-5.6-sol"
    openai_reconciliation_reasoning_effort: str = "medium"
    openai_reconciliation_input_price_per_million_usd: float = 4.0
    openai_reconciliation_output_price_per_million_usd: float = 20.0
    max_reconciliation_cost_per_job_usd: float = 2.0
    openai_reconciliation_timeout_seconds: float = 300.0
    openai_reconciliation_max_retries: int = 3
    # Transcription engine selection
    transcription_engine: str = "mock"
    # Meeting minutes style selection
    minutes_style: str = "standard"
    # Audio normalization
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    normalized_audio_framerate: int = 16000
    normalized_audio_channels: int = 1
    normalized_audio_sample_width: int = 2
    # Chunking config
    # Conservative per-provider request duration for long recordings. This
    # protects finite transcript output and request-time envelopes.
    provider_safe_chunk_duration_sec: int = 5 * 60
    target_max_chunk_bytes: int = 20 * 1024 * 1024
    hard_max_chunk_bytes: int = 24 * 1024 * 1024
    smart_chunking_enabled: bool = True
    default_chunk_duration_sec: int = 12 * 60
    max_chunk_duration_sec: int = 15 * 60
    min_chunk_duration_sec: int = 5 * 60
    overlap_seconds: int = 12
    chunk_boundary_search_window_sec: int = 90
    silence_noise_threshold_db: float = -35.0
    silence_min_duration_sec: float = 0.7
    max_meeting_duration_sec: int = 4 * 3600


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def _parse_optional_nonnegative_float(value: Optional[str]) -> Optional[float]:
    if value is None or not str(value).strip():
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError("Configured provider price must be non-negative")
    return parsed


def paid_cost_authorization_amount(estimated_cost_usd: float, cfg: Config) -> float:
    """Return the paid-provider estimate plus the configured approval reserve."""

    multiplier = 1.0 + float(cfg.paid_provider_cost_buffer_percent) / 100.0
    return float(estimated_cost_usd) * multiplier


def load_config(env_path: Optional[str] = None) -> Config:
    """Load configuration from environment or optional .env file.

    Note: in Task 3 DRY_RUN is enforced to True.
    """
    if load_dotenv is not None:
        try:
            if env_path:
                load_dotenv(dotenv_path=env_path)
            else:
                load_dotenv()
        except Exception:
            # Fail silently: dotenv is optional for Task 3
            pass

    env = os.environ
    cfg = Config(
        dry_run=_parse_bool(env.get("DRY_RUN"), True),
        log_level=env.get("LOG_LEVEL", "INFO"),
        storage_path=env.get("STORAGE_PATH", "./jobs"),
        host_storage_path=env.get("APMA_HOST_JOBS_PATH"),
        cleanup_completed_working_audio=_parse_bool(
            env.get("CLEANUP_COMPLETED_WORKING_AUDIO"), True
        ),
        max_retries=int(env.get("MAX_RETRIES", "3")),
        max_cost_per_job_usd=float(env.get("MAX_COST_PER_JOB_USD", "5.0")),
        paid_provider_cost_buffer_percent=float(
            env.get("PAID_PROVIDER_COST_BUFFER_PERCENT", "15.0")
        ),
        enable_diarization=_parse_bool(env.get("ENABLE_DIARIZATION"), False),
        job_id=env.get("JOB_ID"),
        chinese_script_preference=normalize_chinese_script_preference(
            env.get("CHINESE_SCRIPT_PREFERENCE", "simplified")
        ),
    )
    if cfg.paid_provider_cost_buffer_percent < 0:
        raise ValueError("PAID_PROVIDER_COST_BUFFER_PERCENT must be non-negative")

    # Chunking environment overrides (optional)
    cfg.target_max_chunk_bytes = int(env.get("TARGET_MAX_CHUNK_BYTES", str(cfg.target_max_chunk_bytes)))
    cfg.hard_max_chunk_bytes = int(env.get("HARD_MAX_CHUNK_BYTES", str(cfg.hard_max_chunk_bytes)))
    cfg.smart_chunking_enabled = _parse_bool(
        env.get("SMART_CHUNKING_ENABLED"), cfg.smart_chunking_enabled
    )
    cfg.default_chunk_duration_sec = int(env.get("DEFAULT_CHUNK_DURATION_SEC", str(cfg.default_chunk_duration_sec)))
    cfg.max_chunk_duration_sec = int(env.get("MAX_CHUNK_DURATION_SEC", str(cfg.max_chunk_duration_sec)))
    cfg.min_chunk_duration_sec = int(env.get("MIN_CHUNK_DURATION_SEC", str(cfg.min_chunk_duration_sec)))
    cfg.overlap_seconds = int(env.get("OVERLAP_SECONDS", str(cfg.overlap_seconds)))
    cfg.chunk_boundary_search_window_sec = int(
        env.get(
            "CHUNK_BOUNDARY_SEARCH_WINDOW_SEC",
            str(cfg.chunk_boundary_search_window_sec),
        )
    )
    cfg.silence_noise_threshold_db = float(
        env.get("SILENCE_NOISE_THRESHOLD_DB", str(cfg.silence_noise_threshold_db))
    )
    cfg.silence_min_duration_sec = float(
        env.get("SILENCE_MIN_DURATION_SEC", str(cfg.silence_min_duration_sec))
    )
    cfg.max_meeting_duration_sec = int(env.get("MAX_MEETING_DURATION_SEC", str(cfg.max_meeting_duration_sec)))
    cfg.provider_safe_chunk_duration_sec = int(
        env.get(
            "PROVIDER_SAFE_CHUNK_DURATION_SEC",
            str(cfg.provider_safe_chunk_duration_sec),
        )
    )

    # OpenAI env overrides
    cfg.enable_live_openai_transcription = _parse_bool(env.get("ENABLE_LIVE_OPENAI_TRANSCRIPTION"), cfg.enable_live_openai_transcription)
    cfg.openai_default_model = env.get("OPENAI_DEFAULT_MODEL", cfg.openai_default_model)
    cfg.openai_model = env.get("OPENAI_MODEL", cfg.openai_default_model)
    cfg.openai_recommended_model = env.get("OPENAI_RECOMMENDED_MODEL", cfg.openai_recommended_model)
    cfg.openai_premium_model = env.get("OPENAI_PREMIUM_MODEL", cfg.openai_premium_model)
    cfg.openai_diarize_model = env.get("OPENAI_DIARIZE_MODEL", cfg.openai_diarize_model)
    # pricing per minute (configurable)
    try:
        cfg.openai_price_per_minute = {
            cfg.openai_default_model: float(env.get("OPENAI_PRICE_GPT4O_MINI_TRANSCRIBE", str(cfg.openai_price_per_minute.get("gpt-4o-mini-transcribe", 0.003)))),
            cfg.openai_recommended_model: float(env.get("OPENAI_PRICE_GPT_TRANSCRIBE", str(cfg.openai_price_per_minute.get("gpt-transcribe", 0.0045)))),
            cfg.openai_premium_model: float(env.get("OPENAI_PRICE_GPT4O_TRANSCRIBE", str(cfg.openai_price_per_minute.get("gpt-4o-transcribe", 0.006)))),
            cfg.openai_diarize_model: float(env.get("OPENAI_PRICE_GPT4O_TRANSCRIBE_DIARIZE", str(cfg.openai_price_per_minute.get("gpt-4o-transcribe-diarize", 0.006)))),
        }
    except Exception:
        # leave defaults
        pass

    cfg.openai_file_size_limit_bytes = int(env.get("OPENAI_FILE_SIZE_LIMIT_BYTES", str(cfg.openai_file_size_limit_bytes)))
    cfg.dashboard_upload_limit_bytes = int(env.get("DASHBOARD_UPLOAD_LIMIT_BYTES", str(cfg.dashboard_upload_limit_bytes)))
    cfg.openai_max_retries = int(env.get("OPENAI_MAX_RETRIES", str(cfg.openai_max_retries)))
    cfg.openai_retry_backoff_base = float(env.get("OPENAI_RETRY_BACKOFF_BASE", str(cfg.openai_retry_backoff_base)))
    cfg.openai_max_cost_per_chunk_usd = None if env.get("OPENAI_MAX_COST_PER_CHUNK_USD") is None else float(env.get("OPENAI_MAX_COST_PER_CHUNK_USD"))
    cfg.openai_api_key = env.get("OPENAI_API_KEY", None)
    cfg.enable_provider_timestamps = _parse_bool(
        env.get("ENABLE_PROVIDER_TIMESTAMPS"),
        cfg.enable_provider_timestamps,
    )
    cfg.enable_gemini_speaker_attribution = _parse_bool(
        env.get("ENABLE_GEMINI_SPEAKER_ATTRIBUTION"),
        cfg.enable_gemini_speaker_attribution,
    )
    cfg.enable_live_meralion_transcription = _parse_bool(
        env.get("ENABLE_LIVE_MERALION_TRANSCRIPTION"),
        cfg.enable_live_meralion_transcription,
    )
    cfg.meralion_transcription_model = env.get(
        "MERALION_TRANSCRIPTION_MODEL", cfg.meralion_transcription_model
    )
    cfg.meralion_self_host_model = env.get(
        "MERALION_SELF_HOST_MODEL", cfg.meralion_self_host_model
    )
    cfg.meralion_api_key = env.get("MERALION_API_KEY")
    cfg.meralion_billing_mode = env.get(
        "MERALION_BILLING_MODE", cfg.meralion_billing_mode
    ).strip().lower()
    cfg.meralion_price_per_minute_usd = _parse_optional_nonnegative_float(
        env.get("MERALION_PRICE_PER_MINUTE_USD")
    )
    cfg.meralion_api_url = env.get("MERALION_API_URL", cfg.meralion_api_url)
    cfg.meralion_file_size_limit_bytes = int(
        env.get("MERALION_FILE_SIZE_LIMIT_BYTES", str(cfg.meralion_file_size_limit_bytes))
    )
    cfg.meralion_json_audio_limit_bytes = int(
        env.get(
            "MERALION_JSON_AUDIO_LIMIT_BYTES",
            str(cfg.meralion_json_audio_limit_bytes),
        )
    )
    cfg.enable_live_gemini_transcription = _parse_bool(
        env.get("ENABLE_LIVE_GEMINI_TRANSCRIPTION"),
        cfg.enable_live_gemini_transcription,
    )
    cfg.gemini_flash_model = env.get("GEMINI_FLASH_MODEL", cfg.gemini_flash_model)
    cfg.gemini_transcribe_model = env.get(
        "GEMINI_TRANSCRIBE_MODEL", cfg.gemini_transcribe_model
    )
    cfg.gemini_transcription_model = env.get(
        "GEMINI_TRANSCRIPTION_MODEL", cfg.gemini_transcription_model
    )
    cfg.gemini_api_key = env.get("GEMINI_API_KEY")
    flash_price = _parse_optional_nonnegative_float(env.get("GEMINI_PRICE_PER_MINUTE_USD"))
    if flash_price is not None:
        cfg.gemini_price_per_minute_usd = flash_price
    transcribe_price = _parse_optional_nonnegative_float(
        env.get("GEMINI_TRANSCRIBE_PRICE_PER_MINUTE_USD")
    )
    if transcribe_price is not None:
        cfg.gemini_transcribe_price_per_minute_usd = transcribe_price
    cfg.gemini_api_base_url = env.get("GEMINI_API_BASE_URL", cfg.gemini_api_base_url)
    cfg.gemini_inline_file_size_limit_bytes = int(
        env.get(
            "GEMINI_INLINE_FILE_SIZE_LIMIT_BYTES",
            str(cfg.gemini_inline_file_size_limit_bytes),
        )
    )
    cfg.gemini_thinking_level = env.get(
        "GEMINI_THINKING_LEVEL", cfg.gemini_thinking_level
    )
    cfg.external_transcription_timeout_seconds = float(
        env.get(
            "EXTERNAL_TRANSCRIPTION_TIMEOUT_SECONDS",
            str(cfg.external_transcription_timeout_seconds),
        )
    )
    cfg.external_transcription_max_retries = int(
        env.get(
            "EXTERNAL_TRANSCRIPTION_MAX_RETRIES",
            str(cfg.external_transcription_max_retries),
        )
    )
    cfg.external_transcription_retry_backoff_base = float(
        env.get(
            "EXTERNAL_TRANSCRIPTION_RETRY_BACKOFF_BASE",
            str(cfg.external_transcription_retry_backoff_base),
        )
    )
    cfg.gemini_generate_content_inline_audio_limit_bytes = int(
        env.get(
            "GEMINI_GENERATE_CONTENT_INLINE_AUDIO_LIMIT_BYTES",
            str(cfg.gemini_generate_content_inline_audio_limit_bytes),
        )
    )
    cfg.gemini_generate_content_timeout_seconds = float(
        env.get(
            "GEMINI_GENERATE_CONTENT_TIMEOUT_SECONDS",
            str(cfg.gemini_generate_content_timeout_seconds),
        )
    )
    cfg.external_transcription_retry_max_delay_seconds = float(
        env.get(
            "EXTERNAL_TRANSCRIPTION_RETRY_MAX_DELAY_SECONDS",
            str(cfg.external_transcription_retry_max_delay_seconds),
        )
    )
    cfg.enable_live_qwen_filetrans_transcription = _parse_bool(
        env.get("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION"),
        cfg.enable_live_qwen_filetrans_transcription,
    )
    cfg.qwen_filetrans_model = env.get(
        "QWEN_FILETRANS_MODEL", cfg.qwen_filetrans_model
    )
    cfg.dashscope_api_key = env.get("DASHSCOPE_API_KEY")
    cfg.dashscope_api_base_url = env.get(
        "DASHSCOPE_API_BASE_URL", cfg.dashscope_api_base_url
    ).rstrip("/")
    cfg.qwen_filetrans_staging_mode = env.get(
        "QWEN_FILETRANS_STAGING_MODE", cfg.qwen_filetrans_staging_mode
    ).strip().lower()
    cfg.qwen_filetrans_data_uri_limit_bytes = int(
        env.get(
            "QWEN_FILETRANS_DATA_URI_LIMIT_BYTES",
            str(cfg.qwen_filetrans_data_uri_limit_bytes),
        )
    )
    cfg.dashscope_temporary_upload_api_base_url = env.get(
        "DASHSCOPE_TEMPORARY_UPLOAD_API_BASE_URL",
        cfg.dashscope_temporary_upload_api_base_url,
    ).rstrip("/")
    cfg.qwen_filetrans_temporary_retention_hours = int(
        env.get(
            "QWEN_FILETRANS_TEMPORARY_RETENTION_HOURS",
            str(cfg.qwen_filetrans_temporary_retention_hours),
        )
    )
    cfg.qwen_filetrans_price_per_minute_usd = float(
        env.get(
            "QWEN_FILETRANS_PRICE_PER_MINUTE_USD",
            str(cfg.qwen_filetrans_price_per_minute_usd),
        )
    )
    cfg.qwen_filetrans_file_size_limit_bytes = int(
        env.get(
            "QWEN_FILETRANS_FILE_SIZE_LIMIT_BYTES",
            str(cfg.qwen_filetrans_file_size_limit_bytes),
        )
    )
    cfg.qwen_filetrans_diarization_enabled = _parse_bool(
        env.get("QWEN_FILETRANS_DIARIZATION_ENABLED"),
        cfg.qwen_filetrans_diarization_enabled,
    )
    cfg.qwen_filetrans_max_diarized_duration_sec = int(
        env.get(
            "QWEN_FILETRANS_MAX_DIARIZED_DURATION_SEC",
            str(cfg.qwen_filetrans_max_diarized_duration_sec),
        )
    )
    cfg.qwen_filetrans_poll_interval_seconds = float(
        env.get(
            "QWEN_FILETRANS_POLL_INTERVAL_SECONDS",
            str(cfg.qwen_filetrans_poll_interval_seconds),
        )
    )
    cfg.qwen_filetrans_poll_timeout_seconds = float(
        env.get(
            "QWEN_FILETRANS_POLL_TIMEOUT_SECONDS",
            str(cfg.qwen_filetrans_poll_timeout_seconds),
        )
    )
    cfg.aliyun_oss_access_key_id = env.get("ALIYUN_OSS_ACCESS_KEY_ID")
    cfg.aliyun_oss_access_key_secret = env.get("ALIYUN_OSS_ACCESS_KEY_SECRET")
    cfg.aliyun_oss_endpoint = env.get("ALIYUN_OSS_ENDPOINT")
    cfg.aliyun_oss_bucket = env.get("ALIYUN_OSS_BUCKET")
    cfg.aliyun_oss_object_prefix = env.get(
        "ALIYUN_OSS_OBJECT_PREFIX", cfg.aliyun_oss_object_prefix
    ).strip("/ ")
    cfg.aliyun_oss_signed_url_expiry_seconds = int(
        env.get(
            "ALIYUN_OSS_SIGNED_URL_EXPIRY_SECONDS",
            str(cfg.aliyun_oss_signed_url_expiry_seconds),
        )
    )
    cfg.enable_live_openai_minutes = _parse_bool(env.get("ENABLE_LIVE_OPENAI_MINUTES"), cfg.enable_live_openai_minutes)
    cfg.openai_minutes_model = env.get("OPENAI_MINUTES_MODEL", cfg.openai_minutes_model)
    cfg.openai_minutes_premium_model = env.get("OPENAI_MINUTES_PREMIUM_MODEL", cfg.openai_minutes_premium_model)
    cfg.openai_minutes_best_model = env.get("OPENAI_MINUTES_BEST_MODEL", cfg.openai_minutes_best_model)
    cfg.max_minutes_cost_per_job_usd = float(env.get("MAX_MINUTES_COST_PER_JOB_USD", str(cfg.max_minutes_cost_per_job_usd)))
    cfg.openai_minutes_api_url = env.get("OPENAI_MINUTES_API_URL", cfg.openai_minutes_api_url)
    cfg.openai_minutes_timeout_seconds = float(env.get("OPENAI_MINUTES_TIMEOUT_SECONDS", str(cfg.openai_minutes_timeout_seconds)))
    cfg.enable_live_openai_reconciliation = _parse_bool(
        env.get("ENABLE_LIVE_OPENAI_RECONCILIATION"),
        cfg.enable_live_openai_reconciliation,
    )
    cfg.openai_reconciliation_model = env.get(
        "OPENAI_RECONCILIATION_MODEL", cfg.openai_reconciliation_model
    )
    cfg.openai_reconciliation_reasoning_effort = env.get(
        "OPENAI_RECONCILIATION_REASONING_EFFORT",
        cfg.openai_reconciliation_reasoning_effort,
    )
    cfg.openai_reconciliation_input_price_per_million_usd = float(
        env.get(
            "OPENAI_RECONCILIATION_INPUT_PRICE_PER_1M",
            str(cfg.openai_reconciliation_input_price_per_million_usd),
        )
    )
    cfg.openai_reconciliation_output_price_per_million_usd = float(
        env.get(
            "OPENAI_RECONCILIATION_OUTPUT_PRICE_PER_1M",
            str(cfg.openai_reconciliation_output_price_per_million_usd),
        )
    )
    cfg.max_reconciliation_cost_per_job_usd = float(
        env.get(
            "MAX_RECONCILIATION_COST_PER_JOB_USD",
            str(cfg.max_reconciliation_cost_per_job_usd),
        )
    )
    cfg.openai_reconciliation_timeout_seconds = float(
        env.get(
            "OPENAI_RECONCILIATION_TIMEOUT_SECONDS",
            str(cfg.openai_reconciliation_timeout_seconds),
        )
    )
    cfg.openai_reconciliation_max_retries = int(
        env.get(
            "OPENAI_RECONCILIATION_MAX_RETRIES",
            str(cfg.openai_reconciliation_max_retries),
        )
    )
    if cfg.openai_reconciliation_input_price_per_million_usd < 0:
        raise ValueError("OpenAI reconciliation input price must be non-negative")
    if cfg.openai_reconciliation_output_price_per_million_usd < 0:
        raise ValueError("OpenAI reconciliation output price must be non-negative")
    if cfg.max_reconciliation_cost_per_job_usd < 0:
        raise ValueError("OpenAI reconciliation cost cap must be non-negative")
    if cfg.openai_reconciliation_timeout_seconds <= 0:
        raise ValueError("OpenAI reconciliation timeout must be positive")
    if not 1 <= cfg.openai_reconciliation_max_retries <= 3:
        raise ValueError("OpenAI reconciliation retries must be between 1 and 3")
    try:
        cfg.openai_minutes_price_per_million_tokens = {
            cfg.openai_minutes_model: {
                "input": float(env.get("OPENAI_PRICE_MINUTES_DEFAULT_INPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_model, {}).get("input", 0.75)))),
                "output": float(env.get("OPENAI_PRICE_MINUTES_DEFAULT_OUTPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_model, {}).get("output", 4.50)))),
            },
            cfg.openai_minutes_premium_model: {
                "input": float(env.get("OPENAI_PRICE_MINUTES_PREMIUM_INPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_premium_model, {}).get("input", 2.50)))),
                "output": float(env.get("OPENAI_PRICE_MINUTES_PREMIUM_OUTPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_premium_model, {}).get("output", 15.00)))),
            },
            cfg.openai_minutes_best_model: {
                "input": float(env.get("OPENAI_PRICE_MINUTES_BEST_INPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_best_model, {}).get("input", 5.00)))),
                "output": float(env.get("OPENAI_PRICE_MINUTES_BEST_OUTPUT_PER_1M", str(cfg.openai_minutes_price_per_million_tokens.get(cfg.openai_minutes_best_model, {}).get("output", 30.00)))),
            },
        }
    except Exception:
        pass
    try:
        cfg.openai_minutes_output_tokens_by_style = {
            "standard": int(env.get("OPENAI_MINUTES_STANDARD_OUTPUT_TOKENS", str(cfg.openai_minutes_output_tokens_by_style.get("standard", 2200)))),
            "deep_evidence": int(env.get("OPENAI_MINUTES_DEEP_EVIDENCE_OUTPUT_TOKENS", str(cfg.openai_minutes_output_tokens_by_style.get("deep_evidence", 4200)))),
            "action_focused": int(env.get("OPENAI_MINUTES_ACTION_FOCUSED_OUTPUT_TOKENS", str(cfg.openai_minutes_output_tokens_by_style.get("action_focused", 1400)))),
        }
    except Exception:
        pass
    # Transcription engine env override
    cfg.transcription_engine = env.get("TRANSCRIPTION_ENGINE", cfg.transcription_engine)
    cfg.minutes_style = env.get("MINUTES_STYLE", cfg.minutes_style)
    # Audio normalization env overrides
    cfg.ffmpeg_binary = env.get("FFMPEG_BINARY", cfg.ffmpeg_binary)
    cfg.ffprobe_binary = env.get("FFPROBE_BINARY", cfg.ffprobe_binary)
    cfg.normalized_audio_framerate = int(env.get("NORMALIZED_AUDIO_FRAMERATE", str(cfg.normalized_audio_framerate)))
    cfg.normalized_audio_channels = int(env.get("NORMALIZED_AUDIO_CHANNELS", str(cfg.normalized_audio_channels)))
    cfg.normalized_audio_sample_width = int(env.get("NORMALIZED_AUDIO_SAMPLE_WIDTH", str(cfg.normalized_audio_sample_width)))

    # validate chunking config invariants
    def _validate_config(c: Config) -> None:
        if c.hard_max_chunk_bytes < c.target_max_chunk_bytes:
            raise ValueError("HARD_MAX_CHUNK_BYTES must be >= TARGET_MAX_CHUNK_BYTES")
        if not (c.min_chunk_duration_sec <= c.default_chunk_duration_sec <= c.max_chunk_duration_sec):
            raise ValueError("DEFAULT_CHUNK_DURATION_SEC must be between MIN_CHUNK_DURATION_SEC and MAX_CHUNK_DURATION_SEC")
        if c.overlap_seconds >= c.default_chunk_duration_sec:
            raise ValueError("OVERLAP_SECONDS must be less than DEFAULT_CHUNK_DURATION_SEC")
        if c.chunk_boundary_search_window_sec < 0:
            raise ValueError("CHUNK_BOUNDARY_SEARCH_WINDOW_SEC must be non-negative")
        if c.silence_min_duration_sec <= 0:
            raise ValueError("SILENCE_MIN_DURATION_SEC must be positive")
        if c.provider_safe_chunk_duration_sec <= 0:
            raise ValueError("PROVIDER_SAFE_CHUNK_DURATION_SEC must be positive")
        if c.meralion_json_audio_limit_bytes <= 0:
            raise ValueError("MERALION_JSON_AUDIO_LIMIT_BYTES must be positive")
        if c.meralion_billing_mode not in {"trial_free", "metered"}:
            raise ValueError(
                "MERALION_BILLING_MODE must be either trial_free or metered"
            )
        if c.qwen_filetrans_price_per_minute_usd < 0:
            raise ValueError("QWEN_FILETRANS_PRICE_PER_MINUTE_USD must be non-negative")
        if c.qwen_filetrans_staging_mode not in {
            "private_oss",
            "dashscope_temporary",
            "data_uri",
        }:
            raise ValueError(
                "QWEN_FILETRANS_STAGING_MODE must be private_oss, "
                "dashscope_temporary, or data_uri"
            )
        if c.qwen_filetrans_data_uri_limit_bytes <= 0:
            raise ValueError("QWEN_FILETRANS_DATA_URI_LIMIT_BYTES must be positive")
        if c.qwen_filetrans_temporary_retention_hours <= 0:
            raise ValueError("QWEN_FILETRANS_TEMPORARY_RETENTION_HOURS must be positive")
        if c.qwen_filetrans_file_size_limit_bytes <= 0:
            raise ValueError("QWEN_FILETRANS_FILE_SIZE_LIMIT_BYTES must be positive")
        if c.qwen_filetrans_max_diarized_duration_sec <= 0:
            raise ValueError("QWEN_FILETRANS_MAX_DIARIZED_DURATION_SEC must be positive")
        if c.qwen_filetrans_poll_interval_seconds <= 0:
            raise ValueError("QWEN_FILETRANS_POLL_INTERVAL_SECONDS must be positive")
        if c.qwen_filetrans_poll_timeout_seconds <= 0:
            raise ValueError("QWEN_FILETRANS_POLL_TIMEOUT_SECONDS must be positive")
        if c.aliyun_oss_signed_url_expiry_seconds <= 0:
            raise ValueError("ALIYUN_OSS_SIGNED_URL_EXPIRY_SECONDS must be positive")

    _validate_config(cfg)

    # Enforce DRY_RUN true for Task 3 to keep operations safe.
    cfg.dry_run = True
    return cfg
