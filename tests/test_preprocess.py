from pathlib import Path

import pytest

from tests.helpers import generate_sine_wav
from services.config import Config
from services.preprocess import preprocess_audio, preprocess_wav, probe_audio_metadata


def test_preprocess_reads_metadata(tmp_path):
    src = tmp_path / "p.wav"
    generate_sine_wav(str(src), duration_sec=2.0, framerate=8000)
    meta = preprocess_wav(str(src))
    assert meta["n_channels"] == 1
    assert meta["framerate"] == 8000
    assert abs(meta["duration_seconds"] - 2.0) < 0.01
    assert meta["n_frames"] == int(2.0 * 8000)


def test_probe_non_wav_uses_ffprobe_duration(tmp_path, monkeypatch):
    src = tmp_path / "meeting.mp3"
    src.write_bytes(b"fake mp3 bytes")
    cfg = Config()

    monkeypatch.setattr(
        "services.preprocess._run_ffprobe_json",
        lambda path, cfg_in: {
            "format": {
                "duration": "61.5",
                "size": str(src.stat().st_size),
                "format_name": "mp3",
                "bit_rate": "128000",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "channels": 2,
                    "channel_layout": "stereo",
                    "bit_rate": "128000",
                    "duration": "61.5",
                }
            ],
        },
    )

    meta = probe_audio_metadata(str(src), cfg)

    assert meta["source_format"] == ".mp3"
    assert meta["probe_method"] == "ffprobe"
    assert meta["duration_seconds"] == 61.5
    assert meta["normalized_bytes_per_second"] == 32000
    assert meta["audio_stream"]["codec_name"] == "mp3"
    assert meta["audio_stream"]["channels"] == 2


def test_preprocess_non_wav_normalizes_to_wav(tmp_path, monkeypatch):
    src = tmp_path / "meeting.m4a"
    src.write_bytes(b"fake m4a bytes")
    wav_template = tmp_path / "template.wav"
    generate_sine_wav(str(wav_template), duration_sec=1.0, framerate=8000)
    job_dir = tmp_path / "jobs" / "job-format"
    cfg = Config()

    monkeypatch.setattr(
        "services.preprocess._run_ffprobe_json",
        lambda path, cfg_in: {
            "format": {
                "duration": "1.0",
                "size": str(src.stat().st_size),
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 2,
                    "channel_layout": "stereo",
                    "duration": "1.0",
                }
            ],
        },
    )

    def fake_normalize(src_path, dest_path, cfg_in):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(wav_template.read_bytes())

    def fake_canonical(src_path, job_path, probed, cfg_in):
        canonical = Path(job_path) / "preprocess" / "canonical.flac"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(wav_template.read_bytes())
        return {"path": str(canonical), "lossless": True, "channels": 2}

    monkeypatch.setattr("services.preprocess._create_canonical_audio", fake_canonical)
    monkeypatch.setattr("services.preprocess._run_ffmpeg_normalize", fake_normalize)

    meta = preprocess_audio(str(src), job_dir, cfg)

    assert meta["normalized"] is True
    assert meta["source_format"] == ".m4a"
    assert Path(meta["path"]).name == "normalized.wav"
    assert Path(meta["path"]).exists()
    assert meta["framerate"] == 8000
    assert meta["processing_audio_sha256"]
    assert Path(meta["canonical_audio"]["path"]).name == "canonical.flac"
    assert meta["source_probe"]["codec"] == "aac"
    assert meta["audio_qc"]["processing_audio"]["is_speech_vad"] is False


def test_preprocess_wav_records_probe_integrity_and_signal_qc(tmp_path):
    src = tmp_path / "quality.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)

    meta = preprocess_audio(str(src), tmp_path / "job", Config())

    assert meta["probe_method"] == "ffprobe+wave"
    assert meta["processing_audio_sha256"]
    assert meta["source_probe"]["sampling_band"] == "narrowband"
    assert meta["source_probe"]["nominal_bandwidth_class"] == "NARROWBAND"
    assert meta["source_probe"]["nominal_bandwidth_basis"] == "source_sample_rate_hz"
    assert meta["audio_qc"]["processing_audio"]["analysis"] == "pcm_signal_structure"
    assert meta["audio_qc"]["processing_audio"]["is_speech_vad"] is False
    assert meta["audio_qc"]["technical_profile"]["signal_statistics"]["analysis"] == (
        "ffmpeg_astats"
    )


def test_preprocess_reuses_one_shared_canonical_flac(tmp_path):
    src = tmp_path / "shared.wav"
    generate_sine_wav(str(src), duration_sec=1.0, framerate=8000)

    first = preprocess_audio(str(src), tmp_path / "jobs" / "job-one", Config())
    second = preprocess_audio(str(src), tmp_path / "jobs" / "job-two", Config())

    first_canonical = first["canonical_audio"]
    second_canonical = second["canonical_audio"]
    assert first_canonical["path"] == second_canonical["path"]
    assert first_canonical["shared_canonical_reused"] is False
    assert second_canonical["shared_canonical_reused"] is True
    assert first_canonical["storage_policy"] == "content_addressed_shared_canonical"
    assert len(list((tmp_path / "jobs" / "_audio_cache").rglob("canonical.flac"))) == 1


def test_probe_rejects_media_without_audio_stream(tmp_path, monkeypatch):
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fake video bytes")
    monkeypatch.setattr(
        "services.preprocess._run_ffprobe_json",
        lambda path, cfg: {
            "format": {"duration": "3.0", "size": str(src.stat().st_size), "format_name": "mp4"},
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
        },
    )

    with pytest.raises(ValueError, match="audio_stream_missing"):
        probe_audio_metadata(str(src), Config())


def test_probe_rejects_media_over_duration_limit(tmp_path, monkeypatch):
    src = tmp_path / "meeting.mp3"
    src.write_bytes(b"fake mp3 bytes")
    cfg = Config()
    cfg.max_meeting_duration_sec = 2
    monkeypatch.setattr(
        "services.preprocess._run_ffprobe_json",
        lambda path, cfg_in: {
            "format": {"duration": "3.0", "size": str(src.stat().st_size), "format_name": "mp3"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "channels": 2,
                    "duration": "3.0",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="duration_limit_exceeded"):
        probe_audio_metadata(str(src), cfg)
