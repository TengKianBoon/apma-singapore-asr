from argparse import Namespace
from pathlib import Path

from scripts import run_live_openai_smoke


def _args(**overrides):
    data = {
        "confirm_live_api": False,
        "input_wav": "sample_audio/live.wav",
        "job_id": "live-smoke-test",
        "storage_path": "jobs",
        "generate_synthetic_wav": False,
        "synthetic_duration_sec": 1.0,
    }
    data.update(overrides)
    return Namespace(**data)


def _live_env(**overrides):
    env = {
        "DRY_RUN": "false",
        "ENABLE_LIVE_OPENAI_TRANSCRIPTION": "true",
        "TRANSCRIPTION_ENGINE": "openai",
        "OPENAI_API_KEY": "fake-key",
    }
    env.update(overrides)
    return env


def test_live_smoke_refuses_when_dry_run_true():
    errors = run_live_openai_smoke.validate_live_smoke_gates(
        _args(confirm_live_api=True),
        _live_env(DRY_RUN="true"),
    )
    assert "DRY_RUN must be false" in errors


def test_live_smoke_refuses_when_enable_flag_missing():
    errors = run_live_openai_smoke.validate_live_smoke_gates(
        _args(confirm_live_api=True),
        _live_env(ENABLE_LIVE_OPENAI_TRANSCRIPTION="false"),
    )
    assert "ENABLE_LIVE_OPENAI_TRANSCRIPTION must be true" in errors


def test_live_smoke_refuses_when_api_key_missing():
    env = _live_env()
    env.pop("OPENAI_API_KEY")
    errors = run_live_openai_smoke.validate_live_smoke_gates(_args(confirm_live_api=True), env)
    assert "OPENAI_API_KEY must be set in the environment" in errors


def test_live_smoke_refuses_without_confirm_flag():
    errors = run_live_openai_smoke.validate_live_smoke_gates(_args(confirm_live_api=False), _live_env())
    assert "Missing --confirm-live-api" in errors


def test_live_smoke_refuses_when_cost_exceeds_cap(tmp_path, monkeypatch):
    input_wav = tmp_path / "live.wav"
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("MAX_COST_PER_JOB_USD", "0.000001")

    rc = run_live_openai_smoke.main(
        [
            "--input-wav",
            str(input_wav),
            "--generate-synthetic-wav",
            "--synthetic-duration-sec",
            "60",
            "--confirm-live-api",
        ]
    )

    assert rc == 2


def test_live_smoke_refuses_when_file_exceeds_size_limit(tmp_path, monkeypatch):
    input_wav = tmp_path / "live.wav"
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_FILE_SIZE_LIMIT_BYTES", "1")

    rc = run_live_openai_smoke.main(
        [
            "--input-wav",
            str(input_wav),
            "--generate-synthetic-wav",
            "--confirm-live-api",
        ]
    )

    assert rc == 2


def test_live_smoke_success_path_uses_injected_runner_not_network(tmp_path, monkeypatch):
    input_wav = tmp_path / "live.wav"
    storage = tmp_path / "jobs"
    called = {}

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("ENABLE_LIVE_OPENAI_TRANSCRIPTION", "true")
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("MAX_COST_PER_JOB_USD", "0.05")

    def fake_run_job(job_id, source_path, cfg):
        called["job_id"] = job_id
        called["source_path"] = source_path
        called["dry_run"] = cfg.dry_run
        called["engine"] = cfg.transcription_engine
        called["live_enabled"] = cfg.enable_live_openai_transcription
        called["provider_timestamps"] = cfg.enable_provider_timestamps
        return {"state": "completed"}

    monkeypatch.setattr(run_live_openai_smoke.runner, "run_job", fake_run_job)

    rc = run_live_openai_smoke.main(
        [
            "--input-wav",
            str(input_wav),
            "--storage-path",
            str(storage),
            "--job-id",
            "live-smoke-test",
            "--generate-synthetic-wav",
            "--confirm-live-api",
            "--provider-timestamps",
        ]
    )

    assert rc == 0
    assert Path(called["source_path"]).exists()
    assert called["job_id"] == "live-smoke-test"
    assert called["dry_run"] is False
    assert called["engine"] == "openai"
    assert called["live_enabled"] is True
    assert called["provider_timestamps"] is True
