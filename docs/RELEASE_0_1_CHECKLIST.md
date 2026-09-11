# Release 0.1 Local Dry-Run Checklist

Use this checklist to verify the local MVP without API keys, live OpenAI calls, or billable work.

## Safety Defaults

- `DRY_RUN=true`
- `TRANSCRIPTION_ENGINE=mock`
- No `OPENAI_API_KEY` is required.
- No live API calls are made.
- Mock transcription and mock minutes remain the default local path.
- Do not commit generated WAV files or real/private meeting audio.

Live OpenAI transcription is deferred until explicit user approval. Future live enablement should require `DRY_RUN=false`, `ENABLE_LIVE_OPENAI_TRANSCRIPTION=true`, an environment-provided `OPENAI_API_KEY`, and a strict cost cap.

## Build

```cmd
docker build -t apma-v5:dev .
```

## Run Tests

```cmd
docker run --rm -e DRY_RUN=true -v "%CD%:/app" apma-v5:dev pytest -q
```

PowerShell:

```powershell
docker run --rm -e DRY_RUN=true -v "${PWD}:/app" apma-v5:dev pytest -q
```

## One-Command Dry-Run Demo

Command Prompt:

```cmd
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "%CD%:/app" apma-v5:dev python scripts/run_mvp_dry_run.py --job-id release-0-1-demo
```

PowerShell:

```powershell
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "${PWD}:/app" apma-v5:dev python scripts/run_mvp_dry_run.py --job-id release-0-1-demo
```

The script creates a short synthetic WAV at `sample_audio/synthetic_mvp_demo.wav`, runs the dry-run CLI, and prints the output paths.

## Expected Files

After the demo completes, verify:

- `jobs/<job_id>/chunks/`
- `jobs/<job_id>/transcripts/`
- `jobs/<job_id>/outputs/full_transcript.json`
- `jobs/<job_id>/outputs/full_transcript.txt`
- `jobs/<job_id>/outputs/full_transcript.html`
- `jobs/<job_id>/outputs/full_transcript.srt`
- `jobs/<job_id>/outputs/full_transcript.vtt`
- `jobs/<job_id>/outputs/minutes.md`
- `jobs/<job_id>/outputs/action_items.json`
- `jobs/<job_id>/outputs/job_summary.json`
- `jobs/<job_id>/job_manifest.json`

The final manifest should include:

- `state` set to `completed`
- `source` input metadata
- `summary.chunk_count`
- `summary.transcript_paths`
- `summary.outputs.full_transcript_json`
- `summary.outputs.full_transcript_txt`
- `summary.outputs.full_transcript_html`
- `summary.outputs.full_transcript_srt`
- `summary.outputs.full_transcript_vtt`
- `summary.outputs.minutes_md`
- `summary.outputs.action_items_json`
- `summary.outputs.job_summary_json`
- `estimated_cost_usd` and `actual_cost_usd` equal to `0.0` in mock mode

Confirm that `full_transcript.json` remains the canonical authority, the HTML view is derived
deterministically from it, and SRT/VTT do not invent timestamps for untimed transcript text.
- empty `errors` for a successful dry-run job

## Troubleshooting Docker

- Confirm Docker Desktop is running.
- Confirm `docker version` works in your terminal.
- If Windows reports Docker pipe permission errors, restart Docker Desktop and confirm your Windows user is in the `docker-users` group.
- If the image is missing, rebuild with `docker build -t apma-v5:dev .`.
- Run commands from the repository root so the `-v "%CD%:/app"` mount points at this project.

## Reset Demo Jobs Safely

Warning: this permanently deletes the local demo job folder. Check the folder name carefully before running it.

```cmd
rmdir /s /q jobs\release-0-1-demo
```

PowerShell:

```powershell
Remove-Item -Recurse -Force .\jobs\release-0-1-demo
```
