import json

from services.config import load_config
from services.minutes.mock import MockMinutesGenerator


def test_mock_minutes_generator_writes_exports(tmp_path):
    job_id = "job-minutes-unit"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)

    full_json = outputs_dir / "full_transcript.json"
    full_txt = outputs_dir / "full_transcript.txt"
    full_txt.write_text("Speaker one discussed project status.", encoding="utf-8")
    full_json.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "chunks": [{"chunk_filename": "chunk-00001.wav"}],
                "segments": [{"text": "Speaker one discussed project status."}],
                "text": "Speaker one discussed project status.",
                "word_count": 5,
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config()
    cfg.minutes_style = "deep_evidence"
    result = MockMinutesGenerator().generate(
        job_id,
        job_dir,
        {
            "full_transcript_json": str(full_json),
            "full_transcript_txt": str(full_txt),
        },
        cfg,
    )

    minutes_md = outputs_dir / "minutes.md"
    action_items_json = outputs_dir / "action_items.json"
    job_summary_json = outputs_dir / "job_summary.json"

    assert result["minutes_md"] == str(minutes_md)
    assert result["action_items_json"] == str(action_items_json)
    assert result["job_summary_json"] == str(job_summary_json)
    minutes_text = minutes_md.read_text(encoding="utf-8")
    assert "Dry-run mock minutes" in minutes_text
    assert "Deep Evidence Minutes" in minutes_text
    assert "A. Meeting Overview" in minutes_text
    action_items = json.loads(action_items_json.read_text(encoding="utf-8"))
    assert action_items["minutes_style"] == "deep_evidence"
    assert action_items["action_items"] == []
    summary = json.loads(job_summary_json.read_text(encoding="utf-8"))
    assert summary["job_id"] == job_id
    assert summary["generator"] == "mock"
    assert summary["minutes_style"] == "deep_evidence"
    assert summary["prompt_preset"]["label"] == "Deep Evidence Minutes"
    assert summary["word_count"] == 5
