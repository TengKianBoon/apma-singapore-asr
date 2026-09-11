"""Guarded live transcription adapters for MERaLiON, Gemini, and Qwen."""

from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import hashlib
import html
import http.client
import json
import math
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from services import job as job_mod
from services.config import Config
from services.errors import CostLimitExceededError
from services.transcription.base import Transcriber
from services.transcription.contracts import normalize_asr_candidate, normalize_provider_timing
from services.transcription.models import get_model_spec
from services.transcription.router import get_runtime_model_status
from services.text_normalization import (
    normalize_generated_text,
    normalize_segment_texts,
)


_AUDIO_MIME_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_response(response: Any) -> dict:
    payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Provider returned a non-object JSON response")
    return payload


def _retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    """Return a safe provider-requested delay without exposing response content."""

    structured_delay = getattr(error, "_apma_retry_after_seconds", None)
    if structured_delay is not None:
        return max(0.0, float(structured_delay))
    headers = getattr(error, "headers", None)
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _parse_retry_duration_seconds(value: Any) -> float | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)s\s*", str(value or ""))
    return float(match.group(1)) if match else None


def _capture_structured_retry_delay(error: urllib.error.HTTPError) -> None:
    """Read only safe retry metadata from a provider error response."""

    try:
        raw = error.read()
    except (OSError, ValueError):
        return
    if not raw:
        return
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error_payload, dict):
        return
    details = error_payload.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            retry_delay = _parse_retry_duration_seconds(detail.get("retryDelay"))
            if retry_delay is not None:
                error._apma_retry_after_seconds = retry_delay
                return
    message = error_payload.get("message")
    if isinstance(message, str):
        match = re.search(
            r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)s",
            message,
            flags=re.IGNORECASE,
        )
        if match:
            error._apma_retry_after_seconds = float(match.group(1))


def _retry_delay_seconds(error: Exception, cfg: Config, attempt: int) -> float:
    delay = max(
        0.0,
        float(cfg.external_transcription_retry_backoff_base) * (2**attempt),
    )
    if isinstance(error, urllib.error.HTTPError) and error.code == 429:
        retry_after = _retry_after_seconds(error)
        if retry_after is not None:
            delay = max(delay, retry_after)
    return min(delay, max(0.0, float(cfg.external_transcription_retry_max_delay_seconds)))


def _provider_request(request: urllib.request.Request, cfg: Config) -> dict:
    attempts = max(1, int(cfg.external_transcription_max_retries))
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                request,
                timeout=float(cfg.external_transcription_timeout_seconds),
            ) as response:
                return _read_json_response(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _capture_structured_retry_delay(exc)
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"Provider request failed with HTTP {exc.code}") from exc
            error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = exc
        if attempt + 1 < attempts:
            time.sleep(_retry_delay_seconds(error, cfg, attempt))
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429:
            noun = "attempt" if attempts == 1 else "attempts"
            raise RuntimeError(
                "Provider rate/quota limit returned HTTP 429 after "
                f"{attempts} {noun}; completed chunks are retained and the same job "
                "can be resumed later"
            ) from error
        noun = "attempt" if attempts == 1 else "attempts"
        raise RuntimeError(
            f"Provider request failed with HTTP {error.code} after {attempts} {noun}"
        ) from error
    noun = "attempt" if attempts == 1 else "attempts"
    safe_error_type = type(error).__name__
    raise RuntimeError(
        f"Provider request failed after {attempts} {noun} "
        f"({safe_error_type})"
    ) from error


def _provider_request_once(request: urllib.request.Request, cfg: Config) -> dict:
    """Make one guarded request when retrying could create a duplicate paid task."""

    try:
        with urllib.request.urlopen(
            request,
            timeout=float(cfg.external_transcription_timeout_seconds),
        ) as response:
            return _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Provider request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Provider request failed ({type(exc).__name__})"
        ) from exc


def _audio_mime_type(path: Path) -> str:
    return _AUDIO_MIME_TYPES.get(path.suffix.lower()) or (
        mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )


class MeralionTranscriptionHttpClient:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        cfg: Config,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.cfg = cfg

    @classmethod
    def from_config(cls, cfg: Config) -> "MeralionTranscriptionHttpClient":
        return cls(
            api_url=cfg.meralion_api_url,
            api_key=str(cfg.meralion_api_key or ""),
            cfg=cfg,
        )

    def transcribe(self, file_path: Path, model: str) -> dict:
        mime_type = _audio_mime_type(file_path)
        audio = base64.b64encode(file_path.read_bytes()).decode("ascii")
        body = {"audio_url": f"data:{mime_type};base64,{audio}"}
        if bool(self.cfg.enable_provider_timestamps):
            body["return_timestamps"] = True
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        return _provider_request(request, self.cfg)


class GeminiTranscriptionHttpClient:
    def __init__(self, *, api_base_url: str, api_key: str, cfg: Config):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.cfg = cfg

    @classmethod
    def from_config(cls, cfg: Config) -> "GeminiTranscriptionHttpClient":
        return cls(
            api_base_url=cfg.gemini_api_base_url,
            api_key=str(cfg.gemini_api_key or ""),
            cfg=cfg,
        )

    def transcribe(
        self,
        file_path: Path,
        model: str,
        *,
        diarized: bool,
        thinking_level: str,
    ) -> dict:
        mime_type = _audio_mime_type(file_path)
        audio_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        timestamp_mode = bool(self.cfg.enable_provider_timestamps)
        speaker_mode = bool(self.cfg.enable_gemini_speaker_attribution)
        if (timestamp_mode or speaker_mode) and model == self.cfg.gemini_transcribe_model:
            verbatim_mode: dict[str, Any] = {
                "type": "verbatim",
                "timestamp_granularities": ["word"],
            }
            if speaker_mode:
                verbatim_mode["diarization_mode"] = "speaker"
            body = {
                "model": model,
                "input": [
                    {
                        "type": "audio",
                        "data": audio_data,
                        "mime_type": mime_type,
                    }
                ],
                "generation_config": {
                    "transcription_config": {
                        "mode": verbatim_mode
                    }
                },
                "store": False,
            }
            request = urllib.request.Request(
                f"{self.api_base_url}/interactions",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            return _provider_request(request, self.cfg)
        audio_part = {
            "inlineData": {
                "mimeType": mime_type,
                "data": audio_data,
            }
        }
        generation_config: dict[str, Any] = {}
        if diarized:
            parts = [audio_part]
            generation_config["audioTranscriptionConfig"] = {
                "diarization": True,
            }
        else:
            parts = [
                {
                    "text": "Generate a transcript of the speech. Keep each spoken "
                    "language unchanged. Return only the transcript text."
                },
                audio_part,
            ]
            generation_config["temperature"] = 0
            generation_config["responseMimeType"] = "text/plain"
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}
        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": generation_config,
        }
        encoded_model = urllib.parse.quote(model, safe="")
        request = urllib.request.Request(
            f"{self.api_base_url}/models/{encoded_model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        return _provider_request(request, self.cfg)


def _redact_provider_urls(value: Any) -> Any:
    """Remove temporary signed URLs before provider evidence reaches disk."""

    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED TEMPORARY SIGNED URL]"
                if key in {"file_url", "transcription_url"} and isinstance(item, str)
                else _redact_provider_urls(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_provider_urls(item) for item in value]
    return value


def _stream_multipart_file_upload(
    upload_url: str,
    fields: list[tuple[str, str]],
    file_field: tuple[str, str, Path, str],
    timeout_seconds: float,
) -> None:
    """Stream one multipart upload without loading a long recording into RAM."""

    parsed = urllib.parse.urlsplit(upload_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("DashScope temporary upload returned an unsafe upload host")
    boundary = f"----apma-{uuid.uuid4().hex}"
    prefix_parts: list[bytes] = []
    for name, value in fields:
        prefix_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    field_name, filename, file_path, mime_type = file_field
    prefix_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    prefix = b"".join(prefix_parts)
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    content_length = len(prefix) + file_path.stat().st_size + len(suffix)
    request_path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        port=parsed.port,
        timeout=float(timeout_seconds),
    )
    try:
        connection.putrequest("POST", request_path)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        connection.send(prefix)
        with file_path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        response.read(4096)
        if response.status != 200:
            raise RuntimeError(
                f"DashScope temporary upload failed with HTTP {response.status}"
            )
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise RuntimeError(
            f"DashScope temporary upload failed ({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()


class DashScopeTemporaryFileUploader:
    """Create a Beijing-only provider-managed URL using the Model Studio key."""

    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        cfg: Config,
        request_retry: Callable[[urllib.request.Request, Config], dict] = _provider_request,
        multipart_upload: Callable[
            [str, list[tuple[str, str]], tuple[str, str, Path, str], float], None
        ] = _stream_multipart_file_upload,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.cfg = cfg
        self._request_retry = request_retry
        self._multipart_upload = multipart_upload

    def upload(self, file_path: Path, model: str) -> str:
        query = urllib.parse.urlencode({"action": "getPolicy", "model": model})
        request = urllib.request.Request(
            f"{self.api_base_url}/uploads?{query}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="GET",
        )
        response = self._request_retry(request, self.cfg)
        policy = response.get("data")
        if not isinstance(policy, dict):
            raise RuntimeError("DashScope temporary upload policy is missing")
        required = (
            "upload_host",
            "upload_dir",
            "oss_access_key_id",
            "signature",
            "policy",
            "x_oss_object_acl",
            "x_oss_forbid_overwrite",
        )
        if any(not policy.get(name) for name in required):
            raise RuntimeError("DashScope temporary upload policy is incomplete")
        try:
            max_size_bytes = int(float(policy.get("max_file_size_mb"))) * 1024 * 1024
        except (TypeError, ValueError):
            max_size_bytes = 0
        if max_size_bytes and file_path.stat().st_size > max_size_bytes:
            raise ValueError("Audio exceeds the DashScope temporary-upload file limit")

        safe_filename = f"{_sha256(file_path)}{file_path.suffix.lower()}"
        object_key = f"{str(policy['upload_dir']).rstrip('/')}/{safe_filename}"
        fields = [
            ("OSSAccessKeyId", str(policy["oss_access_key_id"])),
            ("Signature", str(policy["signature"])),
            ("policy", str(policy["policy"])),
            ("x-oss-object-acl", str(policy["x_oss_object_acl"])),
            (
                "x-oss-forbid-overwrite",
                str(policy["x_oss_forbid_overwrite"]),
            ),
            ("key", object_key),
            ("success_action_status", "200"),
        ]
        self._multipart_upload(
            str(policy["upload_host"]),
            fields,
            ("file", safe_filename, file_path, _audio_mime_type(file_path)),
            float(self.cfg.external_transcription_timeout_seconds),
        )
        return f"oss://{object_key}"


class QwenFiletransHttpClient:
    """Stage one URL, poll Filetrans, and preserve no source URL in artifacts."""

    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        bucket: Any | None,
        cfg: Config,
        temporary_uploader: Any | None = None,
        request_once: Callable[[urllib.request.Request, Config], dict] = _provider_request_once,
        request_retry: Callable[[urllib.request.Request, Config], dict] = _provider_request,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.bucket = bucket
        self.cfg = cfg
        self.temporary_uploader = temporary_uploader
        self._request_once = request_once
        self._request_retry = request_retry
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def from_config(cls, cfg: Config) -> "QwenFiletransHttpClient":
        staging_mode = str(cfg.qwen_filetrans_staging_mode).strip().lower()
        bucket = None
        temporary_uploader = None
        if staging_mode == "dashscope_temporary":
            temporary_uploader = DashScopeTemporaryFileUploader(
                api_base_url=cfg.dashscope_temporary_upload_api_base_url,
                api_key=str(cfg.dashscope_api_key or ""),
                cfg=cfg,
            )
        elif staging_mode == "private_oss":
            try:
                import oss2
            except ImportError as exc:  # pragma: no cover - production dependency guard
                raise RuntimeError("Alibaba OSS support is not installed") from exc
            auth = oss2.Auth(
                str(cfg.aliyun_oss_access_key_id or ""),
                str(cfg.aliyun_oss_access_key_secret or ""),
            )
            bucket = oss2.Bucket(
                auth,
                str(cfg.aliyun_oss_endpoint or ""),
                str(cfg.aliyun_oss_bucket or ""),
            )
        return cls(
            api_base_url=cfg.dashscope_api_base_url,
            api_key=str(cfg.dashscope_api_key or ""),
            bucket=bucket,
            cfg=cfg,
            temporary_uploader=temporary_uploader,
        )

    def _headers(
        self, *, submit: bool = False, resolve_temporary_oss: bool = False
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if submit:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                }
            )
        if resolve_temporary_oss:
            headers["X-DashScope-OssResourceResolve"] = "enable"
        return headers

    def transcribe(self, file_path: Path, model: str) -> dict:
        staging_mode = str(self.cfg.qwen_filetrans_staging_mode).strip().lower()
        object_key: str | None = None
        operation_error: Exception | None = None
        result: dict | None = None
        uploaded = False
        try:
            if staging_mode == "data_uri":
                limit = int(self.cfg.qwen_filetrans_data_uri_limit_bytes)
                if file_path.stat().st_size > limit:
                    raise ValueError("Audio exceeds the Qwen data-URI file limit")
                audio = base64.b64encode(file_path.read_bytes()).decode("ascii")
                signed_url = (
                    f"data:{_audio_mime_type(file_path)};base64,{audio}"
                )
            elif staging_mode == "dashscope_temporary":
                if self.temporary_uploader is None:
                    raise RuntimeError("DashScope temporary uploader is not configured")
                signed_url = self.temporary_uploader.upload(file_path, model)
            else:
                object_prefix = str(
                    self.cfg.aliyun_oss_object_prefix or "apma-temporary"
                ).strip("/ ")
                object_key = (
                    f"{object_prefix}/{uuid.uuid4().hex}/{_sha256(file_path)}"
                    f"{file_path.suffix.lower()}"
                )
                try:
                    if self.bucket is None:
                        raise RuntimeError("Private OSS bucket is not configured")
                    self.bucket.put_object_from_file(object_key, str(file_path))
                    uploaded = True
                    signed_url = self.bucket.sign_url(
                        "GET",
                        object_key,
                        int(self.cfg.aliyun_oss_signed_url_expiry_seconds),
                        slash_safe=True,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Temporary private OSS staging failed ({type(exc).__name__})"
                    ) from exc

            parameters: dict[str, Any] = {
                "channel_id": [0],
                "diarization_enabled": bool(
                    self.cfg.qwen_filetrans_diarization_enabled
                ),
                "language_hints": ["zh", "en", "id", "ms"],
            }
            body = {
                "model": model,
                "input": {"file_urls": [signed_url]},
                "parameters": parameters,
            }
            request = urllib.request.Request(
                f"{self.api_base_url}/services/audio/asr/transcription",
                data=json.dumps(body).encode("utf-8"),
                headers=self._headers(
                    submit=True,
                    resolve_temporary_oss=staging_mode == "dashscope_temporary",
                ),
                method="POST",
            )
            # Submission is deliberately not retried: a timeout can be
            # ambiguous and retrying could create a second billable task.
            submitted = self._request_once(request, self.cfg)
            output = submitted.get("output")
            task_id = output.get("task_id") if isinstance(output, dict) else None
            if not task_id:
                raise RuntimeError("Alibaba Filetrans did not return a task ID")

            deadline = self._monotonic() + float(
                self.cfg.qwen_filetrans_poll_timeout_seconds
            )
            completed: dict | None = None
            while self._monotonic() < deadline:
                query = urllib.request.Request(
                    f"{self.api_base_url}/tasks/{urllib.parse.quote(str(task_id), safe='')}",
                    headers=self._headers(),
                    method="GET",
                )
                polled = self._request_retry(query, self.cfg)
                polled_output = polled.get("output")
                status = (
                    str(polled_output.get("task_status") or "").upper()
                    if isinstance(polled_output, dict)
                    else ""
                )
                if status == "SUCCEEDED":
                    completed = polled
                    break
                if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                    raise RuntimeError(f"Alibaba Filetrans task ended with status {status}")
                self._sleep(float(self.cfg.qwen_filetrans_poll_interval_seconds))
            if completed is None:
                raise RuntimeError("Alibaba Filetrans task timed out while polling")

            completed_output = completed.get("output") or {}
            results = completed_output.get("results") or []
            successful = next(
                (
                    item
                    for item in results
                    if isinstance(item, dict)
                    and str(item.get("subtask_status") or "").upper() == "SUCCEEDED"
                    and item.get("transcription_url")
                ),
                None,
            )
            if successful is None:
                failed_code = next(
                    (
                        str(item.get("code"))
                        for item in results
                        if isinstance(item, dict) and item.get("code")
                    ),
                    "NO_SUCCESSFUL_SUBTASK",
                )
                raise RuntimeError(f"Alibaba Filetrans subtask failed ({failed_code})")

            recognition_request = urllib.request.Request(
                str(successful["transcription_url"]),
                headers={"Accept": "application/json"},
                method="GET",
            )
            recognition = self._request_retry(recognition_request, self.cfg)
            usage = completed.get("usage") if isinstance(completed.get("usage"), dict) else {}
            metered_seconds = float(usage.get("duration") or 0.0)
            result = {
                "request_id": completed.get("request_id") or submitted.get("request_id"),
                "task_id": str(task_id),
                "task_status": "SUCCEEDED",
                "metered_duration_seconds": metered_seconds,
                "cost_usd": metered_seconds
                / 60.0
                * float(self.cfg.qwen_filetrans_price_per_minute_usd),
                "raw_response": _redact_provider_urls(recognition),
                "raw_response_redactions": [
                    "source payload and result URLs removed before artifact retention"
                ],
                "file_staging_mode": staging_mode,
                "temporary_oss_cleanup": (
                    "provider_managed_expiry"
                    if staging_mode == "dashscope_temporary"
                    else "not_applicable"
                    if staging_mode == "data_uri"
                    else "pending"
                ),
                "provider_managed_retention_hours": (
                    int(self.cfg.qwen_filetrans_temporary_retention_hours)
                    if staging_mode == "dashscope_temporary"
                    else None
                ),
            }
        except Exception as exc:
            operation_error = exc

        cleanup_error: Exception | None = None
        if uploaded and object_key is not None:
            try:
                self.bucket.delete_object(object_key)
            except Exception as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise RuntimeError(
                "Temporary OSS audio deletion failed; remove the APMA temporary object manually"
            ) from cleanup_error
        if operation_error is not None:
            raise operation_error
        assert result is not None
        if staging_mode == "private_oss":
            result["temporary_oss_cleanup"] = "deleted"
        return result


def _chunk_path(job_id: str, chunk: dict, cfg: Config) -> Path:
    candidate = chunk.get("path")
    if candidate:
        path = Path(candidate)
    else:
        filename = chunk.get("filename") or chunk.get("chunk_filename")
        path = Path(cfg.storage_path) / job_id / "chunks" / str(filename or "")
    if not path.is_file():
        raise FileNotFoundError(f"Chunk file does not exist: {path}")
    return path


def _duration_seconds(chunk: dict) -> float:
    start = float(chunk.get("start_sec", chunk.get("start", 0.0)))
    end = chunk.get("end_sec", chunk.get("end"))
    if end is not None:
        return max(0.0, float(end) - start)
    frames = chunk.get("frames")
    rate = chunk.get("framerate")
    if frames is not None and rate:
        return max(0.0, float(frames) / float(rate))
    raise ValueError("Chunk duration metadata is missing")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_from_text(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S)
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _offset_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip().lower()
    if stripped.endswith("s"):
        stripped = stripped[:-1]
    try:
        return float(stripped)
    except ValueError:
        return None


def _extract_meralion_payload(raw: dict) -> dict:
    merged = dict(raw)
    if isinstance(raw.get("text"), str) or isinstance(raw.get("transcript"), str):
        return merged
    for choice in raw.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            merged["text"] = content
        if isinstance(message, dict) and isinstance(message.get("words"), list):
            merged["words"] = message["words"]
        if isinstance(message, dict) and isinstance(message.get("segments"), list):
            merged["segments"] = message["segments"]
        if merged.get("text"):
            return merged
    return merged


def _gemini_speaker_turns(
    provider_text: str, annotations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group consecutive provider word annotations without rewriting their text."""

    word_annotations = [
        dict(item)
        for item in annotations
        if isinstance(item, dict) and item.get("type") == "word_info"
    ]
    turns: list[dict[str, Any]] = []
    for annotation in word_annotations:
        speaker = str(annotation.get("speaker") or "").strip()
        if not turns or turns[-1]["speaker"] != speaker:
            turns.append(
                {
                    "speaker": speaker or None,
                    "provider_speaker": speaker or None,
                    "start_offset": annotation.get("start_offset"),
                    "end_offset": annotation.get("end_offset"),
                    "start_index": annotation.get("start_index"),
                    "end_index": annotation.get("end_index"),
                    "words": [],
                    "text": "",
                    "text_derivation": "provider_word_annotations",
                }
            )
        turn = turns[-1]
        copied = dict(annotation)
        copied["raw_provider_annotation"] = dict(annotation)
        if "word" not in copied and isinstance(copied.get("text"), str):
            copied["word"] = copied["text"]
        turn["words"].append(copied)
        turn["end_offset"] = annotation.get("end_offset")
        turn["end_index"] = annotation.get("end_index")

    encoded = provider_text.encode("utf-8")
    for turn in turns:
        start_index = turn.get("start_index")
        end_index = turn.get("end_index")
        exact_text: str | None = None
        if (
            isinstance(start_index, int)
            and isinstance(end_index, int)
            and 0 <= start_index < end_index <= len(encoded)
        ):
            try:
                exact_text = encoded[start_index:end_index].decode("utf-8")
            except UnicodeDecodeError:
                exact_text = None
        if exact_text is not None:
            turn["text"] = exact_text
            turn["text_derivation"] = "provider_utf8_byte_span"
        else:
            pieces = [str(word.get("text") or word.get("word") or "") for word in turn["words"]]
            joined = ""
            for piece in pieces:
                needs_space = bool(
                    joined
                    and piece
                    and joined[-1].isascii()
                    and joined[-1].isalnum()
                    and piece[0].isascii()
                    and piece[0].isalnum()
                )
                joined += (" " if needs_space else "") + piece
            turn["text"] = joined
            turn["text_derivation"] = (
                "language_aware_join_of_provider_word_annotations"
            )
    return turns


def _extract_gemini_payload(raw: dict) -> dict:
    parts: list[str] = []
    annotated_words: list[dict] = []
    annotated_segments: list[dict] = []
    for candidate in raw.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
            transcription = part.get("audioTranscription")
            if not isinstance(transcription, dict):
                continue
            words = []
            for item in transcription.get("words") or []:
                if not isinstance(item, dict) or not isinstance(item.get("word"), str):
                    continue
                annotated_words.append(dict(item))
                words.append(
                    {
                        "word": item["word"],
                        "start_sec": _offset_seconds(item.get("startOffset")),
                        "end_sec": _offset_seconds(item.get("endOffset")),
                    }
                )
            if not words:
                continue
            speaker = str(transcription.get("speakerLabel") or "")
            segment_text = " ".join(str(item["word"]) for item in words).strip()
            annotated_segments.append(
                {
                    "speaker": speaker,
                    "provider_speaker": speaker,
                    "start_sec": words[0]["start_sec"],
                    "end_sec": words[-1]["end_sec"],
                    "text": segment_text,
                    "words": words,
                }
            )
    for step in raw.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for content in step.get("content") or []:
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                parts.append(content["text"])
            annotations = content.get("annotations") or []
            for annotation in annotations:
                if not isinstance(annotation, dict) or annotation.get("type") != "word_info":
                    continue
                word = dict(annotation)
                word["raw_provider_annotation"] = dict(annotation)
                if "word" not in word and isinstance(word.get("text"), str):
                    word["word"] = word["text"]
                annotated_words.append(word)
            if isinstance(content.get("text"), str):
                annotated_segments.extend(
                    _gemini_speaker_turns(content["text"], annotations)
                )
    text = "\n".join(parts)
    parsed = _json_from_text(text)
    if parsed is not None:
        merged = dict(raw)
        merged.update(parsed)
        if annotated_segments and not merged.get("segments"):
            merged["segments"] = annotated_segments
        if annotated_words and not merged.get("words"):
            merged["words"] = annotated_words
        return merged
    merged = dict(raw)
    if text:
        merged["text"] = text
    elif annotated_segments:
        merged["text"] = "\n".join(segment["text"] for segment in annotated_segments)
    if annotated_segments:
        merged["segments"] = annotated_segments
    if annotated_words:
        merged["words"] = annotated_words
    return merged


def _extract_qwen_filetrans_payload(raw: dict) -> dict:
    """Expose provider-native Filetrans text/timing without inventing offsets."""

    merged = dict(raw)
    transcript_texts: list[str] = []
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for transcript_index, transcript in enumerate(raw.get("transcripts") or []):
        if not isinstance(transcript, dict):
            continue
        if isinstance(transcript.get("text"), str):
            transcript_texts.append(transcript["text"])
        channel_id = transcript.get("channel_id")
        for sentence_index, sentence in enumerate(transcript.get("sentences") or []):
            if not isinstance(sentence, dict):
                continue
            begin_ms = sentence.get("begin_time")
            end_ms = sentence.get("end_time")
            speaker_id = sentence.get("speaker_id")
            speaker = f"spk:{speaker_id}" if speaker_id is not None else None
            segment = {
                "text": str(sentence.get("text") or ""),
                "start_sec": float(begin_ms) / 1000.0
                if isinstance(begin_ms, (int, float))
                else None,
                "end_sec": float(end_ms) / 1000.0
                if isinstance(end_ms, (int, float))
                else None,
                "speaker": speaker,
                "provider_speaker": speaker,
                "provider_speaker_id": speaker_id,
                "channel_id": channel_id,
                "raw_provider_annotation": dict(sentence),
                "provenance": {
                    "raw_collection": "transcripts.sentences",
                    "transcript_index": transcript_index,
                    "sentence_index": sentence_index,
                },
            }
            segments.append(segment)
            for word_index, item in enumerate(sentence.get("words") or []):
                if not isinstance(item, dict):
                    continue
                word_begin_ms = item.get("begin_time")
                word_end_ms = item.get("end_time")
                words.append(
                    {
                        "word": str(item.get("text") or ""),
                        "text": str(item.get("text") or ""),
                        "punctuation": item.get("punctuation"),
                        "start_sec": float(word_begin_ms) / 1000.0
                        if isinstance(word_begin_ms, (int, float))
                        else None,
                        "end_sec": float(word_end_ms) / 1000.0
                        if isinstance(word_end_ms, (int, float))
                        else None,
                        "speaker": speaker,
                        "provider_speaker": speaker,
                        "provider_speaker_id": speaker_id,
                        "channel_id": channel_id,
                        "raw_provider_annotation": dict(item),
                        "provenance": {
                            "raw_collection": "transcripts.sentences.words",
                            "transcript_index": transcript_index,
                            "sentence_index": sentence_index,
                            "word_index": word_index,
                        },
                    }
                )
    if transcript_texts:
        merged["text"] = "\n".join(transcript_texts)
    if segments:
        merged["segments"] = segments
    if words:
        merged["words"] = words
    return merged


def _gemini_empty_response_details(raw: dict) -> str:
    """Return only safe provider metadata explaining an empty text response."""

    finish_reasons = []
    for candidate in raw.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        reason = candidate.get("finishReason", candidate.get("finish_reason"))
        if reason and str(reason) not in finish_reasons:
            finish_reasons.append(str(reason))
    prompt_feedback = raw.get("promptFeedback", raw.get("prompt_feedback"))
    block_reason = (
        prompt_feedback.get("blockReason", prompt_feedback.get("block_reason"))
        if isinstance(prompt_feedback, dict)
        else None
    )
    details = []
    if finish_reasons:
        details.append("finish reason=" + ",".join(finish_reasons))
    if block_reason:
        details.append("prompt block reason=" + str(block_reason))
    return "; ".join(details) or "provider supplied no finish/block reason"


def _short_name(job_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", job_id).strip("-._")
    return (cleaned or "Job")[:48]


def _retryable_provider_failure(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "http 429",
            "http 5",
            "rate/quota",
            "timeout",
            "timed out",
            "connection",
            "temporarily unavailable",
        )
    )


class _ExternalTranscriber(Transcriber):
    provider = ""
    engine = ""
    client_factory: Callable[[Config], Any]

    def __init__(self, client: Any | None = None):
        self._client = client
        self._run_id: str | None = None

    def _model(self, cfg: Config) -> str:
        raise NotImplementedError

    def _file_limit(self, cfg: Config) -> int:
        raise NotImplementedError

    def _call(self, client: Any, path: Path, model: str, spec: Any, cfg: Config) -> dict:
        raise NotImplementedError

    def _validate(self, cfg: Config) -> tuple[str, Any, dict]:
        if cfg.dry_run:
            raise RuntimeError(f"{self.provider} live transcription requires DRY_RUN=false")
        model = self._model(cfg)
        spec = get_model_spec(model, cfg)
        if spec.provider != self.provider:
            raise RuntimeError(f"Model {model!r} is not a {self.provider} route")
        status = get_runtime_model_status(model, cfg)
        if not status["runnable"]:
            raise RuntimeError(status["readiness_reason"])
        return model, spec, status

    def transcribe_chunks(self, job_id: str, chunks: list[dict], cfg: Config) -> list[dict]:
        model, _spec, status = self._validate(cfg)
        estimate = sum(
            math.ceil(_duration_seconds(chunk)) / 60.0 * float(status["price_per_minute_usd"])
            for chunk in chunks
        )
        if estimate > float(cfg.max_cost_per_job_usd) + 1e-12:
            raise CostLimitExceededError(
                f"Estimated transcription cost ${estimate:.6f} exceeds configured cap ${cfg.max_cost_per_job_usd:.6f}"
            )
        self._run_id = f"{status['provider_code'].lower()}-{uuid.uuid4().hex[:12]}"
        job_dir = Path(cfg.storage_path) / job_id
        manifest = job_mod.read_manifest(job_dir)
        transcription = manifest.setdefault("transcription", {})
        transcription.update(
            {
                "engine": self.engine,
                "model": model,
                "provider": self.provider,
                "provider_code": status["provider_code"],
                "state": "running",
            }
        )
        transcription.setdefault("chunks", [])
        job_mod.write_manifest(job_dir, manifest)

        results: list[dict] = []
        transcripts_root = (job_dir / "transcripts").resolve()
        for chunk in chunks:
            path = _chunk_path(job_id, chunk, cfg)
            chunk_sha = _sha256(path)
            filename = path.name
            cached_entry = next(
                (
                    entry
                    for entry in transcription["chunks"]
                    if entry.get("status") == "completed"
                    and entry.get("chunk_filename") == filename
                    and entry.get("chunk_sha256") == chunk_sha
                    and entry.get("model") == model
                    and entry.get("provider_code") == status["provider_code"]
                ),
                None,
            )
            if cached_entry:
                cached_path = Path(str(cached_entry.get("transcript_path") or "")).resolve()
                if cached_path.is_file() and cached_path.is_relative_to(transcripts_root):
                    cached_result = json.loads(cached_path.read_text(encoding="utf-8"))
                    cached_result["cached"] = True
                    results.append(cached_result)
                    continue

            try:
                result = self.transcribe_chunk(job_id, chunk, cfg)
            except Exception as exc:
                retryable = _retryable_provider_failure(exc)
                transcription["chunks"] = [
                    entry
                    for entry in transcription["chunks"]
                    if entry.get("chunk_filename") != filename
                ]
                transcription["chunks"].append(
                    {
                        "chunk_filename": filename,
                        "chunk_sha256": chunk_sha,
                        "model": model,
                        "provider_code": status["provider_code"],
                        "status": "failed",
                        "retryable": retryable,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                transcription["state"] = "failed"
                transcription["retryable"] = retryable
                job_mod.write_manifest(job_dir, manifest)
                raise

            result["cached"] = False
            results.append(result)
            transcription["chunks"] = [
                entry
                for entry in transcription["chunks"]
                if entry.get("chunk_filename") != filename
            ]
            transcription["chunks"].append(
                {
                    "chunk_filename": filename,
                    "chunk_sha256": chunk_sha,
                    "transcript_path": result["transcript_path"],
                    "provider_artifact_path": result.get("provider_artifact_path"),
                    "model": model,
                    "provider_code": status["provider_code"],
                    "status": "completed",
                    "retryable": False,
                }
            )
            job_mod.write_manifest(job_dir, manifest)

        retained = self._write_retained_outputs(job_id, results, cfg, model, status["provider_code"])
        for result in results:
            result["retained_output_paths"] = retained
            Path(result["transcript_path"]).write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        manifest = job_mod.read_manifest(job_dir)
        manifest.setdefault("transcription", {})["state"] = "completed"
        manifest["transcription"]["retryable"] = False
        job_mod.write_manifest(job_dir, manifest)
        return results

    def transcribe_chunk(self, job_id: str, chunk_meta: dict, cfg: Config) -> dict:
        model, spec, status = self._validate(cfg)
        path = _chunk_path(job_id, chunk_meta, cfg)
        if path.stat().st_size > self._file_limit(cfg):
            raise ValueError(f"Chunk exceeds {self.provider} upload size limit")
        duration = _duration_seconds(chunk_meta)
        estimated_cost = math.ceil(duration) / 60.0 * float(status["price_per_minute_usd"])
        if estimated_cost > float(cfg.max_cost_per_job_usd) + 1e-12:
            raise CostLimitExceededError("Estimated chunk cost exceeds configured job cap")

        client = self._client or self.client_factory(cfg)
        response = self._call(client, path, model, spec, cfg)
        if not isinstance(response, dict):
            raise RuntimeError("Provider client returned a non-object response")
        provider_raw = response.get("raw_response", response)
        if not isinstance(provider_raw, dict):
            raise RuntimeError("Provider raw response is not a JSON object")
        raw = provider_raw
        if self.provider == "meralion":
            raw = _extract_meralion_payload(raw)
        elif self.provider == "google":
            raw = _extract_gemini_payload(raw)
        elif self.provider == "alibaba":
            raw = _extract_qwen_filetrans_payload(raw)
        if isinstance(response.get("text"), str) and not raw.get("text"):
            raw = dict(raw)
            raw["text"] = response["text"]
        if isinstance(response.get("segments"), list) and not raw.get("segments"):
            raw = dict(raw)
            raw["segments"] = response["segments"]

        run_id = self._run_id or f"{spec.provider_code.lower()}-{uuid.uuid4().hex[:12]}"
        request_id = (
            response.get("request_id")
            or provider_raw.get("request_id")
            or provider_raw.get("responseId")
            or provider_raw.get("response_id")
            or provider_raw.get("id")
            or raw.get("request_id")
            or raw.get("responseId")
            or raw.get("response_id")
            or raw.get("id")
        )
        candidate = normalize_asr_candidate(
            provider=self.provider,
            provider_code=spec.provider_code,
            model=model,
            raw_response=raw,
            job_id=job_id,
            run_id=run_id,
            chunk=chunk_meta,
            request_id=request_id,
        )
        job_dir = Path(cfg.storage_path) / job_id
        basename = path.stem
        provider_dir = job_dir / "providers" / spec.provider_code / run_id
        provider_dir.mkdir(parents=True, exist_ok=True)
        raw_path = provider_dir / f"{basename}.json"
        raw_artifact = {
            "schema_version": "apma.provider-response.v1",
            "created_at": _utc_now(),
            "job_id": job_id,
            "run_id": run_id,
            "provider": self.provider,
            "provider_code": spec.provider_code,
            "requested_model": model,
            "resolved_model": candidate["resolved_model"],
            "resolved_model_version": candidate["resolved_model_version"],
            "request_id": request_id,
            "provider_task_id": response.get("task_id"),
            "request_features": {
                "audio_file_bytes": path.stat().st_size,
                "provider_timestamps": bool(
                    cfg.enable_provider_timestamps or self.provider == "alibaba"
                ),
                "gemini_speaker_attribution": bool(
                    cfg.enable_gemini_speaker_attribution
                ),
                "qwen_filetrans_speaker_attribution": bool(
                    self.provider == "alibaba"
                    and cfg.qwen_filetrans_diarization_enabled
                ),
                "speaker_identity_scope": (
                    "provider_native_single_clip"
                    if cfg.enable_gemini_speaker_attribution
                    or (
                        self.provider == "alibaba"
                        and cfg.qwen_filetrans_diarization_enabled
                    )
                    else None
                ),
                "file_staging_mode": response.get("file_staging_mode"),
                "temporary_oss_cleanup": response.get("temporary_oss_cleanup"),
                "provider_managed_retention_hours": response.get(
                    "provider_managed_retention_hours"
                ),
            },
            "raw_response_redactions": response.get("raw_response_redactions") or [],
            "raw_response": provider_raw,
        }
        raw_path.write_text(json.dumps(raw_artifact, indent=2, ensure_ascii=False), encoding="utf-8")

        if not candidate["text"].strip():
            details = (
                _gemini_empty_response_details(provider_raw)
                if self.provider == "google"
                else "provider supplied no text"
            )
            raise RuntimeError(
                f"{self.provider} returned no transcript text ({details}); "
                f"response retained at {raw_path}"
            )
        transcript_text, script_normalization = normalize_generated_text(
            candidate["text"], cfg.chinese_script_preference
        )
        transcript_segments = normalize_segment_texts(
            candidate["segments"], cfg.chinese_script_preference
        )

        start = float(chunk_meta.get("start_sec", chunk_meta.get("start", 0.0)))
        timing = normalize_provider_timing(
            words=raw.get("words") if isinstance(raw.get("words"), list) else [],
            segments=raw.get("segments") if isinstance(raw.get("segments"), list) else [],
            chunk_start_sec=start,
            chunk_duration_sec=duration,
        )
        timing["provider_native"]["words"] = normalize_segment_texts(
            timing["provider_native"]["words"], cfg.chinese_script_preference
        )
        timing["provider_native"]["segments"] = normalize_segment_texts(
            timing["provider_native"]["segments"], cfg.chinese_script_preference
        )
        timing.update(
            {
                "provider": self.provider,
                "provider_code": spec.provider_code,
                "model": model,
                "provider_artifact_path": str(raw_path),
            }
        )
        has_provider_timing = bool(
            timing["provider_native"]["words"] or timing["provider_native"]["segments"]
        )
        timing_requested = bool(
            cfg.enable_provider_timestamps
            or cfg.enable_gemini_speaker_attribution
            or self.provider == "alibaba"
        )
        timing["requested"] = timing_requested
        timing["capability_status"] = (
            "available"
            if has_provider_timing
            else "requested_but_not_returned"
            if timing_requested
            else "not_requested"
        )
        provider_speakers = []
        for item in [
            *timing["provider_native"]["words"],
            *timing["provider_native"]["segments"],
        ]:
            speaker = str(item.get("provider_speaker") or item.get("speaker") or "").strip()
            if speaker and speaker not in provider_speakers:
                provider_speakers.append(speaker)
        actual_cost = (
            float(response.get("cost_usd", estimated_cost))
            if status["cost_cap_included"]
            else 0.0
        )
        transcript_dir = job_dir / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{basename}.json"
        payload = {
            "schema_version": "apma.transcript.chunk.v1",
            "job_id": job_id,
            "chunk_filename": path.name,
            "chunk_sha256": _sha256(path),
            "transcript_path": str(transcript_path),
            "duration_seconds": duration,
            "created_at": _utc_now(),
            "provider": self.provider,
            "provider_code": spec.provider_code,
            "model": model,
            "requested_model": model,
            "resolved_model": candidate["resolved_model"],
            "resolved_model_version": candidate["resolved_model_version"],
            "request_id": request_id,
            "run_id": run_id,
            "response_format": spec.response_format,
            "segment_timing_scope": "chunk" if has_provider_timing or spec.diarization else "job",
            "chunk_start_sec": start,
            "chunk_end_sec": start + duration,
            "diarized": bool(spec.diarization),
            "speaker_attribution": {
                "requested": bool(
                    cfg.enable_gemini_speaker_attribution
                    or (
                        self.provider == "alibaba"
                        and cfg.qwen_filetrans_diarization_enabled
                    )
                ),
                "source": "provider_native" if provider_speakers else None,
                "identity_scope": (
                    "provider_native_single_clip" if provider_speakers else None
                ),
                "provider_speaker_ids": provider_speakers,
                "speaker_count": len(provider_speakers),
                "names_invented": False,
            },
            "provider_artifact_path": str(raw_path),
            "timing": timing,
            "provider_text": candidate["text"],
            "text": transcript_text,
            "segments": transcript_segments,
            "script_normalization": script_normalization,
            "word_count": len(transcript_text.split()),
            "character_count": len(transcript_text),
            "estimated_cost_usd": estimated_cost,
            "actual_cost_usd": actual_cost,
            "billing_mode": status["billing_mode"],
            "cost_cap_included": status["cost_cap_included"],
            "billing_display": status["billing_display"],
            "notes": (
                []
                if has_provider_timing or not timing_requested
                else ["Provider timestamp mode was requested but no native timestamps were returned."]
            ),
        }
        transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return payload

    def _write_retained_outputs(
        self,
        job_id: str,
        results: list[dict],
        cfg: Config,
        model: str,
        provider_code: str,
    ) -> dict:
        output_dir = Path(cfg.storage_path) / job_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now(timezone.utc):%y%m%d%H%M} {_short_name(job_id)} {provider_code}"
        json_path = output_dir / f"{stem}.json"
        html_path = output_dir / f"{stem}.html"
        text = "\n\n".join(
            str(result.get("provider_text") or result["text"]) for result in results
        )
        provenance = {
            "provider": self.provider,
            "provider_code": provider_code,
            "requested_model": model,
            "resolved_models": sorted({str(r.get("resolved_model") or model) for r in results}),
            "resolved_model_versions": sorted(
                {str(r["resolved_model_version"]) for r in results if r.get("resolved_model_version")}
            ),
            "run_ids": sorted({str(r["run_id"]) for r in results}),
        }
        retained = {
            "schema_version": "apma.retained-transcript.v1",
            "job_id": job_id,
            "created_at": _utc_now(),
            "provenance": provenance,
            "text": text,
            "presentation_policy": "provider_native_preserved",
            "chunks": results,
        }
        json_path.write_text(json.dumps(retained, indent=2, ensure_ascii=False), encoding="utf-8")
        speaker_turns = []
        for result in results:
            for segment in result.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                speaker = str(
                    segment.get("provider_speaker")
                    or segment.get("speaker")
                    or "UNLABELLED BY PROVIDER"
                )
                speaker_turns.append(
                    '<div class="turn"><strong>'
                    + html.escape(speaker)
                    + "</strong><pre>"
                    + html.escape(str(segment.get("text") or ""))
                    + "</pre></div>"
                )
        html_path.write_text(
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
            + html.escape(stem)
            + "</title><style>body{font-family:Arial,sans-serif;margin:24px}.turn{border-left:4px solid #1f6feb;background:#f6f8fa;padding:9px;margin:8px 0}pre{white-space:pre-wrap;word-break:break-word}</style></head><body><h1>"
            + html.escape(stem)
            + "</h1><dl><dt>Provider</dt><dd>"
            + html.escape(self.provider)
            + "</dd><dt>Provider code</dt><dd>"
            + html.escape(provider_code)
            + "</dd><dt>Requested model</dt><dd>"
            + html.escape(model)
            + "</dd></dl><p>Provider presentation and native speaker labels are preserved.</p>"
            + ("<h2>Provider-native speaker turns</h2>" + "".join(speaker_turns) if speaker_turns else "")
            + "<h2>Provider transcript presentation</h2><pre>"
            + html.escape(text)
            + "</pre></body></html>",
            encoding="utf-8",
        )
        refs = {"json": str(json_path), "html": str(html_path)}
        job_dir = Path(cfg.storage_path) / job_id
        manifest = job_mod.read_manifest(job_dir)
        manifest.setdefault("transcription", {})["retained_outputs"] = refs
        job_mod.write_manifest(job_dir, manifest)
        return refs


class MeralionTranscriber(_ExternalTranscriber):
    provider = "meralion"
    engine = "meralion"
    client_factory = staticmethod(MeralionTranscriptionHttpClient.from_config)

    def _model(self, cfg: Config) -> str:
        return cfg.meralion_transcription_model

    def _file_limit(self, cfg: Config) -> int:
        return min(
            int(cfg.meralion_file_size_limit_bytes),
            int(cfg.meralion_json_audio_limit_bytes),
        )

    def _call(self, client: Any, path: Path, model: str, spec: Any, cfg: Config) -> dict:
        return client.transcribe(path, model)


class GeminiTranscriber(_ExternalTranscriber):
    provider = "google"
    engine = "gemini"
    client_factory = staticmethod(GeminiTranscriptionHttpClient.from_config)

    def _model(self, cfg: Config) -> str:
        return cfg.gemini_transcription_model

    def _file_limit(self, cfg: Config) -> int:
        # Both current Google routes embed base64 audio inline. The raw-audio
        # limit therefore leaves room below the documented total request size.
        return min(
            int(cfg.gemini_inline_file_size_limit_bytes),
            int(cfg.gemini_generate_content_inline_audio_limit_bytes),
        )

    def _call(self, client: Any, path: Path, model: str, spec: Any, cfg: Config) -> dict:
        return client.transcribe(
            path,
            model,
            diarized=bool(spec.diarization),
            thinking_level=cfg.gemini_thinking_level,
        )


class QwenFiletransTranscriber(_ExternalTranscriber):
    provider = "alibaba"
    engine = "qwen_filetrans"
    client_factory = staticmethod(QwenFiletransHttpClient.from_config)

    def _model(self, cfg: Config) -> str:
        return cfg.qwen_filetrans_model

    def _file_limit(self, cfg: Config) -> int:
        limit = int(cfg.qwen_filetrans_file_size_limit_bytes)
        if str(cfg.qwen_filetrans_staging_mode).strip().lower() == "data_uri":
            limit = min(limit, int(cfg.qwen_filetrans_data_uri_limit_bytes))
        return limit

    def _call(self, client: Any, path: Path, model: str, spec: Any, cfg: Config) -> dict:
        return client.transcribe(path, model)


__all__ = [
    "DashScopeTemporaryFileUploader",
    "GeminiTranscriber",
    "GeminiTranscriptionHttpClient",
    "MeralionTranscriber",
    "MeralionTranscriptionHttpClient",
    "QwenFiletransHttpClient",
    "QwenFiletransTranscriber",
]
