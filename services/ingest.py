from pathlib import Path
import json

from services.audio_formats import SUPPORTED_INPUT_EXTENSIONS
from services.integrity import sha256_file
from services.storage_retention import retain_source_file


class SourceIntegrityError(RuntimeError):
    """Raised when the stored source is not byte-identical to the input."""


def ingest_file(src_path: str, job_dir: Path) -> dict:
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")
    if src.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise ValueError(f"Unsupported audio format: {src.suffix.lower()}. Supported formats: {supported}")

    dest_dir = Path(job_dir) / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    reference_path = dest_dir / "source_reference.json"
    if reference_path.exists():
        raise FileExistsError("Stored source already exists: source_reference.json")

    source_size_before = src.stat().st_size
    source_sha256_before = sha256_file(src)
    dest, retained_sha256, cache_reused = retain_source_file(src, Path(job_dir).parent)
    source_size_after = src.stat().st_size
    source_sha256_after = sha256_file(src)
    stored_size = dest.stat().st_size
    stored_sha256 = sha256_file(dest)

    verified = (
        source_size_before == source_size_after == stored_size
        and source_sha256_before == source_sha256_after == stored_sha256
    )
    if not verified:
        raise SourceIntegrityError("Source changed during ingest or stored copy verification failed")

    reference_payload = {
        "schema_version": "apma.source-reference.v1",
        "original_filename": src.name,
        "retained_path": str(dest),
        "sha256": retained_sha256,
        "size_bytes": stored_size,
        "cache_reused": bool(cache_reused),
    }
    tmp_reference = reference_path.with_suffix(".json.tmp")
    tmp_reference.write_text(
        json.dumps(reference_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_reference.replace(reference_path)

    return {
        "filename": src.name,
        "path": str(dest),
        "size_bytes": stored_size,
        "extension": src.suffix.lower(),
        "sha256": stored_sha256,
        "hash_algorithm": "sha256",
        "storage_policy": "content_addressed_shared_no_overwrite",
        "source_reference_path": str(reference_path),
        "shared_source_reused": bool(cache_reused),
        "copy_verified": True,
        "integrity": {
            "algorithm": "sha256",
            "source_size_bytes": source_size_after,
            "stored_size_bytes": stored_size,
            "source_sha256": source_sha256_after,
            "stored_sha256": stored_sha256,
            "verified": True,
        },
    }


def verify_ingested_source(source_meta: dict) -> None:
    """Reverify a stored source before downstream processing begins."""

    path = Path(str(source_meta.get("path", "")))
    expected_size = source_meta.get("size_bytes")
    expected_sha256 = source_meta.get("sha256")
    if not path.is_file() or expected_size is None or not expected_sha256:
        raise SourceIntegrityError("Stored source metadata is incomplete")
    if path.stat().st_size != int(expected_size):
        raise SourceIntegrityError("Stored source size verification failed")
    if sha256_file(path) != str(expected_sha256):
        raise SourceIntegrityError("Stored source SHA-256 verification failed")
