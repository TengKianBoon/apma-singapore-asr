import json
import urllib.request
from pathlib import Path

from services.evaluation.asr import (
    build_provider_scorecards,
    build_release_gate,
    character_error_rate,
    evaluate_case,
    validate_transcript_contract,
    word_error_rate,
)
from scripts import run_asr_evals


def test_synthetic_multilingual_golden_cases_are_exact_matches():
    fixture_path = Path(__file__).parent / "fixtures" / "asr" / "golden_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    results = [evaluate_case(case) for case in cases]

    assert {result["language"] for result in results} == {"en", "zh-CN", "id", "mixed"}
    assert all(result["word_error_rate"] == 0.0 for result in results)
    assert all(result["character_error_rate"] == 0.0 for result in results)


def test_error_rates_detect_text_regressions():
    assert word_error_rate("send report Friday", "send report Monday") > 0.0
    assert character_error_rate("团队确认", "团队取消") > 0.0


def test_provider_scorecards_are_evidence_based_and_do_not_declare_winner():
    cases = [
        {
            "id": "openai-exact",
            "language": "mixed",
            "provider": "openai",
            "model": "gpt-transcribe",
            "reference": "Please kirim laporan，团队确认。",
            "hypothesis": "Please kirim laporan，团队确认。",
            "duration_seconds": 60,
            "cost_usd": 0.0045,
        },
        {
            "id": "gemini-regression",
            "language": "mixed",
            "provider": "google",
            "model": "fake-gemini",
            "reference": "Please kirim laporan，团队确认。",
            "hypothesis": "unrelated output",
            "duration_seconds": 60,
            "cost_usd": 0.01,
        },
    ]

    scorecards = build_provider_scorecards([evaluate_case(case) for case in cases])
    by_provider = {card["provider"]: card for card in scorecards}

    assert by_provider["openai"]["qualification"] == "pass"
    assert by_provider["openai"]["cost_per_audio_minute_usd"] == 0.0045
    assert by_provider["google"]["qualification"] == "fail"
    gate = build_release_gate(scorecards)
    assert gate["status"] == "fail"
    assert gate["comparison_claim"].startswith("No provider winner")


def test_diarized_contract_requires_speakers_and_timestamps():
    invalid = {
        "schema_version": "apma.transcript.chunk.v1",
        "text": "Hello",
        "diarized": True,
        "segments": [{"text": "Hello"}],
    }
    valid = {
        "schema_version": "apma.transcript.full.v1",
        "text": "Speaker 1: Hello",
        "diarized": True,
        "segments": [{
            "speaker": "Speaker 1",
            "provider_speaker": "A",
            "start_sec": 0.0,
            "end_sec": 1.2,
            "text": "Hello",
        }],
    }

    assert validate_transcript_contract(invalid)
    assert validate_transcript_contract(valid) == []


def test_eval_cli_writes_machine_readable_report(tmp_path):
    fixture_path = Path(__file__).parent / "fixtures" / "asr" / "golden_cases.json"
    output_path = tmp_path / "reports" / "asr-eval.json"

    result = run_asr_evals.main([
        "--fixture",
        str(fixture_path),
        "--output",
        str(output_path),
    ])

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert report["schema_version"] == "apma.asr-eval.v1"
    assert report["case_count"] == 4
    assert report["languages"] == ["en", "id", "mixed", "zh-CN"]
    assert report["mean_word_error_rate"] == 0.0
    assert report["mean_character_error_rate"] == 0.0
    assert report["provider_scorecards"][0]["qualification"] == "pass"
    assert report["release_gate"]["status"] == "pass"


def test_eval_cli_requires_no_api_key_and_makes_no_network_call(tmp_path, monkeypatch):
    fixture_path = Path(__file__).parent / "fixtures" / "asr" / "golden_cases.json"
    output_path = tmp_path / "offline-asr-eval.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def reject_network(*args, **kwargs):
        raise AssertionError("offline ASR eval attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", reject_network)

    result = run_asr_evals.main([
        "--fixture",
        str(fixture_path),
        "--output",
        str(output_path),
    ])

    assert result == 0
    assert output_path.is_file()
