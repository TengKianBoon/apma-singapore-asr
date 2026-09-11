from services.config import load_config


def test_load_config_defaults(monkeypatch, tmp_path):
    # Ensure no env variables influence defaults
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    cfg = load_config(env_path=str(tmp_path / ".env"))
    assert cfg.dry_run is True
    assert cfg.log_level == "INFO"
    assert cfg.storage_path == "./jobs"
    assert cfg.smart_chunking_enabled is True
    assert cfg.default_chunk_duration_sec == 720
    assert cfg.min_chunk_duration_sec == 300
    assert cfg.max_chunk_duration_sec == 900
    assert cfg.overlap_seconds == 12
    assert cfg.enable_provider_timestamps is False
    assert cfg.enable_gemini_speaker_attribution is False
    assert cfg.enable_live_qwen_filetrans_transcription is False
    assert cfg.qwen_filetrans_model == "qwen-audio-3.0-asr-flash-filetrans"
    assert cfg.qwen_filetrans_price_per_minute_usd == 0.0021
    assert cfg.qwen_filetrans_staging_mode == "private_oss"
    assert cfg.qwen_filetrans_data_uri_limit_bytes == 9 * 1024 * 1024
    assert cfg.qwen_filetrans_temporary_retention_hours == 48


def test_load_config_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MAX_RETRIES", "5")
    cfg = load_config(env_path=str(tmp_path / ".env"))
    assert cfg.log_level == "DEBUG"
    assert cfg.max_retries == 5
    # DRY_RUN remains enforced True in Task 3
    assert cfg.dry_run is True


def test_provider_timestamp_mode_is_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PROVIDER_TIMESTAMPS", "true")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.enable_provider_timestamps is True


def test_gemini_speaker_mode_is_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_GEMINI_SPEAKER_ATTRIBUTION", "true")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.enable_gemini_speaker_attribution is True


def test_load_config_smart_chunking_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_CHUNKING_ENABLED", "false")
    monkeypatch.setenv("DEFAULT_CHUNK_DURATION_SEC", "600")
    monkeypatch.setenv("MIN_CHUNK_DURATION_SEC", "240")
    monkeypatch.setenv("MAX_CHUNK_DURATION_SEC", "780")
    monkeypatch.setenv("OVERLAP_SECONDS", "10")
    monkeypatch.setenv("CHUNK_BOUNDARY_SEARCH_WINDOW_SEC", "45")
    monkeypatch.setenv("SILENCE_NOISE_THRESHOLD_DB", "-32")
    monkeypatch.setenv("SILENCE_MIN_DURATION_SEC", "0.5")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.smart_chunking_enabled is False
    assert cfg.default_chunk_duration_sec == 600
    assert cfg.min_chunk_duration_sec == 240
    assert cfg.max_chunk_duration_sec == 780
    assert cfg.overlap_seconds == 10
    assert cfg.chunk_boundary_search_window_sec == 45
    assert cfg.silence_noise_threshold_db == -32.0
    assert cfg.silence_min_duration_sec == 0.5


def test_external_provider_routes_default_to_disabled_with_gemini_pricing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in (
        "ENABLE_LIVE_MERALION_TRANSCRIPTION",
        "MERALION_API_KEY",
        "MERALION_BILLING_MODE",
        "MERALION_PRICE_PER_MINUTE_USD",
        "ENABLE_LIVE_GEMINI_TRANSCRIPTION",
        "GEMINI_API_KEY",
        "GEMINI_PRICE_PER_MINUTE_USD",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.enable_live_meralion_transcription is False
    assert cfg.meralion_api_key is None
    assert cfg.meralion_billing_mode == "trial_free"
    assert cfg.meralion_price_per_minute_usd is None
    assert cfg.enable_live_gemini_transcription is False
    assert cfg.gemini_api_key is None
    assert cfg.gemini_price_per_minute_usd == 0.003315
    assert cfg.gemini_transcribe_price_per_minute_usd == 0.005
    assert cfg.gemini_generate_content_inline_audio_limit_bytes == 9 * 1024 * 1024
    assert cfg.gemini_generate_content_timeout_seconds == 300.0
    assert cfg.meralion_json_audio_limit_bytes == 9 * 1024 * 1024
    assert cfg.provider_safe_chunk_duration_sec == 300


def test_gemini_generate_content_inline_limit_is_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_GENERATE_CONTENT_INLINE_AUDIO_LIMIT_BYTES", "12000000")
    monkeypatch.setenv("GEMINI_GENERATE_CONTENT_TIMEOUT_SECONDS", "240")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.gemini_generate_content_inline_audio_limit_bytes == 12000000
    assert cfg.gemini_generate_content_timeout_seconds == 240.0


def test_long_recording_request_limits_are_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("MERALION_JSON_AUDIO_LIMIT_BYTES", "8000000")
    monkeypatch.setenv("QWEN_FILETRANS_DATA_URI_LIMIT_BYTES", "7000000")
    monkeypatch.setenv("PROVIDER_SAFE_CHUNK_DURATION_SEC", "240")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.meralion_json_audio_limit_bytes == 8000000
    assert cfg.qwen_filetrans_data_uri_limit_bytes == 7000000
    assert cfg.provider_safe_chunk_duration_sec == 240


def test_external_provider_config_is_loaded_without_exposing_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_LIVE_MERALION_TRANSCRIPTION", "true")
    monkeypatch.setenv("MERALION_API_KEY", "meralion-secret")
    monkeypatch.setenv("MERALION_BILLING_MODE", "metered")
    monkeypatch.setenv("MERALION_PRICE_PER_MINUTE_USD", "0.01")
    monkeypatch.setenv("ENABLE_LIVE_GEMINI_TRANSCRIPTION", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("GEMINI_PRICE_PER_MINUTE_USD", "0.02")
    monkeypatch.setenv("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "dashscope_temporary")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_ID", "oss-id")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "oss-secret")
    monkeypatch.setenv("ALIYUN_OSS_ENDPOINT", "https://oss-ap-southeast-1.aliyuncs.com")
    monkeypatch.setenv("ALIYUN_OSS_BUCKET", "private-bucket")

    cfg = load_config(env_path=str(tmp_path / ".env"))

    assert cfg.enable_live_meralion_transcription is True
    assert cfg.meralion_api_key == "meralion-secret"
    assert cfg.meralion_billing_mode == "metered"
    assert cfg.meralion_price_per_minute_usd == 0.01
    assert cfg.enable_live_gemini_transcription is True
    assert cfg.gemini_api_key == "gemini-secret"
    assert cfg.gemini_price_per_minute_usd == 0.02
    assert cfg.enable_live_qwen_filetrans_transcription is True
    assert cfg.dashscope_api_key == "dashscope-secret"
    assert cfg.qwen_filetrans_staging_mode == "dashscope_temporary"
    assert cfg.aliyun_oss_access_key_id == "oss-id"
    assert cfg.aliyun_oss_access_key_secret == "oss-secret"
    assert cfg.aliyun_oss_endpoint == "https://oss-ap-southeast-1.aliyuncs.com"
    assert cfg.aliyun_oss_bucket == "private-bucket"


def test_meralion_billing_mode_rejects_unknown_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("MERALION_BILLING_MODE", "free_forever")

    try:
        load_config(env_path=str(tmp_path / ".env"))
    except ValueError as exc:
        assert "MERALION_BILLING_MODE" in str(exc)
    else:
        raise AssertionError("Unknown MERaLiON billing policy should fail closed")
