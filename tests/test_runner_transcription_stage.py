from pathlib import Path
import json

from services.runner import run_job
from services.config import load_config
from services import job as job_mod
from services import transcription as transcription_mod
from services import preprocess as preprocess_mod
from services import runner as runner_mod
from tests.helpers import generate_sine_wav


def test_runner_transcribes_with_mock(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=2.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "mock"
    cfg.dry_run = True

    job_id = "job-transcribe-mock"
    res = run_job(job_id, str(src), cfg)
    assert res["state"] == "completed"

    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    tx = manifest.get("transcription", {})
    assert tx.get("state") == "completed"
    transcripts = tx.get("transcripts", [])
    chunks = manifest.get("chunks", [])
    assert len(chunks) >= 1
    assert len(transcripts) >= 1

    # ensure transcript files exist
    for t in transcripts:
        path = Path(t.get("transcript_path"))
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("job_id") == job_id

    outputs = manifest.get("outputs", {})
    assert outputs.get("state") == "completed"
    full_json = Path(outputs.get("full_transcript_json"))
    full_txt = Path(outputs.get("full_transcript_txt"))
    assert full_json.exists()
    assert full_txt.exists()

    full_data = json.loads(full_json.read_text(encoding="utf-8"))
    assert full_data.get("job_id") == job_id
    assert len(full_data.get("chunks", [])) == len(transcripts)
    assert "Mock transcript for" in full_data.get("text", "")
    assert "Mock transcript for" in full_txt.read_text(encoding="utf-8")
    assert Path(outputs.get("minutes_md")).exists()
    assert Path(outputs.get("action_items_json")).exists()
    assert Path(outputs.get("job_summary_json")).exists()


def test_runner_normalizes_non_wav_before_mock_transcription(tmp_path, monkeypatch):
    src = tmp_path / "input.mp3"
    src.write_bytes(b"fake mp3 bytes")
    wav_template = tmp_path / "template.wav"
    generate_sine_wav(str(wav_template), duration_sec=2.0, framerate=8000)

    monkeypatch.setattr(
        preprocess_mod,
        "_run_ffprobe_json",
        lambda path, cfg_in: {
            "format": {"duration": "2.0", "size": str(src.stat().st_size), "format_name": "mp3"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "channels": 2,
                    "duration": "2.0",
                }
            ],
        },
    )

    def fake_normalize(src_path, dest_path, cfg_in):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(wav_template.read_bytes())

    def fake_canonical(src_path, job_path, probed, cfg_in):
        canonical = Path(job_path) / "preprocess" / "canonical.flac"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(wav_template.read_bytes())
        return {"path": str(canonical), "lossless": True, "channels": 2}

    monkeypatch.setattr(preprocess_mod, "_create_canonical_audio", fake_canonical)
    monkeypatch.setattr(preprocess_mod, "_run_ffmpeg_normalize", fake_normalize)
    monkeypatch.setattr(
        runner_mod.audio_project_mod,
        "create_audio_project",
        lambda source_path, job_dir, cfg_in, original_filename=None: {
            "subproject_folder": str(Path(job_dir).resolve()),
            "original_folder": str((Path(job_dir) / "audio" / "original").resolve()),
            "mp3_folder": str((Path(job_dir) / "audio" / "mp3").resolve()),
            "mp3_chunks_folder": str(
                (Path(job_dir) / "audio" / "mp3" / "chunks").resolve()
            ),
        },
    )

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "mock"
    cfg.dry_run = True

    job_id = "job-transcribe-mp3-mock"
    res = run_job(job_id, str(src), cfg)

    assert res["state"] == "completed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    assert manifest["preprocess"]["normalized"] is True
    assert manifest["preprocess"]["source_format"] == ".mp3"
    assert Path(manifest["preprocess"]["path"]).name == "normalized.wav"
    assert Path(manifest["preprocess"]["canonical_audio"]["path"]).name == "canonical.flac"
    assert len(manifest.get("chunks", [])) >= 1


def test_transcription_engine_respected(tmp_path, monkeypatch):
    src = tmp_path / "input2.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "mock"
    cfg.dry_run = True

    called = {"n": 0, "args": None}

    class FakeTranscriber:
        def transcribe_chunks(self, job_id, chunks, cfg_in):
            called["n"] += 1
            called["args"] = (job_id, chunks)
            return []

    def fake_factory(name):
        return FakeTranscriber()

    monkeypatch.setattr(transcription_mod, "get_transcriber", fake_factory)

    job_id = "job-transcriber-factory"
    res = run_job(job_id, str(src), cfg)
    assert res["state"] == "completed"
    assert called["n"] == 1
    assert called["args"][0] == job_id


def test_runner_allows_mock_minutes_after_live_transcription_smoke(tmp_path, monkeypatch):
    src = tmp_path / "input-live-smoke.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "openai"
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.openai_api_key = "fake"

    seen = {}

    class FakeLiveTranscriber:
        def transcribe_chunks(self, job_id, chunks, cfg_in):
            seen["dry_run"] = cfg_in.dry_run
            transcripts_dir = Path(cfg_in.storage_path) / job_id / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcripts_dir / "chunk-00001.json"
            payload = {
                "job_id": job_id,
                "chunk_filename": chunks[0]["filename"],
                "transcript_path": str(transcript_path),
                "duration_seconds": 1.0,
                "text": "Live smoke transcript text.",
                "actual_cost_usd": 0.0,
            }
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")
            return [payload]

    monkeypatch.setattr(transcription_mod, "get_transcriber", lambda name: FakeLiveTranscriber())

    job_id = "job-live-transcription-mock-minutes"
    res = run_job(job_id, str(src), cfg)

    assert seen["dry_run"] is False
    assert res["state"] == "completed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    outputs = manifest.get("outputs", {})
    assert Path(outputs["minutes_md"]).exists()
    assert Path(outputs["action_items_json"]).exists()
    assert Path(outputs["job_summary_json"]).exists()


def test_transcriber_failure_sets_failed(tmp_path, monkeypatch):
    src = tmp_path / "input3.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "mock"
    cfg.dry_run = True

    class BadTranscriber:
        def transcribe_chunks(self, job_id, chunks, cfg_in):
            raise RuntimeError("transcriber boom")

    def bad_factory(name):
        return BadTranscriber()

    monkeypatch.setattr(transcription_mod, "get_transcriber", bad_factory)

    job_id = "job-transcriber-fail"
    res = run_job(job_id, str(src), cfg)
    assert res["state"] == "failed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    tx = manifest.get("transcription", {})
    assert tx.get("state") == "failed"
    assert len(tx.get("errors", [])) >= 1
