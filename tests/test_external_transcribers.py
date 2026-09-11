import json
import io
import urllib.error
import wave
from pathlib import Path

import pytest

from scripts import local_dashboard
from services import job as job_mod
from services.config import Config
from services.transcription import get_transcriber
from services.transcription import external_adapter
from services.transcription.external_adapter import (
    DashScopeTemporaryFileUploader,
    GeminiTranscriber,
    GeminiTranscriptionHttpClient,
    MeralionTranscriber,
    MeralionTranscriptionHttpClient,
    QwenFiletransHttpClient,
    QwenFiletransTranscriber,
)


def _job_with_chunk(tmp_path: Path, job_id: str = "provider-test") -> tuple[Config, dict]:
    cfg = Config(storage_path=str(tmp_path / "jobs"), dry_run=False, max_cost_per_job_usd=1.0)
    job_dir = job_mod.create_job(job_id, cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    with wave.open(str(chunk_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    chunk = {
        "chunk_id": 1,
        "filename": chunk_path.name,
        "path": str(chunk_path),
        "start_sec": 0.0,
        "end_sec": 1.0,
    }
    return cfg, chunk


def test_provider_request_retains_safe_http_status_after_retries(monkeypatch):
    cfg = Config(dry_run=False)
    cfg.external_transcription_max_retries = 1

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    request = external_adapter.urllib.request.Request("https://provider.example.test")

    with pytest.raises(RuntimeError, match="HTTP 503 after 1 attempt"):
        external_adapter._provider_request(request, cfg)


def test_provider_request_retains_safe_timeout_type_after_retries(monkeypatch):
    cfg = Config(dry_run=False, external_transcription_max_retries=1)

    def fake_urlopen(request, timeout):
        raise TimeoutError("private network detail must not be displayed")

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    request = external_adapter.urllib.request.Request("https://provider.example.test")

    with pytest.raises(
        RuntimeError,
        match=r"Provider request failed after 1 attempt \(TimeoutError\)$",
    ) as exc_info:
        external_adapter._provider_request(request, cfg)

    assert "private network detail" not in str(exc_info.value)


def test_provider_request_honors_retry_after_and_stops_after_three_attempts(monkeypatch):
    cfg = Config(
        dry_run=False,
        external_transcription_max_retries=3,
        external_transcription_retry_backoff_base=0.5,
        external_transcription_retry_max_delay_seconds=10.0,
    )
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {"Retry-After": "2"},
            None,
        )

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(external_adapter.time, "sleep", sleeps.append)
    request = external_adapter.urllib.request.Request("https://provider.example.test")

    with pytest.raises(
        RuntimeError,
        match=(
            "rate/quota limit returned HTTP 429 after 3 attempts; completed chunks "
            "are retained"
        ),
    ):
        external_adapter._provider_request(request, cfg)

    assert len(calls) == 3
    assert sleeps == [2.0, 2.0]


def test_provider_retry_after_is_bounded(monkeypatch):
    cfg = Config(
        external_transcription_max_retries=2,
        external_transcription_retry_max_delay_seconds=5.0,
    )
    sleeps = []

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {"Retry-After": "3600"},
            None,
        )

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(external_adapter.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="HTTP 429 after 2 attempts"):
        external_adapter._provider_request(
            external_adapter.urllib.request.Request("https://provider.example.test"),
            cfg,
        )

    assert sleeps == [5.0]


def test_provider_request_honors_google_structured_retry_delay(monkeypatch):
    cfg = Config(
        external_transcription_max_retries=2,
        external_transcription_retry_backoff_base=1.0,
        external_transcription_retry_max_delay_seconds=60.0,
    )
    sleeps = []
    calls = 0
    error_body = json.dumps(
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "Quota reached. Please retry in 45.25s.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "45.25s",
                    }
                ],
            }
        }
    ).encode("utf-8")

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {},
                io.BytesIO(error_body),
            )
        return _FakeHttpResponse({"text": "completed after provider delay"})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(external_adapter.time, "sleep", sleeps.append)

    payload = external_adapter._provider_request(
        external_adapter.urllib.request.Request("https://provider.example.test"), cfg
    )

    assert payload["text"] == "completed after provider delay"
    assert calls == 2
    assert sleeps == [45.25]


def test_meralion_live_adapter_writes_provenance_and_retained_outputs(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "meralion-test")
    cfg.enable_live_meralion_transcription = True
    cfg.meralion_api_key = "fake-meralion-key"
    cfg.meralion_price_per_minute_usd = 0.01

    class FakeClient:
        def transcribe(self, file_path, model):
            assert file_path.suffix == ".wav"
            assert model == "MERaLiON-3-3B-ASR-Consortium"
            return {
                "id": "meralion-request-1",
                "model": "MERaLiON-3-3B-ASR-Consortium",
                "modelVersion": "hosted-2026-08",
                "choices": [
                    {"message": {"content": "Regional multilingual transcript."}}
                ],
                "cost_usd": 0.0001,
            }

    result = MeralionTranscriber(client=FakeClient()).transcribe_chunks(
        "meralion-test", [chunk], cfg
    )[0]

    assert result["provider_code"] == "M3ASR"
    assert result["resolved_model_version"] == "hosted-2026-08"
    assert result["request_id"] == "meralion-request-1"
    assert result["text"] == "Regional multilingual transcript."
    assert Path(result["provider_artifact_path"]).exists()
    assert Path(result["transcript_path"]).exists()
    retained = result["retained_output_paths"]
    assert Path(retained["json"]).name.endswith("M3ASR.json")
    assert Path(retained["html"]).name.endswith("M3ASR.html")
    assert Path(retained["json"]).exists()
    assert Path(retained["html"]).exists()
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / "meralion-test")
    assert manifest["transcription"]["retained_outputs"] == retained
    persisted = [
        json.dumps(manifest),
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8"),
        Path(result["transcript_path"]).read_text(encoding="utf-8"),
        Path(retained["json"]).read_text(encoding="utf-8"),
        Path(retained["html"]).read_text(encoding="utf-8"),
    ]
    assert all("fake-meralion-key" not in item for item in persisted)


def test_external_provider_long_job_resume_reuses_completed_chunks(tmp_path):
    cfg, first_chunk = _job_with_chunk(tmp_path, "meralion-resume-test")
    cfg.enable_live_meralion_transcription = True
    cfg.meralion_api_key = "fake-meralion-key"
    chunks = [first_chunk]
    job_dir = Path(cfg.storage_path) / "meralion-resume-test"
    for index in (2, 3):
        chunk_path = job_dir / "chunks" / f"chunk-{index:05d}.wav"
        with wave.open(str(chunk_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(bytes([index, 0]) * 8000)
        chunks.append(
            {
                "chunk_id": index,
                "filename": chunk_path.name,
                "path": str(chunk_path),
                "start_sec": float(index - 1),
                "end_sec": float(index),
            }
        )

    calls: list[str] = []
    fail_middle_once = {"value": True}

    class FakeClient:
        def transcribe(self, file_path, model):
            calls.append(file_path.name)
            if file_path.name == "chunk-00002.wav" and fail_middle_once["value"]:
                fail_middle_once["value"] = False
                raise RuntimeError("Provider request failed with HTTP 503 after 3 attempts")
            return {
                "id": f"request-{file_path.stem}",
                "choices": [
                    {"message": {"content": f"Transcript for {file_path.stem}"}}
                ],
            }

    with pytest.raises(RuntimeError, match="HTTP 503"):
        MeralionTranscriber(client=FakeClient()).transcribe_chunks(
            "meralion-resume-test", chunks, cfg
        )

    interrupted = job_mod.read_manifest(job_dir)["transcription"]
    assert interrupted["state"] == "failed"
    assert interrupted["retryable"] is True
    assert [entry["status"] for entry in interrupted["chunks"]] == [
        "completed",
        "failed",
    ]

    resumed = MeralionTranscriber(client=FakeClient()).transcribe_chunks(
        "meralion-resume-test", chunks, cfg
    )

    assert calls.count("chunk-00001.wav") == 1
    assert calls.count("chunk-00002.wav") == 2
    assert calls.count("chunk-00003.wav") == 1
    assert [result["cached"] for result in resumed] == [True, False, False]
    assert [result["text"] for result in resumed] == [
        "Transcript for chunk-00001",
        "Transcript for chunk-00002",
        "Transcript for chunk-00003",
    ]
    completed = job_mod.read_manifest(job_dir)["transcription"]
    assert completed["state"] == "completed"
    assert all(entry["status"] == "completed" for entry in completed["chunks"])


def test_meralion_timestamp_mode_retains_hosted_words_and_global_offsets(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "meralion-timestamp-test")
    cfg.enable_live_meralion_transcription = True
    cfg.enable_provider_timestamps = True
    cfg.meralion_api_key = "fake-meralion-key"
    cfg.meralion_price_per_minute_usd = 0.01
    chunk["start_sec"] = 915.0
    chunk["end_sec"] = 916.0
    provider_response = {
        "id": "meralion-timestamp-1",
        "choices": [
            {
                "message": {
                    "content": "hello dunia",
                    "words": [
                        {
                            "word": "hello",
                            "start": 0.1,
                            "end": 0.4,
                            "speaker": None,
                        },
                        {"word": "dunia", "start": 0.5, "end": 0.9},
                    ],
                }
            }
        ],
    }

    class FakeClient:
        def transcribe(self, file_path, model):
            return provider_response

    result = MeralionTranscriber(client=FakeClient()).transcribe_chunks(
        "meralion-timestamp-test", [chunk], cfg
    )[0]

    words = result["timing"]["provider_native"]["words"]
    assert len(words) == 2
    assert result["timing"]["capability_status"] == "available"
    assert words[0]["global_start_sec"] == 915.1
    assert words[-1]["global_end_sec"] == 915.9
    artifact = json.loads(Path(result["provider_artifact_path"]).read_text(encoding="utf-8"))
    assert artifact["raw_response"] == provider_response
    assert "words" not in artifact["raw_response"]


def test_gem37f_live_adapter_uses_low_thinking_and_writes_provenance(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "gem37f-test")
    cfg.enable_live_gemini_transcription = True
    cfg.gemini_api_key = "fake-gemini-key"
    cfg.gemini_transcription_model = cfg.gemini_flash_model
    calls = []

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            calls.append((model, diarized, thinking_level))
            return {
                "request_id": "gem37f-request-1",
                "modelVersion": "gem37f-resolved",
                "candidates": [{"content": {"parts": [{"text": "Mixed-language transcript."}]}}],
            }

    result = GeminiTranscriber(client=FakeClient()).transcribe_chunks(
        "gem37f-test", [chunk], cfg
    )[0]

    assert calls == [("gemini-3.7-flash", False, "low")]
    assert result["provider_code"] == "Gem37F"
    assert result["diarized"] is False
    assert result["resolved_model_version"] == "gem37f-resolved"
    assert Path(result["provider_artifact_path"]).exists()
    assert "fake-gemini-key" not in Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    retained = result["retained_output_paths"]
    assert Path(retained["json"]).name.endswith("Gem37F.json")
    assert Path(retained["html"]).name.endswith("Gem37F.html")
    assert Path(retained["json"]).exists()
    assert Path(retained["html"]).exists()
    persisted = [
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8"),
        Path(result["transcript_path"]).read_text(encoding="utf-8"),
        Path(retained["json"]).read_text(encoding="utf-8"),
        Path(retained["html"]).read_text(encoding="utf-8"),
    ]
    assert all("fake-gemini-key" not in item for item in persisted)


def test_google_routes_use_inline_safe_raw_audio_limit():
    cfg = Config()
    cfg.gemini_inline_file_size_limit_bytes = 20 * 1024 * 1024
    cfg.gemini_generate_content_inline_audio_limit_bytes = 9 * 1024 * 1024
    cfg.gemini_transcription_model = cfg.gemini_flash_model

    assert GeminiTranscriber()._file_limit(cfg) == 9 * 1024 * 1024

    cfg.gemini_transcription_model = cfg.gemini_transcribe_model
    assert GeminiTranscriber()._file_limit(cfg) == 9 * 1024 * 1024


def test_meralion_uses_json_base64_safe_raw_audio_limit():
    cfg = Config()
    cfg.meralion_file_size_limit_bytes = 25 * 1024 * 1024
    cfg.meralion_json_audio_limit_bytes = 9 * 1024 * 1024

    assert MeralionTranscriber()._file_limit(cfg) == 9 * 1024 * 1024


def test_gem37f_empty_response_retains_raw_finish_reason(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "gem37f-empty-response")
    cfg.enable_live_gemini_transcription = True
    cfg.gemini_api_key = "fake-gemini-key"
    cfg.gemini_transcription_model = cfg.gemini_flash_model
    provider_response = {
        "responseId": "gem37f-empty-1",
        "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
    }

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            return provider_response

    with pytest.raises(
        RuntimeError,
        match="no transcript text.*finish reason=MAX_TOKENS.*response retained",
    ):
        GeminiTranscriber(client=FakeClient()).transcribe_chunks(
            "gem37f-empty-response", [chunk], cfg
        )

    artifacts = list(
        (Path(cfg.storage_path) / "gem37f-empty-response" / "providers" / "Gem37F").glob(
            "**/*.json"
        )
    )
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["raw_response"] == provider_response
    assert artifact["request_features"]["audio_file_bytes"] == Path(
        chunk["path"]
    ).stat().st_size
    assert "fake-gemini-key" not in artifacts[0].read_text(encoding="utf-8")


def test_gem35t_live_adapter_preserves_diarized_segments(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "gem35t-test")
    cfg.enable_live_gemini_transcription = True
    cfg.gemini_api_key = "fake-gemini-key"
    cfg.gemini_transcription_model = cfg.gemini_transcribe_model

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            assert model == "gemini-3.5-transcribe"
            assert diarized is True
            return {
                "responseId": "gem35t-request-1",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "audioTranscription": {
                                        "speakerLabel": "spk_1",
                                        "words": [
                                            {
                                                "word": "Hello",
                                                "startOffset": "0.100s",
                                                "endOffset": "0.450s",
                                            },
                                            {
                                                "word": "world",
                                                "startOffset": "0.500s",
                                                "endOffset": "0.850s",
                                            },
                                        ],
                                    }
                                }
                            ]
                        }
                    }
                ],
            }

    result = GeminiTranscriber(client=FakeClient()).transcribe_chunks(
        "gem35t-test", [chunk], cfg
    )[0]

    assert result["provider_code"] == "Gem35T"
    assert result["diarized"] is True
    assert result["request_id"] == "gem35t-request-1"
    assert result["text"] == "Hello world"
    assert result["segments"][0]["speaker"] == "spk_1"
    assert result["segments"][0]["start_sec"] == 0.1
    assert result["segments"][0]["end_sec"] == 0.85
    retained = result["retained_output_paths"]
    assert Path(retained["json"]).name.endswith("Gem35T.json")
    assert Path(retained["html"]).name.endswith("Gem35T.html")
    persisted = [
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8"),
        Path(result["transcript_path"]).read_text(encoding="utf-8"),
        Path(retained["json"]).read_text(encoding="utf-8"),
        Path(retained["html"]).read_text(encoding="utf-8"),
    ]
    assert all("fake-gemini-key" not in item for item in persisted)


def test_gem35t_interaction_annotations_are_retained_without_rewriting_raw(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "gem35t-timestamp-test")
    cfg.enable_live_gemini_transcription = True
    cfg.enable_provider_timestamps = True
    cfg.enable_gemini_speaker_attribution = True
    cfg.gemini_api_key = "fake-gemini-key"
    cfg.gemini_transcription_model = cfg.gemini_transcribe_model
    chunk["start_sec"] = 915.0
    chunk["end_sec"] = 916.0
    provider_response = {
        "id": "gem35t-interaction-1",
        "model": "gemini-3.5-transcribe",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": "hello dunia",
                        "annotations": [
                            {
                                "type": "word_info",
                                "text": "hello",
                                "start_offset": "0.100s",
                                "end_offset": "0.400s",
                                "speaker": "spk_1",
                            },
                            {
                                "type": "word_info",
                                "text": "dunia",
                                "start_offset": "0.500s",
                                "end_offset": "0.900s",
                                "speaker": "spk_1",
                            },
                        ],
                    }
                ],
            }
        ],
    }

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            return provider_response

    result = GeminiTranscriber(client=FakeClient()).transcribe_chunks(
        "gem35t-timestamp-test", [chunk], cfg
    )[0]

    words = result["timing"]["provider_native"]["words"]
    assert result["text"] == "hello dunia"
    assert result["provider_text"] == "hello dunia"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["speaker"] == "spk_1"
    assert result["segments"][0]["text"] == "hello dunia"
    assert result["segments"][0]["text_derivation"] == (
        "language_aware_join_of_provider_word_annotations"
    )
    assert result["timing"]["capability_status"] == "available"
    assert len(words) == 2
    assert words[0]["speaker"] == "spk_1"
    assert words[0]["provider_speaker"] == "spk_1"
    assert words[0]["raw_provider_annotation"] == provider_response["steps"][0]["content"][0]["annotations"][0]
    assert words[0]["provider_start"] == "0.100s"
    assert words[0]["global_start_sec"] == 915.1
    assert words[-1]["global_end_sec"] == 915.9
    artifact = json.loads(Path(result["provider_artifact_path"]).read_text(encoding="utf-8"))
    assert artifact["raw_response"] == provider_response
    assert artifact["request_features"]["gemini_speaker_attribution"] is True
    assert result["speaker_attribution"]["provider_speaker_ids"] == ["spk_1"]
    assert result["speaker_attribution"]["names_invented"] is False


def test_gem35t_interaction_builds_exact_native_speaker_turns_from_byte_spans(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "gem35t-native-turns")
    cfg.enable_live_gemini_transcription = True
    cfg.enable_provider_timestamps = True
    cfg.enable_gemini_speaker_attribution = True
    cfg.gemini_api_key = "fake-gemini-key"
    cfg.gemini_transcription_model = cfg.gemini_transcribe_model
    provider_text = "你好。\nHello world"
    encoded = provider_text.encode("utf-8")
    hello_start = encoded.index(b"Hello")
    provider_response = {
        "id": "gem35t-native-turns-1",
        "model": "gemini-3.5-transcribe",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": provider_text,
                        "annotations": [
                            {
                                "type": "word_info",
                                "text": "你",
                                "start_index": 0,
                                "end_index": 3,
                                "start_offset": "0.100s",
                                "end_offset": "0.200s",
                                "speaker": "spk_1",
                            },
                            {
                                "type": "word_info",
                                "text": "好",
                                "start_index": 3,
                                "end_index": 6,
                                "start_offset": "0.200s",
                                "end_offset": "0.300s",
                                "speaker": "spk_1",
                            },
                            {
                                "type": "word_info",
                                "text": "Hello",
                                "start_index": hello_start,
                                "end_index": hello_start + 5,
                                "start_offset": "0.500s",
                                "end_offset": "0.700s",
                                "speaker": "spk_2",
                            },
                            {
                                "type": "word_info",
                                "text": "world",
                                "start_index": hello_start + 6,
                                "end_index": len(encoded),
                                "start_offset": "0.800s",
                                "end_offset": "0.950s",
                                "speaker": "spk_2",
                            },
                        ],
                    }
                ],
            }
        ],
    }

    class FakeClient:
        def transcribe(self, file_path, model, *, diarized, thinking_level):
            return provider_response

    result = GeminiTranscriber(client=FakeClient()).transcribe_chunks(
        "gem35t-native-turns", [chunk], cfg
    )[0]

    assert result["provider_text"] == provider_text
    assert [item["speaker"] for item in result["segments"]] == ["spk_1", "spk_2"]
    assert [item["text"] for item in result["segments"]] == ["你好", "Hello world"]
    assert all(
        item["text_derivation"] == "provider_utf8_byte_span"
        for item in result["segments"]
    )
    retained_html = Path(result["retained_output_paths"]["html"]).read_text(
        encoding="utf-8"
    )
    assert "Provider-native speaker turns" in retained_html
    assert "spk_1" in retained_html and "spk_2" in retained_html


def test_factory_exposes_meralion_but_dashboard_rejects_removed_gemini_flash():
    cfg = Config(
        meralion_api_key="fake-meralion-key",
        gemini_api_key="fake-gemini-key",
        meralion_price_per_minute_usd=0.01,
    )

    meralion_cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        cfg.meralion_transcription_model,
        run_budget_cap_usd=0.05,
        cfg=cfg,
    )
    assert meralion_cfg.transcription_engine == "meralion"
    assert meralion_cfg.enable_live_meralion_transcription is True
    with pytest.raises(ValueError, match="not configured"):
        local_dashboard._build_run_config(
            local_dashboard.LIVE_MODE,
            cfg.gemini_flash_model,
            run_budget_cap_usd=0.05,
            cfg=cfg,
        )
    assert isinstance(get_transcriber("meralion"), MeralionTranscriber)

    gemini_cfg = Config(gemini_api_key="fake-gemini-key")
    gemini_cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        gemini_cfg.gemini_transcribe_model,
        run_budget_cap_usd=0.05,
        cfg=gemini_cfg,
    )
    assert gemini_cfg.transcription_engine == "gemini"
    assert gemini_cfg.enable_live_gemini_transcription is True
    assert isinstance(get_transcriber("gemini"), GeminiTranscriber)
    assert isinstance(get_transcriber("m3asr"), MeralionTranscriber)
    assert isinstance(get_transcriber("gem37f"), GeminiTranscriber)
    assert isinstance(get_transcriber("gem35t"), GeminiTranscriber)
    qwen_cfg = Config(
        dashscope_api_key="fake-dashscope-key",
        aliyun_oss_access_key_id="fake-oss-id",
        aliyun_oss_access_key_secret="fake-oss-secret",
        aliyun_oss_endpoint="https://oss-ap-southeast-1.aliyuncs.com",
        aliyun_oss_bucket="fake-private-bucket",
    )
    qwen_cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        qwen_cfg.qwen_filetrans_model,
        run_budget_cap_usd=0.05,
        cfg=qwen_cfg,
    )
    assert qwen_cfg.transcription_engine == "qwen_filetrans"
    assert qwen_cfg.enable_live_qwen_filetrans_transcription is True
    assert isinstance(get_transcriber("qwen-filetrans"), QwenFiletransTranscriber)


def test_qwen_filetrans_http_client_stages_polls_redacts_and_deletes(tmp_path):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-authorized-audio")
    requests = []

    class FakeBucket:
        def __init__(self):
            self.uploaded = []
            self.deleted = []

        def put_object_from_file(self, key, path):
            self.uploaded.append((key, path))

        def sign_url(self, method, key, expiry, slash_safe):
            assert method == "GET"
            assert expiry == 21600
            assert slash_safe is True
            return "https://private-oss.example/audio?Signature=fake-secret-signature"

        def delete_object(self, key):
            self.deleted.append(key)

    def request_once(request, cfg):
        requests.append(request)
        body = json.loads(request.data.decode("utf-8"))
        assert request.get_header("X-dashscope-async") == "enable"
        assert body["model"] == "qwen-audio-3.0-asr-flash-filetrans"
        assert body["parameters"] == {
            "channel_id": [0],
            "diarization_enabled": True,
            "language_hints": ["zh", "en", "id", "ms"],
        }
        assert body["input"]["file_urls"][0].startswith("https://private-oss.example/")
        return {
            "request_id": "submit-request",
            "output": {"task_status": "PENDING", "task_id": "task-123"},
        }

    def request_retry(request, cfg):
        requests.append(request)
        if "/tasks/" in request.full_url:
            return {
                "request_id": "poll-request",
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "file_url": "https://private-oss.example/audio?Signature=secret",
                            "transcription_url": "https://result.example/task.json?Signature=secret",
                        }
                    ],
                },
                "usage": {"duration": 8},
            }
        return {
            "file_url": "https://private-oss.example/audio?Signature=secret",
            "properties": {"original_duration_in_milliseconds": 8000},
            "transcripts": [
                {
                    "channel_id": 0,
                    "text": "你好 Hello",
                    "sentences": [
                        {
                            "begin_time": 100,
                            "end_time": 7000,
                            "text": "你好 Hello",
                            "speaker_id": 0,
                            "words": [
                                {"begin_time": 100, "end_time": 500, "text": "你好"},
                                {"begin_time": 600, "end_time": 1000, "text": "Hello"},
                            ],
                        }
                    ],
                }
            ],
        }

    cfg = Config(dry_run=False)
    bucket = FakeBucket()
    client = QwenFiletransHttpClient(
        api_base_url=cfg.dashscope_api_base_url,
        api_key="fake-workspace-key",
        bucket=bucket,
        cfg=cfg,
        request_once=request_once,
        request_retry=request_retry,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    response = client.transcribe(audio_path, cfg.qwen_filetrans_model)

    assert len(bucket.uploaded) == 1
    assert bucket.deleted == [bucket.uploaded[0][0]]
    assert response["temporary_oss_cleanup"] == "deleted"
    assert response["task_id"] == "task-123"
    assert response["cost_usd"] == pytest.approx(8 / 60 * 0.0021)
    assert response["raw_response"]["file_url"] == "[REDACTED TEMPORARY SIGNED URL]"
    assert "fake-secret-signature" not in json.dumps(response)
    assert "Signature=secret" not in json.dumps(response)


def test_dashscope_temporary_uploader_uses_api_key_policy_and_hides_filename(tmp_path):
    audio_path = tmp_path / "private-meeting-name.wav"
    audio_path.write_bytes(b"synthetic-authorized-audio")
    seen = {}

    def request_retry(request, cfg):
        seen["policy_url"] = request.full_url
        assert request.get_header("Authorization") == "Bearer fake-beijing-key"
        return {
            "data": {
                "upload_host": "https://dashscope-file.example.oss-cn-beijing.aliyuncs.com",
                "upload_dir": "dashscope-instant/account/run",
                "oss_access_key_id": "temporary-id",
                "signature": "temporary-signature",
                "policy": "temporary-policy",
                "x_oss_object_acl": "private",
                "x_oss_forbid_overwrite": "true",
                "max_file_size_mb": 100,
            }
        }

    def multipart_upload(upload_url, fields, file_field, timeout_seconds):
        seen["upload_url"] = upload_url
        seen["fields"] = fields
        seen["file_field"] = file_field
        assert timeout_seconds == 120.0

    uploader = DashScopeTemporaryFileUploader(
        api_base_url="https://dashscope.aliyuncs.com/api/v1",
        api_key="fake-beijing-key",
        cfg=Config(dry_run=False),
        request_retry=request_retry,
        multipart_upload=multipart_upload,
    )

    uploaded_url = uploader.upload(
        audio_path, "qwen-audio-3.0-asr-flash-filetrans"
    )

    assert seen["policy_url"].startswith(
        "https://dashscope.aliyuncs.com/api/v1/uploads?action=getPolicy&"
    )
    assert seen["fields"][-1] == ("success_action_status", "200")
    assert seen["file_field"][0] == "file"
    assert seen["file_field"][2] == audio_path
    assert "private-meeting-name" not in uploaded_url
    assert uploaded_url.startswith("oss://dashscope-instant/account/run/")


def test_qwen_filetrans_http_client_accepts_beijing_api_key_only_staging(tmp_path):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-authorized-audio")
    requests = []

    class FakeTemporaryUploader:
        def upload(self, file_path, model):
            assert file_path == audio_path
            assert model == "qwen-audio-3.0-asr-flash-filetrans"
            return "oss://dashscope-instant/account/run/audio.wav"

    def request_once(request, cfg):
        requests.append(request)
        body = json.loads(request.data.decode("utf-8"))
        assert request.get_header("X-dashscope-ossresourceresolve") == "enable"
        assert body["input"]["file_urls"] == [
            "oss://dashscope-instant/account/run/audio.wav"
        ]
        return {
            "request_id": "submit-request",
            "output": {"task_status": "PENDING", "task_id": "task-123"},
        }

    def request_retry(request, cfg):
        requests.append(request)
        if "/tasks/" in request.full_url:
            return {
                "request_id": "poll-request",
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "file_url": "oss://dashscope-instant/account/run/audio.wav",
                            "transcription_url": "https://result.example/task.json",
                        }
                    ],
                },
                "usage": {"duration": 8},
            }
        return {
            "file_url": "oss://dashscope-instant/account/run/audio.wav",
            "transcripts": [{"channel_id": 0, "text": "Hello", "sentences": []}],
        }

    cfg = Config(
        dry_run=False,
        qwen_filetrans_staging_mode="dashscope_temporary",
        dashscope_api_base_url="https://dashscope.aliyuncs.com/api/v1",
    )
    client = QwenFiletransHttpClient(
        api_base_url=cfg.dashscope_api_base_url,
        api_key="fake-beijing-key",
        bucket=None,
        cfg=cfg,
        temporary_uploader=FakeTemporaryUploader(),
        request_once=request_once,
        request_retry=request_retry,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    response = client.transcribe(audio_path, cfg.qwen_filetrans_model)

    assert response["file_staging_mode"] == "dashscope_temporary"
    assert response["temporary_oss_cleanup"] == "provider_managed_expiry"
    assert response["provider_managed_retention_hours"] == 48
    assert "oss://" not in json.dumps(response["raw_response"])


def test_qwen_filetrans_factory_does_not_require_oss_library_for_temporary_mode():
    cfg = Config(
        dry_run=False,
        dashscope_api_key="fake-beijing-key",
        qwen_filetrans_staging_mode="dashscope_temporary",
        dashscope_api_base_url="https://dashscope.aliyuncs.com/api/v1",
    )

    client = QwenFiletransHttpClient.from_config(cfg)

    assert client.bucket is None
    assert isinstance(client.temporary_uploader, DashScopeTemporaryFileUploader)


def test_qwen_filetrans_data_uri_mode_needs_no_oss_and_redacts_source(tmp_path):
    audio_path = tmp_path / "synthetic.wav"
    audio_path.write_bytes(b"synthetic-authorized-audio")
    requests = []

    def request_once(request, cfg):
        requests.append(request)
        body = json.loads(request.data.decode("utf-8"))
        source = body["input"]["file_urls"][0]
        assert source.startswith("data:audio/wav;base64,")
        assert "synthetic.wav" not in source
        assert request.get_header("X-dashscope-ossresourceresolve") is None
        return {
            "request_id": "submit-data-uri",
            "output": {"task_status": "PENDING", "task_id": "task-data-uri"},
        }

    def request_retry(request, cfg):
        requests.append(request)
        if "/tasks/" in request.full_url:
            return {
                "request_id": "poll-data-uri",
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "file_url": "data:audio/wav;base64,private-audio",
                            "transcription_url": "https://result.example/data-uri.json",
                        }
                    ],
                },
                "usage": {"duration": 1},
            }
        return {
            "file_url": "data:audio/wav;base64,private-audio",
            "transcripts": [{"channel_id": 0, "text": "Hello", "sentences": []}],
        }

    cfg = Config(
        dry_run=False,
        qwen_filetrans_staging_mode="data_uri",
        dashscope_api_base_url="https://dashscope-intl.aliyuncs.com/api/v1",
    )
    client = QwenFiletransHttpClient(
        api_base_url=cfg.dashscope_api_base_url,
        api_key="fake-international-key",
        bucket=None,
        cfg=cfg,
        request_once=request_once,
        request_retry=request_retry,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )

    response = client.transcribe(audio_path, cfg.qwen_filetrans_model)

    assert response["file_staging_mode"] == "data_uri"
    assert response["temporary_oss_cleanup"] == "not_applicable"
    assert response["provider_managed_retention_hours"] is None
    assert response["raw_response"]["file_url"] == "[REDACTED TEMPORARY SIGNED URL]"
    assert "private-audio" not in json.dumps(response)


def test_qwen_filetrans_data_uri_mode_enforces_raw_audio_limit(tmp_path):
    audio_path = tmp_path / "oversize.wav"
    audio_path.write_bytes(b"12345")
    cfg = Config(
        dry_run=False,
        qwen_filetrans_staging_mode="data_uri",
        qwen_filetrans_data_uri_limit_bytes=4,
    )
    client = QwenFiletransHttpClient(
        api_base_url=cfg.dashscope_api_base_url,
        api_key="fake-international-key",
        bucket=None,
        cfg=cfg,
    )

    with pytest.raises(ValueError, match="data-URI file limit"):
        client.transcribe(audio_path, cfg.qwen_filetrans_model)


def test_qwen_filetrans_adapter_retains_native_speakers_timing_and_outputs(tmp_path):
    cfg, chunk = _job_with_chunk(tmp_path, "qwen-filetrans-test")
    cfg.enable_live_qwen_filetrans_transcription = True
    cfg.dashscope_api_key = "fake-workspace-key"
    cfg.aliyun_oss_access_key_id = "fake-oss-id"
    cfg.aliyun_oss_access_key_secret = "fake-oss-secret"
    cfg.aliyun_oss_endpoint = "https://oss-ap-southeast-1.aliyuncs.com"
    cfg.aliyun_oss_bucket = "fake-private-bucket"

    class FakeClient:
        def transcribe(self, file_path, model):
            return {
                "request_id": "qwen-request-1",
                "task_id": "qwen-task-1",
                "temporary_oss_cleanup": "deleted",
                "raw_response_redactions": ["temporary URL removed"],
                "cost_usd": 0.000035,
                "raw_response": {
                    "file_url": "[REDACTED TEMPORARY SIGNED URL]",
                    "properties": {"original_duration_in_milliseconds": 1000},
                    "transcripts": [
                        {
                            "channel_id": 0,
                            "text": "你好。Hello.",
                            "sentences": [
                                {
                                    "begin_time": 50,
                                    "end_time": 450,
                                    "text": "你好。",
                                    "speaker_id": 0,
                                    "words": [
                                        {"begin_time": 50, "end_time": 300, "text": "你好", "punctuation": "。"}
                                    ],
                                },
                                {
                                    "begin_time": 500,
                                    "end_time": 900,
                                    "text": "Hello.",
                                    "speaker_id": 1,
                                    "words": [
                                        {"begin_time": 500, "end_time": 850, "text": "Hello", "punctuation": "."}
                                    ],
                                },
                            ],
                        }
                    ],
                },
            }

    result = QwenFiletransTranscriber(client=FakeClient()).transcribe_chunks(
        "qwen-filetrans-test", [chunk], cfg
    )[0]

    assert result["provider_code"] == "QwenA3FT"
    assert result["provider_text"] == "你好。Hello."
    assert result["speaker_attribution"]["provider_speaker_ids"] == ["spk:0", "spk:1"]
    assert result["speaker_attribution"]["speaker_count"] == 2
    assert len(result["timing"]["provider_native"]["segments"]) == 2
    assert len(result["timing"]["provider_native"]["words"]) == 2
    assert result["timing"]["provider_native"]["segments"][1]["global_end_sec"] == 0.9
    artifact_text = Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    assert "qwen-task-1" in artifact_text
    assert "fake-workspace-key" not in artifact_text
    assert "fake-oss-secret" not in artifact_text
    assert Path(result["retained_output_paths"]["json"]).name.endswith("QwenA3FT.json")
    assert Path(result["retained_output_paths"]["html"]).name.endswith("QwenA3FT.html")


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_meralion_http_client_uses_json_audio_url_contract(tmp_path, monkeypatch):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-audio")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHttpResponse({"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(external_transcription_max_retries=1)
    client = MeralionTranscriptionHttpClient(
        api_url=cfg.meralion_api_url,
        api_key="fake-meralion-key",
        cfg=cfg,
    )

    client.transcribe(audio_path, cfg.meralion_transcription_model)

    request, timeout = requests[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.meralion.ai/v1/audio/transcriptions"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer fake-meralion-key"
    assert body["audio_url"].startswith("data:audio/wav;base64,")
    assert "model" not in body
    assert "fake-meralion-key" not in json.dumps(body)
    assert timeout == cfg.external_transcription_timeout_seconds


def test_meralion_timestamp_mode_uses_documented_hosted_contract(tmp_path, monkeypatch):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-audio")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _FakeHttpResponse({"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(external_transcription_max_retries=1, enable_provider_timestamps=True)
    client = MeralionTranscriptionHttpClient(
        api_url=cfg.meralion_api_url,
        api_key="fake-meralion-key",
        cfg=cfg,
    )

    client.transcribe(audio_path, cfg.meralion_transcription_model)

    request = requests[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.meralion.ai/v1/audio/transcriptions"
    assert body["return_timestamps"] is True
    assert "return_diarization" not in body
    assert body["audio_url"].startswith("data:audio/wav;base64,")


def test_gemini_http_client_uses_model_specific_generate_content_contract(
    tmp_path, monkeypatch
):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-audio")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _FakeHttpResponse({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(external_transcription_max_retries=1)
    client = GeminiTranscriptionHttpClient(
        api_base_url=cfg.gemini_api_base_url,
        api_key="fake-gemini-key",
        cfg=cfg,
    )

    client.transcribe(
        audio_path,
        cfg.gemini_flash_model,
        diarized=False,
        thinking_level="low",
    )
    flash_body = json.loads(requests[-1].data.decode("utf-8"))
    assert requests[-1].full_url.endswith("/models/gemini-3.7-flash:generateContent")
    assert flash_body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert flash_body["contents"][0]["parts"][0]["text"] == (
        "Generate a transcript of the speech. Keep each spoken language unchanged. "
        "Return only the transcript text."
    )

    client.transcribe(
        audio_path,
        cfg.gemini_transcribe_model,
        diarized=True,
        thinking_level="low",
    )
    transcribe_body = json.loads(requests[-1].data.decode("utf-8"))
    assert requests[-1].full_url.endswith("/models/gemini-3.5-transcribe:generateContent")
    assert transcribe_body["generationConfig"] == {
        "audioTranscriptionConfig": {"diarization": True}
    }
    assert list(transcribe_body["contents"][0]["parts"][0]) == ["inlineData"]
    assert "audioTranscriptionConfig" not in transcribe_body
    assert "fake-gemini-key" not in json.dumps(transcribe_body)


def test_gem35t_timestamp_mode_uses_verbatim_interactions_contract(tmp_path, monkeypatch):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-audio")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _FakeHttpResponse({"steps": []})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(external_transcription_max_retries=1, enable_provider_timestamps=True)
    client = GeminiTranscriptionHttpClient(
        api_base_url=cfg.gemini_api_base_url,
        api_key="fake-gemini-key",
        cfg=cfg,
    )

    client.transcribe(
        audio_path,
        cfg.gemini_transcribe_model,
        diarized=True,
        thinking_level="low",
    )

    request = requests[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url.endswith("/v1beta/interactions")
    assert body["model"] == "gemini-3.5-transcribe"
    assert body["input"][0]["type"] == "audio"
    assert body["input"][0]["mime_type"] == "audio/wav"
    assert body["generation_config"]["transcription_config"]["mode"] == {
        "type": "verbatim",
        "timestamp_granularities": ["word"],
    }
    assert body["store"] is False
    assert "fake-gemini-key" not in json.dumps(body)


def test_gem35t_speaker_mode_combines_diarization_and_word_timestamps(tmp_path, monkeypatch):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"synthetic-audio")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _FakeHttpResponse({"steps": []})

    monkeypatch.setattr(external_adapter.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(
        external_transcription_max_retries=1,
        enable_gemini_speaker_attribution=True,
    )
    client = GeminiTranscriptionHttpClient(
        api_base_url=cfg.gemini_api_base_url,
        api_key="fake-gemini-key",
        cfg=cfg,
    )

    client.transcribe(
        audio_path,
        cfg.gemini_transcribe_model,
        diarized=True,
        thinking_level="low",
    )

    body = json.loads(requests[0].data.decode("utf-8"))
    assert body["generation_config"]["transcription_config"]["mode"] == {
        "type": "verbatim",
        "timestamp_granularities": ["word"],
        "diarization_mode": "speaker",
    }
    assert "fake-gemini-key" not in json.dumps(body)
