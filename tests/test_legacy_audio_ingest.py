from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from services import chunker, ingest, job as job_mod, preprocess
from services.config import Config
from services.media_capabilities import ffmpeg_runtime_capabilities
from services.quality_workflow import run_quality_workflow
from tests.helpers import generate_sine_wav
from tests.test_quality_workflow import FakeQualityFactory, _config as quality_config


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transcode(source: Path, target: Path, arguments: list[str], cfg: Config) -> None:
    command = [
        cfg.ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        *arguments,
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert target.is_file() and target.stat().st_size > 0


def _generated_fixtures(tmp_path: Path, cfg: Config) -> dict[str, tuple[Path, str]]:
    base = tmp_path / "synthetic-stereo.wav"
    generate_sine_wav(str(base), duration_sec=1.2, framerate=44100, channels=2)
    mono = tmp_path / "synthetic-mono.wav"
    generate_sine_wav(str(mono), duration_sec=1.2, framerate=44100, channels=1)
    specs = {
        "3GP_AAC": ("legacy.3gp", ["-c:a", "aac", "-b:a", "64k", "-f", "3gp"], "aac"),
        "3GA_AAC": ("legacy.3ga", ["-c:a", "aac", "-b:a", "64k", "-f", "3gp"], "aac"),
        "WMA": ("legacy.wma", ["-c:a", "wmav2", "-b:a", "64k", "-f", "asf"], "wmav2"),
        "ASF": ("legacy.asf", ["-c:a", "wmav2", "-b:a", "64k", "-f", "asf"], "wmav2"),
        "M4A_AAC": ("modern.m4a", ["-c:a", "aac", "-b:a", "96k"], "aac"),
        "MP3": ("modern.mp3", ["-c:a", "libmp3lame", "-b:a", "96k"], "mp3"),
        "OGG_OPUS": ("modern.ogg", ["-c:a", "libopus", "-b:a", "64k"], "opus"),
        "FLAC": ("modern.flac", ["-c:a", "flac"], "flac"),
        "WEBM_OPUS": ("modern.webm", ["-c:a", "libopus", "-b:a", "64k"], "opus"),
    }
    fixtures = {
        "WAV_MONO": (mono, "pcm_s16le"),
        "WAV_PCM": (base, "pcm_s16le"),
    }
    for label, (filename, arguments, codec) in specs.items():
        target = tmp_path / filename
        _transcode(base, target, arguments, cfg)
        fixtures[label] = (target, codec)
    fixtures["AMR_NB"] = (
        Path("sample_audio/synthetic_legacy_amr_nb.amr").resolve(),
        "amr_nb",
    )
    fixtures["AMR_WB"] = (
        Path("sample_audio/synthetic_legacy_amr_wb.amr").resolve(),
        "amr_wb",
    )
    return fixtures


def test_actual_ffmpeg_capability_inventory_records_requested_commands():
    capabilities = ffmpeg_runtime_capabilities(Config())

    assert capabilities["ffmpeg_build"].startswith("ffmpeg version 9.0.1")
    assert capabilities["ffprobe_build"].startswith("ffprobe version 9.0.1")
    assert capabilities["commands"] == [
        "ffmpeg -version",
        "ffprobe -version",
        "ffmpeg -buildconf",
        "ffmpeg -formats",
        "ffmpeg -codecs",
    ]
    for flag in (
        "--enable-libcodec2",
        "--enable-libgsm",
        "--enable-libmp3lame",
        "--enable-libopus",
        "--enable-libspeex",
    ):
        assert flag in capabilities["build_configuration"]
    assert capabilities["format_support"]["AMR"]["demux"] is True
    assert capabilities["format_support"]["3GP_3GA_M4A"]["demux"] is True
    assert capabilities["format_support"]["WMA_ASF"]["demux"] is True
    assert capabilities["format_support"]["QCP"]["demux"] is True
    assert capabilities["format_support"]["WAV"]["demux"] is True
    assert capabilities["format_support"]["WEBM"]["demux"] is True
    assert capabilities["format_support"]["WEBM"]["mux"] is True
    assert capabilities["codec_support"]["amr_nb"]["decode"] is True
    assert capabilities["codec_support"]["amr_wb"]["decode"] is True
    assert capabilities["codec_support"]["qcelp"]["decode"] is True
    assert capabilities["codec_support"]["flac"]["encode"] is True
    assert capabilities["codec_support"]["opus"]["decode"] is True
    assert capabilities["raw_output_sha256"]["ffmpeg_version"]
    assert capabilities["raw_output_sha256"]["ffprobe_version"]
    assert capabilities["raw_output_sha256"]["buildconf"]
    assert capabilities["raw_output_sha256"]["formats"]
    assert capabilities["raw_output_sha256"]["codecs"]


def test_representative_legacy_and_modern_formats_preserve_original_and_canonical(tmp_path):
    cfg = Config()
    fixtures = _generated_fixtures(tmp_path, cfg)
    results = {}
    for index, (label, (source, expected_codec)) in enumerate(fixtures.items(), start=1):
        source_hash = _sha256(source)
        source_size = source.stat().st_size
        job_dir = job_mod.create_job(f"legacy-format-{index}", str(tmp_path / "jobs"))
        ingested = ingest.ingest_file(str(source), job_dir)
        retained = Path(ingested["path"])
        processed = preprocess.preprocess_audio(str(retained), job_dir, cfg)
        canonical = processed["canonical_audio"]
        profile = processed["audio_qc"]["technical_profile"]

        assert ingested["filename"] == source.name
        assert ingested["size_bytes"] == source_size
        assert ingested["integrity"]["source_sha256"] == source_hash
        assert ingested["integrity"]["stored_sha256"] == source_hash
        assert _sha256(retained) == source_hash
        assert processed["audio_stream"]["codec_name"] == expected_codec
        assert processed["container_format"]
        assert canonical["codec"] == "flac"
        assert canonical["lossless"] is True
        assert canonical["decodable"] is True
        assert canonical["duration_reference"] == "full_decode_progress"
        assert canonical["source_decoded_duration_seconds"] > 0
        assert canonical["canonical_decoded_duration_seconds"] > 0
        assert canonical["duration_delta_seconds"] <= canonical["duration_tolerance_seconds"]
        assert canonical["source_channel_facts_preserved"] is True
        assert canonical["source_sample_rate_preserved"] is True
        assert canonical["upsampling_fidelity_claimed"] is False
        assert profile["characterisation_only"] is True
        assert profile["audio_modified_by_profiling"] is False
        assert profile["source"]["codec"] == expected_codec
        assert profile["source"]["sample_rate_hz"] == processed["audio_stream"]["sample_rate_hz"]
        assert profile["source"]["channels"] == processed["audio_stream"]["channels"]
        assert profile["source"]["sample_format"]
        assert profile["canonical"]["codec"] == "flac"
        assert profile["canonical"]["sample_rate_hz"] == canonical["sample_rate_hz"]
        assert profile["canonical"]["channels"] == canonical["channels"]
        assert profile["canonical"]["sample_format"]
        assert profile["nominal_bandwidth_basis"] == "source_sample_rate_hz"
        assert profile["measured_acoustic_spectral_bandwidth"] is False
        assert profile["recording_age_inferred"] is False
        assert profile["signal_statistics"]["status"] == "success"
        assert profile["signal_statistics"]["analysis"] == "ffmpeg_astats"
        assert profile["signal_statistics"]["overall_peak_level_dbfs"] is not None
        assert profile["signal_statistics"]["overall_rms_level_dbfs"] is not None
        assert len(profile["signal_statistics"]["per_channel"]) == canonical["channels"]
        assert profile["signal_statistics"]["possible_clipping_is_derived"] is True
        assert profile["signal_statistics"]["possible_clipping_threshold_dbfs"] == -0.1
        assert profile["signal_statistics"]["possible_clipping"] is False
        integrity = profile["profiling_integrity"]
        assert integrity["source_sha256_before"] == source_hash
        assert integrity["source_sha256_after"] == source_hash
        assert integrity["source_unchanged"] is True
        assert integrity["canonical_sha256_before"] == canonical["sha256"]
        assert integrity["canonical_sha256_after"] == canonical["sha256"]
        assert integrity["canonical_unchanged"] is True
        results[label] = processed

    assert results["AMR_NB"]["source_probe"]["legacy_quality_classification"] == (
        "LEGACY_NARROWBAND"
    )
    assert results["AMR_NB"]["audio_stream"]["sample_rate_hz"] == 8000
    assert results["AMR_NB"]["audio_qc"]["technical_profile"][
        "nominal_bandwidth_class"
    ] == "NARROWBAND"
    assert results["AMR_WB"]["source_probe"]["legacy_quality_classification"] == (
        "WIDEBAND_MODERN"
    )
    assert results["AMR_WB"]["audio_qc"]["technical_profile"][
        "nominal_bandwidth_class"
    ] == "WIDEBAND"
    assert results["M4A_AAC"]["audio_qc"]["technical_profile"][
        "nominal_bandwidth_class"
    ] == "FULLBAND"
    assert results["M4A_AAC"]["audio_stream"]["channels"] == 2
    assert results["M4A_AAC"]["canonical_audio"]["channels"] == 2
    assert results["M4A_AAC"]["n_channels"] == cfg.normalized_audio_channels
    assert results["M4A_AAC"]["normalization"]["channel_transform"] == (
        "downmixed_for_working_copy"
    )
    assert results["WAV_PCM"]["n_channels"] == 2
    assert results["WAV_PCM"]["canonical_audio"]["channels"] == 2
    assert results["WAV_MONO"]["n_channels"] == 1
    assert results["WAV_MONO"]["canonical_audio"]["channels"] == 1


def test_ffprobe_content_detection_overrules_misleading_extension(tmp_path):
    cfg = Config()
    base = tmp_path / "base.wav"
    generate_sine_wav(str(base), duration_sec=1.0, framerate=16000)
    real_flac = tmp_path / "real.flac"
    _transcode(base, real_flac, ["-c:a", "flac"], cfg)
    misleading = tmp_path / "looks-like-mp3.mp3"
    shutil.copyfile(real_flac, misleading)

    metadata = preprocess.probe_audio_metadata(str(misleading), cfg)

    assert metadata["source_format"] == ".mp3"
    assert metadata["container_format"] == "flac"
    assert metadata["audio_stream"]["codec_name"] == "flac"


def test_invalid_truncated_media_fails_clearly_without_canonical_output(tmp_path):
    source = tmp_path / "truncated.mp3"
    source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x10truncated")
    job_dir = job_mod.create_job("invalid-media", str(tmp_path / "jobs"))
    ingested = ingest.ingest_file(str(source), job_dir)

    with pytest.raises(preprocess.MediaProcessingError) as captured:
        preprocess.preprocess_audio(ingested["path"], job_dir, Config())

    assert captured.value.state == "DECODE_FAILED"
    assert "DECODE_FAILED" in str(captured.value)
    assert not (job_dir / "preprocess" / "canonical.flac").exists()
    assert not (job_dir / "preprocess" / "audio_quality_profile.json").exists()
    assert _sha256(Path(ingested["path"])) == _sha256(source)


def test_quality_pipeline_accepts_canonical_flac_without_provider_network(tmp_path):
    source = tmp_path / "quality-stereo.wav"
    generate_sine_wav(str(source), duration_sec=5.2, framerate=16000, channels=2)
    cfg = Config()
    source_job = job_mod.create_job("canonical-source", str(tmp_path / "source-jobs"))
    ingested = ingest.ingest_file(str(source), source_job)
    canonical = preprocess.preprocess_audio(ingested["path"], source_job, cfg)["canonical_audio"]

    quality_cfg = quality_config(tmp_path / "quality-jobs")
    factory = FakeQualityFactory()
    result = run_quality_workflow(
        "quality-from-canonical",
        canonical["path"],
        quality_cfg,
        transcriber_factory=factory,
    )

    assert result["ok"] is True
    assert result["state"] == "review_required"
    assert result["quality"]["provider_calls"]["new"] == len(factory.calls)
    assert len(factory.calls) > 0


def test_stereo_canonical_can_be_chunked_without_losing_source_channel_facts(tmp_path):
    source = tmp_path / "stereo.wav"
    generate_sine_wav(str(source), duration_sec=2.0, framerate=16000, channels=2)
    job_dir = job_mod.create_job("stereo-chunk", str(tmp_path / "jobs"))
    ingested = ingest.ingest_file(str(source), job_dir)
    processed = preprocess.preprocess_audio(ingested["path"], job_dir, Config())
    chunks = chunker.chunk_file(processed["path"], job_dir, processed, Config())

    assert processed["canonical_audio"]["source_channels"] == 2
    assert processed["canonical_audio"]["channels"] == 2
    assert processed["n_channels"] == 2
    assert len(chunks) == 1
