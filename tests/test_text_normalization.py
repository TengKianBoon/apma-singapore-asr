import json
from pathlib import Path

import pytest

from services import job as job_mod
from services.config import Config, load_config
from services.text_normalization import (
    normalize_generated_text,
    normalize_segment_texts,
    simplified_chinese_instruction,
)
from services.transcription.external_adapter import GeminiTranscriber, MeralionTranscriber
from services.transcription.openai_adapter import OpenAITranscriber


TRADITIONAL_MIXED_TEXT = (
    "臺灣家人講福建話：伊講欲來開會。English and Bahasa Indonesia stay unchanged."
)
SIMPLIFIED_MIXED_TEXT = (
    "台湾家人讲福建话：伊讲欲来开会。English and Bahasa Indonesia stay unchanged."
)


def _job_chunk(tmp_path: Path, job_id: str) -> tuple[Config, dict]:
    cfg = Config(
        storage_path=str(tmp_path / "jobs"),
        dry_run=False,
        max_cost_per_job_usd=1.0,
    )
    job_dir = job_mod.create_job(job_id, cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"synthetic audio")
    return cfg, {
        "path": str(chunk_path),
        "filename": chunk_path.name,
        "start_sec": 0.0,
        "end_sec": 1.0,
        "actual_bytes": chunk_path.stat().st_size,
    }


def test_simplified_chinese_rule_converts_mandarin_and_hokkien_han_text_only():
    converted, metadata = normalize_generated_text(TRADITIONAL_MIXED_TEXT)

    assert converted == SIMPLIFIED_MIXED_TEXT
    assert "English and Bahasa Indonesia stay unchanged." in converted
    assert metadata["preference"] == "simplified"
    assert metadata["method"] == "opencc_t2s"
    assert metadata["changed"] is True
    assert metadata["translation_performed"] is False
    assert metadata["source_text_sha256"] != metadata["output_text_sha256"]
    assert "Hokkien" in metadata["applies_to"]


def test_preserve_mode_and_segment_conversion_are_deterministic():
    preserved, metadata = normalize_generated_text(TRADITIONAL_MIXED_TEXT, "preserve")
    first = normalize_segment_texts([{"speaker": "spk:0", "text": "開會"}])
    second = normalize_segment_texts([{"speaker": "spk:0", "text": "開會"}])

    assert preserved == TRADITIONAL_MIXED_TEXT
    assert metadata["changed"] is False
    assert first == second == [{"speaker": "spk:0", "text": "开会"}]


def test_config_defaults_to_simplified_and_rejects_unknown_value(monkeypatch):
    monkeypatch.delenv("CHINESE_SCRIPT_PREFERENCE", raising=False)
    assert load_config().chinese_script_preference == "simplified"
    monkeypatch.setenv("CHINESE_SCRIPT_PREFERENCE", "traditional")
    with pytest.raises(ValueError, match="CHINESE_SCRIPT_PREFERENCE"):
        load_config()


def test_instruction_preserves_hokkien_language_while_using_simplified_script():
    instruction = simplified_chinese_instruction("simplified")

    assert "Simplified Chinese" in instruction
    assert "Hokkien" in instruction
    assert "do not translate Hokkien into Mandarin" in instruction


def test_openai_canonical_text_is_simplified_but_raw_provider_evidence_is_unchanged(
    tmp_path,
):
    cfg, chunk = _job_chunk(tmp_path, "openai-script-test")
    cfg.enable_live_openai_transcription = True
    cfg.openai_api_key = "fake-key"
    cfg.openai_model = "gpt-transcribe"
    raw_response = {"text": TRADITIONAL_MIXED_TEXT}

    class FakeClient:
        def transcribe(self, path, model=None):
            return {
                "text": TRADITIONAL_MIXED_TEXT,
                "raw_response": raw_response,
                "cost_usd": 0.001,
            }

    result = OpenAITranscriber(client=FakeClient()).transcribe_chunk(
        "openai-script-test", chunk, cfg
    )
    provider_artifact = json.loads(
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    )

    assert result["text"] == SIMPLIFIED_MIXED_TEXT
    assert result["script_normalization"]["changed"] is True
    assert provider_artifact["response"] == raw_response


def test_meralion_canonical_text_is_simplified_but_raw_provider_evidence_is_unchanged(
    tmp_path,
):
    cfg, chunk = _job_chunk(tmp_path, "meralion-script-test")
    cfg.enable_live_meralion_transcription = True
    cfg.meralion_api_key = "fake-key"
    provider_response = {
        "id": "request-1",
        "choices": [{"message": {"content": TRADITIONAL_MIXED_TEXT}}],
    }

    class FakeClient:
        def transcribe(self, file_path, model):
            return provider_response

    result = MeralionTranscriber(client=FakeClient()).transcribe_chunks(
        "meralion-script-test", [chunk], cfg
    )[0]
    provider_artifact = json.loads(
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    )

    assert result["text"] == SIMPLIFIED_MIXED_TEXT
    assert result["script_normalization"]["changed"] is True
    assert provider_artifact["raw_response"] == provider_response


def test_gemini_canonical_text_is_simplified_but_raw_provider_evidence_is_unchanged(
    tmp_path,
):
    cfg, chunk = _job_chunk(tmp_path, "gemini-script-test")
    cfg.enable_live_gemini_transcription = True
    cfg.gemini_api_key = "fake-key"
    cfg.gemini_transcription_model = cfg.gemini_transcribe_model
    provider_response = {
        "responseId": "request-2",
        "candidates": [
            {"content": {"parts": [{"text": TRADITIONAL_MIXED_TEXT}]}}
        ],
    }

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            return provider_response

    result = GeminiTranscriber(client=FakeClient()).transcribe_chunks(
        "gemini-script-test", [chunk], cfg
    )[0]
    provider_artifact = json.loads(
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    )

    assert result["text"] == SIMPLIFIED_MIXED_TEXT
    assert result["script_normalization"]["changed"] is True
    assert provider_artifact["raw_response"] == provider_response
