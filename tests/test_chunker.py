from pathlib import Path
from services import job as job_mod
from services import ingest, preprocess, chunker
from services.config import Config
from tests.helpers import generate_sine_wav


def test_chunker_writes_chunks(tmp_path):
    src = tmp_path / "c.wav"
    # 5 seconds at 8kHz
    generate_sine_wav(str(src), duration_sec=5.0, framerate=8000)

    storage = tmp_path / "jobs"
    job_dir = job_mod.create_job("job-chunk-1", storage_path=str(storage))
    meta = ingest.ingest_file(str(src), job_dir)
    pre = preprocess.preprocess_wav(meta["path"])

    cfg = Config()
    # use small chunk duration for the test
    cfg.default_chunk_duration_sec = 2
    cfg.min_chunk_duration_sec = 1
    cfg.max_chunk_duration_sec = 10
    cfg.overlap_seconds = 1
    cfg.smart_chunking_enabled = False
    chunks = chunker.chunk_file(meta["path"], job_dir, pre, cfg)

    assert len(chunks) >= 2
    starts = [c["start_frame"] for c in chunks]
    # start frames must be strictly increasing
    assert all(earlier < later for earlier, later in zip(starts, starts[1:]))

    # verify chunk files exist
    for c in chunks:
        path = job_dir / "chunks" / c["filename"]
        assert path.exists()
        assert c["actual_bytes"] == path.stat().st_size


def test_chunker_does_not_create_overlap_only_tail_chunk(tmp_path):
    src = tmp_path / "tail.wav"
    generate_sine_wav(str(src), duration_sec=25.0, framerate=8000)

    storage = tmp_path / "jobs"
    job_dir = job_mod.create_job("job-tail", storage_path=str(storage))
    meta = ingest.ingest_file(str(src), job_dir)
    pre = preprocess.preprocess_wav(meta["path"])

    cfg = Config()
    cfg.default_chunk_duration_sec = 10
    cfg.min_chunk_duration_sec = 1
    cfg.max_chunk_duration_sec = 10
    cfg.overlap_seconds = 4
    cfg.smart_chunking_enabled = False

    chunks = chunker.chunk_file(meta["path"], job_dir, pre, cfg)

    assert [(round(c["start_sec"]), round(c["end_sec"])) for c in chunks] == [
        (0, 10),
        (6, 16),
        (12, 22),
        (18, 25),
    ]


def test_chunker_prefers_nearby_silence_and_preserves_global_metadata(tmp_path, monkeypatch):
    src = tmp_path / "silence-boundaries.wav"
    generate_sine_wav(str(src), duration_sec=26.0, framerate=8000)
    job_dir = job_mod.create_job("job-smart", storage_path=str(tmp_path / "jobs"))
    meta = ingest.ingest_file(str(src), job_dir)
    pre = preprocess.preprocess_wav(meta["path"])

    cfg = Config()
    cfg.default_chunk_duration_sec = 10
    cfg.min_chunk_duration_sec = 5
    cfg.max_chunk_duration_sec = 12
    cfg.overlap_seconds = 2
    cfg.chunk_boundary_search_window_sec = 3
    cfg.target_max_chunk_bytes = 10 * 1024 * 1024
    monkeypatch.setattr(
        chunker,
        "_detect_silences",
        lambda *_args, **_kwargs: [(8.8, 9.2), (16.8, 17.0)],
    )

    chunks = chunker.chunk_file(meta["path"], job_dir, pre, cfg)

    assert len(chunks) == 3
    assert [chunk["boundary"]["strategy"] for chunk in chunks] == [
        "silence",
        "silence",
        "source_end",
    ]
    assert chunks[0]["end_sec"] == 9.2
    assert chunks[1]["end_sec"] == 17.0
    assert all(5.0 <= chunk["end_sec"] - chunk["start_sec"] <= 12.0 for chunk in chunks)
    assert [chunk["chunk_index"] for chunk in chunks] == [1, 2, 3]
    assert all(chunk["global_start_sec"] == chunk["start_sec"] for chunk in chunks)
    assert all(chunk["global_end_sec"] == chunk["end_sec"] for chunk in chunks)
    assert [chunk["overlap_before_sec"] for chunk in chunks] == [0.0, 2.0, 2.0]
    assert [chunk["overlap_after_sec"] for chunk in chunks] == [2.0, 2.0, 0.0]
    assert all(0.0 <= chunk["start_sec"] < chunk["end_sec"] <= 26.0 for chunk in chunks)
    assert chunks[0]["source_identity"]["processing_audio_sha256"] == pre[
        "processing_audio_sha256"
    ]


def test_chunker_falls_back_to_bounded_time_cut_when_no_silence(tmp_path, monkeypatch):
    src = tmp_path / "no-silence.wav"
    generate_sine_wav(str(src), duration_sec=25.0, framerate=8000)
    job_dir = job_mod.create_job("job-fallback", storage_path=str(tmp_path / "jobs"))
    meta = ingest.ingest_file(str(src), job_dir)
    pre = preprocess.preprocess_wav(meta["path"])

    cfg = Config()
    cfg.default_chunk_duration_sec = 10
    cfg.min_chunk_duration_sec = 5
    cfg.max_chunk_duration_sec = 10
    cfg.overlap_seconds = 2
    cfg.chunk_boundary_search_window_sec = 3
    cfg.target_max_chunk_bytes = 10 * 1024 * 1024
    monkeypatch.setattr(chunker, "_detect_silences", lambda *_args, **_kwargs: [])

    chunks = chunker.chunk_file(meta["path"], job_dir, pre, cfg)

    assert [chunk["boundary"]["strategy"] for chunk in chunks] == [
        "time_fallback",
        "time_fallback",
        "source_end",
    ]
    assert [(chunk["start_sec"], chunk["end_sec"]) for chunk in chunks] == [
        (0.0, 10.0),
        (8.0, 18.0),
        (16.0, 25.0),
    ]


def test_short_file_remains_one_chunk_without_silence_scan(tmp_path, monkeypatch):
    src = tmp_path / "short.wav"
    generate_sine_wav(str(src), duration_sec=4.0, framerate=8000)
    job_dir = job_mod.create_job("job-short", storage_path=str(tmp_path / "jobs"))
    meta = ingest.ingest_file(str(src), job_dir)
    pre = preprocess.preprocess_wav(meta["path"])
    cfg = Config()
    cfg.default_chunk_duration_sec = 10
    cfg.min_chunk_duration_sec = 5
    cfg.max_chunk_duration_sec = 12

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("short files must not invoke silence detection")

    monkeypatch.setattr(chunker, "_detect_silences", fail_if_called)
    chunks = chunker.chunk_file(meta["path"], job_dir, pre, cfg)

    assert len(chunks) == 1
    assert chunks[0]["start_sec"] == 0.0
    assert chunks[0]["end_sec"] == 4.0
    assert chunks[0]["boundary"]["strategy"] == "source_end"
