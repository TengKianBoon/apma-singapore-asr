from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import local_dashboard
from services import job as job_mod
from services.config import Config
from services.human_review import REVIEW_HTML, ReviewWorkspace
from services.quality_reconciliation import (
    OpenAIReconciliationHttpClient,
    build_chunk_matched_inputs,
    estimate_reconciliation,
    run_reconciliation,
)
from services.quality_workflow import run_quality_workflow
from tests.helpers import generate_sine_wav


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provider_identity(engine: str, cfg: Config) -> tuple[str, str, str]:
    if engine == "openai" and cfg.openai_model == "gpt-transcribe":
        return "OpenAI", "gptTr", "gpt-transcribe"
    if engine == "openai":
        return "OpenAI", "gpt4oDiarz", "gpt-4o-transcribe-diarize"
    if engine == "gemini":
        return "Google", "Gem35T", "gemini-3.5-transcribe"
    raise AssertionError(engine)


class _FakeTranscriber:
    def __init__(self, engine: str):
        self.engine = engine

    def transcribe_chunk(self, job_id: str, chunk: dict, cfg: Config) -> dict:
        provider, code, model = _provider_identity(self.engine, cfg)
        filename = str(chunk["filename"])
        job_dir = Path(cfg.storage_path) / job_id
        transcript_path = job_dir / "transcripts" / f"{code}-{filename}.json"
        raw_path = job_dir / "providers" / code / f"{filename}.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        text = {
            "gptTr": f"叔叔说这个计划可以继续 {filename}",
            "gpt4oDiarz": f"spk:0 叔叔讲这个计划可以继续 {filename}",
            "Gem35T": f"叔叔说，这个计划可以继续。{filename}",
        }[code]
        raw_path.write_text(json.dumps({"text": text}), encoding="utf-8")
        result = {
            "provider": provider,
            "provider_code": code,
            "model": model,
            "run_id": f"fake-{code}",
            "chunk_filename": filename,
            "chunk_sha256": chunk["clip_sha256"],
            "start_sec": float(chunk["start_sec"]),
            "end_sec": float(chunk["end_sec"]),
            "duration_seconds": float(chunk["end_sec"]) - float(chunk["start_sec"]),
            "text": text,
            "estimated_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "provider_artifact_path": str(raw_path),
            "transcript_path": str(transcript_path),
        }
        transcript_path.write_text(json.dumps(result), encoding="utf-8")
        return result

    def transcribe_chunks(self, job_id: str, chunks: list[dict], cfg: Config) -> list[dict]:
        return [self.transcribe_chunk(job_id, chunk, cfg) for chunk in chunks]


class _FakeFactory:
    def __call__(self, engine: str) -> _FakeTranscriber:
        return _FakeTranscriber(engine)


class _FakeSolClient:
    def __init__(self):
        self.calls: list[str] = []

    def reconcile(self, *, model: str, reasoning_effort: str, window: dict, max_output_tokens: int) -> dict:
        self.calls.append(window["window_id"])
        uncertain = window["chunk_index"] == 2
        result = {
            "proposed_text": f"GPT-5.6 Sol proposed exact transcript {window['chunk_index']}",
            "review_required": uncertain,
            "uncertainty_reasons": ["name differs between providers"] if uncertain else [],
            "evidence_provider_codes": ["gptTr", "gpt4oDiarz", "Gem35T"],
            "speaker_notes": ["spk:0 is provider evidence only"],
        }
        return {
            "result": result,
            "raw_response": {
                "id": f"response-{window['chunk_index']}",
                "model": model,
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            },
        }


def _cfg(storage: Path) -> Config:
    return Config(
        storage_path=str(storage),
        dry_run=False,
        max_cost_per_job_usd=5.0,
        enable_live_openai_transcription=True,
        enable_live_gemini_transcription=True,
        enable_live_openai_reconciliation=True,
        openai_api_key="fixture-openai-key",
        gemini_api_key="fixture-gemini-key",
        default_chunk_duration_sec=2,
        min_chunk_duration_sec=1,
        max_chunk_duration_sec=2,
        overlap_seconds=0,
        chunk_boundary_search_window_sec=0,
        smart_chunking_enabled=False,
    )


def _transcribed_job(tmp_path: Path) -> tuple[Path, Config, Path]:
    source = tmp_path / "authorized.wav"
    generate_sine_wav(str(source), duration_sec=3.2, framerate=8000)
    storage = tmp_path / "jobs"
    cfg = _cfg(storage)
    result = run_quality_workflow(
        "quality-reconcile-fixture",
        str(source),
        cfg,
        transcriber_factory=_FakeFactory(),
        stop_after_stage="TRANSCRIBE",
    )
    assert result["ok"] is False
    job_dir = storage / "quality-reconcile-fixture"
    manifest = job_mod.read_manifest(job_dir)
    assert len(manifest["chunks"]) == 2
    old_audio = job_dir / "quality" / "old-review-clips"
    old_audio.mkdir(parents=True)
    (old_audio / "obsolete.wav").write_bytes(b"derived-old-review-audio")
    manifest["quality"]["review_audio_dir"] = str(old_audio)
    job_mod.write_manifest(job_dir, manifest)
    return job_dir, cfg, source


def test_chunk_matching_uses_exact_same_two_chunks_and_preserves_provider_hashes(tmp_path):
    job_dir, cfg, _ = _transcribed_job(tmp_path)
    manifest = job_mod.read_manifest(job_dir)
    windows, provider_inputs = build_chunk_matched_inputs(manifest)

    assert len(windows) == 2
    assert [window["chunk_sha256"] for window in windows] == [
        chunk["clip_sha256"] for chunk in manifest["chunks"]
    ]
    assert all(set(window["provider_candidates"]) == {"gptTr", "gpt4oDiarz", "Gem35T"} for window in windows)
    assert all(item["modified"] is False for item in provider_inputs.values())
    estimate = estimate_reconciliation(manifest, cfg)
    assert estimate["window_count"] == 2
    assert estimate["matching_unit"] == "same_application_owned_audio_chunk"
    assert estimate["audio_sent_to_model"] is False
    assert estimate["provider_api_calls"] == 0
    assert estimate["run_budget_cap_usd"] == pytest.approx(
        estimate["estimated_cost_usd"] * 1.15
    )


def test_reconciliation_reduces_review_to_exact_chunks_and_resumes_without_repeat_calls(tmp_path):
    job_dir, cfg, source = _transcribed_job(tmp_path)
    manifest_before = job_mod.read_manifest(job_dir)
    provider_paths = {
        code: Path(item["json"])
        for code, item in manifest_before["quality"]["outputs"]["providers"].items()
    }
    provider_hashes = {code: _sha256(path) for code, path in provider_paths.items()}
    source_hash = _sha256(source)
    estimate = estimate_reconciliation(manifest_before, cfg)
    fake = _FakeSolClient()

    result = run_reconciliation(
        job_dir,
        cfg,
        confirm_live_reconciliation=True,
        run_budget_cap_usd=estimate["run_budget_cap_usd"],
        client=fake,
    )

    assert result["ok"] is True
    assert result["calls"] == {"new": 2, "reused": 0}
    assert fake.calls == ["reconcile-chunk-00001", "reconcile-chunk-00002"]
    manifest = job_mod.read_manifest(job_dir)
    assert manifest["quality"]["reconciliation"]["window_count"] == 2
    assert not (job_dir / "quality" / "old-review-clips").exists()
    assert _sha256(source) == source_hash
    assert {code: _sha256(path) for code, path in provider_paths.items()} == provider_hashes

    draft_path = Path(manifest["quality"]["outputs"]["final_draft"]["json"])
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft["provider_order"] == ["gptTr", "gpt4oDiarz", "Gem35T", "GPT56Sol"]
    assert draft["statistics"] == {
        "total_windows": 2,
        "auto_accepted": 1,
        "review_required": 1,
        "status_counts": {"GREEN": 1, "AMBER": 1, "RED": 0},
    }
    assert draft["windows"][0]["final_text"] == draft["windows"][0]["provider_candidates"]["GPT56Sol"]["text"]
    assert draft["windows"][1]["final_text"] is None
    assert draft["windows"][1]["review_required"] is True
    review = ReviewWorkspace(
        draft_path,
        Path(manifest["quality"]["review_audio_dir"]),
        Path(manifest["quality"]["outputs"]["final_reviewed"]["json"]).parent,
    ).view()
    assert review["statistics"]["total_windows"] == 2
    assert review["statistics"]["unresolved_windows"] == 1
    _, audio_meta = ReviewWorkspace(
        draft_path,
        Path(manifest["quality"]["review_audio_dir"]),
        Path(manifest["quality"]["outputs"]["final_reviewed"]["json"]).parent,
    ).audio("reconcile-chunk-00002")
    assert audio_meta["global_start_sec"] == manifest["chunks"][1]["global_start_sec"]
    assert audio_meta["global_end_sec"] == manifest["chunks"][1]["global_end_sec"]

    calls_before = list(fake.calls)
    resumed = run_reconciliation(
        job_dir,
        cfg,
        confirm_live_reconciliation=True,
        run_budget_cap_usd=estimate["run_budget_cap_usd"],
        client=fake,
    )
    assert resumed["calls"] == {"new": 0, "reused": 2}
    assert fake.calls == calls_before


def test_reconciliation_requires_feature_key_confirmation_and_exact_budget(tmp_path):
    job_dir, cfg, _ = _transcribed_job(tmp_path)
    estimate = estimate_reconciliation(job_mod.read_manifest(job_dir), cfg)
    cfg.enable_live_openai_reconciliation = False
    cfg.openai_api_key = None
    blocked = run_reconciliation(
        job_dir,
        cfg,
        confirm_live_reconciliation=False,
        run_budget_cap_usd=estimate["run_budget_cap_usd"] + 0.01,
        client=_FakeSolClient(),
    )
    assert blocked["ok"] is False
    joined = " ".join(blocked["errors"])
    assert "ENABLE_LIVE_OPENAI_RECONCILIATION" in joined
    assert "OPENAI_API_KEY" in joined
    assert "explicit confirmation" in joined
    assert "Estimate again" in joined


def test_openai_request_uses_sol_responses_structured_text_and_never_audio(monkeypatch):
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "output_text": json.dumps(
                        {
                            "proposed_text": "测试",
                            "review_required": False,
                            "uncertainty_reasons": [],
                            "evidence_provider_codes": ["gptTr"],
                            "speaker_notes": [],
                        }
                    )
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["authorization"] = request.headers.get("Authorization")
        return _Response()

    monkeypatch.setattr("services.quality_reconciliation.urllib.request.urlopen", fake_urlopen)
    client = OpenAIReconciliationHttpClient("secret-fixture", "https://example.invalid", 1, 1)
    window = {
        "window_id": "reconcile-chunk-00001",
        "global_start_sec": 0.0,
        "global_end_sec": 2.0,
        "provider_candidates": {
            code: {"text": f"{code} text"}
            for code in ("gptTr", "gpt4oDiarz", "Gem35T")
        },
    }
    client.reconcile(model="gpt-5.6-sol", reasoning_effort="medium", window=window, max_output_tokens=900)
    body = captured["body"]
    assert body["model"] == "gpt-5.6-sol"
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "medium"}
    assert body["text"]["format"]["type"] == "json_schema"
    serialized = json.dumps(body).lower()
    assert "input_audio" not in serialized
    assert "audio_url" not in serialized
    assert "file_id" not in serialized
    assert "secret-fixture" not in json.dumps(body)
    assert captured["authorization"] == "Bearer secret-fixture"


def test_dashboard_exposes_beginner_cost_gate_and_reconciliation_routes(tmp_path, monkeypatch):
    job_dir, cfg, _ = _transcribed_job(tmp_path)
    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", job_dir.parent)
    estimate = local_dashboard.estimate_job_reconciliation(job_dir.name, cfg)
    assert estimate["already_completed"] is False
    assert estimate["window_count"] == 2
    assert "/api/quality/reconcile/estimate" in REVIEW_HTML
    assert "/api/quality/reconcile/run" in REVIEW_HTML
    assert "same exact audio chunk" in REVIEW_HTML
    assert "does not hear the audio" in REVIEW_HTML
