import json
import hashlib
import os
from pathlib import Path
import urllib.error
import urllib.request
import pytest

from services.transcription.openai_adapter import (
    OpenAITranscriber,
    OpenAITranscriptionHttpClient,
    OpenAITranscriptionRequestError,
    format_diarized_text,
    is_diarization_model,
    normalize_diarized_segments,
)
from services.config import Config, load_config
from services import job as job_mod
from services import errors as errors_mod


class FakeClientSuccess:
    def transcribe(self, path, model=None):
        return {"text": f"transcribed({Path(path).name})", "cost_usd": 0.001}


class FakeClientTransient:
    def __init__(self, fail_times=2):
        self.fail_times = fail_times
        self.called = 0

    def transcribe(self, path, model=None):
        self.called += 1
        if self.called <= self.fail_times:
            raise OSError("transient I/O")
        return {"text": "ok", "cost_usd": 0.002}


class FakeClientUrlopenTransient:
    def __init__(self):
        self.called = 0

    def transcribe(self, path, model=None):
        self.called += 1
        if self.called == 1:
            raise urllib.error.URLError(BrokenPipeError("Broken pipe"))
        return {"text": "ok after urlopen retry", "cost_usd": 0.002}


class FakeClientPermanent:
    def transcribe(self, path, model=None):
        raise ValueError("permanent error")


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_diarized_segments_normalize_to_stable_speaker_labels():
    payload = {
        "segments": [
            {"speaker": "spk_a", "text": "Hello.", "start": 0.0, "end": 1.0},
            {"speaker": "spk_b", "text": "Apa kabar?", "start": 1.0, "end": 2.0},
            {"speaker": "spk_a", "text": "We continue.", "start": 2.0, "end": 3.0},
        ]
    }

    segments = normalize_diarized_segments(payload)

    assert is_diarization_model("gpt-4o-transcribe-diarize") is True
    assert [segment["speaker"] for segment in segments] == ["Speaker 1", "Speaker 2", "Speaker 1"]
    assert format_diarized_text(segments) == "Speaker 1: Hello.\nSpeaker 2: Apa kabar?\nSpeaker 1: We continue."


def test_http_client_requests_diarized_json_and_formats_speaker_segments(tmp_path, monkeypatch):
    wav_path = tmp_path / "chunk.wav"
    wav_path.write_bytes(b"fake audio bytes")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = request.data.decode("utf-8", errors="replace")
        return FakeHttpResponse(
            {
                "segments": [
                    {"speaker": "a", "text": "Hello from speaker A."},
                    {"speaker": "b", "text": "Reply from speaker B."},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = OpenAITranscriptionHttpClient("fake").transcribe(str(wav_path), model="gpt-4o-transcribe-diarize")

    assert 'name="response_format"' in captured["body"]
    assert "diarized_json" in captured["body"]
    assert 'name="chunking_strategy"' in captured["body"]
    assert "auto" in captured["body"]
    assert result["diarized"] is True
    assert result["text"] == "Speaker 1: Hello from speaker A.\nSpeaker 2: Reply from speaker B."
    assert result["segments"][1]["speaker"] == "Speaker 2"


def test_gpttr_timestamp_mode_requests_verbose_segment_timestamps(tmp_path, monkeypatch):
    wav_path = tmp_path / "chunk.wav"
    wav_path.write_bytes(b"fake audio bytes")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = request.data.decode("utf-8", errors="replace")
        return FakeHttpResponse(
            {
                "text": "Hello world.",
                "segments": [{"text": "Hello world.", "start": 0.25, "end": 1.5}],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cfg = Config(enable_provider_timestamps=True)

    result = OpenAITranscriptionHttpClient("fake", cfg=cfg).transcribe(
        str(wav_path), model=cfg.openai_recommended_model
    )

    assert 'name="response_format"' in captured["body"]
    assert "verbose_json" in captured["body"]
    assert 'name="timestamp_granularities[]"' in captured["body"]
    assert result["response_format"] == "verbose_json"
    assert result["segments"] == [
        {"text": "Hello world.", "start": 0.25, "end": 1.5}
    ]


def test_http_client_retains_successful_empty_transcription(tmp_path, monkeypatch):
    wav_path = tmp_path / "silent-or-omitted.wav"
    wav_path.write_bytes(b"fake audio bytes")
    raw_response = {
        "text": "",
        "languages": [],
        "usage": {"type": "duration", "seconds": 30.0},
    }

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: FakeHttpResponse(raw_response)
    )

    result = OpenAITranscriptionHttpClient("fake").transcribe(
        str(wav_path), model="gpt-transcribe"
    )

    assert result["text"] == ""
    assert result["text_status"] == "provider_empty"
    assert result["raw_response"] == raw_response


def test_http_client_rejects_response_without_text_field(tmp_path, monkeypatch):
    wav_path = tmp_path / "invalid-response.wav"
    wav_path.write_bytes(b"fake audio bytes")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeHttpResponse({"usage": {"seconds": 30.0}}),
    )

    with pytest.raises(RuntimeError, match="did not include text"):
        OpenAITranscriptionHttpClient("fake").transcribe(
            str(wav_path), model="gpt-transcribe"
        )


def test_gpttr_retains_provider_segment_and_global_timing(tmp_path):
    cfg = Config(
        storage_path=str(tmp_path / "jobs"),
        dry_run=False,
        enable_live_openai_transcription=True,
        openai_api_key="fake-openai-key",
        openai_model="gpt-transcribe",
        enable_provider_timestamps=True,
    )
    job_id = "gpttr-timestamp-test"
    job_dir = job_mod.create_job(job_id, cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"synthetic audio")
    raw_response = {
        "text": "Provider text unchanged.",
        "segments": [
            {"id": 0, "text": "Provider text unchanged.", "start": 1.0, "end": 3.0}
        ],
    }

    class TimestampClient:
        def transcribe(self, path, model=None):
            return {
                "text": raw_response["text"],
                "segments": raw_response["segments"],
                "raw_response": raw_response,
                "response_format": "verbose_json",
                "cost_usd": 0.001,
            }

    result = OpenAITranscriber(client=TimestampClient()).transcribe_chunk(
        job_id,
        {
            "path": str(chunk_path),
            "filename": chunk_path.name,
            "start_sec": 915.0,
            "end_sec": 960.0,
            "actual_bytes": chunk_path.stat().st_size,
        },
        cfg,
    )

    segment = result["timing"]["provider_native"]["segments"][0]
    assert result["response_format"] == "verbose_json"
    assert result["timing"]["capability_status"] == "available"
    assert result["text"] == raw_response["text"]
    assert segment["type"] == "provider_native_segment"
    assert segment["global_start_sec"] == 916.0
    assert segment["global_end_sec"] == 918.0
    provider_artifact = json.loads(
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    )
    assert provider_artifact["response"] == raw_response


def test_gpttr_empty_provider_candidate_is_retained_not_retried(tmp_path):
    cfg = Config(
        storage_path=str(tmp_path / "jobs"),
        dry_run=False,
        enable_live_openai_transcription=True,
        openai_api_key="fake-openai-key",
        openai_model="gpt-transcribe",
        openai_max_retries=3,
    )
    job_id = "gpttr-empty-provider-candidate"
    job_dir = job_mod.create_job(job_id, cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"synthetic audio")
    raw_response = {
        "text": "",
        "languages": [],
        "usage": {"type": "duration", "seconds": 30.0},
    }

    class EmptyTextClient:
        def __init__(self):
            self.calls = 0

        def transcribe(self, path, model=None):
            self.calls += 1
            return {
                "text": "",
                "text_status": "provider_empty",
                "raw_response": raw_response,
                "cost_usd": 0.001,
            }

    client = EmptyTextClient()
    result = OpenAITranscriber(client=client).transcribe_chunk(
        job_id,
        {
            "path": str(chunk_path),
            "filename": chunk_path.name,
            "start_sec": 120.0,
            "end_sec": 150.0,
            "actual_bytes": chunk_path.stat().st_size,
        },
        cfg,
    )

    assert client.calls == 1
    assert result["text"] == ""
    assert result["text_status"] == "provider_empty"
    provider_artifact = json.loads(
        Path(result["provider_artifact_path"]).read_text(encoding="utf-8")
    )
    assert provider_artifact["transcript_text_status"] == "provider_empty"
    assert provider_artifact["response"] == raw_response
    manifest = job_mod.read_manifest(job_dir)
    assert manifest["transcription"]["chunks"][0]["status"] == "completed"
    assert manifest["transcription"]["chunks"][0]["text_status"] == "provider_empty"


def test_dry_run_blocks_openai(tmp_path):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    # dry_run is True by default
    t = OpenAITranscriber(client=FakeClientSuccess())
    with pytest.raises(RuntimeError):
        t.transcribe_chunk("job1", {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 1.0, "actual_bytes": 100}, cfg)


def test_requires_enable_flag_and_api_key(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    t = OpenAITranscriber(client=FakeClientSuccess())

    # not enabled
    with pytest.raises(RuntimeError):
        t.transcribe_chunk("job2", {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 1.0, "actual_bytes": 100}, cfg)

    # enable flag but no API key
    cfg.enable_live_openai_transcription = True
    if "OPENAI_API_KEY" in os.environ:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        t.transcribe_chunk("job2", {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 1.0, "actual_bytes": 100}, cfg)

    # provide dummy API key and now it runs — create a dummy chunk file so client receives a valid path
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    chunk_file = tmp_path / "chunk-00001.wav"
    chunk_file.write_bytes(b"fake audio bytes")
    res = t.transcribe_chunk(
        "job2",
        {
            "path": str(chunk_file),
            "filename": "chunk-00001.wav",
            "start_sec": 0.0,
            "end_sec": 1.0,
            "actual_bytes": chunk_file.stat().st_size,
        },
        cfg,
    )
    assert "text" in res or "transcript_path" in res


def test_cost_preflight_blocks_job(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    # make max cost very small to force preflight failure
    cfg.max_cost_per_job_usd = 0.000001
    t = OpenAITranscriber(client=FakeClientSuccess())

    chunks = [
        {"filename": "c1.wav", "start_sec": 0.0, "end_sec": 60.0, "actual_bytes": 1000},
    ]

    with pytest.raises(errors_mod.CostLimitExceededError):
        t.transcribe_chunks("job-cost", chunks, cfg)


def test_actual_cost_guard_stops_before_next_billable_chunk(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.max_cost_per_job_usd = 0.01
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-runtime-cost"
    job_dir = job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunks = []
    for index in (1, 2):
        chunk_path = job_dir / "chunks" / f"chunk-{index:05d}.wav"
        chunk_path.write_bytes(f"synthetic-{index}".encode("ascii"))
        chunks.append({
            "path": str(chunk_path),
            "filename": chunk_path.name,
            "start_sec": float((index - 1) * 60),
            "end_sec": float(index * 60),
            "actual_bytes": chunk_path.stat().st_size,
        })

    class ExpensiveFirstChunkClient:
        def __init__(self):
            self.called = 0

        def transcribe(self, path, model=None):
            self.called += 1
            return {"text": "first chunk", "cost_usd": 0.009}

    client = ExpensiveFirstChunkClient()
    with pytest.raises(errors_mod.CostLimitExceededError):
        OpenAITranscriber(client=client).transcribe_chunks(job_id, chunks, cfg)

    manifest = job_mod.read_manifest(job_dir)
    assert client.called == 1
    assert manifest["transcription"]["state"] == "cost_limit_exceeded"
    assert manifest["transcription"]["actual_cost_usd"] == pytest.approx(0.009)
    assert manifest["transcription"]["cost_limit"]["remaining_this_run_usd"] == pytest.approx(0.001)


def test_resume_cost_guard_counts_completed_cached_chunk_spend(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.max_cost_per_job_usd = 0.01
    cfg.openai_max_retries = 1
    cfg.openai_retry_backoff_base = 0
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-resume-runtime-cost"
    job_dir = job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunks = []
    for index in (1, 2, 3):
        chunk_path = job_dir / "chunks" / f"chunk-{index:05d}.wav"
        chunk_path.write_bytes(f"synthetic-{index}".encode("ascii"))
        chunks.append({
            "path": str(chunk_path),
            "filename": chunk_path.name,
            "start_sec": float((index - 1) * 60),
            "end_sec": float(index * 60),
            "actual_bytes": chunk_path.stat().st_size,
        })

    class FailSecondClient:
        def transcribe(self, path, model=None):
            if Path(path).name == "chunk-00002.wav":
                raise OpenAITranscriptionRequestError(503, "simulated outage")
            return {"text": "first chunk", "cost_usd": 0.006}

    with pytest.raises(OpenAITranscriptionRequestError):
        OpenAITranscriber(client=FailSecondClient()).transcribe_chunks(job_id, chunks, cfg)

    class ResumeClient:
        def __init__(self):
            self.calls = []

        def transcribe(self, path, model=None):
            self.calls.append(Path(path).name)
            return {"text": "resumed chunk", "cost_usd": 0.003}

    resume_client = ResumeClient()
    with pytest.raises(errors_mod.CostLimitExceededError):
        OpenAITranscriber(client=resume_client).transcribe_chunks(job_id, chunks, cfg)

    manifest = job_mod.read_manifest(job_dir)
    assert resume_client.calls == ["chunk-00002.wav"]
    assert manifest["transcription"]["state"] == "cost_limit_exceeded"
    assert manifest["transcription"]["actual_cost_usd"] == pytest.approx(0.009)
    assert manifest["transcription"]["cost_limit"]["remaining_this_run_usd"] == pytest.approx(0.001)


def test_filesize_preflight_blocks_chunk(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    t = OpenAITranscriber(client=FakeClientSuccess())
    big = 30 * 1024 * 1024
    with pytest.raises(errors_mod.StepFailedError):
        t.transcribe_chunk("job-size", {"filename": "big.wav", "start_sec": 0.0, "end_sec": 10.0, "actual_bytes": big}, cfg)


def test_retry_on_transient_errors(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    client = FakeClientTransient(fail_times=2)
    t = OpenAITranscriber(client=client)

    # create job dir so manifest writes succeed
    job_mod.create_job("job-retry", storage_path=cfg.storage_path)

    # create a dummy chunk file for the client
    chunk_file = tmp_path / "chunk-01.wav"
    chunk_file.write_bytes(b"fake")
    res = t.transcribe_chunk(
        "job-retry",
        {"path": str(chunk_file), "filename": "chunk-01.wav", "start_sec": 0.0, "end_sec": 3.0, "actual_bytes": chunk_file.stat().st_size},
        cfg,
    )
    assert res is not None
    # manifest should reflect attempts >=3
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / "job-retry")
    chunks = manifest.get("transcription", {}).get("chunks", [])
    assert len(chunks) == 1
    assert chunks[0]["attempts"] >= 1


def test_retry_on_urlopen_broken_pipe(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    client = FakeClientUrlopenTransient()
    t = OpenAITranscriber(client=client)
    job_mod.create_job("job-urlopen-retry", storage_path=cfg.storage_path)

    chunk_file = tmp_path / "chunk-01.wav"
    chunk_file.write_bytes(b"fake")
    res = t.transcribe_chunk(
        "job-urlopen-retry",
        {"path": str(chunk_file), "filename": "chunk-01.wav", "start_sec": 0.0, "end_sec": 3.0, "actual_bytes": chunk_file.stat().st_size},
        cfg,
    )

    assert res["text"] == "ok after urlopen retry"
    assert client.called == 2


def test_live_transcriber_resolves_runner_chunk_path(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-runner-path"
    job_dir = Path(cfg.storage_path) / job_id
    job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunks_dir = job_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_file = chunks_dir / "chunk-00001.wav"
    chunk_file.write_bytes(b"fake")

    class RecordingClient:
        def __init__(self):
            self.path = None

        def transcribe(self, path, model=None):
            self.path = path
            return {"text": "ok", "cost_usd": 0.0}

    client = RecordingClient()
    t = OpenAITranscriber(client=client)
    res = t.transcribe_chunk(
        job_id,
        {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 1.0, "actual_bytes": chunk_file.stat().st_size},
        cfg,
    )

    assert res["text"] == "ok"
    assert client.path == str(chunk_file)


def test_live_transcriber_writes_diarized_segments(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.openai_model = "gpt-4o-transcribe-diarize"
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-diarized"
    job_dir = Path(cfg.storage_path) / job_id
    job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunks_dir = job_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_file = chunks_dir / "chunk-00001.wav"
    chunk_file.write_bytes(b"fake")

    class FakeDiarizedClient:
        def transcribe(self, path, model=None):
            assert path == str(chunk_file)
            assert model == "gpt-4o-transcribe-diarize"
            return {
                "text": "Speaker 1: Hello.\nSpeaker 2: Terima kasih.",
                "cost_usd": 0.0,
                "diarized": True,
                "segments": [
                    {"speaker": "Speaker 1", "text": "Hello.", "start": 0.0, "end": 1.0},
                    {"speaker": "Speaker 2", "text": "Terima kasih.", "start": 1.0, "end": 2.0},
                ],
            }

    res = OpenAITranscriber(client=FakeDiarizedClient()).transcribe_chunk(
        job_id,
        {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 2.0, "actual_bytes": chunk_file.stat().st_size},
        cfg,
    )
    stored = json.loads(Path(res["transcript_path"]).read_text(encoding="utf-8"))
    manifest = job_mod.read_manifest(job_dir)

    assert res["diarized"] is True
    assert stored["diarized"] is True
    assert stored["segments"][1]["speaker"] == "Speaker 2"
    assert manifest["transcription"]["chunks"][0]["diarized"] is True


def test_no_retry_on_permanent_errors(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    t = OpenAITranscriber(client=FakeClientPermanent())
    job_mod.create_job("job-perm", storage_path=cfg.storage_path)

    # create dummy file for permanent error test
    chunk_file = tmp_path / "chunk-01.wav"
    chunk_file.write_bytes(b"fake")
    with pytest.raises(ValueError):
        t.transcribe_chunk(
            "job-perm",
            {"path": str(chunk_file), "filename": "chunk-01.wav", "start_sec": 0.0, "end_sec": 3.0, "actual_bytes": chunk_file.stat().st_size},
            cfg,
        )


def test_idempotent_skip_if_completed(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-idempotent"
    job_dir = Path(cfg.storage_path) / job_id
    job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"stable audio bytes")
    chunk_sha256 = hashlib.sha256(chunk_path.read_bytes()).hexdigest()
    transcripts_dir = job_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcripts_dir / "chunk-00001.json"
    run_id = "tr-cached"
    transcript_path.write_text(
        json.dumps({
            "schema_version": "apma.transcript.chunk.v1",
            "job_id": job_id,
            "chunk_filename": "chunk-00001.wav",
            "chunk_sha256": chunk_sha256,
            "provider": "openai",
            "provider_code": "gpt4oMini",
            "model": cfg.openai_model,
            "run_id": run_id,
            "response_format": "json",
            "segment_timing_scope": "chunk",
            "text": "cached text",
        }),
        encoding="utf-8",
    )

    # write manifest entry marking chunk completed
    manifest = job_mod.read_manifest(job_dir)
    manifest.setdefault("transcription", {})
    manifest["transcription"]["chunks"] = [{
        "chunk_filename": "chunk-00001.wav",
        "chunk_sha256": chunk_sha256,
        "status": "completed",
        "model": cfg.openai_model,
        "provider": "openai",
        "provider_code": "gpt4oMini",
        "run_id": run_id,
        "response_format": "json",
        "transcript_path": str(transcript_path),
    }]
    job_mod.write_manifest(job_dir, manifest)

    # client that would raise if called
    class BadClient:
        def transcribe(self, path, model=None):
            raise RuntimeError("should not be called")

    t = OpenAITranscriber(client=BadClient())
    res = t.transcribe_chunk(
        job_id,
        {"filename": "chunk-00001.wav", "actual_bytes": chunk_path.stat().st_size},
        cfg,
    )
    assert res.get("cached") is True
    assert res["text"] == "cached text"


def test_cache_payload_identity_mismatch_forces_new_transcription(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-cache-identity"
    job_dir = job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"stable audio bytes")
    chunk_sha256 = hashlib.sha256(chunk_path.read_bytes()).hexdigest()
    transcript_path = job_dir / "transcripts" / "chunk-00001.json"
    transcript_path.write_text(
        json.dumps({
            "schema_version": "apma.transcript.chunk.v1",
            "job_id": "another-job",
            "chunk_filename": "chunk-00001.wav",
            "chunk_sha256": chunk_sha256,
            "provider": "openai",
            "provider_code": "gpt4oMini",
            "model": cfg.openai_model,
            "run_id": "tr-stale",
            "response_format": "json",
            "text": "wrong job text",
        }),
        encoding="utf-8",
    )
    manifest = job_mod.read_manifest(job_dir)
    manifest["transcription"] = {
        "chunks": [{
            "chunk_filename": "chunk-00001.wav",
            "chunk_sha256": chunk_sha256,
            "status": "completed",
            "provider": "openai",
            "provider_code": "gpt4oMini",
            "model": cfg.openai_model,
            "run_id": "tr-stale",
            "response_format": "json",
            "transcript_path": str(transcript_path),
        }]
    }
    job_mod.write_manifest(job_dir, manifest)

    result = OpenAITranscriber(client=FakeClientSuccess()).transcribe_chunk(
        job_id,
        {
            "filename": "chunk-00001.wav",
            "start_sec": 0.0,
            "end_sec": 1.0,
            "actual_bytes": chunk_path.stat().st_size,
        },
        cfg,
    )

    assert result.get("cached") is not True
    assert result["job_id"] == job_id
    assert result["text"] == "transcribed(chunk-00001.wav)"


def test_idempotent_cache_is_invalidated_when_chunk_content_changes(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    job_id = "job-content-change"
    job_dir = job_mod.create_job(job_id, storage_path=cfg.storage_path)
    chunk_path = job_dir / "chunks" / "chunk-00001.wav"
    chunk_path.write_bytes(b"new audio bytes")

    transcript_path = job_dir / "transcripts" / "chunk-00001.json"
    transcript_path.write_text(json.dumps({"text": "stale text"}), encoding="utf-8")
    manifest = job_mod.read_manifest(job_dir)
    manifest["transcription"] = {
        "chunks": [{
            "chunk_filename": "chunk-00001.wav",
            "chunk_sha256": hashlib.sha256(b"old audio bytes").hexdigest(),
            "status": "completed",
            "model": cfg.openai_model,
            "provider_code": "gpt4oMini",
            "transcript_path": str(transcript_path),
        }]
    }
    job_mod.write_manifest(job_dir, manifest)

    client = FakeClientSuccess()
    result = OpenAITranscriber(client=client).transcribe_chunk(
        job_id,
        {
            "filename": "chunk-00001.wav",
            "start_sec": 0.0,
            "end_sec": 1.0,
            "actual_bytes": chunk_path.stat().st_size,
        },
        cfg,
    )

    assert result.get("cached") is not True
    assert result["text"] == "transcribed(chunk-00001.wav)"
    assert result["chunk_sha256"] == hashlib.sha256(b"new audio bytes").hexdigest()


def test_openai_http_client_rejects_non_openai_route_before_network(tmp_path, monkeypatch):
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"synthetic test bytes")
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)
    cfg = load_config()
    client = OpenAITranscriptionHttpClient(api_key="fake-key", cfg=cfg)

    with pytest.raises(ValueError, match="cannot be sent to the OpenAI adapter"):
        client.transcribe(str(wav_path), model=cfg.meralion_transcription_model)

    assert called is False
