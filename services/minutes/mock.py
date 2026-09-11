from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.config import Config
from services.minutes.base import MinutesGenerator
from services.minutes.presets import MinutesStylePreset, get_minutes_style_preset


class MockMinutesGenerator(MinutesGenerator):
    """Deterministic dry-run minutes generator."""

    def _load_full_transcript(self, outputs: dict) -> tuple[dict[str, Any], str]:
        json_path = outputs.get("full_transcript_json")
        txt_path = outputs.get("full_transcript_txt")
        if not json_path:
            raise ValueError("Missing outputs.full_transcript_json")
        if not txt_path:
            raise ValueError("Missing outputs.full_transcript_txt")

        full_json_path = Path(json_path)
        full_txt_path = Path(txt_path)
        with full_json_path.open("r", encoding="utf-8") as fh:
            transcript_json = json.load(fh)
        transcript_text = full_txt_path.read_text(encoding="utf-8")
        return transcript_json, transcript_text

    def _style_sections(self, preset: MinutesStylePreset) -> list[str]:
        if preset.key == "deep_evidence":
            return [
                "## A. Meeting Overview",
                "- Mock output: meeting title, date, attendees, and platform need verification from the transcript.",
                "## B. Executive Summary",
                "- Dry-run placeholder summary. Future live minutes generation should preserve important details and translate mixed-language points into English.",
                "## C. Key Outcomes",
                "- No confirmed decisions were extracted by the mock generator.",
                "## D. Detailed Discussion Notes By Topic",
                "- Topic extraction is deferred to the future live minutes generator.",
                "## E. Decisions Made",
                "- No clear formal decision recorded.",
                "## F. Action Items",
                "- No action items identified by the mock generator.",
                "## G. Questions And Answers",
                "- No questions or answers extracted by the mock generator.",
                "## H. Commitments, Promises, And Verbal Assurances",
                "- No commitments identified by the mock generator.",
                "## I. Numbers, Dates, And Specific Facts",
                "- Specific facts need verification from the transcript.",
                "## J. Risks, Concerns, And Issues Raised",
                "- No risks identified by the mock generator.",
                "## K. Open Issues And Unresolved Questions",
                "- Unclear items should be marked as unclear / needs verification.",
                "## L. Documents, Evidence, And Data Mentioned",
                "- No documents identified by the mock generator.",
                "## M. Follow-Up Plan",
                "- No confirmed follow-up plan identified by the mock generator.",
                "## N. Clean Final Meeting Minutes",
                "- Future live minutes generation should produce a neutral shareable version after evidence notes.",
                "## Final Check",
                "- Check names, numbers, dates, commitments, risks, action items, documents, and unresolved questions before sharing.",
            ]
        if preset.key == "action_focused":
            return [
                "## Short Meeting Summary",
                "- Dry-run placeholder summary based on transcript availability.",
                "## Main Decisions Or Agreements",
                "- No confirmed decisions were extracted by the mock generator.",
                "## Confirmed Action Items",
                "- No confirmed action items identified by the mock generator.",
                "## Risks Or Blockers",
                "- No risks or blockers identified by the mock generator.",
                "## Missing Information / Needs Verification",
                "- Speaker names, owners, dates, and commitments may need verification.",
                "## Suggested Next Steps",
                "- AI-suggested next steps are not generated in mock mode.",
                "## Suggested Follow-Up Message Or Agenda",
                "- Future live minutes generation can draft a follow-up message from the transcript.",
            ]
        return [
            "## Meeting Overview",
            "- Mock output: meeting title, date, attendees, and platform need verification from the transcript.",
            "## Executive Summary",
            "- Dry-run mock minutes generated from the full transcript.",
            "## Detailed Discussion By Topic",
            "- Topic extraction is deferred to the future live minutes generator.",
            "## Decisions Made",
            "- No clear formal decision recorded.",
            "## Confirmed Action Items",
            "- No action items identified by the mock generator.",
            "## Questions And Answers",
            "- No questions or answers extracted by the mock generator.",
            "## Commitments Or Verbal Assurances",
            "- No commitments identified by the mock generator.",
            "## Important Numbers, Dates, Names, And Facts",
            "- Specific facts need verification from the transcript.",
            "## Risks, Concerns, And Open Issues",
            "- No risks identified by the mock generator.",
            "## Documents Or Evidence Mentioned",
            "- No documents identified by the mock generator.",
            "## Follow-Up Plan",
            "- No confirmed follow-up plan identified by the mock generator.",
            "## AI-Suggested Next Steps",
            "- AI-suggested next steps are not generated in mock mode.",
        ]

    def _minutes_markdown(self, job_id: str, transcript_text: str, word_count: int, preset: MinutesStylePreset) -> str:
        preview = transcript_text.strip()
        if len(preview) > 500:
            preview = preview[:497].rstrip() + "..."
        if not preview:
            preview = "No transcript text was available in this dry-run job."

        sections: list[str] = []
        for heading_or_item in self._style_sections(preset):
            sections.extend([heading_or_item, ""])

        return "\n".join(
            [
                "# Meeting Minutes",
                "",
                f"Job ID: {job_id}",
                "Generator: mock",
                f"Minutes style: {preset.label} ({preset.key})",
                "",
                "## Prompt Preset Purpose",
                "",
                preset.description,
                "",
                f"Dry-run mock minutes generated from the full transcript ({word_count} words).",
                "",
                *sections,
                "",
                "## Transcript Preview",
                "",
                preview,
                "",
                "## Preset Prompt For Future Live Minutes",
                "",
                preset.prompt,
                "",
            ]
        )

    def generate(self, job_id: str, job_dir: Path, outputs: dict, cfg: Config) -> dict:
        if not getattr(cfg, "dry_run", True):
            raise RuntimeError("MockMinutesGenerator can only run when cfg.dry_run is True")

        job_dir = Path(job_dir)
        outputs_dir = job_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        transcript_json, transcript_text = self._load_full_transcript(outputs)
        word_count = int(transcript_json.get("word_count", len(transcript_text.split())))
        chunk_count = len(transcript_json.get("chunks", []))
        segment_count = len(transcript_json.get("segments", []))
        preset = get_minutes_style_preset(getattr(cfg, "minutes_style", "standard"))

        minutes_path = outputs_dir / "minutes.md"
        action_items_path = outputs_dir / "action_items.json"
        job_summary_path = outputs_dir / "job_summary.json"

        action_items = {
            "job_id": job_id,
            "generator": "mock",
            "minutes_style": preset.key,
            "prompt_preset": {
                "key": preset.key,
                "label": preset.label,
                "description": preset.description,
            },
            "action_items": [],
        }
        job_summary = {
            "job_id": job_id,
            "generator": "mock",
            "state": "completed",
            "minutes_style": preset.key,
            "prompt_preset": {
                "key": preset.key,
                "label": preset.label,
                "description": preset.description,
                "prompt": preset.prompt,
            },
            "word_count": word_count,
            "chunk_count": chunk_count,
            "segment_count": segment_count,
            "outputs": {
                "full_transcript_json": outputs.get("full_transcript_json"),
                "full_transcript_txt": outputs.get("full_transcript_txt"),
                "full_transcript_html": outputs.get("full_transcript_html"),
                "full_transcript_srt": outputs.get("full_transcript_srt"),
                "full_transcript_vtt": outputs.get("full_transcript_vtt"),
                "minutes_md": str(minutes_path),
                "action_items_json": str(action_items_path),
                "job_summary_json": str(job_summary_path),
            },
        }

        minutes_path.write_text(self._minutes_markdown(job_id, transcript_text, word_count, preset), encoding="utf-8")
        with action_items_path.open("w", encoding="utf-8") as fh:
            json.dump(action_items, fh, indent=2)
        with job_summary_path.open("w", encoding="utf-8") as fh:
            json.dump(job_summary, fh, indent=2)

        return {
            "minutes_md": str(minutes_path),
            "action_items_json": str(action_items_path),
            "job_summary_json": str(job_summary_path),
        }
