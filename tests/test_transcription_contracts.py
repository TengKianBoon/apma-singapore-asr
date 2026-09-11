from services.transcription.contracts import normalize_asr_candidate, normalize_provider_timing


def test_normalized_candidate_is_deterministic_and_preserves_provider_payload():
    raw = {
        "text": "Confirmed speech only.",
        "segments": [{"speaker": "speaker_1", "text": "Confirmed speech only."}],
    }
    kwargs = {
        "provider": "openai",
        "provider_code": "gpt4oDiarz",
        "model": "gpt-4o-transcribe-diarize",
        "raw_response": raw,
        "job_id": "job-1",
        "run_id": "run-1",
        "chunk": {"chunk_id": 1, "filename": "chunk-00001.wav"},
        "request_id": "request-1",
    }

    first = normalize_asr_candidate(**kwargs)
    second = normalize_asr_candidate(**kwargs)

    assert first == second
    assert first["schema_version"] == "apma.asr-candidate.v1"
    assert first["text"] == raw["text"]
    assert first["segments"] == raw["segments"]
    assert first["raw_response"] == raw
    assert "start" not in first["segments"][0]


def test_normalized_candidate_extracts_gemini_style_text_without_invention():
    candidate = normalize_asr_candidate(
        provider="google",
        provider_code="Gem35",
        model="gemini-3.5-flash-lite",
        raw_response={"candidates": [{"content": {"parts": [{"text": "Observed text."}]}}]},
        job_id="job-2",
        run_id="run-2",
        chunk={"chunk_id": 2, "filename": "chunk-00002.wav"},
    )

    assert candidate["text"] == "Observed text."
    assert candidate["segments"] == []


def test_provider_native_timing_preserves_raw_offsets_and_adds_global_offsets():
    timing = normalize_provider_timing(
        words=[{"word": "hello", "start_offset": "1.250s", "end_offset": "1.750s"}],
        segments=[{"text": "hello world", "start": 1.0, "end": 2.0}],
        chunk_start_sec=915.0,
        chunk_duration_sec=45.0,
    )

    word = timing["provider_native"]["words"][0]
    segment = timing["provider_native"]["segments"][0]
    assert word["type"] == "provider_native_word"
    assert word["provider_start"] == "1.250s"
    assert word["provider_end"] == "1.750s"
    assert word["global_start_sec"] == 916.25
    assert word["global_end_sec"] == 916.75
    assert segment["type"] == "provider_native_segment"
    assert segment["global_start_sec"] == 916.0
    assert segment["global_end_sec"] == 917.0
    assert timing["chunk"] == {
        "type": "chunk_global",
        "global_start_sec": 915.0,
        "global_end_sec": 960.0,
    }


def test_provider_native_timing_does_not_clamp_or_invent_invalid_offsets():
    timing = normalize_provider_timing(
        words=[
            {"word": "missing", "start": None, "end": None},
            {"word": "outside", "start": 44.0, "end": 46.0},
        ],
        segments=[],
        chunk_start_sec=0.0,
        chunk_duration_sec=45.0,
    )

    assert timing["provider_native"]["words"] == []
    assert len(timing["normalization_warnings"]) == 2
