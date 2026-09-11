"""Lean manifest-backed coordinator for the proven APMA quality workflow."""

from __future__ import annotations

from copy import copy, deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
from typing import Any, Callable

from services import audio_project as audio_project_mod
from services import chunker, ingest, job as job_mod, preprocess
from services.config import Config, paid_cost_authorization_amount
from services.final_draft import build_final_draft, write_final_draft_artifacts
from services.human_review import ReviewWorkspace
from services.speaker_overlay import attach_speaker_overlay
from services.speaker_timeline import build_speaker_timeline, write_speaker_timeline
from services.selective_rescue import (
    PROVIDER_ORDER,
    QUALITY_PROVIDER_MODELS,
    build_review_comparison_from_regions,
    build_rescue_comparison,
    build_rescue_windows,
    configure_quality_chunking,
    configure_live_provider,
    estimate_live_costs,
    run_live_provider_rescue,
    write_rescue_artifacts,
)
from services.transcript_agreement import classify_comparison
from services.transcript_alignment import build_comparison, comparison_html
from services.transcription import get_transcriber
from services.transcription.router import get_runtime_model_status
from services.storage_retention import cleanup_completed_working_audio


QUALITY_STAGES = (
    "INGEST",
    "CHUNK",
    "TRANSCRIBE",
    "COMPARE",
    "RESCUE",
    "FINAL_DRAFT",
    "REVIEW",
)
QUALITY_AUTO_RESCUE_MAX_WINDOWS = 8
QUALITY_RESCUE_HARD_MAX_WINDOWS = 64


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _quality(manifest: dict[str, Any]) -> dict[str, Any]:
    quality = manifest.setdefault(
        "quality",
        {
            "schema_version": "apma.quality-workflow.v1",
            "stage_order": list(QUALITY_STAGES),
            "stages": {stage: {"status": "pending"} for stage in QUALITY_STAGES},
            "stage_history": [],
            "providers": {},
            "outputs": {},
            "provider_calls": {"reused": 0, "new": 0},
            "overall_status": "running",
        },
    )
    return quality


def _stage_completed(manifest: dict[str, Any], stage: str) -> bool:
    return _quality(manifest)["stages"].get(stage, {}).get("status") == "completed"


def _stage_start(job_dir: Path, manifest: dict[str, Any], stage: str) -> None:
    quality = _quality(manifest)
    quality["current_stage"] = stage
    quality["stages"][stage] = {"status": "running", "started_at": _utc_now()}
    quality["stage_history"].append({"stage": stage, "event": "started"})
    manifest["state"] = f"quality_{stage.lower()}"
    manifest["updated_at"] = _utc_now()
    job_mod.write_manifest(job_dir, manifest)


def _stage_complete(
    job_dir: Path, manifest: dict[str, Any], stage: str, details: dict[str, Any] | None = None
) -> None:
    quality = _quality(manifest)
    entry = quality["stages"].setdefault(stage, {})
    entry.update({"status": "completed", "completed_at": _utc_now()})
    if details:
        entry.update(details)
    quality["stage_history"].append({"stage": stage, "event": "completed"})
    manifest["updated_at"] = _utc_now()
    job_mod.write_manifest(job_dir, manifest)


def _stage_failed(job_dir: Path, manifest: dict[str, Any], stage: str, exc: Exception) -> dict:
    quality = _quality(manifest)
    quality["stages"].setdefault(stage, {}).update(
        {"status": "failed", "failed_at": _utc_now(), "error": str(exc)}
    )
    quality["overall_status"] = "failed"
    quality["stage_history"].append({"stage": stage, "event": "failed"})
    manifest["state"] = "failed"
    manifest.setdefault("errors", []).append(
        {"ts": _utc_now(), "step": stage, "type": type(exc).__name__, "message": str(exc)}
    )
    manifest["updated_at"] = _utc_now()
    job_mod.write_manifest(job_dir, manifest)
    return quality_result(manifest)


def _wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as wav_file:
        return float(wav_file.getnframes()) / float(wav_file.getframerate())


def _retained_provider_artifacts(
    job_id: str,
    provider_code: str,
    results: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, str]:
    if not results:
        raise ValueError(f"{provider_code} returned no transcript results")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{provider_code}.json"
    html_path = output_dir / f"{provider_code}.html"
    payload = {
        "schema_version": "apma.retained-transcript.v1",
        "job_id": job_id,
        "provenance": {
            "provider": results[0].get("provider"),
            "provider_code": provider_code,
            "requested_model": results[0].get("model"),
            "run_ids": sorted(
                {str(result.get("run_id")) for result in results if result.get("run_id")}
            ),
        },
        "text": "\n\n".join(str(result.get("text") or "") for result in results),
        "presentation_policy": "provider_native_preserved",
        "chunks": results,
    }
    _atomic_json(json_path, payload)
    chunk_sections = []
    for result in results:
        turns = []
        for segment in result.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            speaker = str(
                segment.get("provider_speaker")
                or segment.get("speaker")
                or "UNLABELLED BY PROVIDER"
            )
            start = segment.get("start_offset", segment.get("start_sec"))
            end = segment.get("end_offset", segment.get("end_sec"))
            timing = f"{start or '?'} to {end or '?'}"
            turns.append(
                '<div class="turn"><strong>'
                + html.escape(speaker)
                + "</strong> <span>"
                + html.escape(timing)
                + "</span><pre>"
                + html.escape(str(segment.get("text") or ""))
                + "</pre></div>"
            )
        section = (
            "<section><h2>"
            + html.escape(str(result.get("chunk_filename") or "Audio chunk"))
            + "</h2>"
        )
        if turns:
            section += '<h3>Provider-native speaker turns</h3>' + "".join(turns)
        section += (
            '<h3>Provider transcript presentation</h3><pre class="transcript">'
            + html.escape(str(result.get("provider_text") or result.get("text") or ""))
            + "</pre></section>"
        )
        chunk_sections.append(section)
    _atomic_text(
        html_path,
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
        + html.escape(provider_code)
        + " retained transcript</title><style>body{font-family:Arial,sans-serif;margin:24px;line-height:1.45}section{border-top:1px solid #bbb;padding:14px 0}.turn{border-left:4px solid #1f6feb;background:#f6f8fa;padding:9px;margin:8px 0}.turn span{color:#57606a}pre{white-space:pre-wrap;word-break:break-word}</style></head><body><h1>"
        + html.escape(provider_code)
        + "</h1><p>Provider presentation and native speaker labels are preserved. APMA does not rename speakers or force this transcript to match another provider.</p>"
        + "".join(chunk_sections)
        + "</body></html>",
    )
    return {"json": str(json_path), "html": str(html_path)}


def attach_gem35t_speaker_evidence(
    job_dir: Path, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Attach retained Gem35T word/speaker annotations without provider calls."""

    job_dir = Path(job_dir)
    manifest = manifest or job_mod.read_manifest(job_dir)
    quality = _quality(manifest)
    provider_outputs = quality.get("outputs", {}).get("providers", {})
    gem_item = provider_outputs.get("Gem35T")
    if not isinstance(gem_item, dict):
        raise FileNotFoundError("Retained Gem35T evidence is unavailable")
    gem_path = Path(str(gem_item.get("json") or ""))
    if not gem_path.is_file():
        gem_path = job_dir / "quality" / "provider-evidence" / "Gem35T" / "Gem35T.json"
    if not gem_path.is_file():
        raise FileNotFoundError("Retained Gem35T JSON is unavailable")
    gem_payload = json.loads(gem_path.read_text(encoding="utf-8"))
    evidence_paths = []
    for provider_code in PROVIDER_ORDER:
        item = provider_outputs.get(provider_code)
        path = Path(str((item or {}).get("json") or ""))
        if path.is_file():
            evidence_paths.append(path)
    timeline = build_speaker_timeline(
        gem_payload,
        source_sha256=str(manifest.get("source", {}).get("sha256") or ""),
        quality_evidence_paths=evidence_paths,
    )
    timeline_paths = write_speaker_timeline(
        timeline, job_dir / "quality" / "speaker-evidence"
    )
    attach_speaker_overlay(
        job_dir,
        Path(timeline_paths["json"]),
        speaker_timeline_html=Path(timeline_paths["html"]),
    )
    return job_mod.read_manifest(job_dir)


def _provider_model(cfg: Config, provider_code: str) -> str:
    del cfg
    return QUALITY_PROVIDER_MODELS[provider_code]


def _provider_preflight(
    provider_code: str, chunks: list[dict[str, Any]], cfg: Config
) -> tuple[Config, float]:
    provider_cfg = configure_live_provider(cfg, provider_code)
    model = _provider_model(provider_cfg, provider_code)
    status = get_runtime_model_status(model, provider_cfg)
    if not status["runnable"]:
        raise RuntimeError(f"{provider_code} is not runnable: {status['readiness_reason']}")
    estimate = sum(
        math.ceil(float(chunk["end_sec"]) - float(chunk["start_sec"]))
        / 60.0
        * float(status["price_per_minute_usd"])
        for chunk in chunks
    )
    authorization = paid_cost_authorization_amount(estimate, cfg)
    if (
        status["cost_cap_included"]
        and authorization > float(cfg.max_cost_per_job_usd) + 1e-12
    ):
        raise RuntimeError(
            f"{provider_code} buffered authorization ${authorization:.6f} exceeds job cap "
            f"${cfg.max_cost_per_job_usd:.6f}"
        )
    return provider_cfg, estimate


def _run_fresh_provider(
    job_id: str,
    job_dir: Path,
    provider_code: str,
    chunks: list[dict[str, Any]],
    cfg: Config,
    manifest: dict[str, Any],
    transcriber_factory: Callable[[str], Any],
) -> dict[str, Any]:
    quality = _quality(manifest)
    provider_state = quality["providers"].setdefault(
        provider_code,
        {"status": "running", "chunks": [], "new_calls": 0, "reused_calls": 0},
    )
    if provider_state.get("status") == "completed":
        outputs = provider_state.get("outputs") or {}
        if all(Path(str(outputs.get(key) or "")).is_file() for key in ("json", "html")):
            return provider_state

    provider_cfg, estimate = _provider_preflight(provider_code, chunks, cfg)
    billing_status = get_runtime_model_status(
        _provider_model(provider_cfg, provider_code), provider_cfg
    )
    provider_jobs_root = job_dir / "quality" / "provider-jobs"
    provider_cfg.storage_path = str(provider_jobs_root)
    provider_cfg.max_cost_per_job_usd = float(cfg.max_cost_per_job_usd)
    provider_job_id = f"{job_id}-{provider_code.lower()}"
    provider_job_dir = provider_jobs_root / provider_job_id
    if not (provider_job_dir / "job_manifest.json").is_file():
        job_mod.create_job(provider_job_id, str(provider_jobs_root))
    transcriber = transcriber_factory(configure_live_provider(cfg, provider_code).transcription_engine)
    completed_by_filename = {
        entry["chunk_filename"]: entry
        for entry in provider_state.get("chunks") or []
        if entry.get("status") == "completed"
    }
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        filename = str(chunk["filename"])
        cached = completed_by_filename.get(filename)
        if cached:
            result_path = Path(str(cached.get("transcript_path") or ""))
            if result_path.is_file() and cached.get("chunk_sha256") == chunk.get("clip_sha256"):
                results.append(json.loads(result_path.read_text(encoding="utf-8")))
                provider_state["reused_calls"] = int(provider_state.get("reused_calls", 0)) + 1
                quality["provider_calls"]["reused"] = int(
                    quality["provider_calls"].get("reused", 0)
                ) + 1
                continue
        result = transcriber.transcribe_chunk(provider_job_id, chunk, provider_cfg)
        if result.get("chunk_sha256") != chunk.get("clip_sha256"):
            raise RuntimeError(f"{provider_code} result does not match its source chunk")
        results.append(result)
        provider_state["chunks"] = [
            entry for entry in provider_state.get("chunks") or [] if entry.get("chunk_filename") != filename
        ]
        provider_state["chunks"].append(
            {
                "chunk_filename": filename,
                "chunk_sha256": result["chunk_sha256"],
                "transcript_path": result["transcript_path"],
                "provider_artifact_path": result.get("provider_artifact_path"),
                "status": "completed",
            }
        )
        provider_state["new_calls"] = int(provider_state.get("new_calls", 0)) + 1
        quality["provider_calls"]["new"] = int(quality["provider_calls"].get("new", 0)) + 1
        job_mod.write_manifest(job_dir, manifest)
    outputs = _retained_provider_artifacts(
        job_id,
        provider_code,
        results,
        job_dir / "quality" / "provider-evidence" / provider_code,
    )
    provider_state.update(
        {
            "status": "completed",
            "model": _provider_model(provider_cfg, provider_code),
            "estimated_cost_usd": round(estimate, 6),
            "authorization_budget_usd": round(
                paid_cost_authorization_amount(estimate, cfg)
                if billing_status["cost_cap_included"]
                else 0.0,
                6,
            ),
            "billing_mode": billing_status["billing_mode"],
            "cost_cap_included": billing_status["cost_cap_included"],
            "billing_display": billing_status["billing_display"],
            "outputs": outputs,
            "mode": "live",
        }
    )
    job_mod.write_manifest(job_dir, manifest)
    return provider_state


def _validate_reused_provider(
    item: dict[str, Any], provider_code: str, source_sha256: str
) -> tuple[dict[str, Any], int]:
    manifest_path = Path(str(item.get("manifest") or ""))
    json_path = Path(str(item.get("json") or ""))
    html_path = Path(str(item.get("html") or ""))
    if not manifest_path.is_file() or not json_path.is_file() or not html_path.is_file():
        raise FileNotFoundError(f"Reused {provider_code} evidence is incomplete")
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if old_manifest.get("source", {}).get("sha256") != source_sha256:
        raise ValueError(f"Reused {provider_code} evidence belongs to a different source audio")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
    if not chunks and isinstance(payload.get("segments"), list):
        chunks = payload["segments"]
    provider_values = {
        str(payload.get("provenance", {}).get("provider_code") or ""),
        *{
            str(entry.get("provider_code") or "")
            for entry in (payload.get("chunks") or payload.get("segments") or [])
            if isinstance(entry, dict)
        },
    }
    if provider_code not in provider_values:
        raise ValueError(f"Reused evidence does not contain {provider_code} provenance")
    return payload, len(chunks)


def _reuse_provider(
    job_dir: Path,
    provider_code: str,
    item: dict[str, Any],
    source_sha256: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    _, call_count = _validate_reused_provider(item, provider_code, source_sha256)
    evidence_dir = job_dir / "quality" / "provider-evidence" / provider_code
    evidence_dir.mkdir(parents=True, exist_ok=True)
    json_target = evidence_dir / f"{provider_code}.json"
    html_target = evidence_dir / f"{provider_code}.html"
    manifest_target = evidence_dir / "source-job-manifest.json"
    shutil.copy2(item["json"], json_target)
    shutil.copy2(item["html"], html_target)
    shutil.copy2(item["manifest"], manifest_target)
    job_root_value = item.get("job_root")
    if job_root_value:
        source_raw = Path(str(job_root_value)) / "providers"
        if source_raw.is_dir():
            raw_target = evidence_dir / "raw-provider-responses"
            if raw_target.exists():
                shutil.rmtree(raw_target)
            shutil.copytree(source_raw, raw_target)
    state = {
        "status": "completed",
        "mode": "reused",
        "source_evidence_sha256": _sha256(Path(item["json"])),
        "source_manifest_sha256": _sha256(Path(item["manifest"])),
        "new_calls": 0,
        "reused_calls": call_count,
        "outputs": {"json": str(json_target), "html": str(html_target)},
    }
    quality = _quality(manifest)
    quality["providers"][provider_code] = state
    quality["provider_calls"]["reused"] = int(quality["provider_calls"].get("reused", 0)) + call_count
    job_mod.write_manifest(job_dir, manifest)
    return state


def _write_comparison_artifacts(classified: dict[str, Any], output_dir: Path) -> dict[str, str]:
    json_path = output_dir / "comparison.json"
    html_path = output_dir / "comparison.html"
    _atomic_json(json_path, classified)
    _atomic_text(html_path, comparison_html(classified))
    return {"json": str(json_path), "html": str(html_path)}


def _write_review_comparison_artifacts(
    comparison: dict[str, Any], output_dir: Path
) -> dict[str, str]:
    json_path = output_dir / "rescue_comparison.json"
    html_path = output_dir / "rescue_comparison.html"
    _atomic_json(json_path, comparison)
    _atomic_text(html_path, comparison_html(comparison))
    return {"json": str(json_path), "html": str(html_path)}


def _reuse_rescue(
    rescue_root_value: str,
    job_dir: Path,
    source_sha256: str,
    comparison: dict[str, Any],
) -> tuple[dict[str, str], int, str]:
    source_root = Path(rescue_root_value)
    source_json = source_root / "rescue_comparison.json"
    source_html = source_root / "rescue_comparison.html"
    if not source_json.is_file() or not source_html.is_file():
        raise FileNotFoundError("Reused selective-rescue artifacts are incomplete")
    rescue = json.loads(source_json.read_text(encoding="utf-8"))
    if rescue.get("source_audio", {}).get("source_audio_sha256") != source_sha256:
        raise ValueError("Reused selective-rescue evidence belongs to a different source audio")
    first_red = next(
        (region for region in comparison.get("regions") or [] if region.get("agreement_status") == "RED"),
        None,
    )
    selected = rescue.get("selected_coarse_region") or {}
    if first_red is None or (
        float(selected.get("global_start_sec", -1)) != float(first_red["global_start_sec"])
        or float(selected.get("global_end_sec", -1)) != float(first_red["global_end_sec"])
    ):
        raise ValueError("Reused rescue region does not match the current first RED region")
    target_root = job_dir / "quality" / "rescue"
    if target_root.exists():
        shutil.rmtree(target_root)
    shutil.copytree(source_root, target_root)
    calls = sum(
        int(value)
        for value in (rescue.get("statistics", {}).get("provider_calls") or {}).values()
    )
    return (
        {
            "json": str(target_root / "rescue_comparison.json"),
            "html": str(target_root / "rescue_comparison.html"),
        },
        calls,
        str(target_root / "extracted-clips" / "chunks"),
    )


def quality_result(manifest: dict[str, Any]) -> dict[str, Any]:
    quality = _quality(manifest)
    review_required_count = int(
        quality.get("stages", {}).get("REVIEW", {}).get("unresolved_windows", 0)
    )
    return {
        "ok": quality.get("overall_status") in {"REVIEW_REQUIRED", "COMPLETE"},
        "job_id": manifest.get("job_id"),
        "state": manifest.get("state"),
        "quality": deepcopy(quality),
        "outputs": deepcopy(quality.get("outputs") or {}),
        "audio_project": deepcopy(manifest.get("audio_project") or {}),
        "project_paths": audio_project_mod.project_paths(
            manifest.get("audio_project")
        ),
        "review_required_count": review_required_count,
        "review_url": quality.get("review_url"),
        "errors": deepcopy(manifest.get("errors") or []),
    }


def run_quality_workflow(
    job_id: str,
    source_path: str,
    cfg: Config,
    *,
    timeline_offset_seconds: float = 0.0,
    reuse_bundle: dict[str, Any] | None = None,
    transcriber_factory: Callable[[str], Any] = get_transcriber,
    stop_after_stage: str | None = None,
    source_filename: str | None = None,
) -> dict[str, Any]:
    """Run or resume the proven stages using the existing job manifest and artifacts."""

    job_mod.validate_job_id(job_id)
    if stop_after_stage is not None and stop_after_stage not in QUALITY_STAGES:
        raise ValueError("stop_after_stage is not a quality stage")
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Quality source audio not found: {source}")
    source_sha = _sha256(source)
    cfg = configure_quality_chunking(cfg)
    job_dir = Path(cfg.storage_path) / job_id
    manifest_path = job_dir / "job_manifest.json"
    if manifest_path.is_file():
        manifest = job_mod.read_manifest(job_dir)
        if "quality" not in manifest:
            raise ValueError("Existing job is not an APMA quality workflow")
        if manifest.get("source", {}).get("sha256") != source_sha:
            raise ValueError("Quality job cannot resume with different source audio")
        quality = _quality(manifest)
        quality["resume_count"] = int(quality.get("resume_count", 0)) + 1
        if quality.get("overall_status") in {"REVIEW_REQUIRED", "COMPLETE"}:
            manifest["updated_at"] = _utc_now()
            job_mod.write_manifest(job_dir, manifest)
            return quality_result(manifest)
    else:
        job_dir = job_mod.create_job(job_id, str(cfg.storage_path))
        manifest = job_mod.read_manifest(job_dir)
        quality = _quality(manifest)
        quality.update(
            {
                "timeline_offset_seconds": float(timeline_offset_seconds),
                "resume_count": 0,
                "reuse_mode": bool(reuse_bundle),
            }
        )
        job_mod.write_manifest(job_dir, manifest)

    try:
        if not _stage_completed(manifest, "INGEST"):
            _stage_start(job_dir, manifest, "INGEST")
            source_meta = ingest.ingest_file(str(source), job_dir)
            source_meta["original_filename"] = source_filename or source.name
            ingest.verify_ingested_source(source_meta)
            manifest["source"] = source_meta
            pre_meta = preprocess.preprocess_audio(source_meta["path"], job_dir, cfg)
            manifest["preprocess"] = pre_meta
            manifest["audio_project"] = audio_project_mod.create_audio_project(
                source_meta["path"],
                job_dir,
                cfg,
                original_filename=source_meta["original_filename"],
            )
            _stage_complete(
                job_dir,
                manifest,
                "INGEST",
                {"source_sha256": source_meta["sha256"], "duration_seconds": pre_meta["duration_seconds"]},
            )
            if stop_after_stage == "INGEST":
                return quality_result(manifest)

        if not _stage_completed(manifest, "CHUNK"):
            _stage_start(job_dir, manifest, "CHUNK")
            chunks = chunker.chunk_file(
                manifest["preprocess"]["path"], job_dir, manifest["preprocess"], cfg
            )
            for chunk in chunks:
                clip_path = job_dir / "chunks" / chunk["filename"]
                chunk["path"] = str(clip_path)
                chunk["clip_sha256"] = _sha256(clip_path)
                offset = float(_quality(manifest)["timeline_offset_seconds"])
                chunk["start_sec"] = float(chunk["start_sec"]) + offset
                chunk["end_sec"] = float(chunk["end_sec"]) + offset
                chunk["global_start_sec"] = chunk["start_sec"]
                chunk["global_end_sec"] = chunk["end_sec"]
            manifest["chunks"] = chunks
            _stage_complete(job_dir, manifest, "CHUNK", {"chunk_count": len(chunks)})
            if stop_after_stage == "CHUNK":
                return quality_result(manifest)

        if not _stage_completed(manifest, "TRANSCRIBE"):
            _stage_start(job_dir, manifest, "TRANSCRIBE")
            provider_outputs: dict[str, dict[str, str]] = {}
            fresh_provider_codes = [
                provider_code
                for provider_code in PROVIDER_ORDER
                if not (
                    reuse_bundle
                    and provider_code in (reuse_bundle.get("providers") or {})
                )
            ]
            fresh_estimates = {
                provider_code: _provider_preflight(
                    provider_code, manifest["chunks"], cfg
                )[1]
                for provider_code in fresh_provider_codes
            }
            billing_statuses = {
                provider_code: get_runtime_model_status(
                    _provider_model(
                        configure_live_provider(cfg, provider_code), provider_code
                    ),
                    configure_live_provider(cfg, provider_code),
                )
                for provider_code in fresh_provider_codes
            }
            combined_estimate = sum(
                estimate
                for provider_code, estimate in fresh_estimates.items()
                if billing_statuses[provider_code]["cost_cap_included"]
            )
            authorization_total = paid_cost_authorization_amount(
                combined_estimate, cfg
            )
            if authorization_total > float(cfg.max_cost_per_job_usd) + 1e-12:
                raise RuntimeError(
                    "Combined quality-transcription buffered authorization "
                    f"${authorization_total:.6f} "
                    f"exceeds job cap ${cfg.max_cost_per_job_usd:.6f}"
                )
            _quality(manifest)["cost_preflight"] = {
                "provider_estimates_usd": {
                    code: round(value, 6) for code, value in fresh_estimates.items()
                },
                "provider_billing": {
                    code: {
                        "billing_mode": status["billing_mode"],
                        "cost_cap_included": status["cost_cap_included"],
                        "billing_display": status["billing_display"],
                    }
                    for code, status in billing_statuses.items()
                },
                "combined_estimated_cost_usd": round(combined_estimate, 6),
                "paid_provider_estimated_total_usd": round(combined_estimate, 6),
                "paid_provider_cost_buffer_percent": float(
                    cfg.paid_provider_cost_buffer_percent
                ),
                "paid_provider_authorization_total_usd": round(
                    authorization_total, 6
                ),
                "max_cost_per_job_usd": float(cfg.max_cost_per_job_usd),
                "passed": True,
            }
            job_mod.write_manifest(job_dir, manifest)
            for provider_code in PROVIDER_ORDER:
                try:
                    if reuse_bundle and provider_code in (reuse_bundle.get("providers") or {}):
                        state = _reuse_provider(
                            job_dir,
                            provider_code,
                            reuse_bundle["providers"][provider_code],
                            source_sha,
                            manifest,
                        )
                    else:
                        state = _run_fresh_provider(
                            job_id,
                            job_dir,
                            provider_code,
                            manifest["chunks"],
                            cfg,
                            manifest,
                            transcriber_factory,
                        )
                except Exception as exc:
                    raise RuntimeError(
                        f"{provider_code} Quality provider failed: {exc}"
                    ) from exc
                provider_outputs[provider_code] = state["outputs"]
            _quality(manifest)["outputs"]["providers"] = provider_outputs
            _stage_complete(
                job_dir,
                manifest,
                "TRANSCRIBE",
                {
                    "provider_count": len(provider_outputs),
                    "combined_estimated_cost_usd": round(combined_estimate, 6),
                    "paid_provider_estimated_total_usd": round(combined_estimate, 6),
                    "paid_provider_authorization_total_usd": round(
                        authorization_total, 6
                    ),
                },
            )
            if stop_after_stage == "TRANSCRIBE":
                return quality_result(manifest)

        if not _stage_completed(manifest, "COMPARE"):
            _stage_start(job_dir, manifest, "COMPARE")
            provider_paths = {
                code: Path(_quality(manifest)["outputs"]["providers"][code]["json"])
                for code in PROVIDER_ORDER
            }
            duration = float(manifest["preprocess"]["duration_seconds"])
            offset = float(_quality(manifest)["timeline_offset_seconds"])
            comparison = build_comparison(
                provider_paths,
                timeline_offset_seconds=offset,
                recording_duration_seconds=offset + duration,
                provider_order=PROVIDER_ORDER,
            )
            comparison = classify_comparison(comparison)
            comparison_outputs = _write_comparison_artifacts(
                comparison, job_dir / "quality" / "comparison"
            )
            _quality(manifest)["outputs"]["comparison"] = comparison_outputs
            _stage_complete(
                job_dir,
                manifest,
                "COMPARE",
                {
                    "regions": len(comparison["regions"]),
                    "status_counts": comparison["agreement_analysis"]["status_counts"],
                },
            )
            if stop_after_stage == "COMPARE":
                return quality_result(manifest)

        if not _stage_completed(manifest, "RESCUE"):
            _stage_start(job_dir, manifest, "RESCUE")
            comparison_path = Path(_quality(manifest)["outputs"]["comparison"]["json"])
            comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
            has_red_region = any(
                region.get("agreement_status") == "RED"
                for region in comparison.get("regions") or []
                if isinstance(region, dict)
            )
            if not has_red_region:
                rescue_root = job_dir / "quality" / "rescue"
                rescue = build_review_comparison_from_regions(
                    Path(manifest["source"]["path"]),
                    comparison,
                    rescue_root / "review-clips",
                    cfg,
                    source_global_start_sec=float(
                        _quality(manifest)["timeline_offset_seconds"]
                    ),
                    source_sha256=_sha256(comparison_path),
                )
                rescue_outputs = _write_review_comparison_artifacts(rescue, rescue_root)
                review_audio_dir = str(rescue_root / "review-clips" / "chunks")
                rescue_mode = "not_required_no_red"
            elif reuse_bundle and reuse_bundle.get("rescue_root"):
                rescue_outputs, reused_calls, review_audio_dir = _reuse_rescue(
                    str(reuse_bundle["rescue_root"]),
                    job_dir,
                    source_sha,
                    comparison,
                )
                _quality(manifest)["provider_calls"]["reused"] += reused_calls
                rescue_mode = "reused"
            else:
                rescue_root = job_dir / "quality" / "rescue"
                selected, windows, source_identity = build_rescue_windows(
                    Path(manifest["source"]["path"]),
                    comparison,
                    rescue_root / "extracted-clips",
                    cfg,
                    source_global_start_sec=float(_quality(manifest)["timeline_offset_seconds"]),
                    max_windows=QUALITY_RESCUE_HARD_MAX_WINDOWS,
                )
                if len(windows) > QUALITY_AUTO_RESCUE_MAX_WINDOWS:
                    rescue = build_review_comparison_from_regions(
                        Path(manifest["source"]["path"]),
                        comparison,
                        rescue_root / "review-clips",
                        cfg,
                        source_global_start_sec=float(
                            _quality(manifest)["timeline_offset_seconds"]
                        ),
                        source_sha256=_sha256(comparison_path),
                        allow_red=True,
                    )
                    rescue_outputs = _write_review_comparison_artifacts(
                        rescue, rescue_root
                    )
                    review_audio_dir = str(rescue_root / "review-clips" / "chunks")
                    rescue_mode = "deferred_window_limit"
                    _quality(manifest)["rescue_deferred"] = {
                        "reason": "candidate_window_count_exceeds_auto_approval_limit",
                        "candidate_window_count": len(windows),
                        "auto_approval_limit": QUALITY_AUTO_RESCUE_MAX_WINDOWS,
                        "provider_calls": 0,
                    }
                else:
                    primary_estimate = sum(
                        float(state.get("authorization_budget_usd", 0.0))
                        for state in _quality(manifest)["providers"].values()
                        if state.get("cost_cap_included", True)
                    )
                    remaining_cap = max(
                        0.0, float(cfg.max_cost_per_job_usd) - primary_estimate
                    )
                    costs = estimate_live_costs(windows, cfg, min(5.0, remaining_cap))
                    provider_results, summaries = run_live_provider_rescue(
                        windows,
                        cfg,
                        rescue_root,
                        costs,
                        transcriber_factory=transcriber_factory,
                    )
                    rescue = build_rescue_comparison(
                        selected,
                        windows,
                        provider_results,
                        summaries,
                        costs,
                        source_identity,
                        rescue_root,
                        comparison_source_identity={
                            "sha256": _sha256(comparison_path),
                            "modified": False,
                        },
                        source_label=manifest["source"]["filename"],
                        total_processing_seconds=sum(
                            float(summary.get("processing_seconds", 0.0))
                            for summary in summaries.values()
                        ),
                    )
                    rescue_outputs = write_rescue_artifacts(rescue, rescue_root)
                    new_calls = sum(
                        int(summary["provider_calls"])
                        for summary in summaries.values()
                    )
                    _quality(manifest)["provider_calls"]["new"] += new_calls
                    review_audio_dir = str(rescue_root / "extracted-clips" / "chunks")
                    rescue_mode = "live"
            _quality(manifest)["outputs"]["rescue"] = rescue_outputs
            _quality(manifest)["review_audio_dir"] = review_audio_dir
            rescue_details = {"mode": rescue_mode}
            if rescue_mode == "deferred_window_limit":
                rescue_details.update(_quality(manifest)["rescue_deferred"])
            _stage_complete(job_dir, manifest, "RESCUE", rescue_details)
            if stop_after_stage == "RESCUE":
                return quality_result(manifest)

        if not _stage_completed(manifest, "FINAL_DRAFT"):
            _stage_start(job_dir, manifest, "FINAL_DRAFT")
            rescue_path = Path(_quality(manifest)["outputs"]["rescue"]["json"])
            rescue = json.loads(rescue_path.read_text(encoding="utf-8"))
            draft = build_final_draft(rescue, source_sha256=_sha256(rescue_path))
            draft_outputs = write_final_draft_artifacts(
                draft, job_dir / "quality" / "final-draft"
            )
            _quality(manifest)["outputs"]["final_draft"] = draft_outputs
            _stage_complete(
                job_dir,
                manifest,
                "FINAL_DRAFT",
                {
                    "total_windows": draft["statistics"]["total_windows"],
                    "review_required": draft["statistics"]["review_required"],
                },
            )
            if stop_after_stage == "FINAL_DRAFT":
                return quality_result(manifest)

        if not _stage_completed(manifest, "REVIEW"):
            _stage_start(job_dir, manifest, "REVIEW")
            manifest = attach_gem35t_speaker_evidence(job_dir, manifest)
            workspace = ReviewWorkspace(
                Path(_quality(manifest)["outputs"]["final_draft"]["json"]),
                Path(_quality(manifest)["review_audio_dir"]),
                job_dir / "quality" / "review",
            )
            reviewed_outputs = workspace.write_reviewed_outputs()
            view = workspace.view()
            _quality(manifest)["outputs"]["final_reviewed"] = reviewed_outputs
            unresolved = int(view["statistics"]["unresolved_windows"])
            overall = "REVIEW_REQUIRED" if unresolved else "COMPLETE"
            _quality(manifest)["overall_status"] = overall
            _quality(manifest)["review_url"] = f"/review?job_id={job_id}"
            manifest["state"] = overall.lower()
            _stage_complete(
                job_dir,
                manifest,
                "REVIEW",
                {"status": "completed", "unresolved_windows": unresolved},
            )
        if bool(getattr(cfg, "cleanup_completed_working_audio", True)):
            try:
                manifest["storage_retention"] = cleanup_completed_working_audio(job_dir)
            except Exception as cleanup_exc:
                manifest.setdefault("warnings", []).append(
                    {
                        "ts": _utc_now(),
                        "type": type(cleanup_exc).__name__,
                        "message": (
                            "Completed quality-job working-audio cleanup failed: "
                            f"{cleanup_exc}"
                        ),
                    }
                )
            manifest["updated_at"] = _utc_now()
            job_mod.write_manifest(job_dir, manifest)
        return quality_result(manifest)
    except Exception as exc:
        stage = str(_quality(manifest).get("current_stage") or "UNKNOWN")
        return _stage_failed(job_dir, manifest, stage, exc)


__all__ = ["QUALITY_STAGES", "quality_result", "run_quality_workflow"]
