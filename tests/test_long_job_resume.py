import json
from collections import Counter
from pathlib import Path

from services import chunker as chunker_mod
from services import job as job_mod
from services import preprocess as preprocess_mod
from services import runner
from services import transcription as transcription_mod
from services.config import load_config
from services.transcription.openai_adapter import (
    OpenAITranscriber,
    OpenAITranscriptionRequestError,
)
from tests.helpers import generate_sine_wav


class FailMiddleChunkOnceClient:
    def __init__(self):
        self.calls = Counter()

    def transcribe(self, path, model=None):
        filename = Path(path).name
        self.calls[filename] += 1
        if filename == "chunk-00002.wav" and self.calls[filename] == 1:
            raise OpenAITranscriptionRequestError(503, "simulated retryable outage")
        return {
            "text": f"Transcript for {filename}.",
            "cost_usd": 0.001,
        }


class AlwaysSuccessfulClient:
    def __init__(self):
        self.calls = Counter()

    def transcribe(self, path, model=None):
        filename = Path(path).name
        self.calls[filename] += 1
        return {
            "text": f"Transcript for {filename}.",
            "cost_usd": 0.001,
        }


def test_failed_middle_chunk_resumes_without_recalling_completed_chunk(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.wav"
    generate_sine_wav(str(source), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "openai"
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.openai_api_key = "fake-test-key"
    cfg.openai_max_retries = 1
    cfg.openai_retry_backoff_base = 0
    cfg.max_cost_per_job_usd = 1.0

    def fake_preprocess(source_path, job_dir, cfg_in):
        return {
            "path": source_path,
            "normalized": False,
            "source_format": ".wav",
            "source_probe": {},
            "audio_qc": {"processing_audio": {}},
        }

    def fake_chunk_file(source_path, job_dir, pre_meta, cfg_in):
        chunks = []
        for index in range(3):
            filename = f"chunk-{index + 1:05d}.wav"
            path = Path(job_dir) / "chunks" / filename
            path.write_bytes(f"fixture audio {index + 1}".encode("utf-8"))
            start_sec = float(index * 10)
            chunks.append(
                {
                    "chunk_index": index,
                    "filename": filename,
                    "path": str(path),
                    "start_sec": start_sec,
                    "end_sec": start_sec + 10.0,
                    "actual_bytes": path.stat().st_size,
                }
            )
        return chunks

    monkeypatch.setattr(preprocess_mod, "preprocess_audio", fake_preprocess)
    monkeypatch.setattr(chunker_mod, "chunk_file", fake_chunk_file)

    active_client = {"value": FailMiddleChunkOnceClient()}
    monkeypatch.setattr(
        transcription_mod,
        "get_transcriber",
        lambda name: OpenAITranscriber(client=active_client["value"]),
    )

    aggregate_calls = Counter()
    real_aggregate = runner.transcript_aggregator_mod.aggregate_transcripts

    def counted_aggregate(job_id, job_dir, transcript_entries):
        aggregate_calls[job_id] += 1
        return real_aggregate(job_id, job_dir, transcript_entries)

    monkeypatch.setattr(
        runner.transcript_aggregator_mod,
        "aggregate_transcripts",
        counted_aggregate,
    )

    job_id = "goal-c-resume"
    first_result = runner.run_job(job_id, str(source), cfg)
    first_manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    first_calls = dict(active_client["value"].calls)

    assert first_result["state"] == "failed"
    assert first_calls == {
        "chunk-00001.wav": 1,
        "chunk-00002.wav": 1,
    }
    assert aggregate_calls[job_id] == 0
    assert first_manifest["state"] == "failed"
    assert first_manifest["transcription"]["state"] == "failed"
    assert first_manifest["transcription"]["retryable"] is True
    assert [
        (entry["chunk_filename"], entry["status"], entry["attempts"])
        for entry in first_manifest["transcription"]["chunks"]
    ] == [
        ("chunk-00001.wav", "completed", 1),
        ("chunk-00002.wav", "failed", 1),
    ]
    assert first_manifest["transcription"]["chunks"][1]["retryable"] is True
    assert first_manifest["transcription"]["chunks"][1]["max_attempts_per_run"] == 1
    assert first_manifest["transcription"]["chunks"][1]["total_attempts"] == 1

    completed_path = Path(
        first_manifest["transcription"]["chunks"][0]["transcript_path"]
    )
    completed_bytes = completed_path.read_bytes()
    original_chunk_times = [
        (chunk["start_sec"], chunk["end_sec"]) for chunk in first_manifest["chunks"]
    ]

    resumed_result = runner.run_job(job_id, str(source), cfg)
    resumed_manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    cumulative_calls = dict(active_client["value"].calls)
    resumed_calls = {
        filename: cumulative_calls.get(filename, 0) - first_calls.get(filename, 0)
        for filename in (
            "chunk-00001.wav",
            "chunk-00002.wav",
            "chunk-00003.wav",
        )
    }

    assert resumed_result["state"] == "completed"
    assert resumed_calls == {
        "chunk-00001.wav": 0,
        "chunk-00002.wav": 1,
        "chunk-00003.wav": 1,
    }
    assert completed_path.read_bytes() == completed_bytes
    assert aggregate_calls[job_id] == 1
    assert resumed_manifest["state"] == "completed"
    assert resumed_manifest["transcription"]["state"] == "completed"
    assert resumed_manifest["transcription"]["retryable"] is False
    assert resumed_manifest["transcription"]["resume_count"] == 1
    assert all(
        entry["status"] == "completed"
        for entry in resumed_manifest["transcription"]["chunks"]
    )
    resumed_chunk_states = {
        entry["chunk_filename"]: entry
        for entry in resumed_manifest["transcription"]["chunks"]
    }
    assert resumed_chunk_states["chunk-00001.wav"]["total_attempts"] == 1
    assert resumed_chunk_states["chunk-00002.wav"]["attempts"] == 1
    assert resumed_chunk_states["chunk-00002.wav"]["total_attempts"] == 2
    assert resumed_chunk_states["chunk-00002.wav"]["max_attempts_per_run"] == 1
    assert resumed_chunk_states["chunk-00003.wav"]["total_attempts"] == 1
    assert len(resumed_manifest["transcription"]["errors"]) == 1
    assert (
        resumed_manifest["transcription"]["errors"][0]["type"]
        == "OpenAITranscriptionRequestError"
    )
    assert [
        entry["chunk_filename"]
        for entry in resumed_manifest["transcription"]["transcripts"]
    ] == [
        "chunk-00001.wav",
        "chunk-00002.wav",
        "chunk-00003.wav",
    ]
    assert [
        (chunk["start_sec"], chunk["end_sec"])
        for chunk in resumed_manifest["chunks"]
    ] == original_chunk_times
    assert (
        resumed_manifest["transcription"]["preflight"]["estimated_cost_usd"]
        <= cfg.max_cost_per_job_usd
    )

    resumed_output = json.loads(
        Path(resumed_manifest["outputs"]["full_transcript_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert [chunk["chunk_filename"] for chunk in resumed_output["chunks"]] == [
        "chunk-00001.wav",
        "chunk-00002.wav",
        "chunk-00003.wav",
    ]
    assert resumed_output["text"].count("Transcript for chunk-00001.wav.") == 1
    assert resumed_output["text"].count("Transcript for chunk-00002.wav.") == 1
    assert resumed_output["text"].count("Transcript for chunk-00003.wav.") == 1

    uninterrupted_job_id = "goal-c-uninterrupted"
    active_client["value"] = AlwaysSuccessfulClient()
    uninterrupted_result = runner.run_job(uninterrupted_job_id, str(source), cfg)
    uninterrupted_manifest = job_mod.read_manifest(
        Path(cfg.storage_path) / uninterrupted_job_id
    )
    uninterrupted_output = json.loads(
        Path(uninterrupted_manifest["outputs"]["full_transcript_json"]).read_text(
            encoding="utf-8"
        )
    )

    assert uninterrupted_result["state"] == "completed"
    assert aggregate_calls[uninterrupted_job_id] == 1
    assert resumed_output["text"] == uninterrupted_output["text"]
    assert [
        (chunk["chunk_filename"], chunk["chunk_start_sec"], chunk["chunk_end_sec"])
        for chunk in resumed_output["chunks"]
    ] == [
        (chunk["chunk_filename"], chunk["chunk_start_sec"], chunk["chunk_end_sec"])
        for chunk in uninterrupted_output["chunks"]
    ]
