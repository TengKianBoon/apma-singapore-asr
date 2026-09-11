import json
from pathlib import Path

from scripts import run_mvp_dry_run


def test_mvp_dry_run_script_creates_release_outputs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    job_id = "release-test"

    rc = run_mvp_dry_run.main(["--job-id", job_id, "--duration-sec", "1.0"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Release 0.1 dry-run outputs:" in out

    job_dir = tmp_path / "jobs" / job_id
    expected_paths = [
        job_dir / "chunks",
        job_dir / "audio" / "original" / "synthetic_mvp_demo.wav",
        job_dir / "audio" / "mp3" / "synthetic_mvp_demo.mp3",
        job_dir / "audio" / "mp3" / "chunks",
        job_dir / "audio" / "audio_project.json",
        job_dir / "transcripts",
        job_dir / "outputs" / "full_transcript.json",
        job_dir / "outputs" / "full_transcript.txt",
        job_dir / "outputs" / "full_transcript.html",
        job_dir / "outputs" / "full_transcript.srt",
        job_dir / "outputs" / "full_transcript.vtt",
        job_dir / "outputs" / "minutes.md",
        job_dir / "outputs" / "action_items.json",
        job_dir / "outputs" / "job_summary.json",
        job_dir / "job_manifest.json",
    ]
    for path in expected_paths:
        assert path.exists()

    manifest = json.loads((job_dir / "job_manifest.json").read_text(encoding="utf-8"))
    summary = manifest["summary"]
    assert manifest["state"] == "completed"
    assert summary["job_state"] == "completed"
    assert summary["chunk_count"] >= 1
    assert len(summary["transcript_paths"]) >= 1
    assert Path(summary["outputs"]["minutes_md"]).resolve() == (job_dir / "outputs" / "minutes.md").resolve()
    assert Path(summary["outputs"]["action_items_json"]).resolve() == (job_dir / "outputs" / "action_items.json").resolve()
    assert Path(summary["outputs"]["job_summary_json"]).resolve() == (job_dir / "outputs" / "job_summary.json").resolve()
    assert summary["estimated_cost_usd"] == 0.0
    assert summary["actual_cost_usd"] == 0.0
    assert summary["errors"] == []
    assert summary["audio_project"]["subproject_folder"] == str(job_dir.resolve())
