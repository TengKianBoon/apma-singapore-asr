from __future__ import annotations

import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import List, Dict

from services.transcription.base import Transcriber
from services.config import Config


def _resolve_chunk_sha256(job_id: str, chunk_meta: dict, cfg: Config) -> str | None:
    declared = chunk_meta.get("chunk_sha256") or chunk_meta.get("sha256")
    if declared:
        return str(declared)

    candidate = chunk_meta.get("path")
    if candidate is None:
        filename = chunk_meta.get("filename") or chunk_meta.get("chunk_filename")
        if filename:
            candidate = Path(cfg.storage_path) / job_id / "chunks" / filename

    chunk_path = Path(candidate) if candidate else None
    if chunk_path is None or not chunk_path.is_file():
        return None

    digest = hashlib.sha256()
    with chunk_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MockTranscriber(Transcriber):
    """Deterministic mock transcriber for dry-run testing.

    Writes a JSON transcript file per chunk under `jobs/<job_id>/transcripts/`.
    """

    SEGMENT_SECONDS = 5.0
    FIXED_CONFIDENCE = 0.99

    def transcribe_chunk(self, job_id: str, chunk_meta: dict, cfg: Config) -> dict:
        if not getattr(cfg, "dry_run", True):
            raise RuntimeError("MockTranscriber can only run when cfg.dry_run is True")

        # derive timing
        start = float(chunk_meta.get("start_sec", chunk_meta.get("start", 0.0)))
        end = chunk_meta.get("end_sec", chunk_meta.get("end", None))
        if end is None:
            # try frames/framerate fallback
            frames = chunk_meta.get("frames")
            framerate = chunk_meta.get("framerate")
            if frames is not None and framerate:
                duration = float(frames) / float(framerate)
                end = start + duration
            else:
                end = start + self.SEGMENT_SECONDS
        end = float(end)

        duration = max(0.0, end - start)

        # segments
        n_seg = max(1, int(math.ceil(duration / self.SEGMENT_SECONDS)))
        segments: List[Dict] = []
        for i in range(n_seg):
            seg_start = start + i * self.SEGMENT_SECONDS
            seg_end = min(end, seg_start + self.SEGMENT_SECONDS)
            chunk_filename = chunk_meta.get("filename") or chunk_meta.get("chunk_filename") or f"chunk-{i+1:05d}.wav"
            text = f"Mock transcript for {chunk_filename} segment {i+1} ({seg_start:.2f}-{seg_end:.2f}s)."
            segments.append({
                "start_sec": float(seg_start),
                "end_sec": float(seg_end),
                "speaker": None,
                "text": text,
            })

        word_count = sum(len(s["text"].split()) for s in segments)
        char_count = sum(len(s["text"]) for s in segments)

        created_at = datetime.datetime.utcnow().isoformat() + "Z"

        # determine paths
        storage = Path(cfg.storage_path)
        transcripts_dir = storage / job_id / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)

        chunk_filename = chunk_meta.get("filename") or chunk_meta.get("chunk_filename") or "chunk-00000.wav"
        basename = Path(chunk_filename).stem
        out_name = f"{basename}.json"
        out_path = transcripts_dir / out_name

        payload = {
            "schema_version": "apma.transcript.chunk.v1",
            "job_id": job_id,
            "chunk_filename": chunk_filename,
            "chunk_sha256": _resolve_chunk_sha256(job_id, chunk_meta, cfg),
            "transcript_path": str(out_path),
            "duration_seconds": float(duration),
            "created_at": created_at,
            "provider": "mock",
            "provider_code": "mock",
            "model": "mock",
            "run_id": "mock-" + hashlib.sha256(
                f"{job_id}|{chunk_filename}".encode("utf-8")
            ).hexdigest()[:12],
            "response_format": "json",
            "segment_timing_scope": "job",
            "chunk_start_sec": start,
            "chunk_end_sec": end,
            "diarized": False,
            "text": "\n".join(segment["text"] for segment in segments),
            "segments": segments,
            "word_count": int(word_count),
            "character_count": int(char_count),
            "confidence": float(self.FIXED_CONFIDENCE),
            "estimated_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "notes": [],
        }

        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

        return payload


__all__ = ["MockTranscriber"]
