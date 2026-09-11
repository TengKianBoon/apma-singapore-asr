"""Durable meeting archive naming and derived FINAL exports."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from services import job as job_mod
from services.transcript_exports import (
    render_html,
    render_srt,
    render_vtt,
    write_docx,
    write_pdf,
)


PROVIDER_CODES = {
    "M3ASR",
    "gptTr",
    "gpt4oMini",
    "gpt4oTr",
    "gpt4oDiarz",
    "Gem37F",
    "Gem35T",
    "QwenA3FT",
}
DATETIME_SOURCES = {"user", "source_metadata", "filename", "inferred", "unknown"}
_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitize_windows_component(value: str, *, fallback: str = "Untitled") -> str:
    """Preserve Unicode while removing Windows-illegal and trailing characters."""

    cleaned = _WINDOWS_ILLEGAL.sub(" ", str(value or ""))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def _parse_meeting_start(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if not re.search(r"[T ]\d{2}:\d{2}", normalized):
        raise ValueError("meeting_start must include an explicit hour and minute")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("meeting_start must be an ISO-8601 datetime") from exc


def _meeting_naming(
    meeting_start: datetime | str | None,
    likely_project: str,
    content: str,
) -> tuple[str, str, str, str | None]:
    parsed = _parse_meeting_start(meeting_start)
    project = sanitize_windows_component(likely_project, fallback="Unknown Project")
    subject = sanitize_windows_component(content, fallback="Meeting")
    if parsed is None:
        prefix = "UnknownDateTime"
        meeting_date = "UnknownDate"
        start_iso = None
    else:
        prefix = parsed.strftime("%y%m%d%H%M")
        meeting_date = f"{parsed.day:02d}{_MONTHS[parsed.month - 1]}{parsed.year:04d}"
        start_iso = parsed.isoformat()
    folder_name = sanitize_windows_component(f"{prefix} {meeting_date} {project} {subject}")
    return prefix, meeting_date, folder_name, start_iso


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _write_new_text(path: Path, value: str) -> None:
    if path.exists():
        raise FileExistsError(f"Archive output already exists: {path}")
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    _write_new_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def _relative(path: Path, folder: Path) -> str:
    return path.resolve().relative_to(folder.resolve()).as_posix()


def _find_archive_by_meeting_id(archive_root: Path, meeting_id: str) -> Path | None:
    matches = []
    if not archive_root.is_dir():
        return None
    for child in archive_root.iterdir():
        manifest_path = child / "manifest.json"
        if not child.is_dir() or not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("meeting_id") or "") == meeting_id:
            matches.append(child)
    if len(matches) > 1:
        raise ValueError(f"Multiple archives contain meeting_id {meeting_id!r}")
    return matches[0] if matches else None


def _collision_safe_folder(archive_root: Path, desired_name: str, current: Path | None = None) -> Path:
    desired = archive_root / desired_name
    if not desired.exists() or (current is not None and desired.resolve() == current.resolve()):
        return desired
    index = 2
    while True:
        candidate = archive_root / f"{desired_name} ({index})"
        if not candidate.exists() or (
            current is not None and candidate.resolve() == current.resolve()
        ):
            return candidate
        index += 1


def _validate_metadata(
    meeting_id: str,
    meeting_datetime_source: str,
    meeting_start: datetime | str | None,
) -> None:
    if not str(meeting_id or "").strip():
        raise ValueError("meeting_id is required and remains stable across archive renames")
    if meeting_datetime_source not in DATETIME_SOURCES:
        raise ValueError("Unsupported meeting_datetime_source")
    if _parse_meeting_start(meeting_start) is None and meeting_datetime_source != "unknown":
        raise ValueError("A missing meeting_start must use meeting_datetime_source='unknown'")


def _initial_manifest(
    meeting_id: str,
    meeting_datetime_source: str,
    start_iso: str | None,
    likely_project: str,
    content: str,
    short_name: str,
    folder_name: str,
) -> dict[str, Any]:
    return {
        "schema_version": "apma.meeting-archive.v1",
        "meeting_id": meeting_id,
        "meeting_metadata": {
            "meeting_start": start_iso,
            "meeting_datetime_source": meeting_datetime_source,
            "likely_project": likely_project,
            "content": content,
            "short_name": short_name,
            "human_readable_folder": folder_name,
        },
        "authority": {
            "canonical_format": "JSON",
            "derived_formats": ["HTML", "TXT", "SRT", "VTT", "DOCX", "PDF"],
            "docx_status": "generated per revision from canonical FINAL JSON",
            "pdf_status": "generated per revision from canonical FINAL JSON",
            "exports_are_not_canonical_edit_sources": True,
        },
        "revisions": [],
    }


def _resolve_artifact(configured: Any, fallbacks: list[Path]) -> Path:
    configured_path = Path(str(configured or ""))
    if configured_path.is_file():
        return configured_path
    for candidate in fallbacks:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Runtime artifact not found; checked: {fallbacks}")


def _provider_outputs(job_dir: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Path]]:
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    providers = outputs.get("providers") if isinstance(outputs.get("providers"), dict) else {}
    result = {}
    for provider_code, item in providers.items():
        if provider_code not in PROVIDER_CODES:
            raise ValueError(f"Unapproved provider code in runtime evidence: {provider_code}")
        if not isinstance(item, dict):
            continue
        base = job_dir / "quality" / "provider-evidence" / provider_code
        result[provider_code] = {
            "json": _resolve_artifact(item.get("json"), [base / f"{provider_code}.json"]),
            "html": _resolve_artifact(item.get("html"), [base / f"{provider_code}.html"]),
        }
    return result


def _final_reviewed_path(job_dir: Path, manifest: dict[str, Any]) -> Path:
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    item = outputs.get("final_reviewed") if isinstance(outputs.get("final_reviewed"), dict) else {}
    return _resolve_artifact(
        item.get("json"),
        [job_dir / "quality" / "review" / "FINAL_REVIEWED.json"],
    )


def _source_file(job_dir: Path, manifest: dict[str, Any]) -> Path | None:
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    configured = Path(str(source.get("path") or ""))
    if configured.is_file():
        return configured
    source_dir = job_dir / "source"
    candidates = [path for path in source_dir.iterdir() if path.is_file()] if source_dir.is_dir() else []
    return candidates[0] if len(candidates) == 1 else None


def _speaker_evidence(job_dir: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    item = outputs.get("speaker_overlay") if isinstance(outputs.get("speaker_overlay"), dict) else {}
    overlay_path = Path(str(item.get("json") or ""))
    if not overlay_path.is_file():
        overlay_path = job_dir / "quality" / "speaker-overlay" / "speaker_overlay.json"
    if not overlay_path.is_file():
        return {}
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))

    mapping_path = Path(str(item.get("speaker_mappings_json") or ""))
    if not mapping_path.is_file():
        mapping_path = job_dir / "quality" / "review" / "speaker_mappings.json"
    mappings = {}
    if mapping_path.is_file():
        mapping_payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        mappings = {
            str(mapping["local_speaker_key"]): mapping
            for mapping in mapping_payload.get("mappings") or []
        }

    result = {}
    for window in overlay.get("windows") or []:
        turns = []
        for segment in window.get("speaker_evidence", {}).get("segments") or []:
            item_copy = deepcopy(segment)
            mapping = mappings.get(str(segment.get("local_speaker_key") or ""))
            provider_id = str(segment.get("speaker_id") or "UNLABELLED")
            item_copy["display_label"] = (
                f"{mapping['display_name']} [{provider_id}]" if mapping else provider_id
            )
            item_copy["human_mapping"] = deepcopy(mapping)
            turns.append(item_copy)
        result[str(window.get("window_id"))] = {
            "evidence_only": True,
            "turns": turns,
        }
    return result


def _canonical_final(
    final_reviewed: dict[str, Any],
    *,
    meeting_id: str,
    source_path: Path,
    speaker_by_window: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    windows = final_reviewed.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("FINAL_REVIEWED must contain windows")
    segments = []
    for window in windows:
        text = window.get("final_text")
        if not isinstance(text, str):
            raise ValueError("Every FINAL_REVIEWED window must be resolved before final export")
        speaker_evidence = deepcopy(speaker_by_window.get(str(window.get("window_id"))) or {})
        labels = []
        for turn in speaker_evidence.get("turns") or []:
            label = str(turn.get("display_label") or "").strip()
            if label and label not in labels:
                labels.append(label)
        segments.append(
            {
                "window_id": window["window_id"],
                "start_sec": window["global_start_sec"],
                "end_sec": window["global_end_sec"],
                "text": text,
                "speaker": " / ".join(labels) if labels else None,
                "speaker_turns": speaker_evidence.get("turns") or [],
                "timing_authority": "application_owned_global_recording_timeline",
                "review_resolution": deepcopy(window.get("review_resolution")),
            }
        )
    return {
        "schema_version": "apma.meeting-final.v1",
        "meeting_id": meeting_id,
        "created_at": _utc_now(),
        "source_final_reviewed": {
            "path": str(source_path.resolve()),
            "sha256": _sha256(source_path),
            "modified": False,
        },
        "authority": {
            "canonical": True,
            "derived_exports": ["HTML", "TXT", "SRT", "VTT", "DOCX", "PDF"],
        },
        "text": "\n\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "source_windows": deepcopy(windows),
    }


def _copy_provider_artifact(source: Path, target: Path) -> dict[str, Any]:
    if target.exists():
        raise FileExistsError(f"Provider archive target already exists: {target}")
    shutil.copyfile(source, target)
    source_sha = _sha256(source)
    copied_sha = _sha256(target)
    if copied_sha != source_sha:
        target.unlink(missing_ok=True)
        raise OSError("Provider evidence copy failed SHA-256 verification")
    return {
        "path": str(target),
        "relative_path": target.name,
        "sha256": copied_sha,
        "source_sha256": source_sha,
        "copied_byte_for_byte": True,
    }


def _copy_source(source: Path | None, source_dir: Path, revision: int) -> dict[str, Any] | None:
    if source is None:
        return None
    safe_name = sanitize_windows_component(source.name, fallback="source-audio")
    target = source_dir / safe_name
    source_sha = _sha256(source)
    if target.exists() and _sha256(target) == source_sha:
        return {"relative_path": target.name, "sha256": source_sha, "reused": True}
    if target.exists():
        target = source_dir / f"{target.stem} rev{revision:02d}{target.suffix}"
    if target.exists():
        raise FileExistsError(f"Source archive target already exists: {target}")
    shutil.copyfile(source, target)
    if _sha256(target) != source_sha:
        target.unlink(missing_ok=True)
        raise OSError("Source copy failed SHA-256 verification")
    return {"relative_path": target.name, "sha256": source_sha, "reused": False}


def _final_export_paths(final_dir: Path, stem: str) -> dict[str, Path]:
    return {
        extension: final_dir / f"{stem} FINAL.{extension}"
        for extension in ("json", "html", "txt", "srt", "vtt", "docx", "pdf")
    }


def archive_meeting(
    job_dir: Path,
    archive_root: Path,
    *,
    meeting_id: str,
    meeting_start: datetime | str | None,
    meeting_datetime_source: str,
    likely_project: str,
    content: str,
    short_name: str,
) -> dict[str, Any]:
    """Create one non-destructive archive revision from an existing APMA job."""

    _validate_metadata(meeting_id, meeting_datetime_source, meeting_start)
    job_dir = Path(job_dir).resolve()
    archive_root = Path(archive_root).resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    prefix, _meeting_date, desired_name, start_iso = _meeting_naming(
        meeting_start, likely_project, content
    )
    safe_short_name = sanitize_windows_component(short_name, fallback="Meeting")

    archive_folder = _find_archive_by_meeting_id(archive_root, meeting_id)
    if archive_folder is None:
        archive_folder = _collision_safe_folder(archive_root, desired_name)
        archive_folder.mkdir()
        manifest = _initial_manifest(
            meeting_id,
            meeting_datetime_source,
            start_iso,
            sanitize_windows_component(likely_project, fallback="Unknown Project"),
            sanitize_windows_component(content, fallback="Meeting"),
            safe_short_name,
            archive_folder.name,
        )
    else:
        manifest = json.loads((archive_folder / "manifest.json").read_text(encoding="utf-8"))
        if archive_folder.name != desired_name:
            target = _collision_safe_folder(archive_root, desired_name, current=archive_folder)
            archive_folder.rename(target)
            archive_folder = target
        manifest["meeting_metadata"] = {
            "meeting_start": start_iso,
            "meeting_datetime_source": meeting_datetime_source,
            "likely_project": sanitize_windows_component(likely_project, fallback="Unknown Project"),
            "content": sanitize_windows_component(content, fallback="Meeting"),
            "short_name": safe_short_name,
            "human_readable_folder": archive_folder.name,
        }

    source_dir = archive_folder / "source"
    asr_dir = archive_folder / "asr"
    final_dir = archive_folder / "final"
    for directory in (source_dir, asr_dir, final_dir):
        directory.mkdir(exist_ok=True)

    runtime_manifest = job_mod.read_manifest(job_dir)
    revision = len(manifest.get("revisions") or []) + 1
    revision_marker = "" if revision == 1 else f" rev{revision:02d}"
    provider_stem = f"{prefix} {safe_short_name}{revision_marker}"

    providers = {}
    for provider_code, paths in _provider_outputs(job_dir, runtime_manifest).items():
        providers[provider_code] = {}
        for extension, source_path in paths.items():
            target = asr_dir / f"{provider_stem} {provider_code}.{extension}"
            copied = _copy_provider_artifact(source_path, target)
            copied["relative_path"] = _relative(target, archive_folder)
            providers[provider_code][extension] = copied

    final_reviewed_path = _final_reviewed_path(job_dir, runtime_manifest)
    final_reviewed = json.loads(final_reviewed_path.read_text(encoding="utf-8"))
    canonical = _canonical_final(
        final_reviewed,
        meeting_id=meeting_id,
        source_path=final_reviewed_path,
        speaker_by_window=_speaker_evidence(job_dir, runtime_manifest),
    )
    final_stem = f"{prefix} {safe_short_name}{revision_marker}"
    final_paths = _final_export_paths(final_dir, final_stem)
    _write_new_json(final_paths["json"], canonical)
    html_value = render_html(canonical).replace(
        "<code>full_transcript.json</code>",
        f"<code>{escape(final_paths['json'].name)}</code>",
    )
    _write_new_text(final_paths["html"], html_value)
    _write_new_text(final_paths["txt"], canonical["text"] + "\n")
    _write_new_text(final_paths["srt"], render_srt(canonical))
    _write_new_text(final_paths["vtt"], render_vtt(canonical))
    write_docx(canonical, final_paths["docx"])
    write_pdf(canonical, final_paths["pdf"])

    source_record = _copy_source(_source_file(job_dir, runtime_manifest), source_dir, revision)
    revision_record = {
        "revision": revision,
        "created_at": _utc_now(),
        "runtime_job_id": str(runtime_manifest.get("job_id") or job_dir.name),
        "provider_outputs": providers,
        "final_outputs": {
            extension: {
                "relative_path": _relative(path, archive_folder),
                "sha256": _sha256(path),
                "canonical": extension == "json",
            }
            for extension, path in final_paths.items()
        },
        "source": source_record,
        "source_final_reviewed_sha256": _sha256(final_reviewed_path),
        "provider_evidence_modified": False,
    }
    manifest.setdefault("revisions", []).append(revision_record)
    manifest["current_revision"] = revision
    manifest["canonical_final_json"] = revision_record["final_outputs"]["json"]["relative_path"]
    _atomic_json(archive_folder / "manifest.json", manifest)
    return {
        "meeting_id": meeting_id,
        "archive_folder": str(archive_folder),
        "revision": revision,
        "provider_filenames": {
            code: {kind: Path(item["path"]).name for kind, item in values.items()}
            for code, values in providers.items()
        },
        "final_filenames": {kind: path.name for kind, path in final_paths.items()},
        "formats_completed": ["JSON", "HTML", "TXT", "SRT", "VTT", "DOCX", "PDF"],
        "docx_status": manifest["authority"]["docx_status"],
        "pdf_status": manifest["authority"]["pdf_status"],
    }


def rename_meeting_archive(
    archive_root: Path,
    *,
    meeting_id: str,
    meeting_start: datetime | str | None,
    meeting_datetime_source: str,
    likely_project: str,
    content: str,
    short_name: str,
) -> Path:
    """Rename only human-readable metadata while preserving meeting_id and relative refs."""

    _validate_metadata(meeting_id, meeting_datetime_source, meeting_start)
    archive_root = Path(archive_root).resolve()
    current = _find_archive_by_meeting_id(archive_root, meeting_id)
    if current is None:
        raise FileNotFoundError(f"No archive found for meeting_id {meeting_id!r}")
    _prefix, _date, desired_name, start_iso = _meeting_naming(
        meeting_start, likely_project, content
    )
    target = _collision_safe_folder(archive_root, desired_name, current=current)
    if target.resolve() != current.resolve():
        current.rename(target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["meeting_metadata"] = {
        "meeting_start": start_iso,
        "meeting_datetime_source": meeting_datetime_source,
        "likely_project": sanitize_windows_component(likely_project, fallback="Unknown Project"),
        "content": sanitize_windows_component(content, fallback="Meeting"),
        "short_name": sanitize_windows_component(short_name, fallback="Meeting"),
        "human_readable_folder": target.name,
    }
    _atomic_json(manifest_path, manifest)
    return target


__all__ = [
    "DATETIME_SOURCES",
    "PROVIDER_CODES",
    "archive_meeting",
    "rename_meeting_archive",
    "sanitize_windows_component",
]
