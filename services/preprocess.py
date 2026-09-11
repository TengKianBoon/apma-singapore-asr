from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from typing import Any

from services.audio_quality import (
    analyze_ffmpeg_astats,
    analyze_pcm16_wav,
    structural_audio_profile,
    technical_audio_profile,
)
from services.audio_formats import DIRECT_WAV_EXTENSIONS, SUPPORTED_INPUT_EXTENSIONS
from services.config import Config
from services.integrity import sha256_file
from services.storage_retention import shared_canonical_path


class MediaProcessingError(RuntimeError):
    """A clear media state suitable for manifests and operator feedback."""

    def __init__(self, state: str, detail: str):
        self.state = state
        super().__init__(f"{state}: {detail}")


def _read_wav_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        comptype = wf.getcomptype()

    duration_seconds = n_frames / float(framerate) if framerate else 0.0
    bytes_per_second = int(framerate * n_channels * sampwidth)

    return {
        "filename": path.name,
        "path": str(path),
        "n_channels": n_channels,
        "sampwidth": sampwidth,
        "framerate": framerate,
        "n_frames": n_frames,
        "duration_seconds": duration_seconds,
        "comptype": comptype,
        "bytes_per_second": bytes_per_second,
        "source_format": path.suffix.lower(),
        "normalized": False,
    }


def _run_ffprobe_json(path: Path, cfg: Config) -> dict[str, Any]:
    cmd = [
        cfg.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,size,format_name,bit_rate:"
            "stream=index,codec_type,codec_name,codec_long_name,sample_rate,channels,"
            "channel_layout,bit_rate,duration,sample_fmt,bits_per_sample,bits_per_raw_sample"
        ),
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffprobe failed").strip()
        lowered = detail.lower()
        state = (
            "UNSUPPORTED_BY_CURRENT_RUNTIME"
            if "decoder" in lowered and ("not found" in lowered or "unsupported" in lowered)
            else "DECODE_FAILED"
        )
        raise MediaProcessingError(state, f"ffprobe could not inspect media: {detail}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaProcessingError("DECODE_FAILED", "ffprobe returned invalid JSON") from exc


def _optional_int(value: Any) -> int | None:
    if value in {None, "", "N/A"}:
        return None
    return int(float(value))


def _select_audio_stream(data: dict[str, Any]) -> dict[str, Any]:
    streams = [item for item in data.get("streams", []) if item.get("codec_type") == "audio"]
    if not streams:
        raise ValueError("Media has no audio stream (audio_stream_missing)")
    stream = streams[0]
    return {
        "index": _optional_int(stream.get("index")),
        "codec_name": stream.get("codec_name"),
        "codec_long_name": stream.get("codec_long_name"),
        "sample_rate_hz": _optional_int(stream.get("sample_rate")),
        "channels": _optional_int(stream.get("channels")),
        "channel_layout": stream.get("channel_layout"),
        "bit_rate_bps": _optional_int(stream.get("bit_rate")),
        "duration_seconds": float(stream["duration"]) if stream.get("duration") not in {None, "", "N/A"} else None,
        "sample_format": stream.get("sample_fmt"),
        "bits_per_sample": _optional_int(stream.get("bits_per_sample")),
        "bits_per_raw_sample": _optional_int(stream.get("bits_per_raw_sample")),
    }


def _validate_duration(duration_seconds: float, cfg: Config) -> None:
    if duration_seconds <= 0:
        raise ValueError("Media duration must be greater than zero (invalid_duration)")
    if duration_seconds > float(cfg.max_meeting_duration_sec):
        raise ValueError("Media duration exceeds the configured meeting limit (duration_limit_exceeded)")


def probe_audio_metadata(path: str, cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or Config()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_INPUT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise ValueError(f"Unsupported audio format: {suffix}. Supported formats: {supported}")
    data = _run_ffprobe_json(p, cfg)
    fmt = data.get("format", {})
    stream = _select_audio_stream(data)
    probed_duration = stream.get("duration_seconds")
    duration = float(probed_duration if probed_duration is not None else (fmt.get("duration") or 0.0))
    _validate_duration(duration, cfg)
    size = int(float(fmt.get("size") or p.stat().st_size))
    bit_rate = stream.get("bit_rate_bps") or _optional_int(fmt.get("bit_rate"))
    stream["bit_rate_bps"] = bit_rate
    stream["duration_seconds"] = duration

    if suffix in DIRECT_WAV_EXTENSIONS:
        meta = _read_wav_metadata(p)
        _validate_duration(float(meta["duration_seconds"]), cfg)
        meta.update(
            {
                "size_bytes": size,
                "container_format": fmt.get("format_name"),
                "bit_rate_bps": bit_rate,
                "audio_stream": stream,
                "probe_method": "ffprobe+wave",
            }
        )
        return meta

    normalized_bytes_per_second = int(
        cfg.normalized_audio_framerate * cfg.normalized_audio_channels * cfg.normalized_audio_sample_width
    )
    return {
        "filename": p.name,
        "path": str(p),
        "duration_seconds": duration,
        "size_bytes": size,
        "source_format": suffix,
        "container_format": fmt.get("format_name"),
        "bit_rate_bps": bit_rate,
        "audio_stream": stream,
        "probe_method": "ffprobe",
        "normalized": False,
        "normalized_framerate": cfg.normalized_audio_framerate,
        "normalized_channels": cfg.normalized_audio_channels,
        "normalized_sampwidth": cfg.normalized_audio_sample_width,
        "normalized_bytes_per_second": normalized_bytes_per_second,
    }


def _run_ffmpeg_normalize(src: Path, dest: Path, cfg: Config) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        cfg.ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        str(cfg.normalized_audio_channels),
        "-ar",
        str(cfg.normalized_audio_framerate),
        "-acodec",
        "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        raise MediaProcessingError("DECODE_FAILED", f"ffmpeg could not create working WAV: {detail}")


def _run_ffmpeg_canonical(src: Path, dest: Path, cfg: Config) -> None:
    if dest.exists():
        raise FileExistsError(f"Canonical audio already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    cmd = [
        cfg.ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "flac",
        "-f",
        "flac",
        str(partial),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        raise MediaProcessingError("DECODE_FAILED", f"ffmpeg could not create canonical FLAC: {detail}")
    partial.replace(dest)


def _verify_ffmpeg_decode(path: Path, cfg: Config) -> float:
    cmd = [
        cfg.ffmpeg_binary,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-af",
        "asetpts=N/SR/TB",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg decode failed").strip()
        raise MediaProcessingError("DECODE_FAILED", f"media is not fully decodable: {detail}")
    decoded_microseconds = [
        int(line.split("=", 1)[1])
        for line in proc.stdout.splitlines()
        if line.startswith("out_time_us=") and line.split("=", 1)[1].lstrip("-").isdigit()
    ]
    if not decoded_microseconds:
        raise MediaProcessingError(
            "DECODE_FAILED", "ffmpeg did not report a decoded audio duration"
        )
    return max(decoded_microseconds) / 1_000_000.0


def _create_canonical_audio(
    source: Path,
    job_dir: Path,
    source_metadata: dict[str, Any],
    cfg: Config,
) -> dict[str, Any]:
    source_sha256 = sha256_file(source)
    canonical_path = shared_canonical_path(Path(job_dir).parent, source_sha256)
    cache_reused = canonical_path.is_file()
    try:
        source_decoded_duration = _verify_ffmpeg_decode(source, cfg)
        if not cache_reused:
            _run_ffmpeg_canonical(source, canonical_path, cfg)
        canonical_probe = probe_audio_metadata(str(canonical_path), cfg)
        canonical_decoded_duration = _verify_ffmpeg_decode(canonical_path, cfg)
        source_stream = source_metadata.get("audio_stream") or {}
        canonical_stream = canonical_probe.get("audio_stream") or {}
        source_duration = float(source_metadata["duration_seconds"])
        canonical_duration = float(canonical_probe["duration_seconds"])
        tolerance = 0.05
        duration_delta = abs(canonical_decoded_duration - source_decoded_duration)
        if duration_delta > tolerance:
            raise MediaProcessingError(
                "DECODE_FAILED",
                f"canonical decoded duration differs from source decode by {duration_delta:.6f}s",
            )
        source_channels = source_stream.get("channels") or source_metadata.get("n_channels")
        source_rate = source_stream.get("sample_rate_hz") or source_metadata.get("framerate")
        canonical_channels = canonical_stream.get("channels")
        canonical_rate = canonical_stream.get("sample_rate_hz")
        if canonical_stream.get("codec_name") != "flac":
            raise MediaProcessingError("DECODE_FAILED", "canonical working audio is not FLAC")
        if source_channels and canonical_channels != source_channels:
            raise MediaProcessingError("DECODE_FAILED", "canonical FLAC did not preserve channel count")
        if source_rate and canonical_rate != source_rate:
            raise MediaProcessingError("DECODE_FAILED", "canonical FLAC did not preserve sample rate")
    except Exception:
        canonical_path.unlink(missing_ok=True)
        raise
    return {
        "path": str(canonical_path),
        "format": "flac",
        "codec": canonical_stream.get("codec_name"),
        "lossless": True,
        "decodable": True,
        "sha256": sha256_file(canonical_path),
        "size_bytes": canonical_path.stat().st_size,
        "duration_seconds": canonical_duration,
        "source_reported_duration_seconds": source_duration,
        "source_decoded_duration_seconds": source_decoded_duration,
        "canonical_decoded_duration_seconds": canonical_decoded_duration,
        "source_reported_duration_delta_seconds": abs(
            source_duration - source_decoded_duration
        ),
        "duration_reference": "full_decode_progress",
        "duration_delta_seconds": duration_delta,
        "duration_tolerance_seconds": tolerance,
        "sample_rate_hz": canonical_rate,
        "channels": canonical_channels,
        "channel_layout": canonical_stream.get("channel_layout"),
        "bit_rate_bps": canonical_stream.get("bit_rate_bps"),
        "sample_format": canonical_stream.get("sample_format"),
        "bits_per_sample": canonical_stream.get("bits_per_sample"),
        "bits_per_raw_sample": canonical_stream.get("bits_per_raw_sample"),
        "source_sample_rate_hz": source_rate,
        "source_channels": source_channels,
        "source_channel_facts_preserved": canonical_channels == source_channels,
        "source_sample_rate_preserved": canonical_rate == source_rate,
        "upsampling_fidelity_claimed": False,
        "encoding_chain": "source_decode_to_lossless_flac",
        "storage_policy": "content_addressed_shared_canonical",
        "source_sha256": source_sha256,
        "shared_canonical_reused": bool(cache_reused),
    }


def _create_technical_profile(
    source: Path,
    source_metadata: dict[str, Any],
    canonical_audio: dict[str, Any],
    cfg: Config,
) -> dict[str, Any]:
    source_before = sha256_file(source)
    canonical_path = Path(canonical_audio["path"])
    canonical_before = sha256_file(canonical_path)
    signal_statistics = analyze_ffmpeg_astats(canonical_path, cfg.ffmpeg_binary)
    source_after = sha256_file(source)
    canonical_after = sha256_file(canonical_path)
    return technical_audio_profile(
        source_metadata,
        canonical_audio,
        signal_statistics,
        {
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
            "source_unchanged": source_before == source_after,
            "canonical_sha256_before": canonical_before,
            "canonical_sha256_after": canonical_after,
            "canonical_unchanged": canonical_before == canonical_after,
        },
    )


def preprocess_audio(path: str, job_dir: Path | None = None, cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or Config()
    p = Path(path)
    probed = probe_audio_metadata(str(p), cfg)
    suffix = p.suffix.lower()
    canonical_audio = (
        _create_canonical_audio(p, Path(job_dir), probed, cfg) if job_dir is not None else None
    )
    technical_profile = (
        _create_technical_profile(p, probed, canonical_audio, cfg)
        if canonical_audio is not None
        else None
    )
    if suffix in DIRECT_WAV_EXTENSIONS:
        probed.update(
            {
                "processing_audio_sha256": sha256_file(p),
                "processing_audio_size_bytes": p.stat().st_size,
                "source_probe": structural_audio_profile(probed),
                "audio_qc": {
                    "source": structural_audio_profile(probed),
                    "processing_audio": analyze_pcm16_wav(p),
                    "technical_profile": technical_profile,
                },
                "canonical_audio": canonical_audio,
            }
        )
        return probed

    if job_dir is None:
        raise ValueError("job_dir is required when normalizing non-WAV audio")

    normalized_path = Path(job_dir) / "preprocess" / "normalized.wav"
    _run_ffmpeg_normalize(Path(canonical_audio["path"]), normalized_path, cfg)
    normalized_meta = _read_wav_metadata(normalized_path)
    normalized_profile = analyze_pcm16_wav(normalized_path)
    normalized_meta.update(
        {
            "source_filename": p.name,
            "source_path": str(p),
            "source_format": suffix,
            "source_duration_seconds": probed.get("duration_seconds"),
            "source_size_bytes": probed.get("size_bytes"),
            "container_format": probed.get("container_format"),
            "bit_rate_bps": probed.get("bit_rate_bps"),
            "audio_stream": probed.get("audio_stream"),
            "probe_method": probed.get("probe_method"),
            "normalized": True,
            "processing_audio_sha256": sha256_file(normalized_path),
            "processing_audio_size_bytes": normalized_path.stat().st_size,
            "source_probe": structural_audio_profile(probed),
            "audio_qc": {
                "source": structural_audio_profile(probed),
                "processing_audio": normalized_profile,
                "technical_profile": technical_profile,
            },
            "normalization": {
                "tool": "ffmpeg",
                "output_path": str(normalized_path),
                "framerate": cfg.normalized_audio_framerate,
                "channels": cfg.normalized_audio_channels,
                "sample_width": cfg.normalized_audio_sample_width,
                "purpose": "provider_and_chunker_working_copy",
                "lossy_encoding": False,
                "channel_transform": (
                    "downmixed_for_working_copy"
                    if (probed.get("audio_stream") or {}).get("channels")
                    != cfg.normalized_audio_channels
                    else "unchanged"
                ),
            },
            "canonical_audio": canonical_audio,
        }
    )
    return normalized_meta


def preprocess_wav(path: str) -> dict[str, Any]:
    return preprocess_audio(path)
