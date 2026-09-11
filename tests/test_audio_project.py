from pathlib import Path

from services.audio_project import create_audio_project, project_paths
from services.config import load_config
from services.integrity import sha256_file
from tests.helpers import generate_sine_wav


def test_audio_project_retains_named_original_mp3_and_nested_chunks(tmp_path):
    source = tmp_path / "shared-original.wav"
    generate_sine_wav(str(source), duration_sec=2.2, framerate=8000)
    job_dir = tmp_path / "jobs" / "audio-subproject"
    job_dir.mkdir(parents=True)
    cfg = load_config()
    cfg.storage_path = str(tmp_path / "jobs")
    cfg.host_storage_path = r"C:\APMA\jobs"

    result = create_audio_project(
        source,
        job_dir,
        cfg,
        original_filename="Previous case.m4a",
        chunk_duration_seconds=1,
    )

    original = Path(result["original"]["path"])
    mp3 = Path(result["mp3"]["path"])
    chunks = result["mp3_chunks"]
    assert result["subproject_folder"] == r"C:\APMA\jobs\audio-subproject"
    assert original.name == "Previous case.m4a"
    assert original.read_bytes() == source.read_bytes()
    assert result["original"]["sha256"] == sha256_file(source)
    assert mp3.name == "Previous case.mp3"
    assert mp3.is_file()
    assert chunks["count"] >= 2
    assert Path(chunks["folder"]).parent == mp3.parent
    assert all(Path(item["path"]).is_file() for item in chunks["files"])
    assert all("part-" in item["filename"] for item in chunks["files"])
    assert Path(result["manifest_path"]).is_file()
    assert project_paths(result)["mp3_chunks_folder"] == (
        r"C:\APMA\jobs\audio-subproject\audio\mp3\chunks"
    )
