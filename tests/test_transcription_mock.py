import json
import math
from pathlib import Path

import pytest

from services.transcription import get_transcriber
from services.config import load_config


def test_get_transcriber_returns_mock():
    t1 = get_transcriber(None)
    t2 = get_transcriber("mock")
    assert t1.__class__.__name__ == "MockTranscriber"
    assert t2.__class__.__name__ == "MockTranscriber"


def test_transcribe_chunk_writes_json_and_content(tmp_path):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = True

    t = get_transcriber("mock")
    job_id = "job-a"
    chunk_meta = {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 7.0}

    res = t.transcribe_chunk(job_id, chunk_meta, cfg)

    transcripts_dir = Path(cfg.storage_path) / job_id / "transcripts"
    assert transcripts_dir.exists()

    out_file = transcripts_dir / "chunk-00001.json"
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["job_id"] == job_id
    assert data["chunk_filename"] == "chunk-00001.wav"
    assert math.isclose(data["duration_seconds"], 7.0)
    assert isinstance(data["segments"], list) and len(data["segments"]) == 2

    # deterministic segment text
    first_seg = data["segments"][0]
    assert first_seg["text"] == "Mock transcript for chunk-00001.wav segment 1 (0.00-5.00s)."

    # word/char counts consistent
    wc = sum(len(s["text"].split()) for s in data["segments"])
    cc = sum(len(s["text"]) for s in data["segments"])
    assert data["word_count"] == wc
    assert data["character_count"] == cc


def test_transcribe_chunks_writes_all(tmp_path):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = True

    t = get_transcriber()
    job_id = "job-b"
    chunks = [
        {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 3.0},
        {"filename": "chunk-00002.wav", "start_sec": 3.0, "end_sec": 9.0},
    ]

    results = t.transcribe_chunks(job_id, chunks, cfg)
    assert len(results) == 2

    transcripts_dir = Path(cfg.storage_path) / job_id / "transcripts"
    assert (transcripts_dir / "chunk-00001.json").exists()
    assert (transcripts_dir / "chunk-00002.json").exists()


def test_dry_run_enforced(tmp_path):
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.dry_run = False

    t = get_transcriber()
    with pytest.raises(RuntimeError):
        t.transcribe_chunk("job-c", {"filename": "chunk-00001.wav", "start_sec": 0.0, "end_sec": 1.0}, cfg)
