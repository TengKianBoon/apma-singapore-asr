import pytest
from pathlib import Path

from services import runner, job as job_mod, ingest as ingest_mod
from services.config import load_config
from services.errors import JobExistsError
from tests.helpers import generate_sine_wav


def test_runner_success(tmp_path):
    src = tmp_path / "in.wav"
    generate_sine_wav(str(src), duration_sec=3.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.max_retries = 2

    job_id = "job-run-success"
    res = runner.run_job(job_id, str(src), cfg)
    assert res["state"] == "completed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    assert manifest["state"] == "completed"
    assert isinstance(manifest.get("chunks"), list)
    assert manifest["source"]["integrity"]["verified"] is True
    assert manifest["ingest_qc"]["state"] == "accepted"
    assert manifest["ingest_qc"]["integrity_verified"] is True
    assert manifest["ingest_qc"]["speech_vad_performed"] is False
    assert manifest["storage_retention"]["bytes_removed"] > 0
    assert manifest["storage_retention"]["source_retained"] is True
    chunks_dir = Path(cfg.storage_path) / job_id / "chunks"
    assert chunks_dir.is_dir()
    assert not list(chunks_dir.iterdir())
    assert not (Path(cfg.storage_path) / job_id / "preprocess").exists()
    assert Path(manifest["source"]["path"]).is_file()
    audio_project = manifest["audio_project"]
    assert Path(audio_project["original"]["path"]).is_file()
    assert Path(audio_project["mp3"]["path"]).is_file()
    assert Path(audio_project["mp3_chunks_folder"]).is_dir()
    assert audio_project["mp3_chunks"]["count"] >= 1
    assert Path(manifest["outputs"]["full_transcript_json"]).is_file()


def test_runner_idempotency(tmp_path):
    storage = tmp_path / "jobs"
    job_mod.create_job("job-exists", storage_path=str(storage))
    cfg = load_config()
    cfg.storage_path = str(storage)

    with pytest.raises(JobExistsError):
        runner.run_job("job-exists", "doesnotmatter.wav", cfg)


def test_step_failure_sets_failed(tmp_path, monkeypatch):
    src = tmp_path / "bad.wav"
    generate_sine_wav(str(src), duration_sec=1.0)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")

    # monkeypatch ingest to raise ValueError
    def bad_ingest(path, job_dir):
        raise ValueError("corrupt file")

    monkeypatch.setattr(ingest_mod, "ingest_file", bad_ingest)

    res = runner.run_job("job-fail", str(src), cfg)
    assert res["state"] == "failed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / "job-fail")
    assert manifest["state"] == "failed"
    assert len(manifest.get("errors", [])) >= 1


def test_retry_on_transient_io(tmp_path, monkeypatch):
    src = tmp_path / "in2.wav"
    generate_sine_wav(str(src), duration_sec=2.0)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.max_retries = 3

    orig = ingest_mod.ingest_file
    calls = {"n": 0}

    def flaky_ingest(path, job_dir):
        if calls["n"] == 0:
            calls["n"] += 1
            raise OSError("transient I/O")
        return orig(path, job_dir)

    monkeypatch.setattr(ingest_mod, "ingest_file", flaky_ingest)
    # avoid real sleeping
    monkeypatch.setattr('services.runner.time.sleep', lambda s: None)

    res = runner.run_job("job-retry", str(src), cfg)
    assert res["state"] == "completed"


def test_cost_preflight_blocks(tmp_path, monkeypatch):
    src = tmp_path / "in3.wav"
    generate_sine_wav(str(src), duration_sec=1.0)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.max_cost_per_job_usd = 0.01

    # Monkeypatch estimate_job_cost to return huge cost
    monkeypatch.setattr(runner, "estimate_job_cost", lambda source_meta, cfg: 1000.0)

    res = runner.run_job("job-cost", str(src), cfg)
    assert res["state"] == "failed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / "job-cost")
    assert manifest["state"] == "failed"
    assert any(e["type"] == "CostLimitExceededError" for e in manifest.get("errors", []))


def test_runner_reverifies_stored_source_before_preprocess(tmp_path, monkeypatch):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0)
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")

    def tamper_after_ingest(source_meta, cfg_in):
        Path(source_meta["path"]).write_bytes(b"tampered after ingest")
        return 0.0

    monkeypatch.setattr(runner, "estimate_job_cost", tamper_after_ingest)

    result = runner.run_job("job-integrity-fail", str(src), cfg)

    assert result["state"] == "failed"
    manifest = job_mod.read_manifest(Path(cfg.storage_path) / "job-integrity-fail")
    assert manifest["ingest_qc"]["state"] == "rejected"
    assert manifest["ingest_qc"]["failure_code"] == "SourceIntegrityError"
    assert any(
        error["step"] == "preprocess" and error["type"] == "SourceIntegrityError"
        for error in manifest["errors"]
    )
