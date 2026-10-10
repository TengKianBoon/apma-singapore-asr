from __future__ import annotations

import json
from pathlib import Path

from scripts import local_dashboard
from services import job as job_mod
from services.config import Config
from services.integrity import sha256_file
from services.quality_workflow import QUALITY_STAGES, _provider_preflight, run_quality_workflow
from services.selective_rescue import (
    PROVIDER_ORDER,
    QUALITY_PROVIDER_MODELS,
    configure_live_provider,
)
from tests.helpers import generate_sine_wav


def _provider_for_config(engine: str, cfg: Config) -> tuple[str, str, str]:
    if engine == "openai" and cfg.openai_model == "gpt-transcribe":
        return "OpenAI", "gptTr", "gpt-transcribe"
    if engine == "openai":
        return "OpenAI", "gpt4oDiarz", "gpt-4o-transcribe-diarize"
    if engine == "gemini":
        return "Google", "Gem35T", "gemini-3.5-transcribe"
    raise AssertionError(f"Unexpected Quality engine: {engine}")


class FakeQualityTranscriber:
    def __init__(
        self,
        engine: str,
        calls: list[tuple[str, str, str]],
        texts: dict[str, str] | None = None,
    ):
        self.engine = engine
        self.calls = calls
        self.texts = texts

    def transcribe_chunk(self, job_id: str, chunk: dict, cfg: Config) -> dict:
        provider, provider_code, model = _provider_for_config(self.engine, cfg)
        filename = str(chunk["filename"])
        self.calls.append((self.engine, job_id, filename))
        job_dir = Path(cfg.storage_path) / job_id
        transcript_path = job_dir / "transcripts" / f"{Path(filename).stem}.json"
        provider_path = job_dir / "providers" / self.engine / f"{Path(filename).stem}.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        provider_path.parent.mkdir(parents=True, exist_ok=True)
        text = (self.texts or {
            "gptTr": "红色火车已经离开北京站",
            "gpt4oDiarz": "yellow bananas orbit quietly",
            "Gem35T": "pulau hijau menunggu hujan deras",
        })[provider_code]
        provider_path.write_text(json.dumps({"text": text}), encoding="utf-8")
        result = {
            "provider": provider,
            "provider_code": provider_code,
            "model": model,
            "run_id": f"fake-{provider_code}-{filename}",
            "request_id": None,
            "chunk_filename": filename,
            "chunk_sha256": chunk["clip_sha256"],
            "start_sec": float(chunk["start_sec"]),
            "end_sec": float(chunk["end_sec"]),
            "duration_seconds": float(chunk["end_sec"]) - float(chunk["start_sec"]),
            "text": text,
            "estimated_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "provider_artifact_path": str(provider_path),
            "transcript_path": str(transcript_path),
        }
        if provider_code == "Gem35T":
            start = float(chunk["start_sec"])
            result.update(
                {
                    "diarized": True,
                    "speaker_attribution": {
                        "requested": True,
                        "source": "provider_native",
                        "provider_speaker_ids": ["spk_1", "spk_2"],
                        "speaker_count": 2,
                        "names_invented": False,
                    },
                    "timing": {
                        "provider_native": {
                            "words": [
                                {
                                    "text": "hello",
                                    "local_start_sec": 0.1,
                                    "local_end_sec": 0.3,
                                    "global_start_sec": start + 0.1,
                                    "global_end_sec": start + 0.3,
                                    "speaker": "spk_1",
                                    "provider_speaker": "spk_1",
                                    "raw_provider_annotation": {"text": "hello"},
                                },
                                {
                                    "text": "world",
                                    "local_start_sec": 0.4,
                                    "local_end_sec": 0.7,
                                    "global_start_sec": start + 0.4,
                                    "global_end_sec": start + 0.7,
                                    "speaker": "spk_2",
                                    "provider_speaker": "spk_2",
                                    "raw_provider_annotation": {"text": "world"},
                                },
                            ],
                            "segments": [],
                        }
                    },
                }
            )
        transcript_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def transcribe_chunks(self, job_id: str, chunks: list[dict], cfg: Config) -> list[dict]:
        return [self.transcribe_chunk(job_id, chunk, cfg) for chunk in chunks]


class FakeQualityFactory:
    def __init__(self, texts: dict[str, str] | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.texts = texts

    def __call__(self, engine: str) -> FakeQualityTranscriber:
        return FakeQualityTranscriber(engine, self.calls, self.texts)


def _config(storage_path: Path) -> Config:
    return Config(
        storage_path=str(storage_path),
        dry_run=False,
        max_cost_per_job_usd=5.0,
        enable_live_meralion_transcription=True,
        enable_live_openai_transcription=True,
        enable_live_gemini_transcription=True,
        meralion_api_key="fixture-meralion",
        openai_api_key="fixture-openai",
        gemini_api_key="fixture-gemini",
        meralion_price_per_minute_usd=0.001,
        gemini_price_per_minute_usd=0.001,
        default_chunk_duration_sec=2,
        min_chunk_duration_sec=1,
        max_chunk_duration_sec=2,
        overlap_seconds=0,
        chunk_boundary_search_window_sec=0,
        smart_chunking_enabled=False,
    )


def test_integrated_quality_workflow_resumes_without_repeating_primary_calls(tmp_path, monkeypatch):
    source = tmp_path / "authorized-fixture.wav"
    generate_sine_wav(str(source), duration_sec=5.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)
    factory = FakeQualityFactory()

    stopped = run_quality_workflow(
        "quality-fixture",
        str(source),
        cfg,
        transcriber_factory=factory,
        stop_after_stage="TRANSCRIBE",
    )
    manifest = job_mod.read_manifest(storage / "quality-fixture")
    chunk_count = len(manifest["chunks"])
    assert chunk_count >= 3
    assert len(factory.calls) == chunk_count * 3
    assert manifest["quality"]["cost_preflight"]["passed"] is True
    assert manifest["quality"]["cost_preflight"]["combined_estimated_cost_usd"] <= 5.0
    assert manifest["quality"]["cost_preflight"]["provider_estimates_usd"]["gptTr"] > 0.0
    assert manifest["quality"]["cost_preflight"]["provider_billing"]["gptTr"]["cost_cap_included"] is True
    assert manifest["quality"]["cost_preflight"]["paid_provider_estimated_total_usd"] == manifest["quality"]["cost_preflight"]["combined_estimated_cost_usd"]
    primary_calls = list(factory.calls)
    assert [
        item["stage"]
        for item in manifest["quality"]["stage_history"]
        if item["event"] == "completed"
    ] == ["INGEST", "CHUNK", "TRANSCRIBE"]
    assert stopped["ok"] is False

    completed = run_quality_workflow(
        "quality-fixture",
        str(source),
        cfg,
        transcriber_factory=factory,
    )
    assert completed["ok"] is True, completed["errors"]
    assert completed["state"] == "review_required"
    assert factory.calls[: len(primary_calls)] == primary_calls
    assert not any(call in factory.calls[len(primary_calls) :] for call in primary_calls)
    assert len(factory.calls) == len(primary_calls) + 3
    assert completed["quality"]["provider_calls"]["new"] == len(factory.calls)
    assert completed["review_url"] == "/review?job_id=quality-fixture"
    assert completed["outputs"] == completed["quality"]["outputs"]
    assert completed["review_required_count"] >= 1
    assert completed["targeted_human_verification"]["requested"] is False
    assert completed["targeted_human_verification"]["summary"][
        "accuracy_uplift_claimed"
    ] is False

    completed_stages = [
        item["stage"]
        for item in completed["quality"]["stage_history"]
        if item["event"] == "completed"
    ]
    assert completed_stages == list(QUALITY_STAGES)
    outputs = completed["quality"]["outputs"]
    for provider_code in PROVIDER_ORDER:
        assert Path(outputs["providers"][provider_code]["json"]).is_file()
        assert Path(outputs["providers"][provider_code]["html"]).is_file()
    for stage_name in ("comparison", "rescue", "final_draft", "final_reviewed"):
        assert Path(outputs[stage_name]["json"]).is_file()
        assert Path(outputs[stage_name]["html"]).is_file()
    assert Path(outputs["speaker_overlay"]["json"]).is_file()
    overlay = json.loads(Path(outputs["speaker_overlay"]["json"]).read_text(encoding="utf-8"))
    assert overlay["statistics"]["local_speaker_count"] >= 2
    assert overlay["policy"]["quality_text_overwritten"] is False

    reviewed = json.loads(Path(outputs["final_reviewed"]["json"]).read_text(encoding="utf-8"))
    assert reviewed["statistics"]["total_windows"] >= 1
    assert reviewed["statistics"]["unresolved_windows"] >= 1

    calls_before_terminal_resume = list(factory.calls)
    resumed = run_quality_workflow(
        "quality-fixture",
        str(source),
        cfg,
        transcriber_factory=factory,
    )
    assert resumed["ok"] is True
    assert factory.calls == calls_before_terminal_resume
    assert resumed["quality"]["resume_count"] == 2

    manifest = job_mod.read_manifest(storage / "quality-fixture")
    manifest["quality"]["outputs"]["final_draft"]["json"] = "/moved/FINAL_DRAFT.json"
    manifest["quality"]["outputs"]["final_reviewed"]["json"] = "/moved/FINAL_REVIEWED.json"
    manifest["quality"]["review_audio_dir"] = "/moved/chunks"
    job_mod.write_manifest(storage / "quality-fixture", manifest)
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", storage)
    relocated_view = local_dashboard._review_payload("quality-fixture")
    assert relocated_view["statistics"]["unresolved_windows"] >= 1
    assert all("job_id=quality-fixture" in item["audio_url"] for item in relocated_view["windows"])


def test_failed_gem37f_job_resumes_with_only_gem35t_calls(tmp_path):
    source = tmp_path / "authorized-provider-switch.wav"
    generate_sine_wav(str(source), duration_sec=5.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)
    factory = FakeQualityFactory()

    run_quality_workflow(
        "quality-provider-switch",
        str(source),
        cfg,
        transcriber_factory=factory,
        stop_after_stage="TRANSCRIBE",
    )
    manifest = job_mod.read_manifest(storage / "quality-provider-switch")
    chunk_count = len(manifest["chunks"])
    quality = manifest["quality"]
    gpt_tr_calls = quality["providers"]["gptTr"]["new_calls"]
    diarize_calls = quality["providers"]["gpt4oDiarz"]["new_calls"]

    # Model the real production failure: both OpenAI providers completed, while
    # the former Gem37F seat failed. Its raw state remains retained as evidence.
    quality["providers"].pop("Gem35T")
    quality["providers"]["Gem37F"] = {
        "status": "failed",
        "error": "google returned no transcript text (prompt block reason=OTHER)",
        "chunks": [],
    }
    quality["outputs"]["providers"].pop("Gem35T")
    quality["stages"]["TRANSCRIBE"] = {"status": "failed"}
    quality["overall_status"] = "failed"
    manifest["state"] = "failed"
    job_mod.write_manifest(storage / "quality-provider-switch", manifest)
    factory.calls.clear()

    resumed = run_quality_workflow(
        "quality-provider-switch",
        str(source),
        cfg,
        transcriber_factory=factory,
        stop_after_stage="TRANSCRIBE",
    )
    updated = job_mod.read_manifest(storage / "quality-provider-switch")

    assert resumed["ok"] is False
    assert len(factory.calls) == chunk_count
    assert {engine for engine, _job_id, _filename in factory.calls} == {"gemini"}
    assert updated["quality"]["providers"]["gptTr"]["new_calls"] == gpt_tr_calls
    assert updated["quality"]["providers"]["gpt4oDiarz"]["new_calls"] == diarize_calls
    assert updated["quality"]["providers"]["Gem35T"]["status"] == "completed"
    assert updated["quality"]["providers"]["Gem37F"]["status"] == "failed"
    assert set(updated["quality"]["outputs"]["providers"]) == set(PROVIDER_ORDER)


def test_dashboard_exposes_quality_action_and_keeps_simple_action():
    cfg = _config(Path("fixture-jobs"))
    status = local_dashboard.dashboard_status(cfg)

    assert status["quality_transcription"]["runnable"] is True
    assert status["quality_transcription"]["provider_codes"] == [
        "gptTr",
        "gpt4oDiarz",
        "Gem35T",
    ]
    assert status["quality_transcription"]["provider_models"] == {
        "gptTr": "gpt-transcribe",
        "gpt4oDiarz": "gpt-4o-transcribe-diarize",
        "Gem35T": "gemini-3.5-transcribe",
    }
    assert "Compare &amp; Verify" in local_dashboard.DASHBOARD_HTML
    assert "Fast Draft" in local_dashboard.DASHBOARD_HTML
    assert "SEA Multilingual" in local_dashboard.DASHBOARD_HTML
    assert "Speaker-labelled" in local_dashboard.DASHBOARD_HTML
    assert "Advanced provider details" in local_dashboard.DASHBOARD_HTML
    assert "provider candidates separately" in local_dashboard.DASHBOARD_HTML
    quality_javascript = local_dashboard.DASHBOARD_HTML.split(
        'payload = await api("/api/quality/run"', 1
    )[1].split("} else {", 1)[0]
    assert "model:" not in quality_javascript
    assert "/api/quality/run" in local_dashboard.DASHBOARD_HTML
    assert "/api/run" in local_dashboard.DASHBOARD_HTML
    assert "Review flagged areas" in local_dashboard.DASHBOARD_HTML
    assert "Add targeted human audio verification" in local_dashboard.DASHBOARD_HTML
    assert "targeted_human_verification_requested" in local_dashboard.DASHBOARD_HTML
    assert "selected-window review" in local_dashboard.DASHBOARD_HTML


def test_targeted_verification_opt_in_persists_when_quality_job_resumes(tmp_path):
    source = tmp_path / "authorized-targeted-review.wav"
    generate_sine_wav(str(source), duration_sec=1.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)

    first = run_quality_workflow(
        "quality-targeted-review",
        str(source),
        cfg,
        stop_after_stage="INGEST",
        targeted_human_verification_requested=True,
    )
    resumed = run_quality_workflow(
        "quality-targeted-review",
        str(source),
        cfg,
        stop_after_stage="CHUNK",
        targeted_human_verification_requested=False,
    )

    assert first["targeted_human_verification"]["requested"] is True
    assert resumed["targeted_human_verification"]["requested"] is True
    assert resumed["targeted_human_verification"]["scope"] == (
        "selected_flagged_windows"
    )
    assert resumed["targeted_human_verification"]["accuracy_uplift_claimed"] is False


def test_quality_failure_names_the_provider_that_failed(tmp_path):
    source = tmp_path / "authorized-failure-fixture.wav"
    generate_sine_wav(str(source), duration_sec=1.2, framerate=8000)
    cfg = _config(tmp_path / "jobs")

    class FailingTranscriber:
        def transcribe_chunk(self, *_args, **_kwargs):
            raise RuntimeError(
                "Provider rate/quota limit returned HTTP 429 after 3 attempts; "
                "completed chunks are retained and the same job can be resumed later"
            )

    result = run_quality_workflow(
        "quality-provider-error",
        str(source),
        cfg,
        transcriber_factory=lambda engine: FailingTranscriber(),
    )

    assert result["ok"] is False
    assert result["errors"][-1]["message"] == (
        "gptTr Quality provider failed: Provider rate/quota limit returned HTTP 429 "
        "after 3 attempts; completed chunks are retained and the same job can be "
        "resumed later"
    )


def test_quality_provider_models_are_locked_independently_of_simple_config(tmp_path):
    cfg = _config(tmp_path / "jobs")
    cfg.openai_recommended_model = "simple-openai-recommended-override"
    cfg.openai_diarize_model = "simple-openai-override"
    cfg.gemini_transcribe_model = "simple-gemini-override"

    locked = {
        provider_code: configure_live_provider(cfg, provider_code)
        for provider_code in QUALITY_PROVIDER_MODELS
    }

    assert locked["gptTr"].transcription_engine == "openai"
    assert locked["gptTr"].openai_model == "gpt-transcribe"
    assert locked["gpt4oDiarz"].transcription_engine == "openai"
    assert locked["gpt4oDiarz"].openai_model == "gpt-4o-transcribe-diarize"
    assert locked["Gem35T"].transcription_engine == "gemini"
    assert locked["Gem35T"].gemini_transcription_model == "gemini-3.5-transcribe"
    assert locked["Gem35T"].enable_gemini_speaker_attribution is True
    assert locked["Gem35T"].enable_provider_timestamps is True
    assert all(item.chinese_script_preference == "preserve" for item in locked.values())
    assert locked["gptTr"].openai_max_retries == 1
    assert locked["Gem35T"].external_transcription_max_retries == 3
    assert "Gem37F" not in QUALITY_PROVIDER_MODELS


def test_dashboard_finds_failed_quality_job_for_same_audio_only(tmp_path, monkeypatch):
    storage = tmp_path / "jobs"
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", storage)
    source = tmp_path / "same-audio.wav"
    different = tmp_path / "different-audio.wav"
    source.write_bytes(b"authorized fixture audio")
    different.write_bytes(b"different fixture audio")

    failed_dir = job_mod.create_job("dashboard-quality-20260829090000-fixture", storage)
    failed_manifest = job_mod.read_manifest(failed_dir)
    failed_manifest["source"] = {"sha256": sha256_file(source)}
    failed_manifest["quality"] = {"overall_status": "failed"}
    job_mod.write_manifest(failed_dir, failed_manifest)

    assert local_dashboard._find_resumable_quality_job(source) == (
        "dashboard-quality-20260829090000-fixture"
    )
    assert local_dashboard._find_resumable_quality_job(different) is None


def test_revised_quality_trio_all_participate_in_paid_cap(tmp_path):
    cfg = _config(tmp_path / "jobs")
    chunks = [{"start_sec": 0.0, "end_sec": 100.0}]

    for provider_code in PROVIDER_ORDER:
        provider_cfg, estimate = _provider_preflight(provider_code, chunks, cfg)
        status = local_dashboard.get_runtime_model_status(
            QUALITY_PROVIDER_MODELS[provider_code], provider_cfg
        )
        assert estimate > 0.0
        assert status["cost_cap_included"] is True


def test_quality_workflow_without_red_uses_existing_regions_and_no_rescue_calls(tmp_path):
    source = tmp_path / "authorized-no-red.wav"
    generate_sine_wav(str(source), duration_sec=3.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)
    common_text = "Same retained multilingual text 世界 selamat pagi"
    factory = FakeQualityFactory(
        {provider: common_text for provider in PROVIDER_ORDER}
    )

    completed = run_quality_workflow(
        "quality-no-red",
        str(source),
        cfg,
        transcriber_factory=factory,
    )

    assert completed["ok"] is True, completed["errors"]
    manifest = job_mod.read_manifest(storage / "quality-no-red")
    chunk_count = len(manifest["chunks"])
    assert len(factory.calls) == chunk_count * 3
    assert manifest["quality"]["stages"]["RESCUE"]["mode"] == "not_required_no_red"
    assert manifest["quality"]["provider_calls"]["new"] == chunk_count * 3
    comparison = json.loads(
        Path(completed["outputs"]["comparison"]["json"]).read_text(encoding="utf-8")
    )
    draft = json.loads(
        Path(completed["outputs"]["final_draft"]["json"]).read_text(encoding="utf-8")
    )
    assert draft["statistics"]["total_windows"] == len(comparison["regions"])
    assert draft["statistics"]["review_required"] == 0
    assert completed["state"] == "complete"
    assert len(list(Path(manifest["quality"]["review_audio_dir"]).glob("*.wav"))) == len(
        comparison["regions"]
    )


def test_targeted_opt_in_flags_multi_speaker_window_even_when_text_agrees(tmp_path):
    source = tmp_path / "authorized-speaker-sensitive.wav"
    generate_sine_wav(str(source), duration_sec=3.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)
    common_text = "Same retained multilingual text 世界 selamat pagi"
    factory = FakeQualityFactory(
        {provider: common_text for provider in PROVIDER_ORDER}
    )

    completed = run_quality_workflow(
        "quality-speaker-sensitive",
        str(source),
        cfg,
        transcriber_factory=factory,
        targeted_human_verification_requested=True,
    )
    summary = completed["targeted_human_verification"]["summary"]

    assert completed["state"] == "review_required"
    assert summary["content_review_window_count"] == 0
    assert summary["speaker_sensitive_window_count"] >= 1
    assert summary["selected_window_count"] >= 1
    assert summary["speaker_decisions"]["pending"] == summary["selected_window_count"]
    assert summary["completion_label"] == "Automated transcript"


def test_quality_defers_oversized_rescue_to_review_without_provider_calls(
    tmp_path, monkeypatch
):
    source = tmp_path / "authorized-oversized-rescue.wav"
    generate_sine_wav(str(source), duration_sec=5.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _config(storage)
    factory = FakeQualityFactory()

    def oversized_windows(*args, **kwargs):
        comparison = args[1]
        selected = next(
            region
            for region in comparison["regions"]
            if region["agreement_status"] == "RED"
        )
        windows = [
            {
                "window_id": f"rescue-window-{index:05d}",
                "global_start_sec": float(index),
                "global_end_sec": float(index + 1),
            }
            for index in range(9)
        ]
        return selected, windows, {"source_audio_sha256": "fixture"}

    monkeypatch.setattr(
        "services.quality_workflow.build_rescue_windows", oversized_windows
    )

    completed = run_quality_workflow(
        "quality-oversized-rescue", str(source), cfg, transcriber_factory=factory
    )
    manifest = job_mod.read_manifest(storage / "quality-oversized-rescue")
    chunk_count = len(manifest["chunks"])

    assert completed["ok"] is True
    assert completed["state"] == "review_required"
    assert len(factory.calls) == chunk_count * 3
    assert manifest["quality"]["stages"]["RESCUE"]["mode"] == (
        "deferred_window_limit"
    )
    assert manifest["quality"]["stages"]["RESCUE"]["candidate_window_count"] == 9
    assert manifest["quality"]["stages"]["RESCUE"]["provider_calls"] == 0
    assert manifest["quality"]["review_url"].endswith("quality-oversized-rescue")
    comparison = json.loads(
        Path(completed["outputs"]["comparison"]["json"]).read_text(encoding="utf-8")
    )
    draft = json.loads(
        Path(completed["outputs"]["final_draft"]["json"]).read_text(encoding="utf-8")
    )
    assert draft["statistics"]["total_windows"] == len(comparison["regions"])
    assert draft["statistics"]["review_required"] == len(comparison["regions"])
    assert len(list(Path(manifest["quality"]["review_audio_dir"]).glob("*.wav"))) == len(
        comparison["regions"]
    )
