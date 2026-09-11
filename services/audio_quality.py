from __future__ import annotations

import math
import subprocess
import sys
import wave
from array import array
from pathlib import Path
from typing import Any


NEAR_SILENCE_DBFS = -50.0
CLIPPING_FRACTION = 0.99
POSSIBLE_CLIPPING_THRESHOLD_DBFS = -0.1


def _sampling_band(sample_rate_hz: int | None) -> str:
    if not sample_rate_hz:
        return "unknown"
    if sample_rate_hz < 12000:
        return "narrowband"
    if sample_rate_hz < 32000:
        return "wideband"
    return "fullband"


def nominal_bandwidth_class(sample_rate_hz: int | None) -> str:
    """Classify nominal bandwidth from source sample rate, not acoustic content."""

    if not sample_rate_hz or sample_rate_hz <= 0:
        return "UNKNOWN"
    if sample_rate_hz < 16000:
        return "NARROWBAND"
    if sample_rate_hz < 32000:
        return "WIDEBAND"
    return "FULLBAND"


def legacy_quality_classification(codec: str | None, sample_rate_hz: int | None) -> str:
    normalized_codec = str(codec or "").lower()
    if normalized_codec == "amr_nb" or (sample_rate_hz and sample_rate_hz <= 8000):
        return "LEGACY_NARROWBAND"
    if sample_rate_hz and sample_rate_hz >= 12000:
        return "WIDEBAND_MODERN"
    return "UNKNOWN"


def structural_audio_profile(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return selected, non-sensitive media facts from a probe result."""

    stream = metadata.get("audio_stream") or {}
    sample_rate = stream.get("sample_rate_hz") or metadata.get("framerate")
    channels = stream.get("channels") or metadata.get("n_channels")
    sampling_rate = int(sample_rate) if sample_rate else None
    return {
        "container": metadata.get("container_format"),
        "codec": stream.get("codec_name"),
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "channel_layout": stream.get("channel_layout"),
        "bit_rate_bps": stream.get("bit_rate_bps") or metadata.get("bit_rate_bps"),
        "duration_seconds": metadata.get("duration_seconds"),
        "sample_format": stream.get("sample_format"),
        "bits_per_sample": stream.get("bits_per_sample"),
        "bits_per_raw_sample": stream.get("bits_per_raw_sample"),
        "sampling_band": _sampling_band(sampling_rate),
        "nominal_bandwidth_class": nominal_bandwidth_class(sampling_rate),
        "nominal_bandwidth_basis": "source_sample_rate_hz",
        "measured_acoustic_spectral_bandwidth": False,
        "legacy_quality_classification": legacy_quality_classification(
            stream.get("codec_name"), sampling_rate
        ),
    }


def _finite_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6) if math.isfinite(parsed) else None


def analyze_ffmpeg_astats(
    path: str | Path,
    ffmpeg_binary: str = "ffmpeg",
) -> dict[str, Any]:
    """Measure decoded signal levels without modifying the analyzed audio file."""

    command = [
        ffmpeg_binary,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-af",
        "astats=metadata=0:reset=0",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg astats failed").strip()
        return {
            "status": "analysis_failed",
            "analysis": "ffmpeg_astats",
            "is_speech_vad": False,
            "error": detail,
        }

    sections: dict[str, dict[str, float | None]] = {}
    current: str | None = None
    wanted = {
        "Peak level dB": "peak_level_dbfs",
        "RMS level dB": "rms_level_dbfs",
        "Noise floor dB": "noise_floor_dbfs",
    }
    for line in completed.stderr.splitlines():
        if "] " not in line:
            continue
        payload = line.split("] ", 1)[1].strip()
        if payload.startswith("Channel: "):
            channel = payload.split(":", 1)[1].strip()
            current = f"channel_{channel}"
            sections.setdefault(current, {})
            continue
        if payload == "Overall":
            current = "overall"
            sections.setdefault(current, {})
            continue
        if current is None or ": " not in payload:
            continue
        label, value = payload.split(": ", 1)
        if label in wanted:
            sections[current][wanted[label]] = _finite_float(value)

    overall = sections.get("overall") or {}
    channels = [
        {"channel": int(name.split("_", 1)[1]), **values}
        for name, values in sections.items()
        if name.startswith("channel_")
    ]
    channels.sort(key=lambda item: item["channel"])
    if "peak_level_dbfs" not in overall or "rms_level_dbfs" not in overall:
        return {
            "status": "analysis_failed",
            "analysis": "ffmpeg_astats",
            "is_speech_vad": False,
            "error": "ffmpeg astats returned no overall peak/RMS statistics",
        }
    peak = overall.get("peak_level_dbfs")
    possible_clipping = bool(
        peak is not None and peak >= POSSIBLE_CLIPPING_THRESHOLD_DBFS
    )
    return {
        "status": "success",
        "analysis": "ffmpeg_astats",
        "is_speech_vad": False,
        "analyzed_representation": "canonical_flac",
        "overall_peak_level_dbfs": peak,
        "overall_rms_level_dbfs": overall.get("rms_level_dbfs"),
        "noise_floor_dbfs": overall.get("noise_floor_dbfs"),
        "per_channel": channels,
        "possible_clipping": possible_clipping,
        "possible_clipping_is_derived": True,
        "possible_clipping_threshold_dbfs": POSSIBLE_CLIPPING_THRESHOLD_DBFS,
    }


def technical_audio_profile(
    source_metadata: dict[str, Any],
    canonical_audio: dict[str, Any],
    signal_statistics: dict[str, Any],
    integrity: dict[str, Any],
) -> dict[str, Any]:
    """Combine retained source/canonical facts with deterministic signal statistics."""

    source_stream = source_metadata.get("audio_stream") or {}
    source_rate = source_stream.get("sample_rate_hz") or source_metadata.get("framerate")
    return {
        "schema_version": "apma.audio-technical-profile.v1",
        "characterisation_only": True,
        "audio_modified_by_profiling": False,
        "source": {
            "codec": source_stream.get("codec_name"),
            "container": source_metadata.get("container_format"),
            "sample_rate_hz": source_rate,
            "channels": source_stream.get("channels") or source_metadata.get("n_channels"),
            "channel_layout": source_stream.get("channel_layout"),
            "duration_seconds": source_metadata.get("duration_seconds"),
            "bit_rate_bps": source_stream.get("bit_rate_bps")
            or source_metadata.get("bit_rate_bps"),
            "sample_format": source_stream.get("sample_format"),
            "bits_per_sample": source_stream.get("bits_per_sample"),
            "bits_per_raw_sample": source_stream.get("bits_per_raw_sample"),
        },
        "canonical": {
            "codec": canonical_audio.get("codec"),
            "container": canonical_audio.get("format"),
            "sample_rate_hz": canonical_audio.get("sample_rate_hz"),
            "channels": canonical_audio.get("channels"),
            "channel_layout": canonical_audio.get("channel_layout"),
            "duration_seconds": canonical_audio.get("duration_seconds"),
            "bit_rate_bps": canonical_audio.get("bit_rate_bps"),
            "sample_format": canonical_audio.get("sample_format"),
            "bits_per_sample": canonical_audio.get("bits_per_sample"),
            "bits_per_raw_sample": canonical_audio.get("bits_per_raw_sample"),
        },
        "nominal_bandwidth_class": nominal_bandwidth_class(source_rate),
        "nominal_bandwidth_basis": "source_sample_rate_hz",
        "measured_acoustic_spectral_bandwidth": False,
        "recording_age_inferred": False,
        "signal_statistics": signal_statistics,
        "profiling_integrity": integrity,
    }


def analyze_pcm16_wav(path: str | Path) -> dict[str, Any]:
    """Measure deterministic PCM signal properties; this is not speech VAD."""

    audio_path = Path(path)
    with wave.open(str(audio_path), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        frame_count = wav_file.getnframes()

        base = {
            "analysis": "pcm_signal_structure",
            "is_speech_vad": False,
            "sample_width_bytes": sample_width,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "frame_count": frame_count,
            "sampling_band": _sampling_band(sample_rate),
        }
        if sample_width != 2:
            return {
                **base,
                "status": "not_analyzed",
                "warnings": ["pcm_signal_analysis_requires_16_bit_wav"],
            }

        total_samples = 0
        near_silent_samples = 0
        clipped_samples = 0
        peak = 0
        near_silence_threshold = max(1, round(32767 * (10 ** (NEAR_SILENCE_DBFS / 20.0))))
        clipping_threshold = round(32767 * CLIPPING_FRACTION)

        while frames := wav_file.readframes(65536):
            samples = array("h")
            samples.frombytes(frames)
            if sys.byteorder == "big":
                samples.byteswap()
            total_samples += len(samples)
            for sample in samples:
                magnitude = min(abs(sample), 32767)
                peak = max(peak, magnitude)
                if magnitude <= near_silence_threshold:
                    near_silent_samples += 1
                if magnitude >= clipping_threshold:
                    clipped_samples += 1

    denominator = max(1, total_samples)
    near_silence_ratio = near_silent_samples / denominator
    clipping_ratio = clipped_samples / denominator
    peak_dbfs = None if peak == 0 else 20.0 * math.log10(peak / 32767.0)
    warnings: list[str] = []
    if clipping_ratio >= 0.01:
        warnings.append("possible_clipping")
    if near_silence_ratio >= 0.95:
        warnings.append("mostly_near_silence")

    return {
        **base,
        "status": "pass_with_warnings" if warnings else "pass",
        "sample_count": total_samples,
        "peak_dbfs": round(peak_dbfs, 4) if peak_dbfs is not None else None,
        "near_silence_threshold_dbfs": NEAR_SILENCE_DBFS,
        "near_silence_ratio": round(near_silence_ratio, 8),
        "clipping_ratio": round(clipping_ratio, 8),
        "warnings": warnings,
    }
