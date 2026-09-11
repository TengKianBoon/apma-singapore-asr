from argparse import Namespace

from scripts import run_live_provider_smoke


def _args(provider_code: str = "M3ASR", **overrides):
    data = {
        "provider_code": provider_code,
        "attempt_number": 1,
        "input_wav": "sample_audio/live_smoke.wav",
        "job_id": None,
        "storage_path": "jobs",
        "max_cost_usd": 0.05,
        "confirm_live_api": True,
    }
    data.update(overrides)
    return Namespace(**data)


def _live_env(provider_code: str) -> dict[str, str]:
    env = {"DRY_RUN": "false"}
    if provider_code == "M3ASR":
        env.update(
            {
                "ENABLE_LIVE_MERALION_TRANSCRIPTION": "true",
                "MERALION_API_KEY": "fake-meralion-key",
                "MERALION_PRICE_PER_MINUTE_USD": "0.01",
            }
        )
    elif provider_code in {"Gem37F", "Gem35T"}:
        env.update(
            {
                "ENABLE_LIVE_GEMINI_TRANSCRIPTION": "true",
                "GEMINI_API_KEY": "fake-gemini-key",
            }
        )
    else:
        env.update(
            {
                "ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION": "true",
                "DASHSCOPE_API_KEY": "fake-beijing-key",
                "QWEN_FILETRANS_STAGING_MODE": "dashscope_temporary",
                "DASHSCOPE_API_BASE_URL": "https://dashscope.aliyuncs.com/api/v1",
            }
        )
    return env


def test_live_smoke_requires_environment_only_provider_credential():
    env = _live_env("M3ASR")
    env.pop("MERALION_API_KEY")

    errors = run_live_provider_smoke.validate_live_smoke_gates(_args(), env)

    assert errors == ["MERALION_API_KEY must be set in the environment"]


def test_meralion_trial_smoke_does_not_require_metered_price():
    env = _live_env("M3ASR")
    env.pop("MERALION_PRICE_PER_MINUTE_USD")
    env["MERALION_BILLING_MODE"] = "trial_free"

    assert run_live_provider_smoke.validate_live_smoke_gates(_args(), env) == []
    cfg = run_live_provider_smoke._build_live_config(_args(), env)
    assert cfg.meralion_billing_mode == "trial_free"
    assert cfg.meralion_price_per_minute_usd is None


def test_live_smoke_requires_explicit_gate_and_nondry_run():
    env = _live_env("Gem37F")
    env["DRY_RUN"] = "true"
    env["ENABLE_LIVE_GEMINI_TRANSCRIPTION"] = "false"

    errors = run_live_provider_smoke.validate_live_smoke_gates(
        _args("Gem37F"), env
    )

    assert "DRY_RUN must be false" in errors
    assert "ENABLE_LIVE_GEMINI_TRANSCRIPTION must be true" in errors


def test_live_smoke_builds_single_attempt_provider_config(monkeypatch):
    env = _live_env("Gem35T")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = run_live_provider_smoke._build_live_config(
        _args("Gem35T", storage_path="live-jobs", provider_timestamps=True), env
    )

    assert cfg.dry_run is False
    assert cfg.transcription_engine == "gemini"
    assert cfg.gemini_transcription_model == "gemini-3.5-transcribe"
    assert cfg.gemini_api_key == "fake-gemini-key"
    assert cfg.max_retries == 1
    assert cfg.external_transcription_max_retries == 1
    assert cfg.storage_path == "live-jobs"
    assert cfg.enable_provider_timestamps is True


def test_live_smoke_builds_qwen_beijing_api_key_only_config(monkeypatch):
    env = _live_env("QwenA3FT")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = run_live_provider_smoke._build_live_config(
        _args("QwenA3FT", storage_path="live-qwen-jobs"), env
    )

    assert cfg.dry_run is False
    assert cfg.transcription_engine == "qwen_filetrans"
    assert cfg.qwen_filetrans_model == "qwen-audio-3.0-asr-flash-filetrans"
    assert cfg.qwen_filetrans_staging_mode == "dashscope_temporary"
    assert cfg.dashscope_api_key == "fake-beijing-key"
    assert cfg.aliyun_oss_access_key_id is None
    assert cfg.storage_path == "live-qwen-jobs"


def test_live_smoke_builds_qwen_international_data_uri_config(monkeypatch):
    env = _live_env("QwenA3FT")
    env["DASHSCOPE_API_KEY"] = "fake-international-key"
    env["QWEN_FILETRANS_STAGING_MODE"] = "data_uri"
    env["DASHSCOPE_API_BASE_URL"] = "https://dashscope-intl.aliyuncs.com/api/v1"
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = run_live_provider_smoke._build_live_config(
        _args("QwenA3FT", storage_path="live-qwen-data-uri"), env
    )

    assert cfg.qwen_filetrans_staging_mode == "data_uri"
    assert cfg.dashscope_api_key == "fake-international-key"
    assert cfg.aliyun_oss_access_key_id is None


def test_live_smoke_rejects_cap_above_hard_limit():
    errors = run_live_provider_smoke.validate_live_smoke_gates(
        _args("Gem37F", max_cost_usd=5.01), _live_env("Gem37F")
    )

    assert "--max-cost-usd must be greater than 0 and no more than 5" in errors
