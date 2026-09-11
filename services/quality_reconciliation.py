"""Guarded GPT-5.6 Sol reconciliation for chunk-matched Quality transcripts.

The model receives retained text only. Provider artifacts and the original coarse
comparison remain unchanged; the result is a separately derived, auditable draft.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any
import urllib.error
import urllib.request

from services import job as job_mod
from services.config import Config, paid_cost_authorization_amount
from services.human_review import ReviewWorkspace
from services.minutes.openai_adapter import estimate_text_tokens
from services.selective_rescue import PROVIDER_ORDER, _extract_region_wav, _wav_metadata


RECONCILIATION_PROVIDER_CODE = "GPT56Sol"
RECONCILIATION_PROVIDER_ORDER = (*PROVIDER_ORDER, RECONCILIATION_PROVIDER_CODE)
RECONCILIATION_POLICY = (
    "GPT-5.6 Sol receives the three unchanged transcripts for the same APMA-owned "
    "audio chunk. It proposes wording but cannot listen to audio and does not prove "
    "correctness. Original provider evidence remains unchanged. Any stated uncertainty "
    "requires human review with the exact audio chunk."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _repo_path(path_value: str) -> Path:
    """Resolve container-retained /app paths when code is exercised on the host."""

    path = Path(str(path_value))
    if path.exists():
        return path
    normalized = str(path_value).replace("\\", "/")
    if normalized.startswith("/app/"):
        candidate = Path(__file__).resolve().parents[1] / normalized[len("/app/") :]
        if candidate.exists():
            return candidate
    return path


def _quality_manifest(job_dir: Path) -> dict[str, Any]:
    manifest = job_mod.read_manifest(job_dir)
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        raise ValueError("Job is not an APMA Quality Transcription job")
    return manifest


def _provider_payloads(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    quality_outputs = manifest.get("quality", {}).get("outputs", {})
    provider_outputs = quality_outputs.get("providers")
    if not isinstance(provider_outputs, dict):
        raise ValueError("Quality provider outputs are missing")
    payloads: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for provider in PROVIDER_ORDER:
        output = provider_outputs.get(provider)
        if not isinstance(output, dict):
            raise ValueError(f"Retained {provider} output is missing")
        json_path = _repo_path(str(output.get("json") or ""))
        if not json_path.is_file():
            raise FileNotFoundError(f"Retained {provider} JSON is missing")
        raw_bytes = json_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(payload.get("chunks"), list):
            raise ValueError(f"Retained {provider} output has no chunk list")
        payloads[provider] = payload
        identities[provider] = {
            "provider_code": provider,
            "path": str(json_path),
            "sha256": _sha256_bytes(raw_bytes),
            "modified": False,
        }
    return payloads, identities


def build_chunk_matched_inputs(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Match unchanged provider texts by the exact source chunk identity."""

    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("Quality manifest has no retained chunk metadata")
    payloads, identities = _provider_payloads(manifest)
    provider_chunks: dict[str, dict[str, dict[str, Any]]] = {}
    for provider, payload in payloads.items():
        mapped: dict[str, dict[str, Any]] = {}
        for item in payload["chunks"]:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("chunk_filename") or "")
            if filename:
                mapped[filename] = item
        provider_chunks[provider] = mapped

    windows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        filename = str(chunk.get("filename") or "")
        chunk_sha = str(chunk.get("clip_sha256") or "")
        if not filename or not chunk_sha:
            raise ValueError("Quality chunk identity is incomplete")
        start = float(chunk.get("global_start_sec", chunk.get("start_sec", 0.0)))
        end = float(chunk.get("global_end_sec", chunk.get("end_sec", 0.0)))
        if end <= start:
            raise ValueError(f"Quality chunk {filename} has invalid global bounds")
        candidates: dict[str, dict[str, Any]] = {}
        for provider in PROVIDER_ORDER:
            retained = provider_chunks[provider].get(filename)
            if retained is None:
                raise ValueError(f"{provider} has no retained result for {filename}")
            if str(retained.get("chunk_sha256") or "") != chunk_sha:
                raise ValueError(f"{provider} result does not match {filename}")
            text = retained.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{provider} retained empty text for {filename}")
            candidates[provider] = {
                "provider_code": provider,
                "provider": retained.get("provider"),
                "model": retained.get("model"),
                "run_id": retained.get("run_id"),
                "chunk_filename": filename,
                "chunk_sha256": chunk_sha,
                "text": text,
                "transcript_path": retained.get("transcript_path"),
                "provider_response_ref": retained.get("provider_artifact_path"),
                "missing": False,
                "candidate_type": "provider_transcript",
            }
        input_identity = {
            "chunk_filename": filename,
            "chunk_sha256": chunk_sha,
            "candidates": {provider: candidates[provider]["text"] for provider in PROVIDER_ORDER},
        }
        windows.append(
            {
                "window_id": f"reconcile-chunk-{index:05d}",
                "chunk_index": index,
                "chunk_filename": filename,
                "chunk_sha256": chunk_sha,
                "global_start_sec": start,
                "global_end_sec": end,
                "duration_seconds": end - start,
                "timing_authority": "application_owned_audio_chunk_bounds",
                "provider_candidates": candidates,
                "input_sha256": _sha256_bytes(
                    json.dumps(
                        input_identity, sort_keys=True, ensure_ascii=False
                    ).encode("utf-8")
                ),
            }
        )
    return windows, identities


def _window_token_budget(window: dict[str, Any]) -> dict[str, int]:
    candidate_texts = [
        str(window["provider_candidates"][provider]["text"])
        for provider in PROVIDER_ORDER
    ]
    serialized = json.dumps(
        {provider: text for provider, text in zip(PROVIDER_ORDER, candidate_texts)},
        ensure_ascii=False,
    )
    # The existing chars/4 estimator is suitable for mainly English text but can
    # undercount Chinese. Budget ASCII at chars/3 and non-ASCII at two tokens per
    # code point so multilingual preflight remains deliberately conservative.
    ascii_count = sum(1 for char in serialized if ord(char) < 128)
    non_ascii_count = len(serialized) - ascii_count
    input_tokens = max(
        estimate_text_tokens(serialized),
        (ascii_count + 2) // 3 + non_ascii_count * 2,
    ) + 500
    longest_tokens = max(estimate_text_tokens(text) for text in candidate_texts)
    max_output_tokens = max(900, min(6000, longest_tokens + 1200))
    return {
        "estimated_input_tokens": input_tokens,
        "max_output_tokens": max_output_tokens,
    }


def estimate_reconciliation(
    manifest: dict[str, Any], cfg: Config
) -> dict[str, Any]:
    windows, provider_inputs = build_chunk_matched_inputs(manifest)
    budgets = [_window_token_budget(window) for window in windows]
    input_tokens = sum(item["estimated_input_tokens"] for item in budgets)
    output_tokens = sum(item["max_output_tokens"] for item in budgets)
    input_cost = (
        input_tokens
        / 1_000_000.0
        * float(cfg.openai_reconciliation_input_price_per_million_usd)
    )
    output_cost = (
        output_tokens
        / 1_000_000.0
        * float(cfg.openai_reconciliation_output_price_per_million_usd)
    )
    estimated_cost = input_cost + output_cost
    authorization = paid_cost_authorization_amount(estimated_cost, cfg)
    cap = min(
        float(cfg.max_reconciliation_cost_per_job_usd),
        float(cfg.max_cost_per_job_usd),
    )
    return {
        "ok": True,
        "model": cfg.openai_reconciliation_model,
        "reasoning_effort": cfg.openai_reconciliation_reasoning_effort,
        "window_count": len(windows),
        "matching_unit": "same_application_owned_audio_chunk",
        "provider_order": list(PROVIDER_ORDER),
        "estimated_input_tokens": input_tokens,
        "maximum_output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost,
        "paid_provider_cost_buffer_percent": float(
            cfg.paid_provider_cost_buffer_percent
        ),
        "run_budget_cap_usd": authorization,
        "max_reconciliation_cost_per_job_usd": cap,
        "within_cap": authorization <= cap + 1e-12,
        "price_per_million_tokens_usd": {
            "input": float(
                cfg.openai_reconciliation_input_price_per_million_usd
            ),
            "output": float(
                cfg.openai_reconciliation_output_price_per_million_usd
            ),
        },
        "provider_inputs": provider_inputs,
        "audio_sent_to_model": False,
        "provider_api_calls": 0,
    }


RECONCILIATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "proposed_text": {"type": "string"},
        "review_required": {"type": "boolean"},
        "uncertainty_reasons": {"type": "array", "items": {"type": "string"}},
        "evidence_provider_codes": {
            "type": "array",
            "items": {"type": "string", "enum": list(PROVIDER_ORDER)},
        },
        "speaker_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "proposed_text",
        "review_required",
        "uncertainty_reasons",
        "evidence_provider_codes",
        "speaker_notes",
    ],
}


class OpenAIReconciliationHttpClient:
    """Small Responses API client that retains the complete provider response."""

    def __init__(
        self,
        api_key: str,
        api_url: str,
        timeout_seconds: float,
        max_retries: int,
    ):
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, min(3, int(max_retries)))

    @classmethod
    def from_config(cls, cfg: Config) -> "OpenAIReconciliationHttpClient":
        key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for GPT-5.6 Sol reconciliation")
        return cls(
            key,
            cfg.openai_minutes_api_url,
            cfg.openai_reconciliation_timeout_seconds,
            cfg.openai_reconciliation_max_retries,
        )

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        chunks: list[str] = []
        for item in payload.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        return "\n".join(chunks).strip()

    def reconcile(
        self,
        *,
        model: str,
        reasoning_effort: str,
        window: dict[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        candidates = {
            provider: window["provider_candidates"][provider]["text"]
            for provider in PROVIDER_ORDER
        }
        body = {
            "model": model,
            "store": False,
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": int(max_output_tokens),
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are APMA's conservative transcript reconciliation assistant. "
                        "The three texts came from the exact same audio chunk. Produce a "
                        "faithful transcript proposal, not a summary. Do not invent speech, "
                        "facts, names, numbers, or speaker identities. Preserve code-switching. "
                        "Do not standardize the providers' script, punctuation, or formatting "
                        "merely to make them look alike. Do not translate Hokkien into "
                        "Mandarin. Treat gpt4oDiarz speaker labels only as speaker/timing "
                        "evidence, not automatically superior wording. If material wording is "
                        "uncertain, set review_required true and explain briefly."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "window_id": window["window_id"],
                            "global_start_sec": window["global_start_sec"],
                            "global_end_sec": window["global_end_sec"],
                            "provider_candidates": candidates,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "apma_chunk_reconciliation",
                    "strict": True,
                    "schema": RECONCILIATION_SCHEMA,
                },
            },
        }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(
                self.api_url,
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                text = self._output_text(raw)
                if not text:
                    raise RuntimeError("GPT-5.6 Sol response did not include output text")
                return {"result": json.loads(text), "raw_response": raw}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"GPT-5.6 Sol request failed with HTTP {exc.code}: {detail}"
                )
                if exc.code not in {408, 409, 429} and exc.code < 500:
                    break
            except (
                urllib.error.URLError,
                TimeoutError,
                RuntimeError,
                ValueError,
            ) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(4.0, float(2 ** (attempt - 1))))
        raise RuntimeError(
            f"GPT-5.6 Sol reconciliation failed after {self.max_retries} attempts: "
            f"{last_error}"
        )


def _validate_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("GPT-5.6 Sol structured result must be an object")
    proposed = result.get("proposed_text")
    if not isinstance(proposed, str) or not proposed.strip():
        raise ValueError("GPT-5.6 Sol proposed_text is empty")
    reasons = result.get("uncertainty_reasons")
    evidence = result.get("evidence_provider_codes")
    speaker_notes = result.get("speaker_notes")
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        raise ValueError("GPT-5.6 Sol uncertainty_reasons is invalid")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(item not in PROVIDER_ORDER for item in evidence)
    ):
        raise ValueError("GPT-5.6 Sol evidence_provider_codes is invalid")
    if not isinstance(speaker_notes, list) or not all(
        isinstance(x, str) for x in speaker_notes
    ):
        raise ValueError("GPT-5.6 Sol speaker_notes is invalid")
    review_required = bool(result.get("review_required")) or bool(reasons)
    return {
        "proposed_text": proposed,
        "review_required": review_required,
        "uncertainty_reasons": reasons,
        "evidence_provider_codes": list(dict.fromkeys(evidence)),
        "speaker_notes": speaker_notes,
    }


def _usage_cost(raw: dict[str, Any], cfg: Config) -> dict[str, Any]:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cost = (
        input_tokens
        / 1_000_000.0
        * float(cfg.openai_reconciliation_input_price_per_million_usd)
        + output_tokens
        / 1_000_000.0
        * float(cfg.openai_reconciliation_output_price_per_million_usd)
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
        "estimated_actual_cost_usd": cost,
    }


def _render_reconciliation(payload: dict[str, Any]) -> str:
    rows: list[str] = []
    for window in payload["windows"]:
        proposal = window["gpt56_sol_proposal"]
        candidate_cells = "".join(
            f"<td><h4>{html.escape(provider)}</h4><pre>{html.escape(window['provider_candidates'][provider]['text'])}</pre></td>"
            for provider in PROVIDER_ORDER
        )
        status = "REVIEW REQUIRED" if proposal["review_required"] else "AI DRAFT READY"
        reasons = "<br>".join(html.escape(item) for item in proposal["uncertainty_reasons"])
        rows.append(
            "<tr><td>"
            f"{window['global_start_sec']:.3f}–{window['global_end_sec']:.3f}s"
            f"</td><td><strong>{status}</strong><br>{reasons}</td>"
            + candidate_cells
            + f"<td><pre>{html.escape(proposal['proposed_text'])}</pre></td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>APMA GPT-5.6 Sol Reconciliation</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #bbb;padding:9px;vertical-align:top}th{background:#1f4e78;color:white}pre{white-space:pre-wrap;word-break:break-word}.note{padding:12px;background:#eef3f8;border-left:4px solid #1f4e78}</style>"
        "</head><body><h1>APMA GPT-5.6 Sol Assisted Reconciliation</h1>"
        f"<p class=\"note\">{html.escape(RECONCILIATION_POLICY)}</p>"
        "<table><thead><tr><th>TIME</th><th>STATUS</th>"
        + "".join(f"<th>{html.escape(provider)}</th>" for provider in PROVIDER_ORDER)
        + "<th>GPT-5.6 SOL PROPOSED DRAFT</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


def _extract_review_audio(
    source_audio: Path,
    windows: list[dict[str, Any]],
    output_dir: Path,
    cfg: Config,
    *,
    source_global_start_sec: float,
) -> None:
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for index, window in enumerate(windows, start=1):
        clip_path = chunks_dir / f"chunk-{index:05d}.wav"
        _extract_region_wav(
            source_audio,
            clip_path,
            source_global_start_sec=float(source_global_start_sec),
            region_global_start_sec=float(window["global_start_sec"]),
            region_global_end_sec=float(window["global_end_sec"]),
            cfg=cfg,
        )
        metadata = _wav_metadata(clip_path)
        actual_duration = metadata["n_frames"] / metadata["framerate"]
        if abs(actual_duration - float(window["duration_seconds"])) > 0.1:
            raise RuntimeError("Reconciliation review clip duration mismatch")
        clip_sha = metadata["processing_audio_sha256"]
        window["clip_id"] = f"reconcile-clip-{index:05d}-{clip_sha[:12]}"
        window["clip_sha256"] = clip_sha


def _build_final_draft(
    reconciliation: dict[str, Any], source_sha256: str
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    auto_accepted = 0
    review_required = 0
    for source in reconciliation["windows"]:
        proposal = source["gpt56_sol_proposal"]
        candidates = deepcopy(source["provider_candidates"])
        candidates[RECONCILIATION_PROVIDER_CODE] = {
            "provider_code": RECONCILIATION_PROVIDER_CODE,
            "provider": "OpenAI",
            "model": reconciliation["model"],
            "candidate_type": "derived_reconciliation",
            "text": proposal["proposed_text"],
            "missing": False,
            "input_provider_codes": list(PROVIDER_ORDER),
            "evidence_provider_codes": proposal["evidence_provider_codes"],
            "raw_response_ref": source["raw_response_ref"],
            "input_sha256": source["input_sha256"],
        }
        needs_review = bool(proposal["review_required"])
        window = {
            "window_id": source["window_id"],
            "global_start_sec": source["global_start_sec"],
            "global_end_sec": source["global_end_sec"],
            "duration_seconds": source["duration_seconds"],
            "derived_status": "AMBER" if needs_review else "GREEN",
            "derived_status_basis": (
                "GPT-5.6 Sol derived reconciliation uncertainty flag; not correctness "
                "and not provider confidence."
            ),
            "clip_id": source["clip_id"],
            "clip_sha256": source["clip_sha256"],
            "timing_authority": "application_owned_audio_chunk_bounds",
            "boundary": {"strategy": "existing_quality_chunk"},
            "provider_candidates": candidates,
            "reconciliation_uncertainty_reasons": proposal["uncertainty_reasons"],
            "speaker_notes": proposal["speaker_notes"],
            "correctness_claimed": False,
        }
        if needs_review:
            window.update(
                {
                    "final_text": None,
                    "selected_text": None,
                    "selected_provider": None,
                    "selected_candidate_provenance": None,
                    "selection_method": None,
                    "auto_accepted": False,
                    "review_required": True,
                }
            )
            review_required += 1
        else:
            window.update(
                {
                    "final_text": proposal["proposed_text"],
                    "selected_text": proposal["proposed_text"],
                    "selected_provider": RECONCILIATION_PROVIDER_CODE,
                    "selected_candidate_provenance": deepcopy(
                        {
                            key: value
                            for key, value in candidates[
                                RECONCILIATION_PROVIDER_CODE
                            ].items()
                            if key not in {"text", "missing"}
                        }
                    ),
                    "selection_method": "gpt56_sol_assisted_reconciliation",
                    "auto_accepted": True,
                    "review_required": False,
                }
            )
            auto_accepted += 1
        windows.append(window)
    return {
        "schema_version": "apma.final-draft.v1",
        "provider_order": list(RECONCILIATION_PROVIDER_ORDER),
        "source_reconciliation": {
            "sha256": source_sha256,
            "modified": False,
            "model": reconciliation["model"],
        },
        "policy": RECONCILIATION_POLICY,
        "statistics": {
            "total_windows": len(windows),
            "auto_accepted": auto_accepted,
            "review_required": review_required,
            "status_counts": {
                "GREEN": auto_accepted,
                "AMBER": review_required,
                "RED": 0,
            },
        },
        "windows": windows,
    }


def _render_final_draft(draft: dict[str, Any]) -> str:
    rows: list[str] = []
    for window in draft["windows"]:
        proposal = window["provider_candidates"][RECONCILIATION_PROVIDER_CODE]["text"]
        state = (
            "AI DRAFT READY — NO CORRECTNESS CLAIM"
            if window["auto_accepted"]
            else "HUMAN REVIEW REQUIRED"
        )
        rows.append(
            "<tr><td>"
            f"{window['global_start_sec']:.3f}–{window['global_end_sec']:.3f}s"
            f"</td><td>{html.escape(state)}</td><td><pre>{html.escape(proposal)}</pre></td>"
            f"<td>{html.escape('; '.join(window['reconciliation_uncertainty_reasons']))}</td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>APMA GPT-5.6 Sol Final Draft</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:10px;vertical-align:top}th{background:#1f4e78;color:white}pre{white-space:pre-wrap;word-break:break-word}</style>"
        "</head><body><h1>APMA GPT-5.6 Sol Assisted Final Draft</h1>"
        f"<p>{html.escape(RECONCILIATION_POLICY)}</p><table><thead><tr><th>TIME</th><th>STATE</th><th>PROPOSED TEXT</th><th>UNCERTAINTY</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


def _has_human_decisions(manifest: dict[str, Any]) -> bool:
    reviewed = manifest.get("quality", {}).get("outputs", {}).get("final_reviewed")
    if not isinstance(reviewed, dict):
        return False
    reviewed_path = _repo_path(str(reviewed.get("json") or ""))
    decisions_path = reviewed_path.parent / "review_decisions.json"
    if not decisions_path.is_file():
        return False
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    return bool(payload.get("decisions"))


def _cleanup_obsolete_review_audio(
    old_audio_dir: Path | None, new_audio_dir: Path, job_dir: Path
) -> dict[str, Any]:
    result = {"removed": False, "bytes_removed": 0}
    if old_audio_dir is None or not old_audio_dir.is_dir():
        return result
    old_audio_dir = old_audio_dir.resolve()
    new_audio_dir = new_audio_dir.resolve()
    job_dir = job_dir.resolve()
    if old_audio_dir == new_audio_dir:
        return result
    try:
        old_audio_dir.relative_to(job_dir)
    except ValueError:
        return result
    bytes_removed = sum(
        path.stat().st_size for path in old_audio_dir.rglob("*") if path.is_file()
    )
    shutil.rmtree(old_audio_dir)
    return {"removed": True, "bytes_removed": bytes_removed}


def run_reconciliation(
    job_dir: Path,
    cfg: Config,
    *,
    confirm_live_reconciliation: bool,
    run_budget_cap_usd: float,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run or resume chunk-matched reconciliation, then activate its lean review."""

    job_dir = Path(job_dir)
    manifest = _quality_manifest(job_dir)
    estimate = estimate_reconciliation(manifest, cfg)
    errors: list[str] = []
    if cfg.dry_run:
        errors.append("DRY_RUN must be false for live GPT-5.6 Sol reconciliation.")
    if not cfg.enable_live_openai_reconciliation:
        errors.append("ENABLE_LIVE_OPENAI_RECONCILIATION must be true.")
    if not (cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")):
        errors.append("OPENAI_API_KEY must be set in the server environment.")
    if not confirm_live_reconciliation:
        errors.append("GPT-5.6 Sol reconciliation requires explicit confirmation.")
    if abs(float(run_budget_cap_usd) - float(estimate["run_budget_cap_usd"])) > 1e-6:
        errors.append("Reconciliation budget changed. Estimate again before running.")
    if not estimate["within_cap"]:
        errors.append("Reconciliation estimate exceeds its configured cost cap.")
    if _has_human_decisions(manifest):
        errors.append(
            "Existing human review decisions must be preserved; reconciliation cannot replace this active review."
        )
    if errors:
        return {"ok": False, "errors": errors, "estimate": estimate}

    quality = manifest["quality"]
    previous_audio_value = quality.get("review_audio_dir")
    previous_audio_dir = (
        _repo_path(str(previous_audio_value)) if previous_audio_value else None
    )
    root = job_dir / "quality" / "reconciliation"
    raw_dir = root / "raw-responses"
    state_path = root / "reconciliation_state.json"
    windows, provider_inputs = build_chunk_matched_inputs(manifest)
    state = {
        "schema_version": "apma.gpt56-reconciliation-state.v1",
        "model": cfg.openai_reconciliation_model,
        "windows": {},
    }
    if state_path.is_file():
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        if existing.get("model") == cfg.openai_reconciliation_model:
            state = existing
    api = client or OpenAIReconciliationHttpClient.from_config(cfg)
    new_calls = 0
    reused_calls = 0
    actual_cost = 0.0
    reconciled_windows: list[dict[str, Any]] = []
    for window in windows:
        cached = state.get("windows", {}).get(window["window_id"])
        raw_path = raw_dir / f"{window['window_id']}.json"
        if (
            isinstance(cached, dict)
            and cached.get("input_sha256") == window["input_sha256"]
            and raw_path.is_file()
        ):
            validated = _validate_result(cached["result"])
            usage = cached.get("usage") or {}
            reused_calls += 1
        else:
            budget = _window_token_budget(window)
            response = api.reconcile(
                model=cfg.openai_reconciliation_model,
                reasoning_effort=cfg.openai_reconciliation_reasoning_effort,
                window=window,
                max_output_tokens=budget["max_output_tokens"],
            )
            validated = _validate_result(response["result"])
            _atomic_json(raw_path, response["raw_response"])
            usage = _usage_cost(response["raw_response"], cfg)
            state.setdefault("windows", {})[window["window_id"]] = {
                "input_sha256": window["input_sha256"],
                "result": validated,
                "usage": usage,
                "raw_response_ref": str(raw_path),
            }
            _atomic_json(state_path, state)
            new_calls += 1
        actual_cost += float(usage.get("estimated_actual_cost_usd") or 0.0)
        if actual_cost > float(run_budget_cap_usd) + 1e-12:
            raise RuntimeError(
                "GPT-5.6 Sol actual usage reached the approved reconciliation budget; "
                "no further model calls were made. Completed windows remain reusable."
            )
        item = deepcopy(window)
        item["gpt56_sol_proposal"] = validated
        item["raw_response_ref"] = str(raw_path)
        item["usage"] = usage
        reconciled_windows.append(item)

    source_audio = _repo_path(str(manifest.get("source", {}).get("path") or ""))
    if not source_audio.is_file():
        raise FileNotFoundError("Retained source audio is unavailable for review clips")
    _extract_review_audio(
        source_audio,
        reconciled_windows,
        root / "review-clips",
        cfg,
        source_global_start_sec=float(quality.get("timeline_offset_seconds") or 0.0),
    )
    reconciliation = {
        "schema_version": "apma.gpt56-reconciliation.v1",
        "created_at": _utc_now(),
        "model": cfg.openai_reconciliation_model,
        "reasoning_effort": cfg.openai_reconciliation_reasoning_effort,
        "policy": RECONCILIATION_POLICY,
        "matching_unit": "same_application_owned_audio_chunk",
        "provider_order": list(PROVIDER_ORDER),
        "provider_inputs": provider_inputs,
        "source_audio": {
            "sha256": str(manifest.get("source", {}).get("sha256") or ""),
            "audio_sent_to_model": False,
        },
        "cost": {
            "preflight": estimate,
            "actual_usage_estimate_usd": actual_cost,
        },
        "calls": {"new": new_calls, "reused": reused_calls},
        "statistics": {
            "total_windows": len(reconciled_windows),
            "review_required": sum(
                1
                for item in reconciled_windows
                if item["gpt56_sol_proposal"]["review_required"]
            ),
        },
        "windows": reconciled_windows,
    }
    reconciliation_json = root / "GPT56_SOL_RECONCILIATION.json"
    reconciliation_html = root / "GPT56_SOL_RECONCILIATION.html"
    _atomic_json(reconciliation_json, reconciliation)
    _atomic_text(reconciliation_html, _render_reconciliation(reconciliation))

    draft = _build_final_draft(reconciliation, _sha256_file(reconciliation_json))
    draft_dir = root / "final-draft"
    draft_json = draft_dir / "FINAL_DRAFT.json"
    draft_html = draft_dir / "FINAL_DRAFT.html"
    _atomic_json(draft_json, draft)
    _atomic_text(draft_html, _render_final_draft(draft))
    workspace = ReviewWorkspace(
        draft_json, root / "review-clips" / "chunks", root / "review"
    )
    reviewed_outputs = workspace.write_reviewed_outputs()
    view = workspace.view()

    outputs = quality.setdefault("outputs", {})
    outputs.setdefault("pre_reconciliation_final_draft", deepcopy(outputs.get("final_draft")))
    outputs.setdefault(
        "pre_reconciliation_final_reviewed", deepcopy(outputs.get("final_reviewed"))
    )
    outputs["reconciliation"] = {
        "json": str(reconciliation_json),
        "html": str(reconciliation_html),
    }
    outputs["final_draft"] = {"json": str(draft_json), "html": str(draft_html)}
    outputs["final_reviewed"] = reviewed_outputs
    quality["review_audio_dir"] = str(root / "review-clips" / "chunks")
    unresolved = int(view["statistics"]["unresolved_windows"])
    quality["reconciliation"] = {
        "status": "completed",
        "model": cfg.openai_reconciliation_model,
        "window_count": len(reconciled_windows),
        "review_required": unresolved,
        "new_calls": new_calls,
        "reused_calls": reused_calls,
        "actual_usage_estimate_usd": actual_cost,
    }
    quality["overall_status"] = "REVIEW_REQUIRED" if unresolved else "COMPLETE"
    quality["review_url"] = f"/review?job_id={manifest['job_id']}"
    quality.setdefault("stages", {}).setdefault("REVIEW", {})[
        "unresolved_windows"
    ] = unresolved
    manifest["state"] = "review_required" if unresolved else "complete"
    cleanup = _cleanup_obsolete_review_audio(
        previous_audio_dir,
        root / "review-clips" / "chunks",
        job_dir,
    )
    quality["reconciliation"]["obsolete_review_audio_cleanup"] = cleanup
    manifest["updated_at"] = _utc_now()
    job_mod.write_manifest(job_dir, manifest)
    return {
        "ok": True,
        "job_id": manifest["job_id"],
        "state": manifest["state"],
        "review_url": quality["review_url"],
        "statistics": view["statistics"],
        "calls": reconciliation["calls"],
        "cost": reconciliation["cost"],
        "outputs": {
            "reconciliation": outputs["reconciliation"],
            "final_draft": outputs["final_draft"],
            "final_reviewed": outputs["final_reviewed"],
        },
    }


__all__ = [
    "OpenAIReconciliationHttpClient",
    "RECONCILIATION_PROVIDER_CODE",
    "RECONCILIATION_PROVIDER_ORDER",
    "build_chunk_matched_inputs",
    "estimate_reconciliation",
    "run_reconciliation",
]
