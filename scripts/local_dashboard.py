from __future__ import annotations

import json
import math
import mimetypes
import os
import shutil
import sys
import time
import uuid
from copy import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import runner
from services.audio_project import project_paths
from services.audio_formats import SUPPORTED_INPUT_EXTENSIONS
from services.config import Config, load_config, paid_cost_authorization_amount
from services import job as job_mod
from services.human_review import REVIEW_HTML, ReviewWorkspace
from services.integrity import sha256_file
from services.speaker_overlay import SpeakerMappingStore
from services.minutes.openai_adapter import LiveOpenAIMinutesGenerator, estimate_minutes_cost, minutes_model_options
from services.minutes.presets import list_minutes_style_presets, normalize_minutes_style
from services.pilot_evidence import (
    build_pilot_summary,
    load_pilot_evidence,
    record_pilot_evidence,
)
from services.preprocess import probe_audio_metadata
from services.quality_reconciliation import estimate_reconciliation, run_reconciliation
from services.quality_workflow import run_quality_workflow
from services.storage_retention import (
    read_upload_reference,
    remove_upload_reference,
    retain_source_bytes,
    write_upload_reference,
)
from services.selective_rescue import (
    PROVIDER_ORDER as QUALITY_PROVIDER_ORDER,
    QUALITY_PROVIDER_MODELS,
    configure_quality_chunking,
    configure_quality_provider_model,
)
from services.transcription.models import (
    configure_model_chunking,
    configured_model_specs,
    get_model_spec,
)
from services.transcription.router import configured_runtime_models, get_runtime_model_status


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
STORAGE_PATH = Path(os.environ.get("DASHBOARD_STORAGE_PATH", "jobs"))
UPLOADS_DIR_NAME = "dashboard_uploads"
SUPPORTED_AUDIO_EXTENSIONS = SUPPORTED_INPUT_EXTENSIONS
LIVE_MODE = "live_openai"
DRY_RUN_MODE = "dry_run"
BUDGET_EPSILON_USD = 0.000001
TRANSCRIPT_EXPORT_SPECS = {
    "html": {
        "filename": "full_transcript.html",
        "content_type": "text/html; charset=utf-8",
        "disposition": "inline",
    },
    "srt": {
        "filename": "full_transcript.srt",
        "content_type": "application/x-subrip; charset=utf-8",
        "disposition": "attachment",
    },
    "vtt": {
        "filename": "full_transcript.vtt",
        "content_type": "text/vtt; charset=utf-8",
        "disposition": "attachment",
    },
}
TRANSCRIPT_HTML_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src 'none'; "
    "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)

REVIEW_FINAL_DRAFT_ENV = "APMA_REVIEW_FINAL_DRAFT_PATH"
REVIEW_AUDIO_DIR_ENV = "APMA_REVIEW_AUDIO_DIR"
REVIEW_OUTPUT_DIR_ENV = "APMA_REVIEW_OUTPUT_DIR"


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file_response(handler: BaseHTTPRequestHandler, path: Path, spec: dict[str, str]) -> None:
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", spec["content_type"])
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header(
        "Content-Disposition",
        f'{spec["disposition"]}; filename="{spec["filename"]}"',
    )
    if spec["filename"].endswith(".html"):
        handler.send_header("Content-Security-Policy", TRANSCRIPT_HTML_CSP)
    handler.end_headers()
    handler.wfile.write(body)


def _media_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    """Serve local job audio with one bounded HTTP byte range for seeking."""

    size = path.stat().st_size
    start = 0
    end = max(0, size - 1)
    status = 200
    requested = handler.headers.get("Range", "").strip()
    if requested:
        unit, separator, value = requested.partition("=")
        if unit != "bytes" or not separator or "," in value:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        raw_start, dash, raw_end = value.partition("-")
        if not dash:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        try:
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else end
            elif raw_end:
                suffix = int(raw_end)
                start = max(0, size - suffix)
            else:
                raise ValueError
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        if start < 0 or start >= size or end < start:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
        end = min(end, size - 1)
        status = 206

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", _audio_content_type(path.name))
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Disposition", f'inline; filename="{_safe_filename(path.name)}"')
    handler.send_header("X-Content-Type-Options", "nosniff")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                break
            handler.wfile.write(block)
            remaining -= len(block)


def _review_workspace_from_environment(job_id: str | None = None) -> ReviewWorkspace:
    if job_id:
        safe_job_id = job_mod.validate_job_id(job_id)
        job_dir = _job_path(safe_job_id)
        manifest = job_mod.read_manifest(job_dir)
        quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
        outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
        final_draft = Path(str((outputs.get("final_draft") or {}).get("json") or ""))
        audio_dir = Path(str(quality.get("review_audio_dir") or ""))
        final_reviewed = Path(str((outputs.get("final_reviewed") or {}).get("json") or ""))
        if not final_draft.is_file():
            final_draft = job_dir / "quality" / "final-draft" / "FINAL_DRAFT.json"
        if not audio_dir.is_dir():
            audio_dir = job_dir / "quality" / "rescue" / "extracted-clips" / "chunks"
        if not final_reviewed.is_file():
            final_reviewed = job_dir / "quality" / "review" / "FINAL_REVIEWED.json"
        if not final_draft.is_file() or not audio_dir.is_dir():
            raise RuntimeError(f"Quality review artifacts are incomplete for job {safe_job_id}")
        return ReviewWorkspace(final_draft, audio_dir, final_reviewed.parent)
    final_draft = os.environ.get(REVIEW_FINAL_DRAFT_ENV)
    audio_dir = os.environ.get(REVIEW_AUDIO_DIR_ENV)
    output_dir = os.environ.get(REVIEW_OUTPUT_DIR_ENV)
    missing = [
        name
        for name, value in (
            (REVIEW_FINAL_DRAFT_ENV, final_draft),
            (REVIEW_AUDIO_DIR_ENV, audio_dir),
            (REVIEW_OUTPUT_DIR_ENV, output_dir),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Human review is not configured: " + ", ".join(missing))
    return ReviewWorkspace(Path(final_draft), Path(audio_dir), Path(output_dir))


def _sync_quality_review_status(job_id: str | None, view: dict) -> None:
    if not job_id:
        return
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    manifest = job_mod.read_manifest(job_dir)
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    unresolved = int(view.get("statistics", {}).get("unresolved_windows", 0))
    quality["overall_status"] = "REVIEW_REQUIRED" if unresolved else "COMPLETE"
    stages = quality.get("stages") if isinstance(quality.get("stages"), dict) else {}
    review_stage = stages.get("REVIEW") if isinstance(stages.get("REVIEW"), dict) else {}
    review_stage["unresolved_windows"] = unresolved
    stages["REVIEW"] = review_stage
    quality["stages"] = stages
    manifest["quality"] = quality
    manifest["state"] = quality["overall_status"].lower()
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    job_mod.write_manifest(job_dir, manifest)


def _speaker_mapping_store(job_id: str | None) -> SpeakerMappingStore | None:
    if not job_id:
        return None
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    manifest = job_mod.read_manifest(job_dir)
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    item = outputs.get("speaker_overlay") if isinstance(outputs.get("speaker_overlay"), dict) else {}
    overlay_path = Path(str(item.get("json") or ""))
    if not overlay_path.is_file():
        overlay_path = job_dir / "quality" / "speaker-overlay" / "speaker_overlay.json"
    if not overlay_path.is_file():
        return None
    mapping_path = Path(str(item.get("speaker_mappings_json") or ""))
    if (
        not str(mapping_path)
        or mapping_path == Path(".")
        or not mapping_path.parent.is_dir()
    ):
        mapping_path = job_dir / "quality" / "review" / "speaker_mappings.json"
    return SpeakerMappingStore(overlay_path, mapping_path)


def _review_payload(job_id: str | None = None) -> dict:
    workspace = _review_workspace_from_environment(job_id)
    workspace.write_reviewed_outputs()
    view = workspace.view()
    if job_id:
        suffix = f"?job_id={job_mod.validate_job_id(job_id)}"
        for window in view["windows"]:
            window["audio_url"] += suffix
    mapping_store = _speaker_mapping_store(job_id)
    if mapping_store is not None:
        view = mapping_store.apply_to_review_view(view)
    _sync_quality_review_status(job_id, view)
    return view


def _match_review_audio_request(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:3] != ["api", "review", "audio"]:
        return None
    return unquote(parts[3])


def _review_audio_response(
    handler: BaseHTTPRequestHandler, workspace: ReviewWorkspace, window_id: str
) -> None:
    path, metadata = workspace.audio(window_id)
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Content-Disposition", f'inline; filename="{path.name}"')
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-APMA-Window-Id", metadata["window_id"])
    handler.send_header("X-APMA-Global-Start", str(metadata["global_start_sec"]))
    handler.send_header("X-APMA-Global-End", str(metadata["global_end_sec"]))
    handler.send_header("X-APMA-Clip-SHA256", metadata["clip_sha256"])
    handler.end_headers()
    handler.wfile.write(body)


def _safe_filename(filename: str) -> str:
    name = Path(filename or "audio.wav").name
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".", " ") else "_" for ch in name).strip()
    return cleaned or "audio.wav"


def _audio_content_type(filename: str) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    if guessed:
        return guessed
    if Path(filename).suffix.lower() == ".m4a":
        return "audio/mp4"
    return "audio/wav"


def _storage_root() -> Path:
    return STORAGE_PATH if STORAGE_PATH.is_absolute() else REPO_ROOT / STORAGE_PATH


def _upload_path(upload_id: str) -> Path:
    return _storage_root() / UPLOADS_DIR_NAME / upload_id


def _uploaded_audio_path(upload_id: str) -> Path | None:
    upload_dir = _upload_path(upload_id)
    if not upload_dir.is_dir():
        return None
    reference = read_upload_reference(upload_dir, _storage_root())
    if reference is not None:
        return Path(reference["source_path"])
    return next(
        (path for path in upload_dir.iterdir() if path.is_file()),
        None,
    )


def _job_path(job_id: str) -> Path:
    return _storage_root() / job_id


def _job_audio_path(job_id: str) -> Path | None:
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id).resolve()
    manifest = job_mod.read_manifest(job_dir)
    audio_project = (
        manifest.get("audio_project")
        if isinstance(manifest.get("audio_project"), dict)
        else {}
    )
    original = (
        audio_project.get("original")
        if isinstance(audio_project.get("original"), dict)
        else {}
    )
    mp3 = audio_project.get("mp3") if isinstance(audio_project.get("mp3"), dict) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    for raw_path in (original.get("path"), mp3.get("path"), source.get("path")):
        if not raw_path:
            continue
        candidate = Path(str(raw_path)).resolve()
        try:
            candidate.relative_to(job_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _display_job_state(raw_state: str) -> str:
    state = str(raw_state or "queued").lower()
    if state in {"completed", "complete"}:
        return "ready"
    if state == "review_required":
        return "review_required"
    if state == "failed":
        return "failed"
    if state == "cancelled":
        return "cancelled"
    return "processing"


def _job_list_item(manifest: dict) -> dict:
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    preprocess = (
        manifest.get("preprocess")
        if isinstance(manifest.get("preprocess"), dict)
        else {}
    )
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    stages = quality.get("stages") if isinstance(quality.get("stages"), dict) else {}
    review_stage = stages.get("REVIEW") if isinstance(stages.get("REVIEW"), dict) else {}
    providers = quality.get("providers") if isinstance(quality.get("providers"), dict) else {}
    duration = preprocess.get("blob_duration_seconds", preprocess.get("duration_seconds"))
    review_required = int(review_stage.get("unresolved_windows", 0) or 0)
    return {
        "job_id": str(manifest.get("job_id") or ""),
        "title": str(source.get("original_filename") or source.get("filename") or manifest.get("job_id") or "Untitled recording"),
        "state": str(manifest.get("state") or "queued"),
        "display_state": _display_job_state(str(manifest.get("state") or "queued")),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "duration_seconds": duration,
        "estimated_cost_usd": float(manifest.get("estimated_cost_usd", 0.0) or 0.0),
        "actual_cost_usd": float(manifest.get("actual_cost_usd", 0.0) or 0.0),
        "review_required_count": review_required,
        "provider_codes": sorted(str(code) for code in providers),
        "has_transcript": bool((manifest.get("outputs") or {}).get("full_transcript_json")),
        "has_quality_review": bool(quality.get("review_url")),
    }


def list_dashboard_jobs(limit: int = 24) -> dict:
    root = _storage_root()
    if not root.is_dir():
        return {"ok": True, "jobs": []}
    items = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("_") or path.name == UPLOADS_DIR_NAME:
            continue
        manifest_path = path / "job_manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = job_mod.read_manifest(path)
            item = _job_list_item(manifest)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if item["job_id"]:
            items.append(item)
    items.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return {"ok": True, "jobs": items[: max(1, min(int(limit), 50))]}


def dashboard_job(job_id: str) -> dict:
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    manifest = job_mod.read_manifest(job_dir)
    if not manifest:
        raise FileNotFoundError("Job was not found")
    item = _job_list_item(manifest)
    outputs = collect_job_outputs(safe_job_id)
    try:
        pilot_evidence = load_pilot_evidence(job_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        pilot_evidence = {
            "recorded": False,
            "integrity": {"ok": False},
            "error": f"Pilot evidence could not be verified: {exc}",
        }
    return {
        "ok": item["display_state"] in {"ready", "review_required"},
        **item,
        "outputs": outputs,
        "pilot_evidence": pilot_evidence,
        "audio_url": f"/api/jobs/{safe_job_id}/audio" if _job_audio_path(safe_job_id) else None,
        "review_url": f"/review?job_id={safe_job_id}" if item["has_quality_review"] else None,
    }


def save_dashboard_pilot_evidence(job_id: str, payload: dict) -> dict:
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    if not (job_dir / "job_manifest.json").is_file():
        raise FileNotFoundError("Job was not found")
    evidence = record_pilot_evidence(job_dir, payload)
    cohort_code = evidence["pilot_outcome"]["cohort_code"]
    return {
        "ok": True,
        "job_id": safe_job_id,
        "pilot_evidence": load_pilot_evidence(job_dir),
        "pilot_summary": build_pilot_summary(
            _storage_root(), cohort_code=cohort_code
        ),
    }


def dashboard_pilot_summary(cohort_code: str | None = None) -> dict:
    return {
        "ok": True,
        **build_pilot_summary(_storage_root(), cohort_code=cohort_code),
    }


def estimate_job_reconciliation(job_id: str, cfg: Config | None = None) -> dict:
    """Estimate the separately approved GPT-5.6 Sol text reconciliation."""

    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    manifest = job_mod.read_manifest(job_dir)
    settings = cfg or load_config()
    estimate = estimate_reconciliation(manifest, settings)
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    active = quality.get("reconciliation") if isinstance(quality.get("reconciliation"), dict) else {}
    return {
        **estimate,
        "job_id": safe_job_id,
        "already_completed": active.get("status") == "completed",
        "active_reconciliation": active,
        "review_url": quality.get("review_url") or f"/review?job_id={safe_job_id}",
    }


def run_job_reconciliation(
    job_id: str,
    *,
    confirm_live_reconciliation: bool,
    run_budget_cap_usd: float | None,
    cfg: Config | None = None,
    client: object | None = None,
) -> dict:
    """Run/resume reconciliation only after the exact displayed budget is approved."""

    safe_job_id = job_mod.validate_job_id(job_id)
    if run_budget_cap_usd is None:
        return {
            "ok": False,
            "errors": ["Estimate GPT-5.6 Sol first, then approve its displayed budget."],
        }
    return run_reconciliation(
        _job_path(safe_job_id),
        cfg or load_config(),
        confirm_live_reconciliation=confirm_live_reconciliation,
        run_budget_cap_usd=float(run_budget_cap_usd),
        client=client,
    )


def _find_resumable_quality_job(audio_file: Path) -> str | None:
    """Find the newest failed dashboard Quality job for the exact same audio."""

    source_sha256 = sha256_file(audio_file)
    candidates = sorted(
        _storage_root().glob("dashboard-quality-*"),
        key=lambda path: path.name,
        reverse=True,
    )
    for job_dir in candidates:
        manifest_path = job_dir / "job_manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = job_mod.read_manifest(job_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
        if (
            quality.get("overall_status") == "failed"
            and manifest.get("source", {}).get("sha256") == source_sha256
        ):
            return str(manifest.get("job_id") or job_dir.name)
    return None


def transcript_export_path(job_id: str, export_format: str) -> tuple[Path, dict[str, str]]:
    safe_job_id = job_mod.validate_job_id(job_id)
    spec = TRANSCRIPT_EXPORT_SPECS.get(export_format)
    if spec is None:
        raise ValueError(f"Unsupported transcript export format: {export_format}")
    return _job_path(safe_job_id) / "outputs" / spec["filename"], spec


def _match_transcript_export_request(path: str) -> tuple[Path, dict[str, str]] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:2] != ["api", "jobs"] or parts[3] != "exports":
        return None
    try:
        return transcript_export_path(parts[2], parts[4])
    except ValueError:
        return None


def _match_dashboard_job_request(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
        return unquote(parts[2]), "detail"
    if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "audio":
        return unquote(parts[2]), "audio"
    return None


def _estimate_chunk_plan(duration_seconds: float, bytes_per_second: int, cfg: Config) -> list[dict]:
    if bytes_per_second <= 0:
        max_duration_by_size = cfg.default_chunk_duration_sec
    else:
        max_duration_by_size = cfg.target_max_chunk_bytes // bytes_per_second

    effective_max_duration = min(max_duration_by_size, cfg.max_chunk_duration_sec)
    chunk_duration = min(cfg.default_chunk_duration_sec, effective_max_duration)
    if effective_max_duration >= cfg.min_chunk_duration_sec:
        chunk_duration = max(cfg.min_chunk_duration_sec, int(chunk_duration))
    else:
        chunk_duration = max(1, int(effective_max_duration))
    overlap_seconds = min(max(0, int(cfg.overlap_seconds)), max(0, chunk_duration - 1))

    chunks = []
    start_sec = 0.0
    while start_sec < duration_seconds:
        end_sec = min(duration_seconds, start_sec + chunk_duration)
        chunk_seconds = max(0.0, end_sec - start_sec)
        chunks.append(
            {
                "chunk_id": len(chunks) + 1,
                "start_sec": float(start_sec),
                "end_sec": float(end_sec),
                "duration_seconds": float(chunk_seconds),
                "billable_seconds": int(math.ceil(chunk_seconds)),
            }
        )
        if end_sec >= duration_seconds:
            break
        next_start = end_sec - overlap_seconds
        if next_start <= start_sec:
            next_start = start_sec + max(1, chunk_duration // 10)
        start_sec = float(next_start)
    return chunks


def _estimate_chunked_cost(chunks: list[dict], model: str, cfg: Config) -> dict:
    status = get_runtime_model_status(model, cfg)
    price = status["price_per_minute_usd"]
    billing = {
        "billing_mode": status["billing_mode"],
        "cost_cap_included": status["cost_cap_included"],
        "billing_display": status["billing_display"],
    }
    if price is None:
        return {
            "estimated_cost_usd": None,
            "run_budget_cap_usd": None,
            "rounding_guard_usd": None,
            "cost_buffer_percent": None,
            "cost_buffer_usd": None,
            "max_chunk_cost_usd": None,
            "price_per_minute_usd": None,
            **billing,
        }
    chunk_costs = []
    for chunk in chunks:
        cost = float((chunk["billable_seconds"] / 60.0) * price)
        chunk_costs.append(cost)
    estimated_cost = float(sum(chunk_costs))
    rounding_guard_usd = float(
        (len(chunks) / 60.0) * price if billing["cost_cap_included"] else 0.0
    )
    guarded_estimate = estimated_cost + rounding_guard_usd
    run_budget_cap = (
        paid_cost_authorization_amount(guarded_estimate, cfg)
        if billing["cost_cap_included"]
        else guarded_estimate
    )
    cost_buffer_usd = max(0.0, run_budget_cap - guarded_estimate)
    return {
        "estimated_cost_usd": estimated_cost,
        "run_budget_cap_usd": run_budget_cap,
        "rounding_guard_usd": rounding_guard_usd,
        "cost_buffer_percent": (
            float(cfg.paid_provider_cost_buffer_percent)
            if billing["cost_cap_included"]
            else 0.0
        ),
        "cost_buffer_usd": cost_buffer_usd,
        "max_chunk_cost_usd": float(
            (max(chunk_costs) if chunk_costs else 0.0)
            + ((price / 60.0) if chunk_costs and billing["cost_cap_included"] else 0.0)
        ),
        "price_per_minute_usd": float(price),
        **billing,
    }


def _apply_model_chunk_limit(cfg: Config, model: str) -> Config:
    """Apply request-envelope limits before APMA creates provider chunks."""

    return configure_model_chunking(cfg, model)


def _model_options(cfg: Config) -> list[dict]:
    return configured_runtime_models(cfg)


def estimate_audio(path: Path, cfg: Config) -> dict:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        return {
            "ok": False,
            "error": f"This MVP supports these formats: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}.",
        }
    if not path.exists():
        return {"ok": False, "error": "Audio file is missing."}

    size_bytes = path.stat().st_size
    dashboard_upload_limit_bytes = int(getattr(cfg, "dashboard_upload_limit_bytes", cfg.openai_file_size_limit_bytes))
    if size_bytes > dashboard_upload_limit_bytes:
        return {
            "ok": False,
            "error": f"Audio file exceeds the configured dashboard upload limit of {dashboard_upload_limit_bytes} bytes.",
            "size_bytes": size_bytes,
        }

    try:
        metadata = probe_audio_metadata(str(path), cfg)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    duration_seconds = float(metadata.get("duration_seconds", 0.0))
    bytes_per_second = int(metadata.get("bytes_per_second") or metadata.get("normalized_bytes_per_second") or 0)
    chunks = _estimate_chunk_plan(duration_seconds, bytes_per_second, cfg)
    billable_seconds = sum(chunk["billable_seconds"] for chunk in chunks)
    estimation_method = "wav_duration_chunked" if suffix == ".wav" else "ffprobe_duration_normalized_wav_chunked"

    estimates = {}
    for option in _model_options(cfg):
        model = option["model"]
        model_cfg = _apply_model_chunk_limit(copy(cfg), model)
        model_chunks = _estimate_chunk_plan(
            duration_seconds, bytes_per_second, model_cfg
        )
        model_cost = _estimate_chunked_cost(model_chunks, model, model_cfg)
        estimated = model_cost["estimated_cost_usd"]
        estimates[model] = {
            "chunk_count": len(model_chunks),
            "billable_seconds": sum(
                chunk["billable_seconds"] for chunk in model_chunks
            ),
            "target_max_chunk_bytes": int(model_cfg.target_max_chunk_bytes),
            "estimated_cost_usd": estimated,
            "run_budget_cap_usd": model_cost["run_budget_cap_usd"],
            "rounding_guard_usd": model_cost["rounding_guard_usd"],
            "cost_buffer_percent": model_cost["cost_buffer_percent"],
            "cost_buffer_usd": model_cost["cost_buffer_usd"],
            "max_chunk_cost_usd": model_cost["max_chunk_cost_usd"],
            "price_per_minute_usd": model_cost["price_per_minute_usd"],
            "within_cap": bool(
                option["runnable"]
                and estimated is not None
                and model_cost["run_budget_cap_usd"] <= float(cfg.max_cost_per_job_usd)
            ),
            "provider": option["provider"],
            "provider_label": option["provider_label"],
            "runnable": option["runnable"],
            "readiness_reason": option["readiness_reason"],
            "pricing_configured": option["pricing_configured"],
            "billing_mode": model_cost["billing_mode"],
            "cost_cap_included": model_cost["cost_cap_included"],
            "billing_display": model_cost["billing_display"],
        }

    quality_cfg = configure_quality_chunking(cfg)
    quality_chunks = _estimate_chunk_plan(
        duration_seconds, bytes_per_second, quality_cfg
    )
    quality_providers: dict[str, dict] = {}
    quality_statuses: list[dict] = []
    paid_provider_total = 0.0
    paid_provider_authorization_total = 0.0
    paid_estimates_known = True
    for provider_code in QUALITY_PROVIDER_ORDER:
        provider_cfg = configure_quality_provider_model(quality_cfg, provider_code)
        model = QUALITY_PROVIDER_MODELS[provider_code]
        option = get_runtime_model_status(model, provider_cfg)
        quality_statuses.append(option)
        model_estimate = _estimate_chunked_cost(quality_chunks, model, provider_cfg)
        quality_providers[provider_code] = {
            "model": model,
            "estimated_cost_usd": model_estimate["estimated_cost_usd"],
            "authorization_budget_usd": model_estimate["run_budget_cap_usd"],
            "billing_mode": model_estimate["billing_mode"],
            "cost_cap_included": model_estimate["cost_cap_included"],
            "billing_display": model_estimate["billing_display"],
        }
        if model_estimate["cost_cap_included"]:
            if model_estimate["estimated_cost_usd"] is None:
                paid_estimates_known = False
            else:
                paid_provider_total += float(model_estimate["estimated_cost_usd"])
                paid_provider_authorization_total += float(
                    model_estimate["run_budget_cap_usd"]
                )
    quality_within_cap = bool(
        len(quality_statuses) == 3
        and all(option["runnable"] for option in quality_statuses)
        and paid_estimates_known
        and paid_provider_authorization_total <= float(cfg.max_cost_per_job_usd)
    )

    return {
        "ok": True,
        "duration_seconds": duration_seconds,
        "chunk_count": len(chunks),
        "billable_seconds": int(billable_seconds),
        "estimation_method": estimation_method,
        "source_format": suffix,
        "normalized_for_chunking": suffix != ".wav",
        "size_bytes": size_bytes,
        "max_cost_per_job_usd": float(cfg.max_cost_per_job_usd),
        "models": estimates,
        "quality_transcription": {
            "providers": quality_providers,
            "chunk_count": len(quality_chunks),
            "paid_provider_estimated_total_usd": float(paid_provider_total),
            "paid_provider_cost_buffer_percent": float(
                cfg.paid_provider_cost_buffer_percent
            ),
            "paid_provider_authorization_total_usd": float(
                paid_provider_authorization_total
            ),
            "workspace_cap_usd": float(cfg.max_cost_per_job_usd),
            "within_cap": quality_within_cap,
        },
    }


def dashboard_status(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    model_options = _model_options(cfg)
    quality_models = [
        get_runtime_model_status(
            QUALITY_PROVIDER_MODELS[provider_code],
            configure_quality_provider_model(cfg, provider_code),
        )
        for provider_code in QUALITY_PROVIDER_ORDER
    ]
    quality_runnable = len(quality_models) == 3 and all(
        item.get("runnable") for item in quality_models
    )
    quality_reason = (
        "Ready for one explicitly confirmed, cost-capped integrated quality run."
        if quality_runnable
        else ", ".join(QUALITY_PROVIDER_ORDER) + " must all be configured and ready."
    )
    return {
        "dry_run_default": True,
        "openai_key_available": bool(os.environ.get("OPENAI_API_KEY")),
        "live_enabled_env": _parse_bool(os.environ.get("ENABLE_LIVE_OPENAI_TRANSCRIPTION"), False),
        "live_minutes_enabled_env": _parse_bool(os.environ.get("ENABLE_LIVE_OPENAI_MINUTES"), False),
        "max_cost_per_job_usd": float(cfg.max_cost_per_job_usd),
        "max_minutes_cost_per_job_usd": float(cfg.max_minutes_cost_per_job_usd),
        "dashboard_upload_limit_bytes": int(getattr(cfg, "dashboard_upload_limit_bytes", cfg.openai_file_size_limit_bytes)),
        "openai_file_size_limit_bytes": int(cfg.openai_file_size_limit_bytes),
        "models": model_options,
        "quality_transcription": {
            "label": "Quality Transcription",
            "provider_codes": list(QUALITY_PROVIDER_ORDER),
            "provider_models": dict(QUALITY_PROVIDER_MODELS),
            "runnable": quality_runnable,
            "readiness_reason": quality_reason,
            "readiness": {
                item["provider_code"]: item.get("readiness_reason") for item in quality_models
            },
            "billing": {
                item["provider_code"]: {
                    "billing_mode": item["billing_mode"],
                    "cost_cap_included": item["cost_cap_included"],
                    "billing_display": item["billing_display"],
                }
                for item in quality_models
            },
            "paid_provider_cap_usd": float(cfg.max_cost_per_job_usd),
            "paid_provider_cost_buffer_percent": float(
                cfg.paid_provider_cost_buffer_percent
            ),
        },
        "minutes_models": minutes_model_options(cfg),
        "minutes_styles": list_minutes_style_presets(),
        "default_minutes_style": normalize_minutes_style(getattr(cfg, "minutes_style", "standard")),
        "supported_audio_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
    }


def dashboard_health() -> dict:
    """Return a secret-free daily-use readiness summary."""

    cfg = load_config()
    qwen_staging_mode = str(cfg.qwen_filetrans_staging_mode).strip().lower()
    qwen_fields = ["dashscope_api_key"]
    if qwen_staging_mode == "private_oss":
        qwen_fields.extend(
            [
                "aliyun_oss_access_key_id",
                "aliyun_oss_access_key_secret",
                "aliyun_oss_endpoint",
                "aliyun_oss_bucket",
            ]
        )
    return {
        "apma_running": True,
        "docker_backend_available": True,
        "m3asr_credential_configured": bool(cfg.meralion_api_key),
        "gem35t_credential_configured": bool(cfg.gemini_api_key),
        "openai_credential_configured": bool(cfg.openai_api_key),
        "qwena3ft_credential_configured": all(
            bool(getattr(cfg, name, None)) for name in qwen_fields
        ),
    }


def _multipart_parts(content_type: str, body: bytes) -> list[tuple[dict[str, str], bytes]]:
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("Missing multipart boundary.")
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    delimiter = ("--" + boundary).encode("utf-8")
    parts = []
    for raw_part in body.split(delimiter):
        if not raw_part or raw_part in (b"\r\n", b"--", b"\r\n--"):
            continue
        if raw_part.startswith(b"\r\n"):
            raw_part = raw_part[2:]
        if raw_part.endswith(b"--"):
            raw_part = raw_part[:-2]
        if raw_part.endswith(b"\r\n"):
            raw_part = raw_part[:-2]
        header_bytes, sep, payload = raw_part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers: dict[str, str] = {}
        for line in header_bytes.decode("utf-8", errors="replace").split("\r\n"):
            name, colon, value = line.partition(":")
            if colon:
                headers[name.strip().lower()] = value.strip()
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        parts.append((headers, payload))
    return parts


def save_uploaded_audio(content_type: str, body: bytes) -> dict:
    file_payload = None
    filename = None
    for headers, payload in _multipart_parts(content_type, body):
        disposition = headers.get("content-disposition", "")
        if 'name="audio"' not in disposition:
            continue
        for item in disposition.split(";"):
            item = item.strip()
            if item.startswith("filename="):
                filename = item.split("=", 1)[1].strip().strip('"')
                break
        file_payload = payload
        break

    if not file_payload or not filename:
        raise ValueError("Upload did not include an audio file.")

    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError(f"This dashboard supports these formats: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}.")

    upload_id = uuid.uuid4().hex[:12]
    upload_dir = _upload_path(upload_id)
    path, source_sha256, source_reused = retain_source_bytes(
        file_payload,
        safe_name,
        _storage_root(),
    )
    write_upload_reference(
        upload_dir,
        source_path=path,
        source_sha256=source_sha256,
        original_filename=safe_name,
        size_bytes=len(file_payload),
    )

    cfg = load_config()
    estimate = estimate_audio(path, cfg)
    return {
        "upload_id": upload_id,
        "filename": safe_name,
        "path": str(path),
        "source_sha256": source_sha256,
        "shared_source_reused": bool(source_reused),
        "content_type": _audio_content_type(safe_name),
        "estimate": estimate,
    }


def validate_run_request(
    upload_id: str,
    mode: str,
    model: str,
    confirm_live_api: bool,
    run_budget_cap_usd: float | None = None,
    cfg: Config | None = None,
) -> list[str]:
    cfg = cfg or load_config()
    errors = []
    audio_file = _uploaded_audio_path(upload_id)
    if audio_file is None:
        errors.append("Upload is missing. Choose a WAV or M4A file first.")
        return errors

    estimate = estimate_audio(audio_file, cfg)
    if not estimate.get("ok"):
        errors.append(str(estimate.get("error")))
        return errors

    if mode == DRY_RUN_MODE:
        return errors

    if mode != LIVE_MODE:
        errors.append("Unknown transcription mode.")
    if model not in estimate.get("models", {}):
        errors.append("Selected transcription model is not configured.")
    else:
        model_status = get_runtime_model_status(model, cfg)
        if model_status["provider"] == "openai":
            if not confirm_live_api:
                errors.append("Live OpenAI mode requires explicit confirmation.")
            if not os.environ.get("OPENAI_API_KEY"):
                errors.append("OPENAI_API_KEY must be set in the server environment.")
            if not _parse_bool(os.environ.get("ENABLE_LIVE_OPENAI_TRANSCRIPTION"), False):
                errors.append("ENABLE_LIVE_OPENAI_TRANSCRIPTION must be true for live mode.")
        else:
            if not confirm_live_api:
                errors.append("Live transcription requires explicit confirmation.")
            if not model_status["runnable"]:
                errors.append(
                    f"{model_status['provider_label']} route is setup-required: "
                    f"{model_status['readiness_reason']}"
                )

        model_estimate = estimate["models"][model]
        expected_cap = model_estimate["run_budget_cap_usd"]
        if expected_cap is None:
            errors.append("Provider pricing is not configured, so a live budget cannot be locked.")
        elif run_budget_cap_usd is None:
            errors.append("Run budget cap is missing. Upload and estimate the file again.")
        elif abs(float(run_budget_cap_usd) - float(expected_cap)) > BUDGET_EPSILON_USD:
            errors.append("Run budget cap does not match the current estimate. Upload and estimate the file again.")
        elif not model_estimate["within_cap"]:
            errors.append("Estimated transcription cost exceeds MAX_COST_PER_JOB_USD.")
    return errors


def _build_run_config(
    mode: str,
    model: str,
    run_budget_cap_usd: float | None = None,
    max_chunk_cost_usd: float | None = None,
    minutes_style: str | None = None,
    cfg: Config | None = None,
) -> Config:
    cfg = cfg or load_config()
    cfg.storage_path = str(_storage_root())
    cfg.minutes_style = normalize_minutes_style(minutes_style or getattr(cfg, "minutes_style", "standard"))
    if mode == DRY_RUN_MODE:
        cfg.dry_run = True
        cfg.transcription_engine = "mock"
        cfg.enable_live_openai_transcription = False
        return cfg

    active_model_ids = {spec.model_id for spec in configured_model_specs(cfg)}
    if model not in active_model_ids:
        raise ValueError("Selected transcription model is not configured.")
    spec = get_model_spec(model, cfg)
    cfg.dry_run = False
    if spec.provider == "openai":
        cfg.transcription_engine = "openai"
        cfg.enable_live_openai_transcription = True
        cfg.openai_model = model
        cfg.openai_api_key = os.environ.get("OPENAI_API_KEY")
    elif spec.provider == "meralion":
        cfg.transcription_engine = "meralion"
        cfg.enable_live_meralion_transcription = True
        cfg.meralion_transcription_model = model
    elif spec.provider == "google":
        cfg.transcription_engine = "gemini"
        cfg.enable_live_gemini_transcription = True
        cfg.gemini_transcription_model = model
    elif spec.provider == "alibaba":
        cfg.transcription_engine = "qwen_filetrans"
        cfg.enable_live_qwen_filetrans_transcription = True
        cfg.qwen_filetrans_model = model
    else:
        raise ValueError(
            f"Unsupported transcription provider {spec.provider!r} for model {model!r}."
        )
    cfg = _apply_model_chunk_limit(cfg, model)
    if run_budget_cap_usd is not None:
        cfg.max_cost_per_job_usd = float(run_budget_cap_usd) + BUDGET_EPSILON_USD
    if spec.provider == "openai" and max_chunk_cost_usd is not None:
        cfg.openai_max_cost_per_chunk_usd = float(max_chunk_cost_usd) + BUDGET_EPSILON_USD
    return cfg


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    target = Path(path)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


def _read_json(path: str | None) -> dict:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def collect_job_outputs(job_id: str) -> dict:
    safe_job_id = job_mod.validate_job_id(job_id)
    job_dir = _job_path(safe_job_id)
    manifest_path = job_dir / "job_manifest.json"
    manifest = _read_json(str(manifest_path))
    outputs = manifest.get("outputs", {})
    transcript_exports = {}
    for export_format in TRANSCRIPT_EXPORT_SPECS:
        export_path, _ = transcript_export_path(safe_job_id, export_format)
        if export_path.is_file():
            transcript_exports[export_format] = (
                f"/api/jobs/{safe_job_id}/exports/{export_format}"
            )
    return {
        "manifest": manifest,
        "project_paths": project_paths(manifest.get("audio_project")),
        "full_transcript_txt": _read_text(outputs.get("full_transcript_txt")),
        "full_transcript_json": _read_json(outputs.get("full_transcript_json")),
        "minutes_md": _read_text(outputs.get("minutes_md")),
        "action_items_json": _read_json(outputs.get("action_items_json")),
        "job_summary_json": _read_json(outputs.get("job_summary_json")),
        "transcript_exports": transcript_exports,
    }


def estimate_job_minutes(job_id: str, model: str, minutes_style: str, cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    outputs = collect_job_outputs(job_id)
    transcript_text = outputs.get("full_transcript_txt", "")
    if not transcript_text.strip():
        return {"ok": False, "error": "Transcript is required before estimating minutes."}
    model_names = {item["model"] for item in minutes_model_options(cfg)}
    if model not in model_names:
        return {"ok": False, "error": "Selected minutes model is not configured."}
    return estimate_minutes_cost(transcript_text, minutes_style, model, cfg)


def validate_minutes_request(
    job_id: str,
    model: str,
    minutes_style: str,
    confirm_live_minutes: bool,
    run_budget_cap_usd: float | None,
    cfg: Config | None = None,
) -> list[str]:
    cfg = cfg or load_config()
    errors = []
    job_dir = _job_path(job_id)
    if not (job_dir / "job_manifest.json").exists():
        return ["Job was not found. Run transcription first."]
    estimate = estimate_job_minutes(job_id, model, minutes_style, cfg)
    if not estimate.get("ok"):
        return [str(estimate.get("error", "Unable to estimate minutes."))]
    if not _parse_bool(os.environ.get("ENABLE_LIVE_OPENAI_MINUTES"), False):
        errors.append("ENABLE_LIVE_OPENAI_MINUTES must be true for live minutes generation.")
    if not os.environ.get("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY must be set in the server environment.")
    if not confirm_live_minutes:
        errors.append("Live minutes generation requires explicit confirmation.")
    expected_cap = float(estimate["run_budget_cap_usd"])
    if run_budget_cap_usd is None:
        errors.append("Minutes budget cap is missing. Estimate minutes again.")
    elif abs(float(run_budget_cap_usd) - expected_cap) > BUDGET_EPSILON_USD:
        errors.append("Minutes budget cap does not match the current estimate. Estimate minutes again.")
    elif not estimate["within_cap"]:
        errors.append("Estimated minutes cost exceeds MAX_MINUTES_COST_PER_JOB_USD.")
    return errors


def generate_job_minutes(
    job_id: str,
    model: str,
    minutes_style: str,
    confirm_live_minutes: bool,
    run_budget_cap_usd: float | None,
    cfg: Config | None = None,
) -> dict:
    cfg = cfg or load_config()
    errors = validate_minutes_request(job_id, model, minutes_style, confirm_live_minutes, run_budget_cap_usd, cfg)
    if errors:
        return {"ok": False, "errors": errors}

    cfg.dry_run = False
    cfg.enable_live_openai_minutes = True
    cfg.openai_api_key = os.environ.get("OPENAI_API_KEY")
    cfg.minutes_style = normalize_minutes_style(minutes_style)
    outputs = collect_job_outputs(job_id).get("manifest", {}).get("outputs", {})
    job_dir = _job_path(job_id)
    refs = LiveOpenAIMinutesGenerator().generate(
        job_id,
        job_dir,
        outputs,
        cfg,
        model=model,
        minutes_style=minutes_style,
        run_budget_cap_usd=float(run_budget_cap_usd or 0.0),
        confirm_live_minutes=confirm_live_minutes,
    )
    manifest = job_mod.read_manifest(job_dir)
    manifest.setdefault("outputs", {}).update(refs)
    manifest["minutes_generation"] = {
        "generator": "openai",
        "model": model,
        "minutes_style": normalize_minutes_style(minutes_style),
        "estimated_cost_usd": float(run_budget_cap_usd or 0.0),
    }
    manifest.setdefault("summary", {}).setdefault("outputs", {}).update(refs)
    manifest["summary"]["minutes_style"] = normalize_minutes_style(minutes_style)
    job_mod.write_manifest(job_dir, manifest)
    return {
        "ok": True,
        "job_id": job_id,
        "state": manifest.get("state"),
        "outputs": collect_job_outputs(job_id),
    }


def run_uploaded_job(
    upload_id: str,
    mode: str,
    model: str,
    confirm_live_api: bool,
    run_budget_cap_usd: float | None = None,
    minutes_style: str | None = None,
) -> dict:
    cfg = load_config()
    errors = validate_run_request(upload_id, mode, model, confirm_live_api, run_budget_cap_usd, cfg)
    if errors:
        return {"ok": False, "errors": errors}

    upload_dir = _upload_path(upload_id)
    audio_file = _uploaded_audio_path(upload_id)
    if audio_file is None:
        return {"ok": False, "errors": ["Upload is missing. Choose an audio file first."]}
    estimate = estimate_audio(audio_file, cfg)
    model_estimate = estimate.get("models", {}).get(model, {})
    timestamp = time.strftime("%Y%m%d%H%M%S")
    job_id = f"dashboard-{mode}-{timestamp}-{upload_id[:6]}"
    job_dir = _job_path(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)

    run_cfg = _build_run_config(
        mode,
        model,
        run_budget_cap_usd=run_budget_cap_usd,
        max_chunk_cost_usd=model_estimate.get("max_chunk_cost_usd"),
        minutes_style=minutes_style,
        cfg=cfg,
    )
    upload_reference = read_upload_reference(upload_dir, _storage_root())
    source_filename = (
        str(upload_reference.get("original_filename"))
        if upload_reference
        else audio_file.name
    )
    result = runner.run_job(
        job_id,
        str(audio_file),
        run_cfg,
        source_filename=source_filename,
    )
    payload = {
        "ok": result.get("state") == "completed",
        "job_id": job_id,
        "state": result.get("state"),
        "result": result,
        "outputs": collect_job_outputs(job_id),
    }
    if result.get("errors"):
        payload["errors"] = result.get("errors")
    if payload["ok"]:
        remove_upload_reference(upload_dir, _storage_root())
    return payload


def run_uploaded_quality_job(upload_id: str, confirm_live_api: bool) -> dict:
    if not confirm_live_api:
        return {"ok": False, "errors": ["Quality Transcription requires explicit live confirmation."]}
    cfg = load_config()
    upload_dir = _upload_path(upload_id)
    audio_file = _uploaded_audio_path(upload_id)
    if audio_file is None:
        return {"ok": False, "errors": ["Upload is missing. Choose an audio file first."]}
    quality_status = dashboard_status(cfg)["quality_transcription"]
    if not quality_status["runnable"]:
        reasons = [
            f"{provider}: {reason}"
            for provider, reason in quality_status["readiness"].items()
            if reason
        ]
        return {
            "ok": False,
            "errors": ["Quality Transcription providers are not all ready.", *reasons],
        }
    estimate = estimate_audio(audio_file, cfg)
    if not estimate.get("ok"):
        return {"ok": False, "errors": [str(estimate.get("error"))]}
    quality_estimate = estimate["quality_transcription"]
    if not quality_estimate["within_cap"]:
        return {
            "ok": False,
            "errors": [
                "Quality Transcription buffered paid-provider authorization "
                f"${quality_estimate['paid_provider_authorization_total_usd']:.2f} exceeds "
                f"the workspace cap ${quality_estimate['workspace_cap_usd']:.2f}."
            ],
        }
    cfg.storage_path = str(_storage_root())
    cfg.dry_run = False
    resumable_job_id = _find_resumable_quality_job(audio_file)
    if resumable_job_id:
        job_id = resumable_job_id
    else:
        timestamp = time.strftime("%Y%m%d%H%M%S")
        job_id = f"dashboard-quality-{timestamp}-{upload_id[:6]}"
    upload_reference = read_upload_reference(upload_dir, _storage_root())
    source_filename = (
        str(upload_reference.get("original_filename"))
        if upload_reference
        else audio_file.name
    )
    result = run_quality_workflow(
        job_id,
        str(audio_file),
        cfg,
        source_filename=source_filename,
    )
    result["resumed_existing_job"] = bool(resumable_job_id)
    if result.get("ok"):
        remove_upload_reference(upload_dir, _storage_root())
    return result


LEGACY_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APMA Local Dashboard</title>
  <style>
    :root {
      --ink: #17202a;
      --muted: #607080;
      --line: #d8e0e6;
      --panel: #ffffff;
      --surface: #f6f8fa;
      --blue: #1f6feb;
      --blue-dark: #1554b8;
      --green: #1a7f64;
      --amber: #b54708;
      --rose: #b42318;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--surface);
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: var(--surface); }
    button, input, select { font: inherit; }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 16px 22px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    h1 { margin: 0; font-size: 19px; }
    h2 { margin: 0; font-size: 15px; }
    p { margin: 0; }
    .sub { margin-top: 4px; color: var(--muted); font-size: 13px; }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 20px auto;
      display: grid;
      grid-template-columns: minmax(320px, 0.95fr) minmax(380px, 1.05fr);
      gap: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 12px 34px rgba(23, 32, 42, 0.08);
    }
    .head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }
    .body { padding: 16px; }
    label {
      display: block;
      margin: 0 0 8px;
      font-weight: 700;
      font-size: 13px;
      color: #2c3a46;
    }
    input, select {
      width: 100%;
      border: 1px solid #c9d3dc;
      border-radius: 8px;
      padding: 10px 11px;
      background: #fbfcfd;
    }
    input:focus, select:focus {
      outline: 3px solid rgba(31, 111, 235, 0.16);
      border-color: var(--blue);
    }
    .field { margin-bottom: 14px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .drop-zone {
      border: 2px dashed #b8c7d6;
      border-radius: 8px;
      background: #fbfcfd;
      min-height: 118px;
      padding: 16px;
      display: grid;
      place-items: center;
      text-align: center;
      cursor: pointer;
      transition: border-color 120ms ease, background 120ms ease;
    }
    .drop-zone strong {
      display: block;
      margin-bottom: 4px;
      font-size: 15px;
    }
    .drop-zone.dragging {
      border-color: var(--blue);
      background: #eef4ff;
    }
    .drop-zone .selected {
      margin-top: 8px;
      color: var(--ink);
      font-weight: 800;
      overflow-wrap: anywhere;
    }
    .mode-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
    .mode {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
      cursor: pointer;
    }
    .mode input { width: auto; margin-right: 8px; }
    .mode strong { display: inline-block; margin-bottom: 4px; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.4; }
    .project-paths {
      margin-bottom: 14px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f7fafc;
    }
    .project-paths .actions { margin-top: 8px; }
    .project-paths pre { min-height: 0; margin: 8px 0 0; padding: 10px; }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid #b7dfd4;
      background: #eaf7f2;
      color: var(--green);
      padding: 7px 9px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.warn {
      color: var(--amber);
      background: #fff8eb;
      border-color: #f8d6a2;
    }
    .cost-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    .metric {
      background: #fff;
      border: 1px solid #e1e8ee;
      border-radius: 8px;
      padding: 10px;
      min-height: 70px;
    }
    .metric strong { display: block; font-size: 20px; line-height: 1.2; }
    .metric span { display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }
    .check {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      margin-top: 12px;
      font-size: 13px;
    }
    .check input { width: auto; margin-top: 3px; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
    .btn {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 8px 12px;
      background: #edf2f7;
      color: var(--ink);
      cursor: pointer;
      font-weight: 700;
    }
    .btn:hover { background: #e2e8f0; }
    .btn.primary { background: var(--blue); color: #fff; }
    .btn.primary:hover { background: var(--blue-dark); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .notice {
      border-left: 4px solid var(--amber);
      background: #fff8eb;
      color: #5f3b00;
      border-radius: 6px;
      padding: 10px 12px;
      margin-top: 12px;
      font-size: 13px;
      line-height: 1.4;
    }
    .notice.error { border-left-color: var(--rose); background: #fff1f0; color: #7a271a; }
    .tabs { display: flex; gap: 8px; padding: 0 16px; background: #fbfcfd; border-bottom: 1px solid var(--line); }
    .tab {
      min-height: 42px;
      border: 0;
      border-bottom: 3px solid transparent;
      background: transparent;
      color: var(--muted);
      font-weight: 800;
      cursor: pointer;
    }
    .tab.active { color: var(--ink); border-bottom-color: var(--blue); }
    pre {
      margin: 0;
      min-height: 420px;
      max-height: 560px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 12px;
      line-height: 1.5;
    }
    .export-links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .export-links[hidden] { display: none; }
    .export-links a {
      color: var(--blue);
      border: 1px solid #b8c7d8;
      background: #f7faff;
      padding: 8px 10px;
      text-decoration: none;
      font-weight: 700;
      font-size: 13px;
    }
    .export-links a:hover { background: #edf4ff; }
    .hidden { display: none; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      .cost-box { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>APMA Local Dashboard</h1>
      <p class="sub">Upload audio, normalize to WAV, chunk safely, transcribe, then generate guarded live minutes.</p>
    </div>
    <span class="pill" id="keyStatus">Checking key</span>
  </header>

  <main>
    <section class="panel">
      <div class="head">
        <h2>Run Transcription</h2>
        <span class="hint">WAV MP3 M4A MP4 and more</span>
      </div>
      <div class="body">
        <div class="field">
          <label for="audioFile">Audio file</label>
          <div class="drop-zone" id="dropZone" role="button" tabindex="0" aria-label="Choose or drop supported audio file">
            <div>
              <strong>Drop audio file here</strong>
              <span class="hint">or click to choose from your computer</span>
              <div class="selected" id="selectedFileName">No file selected</div>
            </div>
          </div>
          <input id="audioFile" type="file" accept=".wav,.mp3,.m4a,.mp4,.mpeg,.mpga,.webm,.ogg,.flac,.aac,.3gp,.amr,.wma,audio/*,video/mp4">
          <p class="hint">Supported: WAV, MP3, M4A, MP4, MPEG, MPGA, WebM, OGG, FLAC, AAC, 3GP, AMR, WMA. Non-WAV files are normalized to WAV before chunking.</p>
        </div>

        <div class="field">
          <label>Run mode</label>
          <div class="mode-grid">
            <div class="mode"><strong>Provider-routed transcription</strong><br><span class="hint">OpenAI, MERaLiON, Gemini, and Qwen routes remain protected by explicit readiness and budget gates.</span></div>
          </div>
        </div>

        <div class="field">
          <label for="modelSelect">Simple Transcription provider/model</label>
          <select id="modelSelect"></select>
        </div>

        <div class="field">
          <label for="minutesStyleSelect">Minutes style</label>
          <select id="minutesStyleSelect"></select>
          <p class="hint" id="minutesStyleHint">Choose how detailed the meeting minutes should be.</p>
        </div>

        <div class="cost-box">
          <div class="metric"><strong id="durationValue">-</strong><span>duration</span></div>
          <div class="metric"><strong id="chunkValue">-</strong><span>chunks</span></div>
          <div class="metric"><strong id="costValue">$0.00</strong><span>locked run budget</span></div>
          <div class="metric"><strong id="capValue">-</strong><span>workspace max</span></div>
        </div>
        <p class="hint" id="qualityCostSummary">Upload audio to see the three-provider paid estimate and safety reserve.</p>

        <label class="check">
          <input id="confirmLive" type="checkbox">
          <span>I approve this one live transcription run using the displayed paid-provider estimate plus the configured reserve, within the workspace cap.</span>
        </label>

        <div class="actions">
          <button class="btn" id="uploadBtn">Upload and Estimate</button>
          <button class="btn" id="runBtn" disabled>Run Selected Simple Model</button>
          <button class="btn primary" id="qualityBtn" disabled>Run Fixed Quality: gptTr + gpt4oDiarz + Gem35T</button>
        </div>
        <p class="hint"><strong>Quality Transcription uses:</strong> OpenAI gpt-transcribe + OpenAI gpt-4o-transcribe-diarize + Gemini gemini-3.5-transcribe. This is independent of the Simple Transcription selection above. gpt4oDiarz and Gem35T supply provider-native speaker evidence; gptTr does not.</p>
        <p class="hint"><strong>Provider presentation:</strong> Each Quality provider keeps its own script, punctuation, formatting, and native speaker labels. APMA does not force the three transcripts to look alike.</p>

        <div id="message" class="notice" aria-live="polite">Choose an audio file, then click Upload and Estimate. The cost estimate appears here.</div>

        <hr>

        <div class="field">
          <label for="minutesModelSelect">Minutes model</label>
          <select id="minutesModelSelect"></select>
          <p class="hint">Minutes are a separate OpenAI text-generation step. Estimate first, then approve the locked budget.</p>
        </div>

        <div class="cost-box">
          <div class="metric"><strong id="minutesTokenValue">-</strong><span>estimated tokens</span></div>
          <div class="metric"><strong id="minutesCostValue">$0.00</strong><span>locked minutes budget</span></div>
          <div class="metric"><strong id="minutesCapValue">-</strong><span>minutes max</span></div>
          <div class="metric"><strong id="minutesGateValue">-</strong><span>minutes gate</span></div>
        </div>

        <label class="check">
          <input id="confirmMinutes" type="checkbox">
          <span>I approve this one OpenAI minutes generation run using the displayed budget cap.</span>
        </label>

        <div class="actions">
          <button class="btn" id="estimateMinutesBtn" disabled>Estimate Minutes</button>
          <button class="btn primary" id="generateMinutesBtn" disabled>Generate Minutes</button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="head">
        <h2>Outputs</h2>
        <span class="hint" id="jobLabel">No job yet</span>
      </div>
      <div class="tabs">
        <button class="tab active" data-tab="transcript">Transcript</button>
        <button class="tab" data-tab="minutes">Minutes</button>
        <button class="tab" data-tab="actions">Action Items</button>
        <button class="tab" data-tab="summary">Summary</button>
      </div>
      <div class="body">
        <div class="project-paths" id="projectPaths" hidden>
          <strong>Audio subproject folder addresses</strong>
          <pre id="projectPathBox"></pre>
          <div class="actions">
            <button class="btn" id="copyProjectPaths" type="button">Copy folder addresses</button>
          </div>
        </div>
        <pre id="outputBox">Run a job to see results here.</pre>
        <div class="export-links" id="exportLinks" hidden></div>
      </div>
    </section>
  </main>

  <script>
    const state = {
      status: null,
      upload: null,
      result: null,
      minutesEstimate: null,
      activeTab: "transcript"
    };

    const el = {
      keyStatus: document.getElementById("keyStatus"),
      audioFile: document.getElementById("audioFile"),
      dropZone: document.getElementById("dropZone"),
      selectedFileName: document.getElementById("selectedFileName"),
      modelSelect: document.getElementById("modelSelect"),
      minutesStyleSelect: document.getElementById("minutesStyleSelect"),
      minutesStyleHint: document.getElementById("minutesStyleHint"),
      minutesModelSelect: document.getElementById("minutesModelSelect"),
      durationValue: document.getElementById("durationValue"),
      chunkValue: document.getElementById("chunkValue"),
      costValue: document.getElementById("costValue"),
      capValue: document.getElementById("capValue"),
      qualityCostSummary: document.getElementById("qualityCostSummary"),
      minutesTokenValue: document.getElementById("minutesTokenValue"),
      minutesCostValue: document.getElementById("minutesCostValue"),
      minutesCapValue: document.getElementById("minutesCapValue"),
      minutesGateValue: document.getElementById("minutesGateValue"),
      confirmLive: document.getElementById("confirmLive"),
      confirmMinutes: document.getElementById("confirmMinutes"),
      uploadBtn: document.getElementById("uploadBtn"),
      runBtn: document.getElementById("runBtn"),
      qualityBtn: document.getElementById("qualityBtn"),
      estimateMinutesBtn: document.getElementById("estimateMinutesBtn"),
      generateMinutesBtn: document.getElementById("generateMinutesBtn"),
      message: document.getElementById("message"),
      outputBox: document.getElementById("outputBox"),
      exportLinks: document.getElementById("exportLinks"),
      projectPaths: document.getElementById("projectPaths"),
      projectPathBox: document.getElementById("projectPathBox"),
      copyProjectPaths: document.getElementById("copyProjectPaths"),
      jobLabel: document.getElementById("jobLabel")
    };

    function dollars(value) {
      if (value === null || value === undefined || value === "") return "-";
      return "$" + Number(value).toFixed(6);
    }

    function dollars2(value) {
      if (value === null || value === undefined || value === "") return "-";
      return "$" + Number(value).toFixed(2);
    }

    function secondsLabel(value) {
      if (!Number.isFinite(value)) return "-";
      if (value < 60) return value.toFixed(1) + " sec";
      return (value / 60).toFixed(2) + " min";
    }

    function showMessage(text, error = false) {
      el.message.textContent = text;
      el.message.classList.toggle("error", error);
      if (error) {
        el.message.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }

    function formatErrorPart(item) {
      if (!item) return "";
      if (typeof item === "string") return item;
      if (item.message) return item.message;
      if (item.error) return item.error;
      try {
        return JSON.stringify(item);
      } catch {
        return String(item);
      }
    }

    function formatErrorPayload(payload) {
      const parts = payload.errors || [payload.error || "Request failed"];
      const historicalJobErrors = parts.length > 0 && parts.every((item) => {
        return item && typeof item === "object" && item.ts && item.step;
      });
      if (historicalJobErrors) {
        return formatErrorPart(parts[parts.length - 1]);
      }
      return parts.map(formatErrorPart).filter(Boolean).join(" ");
    }

    function setSelectedFile(file) {
      if (!file) return;
      const lowerName = file.name.toLowerCase();
      const supported = [".wav", ".mp3", ".m4a", ".mp4", ".mpeg", ".mpga", ".webm", ".ogg", ".flac", ".aac", ".3gp", ".amr", ".wma"];
      if (!supported.some((ext) => lowerName.endsWith(ext))) {
        showMessage("This MVP supports WAV, MP3, M4A, MP4, MPEG, MPGA, WebM, OGG, FLAC, AAC, 3GP, AMR, and WMA.", true);
        return;
      }
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      el.audioFile.files = dataTransfer.files;
      el.selectedFileName.textContent = file.name;
      state.upload = null;
      el.runBtn.disabled = true;
      el.qualityBtn.disabled = true;
      showMessage("File selected. Click Upload and Estimate.");
    }

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const payload = await response.json();
      if (!response.ok) {
        const error = new Error(formatErrorPayload(payload));
        error.payload = payload;
        throw error;
      }
      return payload;
    }

    async function loadStatus() {
      state.status = await api("/api/status");
      const runnableProviders = new Set(
        state.status.models.filter((item) => item.runnable).map((item) => item.provider)
      );
      const providerCount = runnableProviders.size;
      el.keyStatus.textContent = providerCount
        ? `${providerCount} provider route${providerCount === 1 ? "" : "s"} ready`
        : "Provider setup required";
      el.keyStatus.classList.toggle("warn", providerCount === 0);
      el.modelSelect.innerHTML = state.status.models.map((item) => {
        const speakerLabel = item.diarization ? " + speaker labels" : "";
        const priceLabel = item.cost_cap_included === false
          ? item.billing_display
          : item.price_per_minute_usd === null
            ? "pricing not configured"
            : `$${item.price_per_minute_usd}/min`;
        const readinessLabel = item.runnable ? "" : " - setup required";
        const disabled = item.runnable ? "" : " disabled";
        return `<option value="${item.model}"${disabled}>${item.provider_label} | ${item.tier_label}: ${item.model}${speakerLabel} - ${priceLabel}${readinessLabel}</option>`;
      }).join("");
      const runnableModel = state.status.models.find((item) => item.runnable);
      if (runnableModel) el.modelSelect.value = runnableModel.model;
      el.minutesStyleSelect.innerHTML = state.status.minutes_styles.map((item) => {
        return `<option value="${item.key}">${item.label}</option>`;
      }).join("");
      el.minutesStyleSelect.value = state.status.default_minutes_style || "standard";
      el.minutesModelSelect.innerHTML = state.status.minutes_models.map((item) => {
        return `<option value="${item.model}">${item.label}: ${item.model}</option>`;
      }).join("");
      refreshMinutesStyleHint();
      el.capValue.textContent = dollars(state.status.max_cost_per_job_usd);
      el.minutesCapValue.textContent = dollars(state.status.max_minutes_cost_per_job_usd);
      refreshMinutesControls();
    }

    function refreshMinutesStyleHint() {
      const styles = (state.status && state.status.minutes_styles) || [];
      const selected = styles.find((item) => item.key === el.minutesStyleSelect.value);
      el.minutesStyleHint.textContent = selected ? selected.description : "Choose how detailed the meeting minutes should be.";
    }

    function refreshEstimate() {
      const estimate = state.upload && state.upload.estimate;
      const quality = state.status && state.status.quality_transcription;
      const qualityEstimate = estimate && estimate.quality_transcription;
      const selectedSimpleModel = el.modelSelect.value;
      const resumableQuality = Boolean(
        state.result && !state.result.ok &&
        String(state.result.job_id || "").startsWith("dashboard-quality-")
      );
      el.runBtn.textContent = selectedSimpleModel
        ? `Run Simple with ${selectedSimpleModel}`
        : "Run Selected Simple Model";
      el.qualityBtn.textContent = resumableQuality
        ? "Resume Quality — reuse completed chunks"
        : "Run Fixed Quality: gptTr + gpt4oDiarz + Gem35T";
      el.qualityBtn.disabled = !estimate || !estimate.ok || !quality || !quality.runnable || !qualityEstimate || !qualityEstimate.within_cap;
      if (!estimate || !estimate.ok) {
        el.durationValue.textContent = "-";
        el.chunkValue.textContent = "-";
        el.costValue.textContent = "-";
        el.qualityCostSummary.textContent = "Upload audio to see the three-provider paid estimate and safety reserve.";
        el.runBtn.disabled = true;
        return;
      }
      const model = el.modelSelect.value;
      const modelEstimate = estimate.models[model];
      const modelStatus = state.status && state.status.models.find((item) => item.model === model);
      if (!modelEstimate) {
        el.runBtn.disabled = true;
        showMessage("Selected model is not available for this estimate.", true);
        return;
      }
      el.durationValue.textContent = secondsLabel(estimate.duration_seconds);
      el.chunkValue.textContent = String(modelEstimate.chunk_count || estimate.chunk_count || "-");
      el.costValue.textContent = dollars(modelEstimate.run_budget_cap_usd);
      el.qualityCostSummary.textContent = `Fixed Quality providers: gptTr + gpt4oDiarz + Gem35T · Quality chunks: ${qualityEstimate.chunk_count} · Paid estimate: ${dollars2(qualityEstimate.paid_provider_estimated_total_usd)} · ${qualityEstimate.paid_provider_cost_buffer_percent}% reserve included · Approval budget: ${dollars2(qualityEstimate.paid_provider_authorization_total_usd)} · Workspace cap: ${dollars2(qualityEstimate.workspace_cap_usd)}`;
      if (!modelStatus || !modelStatus.runnable) {
        el.runBtn.disabled = true;
        showMessage((modelStatus && modelStatus.readiness_reason) || "Selected provider route is not ready.", true);
        return;
      }
      if (modelStatus.provider === "openai" && !state.status.openai_key_available) {
        el.runBtn.disabled = true;
        showMessage("Server has no OpenAI API key. Restart the dashboard with the key set in PowerShell.", true);
        return;
      }
      if (modelStatus.provider === "openai" && !state.status.live_enabled_env) {
        el.runBtn.disabled = true;
        showMessage("Live OpenAI gate is not enabled on the server. Restart with ENABLE_LIVE_OPENAI_TRANSCRIPTION=true.", true);
        return;
      }
      if (!modelEstimate.within_cap) {
        el.runBtn.disabled = true;
        showMessage("This estimate is above the workspace maximum. Choose a cheaper model, shorter WAV, or raise MAX_COST_PER_JOB_USD intentionally.", true);
        return;
      }
      el.runBtn.disabled = false;
    }

    function clearMinutesEstimate() {
      state.minutesEstimate = null;
      el.minutesTokenValue.textContent = "-";
      el.minutesCostValue.textContent = "-";
      el.generateMinutesBtn.disabled = true;
    }

    function refreshMinutesControls() {
      const hasTranscript = Boolean(state.result && state.result.job_id && state.result.outputs && state.result.outputs.full_transcript_txt);
      el.estimateMinutesBtn.disabled = !hasTranscript;
      el.minutesGateValue.textContent = state.status && state.status.live_minutes_enabled_env ? "enabled" : "off";
      if (!state.minutesEstimate || !state.minutesEstimate.ok) {
        el.generateMinutesBtn.disabled = true;
        return;
      }
      el.minutesTokenValue.textContent = String(state.minutesEstimate.estimated_total_tokens || "-");
      el.minutesCostValue.textContent = dollars(state.minutesEstimate.run_budget_cap_usd);
      el.generateMinutesBtn.disabled = !hasTranscript || !state.minutesEstimate.within_cap;
    }

    async function uploadAudio() {
      const file = el.audioFile.files && el.audioFile.files[0];
      if (!file) {
        showMessage("Choose a supported audio file first.", true);
        return;
      }
      const form = new FormData();
      form.append("audio", file);
      showMessage("Uploading locally and estimating cost...");
      el.uploadBtn.disabled = true;
      el.runBtn.disabled = true;
      el.qualityBtn.disabled = true;
      try {
        state.upload = await api("/api/upload", { method: "POST", body: form });
        state.result = null;
        clearMinutesEstimate();
        if (!state.upload.estimate.ok) {
          showMessage(state.upload.estimate.error, true);
        } else {
          showMessage("Paid-provider spend is locked from the current estimate. Confirm before running the selected live transcription route.");
        }
        refreshEstimate();
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        el.uploadBtn.disabled = false;
      }
    }

    async function runJob() {
      if (!state.upload) {
        showMessage("Upload and estimate a WAV file first.", true);
        return;
      }
      const modelEstimate = state.upload.estimate.models[el.modelSelect.value];
      const modelStatus = state.status && state.status.models.find((item) => item.model === el.modelSelect.value);
      if (!modelEstimate) {
        showMessage("Selected model is not available for this upload. Upload and estimate again.", true);
        return;
      }
      if (!modelStatus || !modelStatus.runnable) {
        showMessage((modelStatus && modelStatus.readiness_reason) || "Selected provider route is not ready.", true);
        return;
      }
      if (!modelEstimate.within_cap) {
        showMessage("This estimate is above the workspace maximum, so it will not run.", true);
        return;
      }
      if (!el.confirmLive.checked) {
        showMessage("Confirm this one live transcription run before starting.", true);
        return;
      }
      showMessage("Running live transcription with the displayed budget cap. This may take a moment...");
      el.runBtn.disabled = true;
      try {
        state.result = await api("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            upload_id: state.upload.upload_id,
            mode: "live_openai",
            model: el.modelSelect.value,
            minutes_style: el.minutesStyleSelect.value,
            confirm_live_api: el.confirmLive.checked,
            run_budget_cap_usd: modelEstimate.run_budget_cap_usd
          })
        });
        el.jobLabel.textContent = state.result.job_id + " - " + state.result.state;
        clearMinutesEstimate();
        refreshMinutesControls();
        showMessage(state.result.ok ? "Job completed." : "Job failed.", !state.result.ok);
        renderOutput();
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        refreshEstimate();
      }
    }

    async function runQualityJob() {
      if (!state.upload) {
        showMessage("Upload and estimate an audio file first.", true);
        return;
      }
      const quality = state.status && state.status.quality_transcription;
      if (!quality || !quality.runnable) {
        showMessage((quality && quality.readiness_reason) || "Quality Transcription requires gptTr, gpt4oDiarz, and Gem35T to be ready.", true);
        return;
      }
      const qualityEstimate = state.upload.estimate.quality_transcription;
      if (!qualityEstimate || !qualityEstimate.within_cap) {
        showMessage("The paid-provider estimate plus 15% reserve exceeds the $5.00 workspace cap, so Quality Transcription will not run.", true);
        return;
      }
      if (!el.confirmLive.checked) {
        showMessage("Confirm this one live Quality Transcription run before starting.", true);
        return;
      }
      const isResume = Boolean(
        state.result && !state.result.ok &&
        String(state.result.job_id || "").startsWith("dashboard-quality-")
      );
      showMessage(isResume
        ? "Resuming the same Quality job. Completed chunks will be reused, not transcribed again."
        : `Running Quality Transcription with paid estimate ${dollars2(qualityEstimate.paid_provider_estimated_total_usd)} plus ${qualityEstimate.paid_provider_cost_buffer_percent}% reserve; approval budget ${dollars2(qualityEstimate.paid_provider_authorization_total_usd)} under the ${dollars2(qualityEstimate.workspace_cap_usd)} workspace cap. This may take several minutes...`
      );
      el.runBtn.disabled = true;
      el.qualityBtn.disabled = true;
      try {
        state.result = await api("/api/quality/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            upload_id: state.upload.upload_id,
            confirm_live_api: el.confirmLive.checked
          })
        });
        el.jobLabel.textContent = state.result.job_id + " - " + state.result.state;
        clearMinutesEstimate();
        refreshMinutesControls();
        showMessage(state.result.ok ? "Quality Transcription automated stages completed." : "Quality Transcription stopped before completion.", !state.result.ok);
        renderOutput();
      } catch (error) {
        if (error.payload && error.payload.job_id) {
          state.result = error.payload;
          el.jobLabel.textContent = state.result.job_id + " - " + state.result.state;
          renderOutput();
        }
        showMessage(error.message + " Any completed chunks are retained. Click Resume Quality later; APMA will reuse them.", true);
      } finally {
        refreshEstimate();
      }
    }

    async function estimateMinutes() {
      if (!state.result || !state.result.job_id) {
        showMessage("Run transcription first, then estimate minutes.", true);
        return;
      }
      showMessage("Estimating live minutes cost...");
      el.estimateMinutesBtn.disabled = true;
      try {
        state.minutesEstimate = await api("/api/minutes/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            job_id: state.result.job_id,
            model: el.minutesModelSelect.value,
            minutes_style: el.minutesStyleSelect.value
          })
        });
        refreshMinutesControls();
        if (state.minutesEstimate.within_cap) {
          showMessage("Minutes budget is locked from the current estimate. Confirm before generating live minutes.");
        } else {
          showMessage("Minutes estimate is above the configured maximum. Choose a cheaper model/style or raise MAX_MINUTES_COST_PER_JOB_USD intentionally.", true);
        }
      } catch (error) {
        clearMinutesEstimate();
        showMessage(error.message, true);
      } finally {
        refreshMinutesControls();
      }
    }

    async function generateMinutes() {
      if (!state.result || !state.result.job_id) {
        showMessage("Run transcription first.", true);
        return;
      }
      if (!state.minutesEstimate || !state.minutesEstimate.ok) {
        showMessage("Estimate minutes first.", true);
        return;
      }
      if (!el.confirmMinutes.checked) {
        showMessage("Confirm this one OpenAI minutes generation run before starting.", true);
        return;
      }
      showMessage("Generating live minutes with the displayed budget cap...");
      el.generateMinutesBtn.disabled = true;
      try {
        const payload = await api("/api/minutes/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            job_id: state.result.job_id,
            model: el.minutesModelSelect.value,
            minutes_style: el.minutesStyleSelect.value,
            confirm_live_minutes: el.confirmMinutes.checked,
            run_budget_cap_usd: state.minutesEstimate.run_budget_cap_usd
          })
        });
        state.result = payload;
        el.jobLabel.textContent = state.result.job_id + " - " + state.result.state;
        state.activeTab = "minutes";
        document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === "minutes"));
        showMessage("Live minutes completed.");
        renderOutput();
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        refreshMinutesControls();
      }
    }

    function renderTranscriptExports(outputs) {
      el.exportLinks.replaceChildren();
      el.exportLinks.hidden = true;
      if (state.activeTab !== "transcript") return;
      const links = outputs.transcript_exports || {};
      const definitions = [
        ["html", "Open transcript HTML", true],
        ["srt", "Download SRT", false],
        ["vtt", "Download VTT", false]
      ];
      definitions.forEach(([format, label, openNew]) => {
        if (!links[format]) return;
        const anchor = document.createElement("a");
        anchor.href = links[format];
        anchor.textContent = label;
        if (openNew) {
          anchor.target = "_blank";
          anchor.rel = "noopener";
        } else {
          anchor.download = "";
        }
        el.exportLinks.appendChild(anchor);
      });
      el.exportLinks.hidden = el.exportLinks.childElementCount === 0;
    }

    function renderOutput() {
      if (!state.result) {
        el.exportLinks.hidden = true;
        el.projectPaths.hidden = true;
        return;
      }
      const outputs = state.result.outputs || {};
      const paths = outputs.project_paths || state.result.project_paths || {};
      const pathLines = [
        ["Subproject folder", paths.subproject_folder],
        ["Original audio folder", paths.original_folder],
        ["MP3 folder", paths.mp3_folder],
        ["MP3 chunks folder", paths.mp3_chunks_folder]
      ].filter((item) => item[1]);
      el.projectPathBox.textContent = pathLines.map((item) => `${item[0]}: ${item[1]}`).join("\n");
      el.projectPaths.hidden = pathLines.length === 0;
      if (state.result.quality) {
        const quality = state.result.quality;
        el.outputBox.textContent = JSON.stringify({
          state: state.result.state,
          stages: quality.stages,
          provider_calls: quality.provider_calls,
          artifacts: outputs,
          review_required_count: state.result.review_required_count
        }, null, 2);
        el.exportLinks.replaceChildren();
        if (state.result.review_url) {
          const review = document.createElement("a");
          review.href = state.result.review_url;
          review.textContent = "Open Human Review";
          review.target = "_blank";
          review.rel = "noopener";
          el.exportLinks.appendChild(review);
        }
        el.exportLinks.hidden = el.exportLinks.childElementCount === 0;
        return;
      }
      if (state.activeTab === "transcript") {
        el.outputBox.textContent = outputs.full_transcript_txt || "No transcript output.";
      } else if (state.activeTab === "minutes") {
        el.outputBox.textContent = outputs.minutes_md || "No minutes output.";
      } else if (state.activeTab === "actions") {
        el.outputBox.textContent = JSON.stringify(outputs.action_items_json || {}, null, 2);
      } else {
        el.outputBox.textContent = JSON.stringify(outputs.job_summary_json || outputs.manifest || {}, null, 2);
      }
      renderTranscriptExports(outputs);
    }

    el.copyProjectPaths.addEventListener("click", async () => {
      if (!el.projectPathBox.textContent) return;
      try {
        await navigator.clipboard.writeText(el.projectPathBox.textContent);
        showMessage("Folder addresses copied.");
      } catch {
        showMessage("Copy was blocked by the browser. Select the folder addresses above and copy them manually.", true);
      }
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
        tab.classList.add("active");
        state.activeTab = tab.dataset.tab;
        renderOutput();
      });
    });

    el.modelSelect.addEventListener("change", () => {
      refreshEstimate();
      showMessage(`Simple Transcription will use ${el.modelSelect.value}. The fixed Quality provider set is unchanged.`);
    });
    el.minutesStyleSelect.addEventListener("change", () => {
      refreshMinutesStyleHint();
      clearMinutesEstimate();
      refreshMinutesControls();
    });
    el.minutesModelSelect.addEventListener("change", () => {
      clearMinutesEstimate();
      refreshMinutesControls();
    });
    el.audioFile.addEventListener("change", () => {
      const file = el.audioFile.files && el.audioFile.files[0];
      if (file) {
        el.selectedFileName.textContent = file.name;
        state.upload = null;
        state.result = null;
        clearMinutesEstimate();
        el.runBtn.disabled = true;
        el.qualityBtn.disabled = true;
        showMessage("File selected. Click Upload and Estimate.");
      }
    });
    el.dropZone.addEventListener("click", () => el.audioFile.click());
    el.dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        el.audioFile.click();
      }
    });
    el.dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      el.dropZone.classList.add("dragging");
    });
    el.dropZone.addEventListener("dragleave", () => {
      el.dropZone.classList.remove("dragging");
    });
    el.dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      el.dropZone.classList.remove("dragging");
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      setSelectedFile(file);
    });
    el.uploadBtn.addEventListener("click", uploadAudio);
    el.runBtn.addEventListener("click", runJob);
    el.qualityBtn.addEventListener("click", runQualityJob);
    el.estimateMinutesBtn.addEventListener("click", estimateMinutes);
    el.generateMinutesBtn.addEventListener("click", generateMinutes);
    loadStatus().catch((error) => showMessage(error.message, true));
  </script>
</body>
</html>"""


# Keep the professional UI independently reviewable while the Python service owns
# all job, cost-control, evidence, and provider behaviour.
DASHBOARD_HTML = (REPO_ROOT / "web" / "dashboard.html").read_text(encoding="utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        job_id = (parse_qs(parsed.query).get("job_id") or [None])[0]
        if path in ("/", "/index.html"):
            _html_response(self, DASHBOARD_HTML)
            return
        if path in ("/review", "/review/"):
            _html_response(self, REVIEW_HTML)
            return
        if path == "/api/status":
            _json_response(self, 200, dashboard_status())
            return
        if path == "/api/health":
            _json_response(self, 200, dashboard_health())
            return
        if path == "/api/jobs":
            raw_limit = (parse_qs(parsed.query).get("limit") or ["24"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                limit = 24
            _json_response(self, 200, list_dashboard_jobs(limit))
            return
        if path == "/api/pilot/summary":
            cohort_code = (parse_qs(parsed.query).get("cohort_code") or [None])[0]
            try:
                _json_response(self, 200, dashboard_pilot_summary(cohort_code))
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc), "errors": [str(exc)]})
            return
        if path == "/api/review":
            try:
                _json_response(self, 200, _review_payload(job_id))
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc), "errors": [str(exc)]})
            return
        if path == "/api/quality/reconcile/estimate":
            try:
                _json_response(
                    self,
                    200,
                    estimate_job_reconciliation(str(job_id or "")),
                )
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc), "errors": [str(exc)]})
            return
        review_window_id = _match_review_audio_request(path)
        if review_window_id is not None:
            try:
                _review_audio_response(
                    self, _review_workspace_from_environment(job_id), review_window_id
                )
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc), "errors": [str(exc)]})
            return
        dashboard_job_request = _match_dashboard_job_request(path)
        if dashboard_job_request is not None:
            requested_job_id, request_kind = dashboard_job_request
            try:
                if request_kind == "audio":
                    audio_path = _job_audio_path(requested_job_id)
                    if audio_path is None:
                        _json_response(self, 404, {"error": "Job audio is unavailable"})
                    else:
                        _media_response(self, audio_path)
                else:
                    _json_response(self, 200, dashboard_job(requested_job_id))
            except (FileNotFoundError, ValueError) as exc:
                _json_response(self, 404, {"error": str(exc), "errors": [str(exc)]})
            return
        export_request = _match_transcript_export_request(path)
        if export_request is not None:
            export_path, spec = export_request
            if export_path.is_file():
                _file_response(self, export_path, spec)
                return
        _json_response(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query_job_id = (parse_qs(parsed.query).get("job_id") or [None])[0]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            if path == "/api/upload":
                payload = save_uploaded_audio(self.headers.get("Content-Type", ""), body)
                _json_response(self, 200, payload)
                return
            if path == "/api/pilot/evidence":
                data = json.loads(body.decode("utf-8"))
                job_id = str(data.pop("job_id", query_job_id or ""))
                payload = save_dashboard_pilot_evidence(job_id, data)
                _json_response(self, 200, payload)
                return
            if path == "/api/review/decisions":
                data = json.loads(body.decode("utf-8"))
                job_id = str(data.get("job_id") or query_job_id or "") or None
                workspace = _review_workspace_from_environment(job_id)
                decision = workspace.save_decision(
                    window_id=str(data.get("window_id", "")),
                    decision_type=str(data.get("decision_type", "")),
                    selected_provider=(
                        None
                        if data.get("selected_provider") is None
                        else str(data.get("selected_provider"))
                    ),
                    final_text=(
                        None if data.get("final_text") is None else str(data.get("final_text"))
                    ),
                )
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "decision": decision,
                        "review": _review_payload(job_id),
                    },
                )
                return
            if path == "/api/review/speaker-mappings":
                data = json.loads(body.decode("utf-8"))
                job_id = str(data.get("job_id") or query_job_id or "") or None
                mapping_store = _speaker_mapping_store(job_id)
                if mapping_store is None:
                    raise ValueError("Speaker evidence is not attached to this quality job")
                mapping = mapping_store.save_mapping(
                    local_speaker_key=str(data.get("local_speaker_key") or ""),
                    display_name=str(data.get("display_name") or ""),
                    canonical_speaker_id=(
                        None
                        if data.get("canonical_speaker_id") is None
                        else str(data.get("canonical_speaker_id"))
                    ),
                )
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "mapping": mapping,
                        "speaker_mappings": mapping_store.view(),
                    },
                )
                return
            if path == "/api/quality/run":
                data = json.loads(body.decode("utf-8"))
                payload = run_uploaded_quality_job(
                    upload_id=str(data.get("upload_id", "")),
                    confirm_live_api=bool(data.get("confirm_live_api", False)),
                )
                _json_response(self, 200 if payload.get("ok") else 400, payload)
                return
            if path == "/api/quality/reconcile/run":
                data = json.loads(body.decode("utf-8"))
                payload = run_job_reconciliation(
                    job_id=str(data.get("job_id") or query_job_id or ""),
                    confirm_live_reconciliation=bool(
                        data.get("confirm_live_reconciliation", False)
                    ),
                    run_budget_cap_usd=(
                        None
                        if data.get("run_budget_cap_usd") is None
                        else float(data.get("run_budget_cap_usd"))
                    ),
                )
                _json_response(self, 200 if payload.get("ok") else 400, payload)
                return
            if path == "/api/run":
                data = json.loads(body.decode("utf-8"))
                payload = run_uploaded_job(
                    upload_id=str(data.get("upload_id", "")),
                    mode=str(data.get("mode", LIVE_MODE)),
                    model=str(data.get("model", "")),
                    confirm_live_api=bool(data.get("confirm_live_api", False)),
                    run_budget_cap_usd=None if data.get("run_budget_cap_usd") is None else float(data.get("run_budget_cap_usd")),
                    minutes_style=str(data.get("minutes_style", "standard")),
                )
                _json_response(self, 200 if payload.get("ok") else 400, payload)
                return
            if path == "/api/minutes/estimate":
                data = json.loads(body.decode("utf-8"))
                payload = estimate_job_minutes(
                    job_id=str(data.get("job_id", "")),
                    model=str(data.get("model", "")),
                    minutes_style=str(data.get("minutes_style", "standard")),
                )
                _json_response(self, 200 if payload.get("ok") else 400, payload)
                return
            if path == "/api/minutes/generate":
                data = json.loads(body.decode("utf-8"))
                payload = generate_job_minutes(
                    job_id=str(data.get("job_id", "")),
                    model=str(data.get("model", "")),
                    minutes_style=str(data.get("minutes_style", "standard")),
                    confirm_live_minutes=bool(data.get("confirm_live_minutes", False)),
                    run_budget_cap_usd=None if data.get("run_budget_cap_usd") is None else float(data.get("run_budget_cap_usd")),
                )
                _json_response(self, 200 if payload.get("ok") else 400, payload)
                return
            _json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            _json_response(self, 400, {"error": str(exc), "errors": [str(exc)]})


def main() -> int:
    host = os.environ.get("DASHBOARD_HOST", DEFAULT_HOST)
    port = int(os.environ.get("DASHBOARD_PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    open_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"APMA Local Dashboard: http://{open_host}:{port}")
    print("Live OpenAI transcription and minutes require explicit UI confirmation, a server API key, and displayed budget caps.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Dashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
