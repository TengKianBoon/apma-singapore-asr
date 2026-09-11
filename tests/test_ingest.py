import tempfile
import hashlib
from pathlib import Path

import pytest

from services import job
from services import ingest
from tests.helpers import generate_sine_wav


def test_ingest_copies_wav(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0)

    storage = tmp_path / "jobs"
    job_dir = job.create_job("job-ingest-1", storage_path=str(storage))

    meta = ingest.ingest_file(str(src), job_dir)
    dest = Path(meta["path"])
    assert dest.exists()
    assert dest.stat().st_size == meta["size_bytes"]
    assert meta["hash_algorithm"] == "sha256"
    assert meta["sha256"] == hashlib.sha256(dest.read_bytes()).hexdigest()
    assert meta["storage_policy"] == "content_addressed_shared_no_overwrite"
    assert meta["copy_verified"] is True
    assert meta["integrity"]["verified"] is True
    assert meta["integrity"]["source_sha256"] == meta["integrity"]["stored_sha256"]
    # ensure manifest exists
    manifest = job.read_manifest(job_dir)
    assert manifest["job_id"] == "job-ingest-1"


def test_ingest_reuses_one_shared_source_across_jobs(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0)
    storage = tmp_path / "jobs"
    first_job = job.create_job("job-source-first", storage_path=str(storage))
    second_job = job.create_job("job-source-second", storage_path=str(storage))

    first = ingest.ingest_file(str(src), first_job)
    second = ingest.ingest_file(str(src), second_job)

    assert first["path"] == second["path"]
    assert first["shared_source_reused"] is False
    assert second["shared_source_reused"] is True
    assert len(list((storage / "_source_store").rglob("original.wav"))) == 1
    assert not list((first_job / "source").glob("*.wav"))
    assert not list((second_job / "source").glob("*.wav"))


def test_ingest_accepts_common_supported_audio_formats(tmp_path):
    src = tmp_path / "meeting.mp3"
    src.write_bytes(b"fake mp3 bytes")

    storage = tmp_path / "jobs"
    job_dir = job.create_job("job-ingest-mp3", storage_path=str(storage))

    meta = ingest.ingest_file(str(src), job_dir)

    assert meta["extension"] == ".mp3"
    assert Path(meta["path"]).exists()


def test_ingest_refuses_to_overwrite_stored_source(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0)
    job_dir = job.create_job("job-ingest-no-overwrite", storage_path=str(tmp_path / "jobs"))

    ingest.ingest_file(str(src), job_dir)

    with pytest.raises(FileExistsError, match="Stored source already exists"):
        ingest.ingest_file(str(src), job_dir)


def test_verify_ingested_source_detects_tampering(tmp_path):
    src = tmp_path / "input.wav"
    generate_sine_wav(str(src), duration_sec=1.0)
    job_dir = job.create_job("job-ingest-tamper", storage_path=str(tmp_path / "jobs"))
    meta = ingest.ingest_file(str(src), job_dir)

    Path(meta["path"]).write_bytes(b"tampered")

    with pytest.raises(ingest.SourceIntegrityError, match="verification failed"):
        ingest.verify_ingested_source(meta)
