from pathlib import Path

import pytest

from services import job


@pytest.mark.parametrize("job_id", ["../escape", "..\\escape", "/absolute", "bad id", ""])
def test_job_id_rejects_unsafe_paths(job_id):
    with pytest.raises(ValueError):
        job.validate_job_id(job_id)


def test_create_job_stays_under_storage_root(tmp_path):
    storage = tmp_path / "jobs"

    job_dir = job.create_job("safe-job_01", storage_path=str(storage))

    assert job_dir.resolve().parent == storage.resolve()
    assert (job_dir / "providers").is_dir()
    assert (job_dir / "transcripts").is_dir()
    assert Path(job_dir / "job_manifest.json").is_file()
