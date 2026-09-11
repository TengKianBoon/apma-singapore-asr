from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

from services.integrity import sha256_file


DEFAULT_MP3_BITRATE = "128k"
DEFAULT_MP3_SAMPLE_RATE_HZ = 48_000
DEFAULT_MP3_CHUNK_DURATION_SECONDS = 10 * 60


class AudioProjectError(RuntimeError):
    """Raised when APMA cannot retain or verify a subproject audio artifact."""


def _safe_filename(filename: str, fallback_suffix: str) -> str:
    name = Path(str(filename or "")).name.strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    if not name:
        name = f"original{fallback_suffix or '.audio'}"
    path = Path(name)
    suffix = path.suffix[:16]
    stem = path.stem or "original"
    max_stem_length = max(1, 180 - len(suffix))
    return f"{stem[:max_stem_length]}{suffix}"


def _run_media_command(command: list[str], label: str) -> None:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"{label} failed").strip()
        raise AudioProjectError(f"{label} failed: {detail}")


def _probe_mp3(path: Path, ffprobe_binary: str) -> dict[str, Any]:
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration:stream=codec_name,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffprobe failed").strip()
        raise AudioProjectError(f"MP3 verification failed: {detail}")
    try:
        payload = json.loads(proc.stdout or "{}")
        stream = (payload.get("streams") or [])[0]
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioProjectError("MP3 verification returned incomplete metadata") from exc
    if stream.get("codec_name") != "mp3" or duration <= 0:
        raise AudioProjectError("Generated audio is not a valid positive-duration MP3")
    return {
        "duration_seconds": duration,
        "codec": "mp3",
        "sample_rate_hz": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
    }


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if destination.stat().st_size != source.stat().st_size:
            raise AudioProjectError("Existing subproject original has a different size")
        if sha256_file(destination) != expected_sha256:
            raise AudioProjectError("Existing subproject original has a different SHA-256")
        return
    partial = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.partial"
    )
    try:
        shutil.copy2(source, partial)
        if partial.stat().st_size != source.stat().st_size:
            raise AudioProjectError("Subproject original copy size verification failed")
        if sha256_file(partial) != expected_sha256:
            raise AudioProjectError("Subproject original copy SHA-256 verification failed")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def _convert_to_mp3(source: Path, destination: Path, cfg: Any) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        partial = destination.with_name(
            f".{destination.stem}.{uuid.uuid4().hex}.partial.mp3"
        )
        try:
            _run_media_command(
                [
                    cfg.ffmpeg_binary,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    DEFAULT_MP3_BITRATE,
                    "-ar",
                    str(DEFAULT_MP3_SAMPLE_RATE_HZ),
                    str(partial),
                ],
                "MP3 conversion",
            )
            _probe_mp3(partial, cfg.ffprobe_binary)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    probe = _probe_mp3(destination, cfg.ffprobe_binary)
    return {
        "path": str(destination.resolve()),
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
        "bitrate": DEFAULT_MP3_BITRATE,
        **probe,
    }


def _clock_label(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}h{minutes:02d}m{secs:02d}s"


def _folder_address(runtime_path: Path, cfg: Any) -> str:
    host_storage_path = str(getattr(cfg, "host_storage_path", "") or "").strip()
    if not host_storage_path:
        return str(runtime_path.resolve())
    storage_root = Path(cfg.storage_path).resolve()
    try:
        relative = runtime_path.resolve().relative_to(storage_root)
    except ValueError as exc:
        raise AudioProjectError("Audio subproject path is outside APMA storage") from exc
    if re.match(r"^[A-Za-z]:[\\/]", host_storage_path):
        return str(PureWindowsPath(host_storage_path, *relative.parts))
    return str((Path(host_storage_path).expanduser().resolve() / relative).resolve())


def _create_mp3_chunks(
    mp3_path: Path,
    chunks_dir: Path,
    cfg: Any,
    *,
    chunk_duration_seconds: int,
) -> dict[str, Any]:
    if chunk_duration_seconds <= 0:
        raise ValueError("MP3 chunk duration must be positive")
    staging = chunks_dir.with_name(f".{chunks_dir.name}.{uuid.uuid4().hex}.partial")
    if chunks_dir.exists():
        existing = sorted(chunks_dir.glob("*.mp3"))
        if existing:
            full_probe = _probe_mp3(mp3_path, cfg.ffprobe_binary)
            probes = [_probe_mp3(path, cfg.ffprobe_binary) for path in existing]
            files: list[dict[str, Any]] = []
            elapsed = 0.0
            for path, probe in zip(existing, probes):
                start = elapsed
                end = min(
                    full_probe["duration_seconds"],
                    start + probe["duration_seconds"],
                )
                files.append(
                    {
                        "filename": path.name,
                        "path": str(path.resolve()),
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                        "start_seconds": start,
                        "end_seconds": end,
                        **probe,
                    }
                )
                elapsed += probe["duration_seconds"]
            duration_delta = abs(elapsed - full_probe["duration_seconds"])
            tolerance = max(0.25, len(files) * 0.05)
            if duration_delta > tolerance:
                raise AudioProjectError(
                    "Existing MP3 chunks do not cover the generated MP3 within tolerance"
                )
            return {
                "folder": str(chunks_dir.resolve()),
                "chunk_duration_seconds": chunk_duration_seconds,
                "count": len(existing),
                "aggregate_duration_seconds": elapsed,
                "source_mp3_duration_seconds": full_probe["duration_seconds"],
                "duration_delta_seconds": duration_delta,
                "duration_tolerance_seconds": tolerance,
                "stream_copy": True,
                "files": files,
            }
        raise AudioProjectError("Existing MP3 chunks folder is empty")

    staging.mkdir(parents=True, exist_ok=False)
    try:
        _run_media_command(
            [
                cfg.ffmpeg_binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(mp3_path),
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(chunk_duration_seconds),
                "-reset_timestamps",
                "1",
                str(staging / "segment-%05d.mp3"),
            ],
            "MP3 chunking",
        )
        raw_chunks = sorted(staging.glob("segment-*.mp3"))
        if not raw_chunks:
            raise AudioProjectError("MP3 chunking produced no files")

        full_probe = _probe_mp3(mp3_path, cfg.ffprobe_binary)
        files: list[dict[str, Any]] = []
        elapsed = 0.0
        for index, path in enumerate(raw_chunks, start=1):
            probe = _probe_mp3(path, cfg.ffprobe_binary)
            start = elapsed
            end = min(
                full_probe["duration_seconds"],
                start + probe["duration_seconds"],
            )
            renamed = staging / (
                f"{mp3_path.stem} - part-{index:03d} "
                f"{_clock_label(start)}-{_clock_label(end)}.mp3"
            )
            path.replace(renamed)
            files.append(
                {
                    "filename": renamed.name,
                    "path": str((chunks_dir / renamed.name).resolve()),
                    "sha256": sha256_file(renamed),
                    "size_bytes": renamed.stat().st_size,
                    "start_seconds": start,
                    "end_seconds": end,
                    **probe,
                }
            )
            elapsed += probe["duration_seconds"]

        duration_delta = abs(elapsed - full_probe["duration_seconds"])
        tolerance = max(0.25, len(files) * 0.05)
        if duration_delta > tolerance:
            raise AudioProjectError(
                "MP3 chunk durations do not cover the generated MP3 within tolerance"
            )
        staging.replace(chunks_dir)
        return {
            "folder": str(chunks_dir.resolve()),
            "chunk_duration_seconds": chunk_duration_seconds,
            "count": len(files),
            "aggregate_duration_seconds": elapsed,
            "source_mp3_duration_seconds": full_probe["duration_seconds"],
            "duration_delta_seconds": duration_delta,
            "duration_tolerance_seconds": tolerance,
            "stream_copy": True,
            "files": files,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def create_audio_project(
    source_path: str | Path,
    job_dir: str | Path,
    cfg: Any,
    *,
    original_filename: str | None = None,
    chunk_duration_seconds: int = DEFAULT_MP3_CHUNK_DURATION_SECONDS,
) -> dict[str, Any]:
    """Retain one original, full MP3, and range-labelled MP3 chunk set per job."""

    source = Path(source_path).resolve()
    root = Path(job_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Audio project source not found: {source}")
    if not root.is_dir():
        raise FileNotFoundError(f"Audio project job folder not found: {root}")

    audio_dir = root / "audio"
    original_dir = audio_dir / "original"
    mp3_dir = audio_dir / "mp3"
    chunks_dir = mp3_dir / "chunks"
    safe_name = _safe_filename(
        original_filename or source.name,
        source.suffix.lower(),
    )
    original_path = original_dir / safe_name
    mp3_name = f"{Path(safe_name).stem or 'audio'}.mp3"
    mp3_path = mp3_dir / mp3_name
    source_sha256 = sha256_file(source)

    _copy_verified(source, original_path, source_sha256)
    mp3 = _convert_to_mp3(original_path, mp3_path, cfg)
    chunks = _create_mp3_chunks(
        mp3_path,
        chunks_dir,
        cfg,
        chunk_duration_seconds=chunk_duration_seconds,
    )

    payload = {
        "schema_version": "apma.audio-project.v1",
        "subproject_folder": _folder_address(root, cfg),
        "audio_folder": _folder_address(audio_dir, cfg),
        "original_folder": _folder_address(original_dir, cfg),
        "mp3_folder": _folder_address(mp3_dir, cfg),
        "mp3_chunks_folder": _folder_address(chunks_dir, cfg),
        "runtime_subproject_folder": str(root),
        "original": {
            "filename": original_path.name,
            "path": str(original_path.resolve()),
            "sha256": source_sha256,
            "size_bytes": original_path.stat().st_size,
            "copy_verified": True,
        },
        "mp3": mp3,
        "mp3_chunks": chunks,
    }
    manifest_path = audio_dir / "audio_project.json"
    audio_dir.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    payload["manifest_path"] = str(manifest_path.resolve())
    return payload


def project_paths(audio_project: dict[str, Any] | None) -> dict[str, str]:
    item = audio_project or {}
    return {
        key: str(item.get(key) or "")
        for key in (
            "subproject_folder",
            "original_folder",
            "mp3_folder",
            "mp3_chunks_folder",
        )
        if item.get(key)
    }


__all__ = [
    "AudioProjectError",
    "DEFAULT_MP3_CHUNK_DURATION_SECONDS",
    "create_audio_project",
    "project_paths",
]
