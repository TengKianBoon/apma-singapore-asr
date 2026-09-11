import pytest

from services.config import load_config
from services.transcription.models import (
    configured_model_specs,
    configure_model_chunking,
    get_model_spec,
)


def test_transcription_registry_has_stable_product_order(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_RECOMMENDED_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_PREMIUM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_DIARIZE_MODEL", raising=False)
    cfg = load_config()

    specs = configured_model_specs(cfg)

    assert [spec.model_id for spec in specs] == [
        "gpt-4o-mini-transcribe",
        "gpt-transcribe",
        "gpt-4o-transcribe",
        "gpt-4o-transcribe-diarize",
        "MERaLiON-3-3B-ASR-Consortium",
        "gemini-3.5-transcribe",
        "qwen-audio-3.0-asr-flash-filetrans",
    ]
    assert specs[0].tier_label == "Economy"
    assert specs[1].tier_label == "Recommended Multilingual"
    assert specs[4].provider == "meralion"
    assert specs[4].live_adapter_ready is True
    assert specs[4].diarization is False
    assert specs[5].provider == "google"
    assert specs[5].diarization is True
    assert specs[6].provider == "alibaba"
    assert specs[6].provider_code == "QwenA3FT"
    assert specs[6].diarization is True


def test_diarization_model_uses_diarized_json_response():
    cfg = load_config()
    spec = get_model_spec(cfg.openai_diarize_model, cfg)

    assert spec.diarization is True
    assert spec.response_format == "diarized_json"


def test_duplicate_configured_model_ids_are_rejected():
    cfg = load_config()
    cfg.openai_recommended_model = cfg.openai_model

    with pytest.raises(ValueError, match="Duplicate transcription model ID"):
        configured_model_specs(cfg)


def test_unknown_model_is_rejected():
    cfg = load_config()

    with pytest.raises(ValueError, match="Unsupported transcription model"):
        get_model_spec("not-a-real-model", cfg)


def test_qwen_data_uri_chunking_respects_inline_duration_and_byte_limits():
    cfg = load_config()
    cfg.qwen_filetrans_staging_mode = "data_uri"

    model_cfg = configure_model_chunking(cfg, cfg.qwen_filetrans_model)

    assert model_cfg.max_chunk_duration_sec <= cfg.provider_safe_chunk_duration_sec
    assert model_cfg.default_chunk_duration_sec <= cfg.provider_safe_chunk_duration_sec
    assert model_cfg.target_max_chunk_bytes <= cfg.qwen_filetrans_data_uri_limit_bytes
    assert model_cfg.hard_max_chunk_bytes <= cfg.qwen_filetrans_data_uri_limit_bytes
