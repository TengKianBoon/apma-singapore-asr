from pathlib import Path
import time
import datetime
from copy import copy
from typing import Any, Callable

from services import job as job_mod
from services import ingest as ingest_mod
from services import preprocess as preprocess_mod
from services import chunker as chunker_mod
from services import audio_project as audio_project_mod
from services import config as config_mod
from services import transcription as transcription_mod
from services import transcript_aggregator as transcript_aggregator_mod
from services import minutes as minutes_mod
from services.errors import JobExistsError
from services.minutes.presets import normalize_minutes_style
from services.storage_retention import cleanup_completed_working_audio
from services.transcription.models import configure_model_chunking


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def estimate_job_cost(source_meta: dict, cfg: config_mod.Config) -> float:
    # Dry-run: cost is zero
    return 0.0


def _preprocess_verified_source(
    manifest: dict,
    job_dir: Path,
    cfg: config_mod.Config,
) -> dict:
    ingest_mod.verify_ingested_source(manifest["source"])
    return preprocess_mod.preprocess_audio(manifest["source"]["path"], job_dir, cfg)


def _build_manifest_summary(manifest: dict) -> dict:
    outputs = manifest.get("outputs", {})
    transcripts = manifest.get("transcription", {}).get("transcripts", [])
    return {
        "job_state": manifest.get("state"),
        "source": manifest.get("source", {}),
        "audio_project": manifest.get("audio_project", {}),
        "ingest_qc": manifest.get("ingest_qc", {}),
        "chunk_count": len(manifest.get("chunks", [])),
        "transcript_count": len(transcripts),
        "transcript_paths": [item.get("transcript_path") for item in transcripts if item.get("transcript_path")],
        "outputs": {
            "full_transcript_json": outputs.get("full_transcript_json"),
            "full_transcript_txt": outputs.get("full_transcript_txt"),
            "full_transcript_html": outputs.get("full_transcript_html"),
            "full_transcript_srt": outputs.get("full_transcript_srt"),
            "full_transcript_vtt": outputs.get("full_transcript_vtt"),
            "minutes_md": outputs.get("minutes_md"),
            "action_items_json": outputs.get("action_items_json"),
            "job_summary_json": outputs.get("job_summary_json"),
        },
        "minutes_style": manifest.get("minutes_style"),
        "errors": manifest.get("errors", []) + manifest.get("outputs", {}).get("errors", []),
        "estimated_cost_usd": float(manifest.get("estimated_cost_usd", 0.0)),
        "actual_cost_usd": float(manifest.get("actual_cost_usd", 0.0)),
    }


def _append_error(manifest: dict, step: str, err_type: str, message: str) -> None:
    manifest.setdefault("errors", []).append({"ts": _now_iso(), "step": step, "type": err_type, "message": message})


def _apply_completed_storage_retention(
    job_dir: Path,
    manifest: dict,
    cfg: config_mod.Config,
) -> None:
    if manifest.get("state") != "completed" or not bool(
        getattr(cfg, "cleanup_completed_working_audio", True)
    ):
        return
    try:
        manifest["storage_retention"] = cleanup_completed_working_audio(job_dir)
    except Exception as exc:
        manifest.setdefault("warnings", []).append(
            {
                "ts": _now_iso(),
                "type": type(exc).__name__,
                "message": f"Completed-job working-audio cleanup failed: {exc}",
            }
        )


def _increment_attempt(manifest: dict, step: str) -> None:
    attempts = manifest.setdefault("attempts", {})
    attempts[step] = attempts.get(step, 0) + 1


def _is_transient(exc: Exception) -> bool:
    # Conservative: treat OSError/IOError as transient local I/O
    return isinstance(exc, OSError)


def run_with_retries(fn: Callable[[], Any], manifest: dict, step: str, cfg: config_mod.Config) -> Any:
    max_attempts = max(1, int(cfg.max_retries))
    attempt = 0
    backoff_base = 0.5
    while True:
        attempt += 1
        _increment_attempt(manifest, step)
        try:
            return fn()
        except Exception as e:
            if not _is_transient(e):
                raise
            if attempt >= max_attempts:
                raise
            # simple exponential backoff
            time.sleep(backoff_base * (2 ** (attempt - 1)))


def _transcription_model(cfg: config_mod.Config) -> str:
    engine = str(cfg.transcription_engine).strip().lower()
    if engine == "openai":
        return cfg.openai_model
    if engine in {"meralion", "m3asr"}:
        return cfg.meralion_transcription_model
    if engine in {"gemini", "google", "gem37f", "gem35t"}:
        return cfg.gemini_transcription_model
    return "mock"


def _can_resume_transcription(manifest: dict) -> bool:
    transcription = manifest.get("transcription", {})
    transcription_state = transcription.get("state")
    resumable_state = transcription_state in {"queued", "running"} or (
        transcription_state == "failed" and transcription.get("retryable") is True
    )
    return bool(
        manifest.get("chunks")
        and manifest.get("state") in {"failed", "transcription"}
        and resumable_state
    )


def _transcript_manifest_entry(transcript: dict) -> dict:
    return {
        "chunk_filename": transcript.get("chunk_filename") or transcript.get("chunk") or None,
        "chunk_sha256": transcript.get("chunk_sha256"),
        "transcript_path": transcript.get("transcript_path") or transcript.get("path") or None,
        "duration_seconds": transcript.get("duration_seconds", None),
        "chunk_start_sec": transcript.get("chunk_start_sec"),
        "chunk_end_sec": transcript.get("chunk_end_sec"),
        "provider": transcript.get("provider"),
        "provider_code": transcript.get("provider_code"),
        "model": transcript.get("model"),
        "requested_model": transcript.get("requested_model"),
        "resolved_model": transcript.get("resolved_model"),
        "resolved_model_version": transcript.get("resolved_model_version"),
        "request_id": transcript.get("request_id"),
        "run_id": transcript.get("run_id"),
        "response_format": transcript.get("response_format"),
        "diarized": bool(transcript.get("diarized", False)),
        "provider_artifact_path": transcript.get("provider_artifact_path"),
        "retained_output_paths": transcript.get("retained_output_paths", {}),
        "estimated_cost_usd": float(transcript.get("estimated_cost_usd", 0.0)),
        "actual_cost_usd": float(transcript.get("actual_cost_usd", 0.0)),
    }


def _run_transcription_and_outputs(
    job_id: str,
    job_dir: Path,
    cfg: config_mod.Config,
    *,
    resume: bool,
) -> dict:
    manifest = job_mod.read_manifest(job_dir)
    chunks = manifest.get("chunks", [])
    transcription = manifest.setdefault("transcription", {})
    if resume:
        transcription.setdefault("transcripts", [])
        transcription.setdefault("errors", [])
        transcription["resume_count"] = int(transcription.get("resume_count", 0)) + 1
        transcription["last_resumed_at"] = _now_iso()
    else:
        transcription.update({
            "transcripts": [],
            "errors": [],
            "resume_count": 0,
        })
    transcription.update({
        "state": "queued",
        "engine": cfg.transcription_engine,
        "model": _transcription_model(cfg),
        "retryable": False,
    })
    manifest["state"] = "transcription"
    manifest["updated_at"] = _now_iso()
    job_mod.write_manifest(job_dir, manifest)

    transcriber = transcription_mod.get_transcriber(cfg.transcription_engine)
    transcription["state"] = "running"
    job_mod.write_manifest(job_dir, manifest)

    try:
        transcripts = transcriber.transcribe_chunks(job_id, chunks, cfg)
        # The transcriber may have written per-chunk status while it ran. Re-read
        # before adding the ordered result list so those durable statuses survive.
        manifest = job_mod.read_manifest(job_dir)
        transcription = manifest.setdefault("transcription", {})
        transcription["transcripts"] = [
            _transcript_manifest_entry(transcript) for transcript in transcripts
        ]

        retained_outputs = next(
            (
                transcript.get("retained_output_paths")
                for transcript in reversed(transcripts)
                if transcript.get("retained_output_paths")
            ),
            None,
        )
        if retained_outputs:
            transcription["retained_outputs"] = retained_outputs

        transcription["state"] = "completed"
        transcription["retryable"] = False
        transcription["actual_cost_usd"] = sum(
            float(entry.get("actual_cost_usd", 0.0))
            for entry in transcription["transcripts"]
        )
        manifest["state"] = "aggregating"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)

        try:
            manifest["outputs"] = transcript_aggregator_mod.aggregate_transcripts(
                job_id,
                job_dir,
                transcription["transcripts"],
            )
            manifest["state"] = "minutes"
            manifest["updated_at"] = _now_iso()
            job_mod.write_manifest(job_dir, manifest)

            minutes_generator = minutes_mod.get_minutes_generator("mock")
            minutes_cfg = copy(cfg)
            minutes_cfg.dry_run = True
            minutes_cfg.minutes_style = normalize_minutes_style(
                getattr(minutes_cfg, "minutes_style", "standard")
            )
            manifest["outputs"].update(
                minutes_generator.generate(
                    job_id,
                    job_dir,
                    manifest["outputs"],
                    minutes_cfg,
                )
            )
            manifest["minutes_style"] = minutes_cfg.minutes_style
            manifest["state"] = "completed"
        except Exception as exc:
            manifest["outputs"] = {
                "state": "failed",
                "errors": [
                    {
                        "ts": _now_iso(),
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                ],
            }
            manifest["state"] = "failed"

        manifest["actual_cost_usd"] = transcription.get("actual_cost_usd", 0.0)
        manifest["updated_at"] = _now_iso()
        manifest["summary"] = _build_manifest_summary(manifest)
        _apply_completed_storage_retention(job_dir, manifest, cfg)
        job_mod.write_manifest(job_dir, manifest)
        result = {
            "job_id": job_id,
            "state": manifest["state"],
            "chunks": manifest.get("chunks", []),
            "transcripts": transcription["transcripts"],
            "audio_project": manifest.get("audio_project", {}),
        }
        if manifest.get("outputs"):
            result["outputs"] = manifest["outputs"]
        if manifest["state"] == "failed":
            result["errors"] = manifest.get("outputs", {}).get("errors", [])
        return result
    except Exception as exc:
        # Preserve chunk completions/failures written immediately before the
        # exception; a stale in-memory manifest would erase the resume cache.
        manifest = job_mod.read_manifest(job_dir)
        transcription = manifest.setdefault("transcription", {})
        transcription.setdefault("errors", []).append(
            {"ts": _now_iso(), "type": type(exc).__name__, "message": str(exc)}
        )
        transcription["retryable"] = any(
            entry.get("status") == "failed" and entry.get("retryable") is True
            for entry in transcription.get("chunks", [])
        )
        transcription["state"] = "failed"
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {
            "job_id": job_id,
            "state": manifest["state"],
            "errors": transcription["errors"],
        }


def run_job(
    job_id: str,
    source_path: str,
    cfg: config_mod.Config,
    *,
    source_filename: str | None = None,
) -> dict:
    job_mod.validate_job_id(job_id)
    selected_model = _transcription_model(cfg)
    if selected_model != "mock":
        cfg = configure_model_chunking(cfg, selected_model)
    storage = Path(cfg.storage_path)
    job_dir = storage / job_id

    manifest_path = job_dir / "job_manifest.json"
    if manifest_path.exists():
        manifest = job_mod.read_manifest(job_dir)
        if not _can_resume_transcription(manifest):
            raise JobExistsError(f"Job already exists: {job_id}")
        expected_engine = str(manifest.get("transcription", {}).get("engine", "")).strip().lower()
        current_engine = str(cfg.transcription_engine).strip().lower()
        expected_model = manifest.get("transcription", {}).get("model")
        current_model = _transcription_model(cfg)
        if expected_engine != current_engine or expected_model != current_model:
            raise JobExistsError(
                f"Job {job_id} cannot resume with a different transcription engine or model"
            )
        try:
            ingest_mod.verify_ingested_source(manifest.get("source", {}))
        except Exception as exc:
            _append_error(manifest, "resume", type(exc).__name__, str(exc))
            manifest["state"] = "failed"
            manifest["updated_at"] = _now_iso()
            job_mod.write_manifest(job_dir, manifest)
            return {
                "job_id": job_id,
                "state": manifest["state"],
                "errors": manifest.get("errors", []),
            }
        return _run_transcription_and_outputs(job_id, job_dir, cfg, resume=True)

    # create initial job
    job_dir = job_mod.create_job(job_id, storage_path=cfg.storage_path)
    manifest = job_mod.read_manifest(job_dir)

    # Validating
    manifest["state"] = "validating"
    manifest["updated_at"] = _now_iso()
    job_mod.write_manifest(job_dir, manifest)

    # Ingest
    try:
        source_meta = run_with_retries(lambda: ingest_mod.ingest_file(source_path, job_dir), manifest, "ingest", cfg)
        source_meta["original_filename"] = source_filename or Path(source_path).name
        manifest["source"] = source_meta
        manifest["ingest_qc"] = {
            "state": "copy_verified",
            "integrity_verified": bool(source_meta.get("copy_verified")),
            "storage_policy": source_meta.get("storage_policy"),
        }
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
    except Exception as e:
        _append_error(manifest, "ingest", type(e).__name__, str(e))
        manifest["ingest_qc"] = {
            "state": "failed",
            "integrity_verified": False,
            "failure_code": type(e).__name__,
        }
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {"job_id": job_id, "state": manifest["state"], "errors": manifest.get("errors", [])}

    # Cost preflight
    estimated = estimate_job_cost(manifest.get("source", {}), cfg)
    manifest["estimated_cost_usd"] = float(estimated)
    job_mod.write_manifest(job_dir, manifest)
    if estimated > cfg.max_cost_per_job_usd:
        _append_error(manifest, "preflight", "CostLimitExceededError", f"Estimated cost {estimated} exceeds max {cfg.max_cost_per_job_usd}")
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {"job_id": job_id, "state": manifest["state"], "errors": manifest.get("errors", [])}

    # Preprocess
    manifest["state"] = "preprocessing"
    manifest["updated_at"] = _now_iso()
    job_mod.write_manifest(job_dir, manifest)
    try:
        pre_meta = run_with_retries(
            lambda: _preprocess_verified_source(manifest, job_dir, cfg),
            manifest,
            "preprocess",
            cfg,
        )
        manifest["preprocess"] = pre_meta
        audio_qc = pre_meta.get("audio_qc", {})
        manifest["ingest_qc"] = {
            "state": "accepted",
            "integrity_verified": True,
            "storage_policy": manifest["source"].get("storage_policy"),
            "media_probe": pre_meta.get("source_probe", {}),
            "processing_signal_qc": audio_qc.get("processing_audio", {}),
            "speech_vad_performed": False,
        }
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
    except Exception as e:
        _append_error(manifest, "preprocess", type(e).__name__, str(e))
        manifest.setdefault("ingest_qc", {}).update(
            {
                "state": "rejected",
                "failure_code": type(e).__name__,
            }
        )
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {"job_id": job_id, "state": manifest["state"], "errors": manifest.get("errors", [])}

    # Retained subproject audio package
    manifest["state"] = "packaging_audio"
    manifest["updated_at"] = _now_iso()
    job_mod.write_manifest(job_dir, manifest)
    try:
        manifest["audio_project"] = run_with_retries(
            lambda: audio_project_mod.create_audio_project(
                manifest["source"]["path"],
                job_dir,
                cfg,
                original_filename=manifest["source"].get("original_filename"),
            ),
            manifest,
            "audio_project",
            cfg,
        )
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
    except Exception as e:
        _append_error(manifest, "audio_project", type(e).__name__, str(e))
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {"job_id": job_id, "state": manifest["state"], "errors": manifest.get("errors", [])}

    # Chunking
    manifest["state"] = "chunking"
    manifest["updated_at"] = _now_iso()
    job_mod.write_manifest(job_dir, manifest)
    try:
        chunks = run_with_retries(lambda: chunker_mod.chunk_file(manifest["preprocess"]["path"], job_dir, manifest["preprocess"], cfg), manifest, "chunking", cfg)
        manifest["chunks"] = chunks
        job_mod.write_manifest(job_dir, manifest)
        return _run_transcription_and_outputs(job_id, job_dir, cfg, resume=False)
    except Exception as e:
        _append_error(manifest, "chunking", type(e).__name__, str(e))
        manifest["state"] = "failed"
        manifest["updated_at"] = _now_iso()
        job_mod.write_manifest(job_dir, manifest)
        return {"job_id": job_id, "state": manifest["state"], "errors": manifest.get("errors", [])}
