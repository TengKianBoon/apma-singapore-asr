from pathlib import Path
import re
import subprocess
import wave
from typing import List


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


def _parse_silence_intervals(stderr: str, source_duration_sec: float) -> list[tuple[float, float]]:
    """Parse FFmpeg silencedetect output into bounded source-time intervals."""
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
        end_match = _SILENCE_END_RE.search(line)
        if end_match:
            end = min(source_duration_sec, float(end_match.group(1)))
            start = pending_start if pending_start is not None else 0.0
            if end > start:
                intervals.append((start, end))
            pending_start = None
    if pending_start is not None and pending_start < source_duration_sec:
        intervals.append((pending_start, source_duration_sec))
    return intervals


def _detect_silences(source_path: Path, source_duration_sec: float, cfg) -> list[tuple[float, float]]:
    """Return silence intervals, or an empty list when FFmpeg cannot detect them."""
    command = [
        str(cfg.ffmpeg_binary),
        "-hide_banner",
        "-nostats",
        "-i",
        str(source_path),
        "-af",
        (
            f"silencedetect=noise={float(cfg.silence_noise_threshold_db):g}dB:"
            f"d={float(cfg.silence_min_duration_sec):g}"
        ),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(60.0, source_duration_sec * 0.25),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_silence_intervals(completed.stderr or "", source_duration_sec)


def _nearest_silence_boundary(
    silences: list[tuple[float, float]],
    target_sec: float,
    minimum_sec: float,
    maximum_sec: float,
    search_window_sec: float,
) -> tuple[float, tuple[float, float]] | None:
    """Choose the closest valid point inside a nearby silence interval."""
    search_min = max(minimum_sec, target_sec - search_window_sec)
    search_max = min(maximum_sec, target_sec + search_window_sec)
    if search_max < search_min:
        return None

    candidates: list[tuple[float, float, float, float]] = []
    for silence_start, silence_end in silences:
        usable_start = max(silence_start, search_min)
        usable_end = min(silence_end, search_max)
        if usable_end < usable_start:
            continue
        cut = min(max(target_sec, usable_start), usable_end)
        candidates.append((abs(cut - target_sec), cut, silence_start, silence_end))
    if not candidates:
        return None
    _, cut, silence_start, silence_end = min(candidates)
    return cut, (silence_start, silence_end)


def _plan_chunk_ranges(
    total_frames: int,
    framerate: int,
    target_duration_sec: float,
    max_duration_sec: float,
    min_duration_sec: float,
    overlap_sec: float,
    search_window_sec: float,
    silences: list[tuple[float, float]],
) -> list[dict]:
    """Plan deterministic, overlapping chunk ranges in original source time."""
    total_sec = float(total_frames) / framerate
    overlap_frames = min(
        int(round(overlap_sec * framerate)),
        max(0, int(round(target_duration_sec * framerate)) - 1),
    )
    ranges: list[dict] = []
    start_frame = 0

    while start_frame < total_frames:
        start_sec = float(start_frame) / framerate
        remaining_sec = total_sec - start_sec
        target_end_sec = min(total_sec, start_sec + target_duration_sec)

        final_merge_limit = target_duration_sec + min_duration_sec - overlap_sec
        if remaining_sec <= target_duration_sec or (
            remaining_sec <= max_duration_sec and remaining_sec <= final_merge_limit
        ):
            end_frame = total_frames
            strategy = "source_end"
            silence_interval = None
        else:
            minimum_end_sec = start_sec + min_duration_sec
            maximum_end_sec = min(total_sec, start_sec + max_duration_sec)
            # Keep a final chunk of at least the configured minimum after overlap.
            maximum_end_sec = min(
                maximum_end_sec,
                total_sec + (float(overlap_frames) / framerate) - min_duration_sec,
            )
            if maximum_end_sec < minimum_end_sec:
                maximum_end_sec = min(total_sec, start_sec + max_duration_sec)

            selected = _nearest_silence_boundary(
                silences,
                target_end_sec,
                minimum_end_sec,
                maximum_end_sec,
                search_window_sec,
            )
            if selected is None:
                cut_sec = min(max(target_end_sec, minimum_end_sec), maximum_end_sec)
                strategy = "time_fallback"
                silence_interval = None
            else:
                cut_sec, silence_interval = selected
                strategy = "silence"
            end_frame = min(total_frames, max(start_frame + 1, int(round(cut_sec * framerate))))

        ranges.append(
            {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "target_end_sec": target_end_sec,
                "boundary_strategy": strategy,
                "silence_interval": silence_interval,
            }
        )
        if end_frame >= total_frames:
            break

        next_start = end_frame - overlap_frames
        if next_start <= start_frame:
            next_start = start_frame + max(1, (end_frame - start_frame) // 10)
        if next_start <= start_frame:
            break
        start_frame = next_start

    return ranges


def chunk_file(source_path: str, job_dir: Path, preprocess_meta: dict, cfg) -> List[dict]:
    src = Path(source_path)
    chunks_dir = Path(job_dir) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    framerate = int(preprocess_meta["framerate"])
    n_frames = int(preprocess_meta["n_frames"])
    sampwidth = int(preprocess_meta["sampwidth"])
    n_channels = int(preprocess_meta["n_channels"])
    bytes_per_second = int(preprocess_meta["bytes_per_second"])

    # The target byte limit remains authoritative even when a nearby silence is found.
    if bytes_per_second <= 0:
        max_duration_by_size = float(cfg.max_chunk_duration_sec)
    else:
        wav_header_allowance = 44
        max_duration_by_size = max(
            1.0,
            float(max(0, cfg.target_max_chunk_bytes - wav_header_allowance))
            / bytes_per_second,
        )

    effective_max_duration = min(float(cfg.max_chunk_duration_sec), max_duration_by_size)
    effective_target_duration = min(float(cfg.default_chunk_duration_sec), effective_max_duration)
    if effective_max_duration >= float(cfg.min_chunk_duration_sec):
        effective_target_duration = max(
            float(cfg.min_chunk_duration_sec), effective_target_duration
        )
        effective_min_duration = float(cfg.min_chunk_duration_sec)
    else:
        # A provider/file-size cap is allowed to override the requested minimum.
        effective_min_duration = effective_max_duration

    source_duration_sec = float(n_frames) / framerate
    silences: list[tuple[float, float]] = []
    if (
        bool(getattr(cfg, "smart_chunking_enabled", True))
        and source_duration_sec > effective_target_duration
    ):
        silences = _detect_silences(src, source_duration_sec, cfg)

    planned_ranges = _plan_chunk_ranges(
        total_frames=n_frames,
        framerate=framerate,
        target_duration_sec=effective_target_duration,
        max_duration_sec=effective_max_duration,
        min_duration_sec=effective_min_duration,
        overlap_sec=float(cfg.overlap_seconds),
        search_window_sec=(
            float(cfg.chunk_boundary_search_window_sec)
            if bool(getattr(cfg, "smart_chunking_enabled", True))
            else 0.0
        ),
        silences=silences,
    )

    source_identity = {
        "filename": src.name,
        "processing_audio_sha256": preprocess_meta.get("processing_audio_sha256"),
        "processing_audio_size_bytes": preprocess_meta.get("processing_audio_size_bytes"),
    }
    chunks = []

    with wave.open(str(src), "rb") as wf:
        total_frames = wf.getnframes()
        for chunk_idx, planned in enumerate(planned_ranges, start=1):
            start_frame = int(planned["start_frame"])
            end_frame = min(total_frames, int(planned["end_frame"]))
            wf.setpos(start_frame)
            frames_to_read = end_frame - start_frame
            data = wf.readframes(frames_to_read)

            # write chunk wav
            chunk_name = f"chunk-{chunk_idx:05d}.wav"
            chunk_path = chunks_dir / chunk_name
            with wave.open(str(chunk_path), "wb") as out:
                out.setnchannels(n_channels)
                out.setsampwidth(sampwidth)
                out.setframerate(framerate)
                out.writeframes(data)

            actual_bytes = chunk_path.stat().st_size
            estimated_bytes = frames_to_read * n_channels * sampwidth
            oversize = actual_bytes > cfg.hard_max_chunk_bytes
            previous_end = int(planned_ranges[chunk_idx - 2]["end_frame"]) if chunk_idx > 1 else start_frame
            next_start = (
                int(planned_ranges[chunk_idx]["start_frame"])
                if chunk_idx < len(planned_ranges)
                else end_frame
            )
            silence_interval = planned.get("silence_interval")

            chunk_meta = {
                "chunk_id": chunk_idx,
                "chunk_index": chunk_idx,
                "filename": str(chunk_path.name),
                "start_frame": int(start_frame),
                "end_frame": int(end_frame),
                "start_sec": float(start_frame) / framerate,
                "end_sec": float(end_frame) / framerate,
                "global_start_sec": float(start_frame) / framerate,
                "global_end_sec": float(end_frame) / framerate,
                "overlap_before_sec": max(0.0, float(previous_end - start_frame) / framerate),
                "overlap_after_sec": max(0.0, float(end_frame - next_start) / framerate),
                "frames": int(frames_to_read),
                "estimated_bytes": int(estimated_bytes),
                "actual_bytes": int(actual_bytes),
                "oversize": bool(oversize),
                "source_identity": source_identity,
                "boundary": {
                    "strategy": planned["boundary_strategy"],
                    "target_end_sec": float(planned["target_end_sec"]),
                    "silence_start_sec": (
                        float(silence_interval[0]) if silence_interval is not None else None
                    ),
                    "silence_end_sec": (
                        float(silence_interval[1]) if silence_interval is not None else None
                    ),
                },
            }
            chunks.append(chunk_meta)

    return chunks
