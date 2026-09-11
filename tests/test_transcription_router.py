from services.config import load_config
from services.transcription.router import get_runtime_model_status


def test_openai_route_requires_gate_credential_and_price(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")

    status = get_runtime_model_status("gpt-4o-mini-transcribe", load_config())

    assert status["provider"] == "openai"
    assert status["runnable"] is True
    assert status["price_per_minute_usd"] == 0.003
    assert "fake-openai-key" not in repr(status)


def test_external_routes_are_runnable_only_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_MERALION_TRANSCRIPTION", "true")
    monkeypatch.setenv("MERALION_API_KEY", "fake-meralion-key")
    monkeypatch.setenv("MERALION_PRICE_PER_MINUTE_USD", "0.01")
    monkeypatch.setenv("ENABLE_LIVE_GEMINI_TRANSCRIPTION", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_PRICE_PER_MINUTE_USD", "0.02")
    cfg = load_config()

    for model_id, provider_code in (
        ("MERaLiON-3-3B-ASR-Consortium", "M3ASR"),
        ("gemini-3.5-transcribe", "Gem35T"),
    ):
        status = get_runtime_model_status(model_id, cfg)
        assert status["provider_code"] == provider_code
        assert status["live_adapter_ready"] is True
        assert status["runnable"] is True
        assert status["readiness_reason"].startswith("Ready for")
        assert "fake-" not in repr(status)


def test_meralion_trial_free_is_quota_gated_but_not_usd_metered(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_MERALION_TRANSCRIPTION", "true")
    monkeypatch.setenv("MERALION_API_KEY", "fake-meralion-key")
    monkeypatch.setenv("MERALION_BILLING_MODE", "trial_free")

    status = get_runtime_model_status("MERaLiON-3-3B-ASR-Consortium", load_config())

    assert status["runnable"] is True
    assert status["provider_code"] == "M3ASR"
    assert status["cost_cap_included"] is False
    assert status["price_per_minute_usd"] == 0.0
    assert "quota-limited trial run" in status["readiness_reason"]
    assert "fake-meralion-key" not in repr(status)


def test_qwen_filetrans_requires_workspace_key_and_complete_private_oss(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-workspace-key")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_ID", "fake-oss-id")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "fake-oss-secret")
    monkeypatch.setenv("ALIYUN_OSS_ENDPOINT", "https://oss-ap-southeast-1.aliyuncs.com")
    monkeypatch.setenv("ALIYUN_OSS_BUCKET", "fake-private-bucket")

    status = get_runtime_model_status(
        "qwen-audio-3.0-asr-flash-filetrans", load_config()
    )

    assert status["provider_code"] == "QwenA3FT"
    assert status["runnable"] is True
    assert status["price_per_minute_usd"] == 0.0021
    assert status["cost_cap_included"] is True
    assert "fake-" not in repr(status)

    monkeypatch.delenv("ALIYUN_OSS_BUCKET")
    blocked = get_runtime_model_status(
        "qwen-audio-3.0-asr-flash-filetrans", load_config()
    )
    assert blocked["runnable"] is False
    assert "private OSS setup is incomplete" in blocked["readiness_reason"]


def test_qwen_filetrans_allows_beijing_temporary_upload_with_api_key_only(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-beijing-key")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "dashscope_temporary")
    monkeypatch.setenv(
        "DASHSCOPE_API_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
    )

    status = get_runtime_model_status(
        "qwen-audio-3.0-asr-flash-filetrans", load_config()
    )

    assert status["runnable"] is True
    assert status["credential_available"] is True
    assert status["staging_mode"] == "dashscope_temporary"


def test_qwen_filetrans_allows_bounded_data_uri_with_international_key_only(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-international-key")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "data_uri")
    monkeypatch.setenv(
        "DASHSCOPE_API_BASE_URL", "https://dashscope-intl.aliyuncs.com/api/v1"
    )

    status = get_runtime_model_status(
        "qwen-audio-3.0-asr-flash-filetrans", load_config()
    )

    assert status["runnable"] is True
    assert status["credential_available"] is True
    assert status["staging_mode"] == "data_uri"
    assert "fake-" not in repr(status)


def test_qwen_filetrans_rejects_temporary_upload_with_singapore_endpoint(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-singapore-key")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "dashscope_temporary")
    monkeypatch.setenv(
        "DASHSCOPE_API_BASE_URL", "https://dashscope-intl.aliyuncs.com/api/v1"
    )

    status = get_runtime_model_status(
        "qwen-audio-3.0-asr-flash-filetrans", load_config()
    )

    assert status["runnable"] is False
    assert "Beijing-only" in status["readiness_reason"]
