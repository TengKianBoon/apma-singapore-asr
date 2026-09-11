import json
from pathlib import Path

from services.config import load_config
from services import job as job_mod
from services.runner import run_job
from tests.helpers import generate_sine_wav


def test_runner_produces_full_transcript_and_mock_minutes_outputs(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=2.0, framerate=8000)

    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.transcription_engine = "mock"
    cfg.dry_run = True

    job_id = "job-end-to-end-minutes"
    result = run_job(job_id, str(src), cfg)
    assert result["state"] == "completed"

    manifest = job_mod.read_manifest(Path(cfg.storage_path) / job_id)
    outputs = manifest.get("outputs", {})
    required_outputs = [
        "full_transcript_json",
        "full_transcript_txt",
        "full_transcript_html",
        "full_transcript_srt",
        "full_transcript_vtt",
        "minutes_md",
        "action_items_json",
        "job_summary_json",
    ]

    assert manifest["state"] == "completed"
    assert outputs.get("state") == "completed"
    summary = manifest.get("summary", {})
    assert summary.get("job_state") == "completed"
    assert summary.get("chunk_count") == len(manifest.get("chunks", []))
    assert summary.get("transcript_count") == len(manifest.get("transcription", {}).get("transcripts", []))
    assert summary.get("estimated_cost_usd") == 0.0
    assert summary.get("actual_cost_usd") == 0.0
    for key in required_outputs:
        assert key in outputs
        assert Path(outputs[key]).exists()
        assert summary.get("outputs", {}).get(key) == outputs[key]

    full_transcript = json.loads(Path(outputs["full_transcript_json"]).read_text(encoding="utf-8"))
    action_items = json.loads(Path(outputs["action_items_json"]).read_text(encoding="utf-8"))
    job_summary = json.loads(Path(outputs["job_summary_json"]).read_text(encoding="utf-8"))
    minutes_text = Path(outputs["minutes_md"]).read_text(encoding="utf-8")

    assert "Mock transcript for" in full_transcript["text"]
    assert "Dry-run mock minutes" in minutes_text
    assert action_items["action_items"] == []
    assert job_summary["outputs"]["full_transcript_json"] == outputs["full_transcript_json"]
    assert job_summary["outputs"]["minutes_md"] == outputs["minutes_md"]
