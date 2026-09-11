from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import job as job_mod
from services.config import load_config
from services.speaker_timeline import (
    attach_speaker_evidence_to_final,
    build_speaker_timeline,
    write_speaker_timeline,
)
from services.transcription.external_adapter import GeminiTranscriber
from services.transcription.router import get_runtime_model_status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return float(wav_file.getnframes()) / float(wav_file.getframerate())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one guarded Gem35T provider-native speaker attribution proof."
    )
    parser.add_argument("--input-wav", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--storage-path", required=True)
    parser.add_argument("--global-start-sec", required=True, type=float)
    parser.add_argument("--max-cost-usd", required=True, type=float)
    parser.add_argument("--attempt-number", required=True, type=int, choices=(1, 2, 3))
    parser.add_argument("--quality-evidence", action="append", default=[])
    parser.add_argument("--final-draft-json")
    parser.add_argument("--confirm-live-api", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_live_api:
        print("Gem35T speaker proof was not started: explicit live confirmation is required.")
        return 2
    source = Path(args.input_wav).resolve()
    if not source.is_file() or source.suffix.lower() != ".wav":
        print(f"Input must be an existing WAV file: {source}")
        return 2
    duration = _duration(source)
    if not 45.0 <= duration <= 90.0:
        print("Speaker proof input must be approximately 45-90 seconds.")
        return 2

    cfg = load_config()
    cfg.storage_path = str(Path(args.storage_path).resolve())
    cfg.dry_run = False
    cfg.enable_live_gemini_transcription = True
    cfg.enable_provider_timestamps = True
    cfg.enable_gemini_speaker_attribution = True
    cfg.gemini_transcription_model = cfg.gemini_transcribe_model
    cfg.max_cost_per_job_usd = float(args.max_cost_usd)
    cfg.external_transcription_max_retries = 1
    if not cfg.gemini_api_key:
        print("Gem35T speaker proof was not started: GEMINI_API_KEY is unavailable.")
        return 2
    status = get_runtime_model_status(cfg.gemini_transcribe_model, cfg)
    if not status["runnable"]:
        print(f"Gem35T speaker proof was not started: {status['readiness_reason']}")
        return 2
    estimated_cost = duration / 60.0 * float(status["price_per_minute_usd"])
    if estimated_cost > cfg.max_cost_per_job_usd + 1e-12:
        print("Gem35T speaker proof estimate exceeds the explicit cost cap.")
        return 2

    quality_paths = [Path(value).resolve() for value in args.quality_evidence]
    quality_before = {str(path): _sha256(path) for path in quality_paths}
    job_dir = job_mod.create_job(args.job_id, cfg.storage_path)
    source_sha = _sha256(source)
    chunk = {
        "chunk_id": 1,
        "chunk_index": 1,
        "filename": source.name,
        "path": str(source),
        "start_sec": float(args.global_start_sec),
        "end_sec": float(args.global_start_sec) + duration,
        "global_start_sec": float(args.global_start_sec),
        "global_end_sec": float(args.global_start_sec) + duration,
        "duration_seconds": duration,
        "actual_bytes": source.stat().st_size,
        "clip_sha256": source_sha,
        "source_identity": {
            "filename": source.name,
            "sha256": source_sha,
        },
    }
    manifest = job_mod.read_manifest(job_dir)
    manifest["source"] = {
        "filename": source.name,
        "path": str(source),
        "sha256": source_sha,
        "duration_seconds": duration,
        "global_start_sec": float(args.global_start_sec),
        "authorized_local_proof": True,
    }
    manifest["chunks"] = [chunk]
    manifest["speaker_attribution"] = {
        "provider_code": "Gem35T",
        "attempt_number": args.attempt_number,
        "mode": "verbatim_provider_native_speaker_and_word_timestamps",
        "estimated_cost_usd": round(estimated_cost, 6),
        "max_cost_usd": cfg.max_cost_per_job_usd,
    }
    job_mod.write_manifest(job_dir, manifest)

    results = GeminiTranscriber().transcribe_chunks(args.job_id, [chunk], cfg)
    if len(results) != 1:
        raise RuntimeError("Gem35T speaker proof made an unexpected number of provider calls")
    retained_path = Path(results[0]["retained_output_paths"]["json"])
    retained_payload = json.loads(retained_path.read_text(encoding="utf-8"))
    timeline = build_speaker_timeline(
        retained_payload,
        source_sha256=_sha256(retained_path),
        quality_evidence_paths=quality_paths,
    )
    output_dir = job_dir / "speaker-evidence"
    timeline_paths = write_speaker_timeline(timeline, output_dir)

    enriched_final_path = None
    if args.final_draft_json:
        final_path = Path(args.final_draft_json).resolve()
        final_before = _sha256(final_path)
        final_payload = json.loads(final_path.read_text(encoding="utf-8"))
        final_texts = [window.get("final_text") for window in final_payload.get("windows") or []]
        enriched = attach_speaker_evidence_to_final(final_payload, timeline)
        if [window.get("final_text") for window in enriched.get("windows") or []] != final_texts:
            raise RuntimeError("Speaker enrichment changed quality final text")
        enriched_final_path = output_dir / "FINAL_DRAFT_WITH_SPEAKER_EVIDENCE.json"
        enriched_final_path.write_text(
            json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if _sha256(final_path) != final_before:
            raise RuntimeError("Original FINAL_DRAFT changed during speaker enrichment")

    quality_after = {str(path): _sha256(path) for path in quality_paths}
    if quality_after != quality_before:
        raise RuntimeError("Existing quality transcript evidence changed during speaker proof")
    credential = str(cfg.gemini_api_key)
    persisted_paths = [
        Path(results[0]["provider_artifact_path"]),
        Path(results[0]["transcript_path"]),
        retained_path,
        Path(results[0]["retained_output_paths"]["html"]),
        Path(timeline_paths["json"]),
        Path(timeline_paths["html"]),
    ]
    if enriched_final_path:
        persisted_paths.append(enriched_final_path)
    for path in persisted_paths:
        if credential and credential in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"Credential material was written to {path}")

    manifest = job_mod.read_manifest(job_dir)
    manifest["state"] = "completed"
    manifest["speaker_attribution"].update(
        {
            "provider_calls": 1,
            "speaker_count": timeline["statistics"]["speaker_count"],
            "speaker_ids": timeline["statistics"]["speaker_ids"],
            "word_count": timeline["statistics"]["word_count"],
            "segment_count": timeline["statistics"]["segment_count"],
            "timeline_outputs": timeline_paths,
            "enriched_final_json": str(enriched_final_path) if enriched_final_path else None,
            "quality_transcript_unchanged": True,
            "credentials_persisted": False,
        }
    )
    job_mod.write_manifest(job_dir, manifest)
    print(
        json.dumps(
            {
                "job_id": args.job_id,
                "attempt_number": args.attempt_number,
                "provider_calls": 1,
                "speaker_count": timeline["statistics"]["speaker_count"],
                "speaker_ids": timeline["statistics"]["speaker_ids"],
                "word_count": timeline["statistics"]["word_count"],
                "segment_count": timeline["statistics"]["segment_count"],
                "timeline_outputs": timeline_paths,
                "enriched_final_json": str(enriched_final_path) if enriched_final_path else None,
                "quality_transcript_unchanged": True,
                "credentials_persisted": False,
                "estimated_cost_usd": round(estimated_cost, 6),
            },
            indent=2,
        )
    )
    return 0 if timeline["statistics"]["speaker_count"] >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
