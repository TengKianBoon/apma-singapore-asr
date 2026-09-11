import io
import json
import math
import shutil
from pathlib import Path

import pytest

from scripts import local_dashboard
from services.config import Config, load_config
from tests.helpers import generate_sine_wav


def test_dashboard_estimates_configured_openai_models(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    wav_path = tmp_path / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    estimate = local_dashboard.estimate_audio(wav_path, cfg)

    assert estimate["ok"] is True
    assert math.isclose(estimate["duration_seconds"], 1.0)
    assert "gpt-4o-mini-transcribe" in estimate["models"]
    assert estimate["models"]["gpt-4o-mini-transcribe"]["estimated_cost_usd"] > 0
    assert estimate["models"]["gpt-4o-mini-transcribe"]["estimated_cost_usd"] <= cfg.max_cost_per_job_usd
    assert estimate["models"]["gpt-4o-mini-transcribe"]["within_cap"] is False
    assert "gpt-transcribe" in estimate["models"]
    assert "gpt-4o-transcribe-diarize" in estimate["models"]
    assert estimate["models"]["gpt-4o-transcribe-diarize"]["estimated_cost_usd"] > 0

    status = local_dashboard.dashboard_status(cfg)
    diarize_option = next(item for item in status["models"] if item["model"] == "gpt-4o-transcribe-diarize")
    assert diarize_option["diarization"] is True
    assert status["models"][0]["model"] == "gpt-4o-mini-transcribe"
    assert status["models"][1]["tier_label"] == "Recommended Multilingual"
    assert "speaker labels" in local_dashboard.DASHBOARD_HTML
    gemini = next(item for item in status["models"] if item["provider"] == "google")
    assert gemini["runnable"] is False
    assert gemini["price_per_minute_usd"] == cfg.gemini_transcribe_price_per_minute_usd
    assert "Advanced provider details" in local_dashboard.DASHBOARD_HTML
    assert "setup required" in local_dashboard.DASHBOARD_HTML

    assert cfg.meralion_transcription_model in estimate["models"]
    assert cfg.gemini_flash_model not in estimate["models"]
    model_estimate = estimate["models"][cfg.gemini_transcribe_model]
    assert model_estimate["estimated_cost_usd"] > 0
    assert model_estimate["run_budget_cap_usd"] > 0
    assert model_estimate["within_cap"] is False


def test_dashboard_rejects_unsupported_audio_format(tmp_path):
    audio_path = tmp_path / "sample.txt"
    audio_path.write_bytes(b"not audio")

    cfg = load_config()
    estimate = local_dashboard.estimate_audio(audio_path, cfg)

    assert estimate["ok"] is False
    assert ".mp3" in estimate["error"]


def test_revised_quality_estimate_includes_all_three_paid_providers_for_141_56_minutes(tmp_path, monkeypatch):
    audio_path = tmp_path / "long.wav"
    audio_path.write_bytes(b"authorized-fixture")
    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda *_args, **_kwargs: {
            "duration_seconds": 141.56 * 60.0,
            "bytes_per_second": 32000,
        },
    )
    cfg = Config(
        max_cost_per_job_usd=5.0,
        enable_live_meralion_transcription=True,
        enable_live_openai_transcription=True,
        enable_live_gemini_transcription=True,
        meralion_api_key="fake",
        openai_api_key="fake",
        gemini_api_key="fake",
        meralion_billing_mode="trial_free",
        meralion_price_per_minute_usd=1.0,
    )

    estimate = local_dashboard.estimate_audio(audio_path, cfg)
    quality = estimate["quality_transcription"]
    gpt_tr_cost = quality["providers"]["gptTr"]["estimated_cost_usd"]
    openai_cost = quality["providers"]["gpt4oDiarz"]["estimated_cost_usd"]
    gemini_cost = quality["providers"]["Gem35T"]["estimated_cost_usd"]

    assert quality["providers"]["gptTr"]["cost_cap_included"] is True
    assert quality["paid_provider_estimated_total_usd"] == pytest.approx(
        gpt_tr_cost + openai_cost + gemini_cost
    )
    assert quality["paid_provider_cost_buffer_percent"] == 15.0
    assert quality["paid_provider_authorization_total_usd"] == pytest.approx(
        quality["providers"]["gptTr"]["authorization_budget_usd"]
        + quality["providers"]["gpt4oDiarz"]["authorization_budget_usd"]
        + quality["providers"]["Gem35T"]["authorization_budget_usd"]
    )
    assert quality["paid_provider_authorization_total_usd"] > quality[
        "paid_provider_estimated_total_usd"
    ]
    assert 0 < quality["paid_provider_estimated_total_usd"] < 5.0
    assert quality["workspace_cap_usd"] == 5.0
    assert quality["within_cap"] is True
    assert "144.300000" not in local_dashboard.DASHBOARD_HTML
    assert "Estimated provider spend" in local_dashboard.DASHBOARD_HTML
    assert "safety reserve" in local_dashboard.DASHBOARD_HTML

    cfg.openai_price_per_minute[cfg.openai_recommended_model] = 0.0105
    cfg.openai_price_per_minute[cfg.openai_diarize_model] = 0.0105
    cfg.gemini_transcribe_price_per_minute_usd = 0.0105
    buffered_overage = local_dashboard.estimate_audio(audio_path, cfg)[
        "quality_transcription"
    ]
    assert buffered_overage["paid_provider_estimated_total_usd"] < 5.0
    assert buffered_overage["paid_provider_authorization_total_usd"] > 5.0
    assert buffered_overage["within_cap"] is False

    cfg.openai_price_per_minute[cfg.openai_recommended_model] = 0.015
    cfg.openai_price_per_minute[cfg.openai_diarize_model] = 0.015
    cfg.gemini_transcribe_price_per_minute_usd = 0.015
    overage = local_dashboard.estimate_audio(audio_path, cfg)["quality_transcription"]
    assert overage["paid_provider_estimated_total_usd"] > 5.0
    assert overage["within_cap"] is False


def test_simple_budget_guard_covers_per_chunk_rounding_drift(tmp_path, monkeypatch):
    audio_path = tmp_path / "long.wav"
    audio_path.write_bytes(b"authorized-fixture")
    duration_seconds = 141.56 * 60.0
    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda *_args, **_kwargs: {
            "duration_seconds": duration_seconds,
            "bytes_per_second": 32000,
        },
    )
    cfg = Config(
        max_cost_per_job_usd=5.0,
        enable_live_openai_transcription=True,
        openai_api_key="fake",
    )

    estimate = local_dashboard.estimate_audio(audio_path, cfg)
    model = estimate["models"][cfg.openai_recommended_model]
    price = model["price_per_minute_usd"]
    worst_case_rounding_drift = model["chunk_count"] * price / 60.0

    assert model["rounding_guard_usd"] == pytest.approx(worst_case_rounding_drift)
    guarded_estimate = model["estimated_cost_usd"] + worst_case_rounding_drift
    assert model["cost_buffer_percent"] == 15.0
    assert model["cost_buffer_usd"] == pytest.approx(guarded_estimate * 0.15)
    assert model["run_budget_cap_usd"] == pytest.approx(guarded_estimate * 1.15)
    assert model["run_budget_cap_usd"] > 0.64935
    assert model["within_cap"] is True


def test_gemini_transcribe_long_audio_uses_inline_safe_chunks_and_matching_budget(tmp_path, monkeypatch):
    audio_path = tmp_path / "long.wav"
    audio_path.write_bytes(b"authorized-fixture")
    duration_seconds = 141.56 * 60.0
    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda *_args, **_kwargs: {
            "duration_seconds": duration_seconds,
            "bytes_per_second": 32000,
        },
    )
    cfg = Config(
        max_cost_per_job_usd=5.0,
        enable_live_gemini_transcription=True,
        gemini_api_key="fake",
    )

    estimate = local_dashboard.estimate_audio(audio_path, cfg)
    gem35t = estimate["models"][cfg.gemini_transcribe_model]
    run_cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        cfg.gemini_transcribe_model,
        run_budget_cap_usd=gem35t["run_budget_cap_usd"],
        cfg=cfg,
    )

    assert gem35t["target_max_chunk_bytes"] == 9 * 1024 * 1024
    assert gem35t["chunk_count"] > estimate["chunk_count"]
    assert gem35t["chunk_count"] >= 30
    assert run_cfg.target_max_chunk_bytes == gem35t["target_max_chunk_bytes"]
    assert run_cfg.hard_max_chunk_bytes == gem35t["target_max_chunk_bytes"]
    assert run_cfg.external_transcription_timeout_seconds == 300.0
    assert gem35t["run_budget_cap_usd"] > gem35t["estimated_cost_usd"]
    assert gem35t["within_cap"] is True


def test_all_seven_options_plan_safe_complete_100_minute_recording(tmp_path, monkeypatch):
    audio_path = tmp_path / "authorized-100-minute.m4a"
    audio_path.write_bytes(b"authorized-fixture")
    duration_seconds = 100 * 60.0
    normalized_bytes_per_second = 32000
    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda *_args, **_kwargs: {
            "duration_seconds": duration_seconds,
            "normalized_bytes_per_second": normalized_bytes_per_second,
            "source_format": ".m4a",
        },
    )
    cfg = Config(
        max_cost_per_job_usd=5.0,
        enable_live_openai_transcription=True,
        enable_live_meralion_transcription=True,
        enable_live_gemini_transcription=True,
        enable_live_qwen_filetrans_transcription=True,
        openai_api_key="fake-openai",
        meralion_api_key="fake-meralion",
        gemini_api_key="fake-gemini",
        dashscope_api_key="fake-dashscope",
        aliyun_oss_access_key_id="fake-oss-id",
        aliyun_oss_access_key_secret="fake-oss-secret",
        aliyun_oss_endpoint="https://oss-ap-southeast-1.aliyuncs.com",
        aliyun_oss_bucket="fake-private-bucket",
    )

    estimate = local_dashboard.estimate_audio(audio_path, cfg)

    assert estimate["ok"] is True
    assert len(estimate["models"]) == 7
    for model, model_estimate in estimate["models"].items():
        assert model_estimate["runnable"] is True
        assert model_estimate["within_cap"] is True
        if model == cfg.qwen_filetrans_model:
            assert model_estimate["chunk_count"] == 1
        else:
            assert model_estimate["chunk_count"] >= 21
        run_cfg = local_dashboard._build_run_config(
            local_dashboard.LIVE_MODE,
            model,
            run_budget_cap_usd=model_estimate["run_budget_cap_usd"],
            max_chunk_cost_usd=model_estimate["max_chunk_cost_usd"],
            cfg=cfg,
        )
        chunks = local_dashboard._estimate_chunk_plan(
            duration_seconds, normalized_bytes_per_second, run_cfg
        )
        assert chunks[0]["start_sec"] == 0.0
        assert chunks[-1]["end_sec"] == duration_seconds
        expected_max_duration = (
            cfg.qwen_filetrans_max_diarized_duration_sec
            if model == cfg.qwen_filetrans_model
            else cfg.provider_safe_chunk_duration_sec
        )
        assert all(
            0 < chunk["duration_seconds"] <= expected_max_duration
            for chunk in chunks
        )
        assert all(
            chunks[index]["start_sec"] < chunks[index - 1]["end_sec"]
            for index in range(1, len(chunks))
        )
        assert all(
            math.ceil(chunk["duration_seconds"] * normalized_bytes_per_second) + 44
            <= run_cfg.hard_max_chunk_bytes
            for chunk in chunks
        )

    assert cfg.meralion_transcription_model in estimate["models"]
    assert cfg.gemini_flash_model not in estimate["models"]
    qwen = estimate["models"][cfg.qwen_filetrans_model]
    assert qwen["target_max_chunk_bytes"] == 512 * 1024 * 1024
    assert qwen["estimated_cost_usd"] == pytest.approx(0.21)
    assert qwen["run_budget_cap_usd"] > qwen["estimated_cost_usd"]
    assert estimate["quality_transcription"]["chunk_count"] >= 21
    assert estimate["quality_transcription"]["within_cap"] is True


def test_dashboard_upload_limit_is_separate_from_openai_chunk_limit(tmp_path):
    wav_path = tmp_path / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    cfg.openai_file_size_limit_bytes = 1
    cfg.dashboard_upload_limit_bytes = wav_path.stat().st_size + 1

    estimate = local_dashboard.estimate_audio(wav_path, cfg)

    assert estimate["ok"] is True


def test_dashboard_formats_object_errors_for_users():
    assert "item.message" in local_dashboard.DASHBOARD_HTML
    assert 'aria-live="polite"' in local_dashboard.DASHBOARD_HTML


def test_dashboard_status_exposes_minutes_styles():
    status = local_dashboard.dashboard_status(load_config())
    keys = {item["key"] for item in status["minutes_styles"]}

    assert status["default_minutes_style"] == "standard"
    assert keys == {"standard", "deep_evidence", "action_focused"}
    assert "minutesStyleSelect" in local_dashboard.DASHBOARD_HTML
    assert [item["label"] for item in status["minutes_models"]] == ["Default", "Premium", "Best Quality"]
    assert "minutesModelSelect" in local_dashboard.DASHBOARD_HTML
    assert "/api/minutes/estimate" in local_dashboard.DASHBOARD_HTML
    assert "/api/minutes/generate" in local_dashboard.DASHBOARD_HTML
    assert status["dashboard_upload_limit_bytes"] > status["openai_file_size_limit_bytes"]


def test_dashboard_estimates_minutes_from_completed_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    job_id = "job-minutes-estimate"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("Mixed English and Indonesian meeting transcript. " * 100, encoding="utf-8")
    manifest_path = job_dir / "job_manifest.json"
    manifest_path.write_text(
        (
            '{"job_id":"job-minutes-estimate","state":"completed","outputs":'
            f'{{"full_transcript_txt":"{transcript_path.as_posix()}"}}}}'
        ),
        encoding="utf-8",
    )

    estimate = local_dashboard.estimate_job_minutes(job_id, "gpt-5.4-mini", "standard", load_config())

    assert estimate["ok"] is True
    assert estimate["model"] == "gpt-5.4-mini"
    assert estimate["style"] == "standard"
    assert estimate["estimated_total_tokens"] > 0


def test_live_minutes_dashboard_gates_require_confirmation_key_and_enable_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ENABLE_LIVE_OPENAI_MINUTES", raising=False)
    job_id = "job-minutes-gates"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("Transcript is ready.", encoding="utf-8")
    (job_dir / "job_manifest.json").write_text(
        (
            '{"job_id":"job-minutes-gates","state":"completed","outputs":'
            f'{{"full_transcript_txt":"{transcript_path.as_posix()}"}}}}'
        ),
        encoding="utf-8",
    )

    errors = local_dashboard.validate_minutes_request(
        job_id,
        "gpt-5.4-mini",
        "standard",
        confirm_live_minutes=False,
        run_budget_cap_usd=None,
        cfg=load_config(),
    )

    assert "ENABLE_LIVE_OPENAI_MINUTES must be true for live minutes generation." in errors
    assert "OPENAI_API_KEY must be set in the server environment." in errors
    assert "Live minutes generation requires explicit confirmation." in errors
    assert "Minutes budget cap is missing. Estimate minutes again." in errors


def test_live_minutes_dashboard_rejects_stale_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_MINUTES", "true")
    job_id = "job-minutes-budget"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("Transcript is ready.", encoding="utf-8")
    (job_dir / "job_manifest.json").write_text(
        (
            '{"job_id":"job-minutes-budget","state":"completed","outputs":'
            f'{{"full_transcript_txt":"{transcript_path.as_posix()}"}}}}'
        ),
        encoding="utf-8",
    )

    errors = local_dashboard.validate_minutes_request(
        job_id,
        "gpt-5.4-mini",
        "standard",
        confirm_live_minutes=True,
        run_budget_cap_usd=99.0,
        cfg=load_config(),
    )

    assert "Minutes budget cap does not match the current estimate. Estimate minutes again." in errors


def test_live_minutes_dashboard_generates_with_fake_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_MINUTES", "true")
    job_id = "job-minutes-run"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("Transcript is ready for live minutes.", encoding="utf-8")
    manifest_path = job_dir / "job_manifest.json"
    manifest_path.write_text(
        (
            '{"job_id":"job-minutes-run","state":"completed","summary":{},"outputs":'
            f'{{"full_transcript_txt":"{transcript_path.as_posix()}"}}}}'
        ),
        encoding="utf-8",
    )
    estimate = local_dashboard.estimate_job_minutes(job_id, "gpt-5.4-mini", "standard", load_config())

    class FakeGenerator:
        def generate(self, job_id, job_dir, outputs, cfg, *, model, minutes_style, run_budget_cap_usd, confirm_live_minutes):
            minutes_path = Path(job_dir) / "outputs" / "minutes.md"
            action_path = Path(job_dir) / "outputs" / "action_items.json"
            summary_path = Path(job_dir) / "outputs" / "job_summary.json"
            minutes_path.write_text("# Live Minutes\n\nConfirmed notes.", encoding="utf-8")
            action_path.write_text('{"action_items":[]}', encoding="utf-8")
            summary_path.write_text('{"generator":"openai"}', encoding="utf-8")
            return {
                "minutes_md": str(minutes_path),
                "action_items_json": str(action_path),
                "job_summary_json": str(summary_path),
            }

    monkeypatch.setattr(local_dashboard, "LiveOpenAIMinutesGenerator", lambda: FakeGenerator())

    result = local_dashboard.generate_job_minutes(
        job_id,
        "gpt-5.4-mini",
        "standard",
        confirm_live_minutes=True,
        run_budget_cap_usd=estimate["run_budget_cap_usd"],
        cfg=load_config(),
    )

    assert result["ok"] is True
    assert "# Live Minutes" in result["outputs"]["minutes_md"]
    assert result["outputs"]["manifest"]["minutes_generation"]["generator"] == "openai"


def test_dashboard_estimates_non_wav_as_normalized_wav_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    audio_path = tmp_path / "sample.m4a"
    audio_path.write_bytes(b"fake m4a bytes")

    def fake_probe(path, cfg):
        return {
            "duration_seconds": 125.0,
            "normalized_bytes_per_second": 32000,
            "source_format": ".m4a",
            "probe_method": "ffprobe",
        }

    monkeypatch.setattr(local_dashboard, "probe_audio_metadata", fake_probe)

    estimate = local_dashboard.estimate_audio(audio_path, load_config())

    assert estimate["ok"] is True
    assert estimate["estimation_method"] == "ffprobe_duration_normalized_wav_chunked"
    assert estimate["normalized_for_chunking"] is True
    assert math.isclose(estimate["duration_seconds"], 125.0)
    assert estimate["models"]["gpt-4o-mini-transcribe"]["estimated_cost_usd"] > 0


def test_dashboard_multipart_upload_preserves_audio_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    wav_path = tmp_path / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)
    audio_bytes = wav_path.read_bytes()
    boundary = "apma-test-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="sample.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    saved = local_dashboard.save_uploaded_audio(f"multipart/form-data; boundary={boundary}", body)

    assert saved["filename"] == "sample.wav"
    assert Path(saved["path"]).read_bytes() == audio_bytes
    assert saved["estimate"]["ok"] is True


def test_dashboard_multipart_upload_accepts_m4a(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda path, cfg: {"duration_seconds": 90.0, "normalized_bytes_per_second": 32000, "source_format": ".m4a"},
    )
    audio_bytes = b"fake m4a bytes"
    boundary = "apma-test-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="sample.m4a"\r\n'
        "Content-Type: audio/mp4\r\n\r\n"
    ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    saved = local_dashboard.save_uploaded_audio(f"multipart/form-data; boundary={boundary}", body)

    assert saved["filename"] == "sample.m4a"
    assert Path(saved["path"]).read_bytes() == audio_bytes
    assert saved["content_type"] in {"audio/mp4", "audio/x-m4a", "audio/m4a", "audio/mp4a-latm"}
    assert saved["estimate"]["ok"] is True


def test_dashboard_upload_reuses_shared_source_without_audio_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    source = tmp_path / "sample.wav"
    generate_sine_wav(str(source), duration_sec=1.0, framerate=8000)
    audio_bytes = source.read_bytes()
    boundary = "apma-deduplicated-upload"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="sample.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    first = local_dashboard.save_uploaded_audio(
        f"multipart/form-data; boundary={boundary}", body
    )
    second = local_dashboard.save_uploaded_audio(
        f"multipart/form-data; boundary={boundary}", body
    )

    assert first["path"] == second["path"]
    assert first["shared_source_reused"] is False
    assert second["shared_source_reused"] is True
    upload_files = list((tmp_path / "jobs" / "dashboard_uploads").rglob("*"))
    assert not [path for path in upload_files if path.is_file() and path.suffix == ".wav"]
    assert len(list((tmp_path / "jobs" / "_source_store").rglob("original.wav"))) == 1


def test_live_dashboard_gates_require_confirmation_key_and_enable_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    upload_dir = local_dashboard._upload_path("upload-live")
    upload_dir.mkdir(parents=True)
    wav_path = upload_dir / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", raising=False)

    errors = local_dashboard.validate_run_request(
        upload_id="upload-live",
        mode=local_dashboard.LIVE_MODE,
        model="gpt-4o-mini-transcribe",
        confirm_live_api=False,
    )

    assert "Live OpenAI mode requires explicit confirmation." in errors
    assert "OPENAI_API_KEY must be set in the server environment." in errors
    assert "ENABLE_LIVE_OPENAI_TRANSCRIPTION must be true for live mode." in errors
    assert "Run budget cap is missing. Upload and estimate the file again." in errors


def test_live_dashboard_rejects_stale_or_changed_budget_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    upload_dir = local_dashboard._upload_path("upload-budget")
    upload_dir.mkdir(parents=True)
    wav_path = upload_dir / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")

    errors = local_dashboard.validate_run_request(
        upload_id="upload-budget",
        mode=local_dashboard.LIVE_MODE,
        model="gpt-4o-mini-transcribe",
        confirm_live_api=True,
        run_budget_cap_usd=0.99,
    )

    assert "Run budget cap does not match the current estimate. Upload and estimate the file again." in errors


def test_live_dashboard_accepts_matching_budget_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    upload_dir = local_dashboard._upload_path("upload-budget-ok")
    upload_dir.mkdir(parents=True)
    wav_path = upload_dir / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    estimate = local_dashboard.estimate_audio(wav_path, load_config())
    model_cap = estimate["models"]["gpt-4o-mini-transcribe"]["run_budget_cap_usd"]

    errors = local_dashboard.validate_run_request(
        upload_id="upload-budget-ok",
        mode=local_dashboard.LIVE_MODE,
        model="gpt-4o-mini-transcribe",
        confirm_live_api=True,
        run_budget_cap_usd=model_cap,
    )

    assert errors == []


def test_dashboard_accepts_meralion_and_rejects_removed_gemini_flash(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    upload_dir = local_dashboard._upload_path("upload-meralion")
    upload_dir.mkdir(parents=True)
    wav_path = upload_dir / "sample.wav"
    generate_sine_wav(str(wav_path), duration_sec=1.0, framerate=8000)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config()
    meralion_errors = local_dashboard.validate_run_request(
        upload_id="upload-meralion",
        mode=local_dashboard.LIVE_MODE,
        model=cfg.meralion_transcription_model,
        confirm_live_api=True,
        run_budget_cap_usd=None,
        cfg=cfg,
    )
    assert "Selected transcription model is not configured." not in meralion_errors

    removed_errors = local_dashboard.validate_run_request(
        upload_id="upload-meralion",
        mode=local_dashboard.LIVE_MODE,
        model=cfg.gemini_flash_model,
        confirm_live_api=True,
        run_budget_cap_usd=None,
        cfg=cfg,
    )
    assert removed_errors == ["Selected transcription model is not configured."]


def test_dry_run_dashboard_builds_mock_config(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")

    cfg = local_dashboard._build_run_config(
        local_dashboard.DRY_RUN_MODE,
        "gpt-4o-mini-transcribe",
        minutes_style="action_focused",
    )

    assert cfg.dry_run is True
    assert cfg.transcription_engine == "mock"
    assert cfg.enable_live_openai_transcription is False
    assert cfg.minutes_style == "action_focused"
    assert Path(cfg.storage_path) == tmp_path / "jobs"


def test_live_dashboard_builds_openai_config_only_after_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        "gpt-4o-transcribe",
        run_budget_cap_usd=0.0123,
        max_chunk_cost_usd=0.0045,
    )

    assert cfg.dry_run is False
    assert cfg.transcription_engine == "openai"
    assert cfg.enable_live_openai_transcription is True
    assert cfg.openai_model == "gpt-4o-transcribe"
    assert cfg.openai_api_key == "fake-key"
    assert cfg.max_cost_per_job_usd == 0.0123 + local_dashboard.BUDGET_EPSILON_USD
    assert cfg.openai_max_cost_per_chunk_usd == 0.0045 + local_dashboard.BUDGET_EPSILON_USD


def test_live_dashboard_routes_selected_gem35t_model_to_gemini(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    cfg = Config(gemini_api_key="fake-gemini-key")

    run_cfg = local_dashboard._build_run_config(
        local_dashboard.LIVE_MODE,
        cfg.gemini_transcribe_model,
        run_budget_cap_usd=0.25,
        cfg=cfg,
    )

    assert run_cfg.transcription_engine == "gemini"
    assert run_cfg.enable_live_gemini_transcription is True
    assert run_cfg.gemini_transcription_model == "gemini-3.5-transcribe"
    assert run_cfg.target_max_chunk_bytes == 9 * 1024 * 1024
    assert run_cfg.hard_max_chunk_bytes == 9 * 1024 * 1024
    assert run_cfg.external_transcription_timeout_seconds == 300.0


def test_live_dashboard_non_wav_normalizes_chunks_and_writes_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    upload_dir = local_dashboard._upload_path("upload-m4a")
    upload_dir.mkdir(parents=True)
    audio_path = upload_dir / "sample.m4a"
    audio_path.write_bytes(b"fake m4a bytes")
    source_wav = tmp_path / "source.wav"
    generate_sine_wav(str(source_wav), duration_sec=3.0, framerate=8000)

    monkeypatch.setattr(
        local_dashboard,
        "probe_audio_metadata",
        lambda path, cfg: {"duration_seconds": 3.0, "normalized_bytes_per_second": 32000, "source_format": ".m4a"},
    )

    from services import preprocess as preprocess_mod

    monkeypatch.setattr(
        preprocess_mod,
        "_run_ffprobe_json",
        lambda path, cfg: {
            "format": {
                "duration": "3.0",
                "size": str(Path(path).stat().st_size),
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 2,
                    "duration": "3.0",
                }
            ],
        },
    )

    def fake_normalize(src, dest, cfg):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_wav, dest)

    def fake_canonical(src, job_path, probed, cfg):
        canonical = Path(job_path) / "preprocess" / "canonical.flac"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_wav, canonical)
        return {"path": str(canonical), "lossless": True, "channels": 2}

    monkeypatch.setattr(preprocess_mod, "_create_canonical_audio", fake_canonical)
    monkeypatch.setattr(preprocess_mod, "_run_ffmpeg_normalize", fake_normalize)

    def fake_audio_project(source, job_path, cfg, original_filename=None):
        root = Path(job_path).resolve()
        return {
            "subproject_folder": str(root),
            "original_folder": str(root / "audio" / "original"),
            "mp3_folder": str(root / "audio" / "mp3"),
            "mp3_chunks_folder": str(root / "audio" / "mp3" / "chunks"),
        }

    monkeypatch.setattr(
        local_dashboard.runner.audio_project_mod,
        "create_audio_project",
        fake_audio_project,
    )

    class FakeClient:
        def transcribe(self, file_path, model=None):
            assert file_path.endswith(".wav")
            assert model == "gpt-4o-mini-transcribe"
            return {"text": "Hello from a normalized M4A chunk.", "cost_usd": 0.0}

    from services.transcription import openai_adapter

    monkeypatch.setattr(openai_adapter.OpenAITranscriptionHttpClient, "from_config", staticmethod(lambda cfg: FakeClient()))
    estimate = local_dashboard.estimate_audio(audio_path, load_config())
    cap = estimate["models"]["gpt-4o-mini-transcribe"]["run_budget_cap_usd"]

    result = local_dashboard.run_uploaded_job(
        "upload-m4a",
        local_dashboard.LIVE_MODE,
        "gpt-4o-mini-transcribe",
        confirm_live_api=True,
        run_budget_cap_usd=cap,
        minutes_style="action_focused",
    )

    assert result["ok"] is True
    outputs = result["outputs"]
    assert "Hello from a normalized M4A chunk." in outputs["full_transcript_txt"]
    assert "Action-Focused Summary" in outputs["minutes_md"]
    assert outputs["job_summary_json"]["minutes_style"] == "action_focused"
    manifest = outputs["manifest"]
    assert manifest["state"] == "completed"
    assert manifest["minutes_style"] == "action_focused"
    assert manifest["preprocess"]["normalized"] is True
    assert manifest["preprocess"]["source_format"] == ".m4a"
    assert len(manifest["chunks"]) >= 1
    assert manifest["outputs"]["minutes_md"].endswith("minutes.md")


def test_dashboard_exposes_only_existing_allowlisted_transcript_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")
    job_id = "job-export-links"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    audio_project = {
        "subproject_folder": str(job_dir.resolve()),
        "original_folder": str((job_dir / "audio" / "original").resolve()),
        "mp3_folder": str((job_dir / "audio" / "mp3").resolve()),
        "mp3_chunks_folder": str((job_dir / "audio" / "mp3" / "chunks").resolve()),
    }
    (job_dir / "job_manifest.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "state": "completed",
                "outputs": {},
                "audio_project": audio_project,
            }
        ),
        encoding="utf-8",
    )
    (outputs_dir / "full_transcript.html").write_text("<html></html>", encoding="utf-8")
    (outputs_dir / "full_transcript.vtt").write_text("WEBVTT\n", encoding="utf-8")

    result = local_dashboard.collect_job_outputs(job_id)

    assert result["transcript_exports"] == {
        "html": f"/api/jobs/{job_id}/exports/html",
        "vtt": f"/api/jobs/{job_id}/exports/vtt",
    }
    assert result["project_paths"] == audio_project
    assert "exportGrid" in local_dashboard.DASHBOARD_HTML
    assert "Transcript HTML" in local_dashboard.DASHBOARD_HTML
    assert "Local project folders" in local_dashboard.DASHBOARD_HTML
    assert "Copy folder addresses" in local_dashboard.DASHBOARD_HTML


def test_dashboard_transcript_export_path_is_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", tmp_path / "jobs")

    path, spec = local_dashboard.transcript_export_path("job-1", "srt")

    assert path == tmp_path / "jobs" / "job-1" / "outputs" / "full_transcript.srt"
    assert spec["content_type"].startswith("application/x-subrip")
    with pytest.raises(ValueError, match="Unsupported transcript export format"):
        local_dashboard.transcript_export_path("job-1", "pdf")
    with pytest.raises(ValueError):
        local_dashboard.transcript_export_path("../secret", "html")
    assert local_dashboard._match_transcript_export_request(
        "/api/jobs/job-1/exports/html"
    ) is not None
    assert local_dashboard._match_transcript_export_request(
        "/api/jobs/job-1/exports/pdf"
    ) is None


def test_dashboard_html_export_response_sets_security_headers(tmp_path):
    class StubHandler:
        def __init__(self):
            self.status = None
            self.headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.headers[key] = value

        def end_headers(self):
            return None

    html_path = tmp_path / "full_transcript.html"
    html_path.write_text("<html><body>safe transcript</body></html>", encoding="utf-8")
    handler = StubHandler()

    local_dashboard._file_response(
        handler,
        html_path,
        local_dashboard.TRANSCRIPT_EXPORT_SPECS["html"],
    )

    assert handler.status == 200
    assert handler.headers["Content-Security-Policy"] == local_dashboard.TRANSCRIPT_HTML_CSP
    assert handler.headers["X-Content-Type-Options"] == "nosniff"
    assert handler.headers["Content-Disposition"].startswith("inline;")
    assert b"safe transcript" in handler.wfile.getvalue()


def test_professional_dashboard_exposes_a_lean_user_flow():
    html = local_dashboard.DASHBOARD_HTML

    assert "APMA Transcription Assurance" in html
    assert "Reviewable transcription for Southeast Asia's recorded conversations" in html
    assert "Fast Draft" in html
    assert "SEA Multilingual" in html
    assert "Speaker-labelled" in html
    assert "Compare &amp; Verify" in html
    assert "Recent jobs" in html
    assert "/api/jobs?limit=12" in html
    assert 'role="tablist"' in html
    assert "jobAudio.currentTime" in html
    assert "Optional minutes" in html
    assert "APMA Local Dashboard" not in html


def test_dashboard_lists_and_reopens_retained_jobs(tmp_path, monkeypatch):
    storage = tmp_path / "jobs"
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", storage)
    job_id = "job-professional-flow"
    job_dir = storage / job_id
    audio_dir = job_dir / "audio" / "original"
    outputs_dir = job_dir / "outputs"
    audio_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)
    audio_path = audio_dir / "family-conversation.wav"
    generate_sine_wav(str(audio_path), duration_sec=1.0, framerate=8000)
    transcript_json = outputs_dir / "full_transcript.json"
    transcript_txt = outputs_dir / "full_transcript.txt"
    transcript_json.write_text(
        json.dumps(
            {
                "segments": [
                    {"start_sec": 0.0, "end_sec": 1.0, "speaker": "Speaker 1", "text": "Hello"}
                ]
            }
        ),
        encoding="utf-8",
    )
    transcript_txt.write_text("Hello", encoding="utf-8")
    manifest = {
        "job_id": job_id,
        "state": "review_required",
        "created_at": "2026-09-11T01:00:00Z",
        "updated_at": "2026-09-11T02:00:00Z",
        "source": {"original_filename": "family-conversation.wav", "sha256": "fixture-sha"},
        "preprocess": {"duration_seconds": 61.5},
        "audio_project": {
            "subproject_folder": str(job_dir),
            "original_folder": str(audio_dir),
            "original": {"path": str(audio_path)},
        },
        "outputs": {
            "full_transcript_json": str(transcript_json),
            "full_transcript_txt": str(transcript_txt),
        },
        "quality": {
            "review_url": "/review",
            "providers": {"M3ASR": {}, "gptTr": {}, "Gem35T": {}},
            "stages": {"REVIEW": {"unresolved_windows": 2}},
        },
        "estimated_cost_usd": 0.22,
        "actual_cost_usd": 0.18,
    }
    (job_dir / "job_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    listed = local_dashboard.list_dashboard_jobs()
    detail = local_dashboard.dashboard_job(job_id)

    assert listed["jobs"][0]["title"] == "family-conversation.wav"
    assert listed["jobs"][0]["display_state"] == "review_required"
    assert listed["jobs"][0]["duration_seconds"] == 61.5
    assert listed["jobs"][0]["review_required_count"] == 2
    assert detail["audio_url"] == f"/api/jobs/{job_id}/audio"
    assert detail["review_url"] == f"/review?job_id={job_id}"
    assert detail["outputs"]["full_transcript_json"]["segments"][0]["text"] == "Hello"
    assert local_dashboard._match_dashboard_job_request(
        f"/api/jobs/{job_id}/audio"
    ) == (job_id, "audio")
    with pytest.raises(ValueError):
        local_dashboard.dashboard_job("../outside")


def test_dashboard_audio_response_supports_byte_range_seeking(tmp_path):
    class StubHandler:
        def __init__(self):
            self.status = None
            self.headers = {"Range": "bytes=2-5"}
            self.response_headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.response_headers[key] = value

        def end_headers(self):
            return None

    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"0123456789")
    handler = StubHandler()

    local_dashboard._media_response(handler, audio_path)

    assert handler.status == 206
    assert handler.response_headers["Content-Range"] == "bytes 2-5/10"
    assert handler.response_headers["Accept-Ranges"] == "bytes"
    assert handler.wfile.getvalue() == b"2345"


def test_review_progress_updates_recent_job_state_and_count(tmp_path, monkeypatch):
    storage = tmp_path / "jobs"
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", storage)
    job_dir = storage / "job-review-progress"
    job_dir.mkdir(parents=True)
    manifest = {
        "job_id": "job-review-progress",
        "state": "review_required",
        "quality": {
            "overall_status": "REVIEW_REQUIRED",
            "stages": {"REVIEW": {"status": "completed", "unresolved_windows": 5}},
        },
    }
    (job_dir / "job_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    local_dashboard._sync_quality_review_status(
        "job-review-progress", {"statistics": {"unresolved_windows": 2}}
    )
    updated = json.loads((job_dir / "job_manifest.json").read_text(encoding="utf-8"))

    assert updated["state"] == "review_required"
    assert updated["quality"]["stages"]["REVIEW"]["unresolved_windows"] == 2
    assert local_dashboard.list_dashboard_jobs()["jobs"][0]["review_required_count"] == 2

    local_dashboard._sync_quality_review_status(
        "job-review-progress", {"statistics": {"unresolved_windows": 0}}
    )
    completed = json.loads((job_dir / "job_manifest.json").read_text(encoding="utf-8"))

    assert completed["state"] == "complete"
    assert completed["quality"]["overall_status"] == "COMPLETE"
    assert completed["quality"]["stages"]["REVIEW"]["unresolved_windows"] == 0
