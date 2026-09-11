from pathlib import Path
import json
import datetime
import re


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_job_id(job_id: str) -> str:
    """Reject path traversal and filesystem-unsafe job identifiers."""

    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError(
            "job_id must be 1-128 characters using only letters, numbers, '.', '_', or '-'"
        )
    if job_id in {".", ".."}:
        raise ValueError("job_id cannot be '.' or '..'")
    return job_id


def create_job(job_id: str, storage_path: str = "./jobs") -> Path:
    validate_job_id(job_id)
    base = Path(storage_path)
    job_dir = base / job_id
    (job_dir / "source").mkdir(parents=True, exist_ok=True)
    (job_dir / "chunks").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "logs").mkdir(parents=True, exist_ok=True)
    (job_dir / "providers").mkdir(parents=True, exist_ok=True)
    (job_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": "apma.job-manifest.v1",
        "job_id": job_id,
        "state": "queued",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "source": {},
        "ingest_qc": {},
        "preprocess": {},
        "audio_project": {},
        "chunking": {},
        "chunks": [],
        "outputs": {},
        "warnings": [],
        "errors": [],
        "attempts": {},
        "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
    }
    write_manifest(job_dir, manifest)
    return job_dir


def write_manifest(job_dir: Path, manifest: dict) -> None:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp = job_dir / "job_manifest.json.tmp"
    final = job_dir / "job_manifest.json"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    tmp.replace(final)


def read_manifest(job_dir: Path) -> dict:
    final = Path(job_dir) / "job_manifest.json"
    if not final.exists():
        return {}
    with final.open("r", encoding="utf-8") as f:
        return json.load(f)
