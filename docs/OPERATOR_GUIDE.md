# APMA V5 Dry-Run Operator Guide

This guide shows how to run the MVP locally without API keys, live OpenAI calls, or billable work.

## Safety Defaults

Use these settings for local verification:

- `DRY_RUN=true`
- `TRANSCRIPTION_ENGINE=mock`
- No `OPENAI_API_KEY` is needed.
- No live API calls are made.
- Do not commit real or private audio.

The mock pipeline is:

audio -> chunks -> mock transcript -> full transcript -> mock minutes -> exports

## Prerequisites

From the repository root, build the development image if needed:

```cmd
docker build -t apma-v5:dev .
```

Run the test suite before and after changes:

```cmd
docker run --rm -e DRY_RUN=true -v "%CD%:/app" apma-v5:dev pytest -q
```

PowerShell equivalent:

```powershell
docker run --rm -e DRY_RUN=true -v "${PWD}:/app" apma-v5:dev pytest -q
```

## Run A Dry-Run Job

Use a WAV file that is safe for local testing. Do not use or commit private meeting audio. If you need a test file, create or use a short non-private WAV file placed locally under `sample_audio/`. Keep real meeting audio outside Git tracking.

For the simplest Release 0.1 verification, run the synthetic demo script:

```cmd
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "%CD%:/app" apma-v5:dev python scripts/run_mvp_dry_run.py --job-id release-0-1-demo
```

PowerShell equivalent:

```powershell
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "${PWD}:/app" apma-v5:dev python scripts/run_mvp_dry_run.py --job-id release-0-1-demo
```

The script creates a short synthetic WAV locally and runs the complete mock pipeline.

From Command Prompt:

```cmd
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "%CD%:/app" apma-v5:dev python -m services.cli --ingest path/to/test.wav --job-id dry-run-demo
```

PowerShell equivalent:

```powershell
docker run --rm -e DRY_RUN=true -e TRANSCRIPTION_ENGINE=mock -v "${PWD}:/app" apma-v5:dev python -m services.cli --ingest path/to/test.wav --job-id dry-run-demo
```

Replace `path/to/test.wav` with a local WAV path inside the repository folder. The container sees the repository at `/app`, so a file at `sample_audio/test.wav` can be passed as `sample_audio/test.wav`.

Expected terminal result:

```text
Job dry-run-demo finished with state=completed.
```

## Expected Files

After a successful run, inspect:

```text
jobs/dry-run-demo/
```

Expected outputs:

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

The manifest should include output references under:

- `outputs.full_transcript_json`
- `outputs.full_transcript_txt`
- `outputs.full_transcript_html`
- `outputs.full_transcript_srt`
- `outputs.full_transcript_vtt`
- `outputs.minutes_md`
- `outputs.action_items_json`
- `outputs.job_summary_json`

`full_transcript.json` is the canonical transcript authority. The HTML file is a deterministic,
self-contained view of that JSON. SRT and VTT contain only transcript segments with valid source
timestamps; untimed text is omitted instead of being assigned invented timing.

## Reset A Local Demo Job

If you reuse the same `--job-id`, remove that local runtime job folder first:

Warning: this permanently deletes the local demo job folder. Check the folder name carefully before running it.

```cmd
rmdir /s /q jobs\dry-run-demo
```

Only remove local runtime output folders that you intentionally created.
