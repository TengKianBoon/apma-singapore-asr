from __future__ import annotations

from pathlib import Path

from scripts import local_dashboard


ROOT = Path(__file__).resolve().parents[1]


def test_health_is_boolean_only_and_never_returns_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-secret")
    monkeypatch.setenv("MERALION_API_KEY", "fake-meralion-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-dashscope-secret")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_ID", "fake-oss-id")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "fake-oss-secret")
    monkeypatch.setenv("ALIYUN_OSS_ENDPOINT", "https://oss-ap-southeast-1.aliyuncs.com")
    monkeypatch.setenv("ALIYUN_OSS_BUCKET", "fake-private-bucket")

    health = local_dashboard.dashboard_health()

    assert health == {
        "apma_running": True,
        "docker_backend_available": True,
        "m3asr_credential_configured": True,
        "gem35t_credential_configured": True,
        "openai_credential_configured": False,
        "qwena3ft_credential_configured": True,
    }
    assert "secret" not in repr(health).lower()


def test_daily_use_scripts_use_secure_store_and_value_free_docker_arguments():
    launcher = (ROOT / "Start_APMA.ps1").read_text(encoding="utf-8")
    setup = (ROOT / "scripts" / "windows" / "Configure_APMA_Credentials.ps1").read_text(
        encoding="utf-8"
    )
    store = (ROOT / "scripts" / "windows" / "APMA_CredentialStore.ps1").read_text(
        encoding="utf-8"
    )

    assert "CredWriteW" in store and "CredReadW" in store and "CredDeleteW" in store
    assert "$box.UseSystemPasswordChar = $Secret" in setup
    assert "Forget Gemini" in setup
    assert "Forget MERaLiON" in setup
    assert "Alibaba Model Studio API key" in setup
    assert "Forget Qwen + OSS" in setup
    assert "SetEnvironmentVariable('GEMINI_API_KEY', $null, 'User')" in setup
    assert "SetEnvironmentVariable('MERALION_API_KEY', $null, 'User')" in setup
    assert "'-e', 'GEMINI_API_KEY'" in launcher
    assert "'-e', 'MERALION_API_KEY'" in launcher
    assert "ENABLE_LIVE_MERALION_TRANSCRIPTION=true" in launcher
    assert "ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION=true" in launcher
    assert "$qwenStagingMode = 'data_uri'" in launcher
    assert '"QWEN_FILETRANS_STAGING_MODE=$qwenStagingMode"' in launcher
    assert "APMA_HOST_JOBS_PATH=" in launcher
    assert "Both provider credentials are required" not in launcher
    assert "$credentialStatus.DashScopeConfigured" in launcher
    assert "DASHSCOPE_API_KEY=" not in launcher
    assert "GEMINI_API_KEY=" not in launcher
    assert "Start Docker Desktop, then run Start APMA again." in launcher
    assert (ROOT / "Start_APMA.cmd").is_file()
    assert (ROOT / "APMA_Credentials.cmd").is_file()


def test_health_accepts_api_key_only_for_explicit_temporary_upload(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-beijing-key")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "dashscope_temporary")
    for name in (
        "ALIYUN_OSS_ACCESS_KEY_ID",
        "ALIYUN_OSS_ACCESS_KEY_SECRET",
        "ALIYUN_OSS_ENDPOINT",
        "ALIYUN_OSS_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    health = local_dashboard.dashboard_health()

    assert health["qwena3ft_credential_configured"] is True


def test_health_accepts_api_key_only_for_explicit_data_uri(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-international-key")
    monkeypatch.setenv("QWEN_FILETRANS_STAGING_MODE", "data_uri")
    for name in (
        "ALIYUN_OSS_ACCESS_KEY_ID",
        "ALIYUN_OSS_ACCESS_KEY_SECRET",
        "ALIYUN_OSS_ENDPOINT",
        "ALIYUN_OSS_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    health = local_dashboard.dashboard_health()

    assert health["qwena3ft_credential_configured"] is True
