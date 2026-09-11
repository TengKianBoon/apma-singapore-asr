"""Small FFmpeg runtime capability inventory for supported APMA audio formats."""

from __future__ import annotations

import hashlib
import subprocess
from typing import Any

from services.config import Config


FORMAT_ALIASES = {
    "AMR": {"amr"},
    "3GP_3GA_M4A": {"mov", "mp4", "m4a", "3gp", "3g2"},
    "WMA_ASF": {"asf"},
    "QCP": {"qcp"},
    "MP3": {"mp3"},
    "WAV": {"wav"},
    "OGG": {"ogg"},
    "FLAC": {"flac"},
    "WEBM": {"webm", "matroska"},
}
CODEC_NAMES = (
    "amr_nb",
    "amr_wb",
    "aac",
    "mp3",
    "wmav1",
    "wmav2",
    "qcelp",
    "flac",
    "opus",
    "pcm_s16le",
)


def _run(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise RuntimeError(f"FFmpeg capability command failed: {' '.join(command)}")
    return output


def _parse_formats(output: str) -> dict[str, dict[str, bool]]:
    formats: dict[str, dict[str, bool]] = {}
    for line in output.splitlines():
        if len(line) < 5 or line[0] != " " or line[1] not in {"D", " "} or line[2] not in {"E", " "}:
            continue
        names = line[4:].split(maxsplit=1)[0].split(",") if line[4:].strip() else []
        for name in names:
            if name:
                support = formats.setdefault(name, {"demux": False, "mux": False})
                support["demux"] = support["demux"] or line[1] == "D"
                support["mux"] = support["mux"] or line[2] == "E"
    return formats


def _parse_codecs(output: str) -> dict[str, dict[str, bool]]:
    codecs: dict[str, dict[str, bool]] = {}
    for line in output.splitlines():
        if len(line) < 9 or line[0] != " ":
            continue
        flags = line[1:7]
        if len(flags) != 6 or flags[0] not in {"D", "."} or flags[1] not in {"E", "."}:
            continue
        remainder = line[8:].split(maxsplit=1)
        if not remainder:
            continue
        support = codecs.setdefault(remainder[0], {"decode": False, "encode": False})
        support["decode"] = support["decode"] or flags[0] == "D"
        support["encode"] = support["encode"] or flags[1] == "E"
    return codecs


def ffmpeg_runtime_capabilities(cfg: Config | None = None) -> dict[str, Any]:
    """Run the exact FFmpeg capability commands and return relevant support only."""

    cfg = cfg or Config()
    version_output = _run([cfg.ffmpeg_binary, "-version"])
    ffprobe_version_output = _run([cfg.ffprobe_binary, "-version"])
    buildconf_output = _run([cfg.ffmpeg_binary, "-buildconf"])
    formats_output = _run([cfg.ffmpeg_binary, "-hide_banner", "-formats"])
    codecs_output = _run([cfg.ffmpeg_binary, "-hide_banner", "-codecs"])
    formats = _parse_formats(formats_output)
    codecs = _parse_codecs(codecs_output)

    format_support = {}
    for label, aliases in FORMAT_ALIASES.items():
        matches = {name: formats[name] for name in sorted(aliases) if name in formats}
        format_support[label] = {
            "demux": any(item["demux"] for item in matches.values()),
            "mux": any(item["mux"] for item in matches.values()),
            "matched_formats": matches,
        }
    codec_support = {
        name: codecs.get(name, {"decode": False, "encode": False}) for name in CODEC_NAMES
    }
    relevant_names = set().union(*FORMAT_ALIASES.values())
    relevant_format_lines = [
        line
        for line in formats_output.splitlines()
        if len(line) >= 5 and line[4:].strip()
        if any(name in line[4:].split(maxsplit=1)[0].split(",") for name in relevant_names)
    ]
    relevant_codec_lines = [
        line
        for line in codecs_output.splitlines()
        if len(line) >= 9 and line[8:].split(maxsplit=1)[0] in CODEC_NAMES
    ]
    return {
        "schema_version": "apma.ffmpeg-capabilities.v2",
        "ffmpeg_build": version_output.splitlines()[0] if version_output.splitlines() else "unknown",
        "ffprobe_build": (
            ffprobe_version_output.splitlines()[0]
            if ffprobe_version_output.splitlines()
            else "unknown"
        ),
        "build_configuration": buildconf_output,
        "commands": [
            "ffmpeg -version",
            "ffprobe -version",
            "ffmpeg -buildconf",
            "ffmpeg -formats",
            "ffmpeg -codecs",
        ],
        "format_support": format_support,
        "codec_support": codec_support,
        "relevant_format_lines": relevant_format_lines,
        "relevant_codec_lines": relevant_codec_lines,
        "raw_output_sha256": {
            "ffmpeg_version": hashlib.sha256(version_output.encode("utf-8")).hexdigest(),
            "ffprobe_version": hashlib.sha256(
                ffprobe_version_output.encode("utf-8")
            ).hexdigest(),
            "buildconf": hashlib.sha256(buildconf_output.encode("utf-8")).hexdigest(),
            "formats": hashlib.sha256(formats_output.encode("utf-8")).hexdigest(),
            "codecs": hashlib.sha256(codecs_output.encode("utf-8")).hexdigest(),
        },
    }


__all__ = ["ffmpeg_runtime_capabilities"]
