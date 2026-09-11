from pathlib import Path

from services.transcript_exports import render_html, render_srt, render_vtt, write_transcript_exports


def _payload():
    return {
        "schema_version": "apma.transcript.full.v1",
        "job_id": "export-test",
        "created_at": "2026-08-28T00:00:00Z",
        "review": {"highest_attention_grade": "amber"},
        "provenance": {
            "providers": ["openai"],
            "models": ["gpt-4o-transcribe-diarize"],
            "source_sha256": ["a" * 64],
        },
        "segments": [
            {
                "start_sec": 1.25,
                "end_sec": 3.5,
                "speaker": "Speaker <1>",
                "text": "团队确认 & <script>alert('x')</script>",
                "provider": "openai",
                "model": "gpt-4o-transcribe-diarize",
            },
            {
                "start_sec": None,
                "end_sec": None,
                "speaker": None,
                "text": "Timing is unclear.",
                "provider": "openai",
                "model": "gpt-4o-transcribe-diarize",
            },
        ],
    }


def test_html_is_self_contained_escaped_and_names_json_as_authority():
    rendered = render_html(_payload())

    assert "full_transcript.json" in rendered
    assert "a" * 64 in rendered
    assert "Content-Security-Policy" in rendered
    assert "script-src 'none'" in rendered
    assert "Speaker &lt;1&gt;" in rendered
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in rendered
    assert "<script>alert" not in rendered
    assert "Timing unavailable" in rendered


def test_caption_exports_use_absolute_timestamps_and_do_not_invent_missing_timing():
    srt = render_srt(_payload())
    vtt = render_vtt(_payload())

    assert "00:00:01,250 --> 00:00:03,500" in srt
    assert "Speaker &lt;1&gt;: 团队确认" in srt
    assert "Timing is unclear" not in srt
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.250 --> 00:00:03.500" in vtt
    assert "Timing is unclear" not in vtt


def test_export_files_are_deterministic(tmp_path):
    first = write_transcript_exports(_payload(), tmp_path)
    first_contents = {key: Path(path).read_text(encoding="utf-8") for key, path in first.items()}
    second = write_transcript_exports(_payload(), tmp_path)
    second_contents = {key: Path(path).read_text(encoding="utf-8") for key, path in second.items()}

    assert first == second
    assert first_contents == second_contents
