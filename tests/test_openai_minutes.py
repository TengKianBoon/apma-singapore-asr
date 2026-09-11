import json
from pathlib import Path

import pytest

from services.config import load_config
from services.errors import CostLimitExceededError
from services.minutes.openai_adapter import LiveOpenAIMinutesGenerator, estimate_minutes_cost, minutes_model_options


def test_minutes_model_options_expose_default_premium_and_best_quality():
    cfg = load_config()
    options = minutes_model_options(cfg)

    labels = [item["label"] for item in options]

    assert labels == ["Default", "Premium", "Best Quality"]
    assert options[0]["model"] == "gpt-5.4-mini"
    assert options[1]["model"] == "gpt-5.4"
    assert options[2]["model"] == "gpt-5.5"


def test_minutes_cost_estimate_uses_style_and_model_budget():
    cfg = load_config()

    standard = estimate_minutes_cost("hello " * 1000, "standard", "gpt-5.4-mini", cfg)
    deep = estimate_minutes_cost("hello " * 1000, "deep_evidence", "gpt-5.5", cfg)

    assert standard["ok"] is True
    assert standard["estimated_total_tokens"] < deep["estimated_total_tokens"]
    assert deep["run_budget_cap_usd"] > standard["run_budget_cap_usd"]
    assert deep["within_cap"] is True


def test_live_minutes_generator_refuses_without_all_gates(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config()
    cfg.dry_run = True
    cfg.enable_live_openai_minutes = False

    errors = LiveOpenAIMinutesGenerator().check_gates(cfg, confirm_live_minutes=False)

    assert "DRY_RUN must be false for live minutes generation." in errors
    assert "ENABLE_LIVE_OPENAI_MINUTES must be true." in errors
    assert "Live minutes generation requires explicit confirmation." in errors
    assert "OPENAI_API_KEY must be set in the server environment." in errors


def test_live_minutes_generator_writes_outputs_with_fake_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    job_id = "job-live-minutes"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("Speaker discussed water intake, substation coordinates, and follow-up checks.", encoding="utf-8")
    (outputs_dir / "full_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")

    class FakeClient:
        def generate_minutes(self, *, model, prompt, transcript_text):
            assert model == "gpt-5.4-mini"
            assert "Detailed Discussion" in prompt
            assert "substation coordinates" in transcript_text
            return {"text": "# Meeting Minutes\n\n- Substation coordinates need follow-up.", "raw_response": {}}

    cfg = load_config()
    cfg.dry_run = False
    cfg.enable_live_openai_minutes = True
    cfg.openai_api_key = "fake-key"
    estimate = estimate_minutes_cost(transcript_path.read_text(encoding="utf-8"), "standard", "gpt-5.4-mini", cfg)

    refs = LiveOpenAIMinutesGenerator(FakeClient()).generate(
        job_id,
        job_dir,
        {
            "full_transcript_txt": str(transcript_path),
            "full_transcript_json": str(outputs_dir / "full_transcript.json"),
        },
        cfg,
        model="gpt-5.4-mini",
        minutes_style="standard",
        run_budget_cap_usd=estimate["run_budget_cap_usd"],
        confirm_live_minutes=True,
    )

    assert Path(refs["minutes_md"]).read_text(encoding="utf-8").startswith("# Meeting Minutes")
    assert Path(refs["action_items_json"]).exists()
    summary = json.loads(Path(refs["job_summary_json"]).read_text(encoding="utf-8"))
    assert summary["generator"] == "openai"
    assert summary["model"] == "gpt-5.4-mini"


def test_live_minutes_use_simplified_chinese_without_translating_hokkien(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    job_id = "job-live-minutes-chinese-script"
    job_dir = tmp_path / "jobs" / job_id
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("福建話內容", encoding="utf-8")

    class FakeClient:
        def generate_minutes(self, *, model, prompt, transcript_text):
            assert "Simplified Chinese" in prompt
            assert "do not translate Hokkien into Mandarin" in prompt
            return {"text": "保留福建話內容與會議記錄。", "raw_response": {}}

    cfg = load_config()
    cfg.dry_run = False
    cfg.enable_live_openai_minutes = True
    cfg.openai_api_key = "fake-key"
    estimate = estimate_minutes_cost(
        transcript_path.read_text(encoding="utf-8"),
        "standard",
        "gpt-5.4-mini",
        cfg,
    )

    refs = LiveOpenAIMinutesGenerator(FakeClient()).generate(
        job_id,
        job_dir,
        {"full_transcript_txt": str(transcript_path)},
        cfg,
        model="gpt-5.4-mini",
        minutes_style="standard",
        run_budget_cap_usd=estimate["run_budget_cap_usd"],
        confirm_live_minutes=True,
    )

    assert Path(refs["minutes_md"]).read_text(encoding="utf-8") == "保留福建话内容与会议记录。"
    summary = json.loads(Path(refs["job_summary_json"]).read_text(encoding="utf-8"))
    assert summary["script_normalization"]["changed"] is True


def test_live_minutes_generator_rejects_stale_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    job_dir = tmp_path / "job"
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("short transcript", encoding="utf-8")

    cfg = load_config()
    cfg.dry_run = False
    cfg.enable_live_openai_minutes = True
    cfg.openai_api_key = "fake-key"

    with pytest.raises(RuntimeError, match="Minutes budget cap does not match"):
        LiveOpenAIMinutesGenerator(client=object()).generate(
            "job",
            job_dir,
            {"full_transcript_txt": str(transcript_path)},
            cfg,
            model="gpt-5.4-mini",
            minutes_style="standard",
            run_budget_cap_usd=99.0,
            confirm_live_minutes=True,
        )


def test_live_minutes_generator_rejects_over_cost_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    job_dir = tmp_path / "job"
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    transcript_path = outputs_dir / "full_transcript.txt"
    transcript_path.write_text("very long transcript " * 10000, encoding="utf-8")

    cfg = load_config()
    cfg.dry_run = False
    cfg.enable_live_openai_minutes = True
    cfg.openai_api_key = "fake-key"
    cfg.max_minutes_cost_per_job_usd = 0.000001
    estimate = estimate_minutes_cost(transcript_path.read_text(encoding="utf-8"), "deep_evidence", "gpt-5.5", cfg)

    with pytest.raises(CostLimitExceededError):
        LiveOpenAIMinutesGenerator(client=object()).generate(
            "job",
            job_dir,
            {"full_transcript_txt": str(transcript_path)},
            cfg,
            model="gpt-5.5",
            minutes_style="deep_evidence",
            run_budget_cap_usd=estimate["run_budget_cap_usd"],
            confirm_live_minutes=True,
        )
