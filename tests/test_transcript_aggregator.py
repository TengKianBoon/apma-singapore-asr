import json

import pytest

from services.transcript_aggregator import aggregate_transcripts


def _write_candidate(
    path,
    *,
    job_id,
    provider,
    provider_code,
    text,
    chunk_sha256="chunk-hash",
    source_sha256="a" * 64,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "apma.transcript.chunk.v1",
                "job_id": job_id,
                "chunk_filename": "chunk-00001.wav",
                "chunk_sha256": chunk_sha256,
                "source_sha256": source_sha256,
                "chunk_start_sec": 0.0,
                "chunk_end_sec": 5.0,
                "segment_timing_scope": "chunk",
                "provider": provider,
                "provider_code": provider_code,
                "model": f"{provider}-model",
                "run_id": f"{provider}-run",
                "response_format": "json",
                "text": text,
                "segments": [{"start_sec": 0.0, "end_sec": 5.0, "text": text}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "chunk_filename": "chunk-00001.wav",
        "chunk_sha256": chunk_sha256,
        "source_sha256": source_sha256,
        "provider": provider,
        "provider_code": provider_code,
        "model": f"{provider}-model",
        "run_id": f"{provider}-run",
        "response_format": "json",
        "transcript_path": str(path),
    }


def test_aggregation_preserves_multilingual_speakers_provenance_and_absolute_times(tmp_path):
    job_dir = tmp_path / "jobs" / "aggregate-test"
    transcript_path = job_dir / "transcripts" / "chunk-00002.json"
    transcript_path.parent.mkdir(parents=True)
    provider_path = job_dir / "providers" / "gpt4oDiarz" / "tr-123" / "chunk-00002.json"
    provider_path.parent.mkdir(parents=True)
    provider_path.write_text("{}", encoding="utf-8")
    transcript_path.write_text(
        json.dumps(
            {
                "schema_version": "apma.transcript.chunk.v1",
                "job_id": "aggregate-test",
                "chunk_filename": "chunk-00002.wav",
                "source_sha256": "a" * 64,
                "chunk_start_sec": 10.0,
                "chunk_end_sec": 20.0,
                "segment_timing_scope": "chunk",
                "provider": "openai",
                "provider_code": "gpt4oDiarz",
                "model": "gpt-4o-transcribe-diarize",
                "run_id": "tr-123",
                "response_format": "diarized_json",
                "provider_artifact_path": str(provider_path),
                "diarized": True,
                "text": "Speaker 1: 团队确认。\nSpeaker 2: Tolong kirim laporan.",
                "segments": [
                    {
                        "speaker": "Speaker 1",
                        "provider_speaker": "A",
                        "start_sec": 1.0,
                        "end_sec": 2.0,
                        "text": "团队确认。",
                    },
                    {
                        "speaker": "Speaker 2",
                        "provider_speaker": "B",
                        "start_sec": 2.0,
                        "end_sec": 3.5,
                        "text": "Tolong kirim laporan.",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = aggregate_transcripts(
        "aggregate-test",
        job_dir,
        [{"chunk_filename": "chunk-00002.wav", "transcript_path": str(transcript_path)}],
    )

    full_payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    full_text = (job_dir / "outputs" / "full_transcript.txt").read_text(encoding="utf-8")
    assert result["state"] == "completed"
    assert full_payload["diarized"] is True
    assert full_payload["speaker_identity_scope"] == "chunk"
    assert full_payload["overlap_policy"] == "exact_normalized_boundary_overlap_deduplication"
    assert full_payload["segments"][0]["speaker"] == "Speaker 1"
    assert full_payload["segments"][0]["start_sec"] == 11.0
    assert full_payload["segments"][1]["end_sec"] == 13.5
    assert full_payload["provenance"]["models"] == ["gpt-4o-transcribe-diarize"]
    assert full_payload["provenance"]["source_sha256"] == ["a" * 64]
    assert full_payload["provenance"]["provider_artifacts"] == [str(provider_path)]
    assert full_payload["review"]["grade_counts"] == {"green": 0, "amber": 1, "red": 0}
    assert full_payload["chunks"][0]["reconciliation"]["reason"] == "single_candidate_only"
    assert "Speaker 1: 团队确认。" in full_text
    assert "Speaker 2: Tolong kirim laporan." in full_text


def test_aggregation_renders_unlabelled_segments_without_none_prefix(tmp_path):
    job_dir = tmp_path / "jobs" / "unlabelled-test"
    transcript_path = job_dir / "transcripts" / "chunk-00001.json"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text(
        json.dumps({
            "text": "Plain speech",
            "segments": [{
                "speaker": None,
                "start_sec": 0.0,
                "end_sec": 1.0,
                "text": "Plain speech",
            }],
        }),
        encoding="utf-8",
    )

    aggregate_transcripts(
        "unlabelled-test",
        job_dir,
        [{"chunk_filename": "chunk-00001.wav", "transcript_path": str(transcript_path)}],
    )

    full_text = (job_dir / "outputs" / "full_transcript.txt").read_text(encoding="utf-8")
    assert full_text.strip() == "Plain speech"
    assert "None:" not in full_text


def test_aggregation_falls_back_to_text_when_provider_segments_are_empty(tmp_path):
    job_dir = tmp_path / "jobs" / "empty-segments-test"
    transcript_path = job_dir / "transcripts" / "chunk-00001.json"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text(
        json.dumps({
            "chunk_start_sec": 15.0,
            "chunk_end_sec": 20.0,
            "segment_timing_scope": "chunk",
            "provider": "google",
            "provider_code": "Gem35T",
            "model": "gemini-3.5-transcribe",
            "text": "保留供应商返回的文字。",
            "segments": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    aggregate_transcripts(
        "empty-segments-test",
        job_dir,
        [{"chunk_filename": "chunk-00001.wav", "transcript_path": str(transcript_path)}],
    )

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert payload["text"] == "保留供应商返回的文字。"
    assert payload["chunks"][0]["text"] == "保留供应商返回的文字。"
    assert payload["segments"][0]["text"] == "保留供应商返回的文字。"
    assert payload["segments"][0]["chunk_start_sec"] == 15.0
    assert payload["chunks"][0]["chunk_end_sec"] == 20.0
    assert payload["review"]["grade_counts"] == {"green": 0, "amber": 1, "red": 0}


def test_aggregation_rejects_canonical_payload_from_another_job(tmp_path):
    job_dir = tmp_path / "jobs" / "expected-job"
    transcript_path = job_dir / "transcripts" / "chunk-00001.json"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text(
        json.dumps({
            "schema_version": "apma.transcript.chunk.v1",
            "job_id": "different-job",
            "chunk_filename": "chunk-00001.wav",
            "provider": "mock",
            "provider_code": "mock",
            "model": "mock",
            "run_id": "mock-123",
            "response_format": "json",
            "segment_timing_scope": "chunk",
            "text": "foreign transcript",
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="job_id"):
        aggregate_transcripts(
            "expected-job",
            job_dir,
            [{"chunk_filename": "chunk-00001.wav", "transcript_path": str(transcript_path)}],
        )


def test_aggregation_sorts_chunks_and_segments_chronologically(tmp_path):
    job_dir = tmp_path / "jobs" / "ordering-test"
    transcripts_dir = job_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)

    late_path = transcripts_dir / "chunk-00002.json"
    early_path = transcripts_dir / "chunk-00001.json"
    late_path.write_text(
        json.dumps({
            "chunk_start_sec": 10.0,
            "text": "late",
            "segments": [{"start_sec": 1.0, "end_sec": 2.0, "text": "late"}],
        }),
        encoding="utf-8",
    )
    early_path.write_text(
        json.dumps({
            "chunk_start_sec": 0.0,
            "text": "early",
            "segments": [{"start_sec": 0.5, "end_sec": 1.0, "text": "early"}],
        }),
        encoding="utf-8",
    )

    aggregate_transcripts(
        "ordering-test",
        job_dir,
        [
            {"chunk_filename": "chunk-00002.wav", "transcript_path": str(late_path)},
            {"chunk_filename": "chunk-00001.wav", "transcript_path": str(early_path)},
        ],
    )

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert [chunk["text"] for chunk in payload["chunks"]] == ["early", "late"]
    assert [segment["text"] for segment in payload["segments"]] == ["early", "late"]
    assert payload["segments"][1]["start_sec"] == 11.0


def test_aggregation_marks_exact_independent_provider_agreement_green(tmp_path):
    job_dir = tmp_path / "jobs" / "agreement-test"
    transcripts_dir = job_dir / "transcripts"
    text = "团队同意在 Friday 提交 laporan."
    openai_entry = _write_candidate(
        transcripts_dir / "chunk-00001.openai.json",
        job_id="agreement-test",
        provider="openai",
        provider_code="gptTr",
        text=text,
    )
    google_entry = _write_candidate(
        transcripts_dir / "chunk-00001.google.json",
        job_id="agreement-test",
        provider="google",
        provider_code="gemini",
        text=text,
    )

    aggregate_transcripts("agreement-test", job_dir, [openai_entry, google_entry])

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert payload["text"] == text
    assert payload["text"].count(text) == 1
    assert payload["candidate_count"] == 2
    assert len(payload["chunks"]) == 1
    assert len(payload["segments"]) == 1
    assert payload["provenance"]["providers"] == ["google", "openai"]
    decision = payload["chunks"][0]["reconciliation"]
    assert decision["grade"] == "green"
    assert decision["selected_candidate_id"] == payload["chunks"][0]["candidates"][0]["candidate_id"]
    assert decision["synthesis_performed"] is False
    assert decision["translation_performed"] is False
    assert payload["review"]["human_review_required"] is False


def test_aggregation_marks_material_disagreement_red_without_inventing_text(tmp_path):
    job_dir = tmp_path / "jobs" / "disagreement-test"
    transcripts_dir = job_dir / "transcripts"
    primary_text = "Budget is approved for June."
    alternate_text = "The proposal was rejected and postponed indefinitely."
    openai_entry = _write_candidate(
        transcripts_dir / "chunk-00001.openai.json",
        job_id="disagreement-test",
        provider="openai",
        provider_code="gptTr",
        text=primary_text,
    )
    google_entry = _write_candidate(
        transcripts_dir / "chunk-00001.google.json",
        job_id="disagreement-test",
        provider="google",
        provider_code="gemini",
        text=alternate_text,
    )

    aggregate_transcripts("disagreement-test", job_dir, [openai_entry, google_entry])

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    decision = payload["chunks"][0]["reconciliation"]
    assert payload["text"] == primary_text
    assert alternate_text not in payload["text"]
    assert [candidate["text"] for candidate in payload["chunks"][0]["candidates"]] == [
        primary_text,
        alternate_text,
    ]
    assert decision["grade"] == "red"
    assert decision["reason"] == "material_candidate_disagreement"
    assert decision["selection_policy"] == "verbatim_candidate_only"
    assert decision["synthesis_performed"] is False
    assert payload["review"]["human_review_required"] is True


def test_aggregation_rejects_candidates_with_conflicting_source_hashes(tmp_path):
    job_dir = tmp_path / "jobs" / "hash-conflict-test"
    transcripts_dir = job_dir / "transcripts"
    first_entry = _write_candidate(
        transcripts_dir / "chunk-00001.openai.json",
        job_id="hash-conflict-test",
        provider="openai",
        provider_code="gptTr",
        text="Same source?",
        chunk_sha256="hash-one",
    )
    second_entry = _write_candidate(
        transcripts_dir / "chunk-00001.google.json",
        job_id="hash-conflict-test",
        provider="google",
        provider_code="gemini",
        text="Same source?",
        chunk_sha256="hash-two",
    )

    with pytest.raises(ValueError, match="conflicting chunk hashes"):
        aggregate_transcripts("hash-conflict-test", job_dir, [first_entry, second_entry])


def test_aggregation_removes_exact_multilingual_boundary_overlap_only(tmp_path):
    job_dir = tmp_path / "jobs" / "overlap-test"
    transcripts_dir = job_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    repeated = "please send 团队 laporan before Friday"
    payloads = [
        ("chunk-00001.wav", 0.0, f"Opening unique speech. {repeated}"),
        ("chunk-00002.wav", 8.0, f"{repeated}. Closing unique speech."),
    ]
    entries = []
    for index, (filename, start, text) in enumerate(payloads, start=1):
        path = transcripts_dir / f"chunk-{index:05d}.json"
        path.write_text(
            json.dumps(
                {
                    "chunk_filename": filename,
                    "chunk_start_sec": start,
                    "chunk_end_sec": start + 10.0,
                    "segment_timing_scope": "chunk",
                    "provider": "mock",
                    "model": "mock",
                    "text": text,
                    "segments": [{"start_sec": 0.0, "end_sec": 10.0, "text": text}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        entries.append({"chunk_filename": filename, "transcript_path": str(path)})

    aggregate_transcripts("overlap-test", job_dir, entries)

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert payload["text"].count(repeated) == 1
    assert "Opening unique speech." in payload["text"]
    assert "Closing unique speech." in payload["text"]
    assert len(payload["segments"]) == 2
    assert repeated not in payload["segments"][1]["text"]
    assert payload["segments"][1]["text"] == "Closing unique speech."
    assert payload["chunks"][1]["overlap_dedup"]["applied"] is True
    assert payload["chunks"][1]["overlap_dedup"]["removed_token_count"] == 7


def test_aggregation_preserves_unique_boundary_speech(tmp_path):
    job_dir = tmp_path / "jobs" / "unique-boundary-test"
    transcripts_dir = job_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    entries = []
    for index, text in enumerate(
        ("First chunk has unique ending alpha.", "Second chunk starts uniquely beta."),
        start=1,
    ):
        path = transcripts_dir / f"chunk-{index:05d}.json"
        path.write_text(
            json.dumps({"chunk_start_sec": float((index - 1) * 8), "text": text}),
            encoding="utf-8",
        )
        entries.append(
            {"chunk_filename": f"chunk-{index:05d}.wav", "transcript_path": str(path)}
        )

    aggregate_transcripts("unique-boundary-test", job_dir, entries)

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert payload["text"] == (
        "First chunk has unique ending alpha.\n\nSecond chunk starts uniquely beta."
    )
    assert payload["chunks"][1]["overlap_dedup"]["applied"] is False


def test_aggregation_removes_anchored_exact_repeat_but_keeps_unmatched_edges(tmp_path):
    job_dir = tmp_path / "jobs" / "anchored-overlap-test"
    transcripts_dir = job_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    repeated = "this exact repeated boundary phrase has enough matching tokens now"
    texts = (
        f"Previous opening. {repeated}. Previous unique tail remains.",
        f"uh yes {repeated}. Current unique continuation remains.",
    )
    entries = []
    for index, text in enumerate(texts, start=1):
        path = transcripts_dir / f"chunk-{index:05d}.json"
        path.write_text(
            json.dumps(
                {
                    "chunk_start_sec": float((index - 1) * 8),
                    "text": text,
                    "segments": [{"text": text}],
                }
            ),
            encoding="utf-8",
        )
        entries.append(
            {"chunk_filename": f"chunk-{index:05d}.wav", "transcript_path": str(path)}
        )

    aggregate_transcripts("anchored-overlap-test", job_dir, entries)

    payload = json.loads(
        (job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8")
    )
    assert payload["text"].count(repeated) == 1
    assert "Previous unique tail remains." in payload["text"]
    assert "uh yes" in payload["text"]
    assert "Current unique continuation remains." in payload["text"]
    assert payload["segments"][1]["text"] == (
        "uh yes Current unique continuation remains."
    )
    dedup = payload["chunks"][1]["overlap_dedup"]
    assert dedup["applied"] is True
    assert dedup["match_mode"] == "anchored_exact_span"
