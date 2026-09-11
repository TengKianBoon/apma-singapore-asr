from __future__ import annotations

import os
import time
import math
import json
import hashlib
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional, Callable, Any, List
from datetime import datetime, timezone

from services.transcription.base import Transcriber
from services.transcription.contracts import normalize_provider_timing
from services.transcription.models import get_model_spec
from services.config import Config
from services import job as job_mod
from services import errors as errors_mod
from services.text_normalization import normalize_generated_text, normalize_segment_texts


class OpenAITranscriptionRequestError(RuntimeError):
    """HTTP failure retaining status for bounded retry classification."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = int(status_code)
        super().__init__(f"OpenAI transcription request failed: HTTP {status_code}: {detail}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path_is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _cached_payload_matches(
    payload: dict[str, Any],
    manifest_entry: dict[str, Any],
    *,
    job_id: str,
    chunk_filename: str | None,
    chunk_sha256: str,
    model: str,
    provider_code: str,
    response_format: str,
) -> bool:
    expected = {
        "schema_version": "apma.transcript.chunk.v1",
        "job_id": job_id,
        "chunk_filename": chunk_filename,
        "chunk_sha256": chunk_sha256,
        "provider": "openai",
        "provider_code": provider_code,
        "model": model,
        "response_format": response_format,
    }
    if any(payload.get(field) != value for field, value in expected.items()):
        return False
    manifest_run_id = manifest_entry.get("run_id")
    if manifest_run_id and payload.get("run_id") != manifest_run_id:
        return False
    return bool(payload.get("run_id"))


def is_diarization_model(model: str | None, cfg: Config | None = None) -> bool:
    if not model:
        return False
    return get_model_spec(model, cfg or Config()).diarization


def _require_openai_model(model: str, cfg: Config):
    spec = get_model_spec(model, cfg)
    if spec.provider != "openai":
        raise ValueError(
            f"Model {model!r} belongs to {spec.provider_label} and cannot be sent to the OpenAI adapter."
        )
    return spec


def _openai_response_format(spec: Any, cfg: Config) -> str:
    if bool(cfg.enable_provider_timestamps) and spec.provider_code == "gptTr":
        return "verbose_json"
    return str(spec.response_format)


def normalize_diarized_segments(payload: dict) -> list[dict]:
    raw_segments = payload.get("segments") or payload.get("utterances") or []
    speakers: dict[str, str] = {}
    normalized = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        raw_speaker = (
            raw.get("provider_speaker")
            or raw.get("speaker")
            or raw.get("speaker_id")
            or raw.get("label")
            or "speaker"
        )
        speaker_key = str(raw_speaker).strip() or "speaker"
        if speaker_key not in speakers:
            speakers[speaker_key] = f"Speaker {len(speakers) + 1}"
        segment = {
            "speaker": speakers[speaker_key],
            "provider_speaker": speaker_key,
            "speaker_scope": "chunk",
            "text": text,
        }
        start = raw.get("start_sec", raw.get("start", raw.get("start_time")))
        end = raw.get("end_sec", raw.get("end", raw.get("end_time")))
        if start is not None:
            segment["start_sec"] = float(start)
        if end is not None:
            segment["end_sec"] = float(end)
        normalized.append(segment)
    return normalized


def format_diarized_text(segments: list[dict], fallback: str = "") -> str:
    if not segments:
        return fallback.strip()
    return "\n".join(f"{segment['speaker']}: {segment['text']}" for segment in segments).strip()


class OpenAITranscriptionHttpClient:
    """Minimal standard-library client for manual live smoke tests."""

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.openai.com/v1/audio/transcriptions",
        timeout_seconds: float = 120.0,
        cfg: Config | None = None,
    ):
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.cfg = cfg

    @classmethod
    def from_config(cls, cfg: Config) -> "OpenAITranscriptionHttpClient":
        api_key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live OpenAI transcription")
        api_url = os.environ.get("OPENAI_TRANSCRIPTION_API_URL", "https://api.openai.com/v1/audio/transcriptions")
        timeout_seconds = float(os.environ.get("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS", "120"))
        return cls(api_key=api_key, api_url=api_url, timeout_seconds=timeout_seconds, cfg=cfg)

    def transcribe(self, file_path: str, model: str | None = None) -> dict:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        boundary = f"apma-{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = bytearray()

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        resolved_model = model or "gpt-4o-mini-transcribe"
        add_field("model", resolved_model)
        cfg = self.cfg or Config()
        spec = _require_openai_model(resolved_model, cfg)
        response_format = _openai_response_format(spec, cfg)
        if response_format != "json":
            add_field("response_format", response_format)
        if spec.diarization:
            add_field("chunking_strategy", "auto")
        if response_format == "verbose_json" and bool(cfg.enable_provider_timestamps):
            add_field("timestamp_granularities[]", "segment")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        request = urllib.request.Request(
            self.api_url,
            data=bytes(body),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAITranscriptionRequestError(exc.code, detail) from exc

        raw_segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        segments = normalize_diarized_segments(payload) if spec.diarization else raw_segments
        if "text" not in payload and not (spec.diarization and segments):
            raise RuntimeError("OpenAI transcription response did not include text")
        raw_text = payload.get("text", "")
        if not isinstance(raw_text, str):
            raise RuntimeError("OpenAI transcription response text was not a string")
        text = format_diarized_text(segments, raw_text) if spec.diarization else raw_text
        return {
            "text": text,
            "text_status": "available" if text.strip() else "provider_empty",
            "cost_usd": 0.0,
            "raw_response": payload,
            "diarized": bool(spec.diarization and segments),
            "segments": segments,
            "response_format": response_format,
        }


class OpenAITranscriber(Transcriber):
    """Scaffold OpenAI transcription adapter.

    Notes:
    - This is a scaffold: real SDK usage is deferred. Tests must inject a fake
      `client` with a `transcribe(file_path, model=...)` method that returns
      a dict like `{"text": str, "cost_usd": float}`.
    - Live network calls are only allowed when all safety gates pass.
    """

    def __init__(self, client: Optional[Any] = None, client_factory: Optional[Callable[[Config], Any]] = None):
        self.client = client
        self.client_factory = client_factory

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, OpenAITranscriptionRequestError):
            return exc.status_code == 429 or exc.status_code >= 500
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code == 429 or exc.code >= 500
        if isinstance(exc, urllib.error.URLError):
            return True
        return isinstance(exc, (OSError, ConnectionError, TimeoutError))

    def _get_client(self, cfg: Config) -> Any:
        if self.client is not None:
            return self.client
        if self.client_factory is not None:
            return self.client_factory(cfg)
        return OpenAITranscriptionHttpClient.from_config(cfg)

    def _compute_duration(self, chunk_meta: dict) -> float:
        if "start_sec" in chunk_meta and "end_sec" in chunk_meta:
            return max(0.0, float(chunk_meta["end_sec"]) - float(chunk_meta["start_sec"]))
        frames = chunk_meta.get("frames")
        framerate = chunk_meta.get("framerate")
        if frames and framerate:
            return float(frames) / float(framerate)
        return float(chunk_meta.get("duration_seconds", 0.0))

    def _estimate_cost_for_duration(self, duration_seconds: float, model: str, cfg: Config) -> float:
        minutes = math.ceil(duration_seconds) / 60.0
        price = cfg.openai_price_per_minute.get(
            model,
            cfg.openai_price_per_minute.get(cfg.openai_default_model, 0.0),
        )
        return float(minutes * price)

    def _preflight_job(self, job_id: str, chunks: List[dict], cfg: Config) -> dict:
        estimates = []
        model = cfg.openai_model
        for c in chunks:
            dur = self._compute_duration(c)
            est = self._estimate_cost_for_duration(dur, model, cfg)
            estimates.append({"chunk": c.get("filename"), "duration_seconds": dur, "estimated_cost_usd": est})
        total = sum(e["estimated_cost_usd"] for e in estimates)
        return {"per_chunk": estimates, "estimated_cost_usd": float(total)}

    def _check_gates(self, cfg: Config) -> None:
        if getattr(cfg, "dry_run", True):
            raise RuntimeError("DRY_RUN is enabled; OpenAI live transcription is disabled. Use mock transcriber or disable DRY_RUN and enable live mode explicitly.")
        if not getattr(cfg, "enable_live_openai_transcription", False):
            raise RuntimeError("Live OpenAI transcription is not enabled (ENABLE_LIVE_OPENAI_TRANSCRIPTION).")
        api_key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live OpenAI transcription")

    def _check_file_size(self, chunk_meta: dict, cfg: Config) -> None:
        size = chunk_meta.get("actual_bytes")
        if size is None:
            path = chunk_meta.get("path")
            if path:
                try:
                    size = Path(path).stat().st_size
                except Exception:
                    size = None
        if size is not None and size > int(cfg.openai_file_size_limit_bytes):
            raise errors_mod.StepFailedError(f"File too large for OpenAI upload: {size} bytes")

    def _resolve_chunk_path(self, job_id: str, chunk_meta: dict, cfg: Config) -> Path:
        explicit_path = chunk_meta.get("path")
        if explicit_path:
            return Path(explicit_path)
        filename = chunk_meta.get("filename")
        if not filename:
            raise errors_mod.StepFailedError("Chunk metadata is missing filename")
        return Path(cfg.storage_path) / job_id / "chunks" / filename

    def transcribe_chunks(self, job_id: str, chunks: List[dict], cfg: Config) -> List[dict]:
        job_mod.validate_job_id(job_id)
        spec = _require_openai_model(cfg.openai_model, cfg)
        response_format = _openai_response_format(spec, cfg)
        run_id = f"tr-{uuid.uuid4().hex[:16]}"
        max_cost = float(cfg.max_cost_per_job_usd)
        # Preflight: cost and job manifest update
        pre = self._preflight_job(job_id, chunks, cfg)
        if pre["estimated_cost_usd"] > max_cost:
            raise errors_mod.CostLimitExceededError(f"Estimated job cost {pre['estimated_cost_usd']} exceeds max {cfg.max_cost_per_job_usd}")

        # update manifest preflight
        job_dir = Path(cfg.storage_path) / job_id
        manifest = job_mod.read_manifest(job_dir)
        manifest.setdefault("transcription", {})
        manifest["transcription"]["engine"] = "openai"
        manifest["transcription"]["model"] = cfg.openai_model
        manifest["transcription"]["provider"] = spec.provider
        manifest["transcription"]["provider_code"] = spec.provider_code
        manifest["transcription"]["response_format"] = response_format
        manifest["transcription"]["diarization"] = spec.diarization
        manifest["transcription"]["run_id"] = run_id
        manifest["transcription"]["preflight"] = pre
        manifest["transcription"]["state"] = "queued"
        job_mod.write_manifest(job_dir, manifest)

        results = []
        historical_spend = sum(
            float(entry.get("actual_cost_usd", 0.0))
            for entry in manifest["transcription"].get("chunks", [])
            if entry.get("status") == "completed"
            and entry.get("model") == cfg.openai_model
            and entry.get("provider_code") == spec.provider_code
        )
        current_job_spend = historical_spend
        try:
            for c in chunks:
                result = self.transcribe_chunk(
                    job_id,
                    c,
                    cfg,
                    run_id=run_id,
                    remaining_job_budget_usd=max(0.0, max_cost - current_job_spend),
                )
                results.append(result)
                if not result.get("cached"):
                    current_job_spend += float(result.get("actual_cost_usd", 0.0))
                if current_job_spend > max_cost:
                    raise errors_mod.CostLimitExceededError(
                        f"Actual job cost {current_job_spend} exceeds max {max_cost}; no further chunks will run"
                    )
        except errors_mod.CostLimitExceededError:
            manifest = job_mod.read_manifest(job_dir)
            manifest["transcription"]["actual_cost_usd"] = current_job_spend
            manifest["transcription"]["state"] = "cost_limit_exceeded"
            manifest["transcription"]["cost_limit"] = {
                "max_cost_usd": max_cost,
                "historical_spend_usd": historical_spend,
                "spent_this_run_usd": current_job_spend - historical_spend,
                "spent_total_job_usd": current_job_spend,
                "remaining_this_run_usd": max(0.0, max_cost - current_job_spend),
            }
            job_mod.write_manifest(job_dir, manifest)
            raise

        manifest = job_mod.read_manifest(job_dir)
        manifest["transcription"]["actual_cost_usd"] = current_job_spend
        manifest["transcription"]["state"] = "completed"
        job_mod.write_manifest(job_dir, manifest)
        return results

    def transcribe_chunk(
        self,
        job_id: str,
        chunk_meta: dict,
        cfg: Config,
        run_id: str | None = None,
        remaining_job_budget_usd: float | None = None,
    ) -> dict:
        # Gate checks
        job_mod.validate_job_id(job_id)
        self._check_gates(cfg)
        spec = _require_openai_model(cfg.openai_model, cfg)
        response_format = _openai_response_format(spec, cfg)
        run_id = run_id or f"tr-{uuid.uuid4().hex[:16]}"

        chunk_path = self._resolve_chunk_path(job_id, chunk_meta, cfg)
        # Reject oversized chunks before opening the file or attempting a request.
        self._check_file_size(chunk_meta, cfg)
        chunk_sha256 = _sha256_file(chunk_path)

        # Idempotency: if manifest already has completed transcript for this chunk, skip
        job_dir = Path(cfg.storage_path) / job_id
        manifest = job_mod.read_manifest(job_dir)
        existing_chunks = manifest.get("transcription", {}).get("chunks", [])
        for ex in existing_chunks:
            cache_match = (
                ex.get("chunk_filename") == chunk_meta.get("filename")
                and ex.get("status") == "completed"
                and ex.get("model") == cfg.openai_model
                and ex.get("provider_code") == spec.provider_code
                and ex.get("chunk_sha256") == chunk_sha256
            )
            if cache_match:
                tp = ex.get("transcript_path")
                transcript_path = Path(tp).resolve() if tp else None
                if (
                    transcript_path
                    and transcript_path.exists()
                    and _path_is_within(transcript_path, job_dir / "transcripts")
                ):
                    try:
                        with transcript_path.open("r", encoding="utf-8") as cached_file:
                            cached_payload = json.load(cached_file)
                    except (OSError, TypeError, ValueError):
                        continue
                    if _cached_payload_matches(
                        cached_payload,
                        ex,
                        job_id=job_id,
                        chunk_filename=chunk_meta.get("filename"),
                        chunk_sha256=chunk_sha256,
                        model=cfg.openai_model,
                        provider_code=spec.provider_code,
                        response_format=response_format,
                    ):
                        cached_payload["cached"] = True
                        return cached_payload

        # Estimate cost for this chunk
        duration = self._compute_duration(chunk_meta)
        est_cost = self._estimate_cost_for_duration(duration, cfg.openai_model, cfg)
        if cfg.openai_max_cost_per_chunk_usd and est_cost > float(cfg.openai_max_cost_per_chunk_usd):
            raise errors_mod.StepFailedError(f"Per-chunk estimated cost {est_cost} exceeds per-chunk limit {cfg.openai_max_cost_per_chunk_usd}")
        if remaining_job_budget_usd is not None and est_cost > float(remaining_job_budget_usd):
            raise errors_mod.CostLimitExceededError(
                f"Estimated chunk cost {est_cost} exceeds remaining job budget {remaining_job_budget_usd}"
            )

        # Update manifest chunk status -> running
        manifest = job_mod.read_manifest(job_dir)
        manifest.setdefault("transcription", {})
        manifest["transcription"].setdefault("chunks", [])
        manifest["transcription"]["provider"] = spec.provider
        manifest["transcription"]["provider_code"] = spec.provider_code
        manifest["transcription"]["model"] = cfg.openai_model
        manifest["transcription"]["run_id"] = run_id
        manifest["transcription"]["response_format"] = response_format
        manifest["transcription"]["diarization"] = spec.diarization
        manifest["transcription"]["chunks"] = [
            entry
            for entry in manifest["transcription"]["chunks"]
            if not (
                entry.get("chunk_filename") == chunk_meta.get("filename")
                and entry.get("model") == cfg.openai_model
                and entry.get("provider_code") == spec.provider_code
            )
        ]
        previous_attempts = max(
            (
                int(entry.get("total_attempts", entry.get("attempts", 0)))
                for entry in existing_chunks
                if entry.get("chunk_filename") == chunk_meta.get("filename")
                and entry.get("model") == cfg.openai_model
                and entry.get("provider_code") == spec.provider_code
            ),
            default=0,
        )
        max_attempts = max(1, int(cfg.openai_max_retries))
        chunk_entry = {
            "chunk_filename": chunk_meta.get("filename"),
            "status": "running",
            "estimated_cost_usd": est_cost,
            "attempts": 0,
            "total_attempts": previous_attempts,
            "max_attempts_per_run": max_attempts,
            "retryable": None,
            "provider": spec.provider,
            "provider_code": spec.provider_code,
            "model": cfg.openai_model,
            "run_id": run_id,
            "response_format": response_format,
            "chunk_sha256": chunk_sha256,
        }
        manifest["transcription"]["chunks"].append(chunk_entry)
        job_mod.write_manifest(job_dir, manifest)

        # Perform upload/transcription via injected client with retries
        client = self._get_client(cfg)
        attempt = 0
        last_err: Optional[Exception] = None
        while attempt < max_attempts:
            attempt += 1
            try:
                chunk_entry["attempts"] = attempt
                chunk_entry["total_attempts"] = previous_attempts + attempt
                job_mod.write_manifest(job_dir, manifest)
                # Expect client.transcribe(file_path, model=...)
                res = client.transcribe(str(chunk_path), model=cfg.openai_model)
                # res expected to include 'text' and optional 'cost_usd'
                if "text" not in res:
                    raise RuntimeError("OpenAI transcription response did not include text")
                raw_transcript_text = res.get("text")
                if not isinstance(raw_transcript_text, str):
                    raise RuntimeError("OpenAI transcription response text was not a string")
                provider_text = raw_transcript_text.strip()
                cost = float(res.get("cost_usd", 0.0))
                raw_segments = res.get("segments") or []
                segments = (
                    normalize_diarized_segments({"segments": raw_segments})
                    if spec.diarization and raw_segments
                    else []
                )
                diarized = bool(spec.diarization and segments)
                if segments:
                    provider_text = format_diarized_text(segments, provider_text)
                    segments = normalize_segment_texts(
                        segments, cfg.chinese_script_preference
                    )
                transcript_text, script_normalization = normalize_generated_text(
                    provider_text, cfg.chinese_script_preference
                )
                text_status = "available" if transcript_text else "provider_empty"

                transcripts_dir = Path(cfg.storage_path) / job_id / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)
                basename = Path(chunk_meta.get("filename", "chunk")).stem
                out_path = transcripts_dir / f"{basename}.json"

                provider_dir = Path(cfg.storage_path) / job_id / "providers" / spec.provider_code / run_id
                provider_dir.mkdir(parents=True, exist_ok=True)
                provider_path = provider_dir / f"{basename}.json"
                provider_payload = {
                    "schema_version": "apma.provider-response.v1",
                    "provider": spec.provider,
                    "provider_code": spec.provider_code,
                    "model": cfg.openai_model,
                    "run_id": run_id,
                    "chunk_filename": chunk_meta.get("filename"),
                    "chunk_sha256": chunk_sha256,
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "transcript_text_status": text_status,
                    "response": res.get("raw_response", {
                        "text": provider_text,
                        "segments": raw_segments,
                    }),
                }
                with provider_path.open("w", encoding="utf-8") as provider_file:
                    json.dump(provider_payload, provider_file, indent=2, ensure_ascii=False)

                chunk_start = float(chunk_meta.get("start_sec", chunk_meta.get("start", 0.0)))
                raw_response = provider_payload["response"]
                timing = normalize_provider_timing(
                    words=raw_response.get("words") if isinstance(raw_response, dict) else [],
                    segments=raw_response.get("segments") if isinstance(raw_response, dict) else [],
                    chunk_start_sec=chunk_start,
                    chunk_duration_sec=float(duration),
                )
                timing["provider_native"]["words"] = normalize_segment_texts(
                    timing["provider_native"]["words"],
                    cfg.chinese_script_preference,
                )
                timing["provider_native"]["segments"] = normalize_segment_texts(
                    timing["provider_native"]["segments"],
                    cfg.chinese_script_preference,
                )
                timing.update(
                    {
                        "provider": spec.provider,
                        "provider_code": spec.provider_code,
                        "model": cfg.openai_model,
                        "provider_artifact_path": str(provider_path),
                    }
                )
                has_provider_timing = bool(
                    timing["provider_native"]["words"]
                    or timing["provider_native"]["segments"]
                )
                timing["requested"] = bool(cfg.enable_provider_timestamps)
                timing["capability_status"] = (
                    "available"
                    if has_provider_timing
                    else "requested_but_not_returned"
                    if cfg.enable_provider_timestamps
                    else "not_requested"
                )
                payload = {
                    "schema_version": "apma.transcript.chunk.v1",
                    "job_id": job_id,
                    "chunk_filename": chunk_meta.get("filename"),
                    "chunk_sha256": chunk_sha256,
                    "transcript_path": str(out_path),
                    "duration_seconds": float(duration),
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "provider": spec.provider,
                    "provider_code": spec.provider_code,
                    "model": cfg.openai_model,
                    "run_id": run_id,
                    "response_format": res.get("response_format", response_format),
                    "segment_timing_scope": "chunk",
                    "chunk_start_sec": chunk_start,
                    "chunk_end_sec": chunk_start + float(duration),
                    "provider_artifact_path": str(provider_path),
                    "timing": timing,
                    "text": transcript_text,
                    "script_normalization": script_normalization,
                    "text_status": text_status,
                    "diarized": diarized,
                    "estimated_cost_usd": est_cost,
                    "actual_cost_usd": cost,
                }
                if segments:
                    payload["segments"] = segments
                with out_path.open("w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, ensure_ascii=False)

                # update manifest
                chunk_entry.update({
                    "status": "completed",
                    "transcript_path": str(out_path),
                    "provider_artifact_path": str(provider_path),
                    "actual_cost_usd": cost,
                    "diarized": diarized,
                    "text_status": text_status,
                    "retryable": False,
                })
                job_mod.write_manifest(job_dir, manifest)
                return payload
            except Exception as e:
                last_err = e
                if not self._is_transient(e):
                    chunk_entry.update({
                        "status": "failed",
                        "error": str(e),
                        "attempts": attempt,
                        "total_attempts": previous_attempts + attempt,
                        "retryable": False,
                    })
                    job_mod.write_manifest(job_dir, manifest)
                    raise
                # transient -> retry
                chunk_entry.update({
                    "status": "retrying",
                    "error": str(e),
                    "attempts": attempt,
                    "total_attempts": previous_attempts + attempt,
                    "retryable": True,
                })
                job_mod.write_manifest(job_dir, manifest)
                if attempt < max_attempts:
                    time.sleep(float(cfg.openai_retry_backoff_base) * (2 ** (attempt - 1)))

        # exhausted retries
        chunk_entry.update({
            "status": "failed",
            "error": str(last_err),
            "attempts": attempt,
            "total_attempts": previous_attempts + attempt,
            "retryable": True,
        })
        job_mod.write_manifest(job_dir, manifest)
        raise last_err or errors_mod.StepFailedError("OpenAI transcription failed after retries")


__all__ = [
    "OpenAITranscriber",
    "OpenAITranscriptionHttpClient",
    "OpenAITranscriptionRequestError",
    "format_diarized_text",
    "is_diarization_model",
    "normalize_diarized_segments",
]
