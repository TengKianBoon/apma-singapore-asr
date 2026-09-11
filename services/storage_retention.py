from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from services.integrity import sha256_file


SOURCE_STORE_DIR = "_source_store"
CANONICAL_STORE_DIR = "_audio_cache"
UPLOAD_REFERENCE_FILENAME = "upload.json"


class StorageIntegrityError(RuntimeError):
    """Raised when a shared retained audio object does not match its identity."""


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 16 or not suffix[1:].isalnum():
        return ".audio"
    return suffix


def shared_source_path(storage_root: Path, sha256: str, filename: str) -> Path:
    return Path(storage_root) / SOURCE_STORE_DIR / sha256 / f"original{_safe_suffix(filename)}"


def shared_canonical_path(storage_root: Path, source_sha256: str) -> Path:
    return Path(storage_root) / CANONICAL_STORE_DIR / source_sha256 / "canonical.flac"


def _verify_shared_file(path: Path, expected_size: int, expected_sha256: str) -> None:
    if not path.is_file():
        raise StorageIntegrityError(f"Retained audio object is missing: {path.name}")
    if path.stat().st_size != expected_size:
        raise StorageIntegrityError(f"Retained audio size verification failed: {path.name}")
    if sha256_file(path) != expected_sha256:
        raise StorageIntegrityError(f"Retained audio SHA-256 verification failed: {path.name}")


def retain_source_file(source: Path, storage_root: Path) -> tuple[Path, str, bool]:
    """Retain one byte-identical source object per SHA-256 and extension."""

    source = Path(source)
    source_sha256 = sha256_file(source)
    source_size = source.stat().st_size
    target = shared_source_path(storage_root, source_sha256, source.name)
    if source.resolve() == target.resolve():
        _verify_shared_file(target, source_size, source_sha256)
        return target, source_sha256, True
    if target.exists():
        _verify_shared_file(target, source_size, source_sha256)
        return target, source_sha256, True

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
    try:
        shutil.copy2(source, partial)
        _verify_shared_file(partial, source_size, source_sha256)
        if target.exists():
            _verify_shared_file(target, source_size, source_sha256)
            partial.unlink(missing_ok=True)
            return target, source_sha256, True
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target, source_sha256, False


def retain_source_bytes(
    content: bytes,
    original_filename: str,
    storage_root: Path,
) -> tuple[Path, str, bool]:
    """Retain upload bytes once without creating a second per-upload audio file."""

    source_sha256 = hashlib.sha256(content).hexdigest()
    target = shared_source_path(storage_root, source_sha256, original_filename)
    if target.exists():
        _verify_shared_file(target, len(content), source_sha256)
        return target, source_sha256, True

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_bytes(content)
        _verify_shared_file(partial, len(content), source_sha256)
        if target.exists():
            _verify_shared_file(target, len(content), source_sha256)
            partial.unlink(missing_ok=True)
            return target, source_sha256, True
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target, source_sha256, False


def write_upload_reference(
    upload_dir: Path,
    *,
    source_path: Path,
    source_sha256: str,
    original_filename: str,
    size_bytes: int,
) -> Path:
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    reference = upload_dir / UPLOAD_REFERENCE_FILENAME
    payload = {
        "schema_version": "apma.upload-reference.v1",
        "original_filename": original_filename,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "size_bytes": int(size_bytes),
        "storage_policy": "content_addressed_shared_no_overwrite",
    }
    tmp = reference.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(reference)
    return reference


def read_upload_reference(upload_dir: Path, storage_root: Path) -> dict[str, Any] | None:
    reference = Path(upload_dir) / UPLOAD_REFERENCE_FILENAME
    if not reference.is_file():
        return None
    payload = json.loads(reference.read_text(encoding="utf-8"))
    source_path = Path(str(payload.get("source_path", ""))).resolve()
    allowed_root = (Path(storage_root) / SOURCE_STORE_DIR).resolve()
    if source_path == allowed_root or allowed_root not in source_path.parents:
        raise StorageIntegrityError("Upload reference points outside APMA shared source storage")
    _verify_shared_file(
        source_path,
        int(payload.get("size_bytes", -1)),
        str(payload.get("source_sha256", "")),
    )
    payload["source_path"] = str(source_path)
    return payload


def cleanup_completed_working_audio(job_dir: Path) -> dict[str, Any]:
    """Remove reproducible working audio only after a job has completed."""

    job_root = Path(job_dir).resolve()
    removed: list[str] = []
    bytes_removed = 0
    for name in ("chunks", "preprocess"):
        target = (job_root / name).resolve()
        if target.parent != job_root:
            raise ValueError("Working-audio cleanup target escaped the job directory")
        if not target.exists():
            continue
        bytes_removed += sum(
            item.stat().st_size
            for item in target.rglob("*")
            if item.is_file()
        )
        shutil.rmtree(target)
        removed.append(name)
    # Keep the documented job layout while removing the large reproducible files.
    (job_root / "chunks").mkdir(exist_ok=True)
    return {
        "policy": "completed_job_remove_reproducible_working_audio",
        "source_retained": True,
        "provider_and_final_artifacts_retained": True,
        "removed_directories": removed,
        "empty_chunks_directory_retained": True,
        "bytes_removed": int(bytes_removed),
    }


def remove_upload_reference(upload_dir: Path, storage_root: Path) -> bool:
    """Remove a small upload pointer after its job has safely retained the source."""

    upload_root = (Path(storage_root) / "dashboard_uploads").resolve()
    target = Path(upload_dir).resolve()
    if target.parent != upload_root:
        raise ValueError("Upload cleanup target escaped dashboard_uploads")
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True
