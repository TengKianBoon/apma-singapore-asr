import json

from services import cli
from tests.helpers import generate_sine_wav


def test_cli_ingest_dry_run_writes_expected_outputs(tmp_path, monkeypatch, capsys):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)
    storage = tmp_path / "jobs"
    job_id = "cli-dry-run-demo"

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("TRANSCRIPTION_ENGINE", "mock")
    monkeypatch.setenv("STORAGE_PATH", str(storage))

    rc = cli.main(["--ingest", str(src), "--job-id", job_id])

    assert rc == 0
    out = capsys.readouterr().out
    assert f"Job {job_id} finished with state=completed" in out

    job_dir = storage / job_id
    expected_paths = [
        job_dir / "chunks",
        job_dir / "transcripts",
        job_dir / "outputs" / "full_transcript.json",
        job_dir / "outputs" / "full_transcript.txt",
        job_dir / "outputs" / "minutes.md",
        job_dir / "outputs" / "action_items.json",
        job_dir / "outputs" / "job_summary.json",
        job_dir / "job_manifest.json",
    ]
    for path in expected_paths:
        assert path.exists()

    manifest = json.loads((job_dir / "job_manifest.json").read_text(encoding="utf-8"))
    outputs = manifest["outputs"]
    assert outputs["full_transcript_json"] == str(job_dir / "outputs" / "full_transcript.json")
    assert outputs["full_transcript_txt"] == str(job_dir / "outputs" / "full_transcript.txt")
    assert outputs["minutes_md"] == str(job_dir / "outputs" / "minutes.md")
    assert outputs["action_items_json"] == str(job_dir / "outputs" / "action_items.json")
    assert outputs["job_summary_json"] == str(job_dir / "outputs" / "job_summary.json")


def test_cli_runs_and_reports_dry_run(capsys):
    rc = cli.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "APMA V5 dry-run verification" in out
    assert "DRY_RUN=True" in out or "DRY_RUN=true" in out
